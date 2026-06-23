"""Tandem role-task artifact and reviewer verdict contracts."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Mapping

import yaml

REVIEWER_VERDICT_SCHEMA_VERSION = 1
ReviewerVerdictValue = Literal["pass", "repair", "reject", "blocked"]
_ALLOWED_VERDICTS = {"pass", "repair", "reject", "blocked"}
_VERDICT_LINE_RE = re.compile(r"(?m)^\s*verdict\s*:")


@dataclass(frozen=True)
class ProducerArtifactRef:
    """Generic reference extracted from a producer artifact JSON file."""

    path: Path
    proposal_id: str | None
    ok: bool
    detail: str
    reason_code: str | None = None


@dataclass(frozen=True)
class ReviewerVerdict:
    """Fail-closed reviewer verdict envelope."""

    verdict: ReviewerVerdictValue
    schema_version: int | None
    blocking_findings: list[str] = field(default_factory=list)
    repair_hints: list[str] = field(default_factory=list)
    detail: str = ""
    reason_code: str | None = None

    @property
    def is_terminal(self) -> bool:
        """Whether this verdict stops the role-task loop."""

        return self.verdict != "repair"


def read_producer_artifact(path: str | Path) -> ProducerArtifactRef:
    """Read a generic producer artifact and extract its ``proposal_id`` only.

    The target-specific gate owns deeper validation such as proposal shape,
    anchor resolution, and additive-pass proof.
    """

    artifact_path = Path(path)
    if not artifact_path.is_file():
        return ProducerArtifactRef(
            path=artifact_path,
            proposal_id=None,
            ok=False,
            detail=f"producer artifact does not exist: {artifact_path}",
            reason_code="artifact-missing",
        )
    try:
        raw = json.loads(artifact_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return ProducerArtifactRef(
            path=artifact_path,
            proposal_id=None,
            ok=False,
            detail=f"producer artifact is unreadable JSON: {exc}",
            reason_code="artifact-unreadable",
        )
    if not isinstance(raw, dict):
        return ProducerArtifactRef(
            path=artifact_path,
            proposal_id=None,
            ok=False,
            detail="producer artifact JSON must be an object",
            reason_code="artifact-not-object",
        )
    if "schema_version" in raw:
        return ProducerArtifactRef(
            path=artifact_path,
            proposal_id=None,
            ok=False,
            detail="producer artifact must not carry an Arbor schema_version",
            reason_code="artifact-schema-version-forbidden",
        )
    proposal_id = raw.get("proposal_id")
    if not isinstance(proposal_id, str) or not proposal_id.strip():
        return ProducerArtifactRef(
            path=artifact_path,
            proposal_id=None,
            ok=False,
            detail="producer artifact is missing a non-empty proposal_id",
            reason_code="proposal-id-missing",
        )
    return ProducerArtifactRef(
        path=artifact_path,
        proposal_id=proposal_id.strip(),
        ok=True,
        detail="producer artifact reference is readable",
    )


def read_reviewer_verdict(path: str | Path) -> ReviewerVerdict:
    """Load and parse a reviewer verdict artifact from disk."""

    verdict_path = Path(path)
    try:
        text = verdict_path.read_text(encoding="utf-8")
    except OSError as exc:
        return _blocked(
            schema_version=None,
            detail=f"reviewer verdict artifact is unreadable: {exc}",
            reason_code="verdict-unreadable",
        )
    return parse_reviewer_verdict(text)


def parse_reviewer_verdict(payload: str | Mapping[str, Any] | None) -> ReviewerVerdict:
    """Parse a reviewer verdict envelope with fail-closed defaults."""

    if payload is None:
        return _blocked(
            schema_version=None,
            detail="reviewer verdict is absent",
            reason_code="verdict-absent",
        )

    if isinstance(payload, str):
        if len(_VERDICT_LINE_RE.findall(payload)) > 1:
            return _blocked(
                schema_version=None,
                detail="reviewer verdict contains multiple verdict keys",
                reason_code="verdict-contradictory",
            )
        try:
            loaded = yaml.safe_load(payload)
        except yaml.YAMLError as exc:
            return _blocked(
                schema_version=None,
                detail=f"reviewer verdict is not parseable YAML: {exc}",
                reason_code="verdict-unparseable",
            )
    else:
        loaded = dict(payload)

    if not isinstance(loaded, dict):
        return _blocked(
            schema_version=None,
            detail="reviewer verdict must be a mapping",
            reason_code="verdict-not-object",
        )

    version = _parse_schema_version(loaded.get("schema_version"))
    if version != REVIEWER_VERDICT_SCHEMA_VERSION:
        return _blocked(
            schema_version=version,
            detail=(
                f"unsupported reviewer verdict schema_version {loaded.get('schema_version')!r}; "
                f"expected {REVIEWER_VERDICT_SCHEMA_VERSION}"
            ),
            reason_code="unsupported-schema-version",
        )

    raw_verdict = loaded.get("verdict")
    if not isinstance(raw_verdict, str):
        return _blocked(
            schema_version=version,
            detail="reviewer verdict is missing",
            reason_code="verdict-missing",
        )
    normalized = raw_verdict.strip().lower()
    if normalized == "block":
        normalized = "blocked"
    if normalized not in _ALLOWED_VERDICTS:
        return _blocked(
            schema_version=version,
            detail=f"reviewer verdict is unknown: {raw_verdict!r}",
            reason_code="verdict-unknown",
        )

    blocking_findings = _string_list(loaded.get("blocking_findings"))
    repair_hints = _string_list(loaded.get("repair_hints"))
    if normalized == "pass" and blocking_findings:
        return _blocked(
            schema_version=version,
            blocking_findings=blocking_findings,
            repair_hints=repair_hints,
            detail="pass verdict contradicts non-empty blocking_findings",
            reason_code="verdict-contradictory",
        )
    if normalized == "repair" and not repair_hints:
        return _blocked(
            schema_version=version,
            blocking_findings=blocking_findings,
            repair_hints=repair_hints,
            detail="repair verdict requires at least one repair_hint",
            reason_code="repair-hints-missing",
        )

    return ReviewerVerdict(
        verdict=normalized,  # type: ignore[arg-type]
        schema_version=version,
        blocking_findings=blocking_findings,
        repair_hints=repair_hints,
        detail=f"reviewer verdict: {normalized}",
    )


def _parse_schema_version(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item).strip()]


def _blocked(
    *,
    schema_version: int | None,
    detail: str,
    reason_code: str,
    blocking_findings: list[str] | None = None,
    repair_hints: list[str] | None = None,
) -> ReviewerVerdict:
    return ReviewerVerdict(
        verdict="blocked",
        schema_version=schema_version,
        blocking_findings=blocking_findings or [],
        repair_hints=repair_hints or [],
        detail=detail,
        reason_code=reason_code,
    )

