---
status: pending
priority: p2
issue_id: '001'
tags: [tandem, architecture, target-agnostic]
dependencies: []
---

# Tandem driver: make the proposal-discovery glob target-configurable

## Problem Statement

`src/tandem/driver.py` hardcodes `PROPOSAL_GLOB = "docs/maintainer-dogfood/proposals/*.json"` (used by `_snapshot_artifacts` / `_discover_artifact`). The import boundary of the target-agnostic invariant holds (src/tandem imports neither `wiki_forge` nor `examples`), but this is *data* coupling: the location the generic driver scans for produced artifacts is bound to the wiki-forge dogfood layout. A second (non-wiki-forge) target whose producer writes proposals elsewhere would silently get `artifact-missing` and fail closed, with no config recourse. The gate is correctly injected; artifact discovery is not.

Surfaced by the 2026-06-23 adversarial review of the dual-CLI tandem MVP (medium, architecture dimension), verified against the code.

## Findings

- `PROPOSAL_GLOB` is a module constant referenced only at driver.py:34/205/503/514.
- No `proposal_glob`/`artifact_glob` field on `TandemConfig`, `run_tandem_driver(...)`, `execute_tandem_run(...)`, or the `arbor tandem run` CLI.
- No current functional bug: the only wired target (wiki-forge) writes to exactly this path.

## Proposed Solutions

1. Add `proposal_glob: str` to `TandemConfig` (default the wiki-forge value at the example/CLI binding layer, not in core), plumb it through `run_tandem_driver` / `execute_tandem_run` / a `--proposal-glob` CLI option, and have `_snapshot_artifacts`/`_discover_artifact` take it as an argument instead of reading the module constant.

## Recommended Action

Adopt solution 1 when a second tandem target is introduced, or sooner as cheap hardening. Default must remain `docs/maintainer-dogfood/proposals/*.json` for wiki-forge.

## Acceptance Criteria

- The proposal glob is configurable end-to-end (config + CLI) with the wiki-forge default unchanged.
- `_snapshot_artifacts`/`_discover_artifact` no longer read a module-level target-specific constant.
- A test exercises a non-default glob.

## Work Log

- 2026-06-23: filed from adversarial-review finding (medium).
