from __future__ import annotations

import json
from pathlib import Path

from arbor.tandem.contracts import (
    parse_reviewer_verdict,
    read_producer_artifact,
)


def _write_producer_artifact(path: Path, *, proposal_id: str = "fp-123") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "proposal_id": proposal_id,
                "gap_id": "gap-1",
                "surface_key": "docs",
                "target_file": "README.md",
                "gap_type": "missing_example",
                "priority": "LOW",
                "anchor_kind": "end_of_file",
                "anchor_text": "",
                "insertion": "Example",
                "rationale": "Add example",
            }
        ),
        encoding="utf-8",
    )
    return path


def test_reviewer_pass_envelope_and_producer_proposal_id_are_readable(tmp_path: Path) -> None:
    artifact = _write_producer_artifact(
        tmp_path / "docs" / "maintainer-dogfood" / "proposals" / "fp-123.json"
    )

    producer = read_producer_artifact(artifact)
    reviewer = parse_reviewer_verdict(
        {
            "schema_version": 1,
            "proposal_id": "fp-123",
            "branch": "maintainer/fix-fp-123",
            "reviewer_runtime": "codex-cli",
            "verdict": "pass",
            "blocking_findings": [],
            "repair_hints": [],
        }
    )

    assert producer.ok
    assert producer.proposal_id == "fp-123"
    assert reviewer.verdict == "pass"
    assert reviewer.reason_code is None


def test_reviewer_missing_verdict_fails_closed_to_blocked() -> None:
    verdict = parse_reviewer_verdict(
        {"schema_version": 1, "blocking_findings": [], "repair_hints": []}
    )

    assert verdict.verdict == "blocked"
    assert verdict.reason_code == "verdict-missing"


def test_reviewer_contradictory_duplicate_verdict_fails_closed() -> None:
    verdict = parse_reviewer_verdict(
        """
schema_version: 1
verdict: pass
verdict: repair
repair_hints:
  - revise it
"""
    )

    assert verdict.verdict == "blocked"
    assert verdict.reason_code == "verdict-contradictory"


def test_repair_verdict_requires_repair_hints() -> None:
    verdict = parse_reviewer_verdict(
        {"schema_version": 1, "verdict": "repair", "repair_hints": []}
    )

    assert verdict.verdict == "blocked"
    assert verdict.reason_code == "repair-hints-missing"


def test_unknown_reviewer_schema_version_is_rejected_not_misread() -> None:
    verdict = parse_reviewer_verdict(
        {"schema_version": 999, "verdict": "pass", "repair_hints": []}
    )

    assert verdict.verdict == "blocked"
    assert verdict.reason_code == "unsupported-schema-version"


def test_producer_artifact_missing_proposal_id_is_not_readable(tmp_path: Path) -> None:
    artifact = _write_producer_artifact(tmp_path / "fp-missing.json")
    payload = json.loads(artifact.read_text(encoding="utf-8"))
    del payload["proposal_id"]
    artifact.write_text(json.dumps(payload), encoding="utf-8")

    result = read_producer_artifact(artifact)

    assert not result.ok
    assert result.proposal_id is None
    assert result.reason_code == "proposal-id-missing"


def test_producer_artifact_unreadable_json_is_not_readable(tmp_path: Path) -> None:
    artifact = tmp_path / "docs" / "maintainer-dogfood" / "proposals" / "bad.json"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("{not json", encoding="utf-8")

    result = read_producer_artifact(artifact)

    assert not result.ok
    assert result.reason_code == "artifact-unreadable"


def test_producer_artifact_rejects_arbor_schema_version(tmp_path: Path) -> None:
    artifact = _write_producer_artifact(tmp_path / "fp-versioned.json")
    payload = json.loads(artifact.read_text(encoding="utf-8"))
    payload["schema_version"] = 1
    artifact.write_text(json.dumps(payload), encoding="utf-8")

    result = read_producer_artifact(artifact)

    assert not result.ok
    assert result.reason_code == "artifact-schema-version-forbidden"
