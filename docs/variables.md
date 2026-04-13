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

# Template Variables

Variables are defined in chezmoi templates/data files and consumed by dotfile templates and scripts.

## Common Variables

| Variable | Purpose | Used By |
| :--- | :--- | :--- |
| `name` | user full name | git config |
| `email` | user email | git config |
| `CERTPATH` | certificate directory path | certificate install flows |
| `CERTFILES_RAW` | certificate filenames CSV | prompts + devcontainer hooks |
| `CERTFILES` | certificate filenames | certificate install flows |
| `ansible_key` | ansible ssh private key path | reserved/future use |
| `org_dir` | org-mode directory path | doom emacs config |

## WSL2-Focused Variables

| Variable | Purpose | Used By |
| :--- | :--- | :--- |
| `homelab.nfs_server` | NFS host | WSL mount tasks |
| `homelab.nfs_path` | NFS export path | WSL mount tasks |
| `homelab.windows_user` | Windows username | WSL integration |
| `onepassword.url` | 1Password account URL | 1Password setup |
| `onepassword.email` | 1Password account email | 1Password setup |
| `ccr_port` | Claude Code Router port | router config and devcontainer hooks |

## Safety

- Keep secrets out of git.
- Prefer 1Password/runtime injection for sensitive values.
- Do not commit rendered files containing tokens/keys.
