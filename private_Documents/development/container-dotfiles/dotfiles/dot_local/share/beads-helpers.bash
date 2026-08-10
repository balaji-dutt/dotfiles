# Beads shell helpers for this dotfiles repo (bash variant).
#
# Mirrors dot_local/share/beads-helpers.zsh. Shell startup may load this helper
# in interactive or configured non-interactive sessions; automation can bypass
# the wrapper with `command bd`.

_bd_filter_auto_import_noise() {
    command grep -v -E '^auto-import(ing|ed) '
}

_bd_repo_root_from_workdir() {
    local workdir="${1:-$PWD}"

    command git -C "$workdir" rev-parse --show-toplevel 2>/dev/null
}

_bd_repo_is_dolt_source_of_truth() {
    local repo_root="$1"
    local metadata config

    [[ -n "$repo_root" ]] || return 1

    metadata="$repo_root/.beads/metadata.json"
    config="$repo_root/.beads/config.yaml"

    if [[ -f "$metadata" ]] && command grep -Eq '"(backend|database)"[[:space:]]*:[[:space:]]*"dolt"|"dolt_database"[[:space:]]*:' "$metadata"; then
        return 0
    fi

    if [[ -f "$config" ]] && command grep -Eq '^[[:space:]]*export\.auto:[[:space:]]*false([[:space:]]*(#.*)?)?$' "$config"; then
        return 0
    fi

    return 1
}

_bd_should_filter_auto_import_noise() {
    local repo_root="$1"

    case "${BD_FILTER_AUTO_IMPORT_NOISE-}" in
        1|true|TRUE|yes|YES|on|ON)
            return 0
            ;;
        0|false|FALSE|no|NO|off|OFF)
            return 1
            ;;
    esac

    _bd_repo_is_dolt_source_of_truth "$repo_root" && return 1
    return 0
}

_bd_run_filtered() {
    local workdir repo_root

    workdir="$(_bd_command_directory "$@")"
    repo_root="$(_bd_repo_root_from_workdir "$workdir")"

    if _bd_should_filter_auto_import_noise "$repo_root"; then
        BD_EXPORT_GIT_ADD=false command bd "$@" \
            > >(_bd_filter_auto_import_noise) \
            2> >(_bd_filter_auto_import_noise >&2)
    else
        BD_EXPORT_GIT_ADD=false command bd "$@"
    fi
}

_bd_arg_requests_help() {
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

_bd_dolt_sync_action() {
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
            -*)
                shift
                ;;
            *)
                break
                ;;
        esac
    done

    [[ $# -ge 2 && "$1" == dolt ]] || return 1
    case "$2" in
        pull|push)
            printf '%s\n' "$2"
            ;;
        *)
            return 1
            ;;
    esac
}

_bd_run_dolt_sync() {
    local action="$1"
    shift

    local workdir repo_root sync_script arg
    local unsupported=0
    local -a original_args
    original_args=("$@")

    workdir="$(_bd_command_directory "$@")"
    repo_root="$(_bd_repo_root_from_workdir "$workdir")"
    sync_script="$repo_root/assets/beads-sync.sh"

    if [[ -z "$repo_root" || ! -f "$sync_script" ]]; then
        _bd_run_filtered "${original_args[@]}"
        return $?
    fi

    while [[ $# -gt 0 ]]; do
        case "$1" in
            --)
                shift
                break
                ;;
            -C|--directory)
                if [[ $# -gt 1 ]]; then
                    shift 2
                else
                    unsupported=1
                    break
                fi
                ;;
            -C=*|--directory=*)
                shift
                ;;
            --db|--actor|--dolt-auto-commit)
                unsupported=1
                [[ $# -gt 1 ]] || break
                shift 2
                ;;
            *)
                [[ "$1" == dolt ]] && break
                unsupported=1
                shift
                ;;
        esac
    done

    [[ $# -ge 2 && "$1" == dolt && "$2" == "$action" ]] || unsupported=1
    if [[ $# -gt 2 ]]; then
        unsupported=1
    fi

    if [[ $unsupported -ne 0 ]]; then
        printf 'bd: refusing redirected `bd dolt %s` with unsupported arguments; run `%s %s` explicitly\n' \
            "$action" "$sync_script" "$action" >&2
        return 2
    fi

    printf 'bd: redirecting `bd dolt %s` to `%s %s`\n' \
        "$action" "$sync_script" "$action" >&2
    (cd "$repo_root" && "$sync_script" "$action")
}

# Intentionally over-approximate mixed command families: an extra throttled
# snapshot is safer than missing a write when bd adds a mutating code path.
_bd_mutation_requested() {
    local primary subcommand arg

    while [[ $# -gt 0 ]]; do
        case "$1" in
            --) shift; break ;;
            -q|--quiet|-v|--verbose|--json|--profile|--readonly|--sandbox|--global) shift ;;
            -C|--directory|--db|--actor|--dolt-auto-commit)
                [[ $# -gt 1 ]] || return 1
                shift 2
                ;;
            -C=*|--directory=*|--db=*|--actor=*|--dolt-auto-commit=*) shift ;;
            -h|--help|--version|-V) return 1 ;;
            -*) shift ;;
            *) break ;;
        esac
    done
    [[ $# -gt 0 ]] || return 1
    primary="$1"
    shift

    case "$primary" in
        assign|batch|close|comment|create|new|create-form|defer|delete|duplicate|edit|forget|import|link|note|priority|promote|q|remember|rename|reopen|set-state|supersede|tag|undefer|update)
            return 0
            ;;
        comments)
            [[ "${1:-}" == add ]]
            return
            ;;
        dep)
            subcommand="${1:-}"
            [[ "$subcommand" == add || "$subcommand" == remove || "$subcommand" == relate || "$subcommand" == unrelate ]]
            return
            ;;
        label)
            subcommand="${1:-}"
            [[ "$subcommand" == add || "$subcommand" == remove || "$subcommand" == propagate ]]
            return
            ;;
        epic)
            [[ "${1:-}" == close-eligible ]]
            return
            ;;
        gate)
            subcommand="${1:-}"
            [[ "$subcommand" == add-waiter || "$subcommand" == check || "$subcommand" == create || "$subcommand" == resolve ]]
            return
            ;;
        merge-slot)
            subcommand="${1:-}"
            [[ "$subcommand" == acquire || "$subcommand" == create || "$subcommand" == release ]]
            return
            ;;
        todo)
            subcommand="${1:-}"
            [[ "$subcommand" == add || "$subcommand" == done ]]
            return
            ;;
        restore)
            for arg in "$@"; do
                [[ "$arg" == --apply ]] && return 0
            done
            ;;
    esac
    return 1
}

_bd_snapshot_if_due() {
    local workdir="$1" repo_root metadata sync_script

    [[ -z "${BD_GIT_HOOK:-}" ]] || return 0
    case "${BD_AUTO_SNAPSHOT:-1}" in
        0|false|FALSE|no|NO|off|OFF) return 0 ;;
    esac
    repo_root="$(_bd_repo_root_from_workdir "$workdir")"
    [[ -n "$repo_root" ]] || return 0
    metadata="$repo_root/.beads/metadata.json"
    sync_script="$repo_root/assets/beads-sync.sh"
    [[ -f "$metadata" && -x "$sync_script" ]] || return 0
    command grep -Eq '"dolt_database"[[:space:]]*:[[:space:]]*"dots"' "$metadata" || return 0
    if ! (cd "$repo_root" && "$sync_script" snapshot --if-due); then
        printf '%s\n' 'bd: warning: automatic Beads JSONL snapshot failed' >&2
    fi
}

bd() {
    local exit_code workdir sync_action
    local -a bd_args

    if sync_action="$(_bd_dolt_sync_action "$@")"; then
        _bd_run_dolt_sync "$sync_action" "$@"
        return $?
    fi

    bd_args=("$@")
    _bd_run_filtered "${bd_args[@]}"
    exit_code=$?

    if [[ $exit_code -eq 0 ]] && ! _bd_arg_requests_help "${bd_args[@]}" && _bd_mutation_requested "${bd_args[@]}"; then
        workdir="$(_bd_command_directory "${bd_args[@]}")"
        _bd_snapshot_if_due "$workdir"
    fi

    return $exit_code
}
