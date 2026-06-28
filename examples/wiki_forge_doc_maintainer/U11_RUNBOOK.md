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
Date (UTC):
CLIs + versions: claude=...  codex=...
Part A — producer-contract feasibility:
  claude: first-try pass __/__   after-one-repair __/__
  codex:  first-try pass __/__   after-one-repair __/__
  Shim required? (y/n) + which:
  Chosen --repair-budget + justification:
Part B — contrastive proof (seeded SemVer defect):
  producer CLI: ___   reviewer CLI: ___
  different-CLI reviewer verdict: ___ (blocking finding: ___)
  same-CLI self-review verdict: ___
  Proof holds? (different catches AND same-CLI misses): (y/n)
Conclusion: does the quality bet hold for the grounding-defect class?
```

## MVP pass criteria

- Part A: a real CLI can produce a gate-passable, uniquely-anchored
  `FixProposal` within `--repair-budget` (directly, or via the identified shim).
- Part B: the different reviewer returns `repair`/`reject` on the seeded defect
  while the same-CLI self-review passes it.

Both holding = the dual-CLI tandem MVP has proven its quality bet, not just its
wiring.
