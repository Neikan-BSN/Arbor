"""Deterministic synchronous tandem driver."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterator, Literal, Mapping, Protocol

from .cli_worker import CliRunResult, run_cli_worker
from .config import TandemConfig
from .contracts import (
    ProducerArtifactRef,
    parse_reviewer_verdict,
    read_producer_artifact,
    read_reviewer_verdict,
)
from .gate import Gate
from .ledger import (
    RoleTaskLedger,
    RoleTaskRecord,
    atomic_write_json,
    is_complete_on_resume,
    put_role_task_record,
    read_role_task_ledger,
)
from .paid_guard import scrub_provider_env
from .preflight import TandemPreflightResult, run_tandem_preflight
from .worktree import WorktreeError, git_worktree

DEFAULT_INVOCATION_CAP = 8
PROPOSAL_GLOB = "docs/maintainer-dogfood/proposals/*.json"
STOP_RECORD_NAME = "tandem_stop.json"
DriverStatus = Literal["ready", "stopped"]


class CliWorker(Protocol):
    """Callable seam for role worker invocation."""

    def __call__(
        self,
        *,
        cli: str,
        cwd: Path,
        skills_src: Path,
        task: str,
        env: Mapping[str, str],
        timeout_s: int | float = ...,
        claude_permission_mode: str | None = ...,
    ) -> CliRunResult:
        """Run one role task."""


class PreflightRunner(Protocol):
    """Callable seam for preflight checks."""

    def __call__(
        self,
        config: TandemConfig,
        *,
        cwd: Path,
        env: Mapping[str, str],
    ) -> TandemPreflightResult:
        """Run tandem preflight."""


@dataclass(frozen=True)
class CycleHistoryEvent:
    """One produce/gate/review outcome in the repair loop."""

    cycle: int
    phase: str
    outcome: str
    proposal_id: str | None
    detail: str


@dataclass(frozen=True)
class StopRecord:
    """Structured fail-closed stop record."""

    reason_code: str
    role: str
    resolved_cli: str | None
    phase: str
    detail: str
    cycle_history: list[CycleHistoryEvent] = field(default_factory=list)


@dataclass(frozen=True)
class GatedContentPin:
    """Durable content pinned by a passing generic gate."""

    patched_path: Path
    content_hash: str
    target_relpath: str


@dataclass(frozen=True)
class ReviewerWorkspace:
    """Isolated filesystem view handed to the reviewer role."""

    cwd: Path
    artifact_path: Path
    warning: str | None = None


@dataclass(frozen=True)
class TandemDriverResult:
    """Final driver outcome."""

    status: DriverStatus
    ledger: RoleTaskLedger
    proposal_id: str | None = None
    result_artifact_path: Path | None = None
    stop_record: StopRecord | None = None
    cycle_history: list[CycleHistoryEvent] = field(default_factory=list)


def run_tandem_driver(
    config: TandemConfig,
    *,
    session_dir: Path,
    target_repo_root: Path,
    skills_src: Path,
    task: str,
    gate: Gate,
    env: Mapping[str, str] | None = None,
    worker: CliWorker = run_cli_worker,
    preflight_runner: PreflightRunner = run_tandem_preflight,
    invocation_cap: int = DEFAULT_INVOCATION_CAP,
    base_ref: str = "main",
) -> TandemDriverResult:
    """Run preflight -> produce -> gate -> review until ready or stopped."""

    session_dir.mkdir(parents=True, exist_ok=True)
    run_env = dict(os.environ if env is None else env)
    preflight = preflight_runner(config, cwd=target_repo_root, env=run_env)
    history: list[CycleHistoryEvent] = []

    if not preflight.ok:
        reason = preflight.reason_code or "preflight-failed"
        record = StopRecord(
            reason_code=reason,
            role="preflight",
            resolved_cli=None,
            phase="preflight",
            detail=preflight.message,
            cycle_history=history,
        )
        _write_stop_record(session_dir, record)
        put_role_task_record(
            session_dir,
            "preflight",
            RoleTaskRecord(
                role="preflight",
                resolved_cli="",
                inputs_hash=_inputs_hash(task, "preflight"),
                status="stopped",
                verdict=reason,
            ),
        )
        return TandemDriverResult(
            status="stopped",
            ledger=read_role_task_ledger(session_dir),
            stop_record=record,
            cycle_history=history,
        )

    producer_cli = _resolved_cli(preflight, "producer", config.roles.producer)
    reviewer_cli = _resolved_cli(preflight, "reviewer", config.roles.reviewer)
    worker_env = scrub_provider_env(run_env)
    cycle = 0
    repairs_used = 0
    invocations = 0
    feedback_detail: str | None = None
    prior_repair_hints: list[str] = []
    seen_signatures: set[str] = set()

    while True:
        if invocations >= invocation_cap:
            return _stop(
                session_dir=session_dir,
                history=history,
                reason_code="invocation-cap-exceeded",
                role="producer",
                resolved_cli=producer_cli,
                phase="produce",
                detail=f"invocation cap {invocation_cap} reached",
            )

        producer_task_id = f"producer-{cycle}"
        ledger = read_role_task_ledger(session_dir)
        producer_record = ledger.tasks.get(producer_task_id)
        artifact_path: Path | None = None
        artifact_ref: ProducerArtifactRef | None = None
        if producer_record is not None and is_complete_on_resume(producer_record):
            artifact_path = Path(producer_record.result_artifact_path or "")
            artifact_ref = read_producer_artifact(artifact_path)
        else:
            pending_record = RoleTaskRecord(
                role="producer",
                resolved_cli=producer_cli,
                inputs_hash=_inputs_hash(task, cycle, feedback_detail, prior_repair_hints),
                status="pending",
            )
            put_role_task_record(session_dir, producer_task_id, pending_record)
            before = _snapshot_artifacts(target_repo_root)
            producer_result = worker(
                cli=producer_cli,
                cwd=target_repo_root,
                skills_src=skills_src,
                task=_producer_task(task, feedback_detail, prior_repair_hints),
                env=worker_env,
            )
            invocations += max(1, producer_result.invocations)
            artifact_path = _discover_artifact(target_repo_root, before)
            artifact_ref = (
                read_producer_artifact(artifact_path)
                if artifact_path is not None
                else ProducerArtifactRef(
                    path=target_repo_root / PROPOSAL_GLOB,
                    proposal_id=None,
                    ok=False,
                    detail="producer did not write a proposal artifact",
                    reason_code="artifact-missing",
                )
            )
            put_role_task_record(
                session_dir,
                producer_task_id,
                pending_record.model_copy(
                    update={
                        "status": "produced" if artifact_ref.ok else "repair",
                        "result_artifact_path": str(artifact_path) if artifact_path else None,
                        "verdict": artifact_ref.reason_code,
                    }
                ),
            )

        if artifact_ref is None or not artifact_ref.ok or artifact_path is None:
            detail = artifact_ref.detail if artifact_ref is not None else "producer artifact unknown"
            history.append(
                CycleHistoryEvent(
                    cycle=cycle,
                    phase="produce",
                    outcome="not-produced",
                    proposal_id=None,
                    detail=detail,
                )
            )
            decision = _next_repair(
                session_dir=session_dir,
                task_id=producer_task_id,
                history=history,
                repairs_used=repairs_used,
                repair_budget=config.roles.repair_budget,
                role="producer",
                resolved_cli=producer_cli,
                phase="produce",
                detail=detail,
                stop_reason_code=artifact_ref.reason_code if artifact_ref is not None else None,
            )
            if decision is not None:
                return decision
            repairs_used += 1
            cycle += 1
            feedback_detail = detail
            prior_repair_hints = [detail]
            continue

        signature = _artifact_signature(artifact_path)
        if signature in seen_signatures:
            history.append(
                CycleHistoryEvent(
                    cycle=cycle,
                    phase="produce",
                    outcome="oscillating",
                    proposal_id=artifact_ref.proposal_id,
                    detail=f"artifact signature repeated: {signature}",
                )
            )
            return _stop(
                session_dir=session_dir,
                history=history,
                reason_code="blocked-oscillating",
                role="producer",
                resolved_cli=producer_cli,
                phase="produce",
                detail="producer reproduced a prior artifact signature",
                task_id=producer_task_id,
            )
        seen_signatures.add(signature)

        gate_outcome = gate(artifact_path, target_repo_root)
        proposal_id = gate_outcome.proposal_id or artifact_ref.proposal_id
        if not gate_outcome.produced or not gate_outcome.passed:
            outcome = "not-produced" if not gate_outcome.produced else "gate-reject"
            history.append(
                CycleHistoryEvent(
                    cycle=cycle,
                    phase="gate",
                    outcome=outcome,
                    proposal_id=proposal_id,
                    detail=gate_outcome.detail,
                )
            )
            put_role_task_record(
                session_dir,
                producer_task_id,
                read_role_task_ledger(session_dir).tasks[producer_task_id].model_copy(
                    update={"status": "repair", "verdict": gate_outcome.detail}
                ),
            )
            decision = _next_repair(
                session_dir=session_dir,
                task_id=producer_task_id,
                history=history,
                repairs_used=repairs_used,
                repair_budget=config.roles.repair_budget,
                role="producer",
                resolved_cli=producer_cli,
                phase="gate",
                detail=gate_outcome.detail,
            )
            if decision is not None:
                return decision
            repairs_used += 1
            cycle += 1
            feedback_detail = gate_outcome.detail
            prior_repair_hints = [gate_outcome.detail]
            continue

        pin = _pin_gated_content(
            session_dir=session_dir,
            proposal_id=proposal_id,
            gate_outcome=gate_outcome,
            history=history,
            resolved_cli=producer_cli,
        )
        if isinstance(pin, StopRecord):
            _write_stop_record(session_dir, pin)
            put_role_task_record(
                session_dir,
                producer_task_id,
                read_role_task_ledger(session_dir).tasks[producer_task_id].model_copy(
                    update={"status": "stopped", "verdict": pin.reason_code}
                ),
            )
            return TandemDriverResult(
                status="stopped",
                ledger=read_role_task_ledger(session_dir),
                stop_record=pin,
                cycle_history=history,
            )

        put_role_task_record(
            session_dir,
            producer_task_id,
            read_role_task_ledger(session_dir).tasks[producer_task_id].model_copy(
                update={
                    "status": "gated",
                    "verdict": gate_outcome.detail,
                    "gated_content_hash": pin.content_hash,
                    "gated_target_relpath": pin.target_relpath,
                    "gated_patched_path": str(pin.patched_path),
                }
            ),
        )

        if invocations >= invocation_cap:
            return _stop(
                session_dir=session_dir,
                history=history,
                reason_code="invocation-cap-exceeded",
                role="reviewer",
                resolved_cli=reviewer_cli,
                phase="review",
                detail=f"invocation cap {invocation_cap} reached",
            )

        reviewer_task_id = f"reviewer-{cycle}"
        ledger = read_role_task_ledger(session_dir)
        reviewer_record = ledger.tasks.get(reviewer_task_id)
        if reviewer_record is not None and is_complete_on_resume(reviewer_record):
            reviewer_verdict = read_reviewer_verdict(reviewer_record.result_artifact_path or "")
        else:
            verdict_path = session_dir / f"{reviewer_task_id}-verdict.yaml"
            pending_review = RoleTaskRecord(
                role="reviewer",
                resolved_cli=reviewer_cli,
                inputs_hash=_inputs_hash(task, cycle, str(artifact_path), prior_repair_hints),
                status="pending",
            )
            put_role_task_record(session_dir, reviewer_task_id, pending_review)
            isolation_warning: str | None = None
            with _reviewer_workspace(
                target_repo_root=target_repo_root,
                artifact_path=artifact_path,
                gated_target_relpath=pin.target_relpath,
                base_ref=base_ref,
            ) as workspace:
                isolation_warning = workspace.warning
                if isolation_warning is not None:
                    put_role_task_record(
                        session_dir,
                        reviewer_task_id,
                        pending_review.model_copy(update={"isolation_warning": isolation_warning}),
                    )
                review_result = worker(
                    cli=reviewer_cli,
                    cwd=workspace.cwd,
                    skills_src=skills_src,
                    task=_reviewer_task(task, workspace.artifact_path, prior_repair_hints),
                    env=worker_env,
                )
            invocations += max(1, review_result.invocations)
            _atomic_write_text(verdict_path, review_result.stdout)
            reviewer_verdict = parse_reviewer_verdict(review_result.stdout)
            put_role_task_record(
                session_dir,
                reviewer_task_id,
                pending_review.model_copy(
                    update={
                        "status": "reviewed",
                        "result_artifact_path": str(verdict_path),
                        "verdict": reviewer_verdict.verdict,
                        "isolation_warning": isolation_warning,
                    }
                ),
            )

        if reviewer_verdict.verdict == "pass":
            put_role_task_record(
                session_dir,
                producer_task_id,
                read_role_task_ledger(session_dir).tasks[producer_task_id].model_copy(
                    update={"status": "ready", "verdict": "pass"}
                ),
            )
            return TandemDriverResult(
                status="ready",
                ledger=read_role_task_ledger(session_dir),
                proposal_id=proposal_id,
                result_artifact_path=artifact_path,
                cycle_history=history,
            )

        if reviewer_verdict.verdict == "repair":
            detail = _with_warning(
                "; ".join(reviewer_verdict.repair_hints) or reviewer_verdict.detail,
                read_role_task_ledger(session_dir).tasks[reviewer_task_id].isolation_warning,
            )
            history.append(
                CycleHistoryEvent(
                    cycle=cycle,
                    phase="review",
                    outcome="review-repair",
                    proposal_id=proposal_id,
                    detail=detail,
                )
            )
            put_role_task_record(
                session_dir,
                reviewer_task_id,
                read_role_task_ledger(session_dir).tasks[reviewer_task_id].model_copy(
                    update={"status": "repair", "verdict": "repair"}
                ),
            )
            decision = _next_repair(
                session_dir=session_dir,
                task_id=reviewer_task_id,
                history=history,
                repairs_used=repairs_used,
                repair_budget=config.roles.repair_budget,
                role="reviewer",
                resolved_cli=reviewer_cli,
                phase="review",
                detail=detail,
            )
            if decision is not None:
                return decision
            repairs_used += 1
            cycle += 1
            feedback_detail = detail
            prior_repair_hints = reviewer_verdict.repair_hints
            continue

        history.append(
            CycleHistoryEvent(
                cycle=cycle,
                phase="review",
                outcome=f"review-{reviewer_verdict.verdict}",
                proposal_id=proposal_id,
                detail=_with_warning(
                    reviewer_verdict.detail,
                    read_role_task_ledger(session_dir).tasks[reviewer_task_id].isolation_warning,
                ),
            )
        )
        put_role_task_record(
            session_dir,
            reviewer_task_id,
            read_role_task_ledger(session_dir).tasks[reviewer_task_id].model_copy(
                update={"status": "stopped", "verdict": reviewer_verdict.verdict}
            ),
        )
        return _stop(
            session_dir=session_dir,
            history=history,
            reason_code=reviewer_verdict.verdict,
            role="reviewer",
            resolved_cli=reviewer_cli,
            phase="review",
            detail=_with_warning(
                reviewer_verdict.detail,
                read_role_task_ledger(session_dir).tasks[reviewer_task_id].isolation_warning,
            ),
            task_id=reviewer_task_id,
        )


def _pin_gated_content(
    *,
    session_dir: Path,
    proposal_id: str | None,
    gate_outcome: object,
    history: list[CycleHistoryEvent],
    resolved_cli: str | None,
) -> GatedContentPin | StopRecord:
    target_relpath = getattr(gate_outcome, "target_relpath", None)
    patched_content = getattr(gate_outcome, "patched_content", None)
    content_hash = getattr(gate_outcome, "content_hash", None)
    if not isinstance(target_relpath, str) or not target_relpath.strip():
        return _pin_failure(
            history=history,
            resolved_cli=resolved_cli,
            detail="passing gate did not provide target_relpath",
            reason_code="gated-content-missing",
        )
    if not isinstance(patched_content, str):
        return _pin_failure(
            history=history,
            resolved_cli=resolved_cli,
            detail="passing gate did not provide patched_content",
            reason_code="gated-content-missing",
        )
    if not isinstance(content_hash, str) or not content_hash.strip():
        return _pin_failure(
            history=history,
            resolved_cli=resolved_cli,
            detail="passing gate did not provide content_hash",
            reason_code="gated-content-missing",
        )

    computed_hash = hashlib.sha256(patched_content.encode("utf-8")).hexdigest()
    if computed_hash != content_hash:
        return _pin_failure(
            history=history,
            resolved_cli=resolved_cli,
            detail="passing gate content_hash did not match patched_content",
            reason_code="gated-content-divergence",
        )

    safe_id = _safe_filename(proposal_id or "unknown")
    patched_path = session_dir / "patched" / f"{safe_id}.patch"
    _atomic_write_text(patched_path, patched_content)
    return GatedContentPin(
        patched_path=patched_path,
        content_hash=content_hash,
        target_relpath=target_relpath,
    )


def _pin_failure(
    *,
    history: list[CycleHistoryEvent],
    resolved_cli: str | None,
    detail: str,
    reason_code: str,
) -> StopRecord:
    return StopRecord(
        reason_code=reason_code,
        role="producer",
        resolved_cli=resolved_cli,
        phase="gate",
        detail=detail,
        cycle_history=list(history),
    )


@contextmanager
def _reviewer_workspace(
    *,
    target_repo_root: Path,
    artifact_path: Path,
    gated_target_relpath: str | None,
    base_ref: str,
) -> Iterator[ReviewerWorkspace]:
    used_git_worktree = False
    try:
        with git_worktree(target_repo_root, base_ref=base_ref) as worktree:
            isolated_artifact = _copy_artifact_into_workspace(
                artifact_path=artifact_path,
                target_repo_root=target_repo_root,
                workspace=worktree.path,
            )
            used_git_worktree = True
            yield ReviewerWorkspace(cwd=worktree.path, artifact_path=isolated_artifact)
            return
    except (OSError, ValueError, WorktreeError) as exc:
        if used_git_worktree:
            raise
        warning = f"reviewer-isolation-fallback: {exc}"

    with tempfile.TemporaryDirectory(prefix="arbor-tandem-review-") as temp_name:
        workspace = Path(temp_name)
        isolated_artifact = _copy_artifact_into_workspace(
            artifact_path=artifact_path,
            target_repo_root=target_repo_root,
            workspace=workspace,
        )
        _copy_target_file_into_workspace(
            target_repo_root=target_repo_root,
            workspace=workspace,
            gated_target_relpath=gated_target_relpath,
        )
        yield ReviewerWorkspace(cwd=workspace, artifact_path=isolated_artifact, warning=warning)


def _copy_artifact_into_workspace(
    *,
    artifact_path: Path,
    target_repo_root: Path,
    workspace: Path,
) -> Path:
    try:
        relpath = artifact_path.resolve().relative_to(target_repo_root.resolve())
    except ValueError:
        relpath = Path("gated-artifacts") / artifact_path.name
    target = workspace / relpath
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(artifact_path, target)
    return target


def _copy_target_file_into_workspace(
    *,
    target_repo_root: Path,
    workspace: Path,
    gated_target_relpath: str | None,
) -> None:
    if gated_target_relpath is None:
        return
    relpath = _safe_repo_relpath(gated_target_relpath)
    if relpath is None:
        return
    source = target_repo_root / relpath
    if not source.is_file():
        return
    target = workspace / relpath
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)


def _safe_repo_relpath(value: str) -> Path | None:
    relpath = Path(value)
    if relpath.is_absolute() or any(part == ".." for part in relpath.parts):
        return None
    return relpath


def _safe_filename(value: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in "._-" else "-" for ch in value.strip())
    return safe or "unknown"


def _with_warning(detail: str, warning: str | None) -> str:
    if warning is None:
        return detail
    return f"{detail} ({warning})"


def _resolved_cli(preflight: TandemPreflightResult, role: str, fallback: str) -> str:
    identity = preflight.identities.get(role)
    if identity is None:
        return fallback
    return identity.resolved_cli


def _inputs_hash(*parts: object) -> str:
    payload = json.dumps(parts, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _producer_task(
    base_task: str,
    feedback_detail: str | None,
    repair_hints: list[str],
) -> str:
    lines = [
        base_task,
        "",
        "Role: producer. Write one proposal JSON artifact at the agreed proposal path.",
        "Do not edit target docs directly; persist the proposal artifact instead.",
    ]
    if feedback_detail:
        lines.extend(["", "Repair feedback:", feedback_detail])
    if repair_hints:
        lines.extend(["", "Repair hints:"])
        lines.extend(f"- {hint}" for hint in repair_hints)
    return "\n".join(lines)


def _reviewer_task(
    base_task: str,
    artifact_path: Path,
    repair_hints: list[str],
) -> str:
    lines = [
        base_task,
        "",
        "Role: independent reviewer. Review only the current gated artifact and inputs.",
        f"Gated artifact: {artifact_path}",
        "Return the Arbor reviewer-verdict YAML envelope.",
    ]
    if repair_hints:
        lines.extend(["", "Prior repair hints:"])
        lines.extend(f"- {hint}" for hint in repair_hints)
    return "\n".join(lines)


def _snapshot_artifacts(root: Path) -> dict[Path, tuple[int, int]]:
    snapshot: dict[Path, tuple[int, int]] = {}
    for path in root.glob(PROPOSAL_GLOB):
        try:
            stat = path.stat()
        except OSError:
            continue
        snapshot[path] = (stat.st_mtime_ns, stat.st_size)
    return snapshot


def _discover_artifact(root: Path, before: dict[Path, tuple[int, int]]) -> Path | None:
    candidates: list[Path] = []
    all_paths = sorted(root.glob(PROPOSAL_GLOB))
    for path in all_paths:
        try:
            stat = path.stat()
        except OSError:
            continue
        if before.get(path) != (stat.st_mtime_ns, stat.st_size):
            candidates.append(path)
    if not candidates:
        return None
    return max(candidates, key=lambda item: item.stat().st_mtime_ns)


def _artifact_signature(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _next_repair(
    *,
    session_dir: Path,
    task_id: str,
    history: list[CycleHistoryEvent],
    repairs_used: int,
    repair_budget: int,
    role: str,
    resolved_cli: str,
    phase: str,
    detail: str,
    stop_reason_code: str | None = None,
) -> TandemDriverResult | None:
    if repairs_used < repair_budget:
        return None
    return _stop(
        session_dir=session_dir,
        history=history,
        reason_code=stop_reason_code or "blocked",
        role=role,
        resolved_cli=resolved_cli,
        phase=phase,
        detail=detail,
        task_id=task_id,
    )


def _stop(
    *,
    session_dir: Path,
    history: list[CycleHistoryEvent],
    reason_code: str,
    role: str,
    resolved_cli: str | None,
    phase: str,
    detail: str,
    task_id: str | None = None,
) -> TandemDriverResult:
    record = StopRecord(
        reason_code=reason_code,
        role=role,
        resolved_cli=resolved_cli,
        phase=phase,
        detail=detail,
        cycle_history=list(history),
    )
    _write_stop_record(session_dir, record)
    if task_id is not None:
        ledger = read_role_task_ledger(session_dir)
        existing = ledger.tasks.get(task_id)
        if existing is not None:
            put_role_task_record(
                session_dir,
                task_id,
                existing.model_copy(update={"status": "stopped", "verdict": reason_code}),
            )
    return TandemDriverResult(
        status="stopped",
        ledger=read_role_task_ledger(session_dir),
        stop_record=record,
        cycle_history=list(history),
    )


def _write_stop_record(session_dir: Path, record: StopRecord) -> Path:
    payload = asdict(record)
    return atomic_write_json(session_dir / STOP_RECORD_NAME, payload)


def _atomic_write_text(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fp:
            fp.write(text)
            fp.flush()
            os.fsync(fp.fileno())
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return path
