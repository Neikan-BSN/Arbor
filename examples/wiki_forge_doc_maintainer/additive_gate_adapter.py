"""wiki-forge additive gate adapter for Arbor tandem runs."""

from __future__ import annotations

import importlib
import hashlib
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from arbor.tandem.gate import Gate, GateOutcome


@dataclass(frozen=True)
class _WikiForgeGateApi:
    FixProposal: Any
    GateVerdict: Any
    AnchorError: type[Exception]
    resolve_insertion_point: Callable[[str, Any], int]
    evaluate_proposal: Callable[[str, Any], tuple[Any, str | None]]


def build_gate() -> Gate:
    """Return the module-level wiki-forge gate callable."""

    return gate


def gate(artifact_path: Path, target_repo_root: Path) -> GateOutcome:
    """Validate one wiki-forge ``FixProposal`` artifact."""

    api = _load_wiki_forge()
    try:
        raw = artifact_path.read_text(encoding="utf-8")
        proposal = api.FixProposal.model_validate_json(raw)
    except Exception as exc:
        return GateOutcome(
            produced=False,
            passed=False,
            proposal_id=None,
            detail=f"FixProposal artifact did not parse: {exc}",
            reason_code="proposal-parse-failed",
        )

    proposal_id = str(getattr(proposal, "proposal_id", "") or "")
    target_file = str(getattr(proposal, "target_file", "") or "")
    try:
        original = (target_repo_root / target_file).read_text(encoding="utf-8")
    except OSError as exc:
        return GateOutcome(
            produced=False,
            passed=False,
            proposal_id=proposal_id or None,
            detail=f"target file could not be read: {target_file}: {exc}",
            reason_code="target-read-failed",
        )

    try:
        api.resolve_insertion_point(original, proposal)
    except api.AnchorError as exc:
        return GateOutcome(
            produced=False,
            passed=False,
            proposal_id=proposal_id or None,
            detail=f"anchor unresolved: {exc}",
            reason_code="anchor-unresolved",
        )

    record, patched = api.evaluate_proposal(original, proposal)
    passed = record.verdict == api.GateVerdict.PASS
    patched_content = patched if passed else None
    return GateOutcome(
        produced=True,
        passed=passed,
        proposal_id=proposal_id or None,
        detail=str(record.reason),
        reason_code="gate-pass" if passed else "gate-reject",
        target_relpath=target_file if passed else None,
        patched_content=patched_content,
        content_hash=(
            hashlib.sha256(patched_content.encode("utf-8")).hexdigest()
            if patched_content is not None
            else None
        ),
    )


def _load_wiki_forge() -> _WikiForgeGateApi:
    try:
        return _import_wiki_forge_gate()
    except ImportError as first_error:
        root = Path(os.environ.get("WIKI_FORGE_ROOT", "~/wiki-forge")).expanduser()
        src = root / "src"
        if str(src) not in sys.path:
            sys.path.insert(0, str(src))
        try:
            return _import_wiki_forge_gate()
        except ImportError as exc:
            raise RuntimeError(
                "wiki_forge is not importable. Set WIKI_FORGE_ROOT to the "
                f"wiki-forge checkout root; current WIKI_FORGE_ROOT={root}"
            ) from exc or first_error


def _import_wiki_forge_gate() -> _WikiForgeGateApi:
    additive_gate = importlib.import_module("wiki_forge.additive_gate")
    schemas = importlib.import_module("wiki_forge.schemas")
    return _WikiForgeGateApi(
        FixProposal=schemas.FixProposal,
        GateVerdict=schemas.GateVerdict,
        AnchorError=additive_gate.AnchorError,
        resolve_insertion_point=additive_gate.resolve_insertion_point,
        evaluate_proposal=additive_gate.evaluate_proposal,
    )
