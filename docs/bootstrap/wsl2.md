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

- `.chezmoiscripts/run_once_before_00-wsl-provision.sh.tmpl`
- `ansible/wsl-playbook.yml`
- `.chezmoiscripts/run_onchange_after_install_packages.sh.tmpl`

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
- `emacs.yml`
- `debian-dev-tools.yml`
- `certificates.yml`
- `onepassword-setup.yml`

## Validation

After editing WSL-related files:

```sh
./assets/cz-audit.sh check bootstrap-wsl.sh
./assets/cz-audit.sh check ansible/wsl-playbook.yml
chezmoi doctor
```
