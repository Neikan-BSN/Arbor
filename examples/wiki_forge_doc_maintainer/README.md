# Wiki-forge doc-maintainer Arbor run package

This package is an operator runbook for a long-running Arbor stress run against
`wiki-forge`. It keeps planning and smoke checks local by default, uses the same
task contract for Claude Code and Codex CLI, and prevents model review from
becoming the final counting authority.

The default path is `arbor local`. Native `arbor run` is documented only for a
local OpenAI-compatible endpoint or an explicitly authorized provider-backed
run.

## Files

| File                                         | Purpose                                                                                       |
| -------------------------------------------- | --------------------------------------------------------------------------------------------- |
| `task.local.md`                              | Prompt contract for `arbor local run` with Claude Code or Codex CLI.                          |
| `research_config.native-local-template.yaml` | Native Arbor template for local endpoints only. Copy into the target repo only after editing. |
| `quality_gate_contract.md`                   | Authority and output contract for the stronger model/manual review gate.                      |
| `verifier_checklist.md`                      | Operator checklist for B_dev, human approval, final verifier, and merge eligibility.          |

## Safe preflight

From the Arbor checkout:

```bash
uv run arbor local doctor
```

This checks the WSL-local Arbor checkout, Arbor skills, Claude Code CLI, and
Codex CLI. It does not use Arbor's native provider runtime.

## Local adapter: Codex CLI

Preview the command first:

```bash
uv run arbor local run --agent codex --cwd <wiki-forge-repo> --dry-run \
  "$(cat examples/wiki_forge_doc_maintainer/task.local.md)"
```

Run it only after the operator grants permission for the requested scope:

```bash
uv run arbor local run --agent codex --cwd <wiki-forge-repo> \
  "$(cat examples/wiki_forge_doc_maintainer/task.local.md)"
```

## Local adapter: Claude Code

Preview the command first:

```bash
uv run arbor local run --agent claude --cwd <wiki-forge-repo> --dry-run \
  "$(cat examples/wiki_forge_doc_maintainer/task.local.md)"
```

Run it only after the operator grants permission for the requested scope:

```bash
uv run arbor local run --agent claude --cwd <wiki-forge-repo> \
  "$(cat examples/wiki_forge_doc_maintainer/task.local.md)"
```

## Native Arbor with a local endpoint

Use this only when a local OpenAI-compatible endpoint is running and the model
choice is explicit. Do not use `provider: auto` for this run package.

1. Copy `research_config.native-local-template.yaml` into the target repo as
   `research_config.yaml`.
1. Edit `llm.model` and `llm.base_url` for the local endpoint.
1. Launch only after confirming the operator wants a native Arbor run.

Example shape:

```bash
uv run arbor run --yes --yes-cwd <wiki-forge-repo> \
  --config <wiki-forge-repo>/research_config.yaml \
  --run-name wiki-forge-doc-maintainer-longrun \
  "$(cat examples/wiki_forge_doc_maintainer/task.local.md)"
```

Provider-backed hosted models require explicit current-turn permission. Before
running them, state the provider, model, endpoint, and intended scope.

## Evidence location

Live run artifacts belong in the target repo:

```text
<wiki-forge-repo>/.arbor/sessions/wiki-forge-doc-maintainer-longrun/
```

Expected evidence includes the Idea Tree, config snapshot, proposal artifacts,
quality gate reports, approval records, verifier snapshots, and final
`REPORT.md`.

## Stop rules

Stop and ask the operator before:

- installing packages, downloading data, using GPU/training, or starting a
  long job;
- editing protected verifier or approval surfaces;
- creating or updating a PR;
- running final verifier with live server metadata;
- merging, pushing, or otherwise changing `main`;
- switching from local/manual review to a paid provider-backed model.

The verifier's process exit code is not the result. Parse `verdict=...` from
the output and follow `verifier_checklist.md`.
