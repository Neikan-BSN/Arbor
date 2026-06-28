"""Generic injected gate contract for tandem role artifacts."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class GateOutcome:
    """Target-agnostic result from validating a produced artifact."""

    produced: bool
    passed: bool
    proposal_id: str | None
    detail: str
    reason_code: str | None = None
    target_relpath: str | None = None
    patched_content: str | None = None
    content_hash: str | None = None


class Gate(Protocol):
    """Callable seam implemented by target-specific gate bindings."""

    def __call__(self, artifact_path: Path, target_repo_root: Path) -> GateOutcome:
        """Validate ``artifact_path`` against ``target_repo_root``."""
