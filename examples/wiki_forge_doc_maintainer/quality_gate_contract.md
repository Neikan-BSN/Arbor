# Quality gate contract

The quality gate is a blocking merge-eligibility input. It is not the final
counting authority for the run.

## Inputs

For each candidate proposal, provide:

- proposal ID and branch name;
- proposal JSON and approval/rejection history;
- target doc diff;
- additive proof output;
- repo verification output;
- relevant maintainer-dogfood evidence;
- previous repair notes for the same gap or proposal family.

## Reviewer runtimes

| Runtime                                     | Allowed by default?                        | Notes                                                                                             |
| ------------------------------------------- | ------------------------------------------ | ------------------------------------------------------------------------------------------------- |
| Manual human review                         | Yes                                        | Operator reads the inputs and records the decision.                                               |
| Local model endpoint                        | Yes, after endpoint is named               | Must use a local/OpenAI-compatible endpoint and dummy or local key.                               |
| Claude Code or Codex CLI review             | Yes, after operator grants execution scope | Treat output as model review, not verifier output.                                                |
| Hosted Anthropic/OpenAI/other paid provider | No                                         | Requires explicit current-turn permission. State provider, model, endpoint, and scope before use. |

## Output fields

Record each gate result in an artifact with these fields:

```yaml
proposal_id: "<id>"
branch: "maintainer/fix-<id>"
reviewer_runtime: "manual | local-model | claude-cli | codex-cli | paid-authorized"
verdict: "pass | repair | reject | blocked"
confidence: 0
blocking_findings: []
repair_hints: []
evidence_inputs: []
cannot_count_goal: true
reviewed_at_utc: "YYYY-MM-DDTHH:MM:SSZ"
```

`cannot_count_goal` stays true for every quality gate artifact. Counting
authority belongs to the dogfood verifier plus the operator checkpoint.

## Blocking rules

- If the reviewer runtime is ambiguous, return `blocked`.
- If a hosted paid provider would be used without explicit current-turn
  permission, return `blocked`.
- If proposal evidence is missing, return `repair` or `blocked`.
- If the diff is not additive or the additive proof is missing, return `repair`.
- If claims are not grounded in the changed docs or repo evidence, return
  `repair`.
- If the review says the long-run goal is done, ignore that as counting
  evidence and require fresh verifier output.

## Misuse cases

- A model says "approved" but no operator approval artifact exists: not approved.
- A model says "goal complete" but verifier prints `verdict=CONTINUE`: continue.
- A reviewer times out: blocked, not approved.
- A reviewer uses stale proposal inputs: repair, then rerun on fresh inputs.
