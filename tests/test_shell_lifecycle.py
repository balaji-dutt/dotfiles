from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

from tests.support.fixtures import (
    init_git_repository,
    isolated_environment,
    read_json_lines,
    restricted_lifecycle_env,
    run_git,
    write_executable,
    write_fake_command,
)
from tests.support.chezmoi_render import CHEZMOI, render_matrix, render_template


COMMIT_TEMPLATE = ".chezmoiscripts/run_after_10-dotfiles-commit-template.sh.tmpl"
CLEANUP = ".chezmoiscripts/run_once_after_99-cleanup-wrong-apply.sh.tmpl"
PLANNOTATOR = ".chezmoiscripts/run_onchange_after_install_plannotator.sh.tmpl"
KANBAN = ".chezmoiscripts/run_onchange_after_install_better_beads_kanban.sh.tmpl"
RETIRE_LINKS = ".chezmoiscripts/run_once_after_97-retire-macos-beads-dolt-links.sh.tmpl"
RETIRE_MNEMO = ".chezmoiscripts/run_once_after_97-retire-macos-mnemo.sh.tmpl"
ANSIBLE_KEY = ".chezmoiscripts/run_once_before_copy_ansible_key.sh.tmpl"
SUBLIME = ".chezmoiscripts/run_once_before_copy_sublime_merge_packages.sh.tmpl"
PROMPTFOO = ".chezmoiscripts/run_onchange_after_install_promptfoo_runtime.sh.tmpl"
BROWSER_POLICIES = ".chezmoiscripts/run_onchange_after_browser-policies.sh.tmpl"
ANSIBLE_IMAGE = ".chezmoiscripts/run_onchange_after_ansible_syntax_image.sh.tmpl"
REMINDER = ".chezmoiscripts/run_onchange_after_devcontainer_sync_reminder.sh.tmpl"


def run_script(content: str, root: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    script = root / "lifecycle.sh"
    script.write_text(content, encoding="utf-8")
    return subprocess.run(
        ["/bin/bash", str(script)],
        cwd=root,
        env=env,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=30,
    )


@unittest.skipUnless(CHEZMOI, "chezmoi is required")
class CommitTemplateLifecycleTests(unittest.TestCase):
    def render(self, repository: Path) -> str:
        def configure(data: dict[str, object]) -> None:
            chezmoi = data["chezmoi"]
            assert isinstance(chezmoi, dict)
            chezmoi["workingTree"] = str(repository)

        return render_template(COMMIT_TEMPLATE, "linux", configure)

    def test_missing_template_is_a_noop(self) -> None:
        with isolated_environment(prefix="commit-template-") as fixture:
            repository = init_git_repository(fixture.root / "repo with spaces", env=fixture.env)
            result = run_script(self.render(repository), fixture.root, fixture.env)

            self.assertEqual(result.returncode, 0, result.stderr)
            configured = run_git(
                repository,
                "config",
                "--local",
                "--get",
                "commit.template",
                env=fixture.env,
                check=False,
            )
            self.assertNotEqual(configured.returncode, 0)

    def test_missing_or_stale_config_is_set_idempotently(self) -> None:
        with isolated_environment(prefix="commit-template-") as fixture:
            repository = init_git_repository(fixture.root / "repo with spaces", env=fixture.env)
            (repository / ".gitmessage").write_text("subject\n", encoding="utf-8")
            script = self.render(repository)

            first = run_script(script, fixture.root, fixture.env)
            second = run_script(script, fixture.root, fixture.env)

            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertEqual(
                run_git(
                    repository,
                    "config",
                    "--local",
                    "--get",
                    "commit.template",
                    env=fixture.env,
                ).stdout.strip(),
                ".gitmessage",
            )
            self.assertNotIn("set commit.template", second.stdout)

            run_git(
                repository,
                "config",
                "--local",
                "commit.template",
                "missing-template",
                env=fixture.env,
            )
            stale = run_script(script, fixture.root, fixture.env)
            self.assertEqual(stale.returncode, 0, stale.stderr)
            self.assertIn("reset commit.template", stale.stdout)

    def test_existing_custom_template_is_preserved(self) -> None:
        with isolated_environment(prefix="commit-template-") as fixture:
            repository = init_git_repository(fixture.root / "repo", env=fixture.env)
            (repository / ".gitmessage").write_text("managed\n", encoding="utf-8")
            (repository / "custom message").write_text("custom\n", encoding="utf-8")
            run_git(
                repository,
                "config",
                "--local",
                "commit.template",
                "custom message",
                env=fixture.env,
            )

            result = run_script(self.render(repository), fixture.root, fixture.env)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                run_git(
                    repository,
                    "config",
                    "--local",
                    "--get",
                    "commit.template",
                    env=fixture.env,
                ).stdout.strip(),
                "custom message",
            )

    def test_git_write_failure_propagates(self) -> None:
        with isolated_environment(prefix="commit-template-") as fixture:
            repository = fixture.root / "repo"
            repository.mkdir()
            (repository / ".gitmessage").write_text("managed\n", encoding="utf-8")
            log = fixture.root / "git.jsonl"
            write_fake_command(fixture.fake_bin, "git", log_path=log, exit_code=23)
            env = {**fixture.env, "PATH": str(fixture.fake_bin)}

            result = run_script(self.render(repository), fixture.root, env)

            self.assertEqual(result.returncode, 23)
            self.assertGreaterEqual(len(read_json_lines(log)), 2)


@unittest.skipUnless(CHEZMOI, "chezmoi is required")
class CleanupLifecycleTests(unittest.TestCase):
    def render(self, destination: Path, platform: str = "linux") -> str:
        def configure(data: dict[str, object]) -> None:
            chezmoi = data["chezmoi"]
            assert isinstance(chezmoi, dict)
            chezmoi["destDir"] = str(destination)

        return render_template(CLEANUP, platform, configure)

    def test_curated_paths_are_removed_and_unrelated_files_survive(self) -> None:
        with isolated_environment(prefix="wrong-apply-") as fixture:
            for relative in ("assets/file", "docs/file", "AppData/file"):
                path = fixture.home / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("remove\n", encoding="utf-8")
            keep = fixture.home / "keep.txt"
            keep.write_text("keep\n", encoding="utf-8")
            script = self.render(fixture.home)

            first = run_script(script, fixture.root, fixture.env)
            second = run_script(script, fixture.root, fixture.env)

            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertFalse((fixture.home / "assets").exists())
            self.assertFalse((fixture.home / "docs").exists())
            self.assertFalse((fixture.home / "AppData").exists())
            self.assertEqual(keep.read_text(encoding="utf-8"), "keep\n")

    def test_dry_run_preserves_targets(self) -> None:
        with isolated_environment(prefix="wrong-apply-") as fixture:
            target = fixture.home / "assets" / "file"
            target.parent.mkdir(parents=True)
            target.write_text("keep\n", encoding="utf-8")
            env = {**fixture.env, "CHEZMOI_WRONG_APPLY_DRYRUN": "1"}

            result = run_script(self.render(fixture.home), fixture.root, env)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(target.exists())
            self.assertIn("[dry-run] would remove", result.stderr)


@unittest.skipUnless(CHEZMOI, "chezmoi is required")
class InstallerAndPlatformLifecycleTests(unittest.TestCase):
    def test_plannotator_installed_and_unsupported_paths_do_not_download(self) -> None:
        with isolated_environment(prefix="plannotator-") as fixture:
            bin_path = fixture.home / ".local" / "bin" / "plannotator"
            write_executable(bin_path, "#!/bin/sh\nprintf 'plannotator 1.2.3\\n'\n")
            write_executable(fixture.fake_bin / "uname", "#!/bin/sh\nprintf 'x86_64\\n'\n")
            curl_log = fixture.root / "curl.jsonl"
            write_fake_command(fixture.fake_bin, "curl", log_path=curl_log, exit_code=91)

            def configure(data: dict[str, object]) -> None:
                data["plannotator_version"] = "1.2.3"

            installed = run_script(
                render_template(PLANNOTATOR, "macos", configure),
                fixture.root,
                fixture.env,
            )
            unsupported = run_script(
                render_template(PLANNOTATOR, "linux", configure),
                fixture.root,
                fixture.env,
            )

            self.assertEqual(installed.returncode, 0, installed.stderr)
            self.assertIn("already installed", installed.stdout)
            self.assertEqual(unsupported.returncode, 0, unsupported.stderr)
            self.assertFalse(curl_log.exists())

    def test_plannotator_stale_install_is_replaced_after_checksum_validation(self) -> None:
        with isolated_environment(prefix="plannotator-") as fixture:
            bin_path = fixture.home / ".local" / "bin" / "plannotator"
            write_executable(bin_path, "#!/bin/sh\nprintf 'plannotator 1.0.0\\n'\n")
            write_executable(fixture.fake_bin / "uname", "#!/bin/sh\nprintf 'x86_64\\n'\n")
            curl_log = fixture.root / "curl.jsonl"
            payload = b"#!/bin/sh\nprintf 'plannotator 1.2.3\\n'\n"
            digest = hashlib.sha256(payload).hexdigest()
            curl = fixture.fake_bin / "curl"
            write_executable(
                curl,
                f"#!{sys.executable}\n"
                "import json, pathlib, sys\n"
                f"log = pathlib.Path({str(curl_log)!r})\n"
                "with log.open('a', encoding='utf-8') as handle:\n"
                "    handle.write(json.dumps(sys.argv[1:]) + '\\n')\n"
                "destination = pathlib.Path(sys.argv[sys.argv.index('-o') + 1])\n"
                f"payload = {payload!r}\n"
                f"digest = {digest!r}\n"
                "url = next(arg for arg in sys.argv[1:] if arg.startswith('https://'))\n"
                "if url.endswith('.sha256'):\n"
                "    destination.write_text(digest + '  asset\\n', encoding='utf-8')\n"
                "else:\n"
                "    destination.write_bytes(payload)\n",
            )

            def configure(data: dict[str, object]) -> None:
                data["plannotator_version"] = "1.2.3"

            result = run_script(
                render_template(PLANNOTATOR, "macos", configure),
                fixture.root,
                fixture.env,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("Updating plannotator: 1.0.0 -> 1.2.3", result.stdout)
            self.assertEqual(bin_path.read_bytes(), payload)
            self.assertEqual(len(read_json_lines(curl_log)), 2)

    def test_plannotator_checksum_failure_preserves_existing_binary(self) -> None:
        with isolated_environment(prefix="plannotator-") as fixture:
            bin_path = fixture.home / ".local" / "bin" / "plannotator"
            original = "#!/bin/sh\nprintf 'plannotator 1.0.0\\n'\n"
            write_executable(bin_path, original)
            write_executable(fixture.fake_bin / "uname", "#!/bin/sh\nprintf 'x86_64\\n'\n")
            curl = fixture.fake_bin / "curl"
            write_executable(
                curl,
                f"#!{sys.executable}\n"
                "import pathlib, sys\n"
                "destination = pathlib.Path(sys.argv[sys.argv.index('-o') + 1])\n"
                "destination.write_text('wrong\\n', encoding='utf-8')\n",
            )

            def configure(data: dict[str, object]) -> None:
                data["plannotator_version"] = "1.2.3"

            result = run_script(
                render_template(PLANNOTATOR, "macos", configure),
                fixture.root,
                fixture.env,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("SHA256 mismatch", result.stderr)
            self.assertEqual(bin_path.read_text(encoding="utf-8"), original)

    def test_kanban_missing_optional_tools_is_a_noop(self) -> None:
        with isolated_environment(prefix="kanban-") as fixture:
            env = {**fixture.env, "PATH": str(fixture.fake_bin)}
            missing_code = run_script(
                render_template(KANBAN, "linux"), fixture.root, env
            )
            write_fake_command(
                fixture.fake_bin,
                "code",
                log_path=fixture.root / "code.jsonl",
            )
            missing_curl = run_script(
                render_template(KANBAN, "linux"), fixture.root, env
            )

            self.assertEqual(missing_code.returncode, 0, missing_code.stderr)
            self.assertIn("code command not found", missing_code.stdout)
            self.assertEqual(missing_curl.returncode, 0, missing_curl.stderr)
            self.assertIn("curl command not found", missing_curl.stdout)

    def test_macos_retirement_removes_only_expected_links(self) -> None:
        with isolated_environment(prefix="retire-links-") as fixture:
            bin_dir = fixture.home / ".local" / "bin"
            shims = fixture.home / ".local" / "share" / "mise" / "shims"
            bin_dir.mkdir(parents=True)
            shims.mkdir(parents=True)
            (bin_dir / "bd").symlink_to(shims / "bd")
            (bin_dir / "dolt").write_text("custom\n", encoding="utf-8")
            script = render_matrix()[("macos", RETIRE_LINKS)].decode()

            first = run_script(script, fixture.root, fixture.env)
            second = run_script(script, fixture.root, fixture.env)

            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertFalse((bin_dir / "bd").exists())
            self.assertEqual((bin_dir / "dolt").read_text(encoding="utf-8"), "custom\n")

    def test_devcontainer_reminder_is_platform_scoped(self) -> None:
        with isolated_environment(prefix="reminder-") as fixture:
            linux = run_script(
                render_matrix()[("linux", REMINDER)].decode(), fixture.root, fixture.env
            )
            wsl = run_script(
                render_matrix()[("wsl2", REMINDER)].decode(), fixture.root, fixture.env
            )

            self.assertEqual(linux.returncode, 0, linux.stderr)
            self.assertEqual(linux.stdout, "")
            self.assertEqual(wsl.returncode, 0, wsl.stderr)
            self.assertIn("Devcontainer sync inputs changed", wsl.stdout)


@unittest.skipUnless(CHEZMOI and sys.platform.startswith("linux"), "Linux and chezmoi are required")
class LifecycleHostBoundaryTests(unittest.TestCase):
    def test_macos_only_hooks_do_not_invoke_host_commands_on_linux(self) -> None:
        hooks = (
            RETIRE_MNEMO,
            ".chezmoiscripts/run_after_macos-nfs-config.sh.tmpl",
            ".chezmoiscripts/run_after_macos-home-ssh.sh.tmpl",
            ".chezmoiscripts/run_onchange_after_macos-vdi-apps.sh.tmpl",
            ".chezmoiscripts/run_after_update_copyq.sh.tmpl",
            ".chezmoiscripts/run_after_macos-openusage-integrations.sh.tmpl",
            PROMPTFOO,
            ".chezmoiscripts/run_onchange_after_reload_launch_agents.sh.tmpl",
            BROWSER_POLICIES,
            ".chezmoiscripts/run_after_macos-opencode-pin.sh.tmpl",
        )
        with isolated_environment(prefix="lifecycle-host-") as fixture:
            log = fixture.root / "forbidden.jsonl"
            for command in ("brew", "curl", "op", "sudo", "hdiutil", "mise", "npm", "launchctl", "open",
                            "sshd", "pkill"):
                write_fake_command(fixture.fake_bin, command, log_path=log, exit_code=89)
            env = restricted_lifecycle_env(fixture.home, fixture.fake_bin)
            sentinel = fixture.home / "sentinel"
            sentinel.write_text("keep\n", encoding="utf-8")
            for hook in hooks:
                with self.subTest(hook=hook):
                    script = render_template(hook, "linux", environment=env)
                    result = run_script(script, fixture.root, env)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertFalse(log.exists(), hook)
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "keep\n")

    def test_mnemo_retirement_preserves_unrelated_formula_and_taps(self) -> None:
        with isolated_environment(prefix="retire-mnemo-") as fixture:
            env = restricted_lifecycle_env(fixture.home, fixture.fake_bin)
            log = fixture.root / "brew.jsonl"
            formulas = fixture.root / "formulas"
            taps = fixture.root / "taps"
            formulas.write_text("pilan-ai/tap/mnemo\npilan-ai/tap/other\nother/tap/mnemo\n", encoding="utf-8")
            taps.write_text("pilan-ai/tap\nother/tap\n", encoding="utf-8")
            brew = fixture.fake_bin / "brew"
            write_executable(
                brew,
                f"#!{sys.executable}\n"
                "import json, pathlib, sys\n"
                f"log = pathlib.Path({str(log)!r})\n"
                f"formulas = pathlib.Path({str(formulas)!r})\n"
                f"taps = pathlib.Path({str(taps)!r})\n"
                "args = sys.argv[1:]\n"
                "with log.open('a', encoding='utf-8') as handle:\n"
                "    handle.write(json.dumps(args) + '\\n')\n"
                "if args == ['list', '--formula', '--full-name']:\n"
                "    print(formulas.read_text(), end='')\n"
                "elif args == ['tap']:\n"
                "    print(taps.read_text(), end='')\n"
                "elif args == ['uninstall', '--formula', 'pilan-ai/tap/mnemo']:\n"
                "    formulas.write_text(formulas.read_text().replace('pilan-ai/tap/mnemo\\n', ''))\n"
                "elif args == ['untap', 'pilan-ai/tap']:\n"
                "    if 'pilan-ai/tap/other' in formulas.read_text():\n"
                "        sys.exit(20)\n"
                "    taps.write_text(taps.read_text().replace('pilan-ai/tap\\n', ''))\n"
                "else:\n"
                "    sys.exit(21)\n",
            )
            script = render_template(RETIRE_MNEMO, "macos", environment=env)
            first = run_script(script, fixture.root, env)
            second = run_script(script, fixture.root, env)
            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertIn("preserving Homebrew tap", first.stdout)
            self.assertEqual(formulas.read_text(), "pilan-ai/tap/other\nother/tap/mnemo\n")
            self.assertEqual(taps.read_text(), "pilan-ai/tap\nother/tap\n")
            formulas.write_text("other/tap/mnemo\n", encoding="utf-8")
            third = run_script(script, fixture.root, env)
            self.assertEqual(third.returncode, 0, third.stderr)
            self.assertEqual(taps.read_text(), "other/tap\n")
            self.assertEqual(formulas.read_text(), "other/tap/mnemo\n")
            calls = read_json_lines(log)
            self.assertEqual(calls.count(["uninstall", "--formula", "pilan-ai/tap/mnemo"]), 1)
            self.assertEqual(calls.count(["untap", "pilan-ai/tap"]), 3)
            for call in calls:
                if call[0] in ("uninstall", "untap"):
                    self.assertIn(call[-1], ("pilan-ai/tap/mnemo", "pilan-ai/tap"), call)

    def test_mnemo_uninstall_failure_does_not_untap(self) -> None:
        with isolated_environment(prefix="retire-mnemo-error-") as fixture:
            env = restricted_lifecycle_env(fixture.home, fixture.fake_bin)
            log = fixture.root / "brew.jsonl"
            write_executable(
                fixture.fake_bin / "brew",
                f"#!{sys.executable}\n"
                "import json, pathlib, sys\n"
                f"log = pathlib.Path({str(log)!r})\n"
                "args = sys.argv[1:]\n"
                "with log.open('a', encoding='utf-8') as handle:\n"
                "    handle.write(json.dumps(args) + '\\n')\n"
                "if args == ['list', '--formula', '--full-name']:\n"
                "    print('pilan-ai/tap/mnemo')\n"
                "elif args == ['uninstall', '--formula', 'pilan-ai/tap/mnemo']:\n"
                "    sys.exit(19)\n"
                "else:\n"
                "    sys.exit(21)\n",
            )
            result = run_script(render_template(RETIRE_MNEMO, "macos", environment=env), fixture.root, env)
            self.assertEqual(result.returncode, 19)
            self.assertNotIn(["untap", "pilan-ai/tap"], read_json_lines(log))

    def test_wsl_key_hook_copies_only_synthetic_key_with_private_permissions(self) -> None:
        with isolated_environment(prefix="lifecycle-key-") as fixture:
            env = restricted_lifecycle_env(fixture.home, fixture.fake_bin)
            source = fixture.root / "synthetic-key"
            source.write_text("synthetic-only\n", encoding="utf-8")
            def configure(data: dict[str, object]) -> None:
                data["ansible_key"] = str(source)

            script = render_template(ANSIBLE_KEY, "wsl2", configure, environment=env)
            first = run_script(script, fixture.root, env)
            second = run_script(script, fixture.root, env)
            destination = fixture.home / ".ssh" / "root_terraform_ansible"
            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertEqual(destination.read_text(encoding="utf-8"), "synthetic-only\n")
            self.assertEqual(destination.stat().st_mode & 0o777, 0o600)
            self.assertEqual(destination.parent.stat().st_mode & 0o777, 0o700)
            missing = render_template(ANSIBLE_KEY, "wsl2", environment=env)
            self.assertEqual(run_script(missing, fixture.root, env).returncode, 0)

    def test_wsl_key_hook_reports_missing_synthetic_source_without_touching_home(self) -> None:
        with isolated_environment(prefix="lifecycle-key-error-") as fixture:
            env = restricted_lifecycle_env(fixture.home, fixture.fake_bin)

            def configure(data: dict[str, object]) -> None:
                data["ansible_key"] = str(fixture.root / "absent-key")

            script = render_template(ANSIBLE_KEY, "wsl2", configure, environment=env)
            result = run_script(script, fixture.root, env)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("absent-key", result.stderr)
            self.assertFalse((fixture.home / ".ssh" / "root_terraform_ansible").exists())

            inactive = render_template(ANSIBLE_KEY, "macos", configure, environment=env)
            self.assertEqual(run_script(inactive, fixture.root, env).returncode, 0)
            self.assertFalse((fixture.home / ".ssh" / "root_terraform_ansible").exists())

    def test_sublime_copy_stays_in_synthetic_home_without_network(self) -> None:
        with isolated_environment(prefix="lifecycle-sublime-") as fixture:
            env = restricted_lifecycle_env(fixture.home, fixture.fake_bin)
            source = fixture.root / "source" / "configs" / "sublime-merge" / "Git Commit.sublime-syntax"
            source.parent.mkdir(parents=True)
            source.write_text("synthetic syntax\n", encoding="utf-8")
            destination = fixture.home / ".config" / "sublime-merge" / "Packages" / "User" / "Git Formats"
            destination.mkdir(parents=True)
            (destination / "Git Common.sublime-syntax").write_text("synthetic common\n", encoding="utf-8")
            log = fixture.root / "download.jsonl"
            for command in ("curl", "tar", "lastversion"):
                write_fake_command(fixture.fake_bin, command, log_path=log, exit_code=91)

            def configure(data: dict[str, object]) -> None:
                data["chezmoi"]["sourceDir"] = str(fixture.root / "source")

            script = render_template(SUBLIME, "wsl2", configure, environment=env)
            for _ in range(2):
                result = run_script(script, fixture.root, env)
                self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual((destination / "Git Commit.sublime-syntax").read_text(), "synthetic syntax\n")
            self.assertFalse(log.exists())
            (destination / "Git Common.sublime-syntax").unlink()
            missing_token = run_script(script, fixture.root, env)
            self.assertNotEqual(missing_token.returncode, 0)
            self.assertIn("GITHUB_API_TOKEN is not set", missing_token.stdout)
            self.assertFalse(log.exists())
            self.assertEqual(source.read_text(), "synthetic syntax\n")

    def test_promptfoo_install_uses_fake_mise_and_disposable_runtime(self) -> None:
        with isolated_environment(prefix="lifecycle-promptfoo-") as fixture:
            env = restricted_lifecycle_env(fixture.home, fixture.fake_bin)
            log = fixture.root / "mise.jsonl"
            write_fake_command(fixture.fake_bin, "mise", log_path=log)
            for command in ("npm", "node", "curl", "op"):
                write_fake_command(fixture.fake_bin, command, log_path=fixture.root / "forbidden.jsonl", exit_code=91)
            script = render_template(PROMPTFOO, "macos", environment=env)
            result = run_script(script, fixture.root, env)
            runtime = fixture.home / ".local" / "share" / "promptfoo-runtime"
            self.assertEqual(result.returncode, 0, result.stderr)
            calls = read_json_lines(log)
            self.assertEqual(len(calls), 3)
            self.assertEqual(calls[0]["argv"], ["exec", "--", "npm", "ci"])
            self.assertEqual(calls[2]["argv"], ["exec", "--", str(runtime / "node_modules/.bin/promptfoo"), "--version"])
            for name in ("package.json", "package-lock.json"):
                self.assertEqual(
                    (runtime / name).read_bytes(),
                    (Path(__file__).resolve().parents[1] / "configs" / "promptfoo-runtime" / name).read_bytes(),
                )
            self.assertFalse((fixture.root / "forbidden.jsonl").exists())

    def test_browser_policy_staging_is_disabled_or_confined_to_home(self) -> None:
        with isolated_environment(prefix="lifecycle-browser-") as fixture:
            env = restricted_lifecycle_env(fixture.home, fixture.fake_bin)
            log = fixture.root / "open.jsonl"
            write_fake_command(fixture.fake_bin, "open", log_path=log, exit_code=91)
            disabled = render_template(BROWSER_POLICIES, "macos", environment=env)
            self.assertEqual(run_script(disabled, fixture.root, env).returncode, 0)
            destination = fixture.home / ".local/share/dotfiles/browser-policies/justthebrowser"
            self.assertFalse(destination.exists())

            source = fixture.root / "source" / "configs/browser-policies/justthebrowser"
            for browser in ("chrome", "firefox"):
                profile = source / browser / f"{browser}.mobileconfig"
                profile.parent.mkdir(parents=True, exist_ok=True)
                profile.write_text(f"synthetic {browser}\n", encoding="utf-8")
            (source / "manifest.json").write_text("{}\n", encoding="utf-8")

            def configure(data: dict[str, object]) -> None:
                data["chezmoi"]["sourceDir"] = str(fixture.root / "source")
                data["browser_policies"]["justthebrowser"] = {
                    "enabled": True,
                    "macos_open_profiles_on_update": False,
                }

            script = render_template(BROWSER_POLICIES, "macos", configure, environment=env)
            first = run_script(script, fixture.root, env)
            second = run_script(script, fixture.root, env)
            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertIn("already staged", second.stdout)
            for browser in ("chrome", "firefox"):
                self.assertEqual(
                    (destination / f"{browser}.mobileconfig").read_bytes(),
                    (source / browser / f"{browser}.mobileconfig").read_bytes(),
                )
            self.assertFalse(log.exists())

            def configure_auto_open(data: dict[str, object]) -> None:
                data["chezmoi"]["sourceDir"] = str(fixture.root / "source")
                data["browser_policies"]["justthebrowser"] = {"enabled": True}

            for browser in ("chrome", "firefox"):
                (source / browser / f"{browser}.mobileconfig").write_text(f"updated {browser}\n", encoding="utf-8")
            auto_open = render_template(BROWSER_POLICIES, "macos", configure_auto_open, environment=env)
            result = run_script(auto_open, fixture.root, env)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(len(read_json_lines(log)), 3)

    def test_ansible_image_build_is_dispatched_only_to_fake_docker(self) -> None:
        with isolated_environment(prefix="lifecycle-image-") as fixture:
            env = restricted_lifecycle_env(fixture.home, fixture.fake_bin)
            log = fixture.root / "docker.jsonl"
            write_fake_command(fixture.fake_bin, "docker", log_path=log, exit_code=33)
            script = render_template(ANSIBLE_IMAGE, "linux", environment=env)
            result = run_script(script, fixture.root, env)
            self.assertEqual(result.returncode, 33)
            calls = read_json_lines(log)
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0]["argv"][0], "build")
            self.assertIn("local/ansible-syntax:repo", calls[0]["argv"])


def redirect_exactly(script: str, original: str, replacement: str, count: int = 1) -> str:
    if script.count(original) != count:
        raise AssertionError(f"redirect drift: {original!r} occurs {script.count(original)} times")
    return script.replace(original, replacement)


FORBIDDEN_HOST_TOOLS = ("brew", "hdiutil", "launchctl", "mise", "npm", "op", "sudo")


def contained_env(fixture) -> dict[str, str]:
    for tool in FORBIDDEN_HOST_TOOLS:
        write_fake_command(fixture.fake_bin, tool, log_path=fixture.root / "forbidden.jsonl", exit_code=89)
    return {**fixture.env, "PATH": f"{fixture.fake_bin}:/usr/bin:/bin"}


def write_plannotator_curl(fake_bin: Path, log: Path, payload: bytes, *, exit_code: int = 0) -> None:
    digest = hashlib.sha256(payload).hexdigest()
    write_executable(
        fake_bin / "curl",
        f"#!{sys.executable}\n"
        "import json, pathlib, sys\n"
        f"with pathlib.Path({str(log)!r}).open('a', encoding='utf-8') as handle:\n"
        "    handle.write(json.dumps(sys.argv[1:]) + '\\n')\n"
        f"if {exit_code}:\n"
        f"    sys.exit({exit_code})\n"
        "destination = pathlib.Path(sys.argv[sys.argv.index('-o') + 1])\n"
        "url = next(arg for arg in sys.argv[1:] if arg.startswith('https://'))\n"
        "if url.endswith('.sha256'):\n"
        f"    destination.write_text({digest!r} + '  asset\\n', encoding='utf-8')\n"
        "else:\n"
        f"    destination.write_bytes({payload!r})\n",
    )


@unittest.skipUnless(CHEZMOI and sys.platform != "win32", "chezmoi and a POSIX shell are required")
class InstallerFailureContainmentTests(unittest.TestCase):
    ORIGINAL_PLANNOTATOR = "#!/bin/sh\nprintf 'plannotator 1.0.0\\n'\n"

    def plannotator_script(self, root: Path, env: dict[str, str]) -> str:
        def configure(data: dict[str, object]) -> None:
            data["plannotator_version"] = "1.2.3"

        script = render_template(PLANNOTATOR, "macos", configure, environment=env)
        return redirect_exactly(script, "/tmp/plannotator.XXXXXX", f"{root}/work/plannotator.XXXXXX")

    def run_plannotator(self, payload: bytes, *, curl_exit: int = 0, mv_exit: int | None = None):
        with isolated_environment(prefix="plannotator-fail-") as fixture:
            (fixture.root / "work").mkdir()
            bin_path = fixture.home / ".local" / "bin" / "plannotator"
            write_executable(bin_path, self.ORIGINAL_PLANNOTATOR)
            write_executable(fixture.fake_bin / "uname", "#!/bin/sh\nprintf 'x86_64\\n'\n")
            curl_log = fixture.root / "curl.jsonl"
            write_plannotator_curl(fixture.fake_bin, curl_log, payload, exit_code=curl_exit)
            if mv_exit is not None:
                write_fake_command(fixture.fake_bin, "mv", log_path=fixture.root / "mv.jsonl",
                                   exit_code=mv_exit)
            env = contained_env(fixture)
            result = run_script(self.plannotator_script(fixture.root, env), fixture.root, env)
            self.assertFalse((fixture.root / "forbidden.jsonl").exists())
            return (result, bin_path.read_bytes(), read_json_lines(curl_log),
                    sorted(path.name for path in (fixture.root / "work").iterdir()))

    def test_plannotator_rejects_wrong_candidate_before_replacing_binary(self) -> None:
        result, binary, curls, leftovers = self.run_plannotator(
            b"#!/bin/sh\nprintf 'plannotator 9.9.9\\n'\n")

        self.assertEqual(result.returncode, 1)
        self.assertIn("downloaded plannotator version does not match 1.2.3", result.stderr)
        self.assertEqual(binary.decode(), self.ORIGINAL_PLANNOTATOR)
        self.assertEqual(len(curls), 2)
        self.assertEqual(leftovers, [])

    def test_plannotator_reports_published_version_mismatch(self) -> None:
        payload = (b"#!/bin/sh\ncase \"$0\" in\n"
                   b"  */.local/bin/*) printf 'plannotator 9.9.9\\n' ;;\n"
                   b"  *) printf 'plannotator 1.2.3\\n' ;;\nesac\n")
        result, binary, _, leftovers = self.run_plannotator(payload)

        self.assertEqual(result.returncode, 1)
        self.assertIn("installed plannotator version does not match 1.2.3", result.stderr)
        self.assertEqual(binary, payload)
        self.assertEqual(leftovers, [])

    def test_plannotator_download_and_publish_failures_keep_existing_binary(self) -> None:
        payload = b"#!/bin/sh\nprintf 'plannotator 1.2.3\\n'\n"
        for label, options in (("download", {"curl_exit": 22}), ("publish", {"mv_exit": 1})):
            with self.subTest(label):
                result, binary, curls, leftovers = self.run_plannotator(payload, **options)

                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(binary.decode(), self.ORIGINAL_PLANNOTATOR)
                self.assertEqual(len(curls), 1 if label == "download" else 2)
                self.assertEqual(leftovers, [])

    def kanban_fixture(self, fixture, *, payload: bytes, curl_exit: int = 0,
                       install_exit: int = 0, report_install: bool = True) -> tuple[str, Path, dict[str, str]]:
        env = contained_env(fixture)
        rendered = render_template(KANBAN, "linux", environment=env)
        version = re.search(r'^FORK_VERSION="([^"]+)"$', rendered, re.MULTILINE).group(1)
        pinned = re.search(r'^EXPECTED_SHA="([0-9a-f]{64})"$', rendered, re.MULTILINE).group(1)
        fixture_sha = hashlib.sha256(b"fixture vsix\n").hexdigest()
        script = redirect_exactly(rendered, f'EXPECTED_SHA="{pinned}"', f'EXPECTED_SHA="{fixture_sha}"')
        log = fixture.root / "calls.jsonl"
        extensions = fixture.root / "extensions.txt"
        extensions.write_text("unrelated.extension@1.0.0\n", encoding="utf-8")
        write_executable(
            fixture.fake_bin / "curl",
            f"#!{sys.executable}\n"
            "import json, pathlib, sys\n"
            f"with pathlib.Path({str(log)!r}).open('a', encoding='utf-8') as handle:\n"
            "    handle.write(json.dumps({'command': 'curl', 'args': sys.argv[1:]}) + '\\n')\n"
            f"if {curl_exit}:\n"
            f"    sys.exit({curl_exit})\n"
            f"pathlib.Path(sys.argv[sys.argv.index('-o') + 1]).write_bytes({payload!r})\n",
        )
        write_executable(
            fixture.fake_bin / "code",
            f"#!{sys.executable}\n"
            "import json, pathlib, sys\n"
            f"extensions = pathlib.Path({str(extensions)!r})\n"
            "args = sys.argv[1:]\n"
            f"with pathlib.Path({str(log)!r}).open('a', encoding='utf-8') as handle:\n"
            "    handle.write(json.dumps({'command': 'code', 'args': args}) + '\\n')\n"
            "if args == ['--list-extensions', '--show-versions']:\n"
            "    print(extensions.read_text(), end='')\n"
            "elif args[:1] == ['--uninstall-extension'] and len(args) == 2:\n"
            "    sys.exit(1)\n"
            "elif args[:1] == ['--install-extension'] and args[2:] == ['--force']:\n"
            f"    if {install_exit}:\n"
            f"        sys.exit({install_exit})\n"
            f"    if {report_install!r}:\n"
            "        with extensions.open('a', encoding='utf-8') as handle:\n"
            f"            handle.write('balaji-dutt.better-beads-kanban@{version}\\n')\n"
            "else:\n"
            "    sys.exit(97)\n",
        )
        return script, log, env

    def kanban_calls(self, log: Path, command: str) -> list[list[str]]:
        return [entry["args"] for entry in read_json_lines(log) if entry["command"] == command]

    def test_kanban_installs_verified_fixture_vsix_once(self) -> None:
        with isolated_environment(prefix="kanban-install-") as fixture:
            script, log, env = self.kanban_fixture(fixture, payload=b"fixture vsix\n")
            first = run_script(script, fixture.root, env)
            second = run_script(script, fixture.root, env)

            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertIn("already installed", second.stdout)
            cache = Path(fixture.env["XDG_CACHE_HOME"]) / "dotfiles" / "better-beads-kanban-vsix"
            self.assertTrue(cache.resolve().is_relative_to(fixture.root.resolve()))
            vsix = next(cache.glob("*.vsix"))
            self.assertEqual(vsix.read_bytes(), b"fixture vsix\n")
            marker = next(cache.glob("*.installed"))
            self.assertEqual(marker.read_text(encoding="utf-8"),
                             hashlib.sha256(b"fixture vsix\n").hexdigest())
            self.assertEqual(len(self.kanban_calls(log, "curl")), 1)
            installs = [args for args in self.kanban_calls(log, "code")
                        if args[0] == "--install-extension"]
            self.assertEqual(installs, [["--install-extension", str(vsix), "--force"]])
            uninstalled = {args[1] for args in self.kanban_calls(log, "code")
                           if args[0] == "--uninstall-extension"}
            self.assertEqual(uninstalled, {"davidcforbes.beads-kanban",
                                           "balaji-dutt.beads-kanban-bd-fixes"})
            self.assertFalse((fixture.root / "forbidden.jsonl").exists())

    def test_kanban_failures_never_write_the_marker(self) -> None:
        cases = (
            ("checksum", {"payload": b"tampered vsix\n"}, "SHA256 mismatch", False),
            ("download", {"payload": b"fixture vsix\n", "curl_exit": 22}, None, False),
            ("install", {"payload": b"fixture vsix\n", "install_exit": 5}, None, True),
            ("verification", {"payload": b"fixture vsix\n", "report_install": False},
             "was not reported after install", True),
        )
        for label, options, message, installs in cases:
            with self.subTest(label), isolated_environment(prefix=f"kanban-{label}-") as fixture:
                script, log, env = self.kanban_fixture(fixture, **options)
                result = run_script(script, fixture.root, env)

                self.assertNotEqual(result.returncode, 0)
                if message is not None:
                    self.assertIn(message, result.stderr)
                self.assertFalse((fixture.root / "forbidden.jsonl").exists())
                cache = Path(fixture.env["XDG_CACHE_HOME"]) / "dotfiles" / "better-beads-kanban-vsix"
                self.assertEqual(list(cache.glob("*.installed")), [])
                attempted = [args for args in self.kanban_calls(log, "code")
                             if args[0] == "--install-extension"]
                self.assertEqual(bool(attempted), installs)

    def test_retirement_preserves_foreign_links_and_reports_failed_removal(self) -> None:
        with isolated_environment(prefix="retire-links-scope-") as fixture:
            bin_dir = fixture.home / ".local" / "bin"
            shims = fixture.home / ".local" / "share" / "mise" / "shims"
            bin_dir.mkdir(parents=True)
            shims.mkdir(parents=True)
            foreign = fixture.root / "elsewhere" / "bd"
            (bin_dir / "bd").symlink_to(foreign)
            (bin_dir / "dolt").symlink_to(shims / "dolt")
            script = render_matrix()[("macos", RETIRE_LINKS)].decode()
            write_fake_command(fixture.fake_bin, "rm", log_path=fixture.root / "rm.jsonl")

            failed = run_script(script, fixture.root, contained_env(fixture))

            self.assertEqual(failed.returncode, 1)
            self.assertIn("preserving symlink with different target", failed.stdout)
            self.assertIn("failed to remove retired symlink", failed.stderr)
            self.assertEqual((bin_dir / "bd").readlink(), foreign)
            self.assertTrue((bin_dir / "dolt").is_symlink())
            self.assertEqual(read_json_lines(fixture.root / "rm.jsonl")[0]["argv"],
                             ["-f", "--", str(bin_dir / "dolt")])
            self.assertFalse((fixture.root / "forbidden.jsonl").exists())


@unittest.skipUnless(CHEZMOI and sys.platform != "win32", "chezmoi and a POSIX shell are required")
class PosixWrapperDispatchTests(unittest.TestCase):
    MCP = ".chezmoiscripts/run_onchange_after_claude_mcp_servers.sh.tmpl"
    GIT_HOOKS = ".chezmoiscripts/run_after_20-git-template-hooks.sh.tmpl"
    HOST_AI = ".chezmoiscripts/run_onchange_after_host_ai_plugin_refresh.sh.tmpl"

    def write_python(self, fake_bin: Path, name: str, log: Path, *, exit_code: int = 0,
                     version_ok: bool = True) -> None:
        write_executable(
            fake_bin / name,
            f"#!{sys.executable}\n"
            "import json, pathlib, sys\n"
            "args = sys.argv[1:]\n"
            "if args[:1] == ['-c']:\n"
            f"    sys.exit(0 if {version_ok!r} else 1)\n"
            "stdin = sys.stdin.read() if args[:1] == ['-'] else ''\n"
            f"with pathlib.Path({str(log)!r}).open('a', encoding='utf-8') as handle:\n"
            f"    handle.write(json.dumps({{'command': {name!r}, 'args': args, 'stdin': stdin[:40]}}) + '\\n')\n"
            f"sys.exit({exit_code})\n",
        )

    def source_tree(self, root: Path) -> Path:
        source = root / "source dir"
        (source / "assets").mkdir(parents=True)
        (source / "configs").mkdir()
        (source / "dot_claude").mkdir()
        return source

    def render(self, hook: str, source: Path, env: dict[str, str], working_tree: Path | None = None) -> str:
        def configure(data: dict[str, object]) -> None:
            data["chezmoi"]["sourceDir"] = str(source)
            if working_tree is not None:
                data["chezmoi"]["workingTree"] = str(working_tree)

        return render_template(hook, "linux", configure, environment=env)

    def test_claude_mcp_wrapper_skips_absent_tools_and_dispatches_quoted_paths(self) -> None:
        with isolated_environment(prefix="mcp-wrapper-") as fixture:
            source = self.source_tree(fixture.root)
            env = {**fixture.env, "PATH": str(fixture.fake_bin)}
            log = fixture.root / "calls.jsonl"
            script = self.render(self.MCP, source, fixture.env)

            no_claude = run_script(script, fixture.root, env)
            write_fake_command(fixture.fake_bin, "claude", log_path=fixture.root / "claude.jsonl")
            no_python = run_script(script, fixture.root, env)
            self.write_python(fixture.fake_bin, "python3", log, exit_code=7)
            no_applier = run_script(script, fixture.root, env)
            (source / "assets" / "claude-mcp-apply.py").write_text("", encoding="utf-8")
            failed = run_script(script, fixture.root, env)

            self.assertEqual(no_claude.returncode, 0, no_claude.stderr)
            self.assertIn("claude command not found", no_claude.stdout)
            self.assertEqual(no_python.returncode, 0, no_python.stderr)
            self.assertIn("python3 command not found", no_python.stdout)
            self.assertEqual(no_applier.returncode, 1)
            self.assertIn("Claude MCP applier not found", no_applier.stdout)
            self.assertEqual(failed.returncode, 7)
            self.assertEqual([entry["args"] for entry in read_json_lines(log)], [[
                str(source / "assets" / "claude-mcp-apply.py"),
                str(source / "configs" / "claude-mcp.json"),
            ]])
            self.assertFalse((fixture.root / "claude.jsonl").exists())

    def test_git_template_wrapper_selects_python_and_propagates_failure(self) -> None:
        with isolated_environment(prefix="git-hook-wrapper-") as fixture:
            source = self.source_tree(fixture.root)
            working_tree = fixture.root / "working tree"
            env = {**fixture.env, "PATH": str(fixture.fake_bin)}
            log = fixture.root / "calls.jsonl"
            script = self.render(self.GIT_HOOKS, source, fixture.env, working_tree)

            missing = run_script(script, fixture.root, env)
            self.write_python(fixture.fake_bin, "python3", log, version_ok=False)
            too_old = run_script(script, fixture.root, env)
            self.write_python(fixture.fake_bin, "python", log, exit_code=3)
            failed = run_script(script, fixture.root, env)

            for result in (missing, too_old):
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("existing Git hooks were not synchronized", result.stderr)
            self.assertEqual(failed.returncode, 3)
            self.assertEqual(read_json_lines(log), [{
                "command": "python",
                "args": [str(source / "assets" / "git-template-hook-sync.py"),
                         "--template-hooks-dir", f"{fixture.home}/.config/git/template/hooks",
                         "--repo-root", str(working_tree),
                         "--repo-root", f"{fixture.home}/Documents/development",
                         "--repo-root", "/mnt/devdrive",
                         "--repo-root", "/Volumes/devdrive"],
                "stdin": "",
            }])

    def test_host_ai_wrapper_passes_quoted_paths_and_propagates_failure(self) -> None:
        with isolated_environment(prefix="host-ai-wrapper-") as fixture:
            source = self.source_tree(fixture.root)
            env = {**fixture.env, "PATH": str(fixture.fake_bin)}
            log = fixture.root / "calls.jsonl"
            script = self.render(self.HOST_AI, source, fixture.env)

            missing = run_script(script, fixture.root, env)
            self.write_python(fixture.fake_bin, "python3", log, exit_code=1)
            failed = run_script(script, fixture.root, env)

            self.assertEqual(missing.returncode, 0, missing.stderr)
            self.assertIn("python3 command not found", missing.stdout)
            self.assertEqual(failed.returncode, 1)
            calls = read_json_lines(log)
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0]["args"], [
                "-",
                str(source / "configs" / "host-ai-plugin-refresh.jsonc"),
                str(source / "dot_claude" / "settings-base.json"),
            ])
            self.assertTrue(calls[0]["stdin"].startswith("import json"), calls[0]["stdin"])


PACKAGES = ".chezmoiscripts/run_onchange_after_install_packages.sh.tmpl"

PACKAGE_TOOL = r'''
import json, os, pathlib, sys
root = pathlib.Path(os.environ['FIXTURE_ROOT'])
name = pathlib.Path(sys.argv[0]).name
args = sys.argv[1:]
leaked = sorted(key for key in ('ComSpec', 'COMSPEC', 'npm_config_script_shell', 'NPM_CONFIG_SCRIPT_SHELL')
                if key in os.environ)
with (root / 'calls.jsonl').open('a', encoding='utf-8') as handle:
    handle.write(json.dumps({'command': name, 'args': args, 'leaked': leaked}) + '\n')
if name == 'mise':
    if args == ['activate', 'bash']:
        print('export FIXTURE_MISE_ACTIVE=1')
    elif args[:1] == ['which']:
        managed = root / 'mise-tools' / args[1]
        if not managed.exists():
            sys.exit(1)
        print(managed)
    elif args not in (['install', '-y'], ['reshim']):
        sys.exit(97)
elif name == 'uv' and args == ['tool', 'list']:
    print('ruff v0.1.0')
if os.environ.get('TEST_FAIL') == name and args[-1:] != ['list']:
    sys.exit(9)
'''


@unittest.skipUnless(CHEZMOI and sys.platform != "win32", "chezmoi and a POSIX shell are required")
class PackageHydrationLifecycleTests(unittest.TestCase):
    def hydration(self, fixture, platform: str) -> tuple[str, dict[str, str]]:
        source = fixture.root / "source"
        configs = source / "configs"
        configs.mkdir(parents=True)
        (configs / "mise.toml").write_text("[tools]\n", encoding="utf-8")
        (configs / "npm_globals.txt").write_text("# comment\nfixture-npm\n\nsecond-npm\n", encoding="utf-8")
        (configs / "bun_globals.txt").write_text("fixture-bun\n", encoding="utf-8")
        (configs / "uv_tools.txt").write_text("ruff==0.1.0\nblack==24.1.0\n", encoding="utf-8")
        managed = fixture.root / "mise-tools"
        managed.mkdir()
        write_executable(fixture.fake_bin / "mise", f"#!{sys.executable}\n{PACKAGE_TOOL}")
        write_executable(fixture.fake_bin / "bun", f"#!{sys.executable}\n{PACKAGE_TOOL}")
        for tool in ("npm", "uv"):
            write_executable(managed / tool, f"#!{sys.executable}\n{PACKAGE_TOOL}")
        for tool in ("brew", "op", "sudo"):
            write_fake_command(fixture.fake_bin, tool, log_path=fixture.root / "forbidden.jsonl", exit_code=89)
        env = {
            **restricted_lifecycle_env(fixture.home, fixture.fake_bin),
            "FIXTURE_ROOT": str(fixture.root),
            "ComSpec": "C:\\fixture\\cmd.exe",
            "npm_config_script_shell": "/fixture/shell",
        }

        def configure(data: dict[str, object]) -> None:
            data["chezmoi"]["sourceDir"] = str(source)

        script = render_template(PACKAGES, platform, configure, environment=env)
        self.assertIn(f'CONFIG_DIR="{source}/configs"', script)
        script = redirect_exactly(script, "/opt/homebrew/bin", str(fixture.root / "absent-homebrew"), 2)
        script = redirect_exactly(script, "/usr/local/bin", str(fixture.root / "absent-local"), 2)
        return script, env

    def calls(self, fixture) -> list[dict[str, object]]:
        return read_json_lines(fixture.root / "calls.jsonl")

    def test_non_wsl_hydration_runs_only_manifest_commands_through_fakes(self) -> None:
        for platform in ("linux", "macos"):
            with self.subTest(platform), isolated_environment(prefix=f"hydrate-{platform}-") as fixture:
                script, env = self.hydration(fixture, platform)
                result = run_script(script, fixture.root, env)

                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("Hydration Complete.", result.stdout)
                calls = self.calls(fixture)
                actions = [(call["command"], call["args"]) for call in calls
                           if call["args"][:1] != ["which"]]
                self.assertEqual(actions, [
                    ("mise", ["activate", "bash"]),
                    ("mise", ["install", "-y"]),
                    ("mise", ["reshim"]),
                    ("npm", ["install", "-g", "fixture-npm"]),
                    ("npm", ["install", "-g", "second-npm"]),
                    ("bun", ["add", "-g", "fixture-bun"]),
                    ("uv", ["tool", "list"]),
                    ("uv", ["tool", "install", "black==24.1.0", "--force", "--no-build",
                            "--no-python-downloads"]),
                ])
                self.assertEqual([call for call in calls if call["leaked"]], [])
                self.assertFalse((fixture.root / "forbidden.jsonl").exists())

    def test_hydration_failures_stop_before_later_package_managers(self) -> None:
        with isolated_environment(prefix="hydrate-fail-") as fixture:
            script, env = self.hydration(fixture, "linux")
            failed = run_script(script, fixture.root, {**env, "TEST_FAIL": "npm"})

            self.assertNotEqual(failed.returncode, 0)
            self.assertNotIn("Hydration Complete.", failed.stdout)
            commands = [call["command"] for call in self.calls(fixture)]
            self.assertEqual(commands.count("npm"), 1)
            self.assertNotIn("bun", commands)
            self.assertNotIn("uv", commands)

    def test_hydration_requires_mise_and_uv(self) -> None:
        with isolated_environment(prefix="hydrate-missing-") as fixture:
            script, env = self.hydration(fixture, "linux")
            core = fixture.root / "core-bin"
            core.mkdir()
            for tool in ("env", "tr", "readlink"):
                located = shutil.which(tool, path="/usr/bin:/bin")
                self.assertIsNotNone(located, tool)
                (core / tool).symlink_to(located)
            (fixture.fake_bin / "mise").unlink()
            no_mise = run_script(script, fixture.root, {**env, "PATH": f"{fixture.fake_bin}:{core}"})
            write_executable(fixture.fake_bin / "mise", f"#!{sys.executable}\n{PACKAGE_TOOL}")
            (fixture.root / "mise-tools" / "uv").unlink()
            no_uv = run_script(script, fixture.root, {**env, "PATH": f"{fixture.fake_bin}:{core}"})

            self.assertEqual(no_mise.returncode, 1)
            self.assertIn("mise not found", no_mise.stdout)
            self.assertEqual(no_uv.returncode, 1)
            self.assertIn("uv binary not found", no_uv.stderr)
            self.assertNotIn("uv", [call["command"] for call in self.calls(fixture)])


if __name__ == "__main__":
    unittest.main()
