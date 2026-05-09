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
