---
status: pending
priority: p3
issue_id: '002'
tags: [tandem, security, prompt-injection]
dependencies: []
---

# Tandem spine: apply prompt-injection delimiters to spine-introduced untrusted strings

## Problem Statement

The producer/reviewer role-task prompt contracts (`examples/wiki_forge_doc_maintainer/{producer,reviewer}_role_task.md`) describe wrapping untrusted content in `<untrusted_...nonce="RUN_NONCE">` delimiters, but the spine never loads those `.md` files and never constructs a delimited block. `_producer_task`/`_reviewer_task` (driver.py) concatenate `base_task`, `feedback_detail` (from `gate_outcome.detail`, which embeds untrusted proposal-derived text), and `repair_hints` (verbatim from untrusted reviewer-CLI stdout, no sanitization) as plain text. So the short spine-introduced untrusted strings are re-injected into later prompts undelimited.

Surfaced by the 2026-06-23 adversarial review (low, security dimension), verified.

## Findings

- Exploitability is strongly capped: the deterministic additive gate re-derives the patch and rejects any non-insertion regardless of what the producer was told; verdict parsing is fail-closed; human merge authority (no auto-merge).
- The dominant untrusted channel (target file bodies) is read by the agents directly from cwd and is architecturally outside the spine's prompt path, so for that channel the `.md` prose is the only possible layer anyway.
- Only `feedback_detail` and `repair_hints` are spine-introduced and undelimited.

## Proposed Solutions

1. Have the spine wrap `feedback_detail` and `repair_hints` in a per-run nonce-delimited `<untrusted_...>` block with a do-not-obey preamble, and strip/escape control sequences from reviewer-supplied `repair_hints` before re-injection.

## Recommended Action

Adopt solution 1 as defense-in-depth. Low priority because the deterministic gate + fail-closed verdict + human merge already backstop it.

## Acceptance Criteria

- Spine-built prompts wrap untrusted-origin strings in a per-run-nonce delimiter block with a do-not-obey preamble.
- `repair_hints` are control-sequence-sanitized before re-injection.
- A test asserts the delimiter wrapping on a repair re-entry.

## Work Log

- 2026-06-23: filed from adversarial-review finding (low).
