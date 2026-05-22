<!-- markdownlint-disable MD007 MD013 MD023 MD031 MD032 MD034 MD040 MD041 MD051 -->
<!-- markdownlint-configure-file
{
  options": {
    "frontMatter": "(^---\\s*$[^]*?^---\\s*$)(\\r\\n|\\r|\\n|$)"
  },
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

## 8) MVP operating mode recommendation

- Keep explicit single-repo targeting first.
- Keep automerge off initially.
- Keep major updates grouped but separate from minor/patch groups.
- Require a 7-day release age (`minimumReleaseAge`) before updates are eligible,
  except OpenCode plugin patch/digest updates.
- After first successful run, enable weekends-only schedule.

## 9) Statusline auto-sync token (`STATUSLINE_SYNC_TOKEN`)

The dotfiles project ships a vendored copy of the `claude-pace` statusline
script (`dot_claude/executable_statusline.sh` and the container-dotfiles
mirror). A Renovate `customManager` bumps the `CLAUDE_PACE_VERSION`
sentinel when upstream releases ship, but Renovate cannot rewrite the
script body. `.gitlab-ci.yml` closes that gap: on
`renovate/statusline-*` branches it runs `assets/sync-statusline.sh`,
amends the sync onto the Renovate commit, and force-pushes back to the MR
branch before `platformAutomerge` fires.

The amend-and-push step needs a token that can write to this repository.
The built-in `CI_JOB_TOKEN` cannot push branches, and the renovate-runner
group service-account token lives in the runner project (not in this
project's CI variables). A dedicated **Project Access Token** scoped to
the dotfiles project is the right tool.

### Required token type

GitLab **Project Access Token** — not a personal PAT, not a deploy token,
not a group access token. Project Access Tokens act as a bot user
attached to a single project and can push code.

### Required scopes

- `write_repository` — required; allows `git push`.
- `read_repository` — implied by `write_repository`; do not tick
  separately.
- **Do not** grant `api`, `read_user`, `read_registry`, or any other
  scope. The job only needs to push.

### Required role

- **Developer** — sufficient when the `renovate/statusline-*` branches
  are not in the project's protected-branches list. Renovate's default
  behavior keeps MR source branches unprotected, so Developer is the
  right starting point.
- **Maintainer** — required only if `renovate/*` (or
  `renovate/statusline-*` specifically) has been added to Settings →
  Repository → Protected branches. If unsure, leave the role at
  Developer; the first failing CI run will report a
  `pre-receive hook declined` / `protected branch` error and the role
  can be upgraded.

### Steps in the GitLab UI

1. Go to `gitlab.com/balaji-personal-files/dotfiles → Settings →
   Access Tokens → Project access tokens → Add new token`.
2. Set token fields:
   - **Token name:** `statusline-sync` (or `claude-pace-sync`).
   - **Expiry date:** set the maximum GitLab allows (currently 364 days).
     Add a calendar reminder ~14 days before expiry to rotate.
   - **Select a role:** Developer (see "Required role" above).
   - **Select scopes:** tick `write_repository` only.
3. Click **Create project access token**. **Copy the token value
   immediately** — it is shown only once.
4. Store the value in 1Password as an API credential. The 1Password item
   title is purely a human-facing label for recall; it is not referenced
   by any code, chezmoi template, or CI job in this repo. Recommended
   fields (pick any title):
   - Notes: creation date, expiry date, scopes (`write_repository`),
     owning project URL (`gitlab.com/balaji-personal-files/dotfiles`),
     consumer (GitLab CI variable `STATUSLINE_SYNC_TOKEN`).
   - Suggested title, mirroring the renovate-service-account entry:
     `GitLab — dotfiles statusline-sync PAT`.
5. In the **same project → Settings → CI/CD → Variables → Add
   variable**, configure:
   - **Key:** `STATUSLINE_SYNC_TOKEN`.
   - **Value:** paste the token from step 3.
   - **Type:** Variable (not File).
   - **Environment scope:** `*` (all environments).
   - **Protect variable:** **OFF**. `renovate/statusline-*` branches are
     not protected, so a protected variable would be unavailable in the
     sync job and the push would fail.
   - **Mask variable:** **ON**. Hides the value from job logs.
   - **Expand variable reference:** OFF.
6. Click **Add variable**.

### How the CI job consumes the token

The `statusline-sync` job in `.gitlab-ci.yml` rewrites `origin` to embed
the token under the `oauth2:<token>` username convention GitLab accepts
for token-based git auth, then force-pushes:

```bash
git remote set-url origin \
  "https://oauth2:${STATUSLINE_SYNC_TOKEN}@gitlab.com/${CI_PROJECT_PATH}.git"
git push --force-with-lease origin "HEAD:${CI_COMMIT_REF_NAME}"
```

A push made via this Project Access Token (rather than `CI_JOB_TOKEN`)
**does** trigger a new pipeline on the same branch — the desired
behavior, since the new pipeline runs `assets/sync-statusline.sh --check`
to verify the synced body and then unblocks `platformAutomerge`.

### Rotation and revocation

To rotate: create a new token (steps 1–3 above), update the CI variable
value with the new token, then revoke the old token under Settings →
Access Tokens. No code change required.

To revoke: same Settings → Access Tokens page, **Revoke** action on the
token row. The next CI run on a `renovate/statusline-*` branch will
fail loudly, signalling the missing token without leaking history or
performing unwanted pushes.
