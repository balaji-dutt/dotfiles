# AI Co-author Commit Trailer Workflow

This repository manages a Git hook workflow that can append a
`Co-authored-by:` trailer to the next commit message when armed.

## Behavior

- Hook type: `prepare-commit-msg`
- Arming flag: global `coauthor.gptNext` (one-shot)
- Trailer precedence:
  1. repo-local `coauthor.gptTrailer`
  2. global `coauthor.gptTrailer`
  3. built-in default trailer in the hook
- Duplicate protection: does not add the trailer if already present
- Scissors handling: inserts trailer before Git scissors marker if present

## Why `prepare-commit-msg`

Using `prepare-commit-msg` reduces conflict risk with repos that already use
`commit-msg` for tooling such as commitlint or Husky.

## Managed Files

### Host dotfiles

- Hook template:
  - `private_dot_config/git/template/hooks/executable_prepare-commit-msg`
- Global Git config templateDir:
  - `dot_gitconfig.tmpl`
- UI helpers:
  - LazyGit (WSL2/Linux): `private_dot_config/lazygit/config.yml`
  - LazyGit (macOS):
    `private_Library/private_Application Support/lazygit/config.yml`
  - Sublime Merge (WSL2/Linux):
    `private_dot_config/private_sublime-merge/private_Packages/private_User/Default.sublime-commands`
  - Sublime Merge (macOS):
    `private_Library/private_Application Support/Sublime Merge/Packages/User/Default.sublime-commands`
  - Sublime Merge (Windows):
    `AppData/Roaming/Sublime Merge/Packages/User/Default.sublime-commands`

### Container dotfiles

- Hook template:
  - `private_Documents/development/container-dotfiles/dotfiles/private_dot_config/git/template/hooks/executable_prepare-commit-msg`
- Global Git config templateDir:
  - `private_Documents/development/container-dotfiles/dotfiles/dot_gitconfig.tmpl`
- LazyGit:
  - `private_Documents/development/container-dotfiles/dotfiles/private_dot_config/lazygit/config.yml`

## New Repositories

New clones and new `git init` repos receive the hook automatically from
`init.templateDir`.

## Existing Repositories

Templates do not retrofit existing clones. Install the hook once per repo.

### Bash (macOS/WSL2/Linux/Git Bash)

```bash
hook_src="$HOME/.config/git/template/hooks/prepare-commit-msg"
hooks_dir="$(git rev-parse --git-path hooks)"
hook_dst="$hooks_dir/prepare-commit-msg"

cp "$hook_src" "$hook_dst"
chmod +x "$hook_dst" 2>/dev/null || true
```

### PowerShell (Windows)

```powershell
$hookSrc = Join-Path $HOME ".config\git\template\hooks\prepare-commit-msg"
$hooksDir = git rev-parse --git-path hooks
$hookDst = Join-Path $hooksDir "prepare-commit-msg"

Copy-Item $hookSrc $hookDst -Force
```

## Usage

Set global default trailer once (optional):
<!-- markdownlint-disable MD013 -->
```bash
git config --global coauthor.gptTrailer 'Co-authored-by: GPT-5.2 (High Reasoning mode) <noreply@openai.com>'
```
<!-- markdownlint-enable MD013 -->
Arm next commit:

```bash
git config --global coauthor.gptNext true
```

Optional repo-specific override:

```bash
git config --local coauthor.gptTrailer 'Co-authored-by: GPT-5.2 <noreply@openai.com>'
```

Disarm manually:

```bash
git config --global --unset-all coauthor.gptNext
```

## Troubleshooting

- If the trailer is not added in a repo, check for custom `core.hooksPath`
  because it bypasses `.git/hooks`.
- `--no-verify` bypasses hooks by design.
- Keep hook files with LF line endings.
