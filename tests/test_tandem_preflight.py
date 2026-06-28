from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import typer

from arbor.cli.commands.local_cmd import CliProbe
from arbor.core.config_schema import RolesConfig
from arbor.tandem.config import TandemConfig
from arbor.tandem.paid_guard import PaidBackendError
from arbor.tandem.preflight import run_tandem_preflight


def _probe(name: str, *, runnable: bool = True) -> CliProbe:
    return CliProbe(
        name=name,
        path=f"/usr/bin/{name}" if runnable else None,
        runnable=runnable,
        version=f"{name} 1.0" if runnable else None,
        error=None if runnable else "not found on PATH",
    )


def test_preflight_stops_when_any_bound_cli_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paid_calls: list[str] = []

    def fake_probe(name: str) -> CliProbe:
        return _probe(name, runnable=name == "claude")

    def fake_paid(*args, **kwargs):
        paid_calls.append(kwargs.get("cli") or args[1])

    monkeypatch.setattr("arbor.tandem.preflight.probe_cli", fake_probe)
    monkeypatch.setattr("arbor.tandem.preflight.assert_no_paid_backend", fake_paid)

    result = run_tandem_preflight(TandemConfig(), cwd=tmp_path, env={})

    assert not result.ok
    assert result.reason_code == "cli-missing"
    assert paid_calls == []


def test_preflight_stops_on_provider_credential_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("arbor.tandem.preflight.probe_cli", lambda name: _probe(name))

    result = run_tandem_preflight(
        TandemConfig(base_url="http://127.0.0.1:4000/v1"),
        cwd=tmp_path,
        env={"ANTHROPIC_AUTH_TOKEN": "token"},
    )

    assert not result.ok
    assert result.reason_code == "paid-resolution"


def test_preflight_stops_on_paid_backend_resolution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("arbor.tandem.preflight.probe_cli", lambda name: _probe(name))

    def fake_paid(*args, **kwargs):
        raise PaidBackendError("paid backend")

    monkeypatch.setattr("arbor.tandem.preflight.assert_no_paid_backend", fake_paid)

    result = run_tandem_preflight(TandemConfig(), cwd=tmp_path, env={})

    assert not result.ok
    assert result.reason_code == "paid-resolution"


def test_preflight_stops_when_auto_resolves_to_reviewer_cli(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_probe(name: str) -> CliProbe:
        return _probe(name, runnable=name == "codex")

    monkeypatch.setattr("arbor.tandem.preflight.probe_cli", fake_probe)
    monkeypatch.setattr("arbor.tandem.preflight.assert_no_paid_backend", lambda *args, **kwargs: None)
    monkeypatch.setattr("arbor.tandem.preflight.require_wsl_native_path", lambda path, *, label: path)

    result = run_tandem_preflight(
        TandemConfig(roles=RolesConfig(producer="auto", reviewer="codex")),
        cwd=tmp_path,
        env={},
    )

    assert not result.ok
    assert result.reason_code == "independence-collapse"


def test_preflight_stops_on_windows_mount_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("arbor.tandem.preflight.probe_cli", lambda name: _probe(name))
    monkeypatch.setattr("arbor.tandem.preflight.assert_no_paid_backend", lambda *args, **kwargs: None)

    def fail_wsl(path: Path, *, label: str) -> Path:
        raise typer.BadParameter("under /mnt")

    monkeypatch.setattr("arbor.tandem.preflight.require_wsl_native_path", fail_wsl)

    result = run_tandem_preflight(
        TandemConfig(base_url="http://127.0.0.1:4000/v1"),
        cwd=Path("/mnt/c/project"),
        env={},
    )

    assert not result.ok
    assert result.reason_code == "not-wsl-native"


def test_preflight_stops_when_gh_identity_unknown(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("arbor.tandem.preflight.probe_cli", lambda name: _probe(name))
    monkeypatch.setattr("arbor.tandem.preflight.assert_no_paid_backend", lambda *args, **kwargs: None)
    monkeypatch.setattr("arbor.tandem.preflight.require_wsl_native_path", lambda path, *, label: path)
    monkeypatch.setattr(
        "arbor.tandem.preflight.subprocess.run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 1, stdout="", stderr="not logged in"),
    )

    result = run_tandem_preflight(
        TandemConfig(base_url="http://127.0.0.1:4000/v1"),
        cwd=tmp_path,
        env={},
    )

    assert not result.ok
    assert result.reason_code == "gh-identity-unknown"


def test_preflight_passes_with_distinct_clis_non_paid_config_and_gh_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("arbor.tandem.preflight.probe_cli", lambda name: _probe(name))
    monkeypatch.setattr("arbor.tandem.preflight.assert_no_paid_backend", lambda *args, **kwargs: None)
    monkeypatch.setattr("arbor.tandem.preflight.require_wsl_native_path", lambda path, *, label: path)
    monkeypatch.setattr(
        "arbor.tandem.preflight.subprocess.run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0],
            0,
            stdout="",
            stderr="Logged in to github.com account user01",
        ),
    )

    result = run_tandem_preflight(
        TandemConfig(base_url="http://127.0.0.1:4000/v1"),
        cwd=tmp_path,
        env={"PATH": "/usr/bin"},
    )

    assert result.ok
    assert result.reason_code is None
    assert result.identities["producer"].resolved_cli == "claude"
    assert result.identities["reviewer"].resolved_cli == "codex"
    assert "user01" in (result.gh_identity or "")
