---
status: complete
priority: p2
issue_id: '004'
tags: [tandem, contracts, reviewer, robustness]
dependencies: []
---

# Tandem: reviewer verdict parser fail-closes to `blocked` on a prose preamble

## Problem Statement

`contracts.parse_reviewer_verdict` runs `yaml.safe_load` on the reviewer's full raw
output. When a reviewer CLI prepends a prose sentence before the YAML verdict
envelope, the parse fails (`mapping values are not allowed here`) and the verdict
is treated as `blocked` — even when the reviewer emitted a perfectly valid,
correct `reject`/`repair` envelope right after the prose. The run stops as
`blocked` instead of consuming the real verdict.

Surfaced empirically by U11 Part B (2026-06-28): `claude` as reviewer reliably
prepends a one-line prose preamble before the YAML, so its output is spine-clean
only **1/3** of the time (codex emits clean envelopes, 3/3). The substantive
verdict in every claude case was a correct, well-grounded `reject`; the spine
discarded it as `blocked` purely on format.

## Findings

- This is **fail-closed-safe**: `blocked` is a non-pass, so a hallucinated
  proposal never merges on a malformed reviewer output. The harm is reliability /
  signal-loss, not a safety hole — hence p2, not p1.
- Impact in a real `arbor tandem run`: when `claude` is the reviewer, ~2/3 of
  reviews would stop the run as `blocked`, discarding actionable
  `blocking_findings`/`repair_hints` and forcing operator intervention, rather
  than flowing into the bounded repair loop.
- Root cause is in the spine parser, not the prompt: `reviewer_role_task.md`
  already says "emit exactly one verdict envelope … and nothing else"; claude
  ignores it often enough to matter. Prompt-only fixes are unreliable.
- The multi-verdict guard (`_VERDICT_LINE_RE.findall(payload) > 1 → blocked`) runs
  on the raw payload, so any fix must re-apply it to the *extracted* envelope, not
  the raw text (a preamble mentioning "verdict:" must not false-trigger, and two
  genuine envelopes must still block).

## Proposed Solutions

1. **Spine-side tolerant extraction (primary).** Before `yaml.safe_load`, extract
   the envelope: prefer a fenced \`\`\`yaml block; else slice from the first
   `^schema_version:` line to end. Then apply the existing fail-closed checks to
   the *extracted* region. Preserves every fail-closed guarantee (unparseable →
   blocked; `schema_version != 1` → blocked; pass-with-blocking-findings →
   blocked; repair-without-hints → blocked) while tolerating a prose preamble.
   Reference extractor proven in the U11 Part B harness (`extract_envelope` /
   `_slice_from_schema`).
1. **Reviewer-side output constraint (complementary).** Tighten reviewer output to
   the envelope only: for codex, `codex exec --output-schema <verdict schema>`;
   for claude, a stricter envelope instruction or a `--output-format json`
   post-extract. Reduces reliance on model compliance.

## Recommended Action

Adopt solution 1 in `contracts.parse_reviewer_verdict` (robust to any reviewer
CLI's preamble habit, keeps fail-closed semantics). Treat solution 2 as a
follow-on hardening. Guard against masking a genuine double-verdict: if the
extracted region contains more than one distinct `schema_version:`-led envelope or
more than one `verdict:` key, still return `blocked`.

## Acceptance Criteria

- A reviewer output of `<prose preamble>\n\n<valid envelope>` parses to the
  envelope's substantive verdict (not `blocked`).
- All existing fail-closed cases still return `blocked`: unparseable YAML,
  `schema_version != 1`, absent/unknown verdict, `pass` with `blocking_findings`,
  `repair` with no `repair_hints`, and two distinct verdict envelopes.
- Regression tests cover: prose-preamble+envelope (→ intended verdict), fenced
  YAML block (→ intended verdict), double-envelope (→ blocked).
- A U11-style claude reviewer transcript (prose then envelope) yields a clean
  `reject`/`repair` through the spine.

## Work Log

- 2026-06-28: filed from the U11 Part B finding (claude reviewer spine-clean 1/3,
  codex 3/3; substantive verdict was a correct `reject` in every claude case). See
  `examples/wiki_forge_doc_maintainer/U11_RUNBOOK.md` Part B "Format-robustness
  finding".
- 2026-06-28: RESOLVED (solution 1). Added `_extract_verdict_envelope` /
  `_slice_from_schema_version` to `src/tandem/contracts.py` and routed the str branch
  of `parse_reviewer_verdict` through them: prefer a fenced envelope, else slice from
  the first `schema_version:` line; 2+ fenced envelopes fall through to the
  duplicate-verdict guard. All fail-closed guarantees preserved. Added 5 regression
  tests (prose-preamble, fenced block, double-envelope→blocked,
  preamble+malformed→blocked, repair-with-preamble). Full suite 209 passed; ruff +
  `mypy src/tandem` clean. Branch `fix/tandem-reviewer-verdict-envelope-extraction`.
  Solution 2 (reviewer-side output constraint) remains an optional follow-on.
