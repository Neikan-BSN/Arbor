"""Injectable GitHub PR runner for tandem-ready proposals."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Protocol, Sequence

from .contracts import read_producer_artifact
from .ledger import (
    PlannedPr,
    branch_key_for_proposal_id,
    plan_pr_creations,
    put_role_task_record,
    read_role_task_ledger,
)

OPERATOR_DISCLOSURE = "operator-owned; bot credential not configured"


class SubprocessRunner(Protocol):
    """Callable seam matching ``subprocess.run`` for offline gh tests."""

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
        """Run a subprocess command."""


@dataclass(frozen=True)
class DraftPrResult:
    """Structured outcome of the tandem draft-PR step."""

    planned: bool
    mutated: bool
    branch: str | None
    pr_url: str | None
    dry_run: bool
    disclosure: str | None
    detail: str
    title: str | None = None
    body: str | None = None
    reason_code: str | None = None


def open_ready_draft_pr(
    *,
    session_dir: Path,
    target_repo_root: Path,
    gh_identity: str,
    proposal_id: str | None = None,
    dry_run: bool = True,
    base_branch: str = "main",
    runner: SubprocessRunner | None = None,
    env: Mapping[str, str] | None = None,
) -> DraftPrResult:
    """Open or report one draft PR for a ready tandem proposal.

    The default is propose-only: return the branch, title, and body without
    calling ``gh`` or mutating GitHub state.
    """

    ledger = read_role_task_ledger(session_dir)
    existing = _ledger_pr_result(ledger, proposal_id=proposal_id, dry_run=dry_run)
    if existing is not None:
        return existing

    plans = _select_plans(ledger, proposal_id=proposal_id)
    if not plans:
        detail = "no ready tandem proposal without a PR URL was found in the ledger"
        if proposal_id:
            detail = f"{detail} for proposal {proposal_id}"
        return DraftPrResult(
            planned=False,
            mutated=False,
            branch=branch_key_for_proposal_id(proposal_id) if proposal_id else None,
            pr_url=None,
            dry_run=dry_run,
            disclosure=_disclosure_for_identity(gh_identity),
            detail=detail,
            reason_code="no-ready-proposal",
        )

    plan = plans[0]
    title = _draft_title(plan)
    body = _draft_body(plan, gh_identity=gh_identity)
    disclosure = _disclosure_for_identity(gh_identity)

    if dry_run:
        return DraftPrResult(
            planned=True,
            mutated=False,
            branch=plan.branch,
            pr_url=None,
            dry_run=True,
            disclosure=disclosure,
            detail="dry-run: would push branch and open one draft PR",
            title=title,
            body=body,
        )

    run = runner if runner is not None else subprocess.run

    existing_pr = _existing_pr_url(
        branch=plan.branch,
        target_repo_root=target_repo_root,
        runner=run,
        env=env,
    )
    if existing_pr.returncode != 0:
        return _failure(plan, dry_run=dry_run, disclosure=disclosure, detail=existing_pr.detail)
    if existing_pr.pr_url:
        _record_pr_url(session_dir, plan, existing_pr.pr_url)
        return DraftPrResult(
            planned=False,
            mutated=False,
            branch=plan.branch,
            pr_url=existing_pr.pr_url,
            dry_run=False,
            disclosure=disclosure,
            detail="existing open PR found for branch; no duplicate opened",
            title=title,
            body=body,
            reason_code="pr-exists",
        )

    branch_check = _remote_branch_exists(
        branch=plan.branch,
        target_repo_root=target_repo_root,
        runner=run,
        env=env,
    )
    if branch_check.returncode != 0:
        return _failure(plan, dry_run=dry_run, disclosure=disclosure, detail=branch_check.detail)
    if branch_check.exists:
        return DraftPrResult(
            planned=False,
            mutated=False,
            branch=plan.branch,
            pr_url=None,
            dry_run=False,
            disclosure=disclosure,
            detail="remote branch already exists for proposal; no duplicate PR opened",
            title=title,
            body=body,
            reason_code="branch-exists",
        )

    push = _run(
        ["git", "push", "origin", f"HEAD:refs/heads/{plan.branch}"],
        target_repo_root=target_repo_root,
        runner=run,
        env=env,
    )
    if push.returncode != 0:
        return _failure(
            plan,
            dry_run=dry_run,
            disclosure=disclosure,
            detail=f"git push failed: {_combined_output(push)}",
        )

    create = _run(
        [
            "gh",
            "pr",
            "create",
            "--draft",
            "--head",
            plan.branch,
            "--base",
            base_branch,
            "--title",
            title,
            "--body",
            body,
        ],
        target_repo_root=target_repo_root,
        runner=run,
        env=env,
    )
    if create.returncode != 0:
        return _failure(
            plan,
            dry_run=dry_run,
            disclosure=disclosure,
            detail=f"gh pr create failed: {_combined_output(create)}",
        )

    pr_url = _first_line(create.stdout)
    _record_pr_url(session_dir, plan, pr_url)
    return DraftPrResult(
        planned=False,
        mutated=True,
        branch=plan.branch,
        pr_url=pr_url,
        dry_run=False,
        disclosure=disclosure,
        detail="opened one draft PR",
        title=title,
        body=body,
    )


@dataclass(frozen=True)
class _ExistingPr:
    returncode: int
    pr_url: str | None
    detail: str


@dataclass(frozen=True)
class _BranchCheck:
    returncode: int
    exists: bool
    detail: str


def _select_plans(ledger: object, *, proposal_id: str | None) -> list[PlannedPr]:
    plans = plan_pr_creations(ledger)  # type: ignore[arg-type]
    if proposal_id is None:
        return plans
    return [plan for plan in plans if plan.proposal_id == proposal_id]


def _ledger_pr_result(
    ledger: object,
    *,
    proposal_id: str | None,
    dry_run: bool,
) -> DraftPrResult | None:
    tasks = getattr(ledger, "tasks", {})
    for record in tasks.values():
        if not record.pr_url or not record.result_artifact_path:
            continue
        ref = read_producer_artifact(record.result_artifact_path)
        if not ref.ok or ref.proposal_id is None:
            continue
        if proposal_id is not None and ref.proposal_id != proposal_id:
            continue
        return DraftPrResult(
            planned=False,
            mutated=False,
            branch=branch_key_for_proposal_id(ref.proposal_id),
            pr_url=record.pr_url,
            dry_run=dry_run,
            disclosure=None,
            detail="ledger already records a PR URL for this proposal",
            reason_code="ledger-pr-exists",
        )
    return None


def _record_pr_url(session_dir: Path, plan: PlannedPr, pr_url: str | None) -> None:
    if not pr_url:
        return
    ledger = read_role_task_ledger(session_dir)
    record = ledger.tasks.get(plan.task_id)
    if record is None:
        return
    put_role_task_record(session_dir, plan.task_id, record.model_copy(update={"pr_url": pr_url}))


def _existing_pr_url(
    *,
    branch: str,
    target_repo_root: Path,
    runner: SubprocessRunner,
    env: Mapping[str, str] | None,
) -> _ExistingPr:
    result = _run(
        [
            "gh",
            "pr",
            "list",
            "--head",
            branch,
            "--state",
            "open",
            "--json",
            "url",
            "--jq",
            ".[0].url // empty",
        ],
        target_repo_root=target_repo_root,
        runner=runner,
        env=env,
    )
    if result.returncode != 0:
        return _ExistingPr(result.returncode, None, f"gh pr list failed: {_combined_output(result)}")
    return _ExistingPr(0, _first_line(result.stdout) or None, "ok")


def _remote_branch_exists(
    *,
    branch: str,
    target_repo_root: Path,
    runner: SubprocessRunner,
    env: Mapping[str, str] | None,
) -> _BranchCheck:
    result = _run(
        ["gh", "api", f"repos/:owner/:repo/git/ref/heads/{branch}"],
        target_repo_root=target_repo_root,
        runner=runner,
        env=env,
    )
    if result.returncode == 0:
        return _BranchCheck(0, True, "remote branch exists")
    output = _combined_output(result)
    if _is_not_found(output):
        return _BranchCheck(0, False, "remote branch absent")
    return _BranchCheck(result.returncode, False, f"gh branch lookup failed: {output}")


def _run(
    args: Sequence[str],
    *,
    target_repo_root: Path,
    runner: SubprocessRunner,
    env: Mapping[str, str] | None,
) -> subprocess.CompletedProcess[str]:
    return runner(
        list(args),
        cwd=str(target_repo_root),
        capture_output=True,
        text=True,
        check=False,
        env=dict(env) if env is not None else None,
    )


def _failure(
    plan: PlannedPr,
    *,
    dry_run: bool,
    disclosure: str | None,
    detail: str,
) -> DraftPrResult:
    return DraftPrResult(
        planned=False,
        mutated=False,
        branch=plan.branch,
        pr_url=None,
        dry_run=dry_run,
        disclosure=disclosure,
        detail=detail,
        reason_code="gh-failed",
    )


def _draft_title(plan: PlannedPr) -> str:
    return f"docs: apply maintainer proposal {plan.proposal_id}"


def _draft_body(plan: PlannedPr, *, gh_identity: str) -> str:
    lines = [
        "Draft PR opened by Arbor dual-CLI tandem orchestration.",
        "",
        f"Proposal: `{plan.proposal_id}`",
        f"Artifact: `{plan.result_artifact_path}`",
        f"Branch: `{plan.branch}`",
        f"GitHub auth identity: {_identity_summary(gh_identity)}",
        "",
        "The deterministic additive gate and an independent reviewer passed before this PR step.",
    ]
    disclosure = _disclosure_for_identity(gh_identity)
    if disclosure:
        lines.extend(["", f"Disclosure: {disclosure}."])
    lines.extend(["", "This is a draft PR. Human merge authority is unchanged."])
    return "\n".join(lines)


def _disclosure_for_identity(gh_identity: str) -> str | None:
    identity = gh_identity.lower()
    bot_markers = ("[bot]", " bot", "bot ", "github-actions", "app/")
    if any(marker in identity for marker in bot_markers):
        return None
    return OPERATOR_DISCLOSURE


def _identity_summary(gh_identity: str) -> str:
    for line in gh_identity.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped[:240]
    return "unknown"


def _first_line(text: str | None) -> str | None:
    if text is None:
        return None
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return None


def _combined_output(result: subprocess.CompletedProcess[str]) -> str:
    combined = "\n".join(part for part in (result.stdout, result.stderr) if part)
    return combined.strip() or f"exit {result.returncode}"


def _is_not_found(output: str) -> bool:
    lowered = output.lower()
    return "not found" in lowered or "http 404" in lowered
