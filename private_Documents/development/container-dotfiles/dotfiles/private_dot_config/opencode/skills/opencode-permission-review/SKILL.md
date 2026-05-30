---
name: opencode-permission-review
description: Review OpenCode permission-capture logs, extract safe bash permission patterns, decide host vs devcontainer vs project scope, and clear the capture file after user approval. Use when the user says opencode permission review, permission prompt cleanup, or asks to reduce OpenCode approval prompts.
license: MIT
compatibility: opencode
metadata:
  audience: agent-engineers
  workflow: opencode-permission-review
---

# OpenCode permission review

Use this skill to turn captured OpenCode permission prompts into safer, more
focused `opencode.jsonc` permission rules.

## Inputs

- Captured records from the `permission_capture_read` tool.
- Existing permission config from the host and/or project config.
- User preference for global vs repo-specific rules when the scope is unclear.

If the `permission_capture_*` tools are unavailable, tell the user to restart
OpenCode so the local `opencode-permission-capture` plugin can load.

## Workflow

1. Read capture status and recent records with `permission_capture_read`.
2. Group records by normalized command shape:
   - Trim leading/trailing whitespace.
   - Collapse repeated spaces outside quoted strings when describing patterns.
   - Keep shell operators (`&&`, `||`, `;`, pipes, heredocs) visible.
   - Treat exact commands, command probes, shell chains, and heredocs as separate
     categories.
3. For each group, classify risk and scope:
   - **Safe allow candidate**: read-only, deterministic, no network secrets, no
     destructive side effects, narrow command prefix.
   - **Keep ask**: commits, pushes, package installs, network writes, generated
     shell chains, long heredocs, commands with user data/secrets, or anything
     whose safety depends on context.
   - **Deny candidate**: destructive or credential-exfiltration patterns.
   - **Global host+container**: low-risk, platform-generic probes that are useful
     everywhere, such as `command -v ...`, exact `--help` checks, or read-only
     version checks.
   - **Project-specific**: repo scripts, repo-relative validation commands, or
     paths that only make sense in this checkout.
   - **Host-only/container-only**: commands tied to host paths, `/workspaces`,
     Windows paths, macOS tools, WSL helpers, or devcontainer-only tooling.
4. Propose permission-rule edits only after explaining the classification.
5. Ask the user which proposals to apply when there is any ambiguity.
6. After approved edits and validation, clear the capture file with
   `permission_capture_clear` using confirm value `clear permission capture`.

## Scope rules for this dotfiles repo

- Host global OpenCode config:
  `private_dot_config/opencode/opencode.jsonc`.
- Devcontainer global OpenCode config:
  `private_Documents/development/container-dotfiles/dotfiles/private_dot_config/opencode/opencode.jsonc`.
- Repo/project OpenCode config:
  `.opencode/opencode.jsonc`.
- Shared OpenCode assets under `private_dot_config/opencode/{commands,plugins,skills,prompts}`
  are mirrored into the devcontainer copy by `./assets/sync-devcontainer-assets.sh`.
  Edit the host source and sync; do not hand-edit mirrored copies unless the
  config file is intentionally distinct.

## Permission pattern policy

- OpenCode bash rules are order-sensitive: the last matching rule wins.
- Keep broad `"*": "ask"` rules before specific allows and keep explicit denies
  after allows.
- Prefer exact or narrow prefix patterns.
- Do not recommend broad allows for:
  - arbitrary shell chains like `* && *`, `* || *`, or `*; *`;
  - arbitrary heredocs like `python3 - <<*`, `bash <<*`, or `node <<*`;
  - commits (`git commit*`, `oc-commit*`, `cc-commit*`) without user review;
  - force pushes, hard resets, `rm`, `sudo`, permission changes, disk tools, or
    credential/cloud state access;
  - commands containing tokens, passwords, auth headers, or private URLs.
- If a shell chain is repeatedly safe, prefer decomposing it into individual
  allowed commands or allowing a repo-owned wrapper script instead of allowing
  the full chain shape.
- Long validation heredocs should usually become checked-in scripts or stay
  `ask`; do not hide arbitrary code execution behind a broad permission rule.

## Review output format

Return a concise table with these columns:

| Command/group | Risk | Recommended action | Scope | Rule candidate |
| ------------- | ---- | ------------------ | ----- | -------------- |

Then list:

- rules to add globally to both host and container;
- host-only/container-only rules;
- project-specific rules;
- commands that should remain `ask` or become scripts;
- questions for the user.

Do not edit configs until the user approves the proposed rule set.
