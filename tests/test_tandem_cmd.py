from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Mapping

import pytest
from typer.testing import CliRunner

from arbor.cli.app import app
from arbor.cli.commands import tandem_cmd
from arbor.tandem.cli_worker import CliRunResult
from arbor.tandem.config import TandemConfig
from arbor.tandem.gate import GateOutcome
from arbor.tandem.preflight import TandemCliIdentity, TandemPreflightResult


def _skills(tmp_path: Path) -> Path:
    root = tmp_path / "skills"
    root.mkdir()
    return root


def _preflight_ok(
    config: TandemConfig,
    *,
    cwd: Path,
    env: Mapping[str, str],
) -> TandemPreflightResult:
    return TandemPreflightResult(
        ok=True,
        identities={
            "producer": TandemCliIdentity(
                role="producer",
                requested_cli=config.roles.producer,
                resolved_cli="claude",
                path="/usr/bin/claude",
                version="claude 1",
            ),
            "reviewer": TandemCliIdentity(
                role="reviewer",
                requested_cli=config.roles.reviewer,
                resolved_cli="codex",
                path="/usr/bin/codex",
                version="codex 1",
            ),
        },
        gh_identity="Logged in to github.com as user01",
    )


def _write_artifact(root: Path, proposal_id: str = "fp-cmd") -> Path:
    path = root / "docs" / "maintainer-dogfood" / "proposals" / f"{proposal_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "proposal_id": proposal_id,
                "gap_id": "gap-cmd",
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


def _result(cli: str, stdout: str = "ok") -> CliRunResult:
    return CliRunResult(
        cli=cli,
        returncode=0,
        stdout=stdout,
        stderr="",
        invocations=1,
        duration_s=0.01,
    )


def _pass_verdict() -> str:
    return """
schema_version: 1
proposal_id: fp-cmd
branch: maintainer/fix-fp-cmd
reviewer_runtime: codex-cli
verdict: pass
blocking_findings: []
repair_hints: []
"""


def _gate_pass(proposal_id: str = "fp-cmd", patched_content: str = "Base\nNew docs\n") -> GateOutcome:
    return GateOutcome(
        produced=True,
        passed=True,
        proposal_id=proposal_id,
        detail="gate pass",
        target_relpath="README.md",
        patched_content=patched_content,
        content_hash=hashlib.sha256(patched_content.encode("utf-8")).hexdigest(),
    )


def _blocked_verdict() -> str:
    return """
schema_version: 1
proposal_id: fp-cmd
branch: maintainer/fix-fp-cmd
reviewer_runtime: codex-cli
verdict: blocked
blocking_findings:
  - insufficient grounding
repair_hints: []
"""


def test_execute_tandem_run_happy_path_plans_one_dry_run_pr_without_provider_or_subprocess(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider_calls = []
    monkeypatch.setattr("arbor.core.create_provider", lambda *args, **kwargs: provider_calls.append(args))
    target = tmp_path / "wiki-forge"
    target.mkdir()
    calls: list[dict[str, object]] = []
    gate_calls: list[Path] = []

    def worker(**kwargs) -> CliRunResult:
        calls.append(kwargs)
        if kwargs["cli"] == "claude":
            _write_artifact(target)
            return _result("claude", stdout="producer scratch trace")
        assert kwargs["cli"] == "codex"
        assert "producer scratch trace" not in str(kwargs["task"])
        assert "Gated artifact:" in str(kwargs["task"])
        return _result("codex", stdout=_pass_verdict())

    def gate(path: Path, root: Path) -> GateOutcome:
        gate_calls.append(path)
        assert root == target
        return _gate_pass("fp-cmd")

    def gh_runner(*args, **kwargs) -> subprocess.CompletedProcess[str]:
        raise AssertionError("dry-run must not spawn gh or git")

    result = tandem_cmd.execute_tandem_run(
        task="produce docs proposal",
        target_repo_root=target,
        session_dir=tmp_path / "session",
        skills_src=_skills(tmp_path),
        producer_cli="claude",
        reviewer_cli="codex",
        gate=gate,
        dry_run=True,
        env={},
        worker=worker,
        preflight_runner=_preflight_ok,
        subprocess_runner=gh_runner,
    )

    assert result.driver.status == "ready"
    assert result.pr is not None
    assert result.pr.planned
    assert not result.pr.mutated
    assert result.pr.branch == "maintainer/fix-fp-cmd"
    assert [call["cli"] for call in calls] == ["claude", "codex"]
    assert gate_calls
    assert provider_calls == []


def test_blocked_reviewer_stops_run_and_does_not_open_pr(tmp_path: Path) -> None:
    target = tmp_path / "wiki-forge"
    target.mkdir()
    calls: list[str] = []

    def worker(**kwargs) -> CliRunResult:
        calls.append(str(kwargs["cli"]))
        if kwargs["cli"] == "claude":
            _write_artifact(target)
            return _result("claude")
        assert "Gated artifact:" in str(kwargs["task"])
        return _result("codex", stdout=_blocked_verdict())

    result = tandem_cmd.execute_tandem_run(
        task="produce docs proposal",
        target_repo_root=target,
        session_dir=tmp_path / "session",
        skills_src=_skills(tmp_path),
        gate=lambda path, root: _gate_pass("fp-cmd"),
        dry_run=True,
        env={},
        worker=worker,
        preflight_runner=_preflight_ok,
    )

    assert result.driver.status == "stopped"
    assert result.driver.stop_record is not None
    assert result.driver.stop_record.reason_code == "blocked"
    assert result.pr is None
    assert calls == ["claude", "codex"]


def test_load_gate_adapter_from_file_path(tmp_path: Path) -> None:
    adapter = tmp_path / "adapter.py"
    adapter.write_text(
        """
from arbor.tandem.gate import GateOutcome

def gate(artifact_path, target_repo_root):
    return GateOutcome(
        produced=True,
        passed=True,
        proposal_id="fp-dynamic",
        detail="ok",
        target_relpath="README.md",
        patched_content="patched",
        content_hash="d7017ebcd65455e76e953d5b42fa96c3df28c7c3b616c7f069ed930fb4fae5fd",
    )
""",
        encoding="utf-8",
    )

    gate = tandem_cmd.load_gate_adapter(adapter)
    outcome = gate(tmp_path / "artifact.json", tmp_path)

    assert outcome.passed
    assert outcome.proposal_id == "fp-dynamic"


def test_tandem_subpackage_imports_and_cli_help_lists_command() -> None:
    from arbor import tandem
    from arbor.tandem import gh_runner

    assert tandem.__name__ == "arbor.tandem"
    assert gh_runner.OPERATOR_DISCLOSURE

    result = CliRunner().invoke(app, ["tandem", "--help"])

    assert result.exit_code == 0
    assert "Run a producer/reviewer tandem workflow" in result.output


def test_execute_tandem_run_forwards_claude_permission_mode_to_producer(tmp_path: Path) -> None:
    target = tmp_path / "wiki-forge"
    target.mkdir()
    calls: list[dict[str, object]] = []

    def worker(**kwargs) -> CliRunResult:
        calls.append(kwargs)
        if kwargs["cli"] == "claude":
            _write_artifact(target)
            return _result("claude")
        return _result("codex", stdout=_pass_verdict())

    result = tandem_cmd.execute_tandem_run(
        task="produce docs proposal",
        target_repo_root=target,
        session_dir=tmp_path / "session",
        skills_src=_skills(tmp_path),
        claude_permission_mode="acceptEdits",
        gate=lambda path, root: _gate_pass("fp-cmd"),
        dry_run=True,
        env={},
        worker=worker,
        preflight_runner=_preflight_ok,
    )

    assert result.driver.status == "ready"
    producer_calls = [call for call in calls if call["cli"] == "claude"]
    reviewer_calls = [call for call in calls if call["cli"] == "codex"]
    assert producer_calls
    assert all(call.get("claude_permission_mode") == "acceptEdits" for call in producer_calls)
    # Reviewer stays read-only: it never receives an elevated permission mode.
    assert reviewer_calls
    assert all(call.get("claude_permission_mode") is None for call in reviewer_calls)


def test_run_help_lists_claude_permission_mode_option() -> None:
    # Render wide so rich does not truncate the long option name in the help box.
    result = CliRunner().invoke(app, ["tandem", "run", "--help"], env={"COLUMNS": "200"})

    assert result.exit_code == 0
    assert "--claude-permission-mode" in result.output
