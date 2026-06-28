"""`arbor tandem` - dual-CLI tandem role orchestration."""

from __future__ import annotations

import importlib.util
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

import typer

from ...core.config_schema import RolesConfig
from ...tandem.cli_worker import run_cli_worker
from ...tandem.config import TandemConfig
from ...tandem.driver import (
    DEFAULT_INVOCATION_CAP,
    CliWorker,
    PreflightRunner,
    TandemDriverResult,
    run_tandem_driver,
)
from ...tandem.gate import Gate
from ...tandem.gh_runner import DraftPrResult, SubprocessRunner, open_ready_draft_pr
from ...tandem.preflight import TandemPreflightResult, run_tandem_preflight
from .local_cmd import require_wsl_native_path


tandem_app = typer.Typer(
    name="tandem",
    help="Run a producer/reviewer tandem workflow through local subscription CLIs.",
    no_args_is_help=True,
)


@dataclass(frozen=True)
class TandemCommandResult:
    """Result returned by the CLI composition root."""

    driver: TandemDriverResult
    pr: DraftPrResult | None
    preflight: TandemPreflightResult


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _default_skills_src() -> Path:
    return _repo_root() / "skills"


def _default_gate_adapter_path() -> Path:
    return _repo_root() / "examples" / "wiki_forge_doc_maintainer" / "additive_gate_adapter.py"


def load_gate_adapter(path: Path) -> Gate:
    """Load a target-specific gate adapter by file path."""

    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise typer.BadParameter(f"gate adapter does not exist: {resolved}")
    module_name = f"arbor_tandem_gate_adapter_{abs(hash(resolved))}"
    spec = importlib.util.spec_from_file_location(module_name, resolved)
    if spec is None or spec.loader is None:
        raise typer.BadParameter(f"gate adapter is not loadable: {resolved}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    build_gate = getattr(module, "build_gate", None)
    if callable(build_gate):
        loaded = build_gate()
    else:
        loaded = getattr(module, "gate", None)
    if not callable(loaded):
        raise typer.BadParameter(
            f"gate adapter must expose callable `gate` or `build_gate()`: {resolved}"
        )
    return loaded


def execute_tandem_run(
    *,
    task: str,
    target_repo_root: Path,
    session_dir: Path,
    skills_src: Path,
    producer_cli: str = "claude",
    reviewer_cli: str = "codex",
    repair_budget: int = 2,
    base_url: str | None = None,
    claude_permission_mode: str | None = None,
    gate: Gate | None = None,
    gate_adapter_path: Path | None = None,
    dry_run: bool = True,
    base_branch: str = "main",
    env: Mapping[str, str] | None = None,
    worker: CliWorker = run_cli_worker,
    preflight_runner: PreflightRunner = run_tandem_preflight,
    subprocess_runner: SubprocessRunner | None = None,
    invocation_cap: int = DEFAULT_INVOCATION_CAP,
) -> TandemCommandResult:
    """Compose preflight, dynamic gate loading, driver run, and PR planning."""

    config_kwargs: dict[str, object] = {
        "roles": RolesConfig(
            producer=producer_cli,
            reviewer=reviewer_cli,
            repair_budget=repair_budget,
        ),
        "cwd": str(target_repo_root),
        "task": task,
    }
    if base_url is not None:
        config_kwargs["base_url"] = base_url
    config = TandemConfig(**config_kwargs)
    run_env = dict(os.environ if env is None else env)
    preflight = preflight_runner(config, cwd=target_repo_root, env=run_env)

    def cached_preflight(
        cached_config: TandemConfig,
        *,
        cwd: Path,
        env: Mapping[str, str],
    ) -> TandemPreflightResult:
        return preflight

    resolved_gate = gate
    if resolved_gate is None:
        resolved_gate = load_gate_adapter(gate_adapter_path or _default_gate_adapter_path())

    driver = run_tandem_driver(
        config,
        session_dir=session_dir,
        target_repo_root=target_repo_root,
        skills_src=skills_src,
        task=task,
        gate=resolved_gate,
        env=run_env,
        worker=worker,
        preflight_runner=cached_preflight,
        invocation_cap=invocation_cap,
        base_ref=base_branch,
        claude_permission_mode=claude_permission_mode,
    )

    pr_result: DraftPrResult | None = None
    if driver.status == "ready" and driver.proposal_id is not None:
        pr_result = open_ready_draft_pr(
            session_dir=session_dir,
            target_repo_root=target_repo_root,
            gh_identity=preflight.gh_identity or "gh identity unavailable",
            proposal_id=driver.proposal_id,
            dry_run=dry_run,
            base_branch=base_branch,
            runner=subprocess_runner,
            env=run_env,
        )

    return TandemCommandResult(driver=driver, pr=pr_result, preflight=preflight)


@tandem_app.command("run")
def run_command(
    task: str = typer.Argument(..., help="Tandem producer/reviewer task prompt."),
    target_repo_root: Path = typer.Option(
        Path("."),
        "--target-repo",
        "-C",
        help="WSL-native target repository root.",
        exists=False,
        file_okay=False,
    ),
    session_dir: Path = typer.Option(
        Path(".arbor/tandem-session"),
        "--session-dir",
        help="Directory for tandem ledger and verdict artifacts.",
        exists=False,
        file_okay=False,
    ),
    skills_src: Path = typer.Option(
        _default_skills_src(),
        "--skills-src",
        help="Path to this Arbor checkout's skills directory.",
        exists=False,
        file_okay=False,
    ),
    producer_cli: str = typer.Option(
        "claude",
        "--producer-cli",
        help="Producer role CLI binding, for example claude or codex.",
    ),
    reviewer_cli: str = typer.Option(
        "codex",
        "--reviewer-cli",
        help="Reviewer role CLI binding. Must resolve differently from producer.",
    ),
    repair_budget: int = typer.Option(
        2,
        "--repair-budget",
        min=0,
        help="Maximum repair cycles before the run fail-closes.",
    ),
    gate_adapter: Path = typer.Option(
        _default_gate_adapter_path(),
        "--gate-adapter",
        help="File path to a target-specific Arbor tandem gate adapter.",
        exists=False,
        dir_okay=False,
    ),
    base_url: str | None = typer.Option(
        None,
        "--base-url",
        help="Optional local endpoint marker for paid-backend preflight checks.",
    ),
    claude_permission_mode: str | None = typer.Option(
        None,
        "--claude-permission-mode",
        help=(
            "Permission mode for a claude-bound producer (e.g. acceptEdits) so "
            "it can persist the proposal artifact in headless --print mode. "
            "Applied to the producer role only."
        ),
    ),
    dry_run: bool = typer.Option(
        True,
        "--dry-run/--open-pr",
        help="Default dry-run reports the draft PR; --open-pr pushes and opens it.",
    ),
    base_branch: str = typer.Option(
        "main",
        "--base-branch",
        help="Base branch/ref for reviewer isolation and draft PR creation.",
    ),
) -> None:
    """Run the tandem workflow and plan or open one draft docs PR."""

    target = require_wsl_native_path(target_repo_root, label="target repo")
    skills = require_wsl_native_path(skills_src, label="skills directory")
    session = session_dir.expanduser()
    if not session.is_absolute():
        session = target / session

    try:
        result = execute_tandem_run(
            task=task,
            target_repo_root=target,
            session_dir=session,
            skills_src=skills,
            producer_cli=producer_cli,
            reviewer_cli=reviewer_cli,
            repair_budget=repair_budget,
            base_url=base_url,
            claude_permission_mode=claude_permission_mode,
            gate_adapter_path=gate_adapter,
            dry_run=dry_run,
            base_branch=base_branch,
        )
    except Exception as exc:
        typer.secho(f"tandem run failed: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc

    if result.driver.status == "ready":
        typer.secho(f"tandem ready: {result.driver.proposal_id}", fg=typer.colors.GREEN)
    else:
        detail = result.driver.stop_record.detail if result.driver.stop_record else "stopped"
        typer.secho(f"tandem stopped: {detail}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)

    if result.pr is not None:
        if result.pr.mutated:
            typer.echo(f"draft PR opened: {result.pr.pr_url}")
        elif result.pr.planned:
            typer.echo(f"dry-run draft PR branch: {result.pr.branch}")
            typer.echo(f"dry-run draft PR title: {result.pr.title}")
        else:
            typer.echo(result.pr.detail)
