<!-- markdownlint-disable MD007 MD013 MD023 MD031 MD032 MD034 MD040 MD041 MD051 -->
<!-- markdownlint-configure-file
{
  "no-trailing-spaces": false,
  "no-hard-tabs": true
}
-->

# Renovate GitLab runner setup

This runbook captures the working setup for Renovate on GitLab.com.

> These steps were captured against GitLab v19 documentation/UI and may need
> minor navigation adjustments on later GitLab versions.

## 1) Create a group service account

1. Go to `Home -> Groups -> <Group> -> Settings -> Service Accounts`.
2. Select **Add service account**.
3. Set name and username (recommended pattern:
   `<group-name>-renovatebot`).

## 2) Create and store the access token

1. In the same Service Accounts page, open the account menu (`...`).
2. Select **Manage Access Tokens**.
3. Create a token with these scopes:
   - `read_user`
   - `api`
   - `write_repository`
4. Set an expiry date (GitLab currently allows up to 364 days).
5. Store the generated token in 1Password as an API credential with:
   - clear title
   - creation date
   - expiry date

## 3) Grant repo access to the service account

1. Go to `Groups -> <Group> -> <Project> -> Manage -> Members`.
2. Choose **Invite members**.
3. Add the service-account username.
4. Assign at least **Developer** role.

## 4) Create a dedicated runner project

1. Go to `Groups -> <Group> -> Create project`.
2. Name it `renovate-runner`.
3. Set visibility to **Private**.
4. Enable **Secret Detection**.
5. Clone locally if needed:

```bash
git clone git@gitlab.com:<group>/renovate-runner.git
```

## 5) Configure `.gitlab-ci.yml` in runner project

Use the Renovate runner template + validator template + secret detection.

```yaml
include:
  - project: 'renovate-bot/renovate-runner'
    file: '/templates/renovate.gitlab-ci.yml'
    ref: 'v26.0.0'
  - project: 'renovate-bot/renovate-runner'
    file: '/templates/renovate-config-validator.gitlab-ci.yml'
    ref: 'v26.0.0'
  - template: Security/Secret-Detection.gitlab-ci.yml

variables:
  SECRET_DETECTION_ENABLED: 'true'
  RENOVATE_EXTRA_FLAGS: '<group>/<repo> --onboarding=false'
```

### Important include-ref gotcha

- GitLab include `ref` must be an actual Git ref (branch/tag/SHA).
- `26.0.0` may be a release label, while the real tag can be `v26.0.0`.
- If pipeline shows `reference 26.0.0 does not exist`, switch to the actual
  tag name (`v26.0.0` in this example).

## 6) Configure runner project CI/CD variables

Set the following variables in runner project settings:

- `RENOVATE_TOKEN` (service-account token)
- `RENOVATE_GITHUB_COM_TOKEN` (recommended for GitHub-backed metadata)

`RENOVATE_EXTRA_FLAGS` can stay in `.gitlab-ci.yml` for explicit repo targeting
or be moved to CI/CD variables later.

## 7) Trigger the first manual run

1. Open `renovate-runner` on GitLab.com.
2. Go to `Build -> Pipeline schedules`.
3. Create a schedule if none exists yet (weekends-only is supported).
4. Use the **Play** action on the schedule to run immediately.
5. Open `Build -> Pipelines` and inspect the latest pipeline.

Verify:

- `renovate-config-validator` succeeds.
- `renovate` job targets only the intended repository.
- Renovate loads checked-in repo config and opens expected MRs only.

### Local config validation

macOS and WSL2 hosts install the Renovate npm package through the shared
`configs/mise.toml` tool manifest. After running `mise install -y` (or the
normal `chezmoi apply` provisioning path), validate repo config with:

```sh
mise exec -- renovate-config-validator renovate.json5 --no-global
```

If your shell has already activated mise shims, this is equivalent:

```sh
renovate-config-validator renovate.json5 --no-global
```

## 8) MVP operating mode recommendation

- Keep explicit single-repo targeting first.
- Keep automerge off initially.
- Keep major updates grouped but separate from minor/patch groups.
- Require a 7-day release age (`minimumReleaseAge`) before updates are eligible,
  except OpenCode plugin patch/digest updates.
- After first successful run, enable weekends-only schedule.

### GitLab stability-status warning workaround

If the runner logs this warning while processing Renovate branches:

```text
TypeError: err.body?.message?.startsWith is not a function
  at setBranchStatus -> setStatusCheck -> setStability
```

Renovate is failing while publishing the `minimumReleaseAge` status check
(`renovate/stability-days`) to GitLab. GitLab can return a non-string status
API error body, and Renovate 43.233.4 mishandles that shape while logging the
failure.

This repository avoids that GitLab status path by keeping release-age checks
internal to Renovate:

- `internalChecksFilter: "strict"` filters releases until they meet the age
  gate.
- `statusCheckWhen.minimumReleaseAge: "never"` stops publishing
  `renovate/stability-days` commit statuses to GitLab.

Leave `prCreation` at Renovate's default. Combining `prCreation: "not-pending"`
with a non-zero `minimumReleaseAge` disables the not-pending timeout and can
leave otherwise eligible updates stuck in the Dashboard `Pending Status Checks`
section instead of creating MRs. GitLab notification emails come from the MR
events, so blocked MR creation also blocks the usual notification path.

The Renovate Dashboard remains the manual escape hatch: you can still force an
early MR when you intentionally want to bypass the waiting period. Avoid
re-enabling `renovate/stability-days` statuses unless Renovate has fixed the
GitLab catch-block bug or the runner has been validated with that status path.

Runner-side `RENOVATE_X_GITLAB_SKIP_STATUS_WITHOUT_PIPELINE=true` may help only
when the status update lacks a pipeline id. It is not the primary fix for this
repo because the observed failure can also come from other GitLab status API
response shapes.

## 9) Vendored-file auto-sync token (`VENDOREDFILE_SYNC_TOKEN`)

The dotfiles project vendors upstream files that Renovate can pin but cannot
fully rewrite by itself:

- the `claude-pace` statusline script (`dot_claude/executable_statusline.sh`
  and the container-dotfiles mirror)
- Just the Browser policy artifacts under
  `configs/browser-policies/justthebrowser/**`

`.gitlab-ci.yml` closes that gap on known Renovate branches:

- `renovate/statusline-*` runs `assets/sync-statusline.sh`
- `renovate/browser-policies-*` runs
  `python3 assets/sync-browser-policies.py --write`

Each job amends the synced files onto the Renovate commit and force-pushes back
to the MR branch before `platformAutomerge` can fire.

The amend-and-push step needs a token that can write to this repository.
The built-in `CI_JOB_TOKEN` cannot push branches, and the existing
group-level `RENOVATE_TOKEN` is marked Protected — so it is invisible to
pipelines that run on unprotected vendored-file Renovate branches.
A dedicated, scoped token is therefore required.

### Required token type

**Service Account Access Token** issued from the existing `renovate-bot`
service account (the same account that owns the `RENOVATE_TOKEN` you
already use). Reusing the service account — but with a second, narrower
token — keeps the pusher identity in GitLab's audit log aligned with the
commit author identity set by the CI job
(`Renovate Bot <service_account_group_65498163_...@noreply.gitlab.com>`),
so a single MR shows one consistent actor end-to-end.

### Required scopes

- `write_repository` — required; allows `git push`.
- **Do not** tick `read_user`, `api`, `read_registry`, `self_rotate`,
  or any other scope. This token only needs to push the body-sync
  commit; broader scopes would expand blast radius for no benefit.

### Required role

The `renovate-bot` service account already has at least Developer
membership on the dotfiles project (per section 3 of this runbook).
Service-account access tokens inherit that membership; no role is
selected at token-creation time. Verify:

- Developer is sufficient when `renovate/statusline-*` branches are
  **not** in Settings → Repository → Protected branches (the default).
- If you've added `renovate/*` to protected branches, upgrade the
  `renovate-bot` membership on dotfiles to Maintainer; the first failing
  CI run will report a `pre-receive hook declined` / `protected branch`
  error if the role is too low.

### Steps in the GitLab UI

1. Go to
   `gitlab.com/groups/balaji-personal-files/-/service_accounts`
   (Group → Settings → Service Accounts) and locate the `renovate-bot`
   service account.
2. Open the account menu (`⋮`) → **Manage Access Tokens** → **Add new
   token**.
3. Set token fields:
   - **Token name:** `vendored-file-sync`.
   - **Expiry date:** set the maximum GitLab allows (currently 364 days).
     Add a calendar reminder ~14 days before expiry to rotate.
   - **Select scopes:** tick `write_repository` only.
4. Click **Create token**. **Copy the token value immediately** — it is
   shown only once.
5. Store the value in 1Password as an API credential. The 1Password item
   title is purely a human-facing label for recall; it is not referenced
   by any code, chezmoi template, or CI job in this repo. Recommended
   fields (pick any title):
   - Notes: creation date, expiry date, scopes (`write_repository`),
     owning service account (`renovate-bot` in group
     `balaji-personal-files`), consumer (GitLab CI variable
     `VENDOREDFILE_SYNC_TOKEN` on the dotfiles project).
   - Suggested title, parallel to the existing renovate-bot main-PAT
     entry: `GitLab — renovate-bot vendored-file-sync PAT`.
6. Go to **Project → `gitlab.com/balaji-personal-files/dotfiles` →
   Settings → CI/CD → Variables → Add variable**, then configure:
   - **Key:** `VENDOREDFILE_SYNC_TOKEN`.
   - **Value:** paste the token from step 4.
   - **Type:** Variable (not File).
   - **Visibility:** Masked (hides the value from job logs).
   - **Flags → Protect variable:** **OFF**. `renovate/statusline-*` and
     `renovate/browser-policies-*` branches are not protected, so a
     Protected variable would be invisible to the sync job and the push
     would fail. This is also
     why the variable lives at *project* level rather than next to
     `RENOVATE_TOKEN` at group level — the existing group variable is
     Protected and only exposed to protected branches, which is correct
     for Renovate's own scheduled pipelines but wrong for ours.
   - **Environment scope:** `*` (all environments).
   - **Expand variable reference:** OFF.
7. Click **Add variable**.

### How the CI job consumes the token

The sync jobs in `.gitlab-ci.yml` rewrite `origin` to embed
the token under the `oauth2:<token>` username convention GitLab accepts
for token-based git auth, then force-pushes:

```bash
git remote set-url origin \
  "https://oauth2:${VENDOREDFILE_SYNC_TOKEN}@gitlab.com/${CI_PROJECT_PATH}.git"
git push --force-with-lease origin "HEAD:${CI_COMMIT_REF_NAME}"
```

Because the token is owned by `renovate-bot`, GitLab records the pusher
in the project's audit log as the renovate-bot service account —
matching the `Renovate Bot` author the CI job sets via `GIT_AUTHOR_*`
env vars. A push made via this access token (rather than
`CI_JOB_TOKEN`) **does** trigger a new pipeline on the same branch. The
superseding pipeline verifies the synced files with the relevant `--check`
mode before `platformAutomerge` proceeds.

### Rotation and revocation

To rotate: create a new token via the same Group → Service Accounts →
renovate-bot → Manage Access Tokens page (steps 1–4 above), update the
`VENDOREDFILE_SYNC_TOKEN` CI variable on the dotfiles project with the
new value, then revoke the old token from the same page. The broader
`RENOVATE_TOKEN` is independent and is not affected.

To revoke: same Manage Access Tokens page, **Revoke** action on the
token row. The next CI run on a supported vendored-file Renovate branch will
fail loudly, signalling the missing token without leaking history or performing
unwanted pushes.
