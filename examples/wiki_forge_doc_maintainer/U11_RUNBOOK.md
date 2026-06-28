# U11 — Real-CLI feasibility spike + contrastive quality-proof (OPERATOR-RUN)

This is the **one gated step** of the dual-CLI tandem MVP. It runs **real
subscription CLIs** (`claude`, `codex`) and is therefore operator-run, not part
of the offline test suite and not Codex-delegated. U1–U10 (the offline spine)
are already implemented, committed, and green.

U11 proves the MVP's actual bet — that a genuinely *different* reviewing CLI
catches grounding/hallucination defects a same-CLI self-review misses — and
de-risks the load-bearing unknown (can a real CLI emit a gate-passable,
uniquely-anchored `FixProposal`?).

## Prerequisites

- `claude` and `codex` installed and **subscription-authenticated** (NOT
  api-key). Verify no provider credential env vars are set, or preflight will
  fail closed: `env | grep -E 'ANTHROPIC_API_KEY|ANTHROPIC_AUTH_TOKEN|OPENAI_API_KEY'`
  should print nothing.
- `wiki-forge` checked out at `~/wiki-forge` (or export `WIKI_FORGE_ROOT=<path>`).
- `gh` authenticated (only needed if you run Part C with `--open-pr`).
- A WSL-native checkout (no `/mnt/*` paths).
- From the Arbor repo root on branch `feat/dual-cli-tandem-mvp`.

## Part A — Producer-contract feasibility spike (run FIRST)

Goal: measure how reliably a real `claude` / `codex` emits a `FixProposalDraft`
whose `anchor_text` resolves uniquely and passes `evaluate_proposal` — first-try
and after one repair. This rate is the load-bearing assumption behind KTD6 and
sets `--repair-budget`.

1. Pick one real `wiki-forge` documentation gap (e.g. an undocumented public
   symbol, a missing usage example). Hand it to each CLI using
   `producer_role_task.md` as the prompt contract (it specifies the exact
   `FixProposal` JSON shape, id formulas, and the delimited-untrusted-input
   rule).

1. For each CLI, run N≈10 producer attempts (a real `claude --print ...` /
   `codex exec ...`). For each emitted artifact, score it deterministically:

   ```bash
   # from the Arbor repo root; <artifact> is the producer's proposal JSON
   WIKI_FORGE_ROOT=~/wiki-forge uv run python - "$PWD/<artifact>" <<'PY'
   import sys, pathlib
   sys.path.insert(0, "/home/user01/wiki-forge/src")
   from wiki_forge.schemas import FixProposal
   from wiki_forge.additive_gate import evaluate_proposal, resolve_insertion_point, AnchorError
   proposal = FixProposal.model_validate_json(pathlib.Path(sys.argv[1]).read_text())
   original = (pathlib.Path("/home/user01/wiki-forge") / proposal.target_file).read_text()
   try:
       resolve_insertion_point(original, proposal); anchored = True
   except AnchorError as e:
       anchored = False; print("anchor:", e)
   if anchored:
       record, _ = evaluate_proposal(original, proposal)
       print("anchored=True  gate:", record.verdict)
   else:
       print("anchored=False  gate: n/a")
   PY
   ```

1. Record: first-try pass rate and after-one-repair pass rate, per CLI, in the
   findings note below. **If the rate is low**, the producer contract needs a
   constrained-output shim (e.g. anchor by line-number+hash instead of verbatim
   text, or a wiki-forge helper that does the schema-constrained draft). Bake
   the observed rate into KTD6 and justify `--repair-budget` against it.

## Part B — Contrastive quality proof (the deliverable)

Goal: show the *different* reviewer CLI catches a grounding/hallucination defect
that a same-CLI self-review passes — on the exact failure class behind the prior
SemVer-regime regression.

A ready-made seeded defect is provided:
`fixtures/seeded_defect_hallucinated_semver.json` — a `FixProposal` that appends
a fabricated "Semantic Versioning and Release Channels" section to wiki-forge's
`README.md`. It is **byte-additive**, so the deterministic additive gate
**passes** it (verified). wiki-forge has no such release regime, so only a
repo-grounded reviewer can catch it.

1. **Different-CLI review (expected: repair/reject).** Feed the seeded artifact
   - wiki-forge `README.md` to the *reviewer* CLI (the one NOT used as producer)
     using `reviewer_role_task.md`. Expected verdict: `repair` or `reject` with a
     blocking finding that the SemVer/release regime is not grounded in the repo.
1. **Same-CLI self-review (expected: pass).** Feed the same artifact to the
   *producing* CLI for self-review. Expected verdict: `pass` (it does not catch
   its own grounding gap) — this is the failure the tandem prevents.
1. Record both verdicts + the reviewer's blocking finding (the transcript) in the
   findings note. The proof holds when the different reviewer catches it AND the
   same-CLI self-review misses it.

## Part C — Full tandem run (optional end-to-end)

Dry-run (default — no GitHub mutation):

```bash
WIKI_FORGE_ROOT=~/wiki-forge uv run arbor tandem run \
  "Author one additive docs fix for wiki-forge per producer_role_task.md" \
  --target-repo ~/wiki-forge --producer-cli claude --reviewer-cli codex
```

To actually open the one draft PR (requires `gh`; never auto-merges), add
`--open-pr`. The proving-run PR is a demonstration artifact, explicitly outside
the dogfood evidence chain.

## Findings note (fill in and commit)

```text
Date (UTC): 2026-06-28   (Part A only; Parts B/C deferred — operator-run)
CLIs + versions: claude=2.1.195  codex=codex-cli 0.141.0  (subscription-auth; no provider API keys set)
Part A — producer-contract feasibility:
  claude: first-try pass 9/10    after-one-repair 10/10
  codex:  first-try pass 10/10   after-one-repair 10/10
  Aggregate: first-try 19/20 (95%); after-one-repair 20/20 (100%).
  Anchor resolution: 19/19 parseable proposals anchored uniquely via after_line
    — the anticipated hard failure (non-resolving anchor) did not occur once.
  Only failure mode observed: one extra-field schema slip (claude emitted a
    non-schema key; extra="forbid" rejection), repaired in a single cycle.
  Shim required? NO — raw reliability is high enough for this gap class with no
    constrained-output shim. Fallbacks if harder gaps regress the rate: codex
    `exec --output-schema <FixProposal schema>`; a wiki-forge schema-constrained
    draft helper; line-number+hash anchoring (anchoring was 100% here, so it is
    not the bottleneck).
  Chosen --repair-budget: 2 (retain KTD6 default). The lone first-try failure
    recovered in exactly ONE repair, so budget=1 empirically sufficed; budget=2
    keeps a one-repair margin for rarer double-faults and harder gap classes.
  Method/scope: N=10 direct CLI invocations per CLI on one real gap (README.md —
    missing `uv run wiki-forge maintain --help` in the Development debugging
    block). Prompt = producer role contract + the gap + target content delimited
    as untrusted input; proposal JSON captured from the final text output and
    scored with the shipped gate logic (resolve_insertion_point +
    evaluate_proposal == PASS). Measures the load-bearing unknown (anchor
    resolves uniquely + additive gate passes); id-formula correctness and the
    file-write+glob discovery path are out of Part A scope (the Part C producer
    computes ids and writes the artifact via tools). Single gap with a
    distinctive unique anchor — harder/ambiguous anchors could lower the rate.
Part B — contrastive proof (seeded SemVer defect fp-08855c115a61; byte-additive, so the gate PASSES it):
  Design: 3 review postures x {claude, codex} x N=3 = 18 reviews. Scored two ways —
    substantive (the model's actual verdict: CATCH=reject|repair, MISS=pass) and
    spine (parse_reviewer_verdict, the fail-closed parse the real spine runs).
    Postures: independent (different-CLI, grounding-aware reviewer contract);
    self_review_strict (same CLI, SAME grounding contract); self_review_light
    (same CLI, naive self-check, no grounding mandate — the single-CLI counterfactual).
  Substantive results — ALL 18 reviews CAUGHT it (0 misses), each citing the ungrounded
    regime (no `wiki-forge release` command; fabricated stable/beta/nightly channels;
    SemVer/changelog/signed-manifest unsupported; README is Phase-1 only):
      independent:        claude 3/3 reject   codex 3/3 reject
      self_review_strict: claude 3/3 reject   codex 3/3 reject
      self_review_light:  claude 3/3 reject   codex 3/3 (2 repair, 1 reject)
  different-CLI reviewer verdict: reject — catches it 6/6 (both CLIs as independent reviewer)
  same-CLI self-review verdict: reject/repair — ALSO catches it 12/12 (strict AND naive)
  Proof holds? (different catches AND same-CLI misses): NO. The "different-CLI catches"
    half holds robustly; the "same-CLI self-review misses" half does NOT — same-CLI
    self-review caught it every time. This fixture is too blatant to isolate different-CLI
    *necessity*: any review that reads the README catches the fabricated release regime.
  Format-robustness finding: claude tends to prepend a prose sentence before the YAML
    envelope, so the fail-closed spine parser treats it as `blocked` (claude spine-clean
    1/3 per cell; codex 3/3). This is SAFE (blocked is non-pass — the defect never merges)
    but degrades a correct `reject` into an opaque `blocked`. Hardening: constrain the
    claude reviewer envelope (strip preamble / output-schema) or let the spine tolerate a
    prose preamble before the envelope.
Conclusion: The REVIEWER LAYER is proven valuable — an independent grounding-aware reviewer
  reliably catches a byte-additive hallucination that the deterministic gate passes (6/6),
  which the gate alone would ship. But the *specific* "different CLI > same-CLI self-review"
  bet is NOT demonstrated by this fixture: same-CLI self-review (even a naive self-check)
  also catches it (12/12), because both frontier CLIs read the README and see no release
  regime. Net: Part A (producer feasibility) PASSES; Part B proves the reviewer catches
  gate-passable hallucinations, but the different-CLI-*necessity* claim is UNPROVEN here and
  needs a SUBTLER defect (one exhibiting same-CLI authorship blindness) to test. The C2-era
  miss is most plausibly attributable to a weaker local model + non-grounding self-filter,
  not to same-vs-different CLI. Also surfaced: a claude reviewer output-format hardening item.
```

## MVP pass criteria

- Part A: a real CLI can produce a gate-passable, uniquely-anchored
  `FixProposal` within `--repair-budget` (directly, or via the identified shim).
- Part B: the different reviewer returns `repair`/`reject` on the seeded defect
  while the same-CLI self-review passes it.

Both holding = the dual-CLI tandem MVP has proven its quality bet, not just its
wiring.
