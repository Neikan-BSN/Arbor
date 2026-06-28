"""Synchronous git worktree lifecycle helpers for tandem roles."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Mapping, Protocol, Sequence


class WorktreeRunner(Protocol):
    """Callable seam matching ``subprocess.run`` for worktree operations."""

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
        """Run one subprocess command."""


class WorktreeError(RuntimeError):
    """Raised when a git worktree cannot be created or removed."""


@dataclass(frozen=True)
class GitWorktree:
    """Created git worktree metadata."""

    path: Path
    branch: str | None
    base_ref: str


@contextmanager
def git_worktree(
    repo_root: Path,
    *,
    base_ref: str,
    branch: str | None = None,
    runner: WorktreeRunner | None = None,
    env: Mapping[str, str] | None = None,
) -> Iterator[GitWorktree]:
    """Create a temporary git worktree and remove it on exit.

    When ``branch`` is provided, the worktree is created with
    ``git worktree add -b <branch> <tmp> <base_ref>``. Without ``branch`` it is
    detached at ``base_ref`` for read-only reviewer grounding.
    """

    run = runner if runner is not None else subprocess.run
    worktree_path = _new_worktree_path()
    created = False
    try:
        args = ["git", "-C", str(repo_root), "worktree", "add"]
        if branch is None:
            args.append("--detach")
        else:
            args.extend(["-b", branch])
        args.extend([str(worktree_path), base_ref])
        result = run(
            args,
            cwd=None,
            capture_output=True,
            text=True,
            check=False,
            env=dict(env) if env is not None else None,
        )
        if result.returncode != 0:
            _remove_path(worktree_path)
            raise WorktreeError(f"git worktree add failed: {_combined_output(result)}")
        created = True
        yield GitWorktree(path=worktree_path, branch=branch, base_ref=base_ref)
    finally:
        if created:
            result = run(
                ["git", "-C", str(repo_root), "worktree", "remove", "--force", str(worktree_path)],
                cwd=None,
                capture_output=True,
                text=True,
                check=False,
                env=dict(env) if env is not None else None,
            )
            if result.returncode != 0:
                _remove_path(worktree_path)
                raise WorktreeError(f"git worktree remove failed: {_combined_output(result)}")
        elif worktree_path.exists():
            _remove_path(worktree_path)


def _new_worktree_path() -> Path:
    base = Path(tempfile.mkdtemp(prefix="arbor-tandem-worktree-"))
    base.rmdir()
    return base


def _remove_path(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)


def _combined_output(result: subprocess.CompletedProcess[str]) -> str:
    combined = "\n".join(part for part in (result.stdout, result.stderr) if part)
    return combined.strip() or f"exit {result.returncode}"
