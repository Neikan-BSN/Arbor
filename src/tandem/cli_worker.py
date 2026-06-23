"""Synchronous captured CLI worker for tandem role tasks."""

from __future__ import annotations

import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

from ..cli.commands.local_cmd import build_command
from ..core.agent import _scrub_secrets

DEFAULT_TAIL_CHARS = 20_000
DEFAULT_TIMEOUT_S = 3_600

_INT_USAGE_RE = re.compile(
    r"(?i)\b(input_tokens|output_tokens|total_tokens|tokens)\b[^0-9]{0,8}([0-9]+)"
)
_FLOAT_USAGE_RE = re.compile(
    r"(?i)\b(cost_usd|cost)\b[^0-9]{0,8}([0-9]+(?:\.[0-9]+)?)"
)


@dataclass(frozen=True)
class CliRunResult:
    """Captured result from one subscription-CLI invocation."""

    cli: str
    returncode: int | None
    stdout: str
    stderr: str
    invocations: int
    duration_s: float
    usage: dict[str, int | float] = field(default_factory=dict)
    error: str | None = None

    @property
    def ok(self) -> bool:
        """Whether the driver has usable captured content to inspect."""

        return self.error is None and bool(self.stdout.strip())


def _scrubbed_tail(text: str | bytes | None, *, tail_chars: int) -> str:
    if text is None:
        return ""
    if isinstance(text, bytes):
        text = text.decode("utf-8", errors="replace")
    scrubbed = _scrub_secrets(text)
    if len(scrubbed) <= tail_chars:
        return scrubbed
    return scrubbed[-tail_chars:]


def _parse_usage(*texts: str, invocations: int) -> dict[str, int | float]:
    usage: dict[str, int | float] = {"invocations": invocations}
    joined = "\n".join(texts)
    for match in _INT_USAGE_RE.finditer(joined):
        key = match.group(1).lower()
        usage[key] = int(match.group(2))
    for match in _FLOAT_USAGE_RE.finditer(joined):
        key = match.group(1).lower()
        usage[key] = float(match.group(2))
    return usage


def run_cli_worker(
    *,
    cli: str,
    cwd: Path,
    skills_src: Path,
    task: str,
    env: Mapping[str, str],
    timeout_s: int | float = DEFAULT_TIMEOUT_S,
    tail_chars: int = DEFAULT_TAIL_CHARS,
    claude_permission_mode: str | None = None,
) -> CliRunResult:
    """Run one supported local CLI to completion and capture a scrubbed tail."""

    started = time.monotonic()
    if shutil.which(cli) is None:
        return CliRunResult(
            cli=cli,
            returncode=None,
            stdout="",
            stderr=f"{cli} CLI is not found on PATH",
            invocations=0,
            duration_s=time.monotonic() - started,
            usage={"invocations": 0},
            error="not found on PATH",
        )

    try:
        command = build_command(
            agent=cli,
            cwd=cwd,
            skills_src=skills_src,
            task=task,
            claude_permission_mode=claude_permission_mode,
        )
    except Exception as exc:
        message = _scrubbed_tail(str(exc), tail_chars=tail_chars)
        return CliRunResult(
            cli=cli,
            returncode=None,
            stdout="",
            stderr=message,
            invocations=0,
            duration_s=time.monotonic() - started,
            usage={"invocations": 0},
            error=message,
        )

    try:
        completed = subprocess.run(
            command,
            cwd=str(cwd.expanduser().resolve()),
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
            env=dict(env),
        )
    except subprocess.TimeoutExpired as exc:
        stdout = _scrubbed_tail(exc.stdout, tail_chars=tail_chars)
        stderr = _scrubbed_tail(exc.stderr, tail_chars=tail_chars)
        message = f"{cli} timed out after {timeout_s}s"
        stderr = (stderr + "\n" + message).lstrip()
        return CliRunResult(
            cli=cli,
            returncode=None,
            stdout=stdout,
            stderr=stderr,
            invocations=1,
            duration_s=time.monotonic() - started,
            usage=_parse_usage(stdout, stderr, invocations=1),
            error=message,
        )

    stdout = _scrubbed_tail(completed.stdout, tail_chars=tail_chars)
    stderr = _scrubbed_tail(completed.stderr, tail_chars=tail_chars)
    return CliRunResult(
        cli=cli,
        returncode=completed.returncode,
        stdout=stdout,
        stderr=stderr,
        invocations=1,
        duration_s=time.monotonic() - started,
        usage=_parse_usage(stdout, stderr, invocations=1),
    )
