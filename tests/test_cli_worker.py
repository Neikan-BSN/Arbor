from __future__ import annotations

import subprocess
from pathlib import Path

from arbor.cli.commands import local_cmd
from arbor.tandem.cli_worker import run_cli_worker


def _skills_root(tmp_path: Path) -> Path:
    skills = tmp_path / "Arbor" / "skills"
    entrypoint = skills / "arbor-research-agent"
    entrypoint.mkdir(parents=True)
    (entrypoint / "SKILL.md").write_text("---\nname: test\n---\n", encoding="utf-8")
    return skills


def test_cli_worker_captures_successful_stdout(
    tmp_path: Path,
    monkeypatch,
) -> None:
    def fake_which(name: str) -> str | None:
        return f"/usr/bin/{name}" if name == "claude" else None

    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(args[0], 0, stdout="known text\n", stderr="")

    monkeypatch.setattr("arbor.tandem.cli_worker.shutil.which", fake_which)
    monkeypatch.setattr("arbor.tandem.cli_worker.subprocess.run", fake_run)

    result = run_cli_worker(
        cli="claude",
        cwd=tmp_path,
        skills_src=_skills_root(tmp_path),
        task="do work",
        env={},
    )

    assert result.ok
    assert result.stdout == "known text\n"
    assert result.stderr == ""
    assert result.returncode == 0
    assert result.invocations == 1


def test_cli_worker_keeps_nonzero_output_for_driver_decision(
    tmp_path: Path,
    monkeypatch,
) -> None:
    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(args[0], 42, stdout="valid output\n", stderr="warn\n")

    monkeypatch.setattr("arbor.tandem.cli_worker.shutil.which", lambda name: f"/bin/{name}")
    monkeypatch.setattr("arbor.tandem.cli_worker.subprocess.run", fake_run)

    result = run_cli_worker(
        cli="codex",
        cwd=tmp_path,
        skills_src=_skills_root(tmp_path),
        task="do work",
        env={},
    )

    assert result.ok
    assert result.returncode == 42
    assert result.stdout == "valid output\n"
    assert result.stderr == "warn\n"


def test_cli_worker_bounds_and_scrubs_captured_tail(
    tmp_path: Path,
    monkeypatch,
) -> None:
    token_text = (
        "prefix " * 20
        + "sk-abcdef1234567890 "
        + "ghp_abcdefghijklmnop "
        + "tail"
    )

    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(args[0], 0, stdout=token_text, stderr=token_text)

    monkeypatch.setattr("arbor.tandem.cli_worker.shutil.which", lambda name: f"/bin/{name}")
    monkeypatch.setattr("arbor.tandem.cli_worker.subprocess.run", fake_run)

    result = run_cli_worker(
        cli="claude",
        cwd=tmp_path,
        skills_src=_skills_root(tmp_path),
        task="do work",
        env={},
        tail_chars=80,
    )

    assert len(result.stdout) <= 80
    assert len(result.stderr) <= 80
    assert "sk-" not in result.stdout
    assert "ghp_" not in result.stdout
    assert "***" in result.stdout
    assert "sk-" not in result.stderr
    assert "ghp_" not in result.stderr


def test_cli_worker_returns_not_runnable_when_cli_missing(
    tmp_path: Path,
    monkeypatch,
) -> None:
    calls = []

    def fake_run(*args, **kwargs):
        calls.append(args)
        raise AssertionError("subprocess.run should not be called")

    monkeypatch.setattr("arbor.tandem.cli_worker.shutil.which", lambda name: None)
    monkeypatch.setattr("arbor.tandem.cli_worker.subprocess.run", fake_run)

    result = run_cli_worker(
        cli="claude",
        cwd=tmp_path,
        skills_src=_skills_root(tmp_path),
        task="do work",
        env={},
    )

    assert not result.ok
    assert result.returncode is None
    assert result.invocations == 0
    assert "not found on PATH" in result.stderr
    assert calls == []


def test_build_command_is_pure_for_claude_and_codex(tmp_path: Path) -> None:
    skills = _skills_root(tmp_path)

    claude = local_cmd._build_command(
        agent="claude",
        cwd=tmp_path,
        skills_src=skills,
        task="task text",
        claude_permission_mode="plan",
    )
    codex = local_cmd._build_command(
        agent="codex",
        cwd=tmp_path,
        skills_src=skills,
        task="task text",
    )

    assert claude[:5] == ["claude", "--print", "--output-format", "text", "--add-dir"]
    assert "--permission-mode" in claude
    assert claude[-1].endswith("task text\n")
    assert codex[:4] == ["codex", "exec", "--add-dir", str(tmp_path / "Arbor")]
    assert codex[4:6] == ["-C", str(tmp_path)]
