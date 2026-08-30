from __future__ import annotations

import os
import shutil
import stat
import subprocess
import unittest
from pathlib import Path

from tests.support.fixtures import isolated_environment, init_git_repository, run_git


REPO_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_DIR = (
    REPO_ROOT
    / "private_Documents/development/container-dotfiles/devcontainers"
    / "gitlab.com/servers-homelab/homelab-IaC/dot_devcontainer"
)
COMMON = RUNTIME_DIR / "devcontainer-common.sh"
POST_CREATE = RUNTIME_DIR / "postCreate.sh"
POST_START = RUNTIME_DIR / "postStart.sh"
BASH = shutil.which("bash")


@unittest.skipUnless(os.name != "nt" and BASH, "POSIX bash is required")
class DevcontainerLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.environment_context = isolated_environment(prefix="devcontainer-lifecycle-")
        self.fixture = self.environment_context.__enter__()
        self.addCleanup(self.environment_context.__exit__, None, None, None)
        self.env = dict(self.fixture.env)

    def run_bash(
        self,
        script: str,
        *args: str,
        env: dict[str, str] | None = None,
        cwd: Path | None = None,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [BASH or "bash", "-c", script, "bash", *args],
            cwd=cwd or self.fixture.root,
            env=self.env if env is None else env,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    def assert_success(self, result: subprocess.CompletedProcess[str]) -> None:
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_lifecycle_scripts_are_source_safe_and_dispatchable(self) -> None:
        cases = (
            (POST_CREATE, "post_create", "postCreate.log"),
            (POST_START, "post_start", "postStart.log"),
        )
        for source, prefix, log_name in cases:
            with self.subTest(source=source.name):
                log_path = self.fixture.root / log_name
                env = dict(self.env)
                env["LOG_FILE"] = str(log_path)
                result = self.run_bash(
                    r'''
before="$-"
source "$1"
after="$-"
[[ "$before" == "$after" ]] || exit 10
declare -F "${2}_main" "${2}_dispatch" >/dev/null || exit 11
eval "${2}_main() { printf '%s\\n' \"safe-dispatch:\$1\"; return 23; }"
set +e
"${2}_dispatch" "path with spaces"
rc=$?
set -e
[[ "$rc" -eq 23 ]] || exit 12
''',
                    str(source),
                    prefix,
                    env=env,
                )
                self.assert_success(result)
                self.assertIn("safe-dispatch:path with spaces", result.stdout)
                self.assertFalse(log_path.exists())

    def test_lifecycle_scripts_reject_a_failed_common_helper_source(self) -> None:
        for source in (POST_CREATE, POST_START):
            with self.subTest(source=source.name):
                runtime = self.fixture.root / source.stem
                runtime.mkdir()
                copied = runtime / source.name
                shutil.copy2(source, copied)
                (runtime / "devcontainer-common.sh").write_text(
                    "return 31\n", encoding="utf-8"
                )
                sourced = self.run_bash(
                    'set +e; source "$1"; rc=$?; [[ "$rc" -eq 1 ]]', str(copied)
                )
                self.assert_success(sourced)
                self.assertIn("Failed to source shared devcontainer helper", sourced.stderr)
                direct = subprocess.run(
                    [BASH or "bash", str(copied)],
                    cwd=self.fixture.root,
                    env=self.env,
                    check=False,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                self.assertEqual(direct.returncode, 1, direct.stdout + direct.stderr)
                self.assertIn("Failed to source shared devcontainer helper", direct.stderr)

    def test_load_opencode_env_preserves_allexport_state(self) -> None:
        config_dir = self.fixture.home / ".config" / "opencode"
        config_dir.mkdir(parents=True)
        (config_dir / "opencode.env").write_text(
            "LIFECYCLE_EXPORTED=present\n", encoding="utf-8"
        )
        for initial, expected in (("set +a", "off"), ("set -a", "on")):
            with self.subTest(initial=expected):
                result = self.run_bash(
                    f'''
{initial}
source "$1"
load_opencode_env_file
case $- in *a*) state=on ;; *) state=off ;; esac
printf '%s:%s:%s\n' "$state" "$LIFECYCLE_EXPORTED" "$(printenv LIFECYCLE_EXPORTED)"
''',
                    str(COMMON),
                )
                self.assert_success(result)
                self.assertEqual(result.stdout.strip(), f"{expected}:present:present")

    def test_git_safe_directories_are_filtered_sorted_and_idempotent(self) -> None:
        workspace = self.fixture.root / "workspace with spaces"
        workspace.mkdir()
        safe_file = self.fixture.root / "persistent data" / "git" / "safe-dirs"
        safe_file.parent.mkdir(parents=True)
        subprocess.run(
            ["git", "config", "--file", str(safe_file), "--add", "safe.directory", "*"],
            check=True,
            env=self.env,
        )
        subprocess.run(
            [
                "git",
                "config",
                "--file",
                str(safe_file),
                "--add",
                "safe.directory",
                "/existing/path",
            ],
            check=True,
            env=self.env,
        )
        result = self.run_bash(
            'source "$1"; ensure_git_safe_directories "$2/" "$3"; cp "$3" "$4"; ensure_git_safe_directories "$2" "$3"; cmp -s "$3" "$4"',
            str(COMMON),
            str(workspace),
            str(safe_file),
            str(self.fixture.root / "first-safe-dirs"),
        )
        self.assert_success(result)
        values = subprocess.run(
            ["git", "config", "--file", str(safe_file), "--get-all", "safe.directory"],
            check=True,
            env=self.env,
            text=True,
            stdout=subprocess.PIPE,
        ).stdout.splitlines()
        self.assertEqual(
            values,
            sorted(
                {
                    "/existing/path",
                    str(workspace),
                    str(workspace / ".git"),
                    str(workspace / "worktrees" / "*"),
                }
            ),
        )

    def test_agent_of_empires_state_and_link_survive_repeated_runs(self) -> None:
        persist = self.fixture.root / "persistent data" / "agent-of-empires"
        config = self.fixture.home / ".config" / "agent-of-empires"
        config.mkdir(parents=True)
        (config / "config.toml").write_text("theme = 'dark'\n", encoding="utf-8")
        result = self.run_bash(
            'source "$1"; ensure_agent_of_empires_persistence_link "$2" "$3"; printf %s preserved >"$2/state.toml"; ensure_agent_of_empires_persistence_link "$2" "$3"',
            str(COMMON),
            str(persist),
            str(config),
        )
        self.assert_success(result)
        self.assertTrue(config.is_symlink())
        self.assertEqual(os.readlink(config), str(persist))
        self.assertEqual((persist / "config.toml").read_text(encoding="utf-8"), "theme = 'dark'\n")
        self.assertEqual((persist / "state.toml").read_text(encoding="utf-8"), "preserved")
        self.assertEqual(stat.S_IMODE((persist / "state.toml").stat().st_mode), 0o600)

    def test_opencode_links_migrate_partial_state_and_stale_nested_link(self) -> None:
        persistent = self.fixture.root / "persistent data" / "opencode"
        config_home = self.fixture.root / "xdg config"
        cache_home = self.fixture.root / "xdg cache"
        data_home = self.fixture.root / "xdg data"
        state_home = self.fixture.root / "xdg state"
        old_config = config_home / "opencode"
        old_config.mkdir(parents=True)
        (old_config / "legacy.json").write_text("legacy\n", encoding="utf-8")
        (persistent / "config").mkdir(parents=True)
        (persistent / "config" / "kept.json").write_text("kept\n", encoding="utf-8")
        os.symlink(persistent / "config", old_config / "config")
        (cache_home / "opencode").parent.mkdir(parents=True)
        (cache_home / "opencode").write_text("conflict\n", encoding="utf-8")

        result = self.run_bash(
            'source "$1"; ensure_opencode_persistence_links "$2" "$3" "$4" "$5" "$6"; ensure_opencode_persistence_links "$2" "$3" "$4" "$5" "$6"',
            str(COMMON),
            str(persistent),
            str(config_home),
            str(cache_home),
            str(data_home),
            str(state_home),
        )
        self.assert_success(result)
        expected_links = {
            config_home / "opencode": persistent / "config",
            cache_home / "opencode": persistent / "cache",
            data_home / "opencode": persistent / "share",
            state_home / "opencode": persistent / "state",
        }
        for link, target in expected_links.items():
            self.assertTrue(link.is_symlink(), link)
            self.assertEqual(os.readlink(link), str(target))
        self.assertEqual((persistent / "config" / "legacy.json").read_text(), "legacy\n")
        self.assertEqual((persistent / "config" / "kept.json").read_text(), "kept\n")
        self.assertFalse((persistent / "config" / "config").exists())

    def test_claude_links_migrate_config_and_home_file_idempotently(self) -> None:
        persistent = self.fixture.root / "persistent data" / "claude"
        config = self.fixture.home / ".claude"
        home_config = self.fixture.home / ".claude.json"
        config.mkdir()
        (config / "settings.json").write_text("{}\n", encoding="utf-8")
        home_config.write_text('{"theme":"dark"}\n', encoding="utf-8")
        result = self.run_bash(
            'source "$1"; ensure_claude_persistence_links "$2" "$3" "$4"; ensure_claude_persistence_links "$2" "$3" "$4"',
            str(COMMON),
            str(persistent),
            str(config),
            str(home_config),
        )
        self.assert_success(result)
        self.assertTrue(config.is_symlink())
        self.assertEqual(os.readlink(config), str(persistent / "config"))
        self.assertTrue(home_config.is_symlink())
        self.assertEqual(os.readlink(home_config), str(persistent / ".claude.json"))
        self.assertEqual((persistent / "config" / "settings.json").read_text(), "{}\n")

    def test_opencode_materialization_handles_conflicts_modes_stale_and_symlinks(self) -> None:
        source = self.fixture.root / "managed source"
        target = self.fixture.root / "managed target"
        state_dir = self.fixture.root / "managed state"
        backup = self.fixture.root / "backups"
        (source / "agents").mkdir(parents=True)
        (source / "AGENTS.md").write_text("managed\n", encoding="utf-8")
        executable = source / "agents" / "worker.sh"
        executable.write_text("#!/bin/sh\n", encoding="utf-8")
        executable.chmod(0o755)
        target.mkdir()
        (target / "AGENTS.md").mkdir()
        (target / "AGENTS.md" / "unmanaged").write_text("keep\n", encoding="utf-8")
        env = dict(self.env)
        env["OPENCODE_MANAGED_BACKUP_ROOT"] = str(backup)
        first = self.run_bash(
            'source "$1"; materialize_opencode_managed_assets_from "$2" "$3" "$4"',
            str(COMMON),
            str(source),
            str(target),
            str(state_dir),
            env=env,
        )
        self.assert_success(first)
        self.assertEqual((target / "AGENTS.md").read_text(), "managed\n")
        self.assertTrue(os.access(target / "agents" / "worker.sh", os.X_OK))
        self.assertEqual(len(list(backup.rglob("unmanaged"))), 1)

        executable.unlink()
        (source / "agents" / "replacement.md").write_text("replacement\n", encoding="utf-8")
        second = self.run_bash(
            'source "$1"; materialize_opencode_managed_assets_from "$2" "$3" "$4"',
            str(COMMON),
            str(source),
            str(target),
            str(state_dir),
            env=env,
        )
        self.assert_success(second)
        self.assertFalse((target / "agents" / "worker.sh").exists())
        self.assertTrue((target / "agents" / "replacement.md").is_file())
        os.symlink(source / "AGENTS.md", source / "tui.json")
        rejected = self.run_bash(
            'source "$1"; materialize_opencode_managed_assets_from "$2" "$3" "$4"',
            str(COMMON),
            str(source),
            str(target),
            str(state_dir),
            env=env,
        )
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn("source symlinks are not supported", rejected.stderr)

    def test_claude_managed_directory_decodes_and_materializes_modes(self) -> None:
        source = self.fixture.root / "claude source"
        target = self.fixture.root / "claude target"
        source.mkdir()
        executable = source / "executable_hook.sh"
        executable.write_text("#!/bin/sh\n", encoding="utf-8")
        normal = source / "dot_config.json"
        normal.write_text("{}\n", encoding="utf-8")
        result = self.run_bash(
            'source "$1"; materialize_claude_managed_dir "$2" "$3"; materialize_claude_managed_dir "$2" "$3"',
            str(COMMON),
            str(source),
            str(target),
        )
        self.assert_success(result)
        self.assertTrue((target / "hook.sh").is_file())
        self.assertFalse((target / "hook.sh").is_symlink())
        self.assertEqual(stat.S_IMODE((target / "hook.sh").stat().st_mode), 0o755)
        self.assertTrue((target / ".config.json").is_symlink())
        self.assertEqual(os.readlink(target / ".config.json"), str(normal))

    def test_post_create_helpers_handle_git_env_profiles_and_mnemo(self) -> None:
        repository = init_git_repository(self.fixture.root / "seed repository", env=self.env)
        (repository / "tracked.txt").write_text("tracked\n", encoding="utf-8")
        run_git(repository, "add", "tracked.txt", env=self.env)
        run_git(repository, "commit", "-m", "seed", env=self.env)
        workspace = self.fixture.root / "workspace with spaces"
        workspace.mkdir()
        env_source = self.fixture.root / "opencode source.env"
        env_target = self.fixture.root / "persistent data" / "opencode.env"
        env_source.write_text("TOKEN=synthetic\nOPENCODE_PROFILE=new\n", encoding="utf-8")
        env_target.parent.mkdir(parents=True)
        env_target.write_text(
            'OLD=1\nOPENCODE_PROFILES="defaults work"\nOPENCODE_PROFILE=defaults\n',
            encoding="utf-8",
        )
        mnemo_target = self.fixture.root / "persistent data" / "mnemo"
        mnemo_link = self.fixture.home / ".mnemo"
        mnemo_target.mkdir(parents=True)
        os.symlink(mnemo_target, mnemo_link)
        result = self.run_bash(
            '''
source "$1"
bootstrap_local_git_metadata "$2" "$3"
install_opencode_env_file "$4" "$5"
retire_mnemo_persistence_link "$6" "$7"
retire_mnemo_persistence_link "$6" "$7"
[[ "$(trim_whitespace '  value  ')" == value ]]
[[ "$(npm_package_name_from_spec '@scope/pkg@1.2.3')" == '@scope/pkg' ]]
''',
            str(POST_CREATE),
            str(workspace),
            str(repository / ".git"),
            str(env_source),
            str(env_target),
            str(mnemo_link),
            str(mnemo_target),
        )
        self.assert_success(result)
        self.assertTrue((workspace / ".git" / "HEAD").is_file())
        self.assertIn('OPENCODE_PROFILES="defaults work"', env_target.read_text())
        self.assertIn("OPENCODE_PROFILE=defaults", env_target.read_text())
        self.assertNotIn("OPENCODE_PROFILE=new", env_target.read_text())
        self.assertEqual(stat.S_IMODE(env_target.stat().st_mode), 0o600)
        self.assertFalse(mnemo_link.exists())

    def test_post_start_profile_helpers_validate_preserve_and_replace(self) -> None:
        env_file = self.fixture.root / "persistent env" / "opencode.env"
        env_file.parent.mkdir(parents=True)
        env_file.write_text(
            'TOKEN=synthetic\nexport OPENCODE_PROFILES="chatgpt work work"\nOPENCODE_PROFILE=old\n',
            encoding="utf-8",
        )
        result = self.run_bash(
            '''
source "$1"
raw="$(read_opencode_profiles_from_env_file "$2")"
[[ "$raw" == 'chatgpt work work' ]]
normalized="$(normalize_opencode_profiles "$raw")"
[[ "$normalized" == 'defaults work' ]]
write_opencode_profiles_to_env_file "$2" "$normalized"
if normalize_opencode_profiles '../unsafe' >/dev/null 2>&1; then exit 20; fi
''',
            str(POST_START),
            str(env_file),
        )
        self.assert_success(result)
        self.assertEqual(
            env_file.read_text(encoding="utf-8"),
            'TOKEN=synthetic\nOPENCODE_PROFILES="defaults work"\nOPENCODE_PROFILE=defaults\n',
        )
        self.assertEqual(stat.S_IMODE(env_file.stat().st_mode), 0o600)

    def test_invalid_lifecycle_inputs_fail_without_host_changes(self) -> None:
        missing = self.fixture.root / "missing workspace"
        result = self.run_bash(
            'source "$1"; ensure_beads_persistence_mounts "$2"',
            str(POST_START),
            str(missing),
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Workspace path does not exist", result.stderr)
        self.assertFalse(missing.exists())


if __name__ == "__main__":
    unittest.main()
