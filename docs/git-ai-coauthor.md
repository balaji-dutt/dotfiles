# AI Co-author Commit Trailer Workflow

This repository manages a Git hook workflow that can append a
`Co-authored-by:` trailer to the next commit message when armed.

## Behavior

- Hook type: `prepare-commit-msg`
- Arming flag: global `coauthor.gptNext` (one-shot)
- Each `chezmoi apply` copies managed template hooks into existing repositories
  under the standard development roots. Git's global `core.hooksPath` remains
  unset so repository-specific hook frameworks keep working.
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

The `20-git-template-hooks` apply hook retrofits repositories found directly at
the configured development roots or one directory below them. It records a
content hash for each installed hook in the repository's Git hooks directory.
Later applies update or remove only an unchanged, recorded copy. Byte-identical
copies are adopted; unrelated hooks and unknown same-name hooks are preserved.

Valid repository-specific `core.hooksPath` values are also preserved and
reported. The apply hook removes only two stale local overrides after seeding
the default hooks directory: the old managed template path and a missing
`.beads/hooks` path.

## Usage

Set global default trailer once (optional):
<!-- markdownlint-disable MD013 -->
```bash
git config --global coauthor.gptTrailer 'Co-authored-by: GPT-5.5 (High Reasoning mode) <noreply@openai.com>'
```
<!-- markdownlint-enable MD013 -->
Arm next commit:

```bash
git config --global coauthor.gptNext true
```

Optional repo-specific override:

```bash
git config --local coauthor.gptTrailer 'Co-authored-by: GPT-5.5 <noreply@openai.com>'
```

Disarm manually:

```bash
git config --global --unset-all coauthor.gptNext
```

## Troubleshooting

- If the trailer is not added in a repo, inspect the latest `chezmoi apply`
  output for a preserved same-name hook, inaccessible repository, or custom
  `core.hooksPath`. Resolve custom paths in the repository that owns them; the
  apply hook intentionally does not replace valid repository-specific paths.
- `--no-verify` bypasses the Beads pre-commit guard. Git still runs
  `prepare-commit-msg` hooks by design.
- Keep hook files with LF line endings.
