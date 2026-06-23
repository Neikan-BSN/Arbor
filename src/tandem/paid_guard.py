"""Fail-closed paid-backend guard and CLI subprocess environment scrubber."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from ..core import resolve_backend

PROVIDER_CREDENTIAL_ENV_VARS: tuple[str, ...] = (
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "OPENAI_API_KEY",
)
_HOSTED_BACKENDS = frozenset({"anthropic", "openai-responses", "openai-chat"})


@dataclass(frozen=True)
class PaidBackendError(RuntimeError):
    """Raised when a role binding would resolve to a hosted paid backend."""

    message: str

    def __str__(self) -> str:
        return self.message


def assert_no_paid_backend(
    *,
    role: str,
    cli: str,
    provider: str | None,
    openai_api: str | None,
    model: str | None,
    base_url: str | None,
) -> str | None:
    """Hard-stop role bindings that would route through a hosted provider."""

    if cli != "claude":
        return None

    backend = resolve_backend(provider, openai_api, model, base_url)
    if backend in _HOSTED_BACKENDS and not base_url:
        raise PaidBackendError(
            f"{role} role bound to {cli} resolves to paid backend {backend}; "
            "configure an explicit local base_url or use a subscription CLI binding"
        )
    return backend


def scrub_provider_env(env: Mapping[str, str]) -> dict[str, str]:
    """Return a copy with provider API credentials removed."""

    return {
        key: value
        for key, value in env.items()
        if key not in PROVIDER_CREDENTIAL_ENV_VARS
    }
