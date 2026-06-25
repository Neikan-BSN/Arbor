from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Mapping

import pytest

from arbor.core.config_schema import RolesConfig
from arbor.tandem.cli_worker import CliRunResult
from arbor.tandem.config import TandemConfig
from arbor.tandem.driver import run_tandem_driver
from arbor.tandem.gate import GateOutcome
from arbor.tandem.ledger import RoleTaskRecord, put_role_task_record
from arbor.tandem.preflight import (
    TandemCliIdentity,
    TandemPreflightResult,
)


def _config(repair_budget: int = 2) -> TandemConfig:
    return TandemConfig(
        roles=RolesConfig(producer="claude", reviewer="codex", repair_budget=repair_budget),
        base_url="http://127.0.0.1:4000/v1",
    )


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
        gh_identity="user01",
    )


def _write_artifact(root: Path, proposal_id: str, *, insertion: str | None = None) -> Path:
    path = root / "docs" / "maintainer-dogfood" / "proposals" / f"{proposal_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "proposal_id": proposal_id,
                "gap_id": "gap",
                "surface_key": "docs",
                "target_file": "README.md",
                "gap_type": "missing_example",
                "priority": "LOW",
                "anchor_kind": "end_of_file",
                "anchor_text": "",
                "insertion": insertion or f"content {proposal_id}",
                "rationale": "rationale",
            }
        ),
        encoding="utf-8",
    )
    return path


def _pass_verdict() -> str:
    return """
schema_version: 1
proposal_id: fp-0
branch: maintainer/fix-fp-0
reviewer_runtime: codex-cli
verdict: pass
blocking_findings: []
repair_hints: []
"""


def _repair_verdict(hint: str = "revise grounding") -> str:
    return f"""
schema_version: 1
proposal_id: fp-0
branch: maintainer/fix-fp-0
reviewer_runtime: codex-cli
verdict: repair
blocking_findings:
  - {hint}
repair_hints:
  - {hint}
"""


def _result(cli: str, stdout: str = "ok") -> CliRunResult:
    return CliRunResult(
        cli=cli,
        returncode=0,
        stdout=stdout,
        stderr="",
        invocations=1,
        duration_s=0.01,
    )


def _gate_pass(proposal_id: str = "fp-0", patched_content: str = "Base\ncontent\n") -> GateOutcome:
    return GateOutcome(
        produced=True,
        passed=True,
        proposal_id=proposal_id,
        detail="gate pass",
        target_relpath="README.md",
        patched_content=patched_content,
        content_hash=hashlib.sha256(patched_content.encode("utf-8")).hexdigest(),
    )


def test_driver_happy_path_marks_ready_with_zero_provider_calls(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider_calls = []
    monkeypatch.setattr("arbor.core.create_provider", lambda *args, **kwargs: provider_calls.append(args))
    target = tmp_path / "repo"
    target.mkdir()
    (target / "scratch.txt").write_text("producer scratch state", encoding="utf-8")
    calls: list[dict[str, object]] = []

    def worker(**kwargs) -> CliRunResult:
        calls.append(kwargs)
        if kwargs["cli"] == "claude":
            _write_artifact(target, "fp-0")
            return _result("claude", stdout="producer trace must stay private")
        assert kwargs["cli"] == "codex"
        reviewer_cwd = kwargs["cwd"]
        assert isinstance(reviewer_cwd, Path)
        assert reviewer_cwd != target
        assert not (reviewer_cwd / "scratch.txt").exists()
        assert "producer trace" not in str(kwargs["task"])
        assert "scratch" not in str(kwargs["task"])
        assert "Gated artifact:" in str(kwargs["task"])
        assert str(reviewer_cwd) in str(kwargs["task"])
        return _result("codex", stdout=_pass_verdict())

    def gate(path: Path, root: Path) -> GateOutcome:
        assert root == target
        return _gate_pass("fp-0")

    result = run_tandem_driver(
        _config(),
        session_dir=tmp_path / "session",
        target_repo_root=target,
        skills_src=_skills(tmp_path),
        task="produce docs proposal",
        gate=gate,
        env={"ANTHROPIC_API_KEY": "scrub-me"},
        worker=worker,
        preflight_runner=_preflight_ok,
    )

    assert result.status == "ready"
    assert result.proposal_id == "fp-0"
    assert result.ledger.tasks["producer-0"].status == "ready"
    assert [call["cli"] for call in calls] == ["claude", "codex"]
    assert calls[0]["env"] == {}
    assert provider_calls == []


def test_gate_reject_reenters_producer_before_review(tmp_path: Path) -> None:
    target = tmp_path / "repo"
    target.mkdir()
    calls: list[str] = []
    gate_calls = 0

    def worker(**kwargs) -> CliRunResult:
        calls.append(str(kwargs["cli"]))
        if kwargs["cli"] == "claude":
            idx = calls.count("claude") - 1
            _write_artifact(target, f"fp-{idx}")
            return _result("claude")
        return _result("codex", stdout=_pass_verdict())

    def gate(path: Path, root: Path) -> GateOutcome:
        nonlocal gate_calls
        gate_calls += 1
        if gate_calls == 1:
            return GateOutcome(produced=True, passed=False, proposal_id="fp-0", detail="not insert-only")
        return _gate_pass("fp-1")

    result = run_tandem_driver(
        _config(repair_budget=1),
        session_dir=tmp_path / "session",
        target_repo_root=target,
        skills_src=_skills(tmp_path),
        task="produce docs proposal",
        gate=gate,
        env={},
        worker=worker,
        preflight_runner=_preflight_ok,
    )

    assert result.status == "ready"
    assert calls == ["claude", "claude", "codex"]
    assert [event.outcome for event in result.cycle_history] == ["gate-reject"]


def test_reviewer_repair_changing_artifact_exhausts_budget(tmp_path: Path) -> None:
    target = tmp_path / "repo"
    target.mkdir()
    producer_count = 0
    reviewer_count = 0

    def worker(**kwargs) -> CliRunResult:
        nonlocal producer_count, reviewer_count
        if kwargs["cli"] == "claude":
            _write_artifact(target, f"fp-{producer_count}", insertion=f"content {producer_count}")
            producer_count += 1
            return _result("claude")
        reviewer_count += 1
        return _result("codex", stdout=_repair_verdict(f"revise {reviewer_count}"))

    result = run_tandem_driver(
        _config(repair_budget=2),
        session_dir=tmp_path / "session",
        target_repo_root=target,
        skills_src=_skills(tmp_path),
        task="produce docs proposal",
        gate=lambda path, root: _gate_pass(
            json.loads(path.read_text(encoding="utf-8"))["proposal_id"]
        ),
        env={},
        worker=worker,
        preflight_runner=_preflight_ok,
    )

    assert result.status == "stopped"
    assert result.stop_record is not None
    assert result.stop_record.reason_code == "blocked"
    assert producer_count == 3
    assert reviewer_count == 3
    assert [event.outcome for event in result.cycle_history] == [
        "review-repair",
        "review-repair",
        "review-repair",
    ]


def test_reviewer_repair_reproducing_artifact_stops_oscillating(tmp_path: Path) -> None:
    target = tmp_path / "repo"
    target.mkdir()
    producer_count = 0
    reviewer_count = 0

    def worker(**kwargs) -> CliRunResult:
        nonlocal producer_count, reviewer_count
        if kwargs["cli"] == "claude":
            _write_artifact(target, "fp-repeat", insertion="same content")
            producer_count += 1
            return _result("claude")
        reviewer_count += 1
        return _result("codex", stdout=_repair_verdict("same fix"))

    result = run_tandem_driver(
        _config(repair_budget=3),
        session_dir=tmp_path / "session",
        target_repo_root=target,
        skills_src=_skills(tmp_path),
        task="produce docs proposal",
        gate=lambda path, root: _gate_pass("fp"),
        env={},
        worker=worker,
        preflight_runner=_preflight_ok,
    )

    assert result.status == "stopped"
    assert result.stop_record is not None
    assert result.stop_record.reason_code == "blocked-oscillating"
    assert producer_count == 2
    assert reviewer_count == 1


def test_repair_regressing_gate_is_recorded_as_gate_reject(tmp_path: Path) -> None:
    target = tmp_path / "repo"
    target.mkdir()
    producer_count = 0
    gate_count = 0

    def worker(**kwargs) -> CliRunResult:
        nonlocal producer_count
        if kwargs["cli"] == "claude":
            _write_artifact(target, f"fp-{producer_count}", insertion=f"content {producer_count}")
            producer_count += 1
            return _result("claude")
        return _result("codex", stdout=_repair_verdict("make it additive"))

    def gate(path: Path, root: Path) -> GateOutcome:
        nonlocal gate_count
        gate_count += 1
        if gate_count == 2:
            return GateOutcome(produced=True, passed=False, proposal_id="fp-1", detail="gate reject after repair")
        return _gate_pass("fp-0")

    result = run_tandem_driver(
        _config(repair_budget=1),
        session_dir=tmp_path / "session",
        target_repo_root=target,
        skills_src=_skills(tmp_path),
        task="produce docs proposal",
        gate=gate,
        env={},
        worker=worker,
        preflight_runner=_preflight_ok,
    )

    assert result.status == "stopped"
    assert result.stop_record is not None
    assert result.stop_record.reason_code == "blocked"
    assert [event.outcome for event in result.stop_record.cycle_history] == [
        "review-repair",
        "gate-reject",
    ]


def test_preflight_fail_writes_stop_record_and_invokes_no_worker(tmp_path: Path) -> None:
    target = tmp_path / "repo"
    target.mkdir()
    calls: list[object] = []

    def preflight_fail(
        config: TandemConfig,
        *,
        cwd: Path,
        env: Mapping[str, str],
    ) -> TandemPreflightResult:
        return TandemPreflightResult(
            ok=False,
            reason_code="paid-resolution",
            message="provider credential env vars are present",
        )

    def worker(**kwargs) -> CliRunResult:
        calls.append(kwargs)
        raise AssertionError("worker should not be invoked")

    result = run_tandem_driver(
        _config(),
        session_dir=tmp_path / "session",
        target_repo_root=target,
        skills_src=_skills(tmp_path),
        task="produce docs proposal",
        gate=lambda path, root: _gate_pass("fp"),
        env={},
        worker=worker,
        preflight_runner=preflight_fail,
    )

    assert result.status == "stopped"
    assert result.stop_record is not None
    assert result.stop_record.reason_code == "paid-resolution"
    assert result.ledger.tasks["preflight"].status == "stopped"
    assert calls == []


def test_noop_producer_does_not_reuse_stale_single_artifact(tmp_path: Path) -> None:
    target = tmp_path / "repo"
    target.mkdir()
    _write_artifact(target, "fp-stale")

    def worker(**kwargs) -> CliRunResult:
        assert kwargs["cli"] == "claude"
        return _result("claude")

    result = run_tandem_driver(
        _config(repair_budget=0),
        session_dir=tmp_path / "session",
        target_repo_root=target,
        skills_src=_skills(tmp_path),
        task="produce docs proposal",
        gate=lambda path, root: _gate_pass("fp-stale"),
        env={},
        worker=worker,
        preflight_runner=_preflight_ok,
    )

    assert result.status == "stopped"
    assert result.stop_record is not None
    assert result.stop_record.reason_code == "artifact-missing"
    assert result.cycle_history[0].outcome == "not-produced"


def test_resume_reuses_existing_producer_artifact_without_invoking_producer(tmp_path: Path) -> None:
    target = tmp_path / "repo"
    target.mkdir()
    artifact = _write_artifact(target, "fp-resume")
    session_dir = tmp_path / "session"
    put_role_task_record(
        session_dir,
        "producer-0",
        RoleTaskRecord(
            role="producer",
            resolved_cli="claude",
            inputs_hash="hash",
            status="produced",
            result_artifact_path=str(artifact),
        ),
    )
    calls: list[str] = []

    def worker(**kwargs) -> CliRunResult:
        calls.append(str(kwargs["cli"]))
        if kwargs["cli"] == "claude":
            raise AssertionError("producer should not be invoked on resume")
        return _result("codex", stdout=_pass_verdict())

    result = run_tandem_driver(
        _config(),
        session_dir=session_dir,
        target_repo_root=target,
        skills_src=_skills(tmp_path),
        task="produce docs proposal",
        gate=lambda path, root: _gate_pass("fp-resume"),
        env={},
        worker=worker,
        preflight_runner=_preflight_ok,
    )

    assert result.status == "ready"
    assert calls == ["codex"]


def test_resume_reuses_existing_reviewer_verdict_without_invoking_reviewer(tmp_path: Path) -> None:
    target = tmp_path / "repo"
    target.mkdir()
    artifact = _write_artifact(target, "fp-resume")
    session_dir = tmp_path / "session"
    verdict = session_dir / "reviewer-0-verdict.yaml"
    verdict.parent.mkdir(parents=True)
    verdict.write_text(_pass_verdict(), encoding="utf-8")
    put_role_task_record(
        session_dir,
        "producer-0",
        RoleTaskRecord(
            role="producer",
            resolved_cli="claude",
            inputs_hash="hash",
            status="gated",
            result_artifact_path=str(artifact),
            gated_content_hash=_gate_pass("fp-resume").content_hash,
            gated_target_relpath="README.md",
            gated_patched_path=str(session_dir / "patched" / "fp-resume.patch"),
        ),
    )
    put_role_task_record(
        session_dir,
        "reviewer-0",
        RoleTaskRecord(
            role="reviewer",
            resolved_cli="codex",
            inputs_hash="hash",
            status="reviewed",
            result_artifact_path=str(verdict),
            verdict="pass",
        ),
    )
    calls: list[str] = []

    def worker(**kwargs) -> CliRunResult:
        calls.append(str(kwargs["cli"]))
        raise AssertionError("workers should not be invoked on resume")

    result = run_tandem_driver(
        _config(),
        session_dir=session_dir,
        target_repo_root=target,
        skills_src=_skills(tmp_path),
        task="produce docs proposal",
        gate=lambda path, root: _gate_pass("fp-resume"),
        env={},
        worker=worker,
        preflight_runner=_preflight_ok,
    )

    assert result.status == "ready"
    assert calls == []


def test_invocation_cap_stops_after_gate_pass_before_reviewer(tmp_path: Path) -> None:
    target = tmp_path / "repo"
    target.mkdir()

    def worker(**kwargs) -> CliRunResult:
        if kwargs["cli"] == "claude":
            _write_artifact(target, "fp-cap")
            return _result("claude")
        raise AssertionError("reviewer should not run after invocation cap")

    result = run_tandem_driver(
        _config(),
        session_dir=tmp_path / "session",
        target_repo_root=target,
        skills_src=_skills(tmp_path),
        task="produce docs proposal",
        gate=lambda path, root: _gate_pass("fp-cap"),
        env={},
        worker=worker,
        preflight_runner=_preflight_ok,
        invocation_cap=1,
    )

    assert result.status == "stopped"
    assert result.stop_record is not None
    assert result.stop_record.reason_code == "invocation-cap-exceeded"
