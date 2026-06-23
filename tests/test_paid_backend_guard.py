from __future__ import annotations

import pytest

import arbor.core as core
from arbor.tandem import paid_guard
from arbor.tandem.paid_guard import PaidBackendError, assert_no_paid_backend, scrub_provider_env


def test_default_claude_binding_hard_stops_paid_backend() -> None:
    with pytest.raises(PaidBackendError, match="paid backend"):
        assert_no_paid_backend(
            role="producer",
            cli="claude",
            provider="auto",
            openai_api="responses",
            model="claude-sonnet-4-20250514",
            base_url=None,
        )


def test_claude_binding_with_explicit_local_base_url_is_allowed() -> None:
    backend = assert_no_paid_backend(
        role="producer",
        cli="claude",
        provider="auto",
        openai_api="responses",
        model="claude-sonnet-4-20250514",
        base_url="http://127.0.0.1:4000/v1",
    )

    assert backend == "litellm"


def test_codex_binding_skips_anthropic_backend_resolution(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_resolve(*args, **kwargs):
        raise AssertionError("codex-bound roles must not run Anthropic backend checks")

    monkeypatch.setattr(paid_guard, "resolve_backend", fail_resolve)

    assert_no_paid_backend(
        role="reviewer",
        cli="codex",
        provider="auto",
        openai_api="responses",
        model="claude-sonnet-4-20250514",
        base_url=None,
    )


def test_scrub_provider_env_removes_provider_credentials() -> None:
    env = {
        "ANTHROPIC_API_KEY": "sk-anthropic",
        "ANTHROPIC_AUTH_TOKEN": "token",
        "OPENAI_API_KEY": "sk-openai",
        "PATH": "/usr/bin",
    }

    scrubbed = scrub_provider_env(env)

    assert scrubbed == {"PATH": "/usr/bin"}
    assert env["ANTHROPIC_API_KEY"] == "sk-anthropic"


def test_paid_guard_constructs_zero_llm_providers(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_create_provider(*args, **kwargs):
        raise AssertionError("paid guard must not construct LLM providers")

    monkeypatch.setattr(core, "create_provider", fail_create_provider)

    assert_no_paid_backend(
        role="producer",
        cli="claude",
        provider="auto",
        openai_api="responses",
        model="claude-sonnet-4-20250514",
        base_url="http://127.0.0.1:4000/v1",
    )
