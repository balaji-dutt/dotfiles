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

_bd_primary_command() {
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

_bd_default_create_assignee() {
    local assignee="${BD_DEFAULT_CREATE_ASSIGNEE-balaji}"

    [[ -n "$assignee" ]] || return 1
    printf '%s\n' "$assignee"
}

_bd_create_args_have_assignee() {
    local primary_command_seen=0

    while [[ $# -gt 0 ]]; do
        if [[ $primary_command_seen -eq 1 ]]; then
            case "$1" in
                --)
                    return 1
                    ;;
                --assignee|-a|--assignee=*|-a=*)
                    return 0
                    ;;
            esac
            shift
            continue
        fi

        case "$1" in
            --)
                return 1
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
                case "$1" in
                    create|new)
                        primary_command_seen=1
                        shift
                        ;;
                    *)
                        return 1
                        ;;
                esac
                ;;
        esac
    done

    return 1
}

_bd_should_default_create_assignee() {
    local primary_command

    _bd_default_create_assignee >/dev/null || return 1
    _bd_arg_requests_help "$@" && return 1

    primary_command="$(_bd_primary_command "$@")" || return 1
    case "$primary_command" in
        create|new)
            ;;
        *)
            return 1
            ;;
    esac

    _bd_create_args_have_assignee "$@" && return 1
    return 0
}

_bd_should_refresh_issues_export() {
    case "$1" in
        assign|batch|close|comment|comments|create|defer|delete|dep|duplicate|edit|epic|gate|label|link|merge-slot|note|priority|promote|q|rename|reopen|set-state|supersede|tag|todo|undefer|update)
            return 0
            ;;
    esac

    return 1
}

_bd_autocommit_issues_jsonl() {
    local workdir="${1:-$PWD}"
    local repo_root issues_path issues_status conflicts
    local commit_message='chore(beads): Commit updated issues.jsonl'

    [[ "${BD_AUTO_COMMIT_ISSUES_JSONL:-0}" != 0 ]] || return 0
    [[ -z "${BD_GIT_HOOK:-}" ]] || return 0

    if ! command git -C "$workdir" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
        return 0
    fi

    repo_root="$(command git -C "$workdir" rev-parse --show-toplevel 2>/dev/null)" || return 0
    issues_path='.beads/issues.jsonl'

    if _bd_repo_is_dolt_source_of_truth "$repo_root"; then
        printf '%s\n' 'bd: warning: skipping .beads/issues.jsonl auto-commit; Dolt is source of truth for this repo' >&2
        return 0
    fi

    [[ -f "$repo_root/$issues_path" ]] || return 0

    if ! ( cd "$repo_root" && _bd_run_filtered -q export -o "$issues_path" ); then
        printf '%s\n' 'bd: warning: failed to refresh .beads/issues.jsonl for auto-commit' >&2
        return 0
    fi

    conflicts="$(command git -C "$repo_root" diff --name-only --diff-filter=U -- "$issues_path" 2>/dev/null)"
    if [[ -n "$conflicts" ]]; then
        printf '%s\n' 'bd: warning: .beads/issues.jsonl has merge conflicts; skipping auto-commit' >&2
        return 0
    fi

    issues_status="$(command git -C "$repo_root" status --porcelain -- "$issues_path" 2>/dev/null)"
    [[ -n "$issues_status" ]] || return 0

    if ! command git -C "$repo_root" add -- "$issues_path"; then
        printf '%s\n' 'bd: warning: failed to stage .beads/issues.jsonl for auto-commit' >&2
        return 0
    fi

    if ! command git -C "$repo_root" commit -m "$commit_message" -- "$issues_path"; then
        printf '%s\n' 'bd: warning: failed to auto-commit .beads/issues.jsonl' >&2
        command git -C "$repo_root" restore --staged -- "$issues_path" >/dev/null 2>&1 || true
    fi
}

bd() {
    local default_assignee exit_code inserted primary_command workdir arg sync_action
    local -a bd_args defaulted_bd_args

    if sync_action="$(_bd_dolt_sync_action "$@")"; then
        _bd_run_dolt_sync "$sync_action" "$@"
        return $?
    fi

    bd_args=("$@")
    if _bd_should_default_create_assignee "$@"; then
        default_assignee="$(_bd_default_create_assignee)" || return 1
        inserted=0
        defaulted_bd_args=()
        for arg in "${bd_args[@]}"; do
            if [[ "$arg" == -- ]] && [[ $inserted -eq 0 ]]; then
                defaulted_bd_args+=(--assignee "$default_assignee")
                inserted=1
            fi
            defaulted_bd_args+=("$arg")
        done
        if [[ $inserted -eq 0 ]]; then
            defaulted_bd_args+=(--assignee "$default_assignee")
        fi
        bd_args=("${defaulted_bd_args[@]}")
    fi

    _bd_run_filtered "${bd_args[@]}"
    exit_code=$?

    if [[ $exit_code -eq 0 ]] && ! _bd_arg_requests_help "${bd_args[@]}"; then
        primary_command="$(_bd_primary_command "${bd_args[@]}")"
        if _bd_should_refresh_issues_export "$primary_command"; then
            workdir="$(_bd_command_directory "${bd_args[@]}")"
            _bd_autocommit_issues_jsonl "$workdir"
        fi
    fi

    return $exit_code
}
