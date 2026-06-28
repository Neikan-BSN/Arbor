from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from arbor.tandem.contracts import parse_reviewer_verdict, read_producer_artifact

REPO_ROOT = Path(__file__).resolve().parent.parent
ADAPTER_PATH = REPO_ROOT / "examples" / "wiki_forge_doc_maintainer" / "additive_gate_adapter.py"
PRODUCER_PROMPT = REPO_ROOT / "examples" / "wiki_forge_doc_maintainer" / "producer_role_task.md"
REVIEWER_PROMPT = REPO_ROOT / "examples" / "wiki_forge_doc_maintainer" / "reviewer_role_task.md"
WIKI_FORGE_ROOT = Path(os.environ.get("WIKI_FORGE_ROOT", "~/wiki-forge")).expanduser()
WIKI_FORGE_SRC = WIKI_FORGE_ROOT / "src"
WIKI_FORGE_VERIFIER = WIKI_FORGE_ROOT / "scripts" / "verify_maintainer_dogfood.py"

if WIKI_FORGE_SRC.is_dir() and str(WIKI_FORGE_SRC) not in sys.path:
    sys.path.insert(0, str(WIKI_FORGE_SRC))

try:
    from wiki_forge.additive_gate import AnchorError, resolve_insertion_point
    from wiki_forge.schemas import FixProposal, GateRecord, GateVerdict
except ImportError:
    pytest.skip("wiki-forge not importable", allow_module_level=True)


def _load_adapter() -> ModuleType:
    spec = importlib.util.spec_from_file_location("wiki_forge_additive_gate_adapter_test", ADAPTER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _proposal(path: Path, *, proposal_id: str = "fp-123", anchor_text: str = "Unique anchor", insertion: str = "Added docs\n") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "proposal_id": proposal_id,
                "gap_id": "gap-123",
                "surface_key": "docs",
                "target_file": "README.md",
                "gap_type": "missing_example",
                "priority": "LOW",
                "anchor_kind": "after_line",
                "anchor_text": anchor_text,
                "insertion": insertion,
                "rationale": "Adds missing docs",
            }
        ),
        encoding="utf-8",
    )
    return path


def test_adapter_returns_pass_for_byte_additive_fixproposal(tmp_path: Path) -> None:
    adapter = _load_adapter()
    target = tmp_path / "wiki-forge"
    target.mkdir()
    (target / "README.md").write_text("Intro\nUnique anchor\nTail\n", encoding="utf-8")
    artifact = _proposal(target / "docs" / "maintainer-dogfood" / "proposals" / "fp-123.json")
    verifier_before = hashlib.sha256(WIKI_FORGE_VERIFIER.read_bytes()).hexdigest()

    outcome = adapter.gate(artifact, target)

    assert outcome.produced
    assert outcome.passed
    assert outcome.proposal_id == "fp-123"
    assert outcome.reason_code == "gate-pass"
    assert "insert-only" in outcome.detail
    assert outcome.target_relpath == "README.md"
    assert outcome.patched_content is not None
    assert "Added docs" in outcome.patched_content
    assert outcome.content_hash == hashlib.sha256(
        outcome.patched_content.encode("utf-8")
    ).hexdigest()
    assert hashlib.sha256(WIKI_FORGE_VERIFIER.read_bytes()).hexdigest() == verifier_before


def test_adapter_maps_gate_record_reject_to_passed_false(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = _load_adapter()
    target = tmp_path / "wiki-forge"
    target.mkdir()
    (target / "README.md").write_text("Intro\nUnique anchor\nTail\n", encoding="utf-8")
    artifact = _proposal(target / "docs" / "maintainer-dogfood" / "proposals" / "fp-123.json")

    def fake_evaluate(original: str, proposal: FixProposal) -> tuple[GateRecord, None]:
        return (
            GateRecord(
                proposal_id=proposal.proposal_id,
                gap_id=proposal.gap_id,
                target_file=proposal.target_file,
                verdict=GateVerdict.REJECT,
                reason="not insert-only: 1 byte(s) modified, 0 deleted",
                anchor_resolved=True,
                existing_bytes_modified=1,
                existing_bytes_deleted=0,
                inserted_bytes=0,
            ),
            None,
        )

    monkeypatch.setattr(
        adapter,
        "_load_wiki_forge",
        lambda: SimpleNamespace(
            FixProposal=FixProposal,
            GateVerdict=GateVerdict,
            AnchorError=AnchorError,
            resolve_insertion_point=resolve_insertion_point,
            evaluate_proposal=fake_evaluate,
        ),
    )

    outcome = adapter.gate(artifact, target)

    assert outcome.produced
    assert not outcome.passed
    assert outcome.reason_code == "gate-reject"
    assert outcome.target_relpath is None
    assert outcome.patched_content is None
    assert outcome.content_hash is None
    assert "not insert-only" in outcome.detail


@pytest.mark.parametrize(
    ("readme", "anchor_text"),
    [
        ("Intro\nOther anchor\n", "Missing anchor"),
        ("Intro\nRepeated\nMiddle\nRepeated\n", "Repeated"),
    ],
)
def test_adapter_reports_unresolved_anchor_as_not_produced(
    tmp_path: Path,
    readme: str,
    anchor_text: str,
) -> None:
    adapter = _load_adapter()
    target = tmp_path / "wiki-forge"
    target.mkdir()
    (target / "README.md").write_text(readme, encoding="utf-8")
    artifact = _proposal(
        target / "docs" / "maintainer-dogfood" / "proposals" / "fp-anchor.json",
        proposal_id="fp-anchor",
        anchor_text=anchor_text,
    )

    outcome = adapter.gate(artifact, target)

    assert not outcome.produced
    assert not outcome.passed
    assert outcome.proposal_id == "fp-anchor"
    assert outcome.reason_code == "anchor-unresolved"


def test_prompt_contract_shapes_match_arbor_parsers(tmp_path: Path) -> None:
    producer = PRODUCER_PROMPT.read_text(encoding="utf-8")
    reviewer = REVIEWER_PROMPT.read_text(encoding="utf-8")
    output_paths = re.findall(r"docs/maintainer-dogfood/proposals/<proposal_id>\.json", producer)
    assert output_paths

    artifact = _proposal(
        tmp_path / output_paths[0].replace("<proposal_id>", "fp-contract"),
        proposal_id="fp-contract",
    )
    artifact_ref = read_producer_artifact(artifact)
    assert artifact_ref.ok
    assert artifact_ref.proposal_id == "fp-contract"

    assert "schema_version: 1" in reviewer
    assert "<untrusted_target_content" in producer
    assert "<untrusted_proposal_and_target" in reviewer
    assert "RUN_NONCE" in producer
    assert "RUN_NONCE" in reviewer
    assert re.search(r"data,\s+not\s+instructions", producer.lower())
    assert re.search(r"data,\s+not\s+instructions", reviewer.lower())
    verdict = parse_reviewer_verdict(
        """
schema_version: 1
proposal_id: fp-contract
branch: maintainer/fix-fp-contract
reviewer_runtime: codex-cli
verdict: pass
confidence: 0
blocking_findings: []
repair_hints: []
evidence_inputs: []
cannot_count_goal: true
reviewed_at_utc: "2026-06-23T00:00:00Z"
"""
    )
    assert verdict.verdict == "pass"
    assert verdict.schema_version == 1
