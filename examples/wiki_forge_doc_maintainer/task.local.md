# Arbor task: wiki-forge doc-maintainer long run

Target: the current working directory is the `wiki-forge` repo.

Use the local Arbor skill suite. Do not use native Arbor provider setup unless
the operator explicitly switches to a native run.

## Objective

Operate a long-running, guardrailed doc-maintainer loop for `wiki-forge`.
The loop should produce small additive documentation proposals, repair itself
from deterministic and reviewer feedback, and count progress only through the
repo's dogfood verifier plus operator checkpoint.

## Default mode

Start in smoke/preflight mode:

- inspect target repo state and existing maintainer-dogfood docs;
- identify current branch, dirt, verifier command, approval surfaces, and
  proposal directories;
- do not edit files until the operator grants write scope;
- do not create PRs, push, merge, or run final verifier until explicitly
  approved.

## B_dev and B_test mapping

- B_dev: offline proposal generation, additive proof, local repo checks,
  local/manual quality gate, and repair-loop evidence.
- B_test: fresh dogfood verifier output after explicit human approval.

The dogfood verifier is the only counting authority. Parse `verdict=...`; do
not treat exit code alone as pass or done.

## Lifecycle

Use this state machine:

1. `planned`
1. `smoke`
1. `b_dev_iteration`
1. `quality_gate`
1. `additive_gate`
1. `pr_ready`
1. `human_approved`
1. `final_verifier`
1. `counted_progress | continue | repair | blocked | stop`

Record the current state in the run evidence. On resume, trust durable artifacts
and the Idea Tree over transcript memory.

## Proposal rules

- Work on isolated branches or worktrees only.
- Prefer branch names shaped like `maintainer/fix-<proposal_id>`.
- Preserve a durable join key across proposal JSON, approval artifact, quality
  report, PR, and verifier snapshot.
- Feed rejected proposal reasons and reviewer findings into the next iteration.
- If a branch is stale or partially applied, record `repair` or `blocked`; do
  not force-rewrite `main`.

## Quality gate

Before human approval, run the quality gate described in
`examples/wiki_forge_doc_maintainer/quality_gate_contract.md` from the Arbor
checkout.

The gate may return `pass`, `repair`, `reject`, or `blocked`. It cannot declare
the run complete and cannot replace the dogfood verifier.

Default reviewer runtimes are manual, local endpoint, Claude Code CLI, or Codex
CLI. Hosted Anthropic/OpenAI/other paid providers require explicit current-turn
permission with provider, model, endpoint, and scope stated first.

## Human gates

Stop and ask before:

- editing target docs or maintainer artifacts outside approved branch scope;
- installing packages or downloading data;
- running long jobs, GPU work, or training;
- using hosted paid model providers;
- creating or updating a PR;
- running final verifier with live server metadata;
- merging or changing `main`.

Human approval must be explicit. Do not auto-approve because a review prompt
timed out.

## Stop conditions

Stop and report when:

- the operator-provided budget is exhausted;
- a cadence or checkpoint window cannot be satisfied;
- verifier output remains `CONTINUE` after the budget/window cap;
- state is corrupted and cannot be reconstructed from durable artifacts;
- a required permission is missing.

The final report should separate model-quality findings, deterministic gate
results, operator approvals, and verifier evidence.
