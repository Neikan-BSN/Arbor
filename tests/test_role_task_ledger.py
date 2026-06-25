from __future__ import annotations

import json
from pathlib import Path

import pytest

from arbor.tandem.ledger import (
    ROLE_TASK_LEDGER_SCHEMA_VERSION,
    RoleTaskLedger,
    RoleTaskRecord,
    UnsupportedRoleTaskLedgerVersion,
    is_complete_on_resume,
    plan_pr_creations,
    put_role_task_record,
    read_role_task_ledger,
    role_task_ledger_path,
    should_invoke_task,
    write_role_task_ledger,
)


def _proposal(path: Path, proposal_id: str = "fp-ledger") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"proposal_id": proposal_id}), encoding="utf-8")
    return path


def _patched(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("patched content", encoding="utf-8")
    return path


def test_role_task_ledger_round_trips_and_leaves_no_temp_file(tmp_path: Path) -> None:
    artifact = _proposal(tmp_path / "docs" / "maintainer-dogfood" / "proposals" / "fp.json")
    ledger = RoleTaskLedger(
        tasks={
            "producer-0": RoleTaskRecord(
                role="producer",
                resolved_cli="claude",
                inputs_hash="hash",
                status="ready",
                result_artifact_path=str(artifact),
                verdict="pass",
                gated_content_hash="hash",
                gated_target_relpath="README.md",
                gated_patched_path=str(_patched(tmp_path / "patched" / "fp.patch")),
            )
        }
    )

    write_role_task_ledger(tmp_path, ledger)
    loaded = read_role_task_ledger(tmp_path)

    assert loaded.tasks["producer-0"].status == "ready"
    assert loaded.tasks["producer-0"].result_artifact_path == str(artifact)
    assert sorted(path.name for path in tmp_path.iterdir()) == ["docs", "patched", "role_tasks.json"]


def test_task_with_existing_artifact_is_complete_and_not_reinvoked(tmp_path: Path) -> None:
    artifact = _proposal(tmp_path / "result.json")
    record = RoleTaskRecord(
        role="producer",
        resolved_cli="codex",
        inputs_hash="hash",
        status="produced",
        result_artifact_path=str(artifact),
    )

    invoked = False
    if should_invoke_task(record):
        invoked = True

    assert is_complete_on_resume(record)
    assert not should_invoke_task(record)
    assert not invoked


def test_ready_without_pr_plans_one_pr_then_none_after_pr_url(tmp_path: Path) -> None:
    artifact = _proposal(tmp_path / "docs" / "maintainer-dogfood" / "proposals" / "fp-ready.json", "fp-ready")
    record = RoleTaskRecord(
        role="producer",
        resolved_cli="claude",
        inputs_hash="hash",
        status="ready",
        result_artifact_path=str(artifact),
        verdict="pass",
        gated_content_hash="hash",
        gated_target_relpath="README.md",
        gated_patched_path=str(_patched(tmp_path / "patched" / "fp-ready.patch")),
    )
    ledger = put_role_task_record(tmp_path, "producer-0", record)

    planned = plan_pr_creations(ledger)
    assert len(planned) == 1
    assert planned[0].branch == "maintainer/fix-fp-ready"
    assert planned[0].gated_content_hash == "hash"
    assert planned[0].gated_target_relpath == "README.md"
    assert planned[0].gated_patched_path == tmp_path / "patched" / "fp-ready.patch"

    ledger = put_role_task_record(
        tmp_path,
        "producer-0",
        record.model_copy(update={"pr_url": "https://github.invalid/pr/1"}),
    )
    assert plan_pr_creations(ledger) == []


def test_duplicate_ready_records_plan_one_pr_per_branch(tmp_path: Path) -> None:
    artifact = _proposal(tmp_path / "docs" / "maintainer-dogfood" / "proposals" / "fp-dupe.json", "fp-dupe")
    record = RoleTaskRecord(
        role="producer",
        resolved_cli="claude",
        inputs_hash="hash",
        status="ready",
        result_artifact_path=str(artifact),
        verdict="pass",
        gated_content_hash="hash",
        gated_target_relpath="README.md",
        gated_patched_path=str(_patched(tmp_path / "patched" / "fp-dupe.patch")),
    )

    put_role_task_record(tmp_path, "producer-0", record)
    ledger = put_role_task_record(tmp_path, "producer-1", record)

    assert [plan.branch for plan in plan_pr_creations(ledger)] == ["maintainer/fix-fp-dupe"]


def test_unknown_schema_version_is_refused(tmp_path: Path) -> None:
    path = role_task_ledger_path(tmp_path)
    path.write_text(
        json.dumps({"schema_version": ROLE_TASK_LEDGER_SCHEMA_VERSION + 1, "tasks": {}}),
        encoding="utf-8",
    )

    with pytest.raises(UnsupportedRoleTaskLedgerVersion):
        read_role_task_ledger(tmp_path)


def test_current_role_task_ledger_schema_version_is_two() -> None:
    assert ROLE_TASK_LEDGER_SCHEMA_VERSION == 2
