from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Mapping, Sequence

from arbor.tandem.gh_runner import OPERATOR_DISCLOSURE, open_ready_draft_pr
from arbor.tandem.ledger import RoleTaskRecord, put_role_task_record, read_role_task_ledger


def _artifact(path: Path, proposal_id: str = "fp-123") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "proposal_id": proposal_id,
                "gap_id": "gap-123",
                "surface_key": "docs",
                "target_file": "README.md",
                "gap_type": "missing_example",
                "priority": "LOW",
                "anchor_kind": "end_of_file",
                "anchor_text": "",
                "insertion": "New docs",
                "rationale": "Adds missing docs",
            }
        ),
        encoding="utf-8",
    )
    return path


def _ready_ledger(session_dir: Path, artifact: Path, proposal_id: str = "fp-123") -> None:
    put_role_task_record(
        session_dir,
        "producer-0",
        RoleTaskRecord(
            role="producer",
            resolved_cli="claude",
            inputs_hash="hash",
            status="ready",
            result_artifact_path=str(artifact),
            verdict="pass",
        ),
    )


class FakeRunner:
    def __init__(self, responses: list[subprocess.CompletedProcess[str]]) -> None:
        self.responses = responses
        self.calls: list[list[str]] = []

    def __call__(
        self,
        args: Sequence[str],
        *,
        cwd: str | None = None,
        capture_output: bool = True,
        text: bool = True,
        check: bool = False,
        env: Mapping[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        self.calls.append(list(args))
        if not self.responses:
            raise AssertionError(f"unexpected subprocess call: {list(args)}")
        return self.responses.pop(0)


def _completed(args: list[str] | None = None, code: int = 0, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args or [], code, stdout=stdout, stderr=stderr)


def test_dry_run_default_reports_would_open_without_gh_mutation(tmp_path: Path) -> None:
    session_dir = tmp_path / "session"
    target = tmp_path / "repo"
    target.mkdir()
    artifact = _artifact(target / "docs" / "maintainer-dogfood" / "proposals" / "fp-123.json")
    _ready_ledger(session_dir, artifact)
    runner = FakeRunner([])

    result = open_ready_draft_pr(
        session_dir=session_dir,
        target_repo_root=target,
        gh_identity="Logged in to github.com as user01",
        runner=runner,
    )

    assert result.planned
    assert not result.mutated
    assert result.dry_run
    assert result.branch == "maintainer/fix-fp-123"
    assert result.title == "docs: apply maintainer proposal fp-123"
    assert result.body is not None
    assert OPERATOR_DISCLOSURE in result.body
    assert runner.calls == []


def test_enabled_opens_exactly_one_draft_pr_with_operator_disclosure(tmp_path: Path) -> None:
    session_dir = tmp_path / "session"
    target = tmp_path / "repo"
    target.mkdir()
    artifact = _artifact(target / "docs" / "maintainer-dogfood" / "proposals" / "fp-123.json")
    _ready_ledger(session_dir, artifact)
    runner = FakeRunner(
        [
            _completed(stdout=""),
            _completed(code=1, stderr="Not Found (HTTP 404)"),
            _completed(stdout="pushed"),
            _completed(stdout="https://github.example/wiki-forge/pull/7\n"),
        ]
    )

    result = open_ready_draft_pr(
        session_dir=session_dir,
        target_repo_root=target,
        gh_identity="Logged in to github.com as user01",
        dry_run=False,
        runner=runner,
    )

    assert result.mutated
    assert result.pr_url == "https://github.example/wiki-forge/pull/7"
    assert OPERATOR_DISCLOSURE in (result.body or "")
    assert [call[:3] for call in runner.calls] == [
        ["gh", "pr", "list"],
        ["gh", "api", "repos/:owner/:repo/git/ref/heads/maintainer/fix-fp-123"],
        ["git", "push", "origin"],
        ["gh", "pr", "create"],
    ]
    create_call = runner.calls[-1]
    assert "--draft" in create_call
    assert all(call[:3] != ["gh", "pr", "merge"] for call in runner.calls)
    body = create_call[create_call.index("--body") + 1]
    assert f"Disclosure: {OPERATOR_DISCLOSURE}." in body
    assert read_role_task_ledger(session_dir).tasks["producer-0"].pr_url == result.pr_url


def test_existing_pr_for_branch_opens_none_and_records_url(tmp_path: Path) -> None:
    session_dir = tmp_path / "session"
    target = tmp_path / "repo"
    target.mkdir()
    artifact = _artifact(target / "docs" / "maintainer-dogfood" / "proposals" / "fp-123.json")
    _ready_ledger(session_dir, artifact)
    runner = FakeRunner([_completed(stdout="https://github.example/wiki-forge/pull/8\n")])

    result = open_ready_draft_pr(
        session_dir=session_dir,
        target_repo_root=target,
        gh_identity="Logged in to github.com as arbor-maintainer[bot]",
        dry_run=False,
        runner=runner,
    )

    assert not result.mutated
    assert result.reason_code == "pr-exists"
    assert result.pr_url == "https://github.example/wiki-forge/pull/8"
    assert len(runner.calls) == 1
    assert runner.calls[0][:3] == ["gh", "pr", "list"]
    assert read_role_task_ledger(session_dir).tasks["producer-0"].pr_url == result.pr_url


def test_existing_remote_branch_opens_no_duplicate_pr(tmp_path: Path) -> None:
    session_dir = tmp_path / "session"
    target = tmp_path / "repo"
    target.mkdir()
    artifact = _artifact(target / "docs" / "maintainer-dogfood" / "proposals" / "fp-123.json")
    _ready_ledger(session_dir, artifact)
    runner = FakeRunner([_completed(stdout=""), _completed(stdout='{"ref":"heads/maintainer/fix-fp-123"}')])

    result = open_ready_draft_pr(
        session_dir=session_dir,
        target_repo_root=target,
        gh_identity="Logged in to github.com as user01",
        dry_run=False,
        runner=runner,
    )

    assert not result.mutated
    assert result.reason_code == "branch-exists"
    assert [call[:3] for call in runner.calls] == [
        ["gh", "pr", "list"],
        ["gh", "api", "repos/:owner/:repo/git/ref/heads/maintainer/fix-fp-123"],
    ]


def test_gh_failure_is_structured_and_does_not_create_duplicate(tmp_path: Path) -> None:
    session_dir = tmp_path / "session"
    target = tmp_path / "repo"
    target.mkdir()
    artifact = _artifact(target / "docs" / "maintainer-dogfood" / "proposals" / "fp-123.json")
    _ready_ledger(session_dir, artifact)
    runner = FakeRunner(
        [
            _completed(stdout=""),
            _completed(code=1, stderr="Not Found (HTTP 404)"),
            _completed(code=1, stderr="permission denied"),
        ]
    )

    result = open_ready_draft_pr(
        session_dir=session_dir,
        target_repo_root=target,
        gh_identity="Logged in to github.com as user01",
        dry_run=False,
        runner=runner,
    )

    assert not result.mutated
    assert result.reason_code == "gh-failed"
    assert "permission denied" in result.detail
    assert all(call[:3] != ["gh", "pr", "create"] for call in runner.calls)
    assert read_role_task_ledger(session_dir).tasks["producer-0"].pr_url is None
