<!-- markdownlint-disable MD007 MD013 MD023 MD031 MD032 MD034 MD040 MD041 MD051 -->
<!-- markdownlint-configure-file
{
  "no-trailing-spaces": false,
  "no-hard-tabs": true
}
-->

# Automated Git identity policy

This policy sets the author, committer, and attribution trailers for commits
that OpenCode and Renovate create in this repository. GitLab
(`balaji-personal-files/dotfiles`) is canonical. A GitLab push mirror copies
protected branches, which today means only `main`, to
`github.com/balaji-dutt/dotfiles` without changing SHAs or identities. The
GitHub results described here therefore apply only to commits that land on
`main`.

Decision record: [ADR 0004](../decisions/0004-github-attribution-coauthor-trailers.md).
Decided 2026-09-25 under `dots-a0jr.1`. Implementation is tracked by
`dots-a0jr.2` (OpenCode) and `dots-a0jr.3` (Renovate), and verification after
rollout by `dots-a0jr.4`.

## Four separate roles

| Role | Recorded in | Who sets it |
|---|---|---|
| Author | Git `author` header | The tool that produced the change |
| Committer | Git `committer` header | The process that wrote the commit object |
| Authenticated pusher | GitLab audit log, MR and push events | The token owner (`RENOVATE_TOKEN`, `VENDOREDFILE_SYNC_TOKEN`, or the user's credentials) |
| AI/tool provenance | `AI-Participant` trailer groups ([spec](../git-agent-attestation.md)) | Commit wrappers |

A `Co-authored-by:` trailer adds a fourth, GitHub-facing attribution field. It
does not replace any of the four roles, and Git committer metadata is not
evidence of who pushed.

## Current identity matrix

Observed 2026-09-25 from source, local history, and the GitLab and GitHub APIs.
"GitHub login" is the top-level `author.login` / `committer.login` in
`GET /repos/balaji-dutt/dotfiles/commits`.

| Path | Author | Committer | Identity source | Trailers | GitHub login |
|---|---|---|---|---|---|
| `oc-commit` (Bash, host and container), new commit | `OpenCode <noreply@opencode.ai>` | same | `GIT_AUTHOR_*` / `GIT_COMMITTER_*` exported by `bin/executable_oc-commit` | `AI-Participant` groups | null / null |
| `oc-commit.ps1`, new commit | same | same | Process environment set by `dot_local/executable_oc-commit.ps1` | `AI-Participant` groups | null / null |
| `oc-commit --amend` (either wrapper) | Original author, kept by Git | `OpenCode` | `--amend` ignores `GIT_AUTHOR_*` without `--reset-author` | Existing trailers kept, wrapper trailers appended | depends on the original author |
| `agent-wt-merge no-ff --actor opencode` | `OpenCode` | `OpenCode` | `actor_env()` in `assets/agent-wt-merge` | none | null / null |
| Renovate initial commit | `Renovate Bot <service_account_group_65498163_…@noreply.gitlab.com>` | same | GitLab `/user` of the `RENOVATE_TOKEN` owner (`personalfiles-renovatebot`) | none | null / null |
| Renovate commit amended by CI (`statusline-sync`, `beads-kanban-pin-sync`, `browser-policy-sync`) | Renovate's original author, kept by Git | `Renovate Bot` (same address) | `GIT_COMMITTER_*` in `.gitlab-ci.yml`; the job's `GIT_AUTHOR_*` exports have no effect under `--amend --no-edit` | Original message kept | null / null |
| Renovate automerge merge commit | `Renovate Bot` | same | GitLab merge performed as the token owner | none | null / null |
| `git ocauth` / `git clauth` aliases | `--author` value | the user's own identity | `dot_gitconfig.tmpl` alias | none | superseded by `oc-commit` / `cc-commit`; retirement is a follow-up |

Renovate's effective `gitAuthor` resolution in this deployment runs from the
highest priority down:

1. The repository `renovate.json5`, which sets none.
2. CLI flags in `RENOVATE_EXTRA_FLAGS`, which set none.
3. The `RENOVATE_GIT_AUTHOR` environment variable, set in neither the runner
   file, the upstream template, nor the project and group CI/CD variables.
4. A global config file, which doesn't exist.
5. The GitLab `/user` fallback, which is what applies.

The runner (`balaji-personal-files/renovate-runner`) includes
`renovate-bot/renovate-runner` templates at `v26.0.0`. The image tag
`ghcr.io/renovatebot/renovate:43` floats, and the 2026-09-24 scheduled job
reported version `43.288.0`. Source references at that tag:

- GitLab fallback: `lib/modules/platform/gitlab/index.ts:141-151`
- Repository override: `setUserRepoConfig` in `lib/util/git/index.ts`
- Author-email-only branch-modified check: `lib/util/git/index.ts:1000-1012`
- `commitBody` appended after a blank line:
  `lib/workers/repository/update/branch/index.ts:728-731`

Renovate writes `user.name` and `user.email` into its local Git config and
commits without `--author`, so its author and committer are always identical.

## Decision

### OpenCode

Keep `OpenCode <noreply@opencode.ai>` as author and committer. Add exactly one
trailer to every commit that repository tooling writes for OpenCode:

```text
Co-authored-by: opencode-agent[bot] <opencode-agent[bot]@users.noreply.github.com>
```

- Scope: `oc-commit` (Bash, PowerShell, and the container copy), including
  `--amend`, plus `agent-wt-merge` no-ff merges run with `--actor opencode`.
- Placement: in the final trailer block, before the `AI-Participant` groups,
  so the participant groups stay contiguous at the end.
- Exactly once: an identical existing line must not be duplicated, for
  example on amend (`trailer.Co-authored-by.ifexists=addIfDifferent`). Existing
  genuine co-authors and original authors are preserved.
- `AI-Participant` remains the provenance record. The co-author trailer exists
  only for GitHub attribution.
- The address is the one OpenCode's own GitHub integration configures:
  `AGENT_USERNAME = "opencode-agent[bot]"` and
  `user.email = opencode-agent[bot]@users.noreply.github.com` in
  [`github.handler.ts` at `16c56fe5`](https://github.com/anomalyco/opencode/blob/16c56fe5ecc3305028d1f0a9cff5806e51c9d480/packages/opencode/src/cli/cmd/github.handler.ts#L143).
  The GitHub account is `opencode-agent[bot]`, ID 219766164, type Bot.
- Fallback if `dots-a0jr.4` shows the address does not resolve: switch to the
  ID form `219766164+opencode-agent[bot]@users.noreply.github.com`, forward
  only.

### Renovate

Keep the service-account identity as author and committer. Set this in
`renovate.json5`:

```json5
commitBody: "Co-authored-by: renovate[bot] <29139614+renovate[bot]@users.noreply.github.com>",
```

- `commitBody` can be set in the repository config (it is not global-only).
  Renovate appends it after a blank line, so it forms its own trailer block.
- CI amendments use `--no-edit`, so the trailer survives them.
- Any future package rule that sets `commitBody` replaces this value and must
  carry the trailer too.
- `gitAuthor` stays unset, so Renovate's own branch-modified check keeps
  comparing against the same address it uses today. No `gitIgnoredAuthors`
  migration is needed.
- The owner accepted a known inaccuracy: `renovate[bot]` (ID 29139614) is
  the GitHub App that Mend hosts, and it takes no part in this self-hosted
  run. The trailer credits the Renovate software, not that App.

### Unchanged

- `RENOVATE_TOKEN` and `VENDOREDFILE_SYNC_TOKEN` authentication, scopes, and
  protection flags.
- Service-account attribution of GitLab pushes and MR operations.
- The amendment jobs' branch guards and explicit `--force-with-lease` leases.
- Branch protections. GitLab push rules are not available on the group's
  Free plan: the push-rule endpoints return 404 for projects 44618209 and
  81546422 and group 65498163. So no author-email, committer, signature, or
  DCO rule applies today. Trailers change no author or committer address, so
  adding a push rule later would not conflict with this policy.
- Claude attribution and linking of historical human emails.

## Rejected options

- **Human authorship for agent commits.** `oc-commit` and `cc-commit` exist
  so the harness that produced and committed the change is the author.
  Improving a personal contribution graph does not justify changing that.
- **Raw author and committer set to `opencode-agent[bot]`.** The upstream
  evidence supports this address as a co-author for locally directed work,
  not as the raw identity of commits the App never made.
- **An owner-controlled automation account for OpenCode.** It would resolve
  on GitHub, but it costs an account to maintain. It is the fallback if the
  co-author trailer proves unsuitable.
- **`gitAuthor` set to `renovate[bot]`.** Author and committer would both
  claim the Mend App. Every open Renovate branch would count as modified
  unless the old address went into `gitIgnoredAuthors`. And CI amendments
  would leave a service-account committer under a `renovate[bot]` author.
- **A portable author with a service-account committer for Renovate.**
  Renovate always writes both from `gitAuthor`. Splitting them would need a
  runner-level Git environment change, not a repository setting.
- **`.mailmap`.** It changes only `git log` and `git shortlog` output. GitHub
  does not use it for attribution. Adding one needs its own justification.
- **Rewriting historical commits.** History stays as is, and there is no
  force-push of rewritten `main`.

## Expected GitHub results

- Top-level `author.login` and `committer.login` stay null for OpenCode and
  Renovate commits, because the raw addresses do not change.
- The commit page lists the co-author. The upstream reporter in
  [OpenCode #43358](https://github.com/anomalyco/opencode/issues/43358#issuecomment-5346664581)
  saw the avatar, and OpenCode appeared as a repository contributor. This
  repository's own result is recorded by `dots-a0jr.4`.
- Renovate automerge merge commits and existing history carry no trailer and
  stay unresolved.
- No change to the owner's personal contribution graph is promised.

## Rollout and rollback

1. This policy and ADR 0004 land on `main`.
2. `dots-a0jr.2`: OpenCode wrappers, the container copy, `agent-wt-merge`, and
   tests for exact-once insertion on new commits, amendments, and merges.
3. `dots-a0jr.3`: `commitBody` in `renovate.json5`. The CI amendment jobs need
   no identity change.
4. `dots-a0jr.4`: check a genuinely new OpenCode commit, a new Renovate
   commit, and an amended Renovate commit after they mirror to GitHub.

Rollback is forward-only: revert the tooling or config commit. Commits that
already carry a trailer keep it.

## Related

- [Renovate GitLab runner setup](renovate-gitlab-runner-setup.md)
- [Git agent attestation](../git-agent-attestation.md) and
  [ADR 0003](../decisions/0003-git-agent-attestation.md)
- [GitHub: creating a commit with multiple authors](https://docs.github.com/en/pull-requests/committing-changes-to-your-project/creating-and-editing-commits/creating-a-commit-with-multiple-authors)
