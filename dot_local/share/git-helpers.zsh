# ==========================================
# Git helper: pull --rebase with stash preflight
# ==========================================

git_pull_rebase_then_apply_stash() {
    emulate -L zsh
    setopt pipefail

    local debug=false
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --debug)
                debug=true
                shift
                ;;
            -*)
                echo "gpls: unknown option: $1" >&2
                return 1
                ;;
            *)
                echo "gpls: unexpected argument: $1" >&2
                return 1
                ;;
        esac
    done

    if [[ $debug == true ]]; then
        local -x GIT_TRACE=1
        local -x GIT_TRACE_SETUP=1
    fi

    local stash_message="autostash-before-pull"
    local stash_ref="stash@{0}"
    local before after
    local tmpdir=""
    local preflight_log=""
    local repo_root=""
    local git_common_dir=""
    local guarded_sync=false
    local guarded_sync_helper=""
    local pull_exit=0


    if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
        echo "Not inside a git repository."
        return 1
    fi

    if ! repo_root="$(git rev-parse --show-toplevel 2>/dev/null)"; then
        echo "Failed to determine repository root."
        return 1
    fi

    git_common_dir="$(git rev-parse --path-format=absolute --git-common-dir 2>/dev/null || true)"
    guarded_sync_helper="$repo_root/assets/guarded-main-sync"

    _gpls_pull_rebase() {
        local stash_oid="${1:-}"
        local sync_exit
        local -a sync_args

        guarded_sync=false
        if [[ ! -x "$guarded_sync_helper" ]]; then
            git pull --rebase
            return $?
        fi

        sync_args=(sync)
        if [[ -n "$stash_oid" ]]; then
            sync_args+=(--stash-oid "$stash_oid")
        fi
        "$guarded_sync_helper" "${sync_args[@]}"
        sync_exit=$?
        if [[ $sync_exit -eq 20 ]]; then
            git pull --rebase
            return $?
        fi
        if [[ $sync_exit -ne 0 ]]; then
            return $sync_exit
        fi
        guarded_sync=true
        return 0
    }

    _gpls_finalize_guarded_sync() {
        if [[ $guarded_sync != true ]]; then
            return 0
        fi
        "$guarded_sync_helper" finalize
    }

    _gpls_stash_ref_for_oid() {
        local stash_oid="$1"
        git stash list --format='%gd %H' |
            awk -v oid="$stash_oid" '$2 == oid { print $1; exit }'
    }

    _gpls_temp_git() {
        if [[ -n "$git_common_dir" ]]; then
            git -c "safe.directory=$repo_root" -c "safe.directory=$tmpdir" -c "safe.directory=$git_common_dir" "$@"
        else
            git -c "safe.directory=$repo_root" -c "safe.directory=$tmpdir" "$@"
        fi
    }

    # If clean, just pull.
    if [[ -z "$(git status --porcelain)" ]]; then
        _gpls_pull_rebase
        pull_exit=$?
        if [[ $pull_exit -ne 0 ]]; then
            return $pull_exit
        fi
        _gpls_finalize_guarded_sync
        return $?
    fi

    print -r -- "[gpls $(date '+%H:%M:%S')] dirty worktree detected; stashing before pull" >&2

    before=$(git rev-parse -q --verify stash@{0} 2>/dev/null || true)

    if [[ $debug == true ]]; then
        git stash push -u -m "$stash_message"
    else
        git stash push -u -m "$stash_message" >/dev/null
    fi
    if [[ $? -ne 0 ]]; then
        echo "Failed to create stash."
        return 1
    fi

    # Safety: if we're still dirty after stashing, don't proceed.
    if [[ -n "$(git status --porcelain)" ]]; then
        echo "Working tree still dirty after stashing; aborting."
        return 1
    fi

    after=$(git rev-parse -q --verify stash@{0} 2>/dev/null || true)
    if [[ -z "$after" || "$after" == "$before" ]]; then
        # Unexpected, but safe: proceed without stash logic.
        _gpls_pull_rebase
        pull_exit=$?
        if [[ $pull_exit -ne 0 ]]; then
            return $pull_exit
        fi
        _gpls_finalize_guarded_sync
        return $?
    fi
    stash_ref="$after"

    print -r -- "[gpls $(date '+%H:%M:%S')] git pull --rebase" >&2
    _gpls_pull_rebase "$after"
    pull_exit=$?
    if [[ $pull_exit -ne 0 ]]; then
        echo "pull/reconciliation failed; stash left intact: $stash_ref"
        return $pull_exit
    fi

    tmpdir=$(mktemp -d 2>/dev/null || mktemp -d -t gpls)
    preflight_log=$(mktemp 2>/dev/null || mktemp -t gpls)

    _gpls_cleanup() {
        # Best-effort cleanup.
        if [[ -n "$tmpdir" ]]; then
            _gpls_temp_git worktree remove --force "$tmpdir" >/dev/null 2>&1 || true
            rm -rf "$tmpdir" >/dev/null 2>&1 || true
        fi
        if [[ -n "$preflight_log" ]]; then
            rm -f "$preflight_log" >/dev/null 2>&1 || true
        fi
    }
    trap _gpls_cleanup EXIT INT TERM

    print -r -- "[gpls $(date '+%H:%M:%S')] creating preflight worktree" >&2
    if [[ $debug == true ]]; then
        git worktree add --detach "$tmpdir" HEAD
    else
        git worktree add --detach "$tmpdir" HEAD >/dev/null 2>&1
    fi
    if [[ $? -ne 0 ]]; then
        echo "Failed to create temporary worktree for preflight."
        return 1
    fi

    if [[ $debug == true ]]; then
        print -r -- "[gpls $(date '+%H:%M:%S')] preflight stash apply" >&2
    fi
    ( cd "$tmpdir" && _gpls_temp_git stash apply --index "$stash_ref" ) >"$preflight_log" 2>&1
    local preflight_exit=$?

    local conflicts
    conflicts=$(cd "$tmpdir" && _gpls_temp_git diff --name-only --diff-filter=U 2>>"$preflight_log")
    local diff_exit=$?

    local parsed_conflicts
    parsed_conflicts=$(awk '
      /CONFLICT .* in /{ sub(/^.* in /, ""); print; next }
      /patch failed:/{ sub(/^.*patch failed: /, ""); sub(/:[0-9]+$/, ""); print; next }
      /already exists, no checkout/{ print $1; next }
      /would be overwritten by merge:/{ mode=1; next }
      mode==1 {
        if ($0 ~ /^[[:space:]]+[[:graph:]]/) { gsub(/^[[:space:]]+/, ""); print; next }
        if ($0 ~ /^Please /) { mode=0 }
      }
    ' "$preflight_log" | LC_ALL=C sort -u)

    conflicts=$( { echo "$conflicts"; echo "$parsed_conflicts"; } | sed '/^$/d' | LC_ALL=C sort -u )

    if [[ -n "$conflicts" || $preflight_exit -ne 0 || $diff_exit -ne 0 ]]; then
        echo "Stash apply would conflict; leaving stash intact: $stash_ref"
        if [[ -n "$conflicts" ]]; then
            echo "Conflicted files:"
            echo "$conflicts" | sed 's/^/  - /'
            if [[ $debug == true ]]; then
                echo "Preflight output (first 300 lines):" >&2
                sed -n '1,300p' "$preflight_log" >&2
            fi
        elif [[ $diff_exit -ne 0 ]]; then
            echo "Could not determine conflicted files (git diff failed). Output:"
            if [[ $debug == true ]]; then
                sed -n '1,300p' "$preflight_log" >&2
            else
                sed -n '1,120p' "$preflight_log"
            fi
        else
            echo "Could not determine conflicted files. Output:"
            if [[ $debug == true ]]; then
                sed -n '1,300p' "$preflight_log" >&2
            else
                sed -n '1,120p' "$preflight_log"
            fi
        fi
        return 1
    fi

    local apply_log
    apply_log=$(mktemp 2>/dev/null || mktemp -t gpls)

    print -r -- "[gpls $(date '+%H:%M:%S')] applying stash to working tree" >&2
    git stash apply --index "$stash_ref" >"$apply_log" 2>&1
    local apply_exit=$?

    if [[ $apply_exit -ne 0 ]]; then
        echo "git stash apply failed; stash left intact: $stash_ref"

        local real_conflicts
        real_conflicts=$(git diff --name-only --diff-filter=U)

        if [[ -n "$real_conflicts" ]]; then
            echo "Conflicted files:"
            echo "$real_conflicts" | sed 's/^/  - /'
            if [[ $debug == true ]]; then
                echo "Apply output (first 300 lines):" >&2
                sed -n '1,300p' "$apply_log" >&2
            fi
        else
            echo "Output:"
            if [[ $debug == true ]]; then
                sed -n '1,300p' "$apply_log" >&2
            else
                sed -n '1,120p' "$apply_log"
            fi
        fi

        rm -f "$apply_log" >/dev/null 2>&1 || true
        return $apply_exit
    fi

    rm -f "$apply_log" >/dev/null 2>&1 || true

    if [[ $debug == true ]]; then
        print -r -- "[gpls $(date '+%H:%M:%S')] dropping stash" >&2
    fi
    stash_ref="$(_gpls_stash_ref_for_oid "$after")"
    if [[ -z "$stash_ref" ]]; then
        echo "Applied stash but could not locate it for exact drop: $after"
        return 1
    fi
    git stash drop "$stash_ref" >/dev/null 2>&1
    local drop_exit=$?

    if [[ $drop_exit -ne 0 ]]; then
        echo "Applied stash but failed to drop it: $stash_ref"
        return $drop_exit
    fi

    if ! _gpls_finalize_guarded_sync; then
        echo "Stash restored, but guarded main sync finalization failed; recovery state was retained."
        return 1
    fi

    print -r -- "[gpls $(date '+%H:%M:%S')] done" >&2
    return 0
}

alias gpls='git_pull_rebase_then_apply_stash'
