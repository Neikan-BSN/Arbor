---
title: 'codex critique: 2026-06-22-001-feat-dual-cli-tandem-mvp-plan'
generated_at: 2026-06-22T18:41:27Z
plan: /home/user01/agent-workspace/Arbor/docs/plans/2026-06-22-001-feat-dual-cli-tandem-mvp-plan.md
corpus_source: /home/user01/agent-workspace/docs/codex-critique-corpus.md
command: /codex-critique
capture_method: output-last-message
json_source: /home/user01/agent-workspace/Arbor/docs/plans/2026-06-22-001-feat-dual-cli-tandem-mvp-plan-codex-critique.json
raw_log: /home/user01/agent-workspace/Arbor/docs/plans/2026-06-22-001-feat-dual-cli-tandem-mvp-plan-codex-critique.raw.log
---

# critique: feat: Dual-CLI Tandem Role-Orchestration MVP

## Review Frame
Checked the plan against the live Arbor checkout and the cited wiki-forge source under /home/user01/wiki-forge. Identifier claims were verified with grep before use; schema/API claims were checked against the cited Pydantic models, gate functions, preflight/probe code, config surfaces, and verifier script. No paid APIs or real CLI role runs were invoked.

## Critical Findings
### 1. Blocker: U7 depends on the U9 gate adapter even though U9 is in the later phase [KNOWN-ERR-phase-boundary-violations]
The plan defines Phase B as U5-U7 and Phase C as U8-U11, but U7's driver calls the wiki-forge gate through an adapter explicitly supplied by U9. U7's dependency list omits U9, while U9 itself depends on U7, creating both a phase-boundary violation and a circular implementation dependency. Phase B cannot ship or test its driver as described unless the gate adapter is moved earlier or U7 is narrowed to an injectable gate interface with only fake tests until U9 lands.
Cited: plan docs/plans/2026-06-22-001-feat-dual-cli-tandem-mvp-plan.md:114, docs/plans/2026-06-22-001-feat-dual-cli-tandem-mvp-plan.md:226, docs/plans/2026-06-22-001-feat-dual-cli-tandem-mvp-plan.md:228, docs/plans/2026-06-22-001-feat-dual-cli-tandem-mvp-plan.md:263, docs/plans/2026-06-22-001-feat-dual-cli-tandem-mvp-plan.md:264
### 2. Blocker: Versioned producer artifact conflicts with wiki-forge's strict FixProposal schema [KNOWN-ERR-schema-shape-conflation]
U5 says the producer artifact is a wiki-forge FixProposal that must parse as that schema, then says both contract models carry schema_version and tests reject unknown schema_version. The cited wiki-forge FixProposal is extra=forbid and its field set is only proposal_id, gap_id, surface_key, target_file, gap_type, priority, anchor_kind, anchor_text, insertion, and rationale. A raw FixProposal with schema_version will not parse, while a versioned envelope will not be the raw FixProposal consumed by evaluate_proposal. This needs two shapes: a raw FixProposal payload plus a separate versioned Arbor envelope, or schema_version must live only in the ledger/verdict contract.
Cited: plan docs/plans/2026-06-22-001-feat-dual-cli-tandem-mvp-plan.md:193, docs/plans/2026-06-22-001-feat-dual-cli-tandem-mvp-plan.md:194, docs/plans/2026-06-22-001-feat-dual-cli-tandem-mvp-plan.md:201; source /home/user01/wiki-forge/src/wiki_forge/schemas.py:148, /home/user01/wiki-forge/src/wiki_forge/schemas.py:155, /home/user01/wiki-forge/src/wiki_forge/schemas.py:157, /home/user01/wiki-forge/src/wiki_forge/schemas.py:166

## High Findings
### 1. High: The plan says Arbor stays primitives-only while adding wiki-forge gate orchestration to core Arbor [KNOWN-ERR-dishonest-scope-boundaries]
The origin requirements and plan scope say target-specific actions and gate orchestration live in the target or an example package, not Arbor core. But U9 authorizes a wiki-forge gate-invocation adapter in src/tandem, and U7's core driver directly calls the wiki-forge additive gate through that adapter. That is target-specific gate orchestration inside the reusable Arbor package, not merely reusable tandem primitives. Move the adapter and prompt contracts under the example/target package, or define a generic gate callback/interface in src/tandem and keep wiki-forge binding outside core.
Cited: plan docs/plans/2026-06-22-001-feat-dual-cli-tandem-mvp-plan.md:228, docs/plans/2026-06-22-001-feat-dual-cli-tandem-mvp-plan.md:264, docs/plans/2026-06-22-001-feat-dual-cli-tandem-mvp-plan.md:265, docs/plans/2026-06-22-001-feat-dual-cli-tandem-mvp-plan.md:331; source docs/brainstorms/2026-06-22-dual-cli-tandem-roles-requirements.md:84, docs/brainstorms/2026-06-22-dual-cli-tandem-roles-requirements.md:85, docs/brainstorms/2026-06-22-dual-cli-tandem-roles-requirements.md:86
### 2. High: The cited CLI probe surfaces cannot classify Claude subscription auth versus API-key auth [KNOWN-ERR-inverted-dependency-claims]
U4 requires preflight to distinguish a subscription-authenticated claude role from an api-key-only claude role. The cited local_cmd probe only runs '<cli> --version' and returns path, runnable, version, and error; local doctor only counts runnable claude/codex binaries. The other cited doctor/preflight code treats environment API keys as present credentials, which the plan correctly says is the inverse of the desired policy. As written, the plan requires an auth-classification feature that the cited dependencies do not model; it must specify a new explicit auth probe/contract or downgrade the claim to fail closed whenever provider credential env vars are present.
Cited: plan docs/plans/2026-06-22-001-feat-dual-cli-tandem-mvp-plan.md:34, docs/plans/2026-06-22-001-feat-dual-cli-tandem-mvp-plan.md:171, docs/plans/2026-06-22-001-feat-dual-cli-tandem-mvp-plan.md:175, docs/plans/2026-06-22-001-feat-dual-cli-tandem-mvp-plan.md:176; source src/cli/commands/local_cmd.py:101, src/cli/commands/local_cmd.py:124, src/cli/commands/local_cmd.py:273, src/cli/commands/local_cmd.py:290, src/cli/commands/doctor_cmd.py:103, src/cli/commands/doctor_cmd.py:125, src/cli/preflight.py:119, src/cli/preflight.py:145
### 3. High: U9 cites FixProposalDraft.from_draft, but the method is FixProposal.from_draft [KNOWN-ERR-invented-identifiers]
The U9 pattern citation names FixProposalDraft.from_draft in wiki-forge schemas.py. The live schema documents and implements from_draft on FixProposal, taking a FixProposalDraft as input. This is exactly the kind of plausible identifier error that sends implementers to the wrong API surface.
Cited: plan docs/plans/2026-06-22-001-feat-dual-cli-tandem-mvp-plan.md:266; source /home/user01/wiki-forge/src/wiki_forge/schemas.py:130, /home/user01/wiki-forge/src/wiki_forge/schemas.py:135, /home/user01/wiki-forge/src/wiki_forge/schemas.py:148, /home/user01/wiki-forge/src/wiki_forge/schemas.py:169

## Neutral Findings
_None._

## Positive Findings
### 1. Verifier exit-code handling is correctly constrained [NOVEL]
The plan correctly avoids using the dogfood verifier exit code as the MVP done-signal and instead says to parse the emitted verdict text. The cited verifier formats a MAINTAINER-DOGFOOD line with verdict=... and returns 0 regardless of verdict, so this is an important accurate constraint.
Cited: plan docs/plans/2026-06-22-001-feat-dual-cli-tandem-mvp-plan.md:33, docs/plans/2026-06-22-001-feat-dual-cli-tandem-mvp-plan.md:270; source /home/user01/wiki-forge/scripts/verify_maintainer_dogfood.py:526, /home/user01/wiki-forge/scripts/verify_maintainer_dogfood.py:531, /home/user01/wiki-forge/scripts/verify_maintainer_dogfood.py:587, /home/user01/wiki-forge/scripts/verify_maintainer_dogfood.py:595

## Highest-Leverage Corrections
1. Move the wiki-forge gate adapter before U7 or replace U7's dependency on U9 with a generic injectable gate interface, then make the phase dependency graph acyclic.
1. Separate the raw wiki-forge FixProposal payload from any versioned Arbor envelope; do not add schema_version to a strict FixProposal object.
1. Keep wiki-forge-specific gate invocation and prompt contracts out of src/tandem core, or explicitly revise the scope boundary to admit a target adapter layer in Arbor.
1. Define a concrete Claude auth-classification probe for subscription versus API-key auth, or fail closed whenever paid-provider credential env vars are present.
1. Correct the U9 schema citation from FixProposalDraft.from_draft to FixProposal.from_draft.
