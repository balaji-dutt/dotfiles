<!-- markdownlint-disable MD007 MD023 MD031 MD032 MD034 MD040 MD041 MD051 -->
<!-- markdownlint-configure-file
{
  options": {
    "frontMatter": "(^---\\s*$[^]*?^---\\s*$)(\\r\\n|\\r|\\n|$)"
  },
  "no-trailing-spaces": false,
  "no-hard-tabs": true
}
-->

## Dotfiles README

This repo contains configuration files that I use across Linux & macOS, managed using [chezmoi](https://www.chezmoi.io/).

The list of configurations that I'm currenly managing through this repo are:

* Bash Shell
* Git
* Ansible
* Sublime Merge
* lazygit
* Emacs (more specifically the Doom Emacs framework) configuration and dependent shell scripts covering:
  * Custom fonts
  * Emacs tabs (centaur-tabs)
  * Emacs spellcheck (hunspell)
  * org-mode
  * Custom elisp functions.
* ZSH Shell with the following frameworks:
  * antidote (with zprezto and oh-my-zsh framework functions)
  * powerlevel10k
* Brew package manager
* Mise (Runtime Manager)
* Claude (Settings)
* Claude Code (Config)
* Package Managers (tracked lists):
  * pipx packages
  * uv tools
  * npm global packages
  * bunx global packages
* Devcontainers
