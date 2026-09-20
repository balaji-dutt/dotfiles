from __future__ import annotations

import os
import re
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
DEVCONTAINER_CONFIG = RUNTIME_DIR / "devcontainer.json.tmpl"
PROMPTFOO_SOURCE = RUNTIME_DIR.parent / "configs/promptfoo-runtime"
BASH = shutil.which("bash")
UNSLOP_SKILLS = (
    "unslop", "unslop-commit", "unslop-file", "unslop-file-voice",
    "unslop-help", "unslop-reasoning", "unslop-review",
)
CONTAINER_DOTFILES = REPO_ROOT / "private_Documents/development/container-dotfiles/dotfiles"


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

    def test_claude_installer_provisions_skills_and_preserves_local_content(self) -> None:
        for index, root in enumerate((REPO_ROOT, CONTAINER_DOTFILES)):
            with self.subTest(source=root):
                source = self.fixture.root / f"claude source {index}"
                home = self.fixture.root / f"claude home {index}"
                target = home / ".claude" / "skills"
                backup = self.fixture.root / f"claude backups {index}"
                for name in UNSLOP_SKILLS:
                    shutil.copytree(root / "dot_claude/skills" / name, source / "skills" / name)
                (target / "local-only").mkdir(parents=True)
                (target / "local-only/SKILL.md").write_text("local\n", encoding="utf-8")
                (target / "unslop-commit").mkdir()
                (target / "unslop-commit/SKILL.md").write_text("conflict\n", encoding="utf-8")
                env = dict(self.env, HOME=str(home), CLAUDE_MANAGED_BACKUP_ROOT=str(backup))
                script = '''
set -e
source "$1"
fixture_source="$2"
claude_managed_source_dir() { printf '%s\\n' "$fixture_source"; }
install_claude_managed_asset_links
install_claude_managed_asset_links
'''
                self.assert_success(self.run_bash(script, str(COMMON), str(source), env=env))
                self.assertFalse(target.is_symlink())
                for name in UNSLOP_SKILLS:
                    self.assertTrue((target / name).is_symlink())
                    self.assertEqual((target / name / "SKILL.md").read_bytes(),
                                     (source / "skills" / name / "SKILL.md").read_bytes())
                nested = Path("unslop-file/scripts/cli.py")
                self.assertEqual((target / nested).read_bytes(), (source / "skills" / nested).read_bytes())
                (source / "skills/unslop-commit/SKILL.md").write_text("updated\n", encoding="utf-8")
                self.assertEqual((target / "unslop-commit/SKILL.md").read_text(), "updated\n")
                self.assertEqual((target / "local-only/SKILL.md").read_text(), "local\n")
                backups = list(backup.rglob("SKILL.md"))
                self.assertEqual(len(backups), 1)
                self.assertEqual(backups[0].read_text(), "conflict\n")

    def test_claude_installer_keeps_skills_when_source_is_missing(self) -> None:
        skill = self.fixture.home / ".claude/skills/unslop-commit/SKILL.md"
        skill.parent.mkdir(parents=True)
        skill.write_text("keep\n", encoding="utf-8")
        result = self.run_bash(
            'set -e; source "$1"; claude_managed_source_dir() { return 1; }; install_claude_managed_asset_links',
            str(COMMON),
        )
        self.assert_success(result)
        self.assertIn("WARN: Claude managed source not found", result.stderr)
        self.assertEqual(skill.read_text(), "keep\n")

    def test_both_persistence_phases_install_claude_assets(self) -> None:
        for source, phase in ((POST_CREATE, "post_create_persistence_phase"),
                              (POST_START, "post_start_persistence_phase")):
            with self.subTest(source=source):
                result = self.run_bash(
                    'source "$1"; declare -f "$2"', str(source), phase,
                )
                self.assert_success(result)
                self.assertRegex(result.stdout, r"(?m)^\s*install_claude_managed_asset_links\s*;?$")

    def test_opencode_materializes_unslop_skills_and_preserves_local_content(self) -> None:
        source = self.fixture.root / "opencode source"
        target = self.fixture.root / "opencode target"
        state_dir = self.fixture.root / "opencode state"
        for name in UNSLOP_SKILLS:
            shutil.copytree(CONTAINER_DOTFILES / "private_dot_config/opencode/skills" / name,
                            source / "skills" / name)
        local_skill = target / "skills/local-only/SKILL.md"
        local_skill.parent.mkdir(parents=True)
        local_skill.write_text("local\n", encoding="utf-8")
        script = 'set -e; source "$1"; materialize_opencode_managed_assets_from "$2" "$3" "$4"'
        args = (str(COMMON), str(source), str(target), str(state_dir))
        self.assert_success(self.run_bash(script, *args))
        manifest = (state_dir / "managed-assets.tsv").read_bytes()
        self.assert_success(self.run_bash(script, *args))
        self.assertEqual((state_dir / "managed-assets.tsv").read_bytes(), manifest)
        for name in UNSLOP_SKILLS:
            installed = target / "skills" / name / "SKILL.md"
            self.assertFalse(installed.is_symlink())
            self.assertEqual(installed.read_bytes(), (source / "skills" / name / "SKILL.md").read_bytes())
        nested = Path("skills/unslop-file/scripts/cli.py")
        self.assertFalse((target / nested).is_symlink())
        self.assertEqual((target / nested).read_bytes(), (source / nested).read_bytes())
        (source / "skills/unslop-commit/SKILL.md").write_text("updated\n", encoding="utf-8")
        self.assert_success(self.run_bash(script, *args))
        self.assertEqual((target / "skills/unslop-commit/SKILL.md").read_text(), "updated\n")
        self.assertEqual(local_skill.read_text(), "local\n")

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

    def test_global_npm_install_timing_preserves_failures(self) -> None:
        fake_bin = self.fixture.root / "fake bin"
        fake_bin.mkdir()
        npm_log = self.fixture.root / "npm-args.log"
        fake_npm = fake_bin / "npm"
        fake_npm.write_text(
            """#!/bin/sh
printf '%s\n' "$*" >>"$NPM_ARG_LOG"
case "$*" in
  *fail-package*) exit 37 ;;
esac
""",
            encoding="utf-8",
        )
        fake_npm.chmod(0o755)
        env = dict(self.env)
        env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"
        env["NPM_ARG_LOG"] = str(npm_log)

        result = self.run_bash(
            r'''
source "$1"
install_global_npm_package '@opencode-ai/sdk@1.18.21'
set +e
install_global_npm_package 'fail-package@1.0.0'
rc=$?
set -e
[[ "$rc" -eq 37 ]]
''',
            str(POST_CREATE),
            env=env,
        )
        self.assert_success(result)
        self.assertEqual(
            npm_log.read_text(encoding="utf-8").splitlines(),
            [
                "install -g @opencode-ai/sdk@1.18.21",
                "install -g fail-package@1.0.0",
            ],
        )
        self.assertRegex(
            result.stdout,
            r"\[npm\] finish package=fail-package@1\.0\.0 status=37 elapsed_seconds=\d+",
        )

    def test_prepare_npm_cache_honors_override_and_is_idempotent(self) -> None:
        cache_dir = self.fixture.root / "persistent data" / "npm cache"
        env = dict(self.env)
        env["npm_config_cache"] = str(cache_dir)
        result = self.run_bash(
            r'''
source "$1"
prepare_npm_cache
prepare_npm_cache
printf '%s\n' "$npm_config_cache"
''',
            str(POST_CREATE),
            env=env,
        )
        self.assert_success(result)
        self.assertEqual(result.stdout.strip(), str(cache_dir))
        self.assertTrue(cache_dir.is_dir())
        self.assertEqual(stat.S_IMODE(cache_dir.stat().st_mode), 0o700)

    def test_promptfoo_runtime_install_uses_lockfile_and_omits_optional(self) -> None:
        fake_bin = self.fixture.root / "fake bin"
        fake_bin.mkdir()
        source_dir = self.fixture.root / "runtime source"
        runtime_dir = self.fixture.root / "runtime target"
        source_dir.mkdir()
        (source_dir / "package.json").write_text('{"private":true}\n', encoding="utf-8")
        (source_dir / "package-lock.json").write_text(
            """{
  "lockfileVersion": 3,
  "packages": {
    "node_modules/@libsql/linux-arm64-gnu": {
      "version": "0.5.29",
      "optional": true
    },
    "node_modules/@libsql/linux-x64-gnu": {
      "version": "0.5.29",
      "optional": true
    }
  }
}
""",
            encoding="utf-8",
        )
        npm_log = self.fixture.root / "npm.log"
        (fake_bin / "npm").write_text(
            """#!/bin/sh
printf 'cwd=%s args=%s CI=%s\n' "$PWD" "$*" "${CI-unset}" >>"$NPM_LOG"
""",
            encoding="utf-8",
        )
        (fake_bin / "npm").chmod(0o755)
        env = dict(self.env)
        env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"
        env["NPM_LOG"] = str(npm_log)
        env["CI"] = "1"

        result = self.run_bash(
            r'''
source "$1"
install_promptfoo_runtime "$2" "$3"
''',
            str(POST_CREATE),
            str(source_dir),
            str(runtime_dir),
            env=env,
        )
        self.assert_success(result)
        self.assertEqual(
            (runtime_dir / "package.json").read_text(encoding="utf-8"),
            '{"private":true}\n',
        )
        self.assertEqual(
            (runtime_dir / "package-lock.json").read_text(encoding="utf-8"),
            (source_dir / "package-lock.json").read_text(encoding="utf-8"),
        )
        self.assertEqual(stat.S_IMODE(runtime_dir.stat().st_mode), 0o700)
        npm_lines = npm_log.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(npm_lines), 2)
        self.assertEqual(
            npm_lines[0],
            f"cwd={runtime_dir} args=ci --omit=optional --timing CI=unset",
        )
        self.assertRegex(
            npm_lines[1],
            rf"^cwd={re.escape(str(runtime_dir))} args=install --no-save "
            r"--omit=optional @libsql/linux-(?:arm64|x64)-gnu@0\.5\.29 CI=unset$",
        )
        self.assertNotIn("--allow-scripts", "\n".join(npm_lines))
        self.assertRegex(
            result.stdout,
            r"\[npm\] finish package=promptfoo-runtime status=0 elapsed_seconds=\d+",
        )

    def test_promptfoo_runtime_is_a_required_independent_phase(self) -> None:
        post_create = POST_CREATE.read_text(encoding="utf-8")
        global_step = 'step "Install global npm packages from '
        runtime_step = 'step "Install lockfile-backed Promptfoo runtime"'

        self.assertIn(
            'source_dir="${1:-/tmp/host-homelab-configs/promptfoo-runtime}"',
            post_create,
        )
        self.assertNotIn("npm_package_belongs_to_promptfoo_runtime", post_create)
        self.assertNotIn("promptfoo_runtime_requested", post_create)
        self.assertLess(post_create.index(global_step), post_create.index(runtime_step))
        runtime_phase = post_create[post_create.index(runtime_step) :]
        self.assertIn("install_promptfoo_runtime\n", runtime_phase)
        self.assertIn("verify_promptfoo_runtime\n", runtime_phase)

    def test_promptfoo_runtime_source_is_delivered_by_configs_mount(self) -> None:
        devcontainer = DEVCONTAINER_CONFIG.read_text(encoding="utf-8")
        mount = (
            "source=${localEnv:HOME}/Documents/development/dotfiles/"
            "private_Documents/development/container-dotfiles/devcontainers/"
            "gitlab.com/servers-homelab/homelab-IaC/configs,"
            "target=/tmp/host-homelab-configs,type=bind,readonly"
        )

        self.assertIn(mount, devcontainer)
        self.assertTrue((PROMPTFOO_SOURCE / "package.json").is_file())
        self.assertTrue((PROMPTFOO_SOURCE / "package-lock.json").is_file())

    def test_promptfoo_runtime_install_rejects_missing_owned_inputs(self) -> None:
        fake_bin = self.fixture.root / "fake bin"
        fake_bin.mkdir()
        missing_source = self.fixture.root / "missing runtime source"
        runtime_dir = self.fixture.root / "runtime target"
        npm_log = self.fixture.root / "npm.log"
        (fake_bin / "npm").write_text(
            "#!/bin/sh\nprintf '%s\\n' \"$*\" >>\"$NPM_LOG\"\n",
            encoding="utf-8",
        )
        (fake_bin / "npm").chmod(0o755)
        env = dict(self.env)
        env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"
        env["NPM_LOG"] = str(npm_log)

        result = self.run_bash(
            'source "$1"; install_promptfoo_runtime "$2" "$3"',
            str(POST_CREATE),
            str(missing_source),
            str(runtime_dir),
            env=env,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Promptfoo runtime manifest or lockfile is missing", result.stderr)
        self.assertFalse(runtime_dir.exists())
        self.assertFalse(npm_log.exists())

    def test_promptfoo_runtime_install_rejects_missing_platform_binding(self) -> None:
        fake_bin = self.fixture.root / "fake bin"
        fake_bin.mkdir()
        source_dir = self.fixture.root / "runtime source"
        runtime_dir = self.fixture.root / "runtime target"
        source_dir.mkdir()
        (source_dir / "package.json").write_text('{"private":true}\n', encoding="utf-8")
        (source_dir / "package-lock.json").write_text(
            '{"lockfileVersion":3,"packages":{}}\n',
            encoding="utf-8",
        )
        npm_log = self.fixture.root / "npm.log"
        (fake_bin / "npm").write_text(
            "#!/bin/sh\nprintf '%s\\n' \"$*\" >>\"$NPM_LOG\"\n",
            encoding="utf-8",
        )
        (fake_bin / "npm").chmod(0o755)
        env = dict(self.env)
        env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"
        env["NPM_LOG"] = str(npm_log)

        result = self.run_bash(
            'source "$1"; install_promptfoo_runtime "$2" "$3"',
            str(POST_CREATE),
            str(source_dir),
            str(runtime_dir),
            env=env,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Missing optional lockfile entry for @libsql/linux-", result.stderr)
        self.assertFalse(npm_log.exists())

    def test_promptfoo_runtime_verifier_checks_esm_packages_before_cli(self) -> None:
        fake_bin = self.fixture.root / "fake bin"
        fake_bin.mkdir()
        runtime_dir = self.fixture.root / "runtime root"
        promptfoo_bin = runtime_dir / "node_modules" / ".bin" / "promptfoo"
        promptfoo_bin.parent.mkdir(parents=True)
        node_log = self.fixture.root / "node.log"
        promptfoo_log = self.fixture.root / "promptfoo.log"

        (fake_bin / "node").write_text(
            """#!/bin/sh
payload="$(cat)"
printf 'cwd=%s args=%s\n%s\n' "$PWD" "$*" "$payload" >"$NODE_LOG"
if [ "${NODE_RESOLVE_FAIL:-0}" = 1 ]; then exit 41; fi
""",
            encoding="utf-8",
        )
        promptfoo_bin.write_text(
            """#!/bin/sh
printf '%s\n' "$*" >"$PROMPTFOO_LOG"
""",
            encoding="utf-8",
        )
        (fake_bin / "node").chmod(0o755)
        promptfoo_bin.chmod(0o755)

        env = dict(self.env)
        env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"
        env["NODE_LOG"] = str(node_log)
        env["PROMPTFOO_LOG"] = str(promptfoo_log)
        result = self.run_bash(
            'source "$1"; verify_promptfoo_runtime "$2"',
            str(POST_CREATE),
            str(runtime_dir),
            env=env,
        )
        self.assert_success(result)
        node_text = node_log.read_text(encoding="utf-8")
        self.assertIn(f"cwd={runtime_dir}", node_text)
        for package_name in (
            "promptfoo",
            "@opencode-ai/sdk",
            "@anthropic-ai/claude-agent-sdk",
            "@anthropic-ai/sdk",
        ):
            self.assertIn(package_name, node_text)
        self.assertEqual(promptfoo_log.read_text(encoding="utf-8").strip(), "--version")

        promptfoo_log.unlink()
        env["NODE_RESOLVE_FAIL"] = "1"
        failed = self.run_bash(
            'source "$1"; set +e; verify_promptfoo_runtime "$2"; rc=$?; [[ "$rc" -eq 1 ]]',
            str(POST_CREATE),
            str(runtime_dir),
            env=env,
        )
        self.assert_success(failed)
        self.assertFalse(promptfoo_log.exists())

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
