# Modern CLI habit helpers for ripgrep, fzf, and common shell antipatterns.

if (( ${+commands[rg]} )); then
    alias ri='rg -i'                         # Search contents case-insensitively.
    alias rn='rg -n'                         # Search contents and show line numbers explicitly.
    alias rl='rg -l'                         # List only files containing matches.
    alias rc='rg -c'                         # Count matches per file.
    alias rh='rg --hidden --glob "!.git/*"'  # Search hidden files while skipping .git internals.
    alias ru='rg --no-ignore'                # Search files normally skipped by ignore files.
fi

# Interactively pick a project file using rg --files and fzf.
rgf() {
    emulate -L zsh

    if ! (( ${+commands[rg]} )); then
        print -ru2 -- 'rgf: rg is not available'
        return 127
    fi

    if ! (( ${+commands[fzf]} )); then
        print -ru2 -- 'rgf: fzf is not available; falling back to rg --files'
        rg --files --hidden --glob '!.git/*'
        return $?
    fi

    rg --files --hidden --glob '!.git/*' | fzf --query "$*"
}

_nag_antipatterns() {
    emulate -L zsh

    [[ "${ZSH_ANTIPATTERN_NAGS:-1}" != 0 ]] || return 0

    local cmdline="$1"
    local regex

    [[ -n "$cmdline" ]] || return 0

    regex='(^|[;&|][[:space:]]*)cat[[:space:]][^|;]+[[:space:]]*\|[[:space:]]*grep([[:space:]]|$)'
    if [[ "$cmdline" =~ $regex ]]; then
        if (( ${+commands[rg]} )); then
            print -ru2 -- 'hint: use rg PATTERN FILE instead of cat FILE | grep PATTERN'
        else
            print -ru2 -- 'hint: grep can read files directly; avoid cat FILE | grep PATTERN'
        fi
        return 0
    fi

    regex='(^|[;&|][[:space:]]*)grep[[:space:]][^|;]+[[:space:]]*\|[[:space:]]*wc[[:space:]]+-l([[:space:]]|$)'
    if [[ "$cmdline" =~ $regex ]]; then
        if (( ${+commands[rg]} )); then
            print -ru2 -- 'hint: use rg -c PATTERN FILE or grep -c PATTERN FILE instead of grep ... | wc -l'
        else
            print -ru2 -- 'hint: use grep -c PATTERN FILE instead of grep ... | wc -l'
        fi
        return 0
    fi

    regex='(^|[;&|][[:space:]]*)ps([[:space:]]+[^|;]*)?[[:space:]]*\|[[:space:]]*grep([[:space:]]|$)'
    if [[ "$cmdline" =~ $regex ]]; then
        if (( ${+commands[pgrep]} )); then
            print -ru2 -- 'hint: use pgrep -f PATTERN instead of ps ... | grep PATTERN'
        else
            print -ru2 -- 'hint: ps ... | grep PATTERN is brittle; use a process lookup tool when available'
        fi
    fi

    return 0
}

if [[ -o interactive ]]; then
    autoload -Uz add-zsh-hook
    add-zsh-hook -d preexec _nag_antipatterns 2>/dev/null || true
    add-zsh-hook preexec _nag_antipatterns
fi
