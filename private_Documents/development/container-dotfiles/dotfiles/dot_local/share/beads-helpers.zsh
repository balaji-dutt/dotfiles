# Beads shell helpers for this dotfiles repo.

_bd_filter_auto_import_noise() {
    emulate -L zsh

    command grep -v -E '^auto-import(ing|ed) '
}

_bd_run_filtered() {
    emulate -L zsh

    BD_EXPORT_GIT_ADD=false command bd "$@" \
        > >(_bd_filter_auto_import_noise) \
        2> >(_bd_filter_auto_import_noise >&2)
}

_bd_arg_requests_help() {
    emulate -L zsh

    local arg

    for arg in "$@"; do
        case "$arg" in
            -h|--help|--version|-V)
                return 0
                ;;
        esac
    done

    return 1
}

_bd_command_directory() {
    emulate -L zsh

    local dir="$PWD"

    while [[ $# -gt 0 ]]; do
        case "$1" in
            --)
                break
                ;;
            -C|--directory)
                if [[ $# -gt 1 ]]; then
                    dir="$2"
                    shift 2
                    continue
                fi
                break
                ;;
            -C=*|--directory=*)
                dir="${1#*=}"
                shift
                continue
                ;;
            --db|--actor|--dolt-auto-commit)
                if [[ $# -gt 1 ]]; then
                    shift 2
                    continue
                fi
                break
                ;;
            --db=*|--actor=*|--dolt-auto-commit=*)
                shift
                continue
                ;;
            -*)
                shift
                continue
                ;;
            *)
                break
                ;;
        esac
    done

    printf '%s\n' "$dir"
}

_bd_primary_command() {
    emulate -L zsh

    while [[ $# -gt 0 ]]; do
        case "$1" in
            --)
                shift
                break
                ;;
            -q|--quiet|-v|--verbose|--json|--profile|--readonly|--sandbox|--global)
                shift
                ;;
            -C|--directory|--db|--actor|--dolt-auto-commit)
                [[ $# -gt 1 ]] || return 1
                shift 2
                ;;
            -C=*|--directory=*|--db=*|--actor=*|--dolt-auto-commit=*)
                shift
                ;;
            -h|--help|--version|-V)
                return 1
                ;;
            -*)
                shift
                ;;
            *)
                printf '%s\n' "$1"
                return 0
                ;;
        esac
    done

    return 1
}

_bd_should_refresh_issues_export() {
    emulate -L zsh

    case "$1" in
        assign|batch|close|comment|comments|create|defer|delete|dep|duplicate|edit|epic|gate|label|link|merge-slot|note|priority|promote|q|rename|reopen|set-state|supersede|tag|todo|undefer|update)
            return 0
            ;;
    esac

    return 1
}

_bd_autocommit_issues_jsonl() {
    emulate -L zsh

    local workdir="${1:-$PWD}"
    local repo_root issues_path issues_status conflicts
    local commit_message='chore(beads): Commit updated issues.jsonl'

    [[ "${BD_AUTO_COMMIT_ISSUES_JSONL:-1}" != 0 ]] || return 0
    [[ -z "${BD_GIT_HOOK:-}" ]] || return 0

    if ! command git -C "$workdir" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
        return 0
    fi

    repo_root="$(command git -C "$workdir" rev-parse --show-toplevel 2>/dev/null)" || return 0
    issues_path='.beads/issues.jsonl'

    [[ -f "$repo_root/$issues_path" ]] || return 0

    if ! ( cd "$repo_root" && _bd_run_filtered -q export -o "$issues_path" ); then
        print -u2 -- 'bd: warning: failed to refresh .beads/issues.jsonl for auto-commit'
        return 0
    fi

    conflicts="$(command git -C "$repo_root" diff --name-only --diff-filter=U -- "$issues_path" 2>/dev/null)"
    if [[ -n "$conflicts" ]]; then
        print -u2 -- 'bd: warning: .beads/issues.jsonl has merge conflicts; skipping auto-commit'
        return 0
    fi

    issues_status="$(command git -C "$repo_root" status --porcelain -- "$issues_path" 2>/dev/null)"
    [[ -n "$issues_status" ]] || return 0

    if ! command git -C "$repo_root" add -- "$issues_path"; then
        print -u2 -- 'bd: warning: failed to stage .beads/issues.jsonl for auto-commit'
        return 0
    fi

    if ! command git -C "$repo_root" commit -m "$commit_message" -- "$issues_path"; then
        print -u2 -- 'bd: warning: failed to auto-commit .beads/issues.jsonl'
        command git -C "$repo_root" restore --staged -- "$issues_path" >/dev/null 2>&1 || true
    fi
}

bd() {
    emulate -L zsh

    local exit_code primary_command workdir

    _bd_run_filtered "$@"
    exit_code=$?

    if [[ $exit_code -eq 0 ]] && ! _bd_arg_requests_help "$@"; then
        primary_command="$(_bd_primary_command "$@")"
        if _bd_should_refresh_issues_export "$primary_command"; then
            workdir="$(_bd_command_directory "$@")"
            _bd_autocommit_issues_jsonl "$workdir"
        fi
    fi

    return $exit_code
}
