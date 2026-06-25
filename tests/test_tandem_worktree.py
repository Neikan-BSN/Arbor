from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from arbor.tandem.worktree import git_worktree


def _run_git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def _init_repo(repo: Path) -> None:
    if shutil.which("git") is None:
        pytest.skip("git is not available")
    repo.mkdir()
    assert _run_git(repo, "init").returncode == 0
    assert _run_git(repo, "config", "user.email", "test@example.invalid").returncode == 0
    assert _run_git(repo, "config", "user.name", "Test User").returncode == 0
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    assert _run_git(repo, "add", "README.md").returncode == 0
    assert _run_git(repo, "commit", "-m", "initial").returncode == 0
    assert _run_git(repo, "branch", "-M", "main").returncode == 0


def test_git_worktree_creates_branch_and_removes_worktree(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    created_path: Path

    with git_worktree(repo, base_ref="main", branch="maintainer/fix-fp-test") as worktree:
        created_path = worktree.path
        assert created_path != repo
        assert (created_path / "README.md").read_text(encoding="utf-8") == "base\n"
        branch = _run_git(created_path, "branch", "--show-current")
        assert branch.returncode == 0
        assert branch.stdout.strip() == "maintainer/fix-fp-test"

    assert not created_path.exists()


def test_git_worktree_detached_variant_has_no_branch(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)

    with git_worktree(repo, base_ref="main") as worktree:
        branch = _run_git(worktree.path, "branch", "--show-current")
        assert branch.returncode == 0
        assert branch.stdout.strip() == ""
