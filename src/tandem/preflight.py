"""Fail-closed preflight checks for tandem role runs."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Mapping

import typer

from ..cli.commands.local_cmd import probe_cli, require_wsl_native_path
from .config import TandemConfig
from .paid_guard import (
    PROVIDER_CREDENTIAL_ENV_VARS,
    PaidBackendError,
    assert_no_paid_backend,
    scrub_provider_env,
)

ReasonCode = Literal[
    "cli-missing",
    "paid-resolution",
    "independence-collapse",
    "not-wsl-native",
    "gh-identity-unknown",
]

_SUPPORTED_ROLE_CLIS = ("claude", "codex")


@dataclass(frozen=True)
class TandemCliIdentity:
    role: str
    requested_cli: str
    resolved_cli: str
    path: str | None
    version: str | None


@dataclass(frozen=True)
class TandemPreflightResult:
    ok: bool
    reason_code: ReasonCode | None = None
    message: str = ""
    identities: dict[str, TandemCliIdentity] = field(default_factory=dict)
    gh_identity: str | None = None


def _failure(reason_code: ReasonCode, message: str) -> TandemPreflightResult:
    return TandemPreflightResult(ok=False, reason_code=reason_code, message=message)


def _resolve_role_cli(role: str, requested: str) -> TandemCliIdentity | TandemPreflightResult:
    if requested == "auto":
        probes = [probe_cli(name) for name in _SUPPORTED_ROLE_CLIS]
        for probe in probes:
            if probe.runnable:
                return TandemCliIdentity(
                    role=role,
                    requested_cli=requested,
                    resolved_cli=probe.name,
                    path=probe.path,
                    version=probe.version,
                )
        details = "; ".join(f"{probe.name}: {probe.error or 'not runnable'}" for probe in probes)
        return _failure("cli-missing", f"{role} auto CLI resolution failed: {details}")

    probe = probe_cli(requested)
    if not probe.runnable:
        return _failure(
            "cli-missing",
            f"{role} CLI {requested!r} is not runnable: {probe.error or 'unknown error'}",
        )
    return TandemCliIdentity(
        role=role,
        requested_cli=requested,
        resolved_cli=requested,
        path=probe.path,
        version=probe.version,
    )


def _provider_env_present(env: Mapping[str, str]) -> list[str]:
    return [name for name in PROVIDER_CREDENTIAL_ENV_VARS if env.get(name)]


def _gh_identity(env: Mapping[str, str]) -> str | None:
    try:
        result = subprocess.run(
            ["gh", "auth", "status"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
            env=scrub_provider_env(env),
        )
    except Exception:
        return None
    if result.returncode != 0:
        return None
    output = (result.stdout or result.stderr or "").strip()
    return output or "gh auth status passed"


def run_tandem_preflight(
    config: TandemConfig,
    *,
    cwd: Path,
    env: Mapping[str, str],
) -> TandemPreflightResult:
    """Validate runnable, distinct, non-paid role CLIs before any role task."""

    producer = _resolve_role_cli("producer", config.roles.producer)
    reviewer = _resolve_role_cli("reviewer", config.roles.reviewer)
    if isinstance(producer, TandemPreflightResult):
        return producer
    if isinstance(reviewer, TandemPreflightResult):
        return reviewer

    leaked_env = _provider_env_present(env)
    if leaked_env:
        names = ", ".join(leaked_env)
        return _failure("paid-resolution", f"provider credential env vars are present: {names}")

    for identity in (producer, reviewer):
        try:
            assert_no_paid_backend(
                role=identity.role,
                cli=identity.resolved_cli,
                provider=config.llm.provider,
                openai_api=config.llm.openai_api,
                model=config.llm.model,
                base_url=config.llm.base_url,
            )
        except PaidBackendError as exc:
            return _failure("paid-resolution", str(exc))

    if producer.resolved_cli == reviewer.resolved_cli:
        return _failure(
            "independence-collapse",
            f"producer and reviewer both resolved to {producer.resolved_cli}",
        )

    try:
        require_wsl_native_path(cwd, label="tandem cwd")
    except typer.BadParameter as exc:
        return _failure("not-wsl-native", str(exc))

    gh_identity = _gh_identity(env)
    if gh_identity is None:
        return _failure("gh-identity-unknown", "gh auth status did not report a known identity")

    return TandemPreflightResult(
        ok=True,
        identities={"producer": producer, "reviewer": reviewer},
        gh_identity=gh_identity,
    )
