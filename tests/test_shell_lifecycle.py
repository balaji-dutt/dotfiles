from __future__ import annotations

import hashlib
import subprocess
import sys
import unittest
from pathlib import Path

from tests.support.fixtures import (
    init_git_repository,
    isolated_environment,
    read_json_lines,
    run_git,
    write_executable,
    write_fake_command,
)
from tests.test_chezmoi_lifecycle_render import CHEZMOI, render_matrix, render_template


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


def restricted_lifecycle_env(home: Path, fake_bin: Path) -> dict[str, str]:
    return {
        "HOME": str(home),
        "PATH": f"{fake_bin}:/usr/bin:/bin",
        "TMPDIR": str(home),
        "LC_ALL": "C",
        "CHEZMOI_NO_TTY": "1",
    }


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
            for command in ("brew", "curl", "op", "sudo", "hdiutil", "mise", "npm", "launchctl", "open"):
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


if __name__ == "__main__":
    unittest.main()
