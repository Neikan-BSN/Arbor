---
status: pending
priority: p3
issue_id: '005'
tags: [tandem, paid-guard, preflight, dx]
dependencies: []
---

# Tandem: paid guard forces a fake `--base-url` marker for claude producers and promises an unimplemented escape

## Problem Statement

`assert_no_paid_backend` (`src/tandem/paid_guard.py`) gates a `claude` role binding on Arbor's LLM provider resolution (`resolve_backend(config.llm.*)`) and fails closed — `producer role bound to claude resolves to paid backend anthropic; configure an explicit local base_url or use a subscription CLI binding` — unless an explicit local `--base-url` marker is set. But in the tandem architecture the role CLIs are ALWAYS opaque subscription subprocesses (`claude --print` / `codex exec`); they never call `provider.create` / the hosted API, so gating them on `config.llm` is a category mismatch. The error's "or use a subscription CLI binding" alternative is unimplemented — the only working escape is passing a fake local `base_url` the subprocess never uses.

Surfaced by U11 Part C (2026-06-28): the documented Part C command failed preflight until `--base-url http://127.0.0.1:4000/v1` was added. Now documented as a required flag in `examples/wiki_forge_doc_maintainer/U11_RUNBOOK.md`.

## Findings

- The real paid-API protections are independent of this check and already in place: `preflight._provider_env_present` fails closed on any `ANTHROPIC_API_KEY`/`ANTHROPIC_AUTH_TOKEN`/`OPENAI_API_KEY`; `paid_guard.scrub_provider_env` strips provider creds from the subprocess env; the spine makes zero `provider.create` calls. So narrowing the `config.llm`-based check for subscription-CLI bindings would NOT weaken the paid-API guarantee.
- `base_url` is consumed ONLY by preflight (`tandem_cmd` → `config.llm.base_url` → `assert_no_paid_backend`); `run_cli_worker`/`build_command` never pass it to the subprocess (verified). It is purely a preflight marker.
- The asymmetry is `cli == "claude"`-specific: a `codex` binding returns early (treated as inherently subscription), so only `claude` producers/reviewers hit this.
- No functional bug once the marker is set (Part C proved the full pipeline green with it); the harm is a confusing failure + a misleading remedy message + a smelly fake marker.

## Proposed Solutions

1. Implement the promised "subscription CLI binding" concept: in the tandem path treat a role bound to a supported subscription CLI (`claude`/`codex`) as inherently non-paid, so no `--base-url` marker is required. Keep the real guard = env-credential presence check + env scrubbing + zero-`create`-calls. Add a regression test proving a run with a provider credential env var set still fails closed.
1. Minimal honesty fix: if the marker requirement stays, change the error message to state the only implemented remedy (set an explicit local `--base-url`) and drop the unimplemented "or use a subscription CLI binding" clause.
1. Document-only (DONE in Part C): the runbook now states the `--base-url` requirement for `claude` producers.

## Recommended Action

Adopt solution 1 — the CLI binding IS the subscription boundary, so gating it on `config.llm` is the category error. Because it changes a fail-closed paid guard, gate it behind tests proving the env-credential fail-closed path is preserved. Until then, solution 2 (honest message) is a safe immediate improvement and the runbook documents the workaround.

## Acceptance Criteria

- A `claude` (or other supported subscription-CLI) producer passes preflight without a fake `--base-url` marker, OR the error message accurately names the only supported remedy.
- The paid-API guarantee is preserved: a run with any provider credential env var set still fails closed (covered by a regression test).
- Tests cover the subscription-CLI-binding-is-not-paid path.

## Work Log

- 2026-06-28: filed from U11 Part C finding (preflight blocker #1). Workaround (`--base-url <local-marker>`) documented in the U11 runbook; the producer-write blocker #2 was fixed separately via `--claude-permission-mode` (Neikan-BSN/Arbor#5).
