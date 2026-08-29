from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

from tests.support.fixtures import isolated_environment, read_json_lines, write_executable
from tests.test_chezmoi_lifecycle_render import CHEZMOI, render_matrix


REPO_ROOT = Path(__file__).resolve().parents[1]
BOOTSTRAP = REPO_ROOT / "bootstrap-wsl.sh"
PROVISION = ".chezmoiscripts/run_onchange_before_00-wsl-provision.sh.tmpl"


def write_recorder(
    path: Path,
    name: str,
    log_path: Path,
    *,
    exit_code: int = 0,
    stdout: str = "",
) -> Path:
    return write_executable(
        path,
        f"#!{sys.executable}\n"
        "import json, os, pathlib, sys\n"
        f"log = pathlib.Path({str(log_path)!r})\n"
        "log.parent.mkdir(parents=True, exist_ok=True)\n"
        "with log.open('a', encoding='utf-8') as handle:\n"
        f"    handle.write(json.dumps({{'name': {name!r}, 'argv': sys.argv[1:], "
        "'token_present': bool(os.environ.get('GITHUB_API_TOKEN'))}, sort_keys=True) + '\\n')\n"
        f"sys.stdout.write({stdout!r})\n"
        f"raise SystemExit({exit_code})\n",
    )


def run_bootstrap(
    arguments: list[str], env: dict[str, str]
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["/bin/bash", str(BOOTSTRAP), *arguments],
        cwd=REPO_ROOT,
        env=env,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=30,
    )


class BootstrapWslTests(unittest.TestCase):
    def install_fakes(
        self,
        fake_bin: Path,
        log: Path,
        *,
        ansible_installed: bool,
        chezmoi_installed: bool,
        sudo_exit: int = 0,
    ) -> None:
        write_recorder(fake_bin / "sudo", "sudo", log, exit_code=sudo_exit)
        write_recorder(fake_bin / "pipx", "pipx", log)
        write_recorder(fake_bin / "ansible-galaxy", "ansible-galaxy", log)
        write_recorder(fake_bin / "curl", "curl", log, stdout="installer")
        write_recorder(fake_bin / "sh", "sh", log)
        write_recorder(fake_bin / "mkdir", "mkdir", log)
        write_recorder(fake_bin / "login-shell", "login-shell", log)
        write_recorder(fake_bin / "bash", "bash", log)
        if ansible_installed:
            write_recorder(fake_bin / "ansible-playbook", "ansible-playbook", log)
        if chezmoi_installed:
            write_recorder(fake_bin / "chezmoi", "chezmoi", log)

    def test_missing_noninteractive_token_fails_before_commands(self) -> None:
        with isolated_environment(prefix="bootstrap-wsl-") as fixture:
            log = fixture.root / "calls.jsonl"
            self.install_fakes(
                fixture.fake_bin,
                log,
                ansible_installed=False,
                chezmoi_installed=False,
            )
            env = {**fixture.env, "PATH": str(fixture.fake_bin)}
            env.pop("GITHUB_API_TOKEN", None)

            result = run_bootstrap([], env)

            self.assertEqual(result.returncode, 1)
            self.assertIn("no TTY is available", result.stdout)
            self.assertFalse(log.exists())

    def test_missing_tools_are_installed_and_source_with_spaces_is_initialized(self) -> None:
        with isolated_environment(prefix="bootstrap-wsl-") as fixture:
            log = fixture.root / "calls.jsonl"
            self.install_fakes(
                fixture.fake_bin,
                log,
                ansible_installed=False,
                chezmoi_installed=False,
            )
            source = fixture.root / "source with spaces"
            source.mkdir()
            installed_chezmoi = (
                f"#!{sys.executable}\n"
                "import json, os, pathlib, sys\n"
                f"log = pathlib.Path({str(log)!r})\n"
                "with log.open('a', encoding='utf-8') as handle:\n"
                "    handle.write(json.dumps({'name': 'chezmoi', 'argv': sys.argv[1:], "
                "'token_present': bool(os.environ.get('GITHUB_API_TOKEN'))}, "
                "sort_keys=True) + '\\n')\n"
            )
            write_executable(
                fixture.fake_bin / "sh",
                f"#!{sys.executable}\n"
                "import json, os, pathlib, sys\n"
                f"log = pathlib.Path({str(log)!r})\n"
                "with log.open('a', encoding='utf-8') as handle:\n"
                "    handle.write(json.dumps({'name': 'sh', 'argv': sys.argv[1:], "
                "'token_present': bool(os.environ.get('GITHUB_API_TOKEN'))}, "
                "sort_keys=True) + '\\n')\n"
                f"target = pathlib.Path({str(fixture.home / '.local/bin/chezmoi')!r})\n"
                "target.parent.mkdir(parents=True, exist_ok=True)\n"
                f"target.write_text({installed_chezmoi!r}, encoding='utf-8')\n"
                "target.chmod(0o755)\n",
            )
            token = "fixture-token-not-a-secret"
            env = {
                **fixture.env,
                "PATH": str(fixture.fake_bin),
                "GITHUB_API_TOKEN": token,
                "SHELL": str(fixture.fake_bin / "login-shell"),
            }

            result = run_bootstrap([str(source)], env)
            calls = read_json_lines(log)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn(token, result.stdout + result.stderr + log.read_text(encoding="utf-8"))
            self.assertTrue(all(call["token_present"] for call in calls))
            self.assertEqual(
                [call["name"] for call in calls],
                [
                    "sudo",
                    "sudo",
                    "pipx",
                    "pipx",
                    "pipx",
                    "ansible-galaxy",
                    "pipx",
                    "curl",
                    "sh",
                    "mkdir",
                    "chezmoi",
                    "login-shell",
                ],
            )
            self.assertEqual(calls[0]["argv"], ["apt", "update"])
            self.assertEqual(calls[1]["argv"][:3], ["apt", "install", "-y"])
            self.assertEqual(calls[3]["argv"], ["install", "--include-deps", "ansible"])
            self.assertEqual(calls[5]["argv"][-1], str(source / "ansible" / "requirements.yml"))
            self.assertEqual(calls[8]["argv"][-2:], ["-b", str(fixture.home / ".local/bin")])

    def test_installed_tools_and_existing_config_skip_install_and_init(self) -> None:
        with isolated_environment(prefix="bootstrap-wsl-") as fixture:
            log = fixture.root / "calls.jsonl"
            self.install_fakes(
                fixture.fake_bin,
                log,
                ansible_installed=True,
                chezmoi_installed=True,
            )
            source = fixture.root / "source"
            source.mkdir()
            config = fixture.home / ".config" / "chezmoi" / "chezmoi.toml"
            config.parent.mkdir(parents=True)
            config.write_text("", encoding="utf-8")
            env = {
                **fixture.env,
                "PATH": str(fixture.fake_bin),
                "GITHUB_API_TOKEN": "fixture-token",
                "SHELL": str(fixture.fake_bin / "login-shell"),
            }

            result = run_bootstrap([str(source)], env)
            calls = read_json_lines(log)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("Ansible already installed", result.stdout)
            self.assertIn("chezmoi already installed", result.stdout)
            self.assertIn("chezmoi already initialized", result.stdout)
            pipx_calls = [call["argv"] for call in calls if call["name"] == "pipx"]
            self.assertNotIn(["install", "--include-deps", "ansible"], pipx_calls)
            self.assertFalse(any(call["name"] == "curl" for call in calls))
            self.assertFalse(any(call["name"] == "chezmoi" for call in calls))

    def test_package_failure_stops_before_later_commands(self) -> None:
        with isolated_environment(prefix="bootstrap-wsl-") as fixture:
            log = fixture.root / "calls.jsonl"
            self.install_fakes(
                fixture.fake_bin,
                log,
                ansible_installed=False,
                chezmoi_installed=False,
                sudo_exit=23,
            )
            env = {
                **fixture.env,
                "PATH": str(fixture.fake_bin),
                "GITHUB_API_TOKEN": "fixture-token",
            }

            result = run_bootstrap([], env)
            calls = read_json_lines(log)

            self.assertEqual(result.returncode, 23)
            self.assertEqual([call["name"] for call in calls], ["sudo"])

    def test_missing_source_fails_and_unset_shell_uses_bash_fallback(self) -> None:
        with isolated_environment(prefix="bootstrap-wsl-") as fixture:
            log = fixture.root / "calls.jsonl"
            self.install_fakes(
                fixture.fake_bin,
                log,
                ansible_installed=True,
                chezmoi_installed=True,
            )
            env = {
                **fixture.env,
                "PATH": str(fixture.fake_bin),
                "GITHUB_API_TOKEN": "fixture-token",
            }
            env.pop("SHELL", None)

            missing = run_bootstrap([str(fixture.root / "missing")], env)
            source = fixture.root / "source"
            source.mkdir()
            config = fixture.home / ".config" / "chezmoi" / "chezmoi.toml"
            config.parent.mkdir(parents=True)
            config.write_text("", encoding="utf-8")
            fallback = run_bootstrap([str(source)], env)

            self.assertEqual(missing.returncode, 1)
            self.assertIn("Dotfiles repo not found", missing.stdout)
            self.assertEqual(fallback.returncode, 0, fallback.stderr)
            self.assertIn("Starting a new login shell", fallback.stdout)
            self.assertFalse(
                any(call["name"] == "login-shell" for call in read_json_lines(log))
            )


@unittest.skipUnless(CHEZMOI, "chezmoi is required")
class WslProvisionLifecycleTests(unittest.TestCase):
    def run_provision(
        self,
        content: str,
        root: Path,
        env: dict[str, str],
    ) -> subprocess.CompletedProcess[str]:
        script = root / "provision.sh"
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

    def test_portable_linux_is_a_noop_and_missing_ansible_fails(self) -> None:
        with isolated_environment(prefix="wsl-provision-") as fixture:
            env = {**fixture.env, "PATH": str(fixture.fake_bin)}
            portable = self.run_provision(
                render_matrix()[("linux", PROVISION)].decode(), fixture.root, env
            )
            missing = self.run_provision(
                render_matrix()[("wsl2", PROVISION)].decode(), fixture.root, env
            )

            self.assertEqual(portable.returncode, 0, portable.stderr)
            self.assertNotIn("WSL2 Provisioning", portable.stdout)
            self.assertEqual(missing.returncode, 1)
            self.assertIn("Ansible not found", missing.stdout)

    def test_ansible_arguments_are_exact_and_rerun_is_stable(self) -> None:
        with isolated_environment(prefix="wsl-provision-") as fixture:
            log = fixture.root / "ansible.jsonl"
            write_recorder(
                fixture.fake_bin / "ansible-playbook",
                "ansible-playbook",
                log,
            )
            env = {**fixture.env, "PATH": str(fixture.fake_bin)}
            script = render_matrix()[("wsl2", PROVISION)].decode()

            first = self.run_provision(script, fixture.root, env)
            second = self.run_provision(script, fixture.root, env)
            calls = read_json_lines(log)

            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertEqual(len(calls), 2)
            self.assertEqual(calls[0]["argv"], calls[1]["argv"])
            arguments = calls[0]["argv"]
            self.assertEqual(arguments[0], str(REPO_ROOT / "ansible" / "wsl-playbook.yml"))
            self.assertIn("--ask-become-pass", arguments)
            self.assertIn("distro_id=debian", arguments)
            self.assertIn("distro_version=fixture-1", arguments)
            self.assertIn("beads_version=1.2.3-fixture", arguments)
            self.assertIn("op_url=https://fixture.example.invalid", arguments)

    def test_ansible_failure_propagates(self) -> None:
        with isolated_environment(prefix="wsl-provision-") as fixture:
            log = fixture.root / "ansible.jsonl"
            write_recorder(
                fixture.fake_bin / "ansible-playbook",
                "ansible-playbook",
                log,
                exit_code=29,
            )
            env = {**fixture.env, "PATH": str(fixture.fake_bin)}

            result = self.run_provision(
                render_matrix()[("wsl2", PROVISION)].decode(), fixture.root, env
            )

            self.assertEqual(result.returncode, 29)
            self.assertEqual(len(read_json_lines(log)), 1)


if __name__ == "__main__":
    unittest.main()
