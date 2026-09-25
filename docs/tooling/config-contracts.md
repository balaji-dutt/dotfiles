<!-- markdownlint-disable MD007 MD013 MD023 MD031 MD032 MD034 MD040 MD041 MD051 -->
<!-- markdownlint-configure-file
{
  "no-trailing-spaces": false,
  "no-hard-tabs": true
}
-->

# Configuration Contracts

Repository policy files and published transient payloads that have custom
structure use immutable, versioned JSON Schema contracts. Structural schemas
are the machine-readable contract; the owning command or specification remains
responsible for semantic checks such as sorted values, tracked paths,
cross-file references, and runtime capability rules.

## Managed contracts

| Instance | Schema | Semantic consumer |
| :--- | :--- | :--- |
| `configs/automation-provenance.json` | `configs/schemas/automation-provenance.v2.schema.json` | `assets/check-automation-provenance.py` |
| `configs/automation-test-inventory.json` | `configs/schemas/automation-test-inventory.v3.schema.json` | `assets/check-automation-test-inventory.py` |
| `configs/ai-tooling-support.json` | `configs/schemas/ai-tooling-support.v1.schema.json` | `assets/check-ai-tooling.py` |
| `configs/claude-mcp.json` | `configs/schemas/claude-mcp.v1.schema.json` | `assets/check-ai-tooling.py`; `assets/claude-mcp-apply.py`; `.chezmoiscripts/run_onchange_after_claude_mcp_servers.ps1.tmpl` |
| `configs/devcontainer-sync.jsonc` | `configs/schemas/devcontainer-sync.v1.schema.json` | `assets/check-automation-provenance.py`; `assets/sync-devcontainer-assets.sh`; `bin/executable_devcontainer-launch.tmpl` |
| `configs/gitlab-pipeline-guard.json` | `configs/schemas/gitlab-pipeline-guard.v1.schema.json` | `assets/check-gitlab-pipeline.py`; `assets/gitlab_pipeline_runtime.py` |
| `configs/host-ai-plugin-refresh.jsonc` | `configs/schemas/host-ai-plugin-refresh.v1.schema.json` | `.chezmoiscripts/run_onchange_after_host_ai_plugin_refresh.sh.tmpl`; `.chezmoiscripts/run_onchange_after_host_ai_plugin_refresh.ps1.tmpl` |
| `configs/browser-policies/justthebrowser/manifest.json` | `configs/schemas/justthebrowser-manifest.v1.schema.json` | `assets/sync-browser-policies.py` |
| `configs/packages.yaml` | `configs/schemas/packages.v1.schema.json` | `ansible/wsl-playbook.yml` |
| `configs/plannotator-assets.json` | `configs/schemas/plannotator-assets.v1.schema.json` | `assets/sync-plannotator-assets.py` |
| `configs/test-suites.json` | `configs/schemas/test-suites.v1.schema.json` | `assets/run-tests.py` |

Each JSON or JSONC instance has a repository-relative `$schema` link and an
integer `schema_version`. Its consumers check both before processing the rest
of the file. The schemas use JSON Schema Draft 2020-12 and reject undeclared
fields.

`configs/packages.yaml` is the one association-only exception. Its
`yaml-language-server` header points editors at the schema, but the file does
not add `$schema` or `schema_version` data keys because Ansible loads every
top-level key as a variable. Ansible remains the semantic consumer.

## Standalone contracts

These schemas describe transient inputs or opt-in policies for other repositories.
They have no active tracked instance in this repository.

| Payload | Schema | Semantic authority |
| :--- | :--- | :--- |
| AI attestation handoff | `configs/schemas/ai-attestation-handoff.v1.schema.json` | `docs/git-agent-attestation.md` |
| GitHub pipeline policy (`configs/pipeline-guard.json` in a consuming repo) | `configs/schemas/pipeline-guard.v1.schema.json` | `assets/pipeline_policy.py`; `docs/tooling/continuous-integration.md` |

The attestation handoff is invocation-scoped or stored briefly in a
worktree-private Git path. Its schema defines the closed v1 JSON shape; the
normative document defines transport lifecycle, trailer rendering, and trust
semantics.

The GitHub policy selects a host, repository, guarded remote/ref, required workflow
IDs or filenames, and request bounds. It conflicts with a legacy GitLab policy;
workflow YAML alone does not opt in. The clone-local account pin and Git-common-dir
evidence records are not tracked policy fields. Dotfiles retains its active GitLab
policy unchanged.

## Inventory boundaries

The catalog covers custom, active repository policy formats. Other tracked
JSON, JSONC, YAML, and TOML files remain under their existing authorities:

- Chezmoi (`.chezmoidata.yaml`, `.chezmoiexternal.toml`), Ansible
  (`ansible/**/*.yml`), and mise (`configs/mise*.toml`) files use those tools'
  native formats.
- Managed OpenCode, Claude Code, Espanso, Beads, AoE, Starship, Lazygit, `gh`,
  and `glab` settings use their owning tools' formats. This includes root tool
  settings and the corresponding `dot_*`, `private_dot_config/**`, platform,
  and container-mirror paths.
- `.gitlab-ci.yml` and `dot_markdownlint-cli2.jsonc` use the formats owned by
  GitLab CI and markdownlint-cli2.
- `configs/winget-packages.json` is a WinGet export and uses Microsoft's
  schema. Promptfoo `package.json` and lock files use npm's formats.
- `configs/schemas/*.schema.json` are schema documents, not policy instances;
  contract tests check their shared metadata and immutable inventory.
- `.agentic-tooling/generated-manifest.json` and files under generated
  container mirrors are checked against their canonical source and provenance
  rules rather than assigned duplicate contracts.
- Vendored browser payloads retain byte-and-digest validation. Their custom
  `manifest.json` is managed above, but the payload formats are not.
- Files under `archive/**` are historical and are not active contracts.

## Versioning

The migrated legacy formats use v1 as their first formal version. Informal
`schema`, `schemaVersion`, or missing markers did not publish a previous JSON
Schema contract, so normalizing those markers does not consume a later version.

Published `configs/schemas/*.vN.schema.json` files are immutable. Breaking changes
get a new schema file and a new `$id` suffix. Managed instances also
receive a matching `schema_version`; standalone payloads carry the version
marker their schema defines. Update the instance or payload producer, consumer,
tests, migration notes, and this catalog in the same change. Retain published
schemas as historical contracts; the automation provenance v1 and automation
inventory v1 and v2 schemas remain immutable while their active successors
carry the breaking changes. Automation inventory v3 dropped the stored
`candidate_digest`; the reviewed candidate set now lives line by line in
`configs/automation-candidates.txt` so most concurrent additions merge cleanly.

Compatible semantic tightening can remain within the consumer when the JSON
shape is unchanged. Document the new rule and add a consumer test rather than
silently broadening the schema.

## Validation

The repository does not require a JSON Schema runtime package. Standard-library
consumers fail closed on marker identity and the fields they use, while contract
tests verify schema links and the common immutable-version rules:

```sh
python3 -m unittest tests.test_config_contracts
./assets/run-tests.sh provenance
```

Editors and optional external validators may follow each instance's `$schema`
link for the full structural contract. `check-jsonschema` can validate local
Draft 2020-12 JSON, YAML, and TOML, but accepts one schema per invocation and
needs optional JSON5 support plus a forced file type for JSONC. The SourceMeta
JSON Schema CLI has portable binaries but does not cover TOML or JSONC. Neither
is provisioned consistently on macOS, WSL2, and native Windows, so adopting one
as a required validator would add format-specific wrappers and a new
cross-platform dependency. The repository therefore keeps them optional and
leaves semantic authority with each consumer.
