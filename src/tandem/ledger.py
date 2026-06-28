"""Durable role-task ledger for deterministic tandem resume."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .contracts import read_producer_artifact

ROLE_TASK_LEDGER_SCHEMA_VERSION = 2
ROLE_TASK_LEDGER_NAME = "role_tasks.json"
RoleTaskStatus = Literal[
    "pending",
    "produced",
    "gated",
    "reviewed",
    "repair",
    "ready",
    "stopped",
]


class UnsupportedRoleTaskLedgerVersion(RuntimeError):
    """Raised when a role-task ledger is newer than this reader."""


class RoleTaskRecord(BaseModel):
    """One durable role-task transition record."""

    model_config = ConfigDict(extra="forbid")

    role: str
    resolved_cli: str
    inputs_hash: str
    status: RoleTaskStatus = "pending"
    result_artifact_path: str | None = None
    verdict: str | None = None
    pr_url: str | None = None
    gated_content_hash: str | None = None
    gated_target_relpath: str | None = None
    gated_patched_path: str | None = None
    isolation_warning: str | None = None


class RoleTaskLedger(BaseModel):
    """Versioned keyed ledger persisted as one atomic JSON file."""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = ROLE_TASK_LEDGER_SCHEMA_VERSION
    tasks: dict[str, RoleTaskRecord] = Field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe mapping for serialization."""

        return self.model_dump(mode="json")

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RoleTaskLedger":
        """Validate an on-disk ledger, rejecting unknown versions."""

        version = data.get("schema_version")
        if version != ROLE_TASK_LEDGER_SCHEMA_VERSION:
            raise UnsupportedRoleTaskLedgerVersion(
                f"role-task ledger schema_version {version!r} is not supported; "
                f"expected {ROLE_TASK_LEDGER_SCHEMA_VERSION}"
            )
        return cls.model_validate(data)


@dataclass(frozen=True)
class PlannedPr:
    """Idempotent PR creation intent derived from a ready ledger record."""

    task_id: str
    proposal_id: str
    branch: str
    result_artifact_path: Path
    gated_content_hash: str | None
    gated_target_relpath: str | None
    gated_patched_path: Path | None


def role_task_ledger_path(session_dir: str | Path) -> Path:
    """Return the role-task ledger path for ``session_dir``."""

    return Path(session_dir) / ROLE_TASK_LEDGER_NAME


def atomic_write_json(path: str | Path, payload: dict[str, Any]) -> Path:
    """Atomically write JSON using temp+fsync+``os.replace``."""

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False)
    fd, tmp_name = tempfile.mkstemp(
        dir=target.parent,
        prefix=f".{target.name}.",
        suffix=".tmp",
    )
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fp:
            fp.write(text)
            fp.flush()
            os.fsync(fp.fileno())
        os.replace(tmp, target)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return target


def read_role_task_ledger(session_dir: str | Path) -> RoleTaskLedger:
    """Read the role-task ledger, or return an empty current ledger."""

    path = role_task_ledger_path(session_dir)
    if not path.is_file():
        return RoleTaskLedger()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"corrupt role-task ledger at {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError(f"role-task ledger at {path} must be a JSON object")
    return RoleTaskLedger.from_dict(raw)


def write_role_task_ledger(session_dir: str | Path, ledger: RoleTaskLedger) -> Path:
    """Atomically persist ``ledger`` under ``session_dir``."""

    return atomic_write_json(role_task_ledger_path(session_dir), ledger.to_dict())


def put_role_task_record(
    session_dir: str | Path,
    task_id: str,
    record: RoleTaskRecord,
) -> RoleTaskLedger:
    """Upsert one role-task record and persist the ledger."""

    ledger = read_role_task_ledger(session_dir)
    ledger.tasks[task_id] = record
    write_role_task_ledger(session_dir, ledger)
    return ledger


def is_complete_on_resume(record: RoleTaskRecord) -> bool:
    """Return true when the recorded result artifact exists on disk."""

    if not record.result_artifact_path:
        return False
    return Path(record.result_artifact_path).is_file()


def should_invoke_task(record: RoleTaskRecord | None) -> bool:
    """Resume rule: completed artifacts are not re-invoked."""

    return record is None or not is_complete_on_resume(record)


def branch_key_for_proposal_id(proposal_id: str) -> str:
    """Return the PR branch join key for a proposal id."""

    return f"maintainer/fix-{proposal_id}"


def plan_pr_creations(ledger: RoleTaskLedger) -> list[PlannedPr]:
    """Plan one PR per ready proposal whose ledger record has no PR URL."""

    planned: list[PlannedPr] = []
    seen_branches: set[str] = set()
    for task_id, record in ledger.tasks.items():
        if record.status != "ready" or record.pr_url or not record.result_artifact_path:
            continue
        ref = read_producer_artifact(record.result_artifact_path)
        if not ref.ok or ref.proposal_id is None:
            continue
        branch = branch_key_for_proposal_id(ref.proposal_id)
        if branch in seen_branches:
            continue
        seen_branches.add(branch)
        planned.append(
            PlannedPr(
                task_id=task_id,
                proposal_id=ref.proposal_id,
                branch=branch,
                result_artifact_path=ref.path,
                gated_content_hash=record.gated_content_hash,
                gated_target_relpath=record.gated_target_relpath,
                gated_patched_path=(
                    Path(record.gated_patched_path) if record.gated_patched_path else None
                ),
            )
        )
    return planned
