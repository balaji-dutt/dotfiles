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
            payload = b"fixture-plannotator"
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


if __name__ == "__main__":
    unittest.main()
