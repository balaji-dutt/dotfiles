<!-- markdownlint-disable MD007 MD013 MD023 MD031 MD032 MD034 MD040 MD041 MD051 -->
<!-- markdownlint-configure-file
{
  "no-trailing-spaces": false,
  "no-hard-tabs": true
}
-->

# WSL2 Bootstrap and Provisioning

This flow is intended for fresh Ubuntu/Debian WSL2 environments.

## Step 1: Initial Bootstrap

Run:

```sh
./bootstrap-wsl.sh
```

Bootstrap installs prerequisites (for example `chezmoi`, `ansible` bootstrap dependencies, and helper tools), then initializes local state for this repository.

## Step 2: Apply and Provision

Run:

```sh
chezmoi apply
```

This triggers key hooks and provisioning, including:

- `.chezmoiscripts/run_onchange_before_00-wsl-provision.sh.tmpl`
- `ansible/wsl-playbook.yml`

## Ansible Structure

Main playbooks:

- `ansible/wsl-playbook.yml`
- `ansible/requirements.yml`

Task files under `ansible/tasks/`:

- `apt-repos.yml`
- `base-packages.yml`
- `zsh-setup.yml`
- `system-config.yml`
- `ubuntu-extras.yml`
- `debian-dev-tools.yml`
- `certificates.yml`
- `onepassword-setup.yml`

WSL2 package and tool hydration is owned by the Ansible playbook. Changes to
watched inputs such as `.chezmoidata.yaml`, `configs/packages.yaml`,
`configs/mise*.toml`, `configs/uv_tools.txt`, and npm/bun manifests retrigger
the WSL provisioning hook on the next `chezmoi apply`. The WSL lazygit binary is
installed to `~/.local/bin/lazygit` from the Renovate-managed
`.chezmoidata.yaml:lazygit_version` pin.

## Validation

After editing WSL-related files:

```sh
./assets/cz-audit.sh check bootstrap-wsl.sh
./assets/cz-audit.sh check .chezmoiscripts/run_onchange_before_00-wsl-provision.sh.tmpl
./assets/cz-audit.sh check ansible/wsl-playbook.yml
chezmoi doctor
```
