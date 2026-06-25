---
status: pending
priority: p3
issue_id: '003'
tags: [tandem, architecture, extensibility]
dependencies: []
---

# Tandem: parameterize PR branch prefix and title template

## Problem Statement

The target-agnostic spine hardcodes wiki-forge doc-maintainer naming: `ledger.branch_key_for_proposal_id` → `maintainer/fix-<id>` and `gh_runner._draft_title` → `docs: apply maintainer proposal <id>`. A non-doc/non-wiki-forge target would emit misleading `docs:`/`maintainer` branches and titles with no override.

Surfaced by the 2026-06-23 adversarial review (low, architecture dimension), verified.

## Findings

- The branch prefix `maintainer/fix-` is NOT a free convention — it is a deliberate cross-repo join key also hardcoded in wiki-forge `scripts/verify_maintainer_dogfood.py` (`BRANCH_PREFIX`) and prescribed by the MVP plan (KTD8/U6/U8). For the only existing target the literal is correct and required.
- The `docs:` title is cosmetic (PR title only, not part of any join contract).
- No functional bug; the harm is hypothetical (no second target exists).

## Proposed Solutions

1. Accept a `branch_prefix` / `title_template` (or a small naming-policy object) on `open_ready_draft_pr` and the branch-key helper, defaulted at the `tandem_cmd`/example binding layer to the current values. Keep `ledger.py`/`gh_runner.py` free of the literal `maintainer`/`docs:` tokens.

## Recommended Action

Adopt solution 1 when a second target is introduced. Default must remain `maintainer/fix-` (join-key compatibility with the wiki-forge verifier) and `docs: apply maintainer proposal`.

## Acceptance Criteria

- Branch prefix + title template are parameterized with the current values as defaults.
- `ledger.py`/`gh_runner.py` contain no literal `maintainer`/`docs:` tokens.
- The wiki-forge `maintainer/fix-<id>` join key is preserved by default.

## Work Log

- 2026-06-23: filed from adversarial-review finding (low).
