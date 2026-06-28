from __future__ import annotations

import logging

import pytest
from pydantic import ValidationError

from arbor.core.config_schema import RolesConfig, redacted_snapshot
from arbor.core.config_resolve import _warn_unknown_nested_blocks
from arbor.tandem.config import TandemConfig


def test_roles_config_defaults_and_imperative_override() -> None:
    config = TandemConfig()

    assert config.roles.producer == "claude"
    assert config.roles.reviewer == "codex"
    assert config.roles.repair_budget == 2

    config.roles.producer = "gemini"

    assert config.roles.producer == "gemini"
    assert config.roles.reviewer == "codex"


def test_roles_config_rejects_same_concrete_cli() -> None:
    with pytest.raises(ValidationError, match="different CLI"):
        RolesConfig(producer="claude", reviewer="claude")


def test_roles_config_rejects_both_auto() -> None:
    with pytest.raises(ValidationError, match="cannot both be auto"):
        RolesConfig(producer="auto", reviewer="auto")


def test_roles_config_allows_auto_until_preflight_resolution() -> None:
    config = RolesConfig(producer="auto", reviewer="codex")

    assert config.producer == "auto"
    assert config.reviewer == "codex"


def test_roles_unknown_nested_key_warns(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.WARNING, logger="arbor.core.config_resolve")

    _warn_unknown_nested_blocks({"roles": {"producer": "claude", "bogus": True}}, location="config")

    assert "Unknown config key config.roles.bogus will be ignored" in caplog.text


def test_tandem_config_redacted_snapshot_masks_sibling_secret() -> None:
    config = TandemConfig(api_key="sk-secret", roles=RolesConfig(producer="claude", reviewer="codex"))

    snapshot = redacted_snapshot(config)

    assert snapshot["roles"]["producer"] == "claude"
    assert snapshot["roles"]["reviewer"] == "codex"
    assert snapshot["llm"]["api_key"] == "***REDACTED***"


def test_roles_validator_performs_no_probe_io(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_probe(name: str):
        raise AssertionError("roles validation must not probe subprocess CLIs")

    monkeypatch.setattr("arbor.cli.commands.local_cmd._probe_cli", fail_probe)

    assert RolesConfig(producer="auto", reviewer="codex").producer == "auto"
