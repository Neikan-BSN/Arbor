# Verifier checklist

Use this checklist for final eligibility. It is intentionally stricter than a
model review.

## Preflight

- Confirm the working repo is `wiki-forge`.
- Confirm the run is on a WSL/Linux filesystem, not a mounted Windows path.
- Confirm the target branch and worktree are clean enough for the requested
  operation.
- Confirm the operator granted the current operation scope.
- Confirm proposal ID, branch, approval artifact, quality gate report, and
  verifier snapshot path share the same join key.

## B_dev eligibility

Before asking for human approval, require:

- additive proof passed;
- repo checks passed or failures are explained as non-final blockers;
- quality gate verdict is `pass`;
- proposal artifact and doc diff are present;
- no protected verifier or approval surfaces were modified without approval.

## Human approval

Human approval is a hard gate. The run cannot proceed to final verifier or merge
eligibility without an explicit approval artifact.

Do not treat any timeout, model response, or interaction-mode default as
approval.

## Final verifier

From the target repo, the read-only counting verifier shape is:

```bash
UV_FROZEN=1 uv run python scripts/verify_maintainer_dogfood.py \
  --repo . \
  --repo-slug Neikan-BSN/wiki-forge \
  --base main \
  --enabling-mode worktree \
  --json
```

When the operator has explicitly approved running repo verification as part of
the final check, add `--run-verify`.

For offline or smoke checks, use `--no-fetch` or `--pr-data` only when the
evidence is clearly labeled offline. Do not present offline injected metadata
as fresh server state.

## Interpreting output

- Parse `verdict=...` from the human-readable line or the JSON payload.
- `verdict=CONTINUE` means keep operating or report a blocker. It is not
  success, even when the process exits 0.
- `verify=unchecked` means repo verification was not part of the evidence.
- Missing mid-window checkpoint, insufficient merge count, insufficient
  distinct days, or an out-of-range window are blockers until repaired by real
  run history.

## After verifier

- If verifier says continue: record the reasons, feed them into the next
  iteration, and do not merge solely on model review.
- If verifier supports progress but horizon is not complete: count the evidence
  and continue only within the approved budget/window.
- If verifier and quality gate disagree: prefer verifier for counting, but use
  quality findings to decide repair before any merge.
- If final merge is requested: re-check human approval, protected paths, branch,
  and latest verifier snapshot before merging.
