from __future__ import annotations

import shutil
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
from tests.test_chezmoi_lifecycle_render import CHEZMOI, render_template
from tests.test_shell_lifecycle import restricted_lifecycle_env


GITIGNORE = ".chezmoiscripts/run_copy_win_gitignore.sh.tmpl"
OVERLAY = ".chezmoiscripts/run_after_50-publish-devcontainer-overlays-wsl.sh.tmpl"
GIT_TEMPLATE = ".chezmoiscripts/run_after_configure_git_templates.sh.tmpl"
CLEANUP = ".chezmoiscripts/run_once_after_99-cleanup-wrong-apply.sh.tmpl"
PACKAGES = ".chezmoiscripts/run_onchange_after_install_packages.sh.tmpl"
HOST_MOUNTS = ("/mnt/c", "/mnt/devdrive")


def confined(root: Path, target: Path) -> Path:
    root = root.resolve(strict=True)
    target = target.resolve(strict=False)
    if root not in target.parents:
        raise AssertionError(f"path escapes fixture: {target}")
    return target


def redirect(script: str, original: str, replacement: str, root: Path, target: Path) -> str:
    if script.count(original) != 1:
        raise AssertionError(f"expected exactly one host path occurrence: {original}")
    if any(mount in replacement for mount in HOST_MOUNTS):
        raise AssertionError("replacement still uses a host mount")
    script = script.replace(original, replacement)
    if any(mount in script for mount in HOST_MOUNTS):
        raise AssertionError("unredirected host path in executable script")
    if str(target) not in replacement:
        raise AssertionError("replacement does not name fixture target")
    confined(root, target)
    return script


def run_script(content: str, root: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    script = root / "lifecycle.sh"
    script.write_text(content, encoding="utf-8")
    return subprocess.run(
        [shutil.which("bash") or "/bin/bash", str(script)],
        cwd=root,
        env=env,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=30,
    )


@unittest.skipUnless(CHEZMOI and shutil.which("bash"), "Bash and chezmoi are required")
class WslLifecycleBoundaryTests(unittest.TestCase):
    def test_redirect_rejects_drift_and_symlink_escape(self) -> None:
        with isolated_environment(prefix="wsl-boundary-") as fixture:
            root = fixture.root
            original = "repo_dir=\"/mnt/devdrive/$repo_name\""
            replacement = f'repo_dir="{root}/devdrive/$repo_name"'
            with self.assertRaises(AssertionError):
                redirect("exit 0", original, replacement, root, root / "devdrive")
            with self.assertRaises(AssertionError):
                redirect(f"{original}\n{original}", original, replacement, root, root / "devdrive")
            for extra in ('repo_dir="/mnt/devdrive"', "cp file /mnt/c"):
                with self.subTest(extra=extra), self.assertRaises(AssertionError):
                    redirect(f"{original}\n{extra}", original, replacement, root, root / "devdrive")
            (root / "escape").symlink_to(root.parent, target_is_directory=True)
            with self.assertRaises(AssertionError):
                redirect(original, original, f'repo_dir="{root}/escape/$repo_name"', root, root / "escape")

    def test_unsupported_gitignore_and_package_hydration_are_inert(self) -> None:
        with isolated_environment(prefix="wsl-boundary-") as fixture:
            forbidden = fixture.root / "forbidden.jsonl"
            for name in ("cp", "chezmoi", "curl", "brew", "apt", "npm"):
                write_fake_command(fixture.fake_bin, name, log_path=forbidden, exit_code=95)
            for platform in ("linux", "macos", "windows"):
                with self.subTest(platform=platform):
                    script = render_template(GITIGNORE, platform)
                    self.assertEqual(script, "")
            self.assertEqual(render_template(PACKAGES, "wsl2"), "")
            self.assertFalse(forbidden.exists())

    def test_gitignore_copy_and_failure_are_confined(self) -> None:
        with isolated_environment(prefix="wsl-boundary-") as fixture:
            source = fixture.root / "source" / "dot_local" / "config" / "executable_gitignore_global.txt"
            source.parent.mkdir(parents=True)
            source.write_bytes(b"fixture gitignore\n")
            destination = fixture.root / "windows" / "Users" / "Fixture" / "gitignore_global.txt"
            destination.parent.mkdir(parents=True)
            sibling = destination.parent / "keep.txt"
            sibling.write_text("keep", encoding="utf-8")
            for target in (source, destination, sibling):
                confined(fixture.root, target)

            def configure(data: dict[str, object]) -> None:
                data["chezmoi"]["workingTree"] = str(fixture.root / "source")

            rendered = render_template(GITIGNORE, "wsl2", configure)
            original = "/mnt/c/Users/Balaji/gitignore_global.txt"
            script = redirect(rendered, original, str(destination), fixture.root, destination)
            log = fixture.root / "copy.jsonl"
            write_executable(
                fixture.fake_bin / "cp",
                f"#!{sys.executable}\n"
                "import json, pathlib, shutil, sys\n"
                f"log = pathlib.Path({str(log)!r})\n"
                "with log.open('a', encoding='utf-8') as handle:\n"
                "    handle.write(json.dumps(sys.argv[1:]) + '\\n')\n"
                "shutil.copyfile(sys.argv[1], sys.argv[2])\n",
            )
            env = restricted_lifecycle_env(fixture.home, fixture.fake_bin)
            for _ in range(2):
                result = run_script(script, fixture.root, env)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(destination.read_bytes(), source.read_bytes())
            self.assertEqual(read_json_lines(log), [[str(source), str(destination)]] * 2)
            self.assertEqual(sibling.read_text(encoding="utf-8"), "keep")

            source.unlink()
            missing = run_script(script, fixture.root, env)
            self.assertNotEqual(missing.returncode, 0)
            self.assertEqual(destination.read_bytes(), b"fixture gitignore\n")
            source.write_bytes(b"replacement\n")
            write_fake_command(fixture.fake_bin, "cp", log_path=log, exit_code=27, stderr="copy denied\n")
            denied = run_script(script, fixture.root, env)
            self.assertEqual(denied.returncode, 27)
            self.assertIn("copy denied", denied.stderr)
            self.assertEqual(destination.read_bytes(), b"fixture gitignore\n")

    def test_overlay_platform_and_distro_guards(self) -> None:
        with isolated_environment(prefix="wsl-boundary-") as fixture:
            forbidden = fixture.root / "forbidden.jsonl"
            for name in ("chezmoi", "install", "mkdir", "mktemp"):
                write_fake_command(fixture.fake_bin, name, log_path=forbidden, exit_code=95)
            env = restricted_lifecycle_env(fixture.home, fixture.fake_bin)
            for platform in ("linux", "macos", "windows", "wsl2"):
                with self.subTest(platform=platform):
                    script = render_template(OVERLAY, platform)
                    case_env = env if platform == "wsl2" else {**env, "WSL_DISTRO_NAME": "FixtureDistro"}
                    self.assertEqual(run_script(script, fixture.root, case_env).returncode, 0)

            def ubuntu(data: dict[str, object]) -> None:
                data["chezmoi"]["osRelease"]["id"] = "ubuntu"
                data["isDebianWSL2"] = False
                data["isUbuntuWSL2"] = True

            ubuntu_script = render_template(OVERLAY, "wsl2", ubuntu)
            self.assertEqual(run_script(ubuntu_script, fixture.root, {**env, "WSL_DISTRO_NAME": "Ubuntu"}).returncode, 0)
            self.assertFalse(forbidden.exists())

    def test_overlay_scoped_publish_rerun_and_failures(self) -> None:
        with isolated_environment(prefix="wsl-boundary-") as fixture:
            source = fixture.root / "source"
            overlays = source / "private_Documents" / "development" / "container-dotfiles" / "devcontainers"
            mount = fixture.root / "devdrive"
            for repo_name in ("alpha", "beta", "unrelated"):
                (mount / repo_name).mkdir(parents=True)
            (mount / "alpha" / ".devcontainer").mkdir()
            (mount / "unrelated" / ".devcontainer").mkdir()
            for repo_name in ("alpha", "beta"):
                template = overlays / repo_name / "dot_devcontainer" / "devcontainer.json.tmpl"
                template.parent.mkdir(parents=True)
                template.write_text(f'{{"fixture":"{repo_name}"}}\n', encoding="utf-8")
                confined(fixture.root, template)
            target = mount / "alpha" / ".devcontainer" / "personal-wsl" / "devcontainer.json"
            untouched = mount / "unrelated" / ".devcontainer" / "keep.json"
            untouched.write_text("keep", encoding="utf-8")
            for path in (
                mount, target, untouched, fixture.home,
                mount / "beta" / ".devcontainer" / "personal-wsl" / "devcontainer.json",
            ):
                confined(fixture.root, path)

            def configure(data: dict[str, object]) -> None:
                data["chezmoi"]["sourceDir"] = str(source)

            rendered = render_template(OVERLAY, "wsl2", configure)
            original = 'repo_dir="/mnt/devdrive/$repo_name"'
            script = redirect(rendered, original, f'repo_dir="{mount}/$repo_name"', fixture.root, mount)
            render_log = fixture.root / "render.jsonl"
            install_log = fixture.root / "install.jsonl"
            write_executable(
                fixture.fake_bin / "chezmoi",
                f"#!{sys.executable}\n"
                "import json, pathlib, sys\n"
                f"log = pathlib.Path({str(render_log)!r})\n"
                "with log.open('a', encoding='utf-8') as handle:\n"
                "    handle.write(json.dumps(sys.argv[1:]) + '\\n')\n"
                "sys.stdout.write(pathlib.Path(sys.argv[3]).read_text(encoding='utf-8'))\n",
            )
            write_executable(
                fixture.fake_bin / "install",
                f"#!{sys.executable}\n"
                "import json, pathlib, shutil, sys\n"
                f"log = pathlib.Path({str(install_log)!r})\n"
                "with log.open('a', encoding='utf-8') as handle:\n"
                "    handle.write(json.dumps(sys.argv[1:]) + '\\n')\n"
                "shutil.copyfile(sys.argv[-2], sys.argv[-1])\n",
            )
            env = {**restricted_lifecycle_env(fixture.home, fixture.fake_bin), "WSL_DISTRO_NAME": "FixtureDistro"}
            first = run_script(script, fixture.root, env)
            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertEqual(target.read_text(encoding="utf-8"), '{"fixture":"alpha"}\n')
            self.assertFalse((mount / "beta" / ".devcontainer").exists())
            self.assertEqual(untouched.read_text(encoding="utf-8"), "keep")
            self.assertEqual(len(read_json_lines(install_log)), 1)
            self.assertEqual(read_json_lines(render_log), [[
                "execute-template", "--file",
                str(overlays / "alpha" / "dot_devcontainer" / "devcontainer.json.tmpl"),
            ]])
            self.assertEqual(read_json_lines(install_log)[0][-1], str(target))
            before = target.stat().st_mtime_ns
            second = run_script(script, fixture.root, env)
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertEqual(target.stat().st_mtime_ns, before)
            self.assertEqual(len(read_json_lines(install_log)), 1)

            write_fake_command(fixture.fake_bin, "chezmoi", log_path=render_log, exit_code=26, stderr="render denied\n")
            failed_render = run_script(script, fixture.root, env)
            self.assertEqual(failed_render.returncode, 26)
            self.assertIn("render denied", failed_render.stderr)
            self.assertEqual(len(read_json_lines(install_log)), 1)
            self.assertEqual(target.read_text(encoding="utf-8"), '{"fixture":"alpha"}\n')

            write_fake_command(fixture.fake_bin, "chezmoi", log_path=render_log, stdout='{"fixture":"updated"}\n')
            write_fake_command(fixture.fake_bin, "install", log_path=install_log, exit_code=28, stderr="install denied\n")
            failed_install = run_script(script, fixture.root, env)
            self.assertEqual(failed_install.returncode, 28)
            self.assertIn("install denied", failed_install.stderr)
            self.assertEqual(target.read_text(encoding="utf-8"), '{"fixture":"alpha"}\n')
            self.assertEqual(untouched.read_text(encoding="utf-8"), "keep")

    def test_git_template_wsl_scoped_config_and_failure(self) -> None:
        with isolated_environment(prefix="wsl-boundary-") as fixture:
            repo = fixture.root / "source"

            def configure(data: dict[str, object]) -> None:
                data["chezmoi"]["sourceDir"] = str(repo)

            script = render_template(GIT_TEMPLATE, "wsl2", configure)
            env = restricted_lifecycle_env(fixture.home, fixture.fake_bin)
            self.assertEqual(run_script(script, fixture.root, env).returncode, 0)
            init_git_repository(repo, env=env)
            self.assertEqual(run_script(script, fixture.root, env).returncode, 0)
            (repo / ".gitmessage").write_text("fixture\n", encoding="utf-8")
            applied = run_script(script, fixture.root, env)
            self.assertEqual(applied.returncode, 0, applied.stderr)
            self.assertEqual(run_git(repo, "config", "--local", "--get", "commit.template", env=env).stdout.strip(), ".gitmessage")
            log = fixture.root / "git.jsonl"
            write_fake_command(fixture.fake_bin, "git", log_path=log, exit_code=29, stderr="git denied\n")
            failed = run_script(script, fixture.root, env)
            self.assertEqual(failed.returncode, 29)
            self.assertIn("git denied", failed.stderr)
            self.assertEqual(len(read_json_lines(log)), 1)

    def test_cleanup_debian_and_non_debian_wsl_scope(self) -> None:
        for distro in ("debian", "ubuntu"):
            with self.subTest(distro=distro), isolated_environment(prefix="wsl-boundary-") as fixture:
                home = fixture.home
                root = home / "Documents" / "development" / "container-dotfiles"
                root.mkdir(parents=True)
                (root / "keep.txt").write_text("fixture", encoding="utf-8")
                (home / "keep.txt").write_text("keep", encoding="utf-8")

                def configure(data: dict[str, object]) -> None:
                    data["chezmoi"]["destDir"] = str(home)
                    data["chezmoi"]["osRelease"]["id"] = distro
                    data["isDebianWSL2"] = distro == "debian"
                    data["isUbuntuWSL2"] = distro == "ubuntu"

                script = render_template(CLEANUP, "wsl2", configure)
                result = run_script(script, fixture.root, restricted_lifecycle_env(home, fixture.fake_bin))
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(root.exists(), distro == "debian")
                self.assertEqual((home / "keep.txt").read_text(encoding="utf-8"), "keep")


if __name__ == "__main__":
    unittest.main()
