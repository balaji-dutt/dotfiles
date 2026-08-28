from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

from tests.support.fixtures import isolated_environment, write_executable


REPO_ROOT = Path(__file__).resolve().parents[1]
BIN = REPO_ROOT / "bin"


def run_script(
    script: Path,
    *args: str,
    env: dict[str, str],
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["/bin/bash", str(script), *args],
        env=env,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def write_env_logger(path: Path, log_path: Path) -> Path:
    return write_executable(
        path,
        f"#!{sys.executable}\n"
        "import json, os, pathlib, sys\n"
        f"path = pathlib.Path({str(log_path)!r})\n"
        "path.parent.mkdir(parents=True, exist_ok=True)\n"
        "keys = [\n"
        "    'GIT_AUTHOR_NAME', 'GIT_AUTHOR_EMAIL', 'GIT_COMMITTER_NAME',\n"
        "    'GIT_COMMITTER_EMAIL', 'PLANNOTATOR_PORT',\n"
        "    'CLAUDE_PLANNOTATOR_POOL', 'OPENCODE_PLANNOTATOR_POOL',\n"
        "    'ANTHROPIC_SYSTEM_PROMPT_PATH',\n"
        "    'OPENCODE_DISABLE_CLAUDE_CODE_PROMPT',\n"
        "    'OPENCODE_DISABLE_CLAUDE_CODE_SKILLS',\n"
        "]\n"
        "payload = {'argv': sys.argv[1:], 'env': {key: os.environ.get(key) for key in keys}}\n"
        "path.write_text(json.dumps(payload), encoding='utf-8')\n"
        "raise SystemExit(int(os.environ.get('FAKE_EXIT', '0')))\n",
    )


class CommitWrapperTests(unittest.TestCase):
    def test_wrappers_forward_identity_arguments_and_exit_status(self) -> None:
        cases = (
            ("executable_cc-commit", "Claude", "noreply@anthropic.com"),
            ("executable_oc-commit", "OpenCode", "noreply@opencode.ai"),
        )
        for filename, identity, email in cases:
            with self.subTest(filename=filename), isolated_environment(
                prefix="commit wrapper "
            ) as fixture:
                log = fixture.root / "git.json"
                write_env_logger(fixture.fake_bin / "git", log)
                env = fixture.env | {"FAKE_EXIT": "27"}

                result = run_script(
                    BIN / filename,
                    "-m",
                    "subject with spaces",
                    "--",
                    "path;literal",
                    env=env,
                )

                self.assertEqual(result.returncode, 27)
                payload = json.loads(log.read_text(encoding="utf-8"))
                self.assertEqual(
                    payload["argv"],
                    ["commit", "-m", "subject with spaces", "--", "path;literal"],
                )
                self.assertEqual(payload["env"]["GIT_AUTHOR_NAME"], identity)
                self.assertEqual(payload["env"]["GIT_COMMITTER_NAME"], identity)
                self.assertEqual(payload["env"]["GIT_AUTHOR_EMAIL"], email)
                self.assertEqual(payload["env"]["GIT_COMMITTER_EMAIL"], email)

    def test_help_does_not_invoke_git(self) -> None:
        with isolated_environment(prefix="commit help ") as fixture:
            marker = fixture.root / "git-called"
            write_executable(
                fixture.fake_bin / "git",
                f"#!/bin/sh\ntouch {str(marker)!r}\nexit 99\n",
            )
            for filename in ("executable_cc-commit", "executable_oc-commit"):
                result = run_script(BIN / filename, "--help", env=fixture.env)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("git commit", result.stdout)
            self.assertFalse(marker.exists())


class CodeWrapperTests(unittest.TestCase):
    def test_remote_terminal_uses_newest_executable_cli_and_preserves_args(self) -> None:
        with isolated_environment(prefix="code remote ") as fixture:
            old = fixture.home / ".vscode-server/bin/old/bin/remote-cli/code"
            new = fixture.home / ".vscode-server/bin/new/bin/remote-cli/code"
            old_log = fixture.root / "old.json"
            new_log = fixture.root / "new.json"
            write_env_logger(old, old_log)
            write_env_logger(new, new_log)
            os.utime(old.parent, (1, 1))
            os.utime(new.parent, (2, 2))

            result = run_script(
                BIN / "executable_code",
                "folder with spaces",
                "--reuse-window",
                env=fixture.env | {"VSCODE_IPC_HOOK_CLI": "active"},
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(old_log.exists())
            payload = json.loads(new_log.read_text(encoding="utf-8"))
            self.assertEqual(payload["argv"], ["folder with spaces", "--reuse-window"])

    def test_fallback_override_is_literal_and_propagates_failure(self) -> None:
        with isolated_environment(prefix="code fallback ") as fixture:
            log = fixture.root / "code.json"
            fallback = write_env_logger(fixture.root / "Windows Code/code", log)
            result = run_script(
                BIN / "executable_code",
                "name;not-shell",
                env=fixture.env | {"CODE_WINDOWS_CLI": str(fallback), "FAKE_EXIT": "31"},
            )

            self.assertEqual(result.returncode, 31)
            payload = json.loads(log.read_text(encoding="utf-8"))
            self.assertEqual(payload["argv"], ["name;not-shell"])


class PlannotatorWrapperTests(unittest.TestCase):
    @staticmethod
    def clean_port_environment(env: dict[str, str]) -> dict[str, str]:
        cleaned = env.copy()
        for key in (
            "DEVCONTAINER",
            "PLANNOTATOR_PORTS_BUILD",
            "PLANNOTATOR_PORTS_CLAUDE",
            "PLANNOTATOR_PORTS_CUSTOM",
        ):
            cleaned.pop(key, None)
        return cleaned

    def render(self, fixture, source_name: str, target_name: str) -> Path:
        replacements = {
            "{{ .plannotator_ports.host.claude }}": "4100-4109",
            "{{ .plannotator_ports.devcontainer.claude }}": "5100-5109",
            "{{ .plannotator_ports.host.build }}": "4200-4209",
            "{{ .plannotator_ports.host.custom }}": "4300-4309",
            "{{ .plannotator_ports.devcontainer.build }}": "5200-5209",
            "{{ .plannotator_ports.devcontainer.custom }}": "5300-5309",
        }
        content = (BIN / source_name).read_text(encoding="utf-8")
        for token, value in replacements.items():
            content = content.replace(token, value)
        self.assertNotIn("{{", content)
        return write_executable(fixture.root / target_name, content)

    def test_claude_host_and_devcontainer_ranges_and_override(self) -> None:
        with isolated_environment(prefix="claude plannotator ") as fixture:
            wrapper = self.render(
                fixture, "executable_claude-plannotator.tmpl", "claude-plannotator"
            )
            agent_log = fixture.root / "claude.json"
            agent = write_env_logger(fixture.root / "agent tools/claude", agent_log)
            base_env = self.clean_port_environment(fixture.env)
            cases = (
                ({}, "4100-4109"),
                ({"DEVCONTAINER": "1"}, "5100-5109"),
                ({"PLANNOTATOR_PORTS_CLAUDE": "6100-6101"}, "6100-6101"),
            )
            for extra, expected in cases:
                with self.subTest(extra=extra):
                    result = run_script(
                        wrapper,
                        "--model",
                        "name with spaces",
                        env=base_env | extra | {"CLAUDE_BIN": str(agent)},
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
                    payload = json.loads(agent_log.read_text(encoding="utf-8"))
                    self.assertEqual(payload["argv"], ["--model", "name with spaces"])
                    self.assertEqual(payload["env"]["PLANNOTATOR_PORT"], expected)
                    self.assertEqual(payload["env"]["CLAUDE_PLANNOTATOR_POOL"], "claude")

    def test_opencode_build_and_custom_profiles_export_guard_environment(self) -> None:
        with isolated_environment(prefix="opencode plannotator ") as fixture:
            agent_log = fixture.root / "opencode.json"
            agent = write_env_logger(fixture.root / "agent tools/opencode", agent_log)
            base_env = self.clean_port_environment(fixture.env)
            cases = (
                ("executable_opencode-plannotator.tmpl", "opencode-plannotator", "build", "4200-4209"),
                (
                    "executable_opencode-plannotator-custom.tmpl",
                    "opencode-plannotator-custom",
                    "custom",
                    "4300-4309",
                ),
            )
            for source, target, profile, port_range in cases:
                with self.subTest(profile=profile):
                    wrapper = self.render(fixture, source, target)
                    result = run_script(
                        wrapper,
                        "--agent",
                        "build agent",
                        env=base_env | {"OPENCODE_BIN": str(agent), "FAKE_EXIT": "23"},
                    )
                    self.assertEqual(result.returncode, 23)
                    payload = json.loads(agent_log.read_text(encoding="utf-8"))
                    self.assertEqual(payload["argv"], ["--agent", "build agent"])
                    self.assertEqual(payload["env"]["PLANNOTATOR_PORT"], port_range)
                    self.assertEqual(payload["env"]["OPENCODE_PLANNOTATOR_POOL"], profile)
                    self.assertEqual(payload["env"]["ANTHROPIC_SYSTEM_PROMPT_PATH"], "/dev/null")
                    self.assertEqual(payload["env"]["OPENCODE_DISABLE_CLAUDE_CODE_PROMPT"], "1")
                    self.assertEqual(payload["env"]["OPENCODE_DISABLE_CLAUDE_CODE_SKILLS"], "1")

    def test_missing_and_nonexecutable_agents_fail_without_leaking_arguments(self) -> None:
        with isolated_environment(prefix="plannotator failure ") as fixture:
            wrapper = self.render(
                fixture, "executable_opencode-plannotator.tmpl", "opencode-plannotator"
            )
            env = fixture.env | {"PATH": "/usr/bin:/bin"}
            missing = run_script(wrapper, "synthetic-secret", env=env)
            self.assertEqual(missing.returncode, 1)
            self.assertIn("opencode not found", missing.stderr)
            self.assertNotIn("synthetic-secret", missing.stderr)

            nonexec = fixture.root / "not executable"
            nonexec.write_text("no\n", encoding="utf-8")
            refused = run_script(wrapper, env=env | {"OPENCODE_BIN": str(nonexec)})
            self.assertEqual(refused.returncode, 1)
            self.assertIn("is not executable", refused.stderr)


class CcrLauncherTests(unittest.TestCase):
    def render(self, fixture) -> Path:
        content = (BIN / "executable_ccr_launcher.sh.tmpl").read_text(encoding="utf-8")
        return write_executable(
            fixture.root / "ccr-launcher",
            content.replace("{{ .ccr_port }}", "4312"),
        )

    def test_start_is_forwarded_and_failure_is_logged(self) -> None:
        with isolated_environment(prefix="ccr launcher ") as fixture:
            wrapper = self.render(fixture)
            ccr_log = fixture.root / "ccr-calls.jsonl"
            write_executable(
                fixture.fake_bin / "lsof",
                "#!/bin/sh\nexit 1\n",
            )
            write_executable(
                fixture.fake_bin / "ccr",
                f"#!{sys.executable}\n"
                "import json, pathlib, sys\n"
                f"path = pathlib.Path({str(ccr_log)!r})\n"
                "path.write_text(json.dumps(sys.argv[1:]), encoding='utf-8')\n"
                "raise SystemExit(17)\n",
            )

            result = run_script(wrapper, env=fixture.env)

            self.assertEqual(result.returncode, 1)
            self.assertEqual(json.loads(ccr_log.read_text(encoding="utf-8")), ["start"])
            launcher_log = fixture.home / ".claude-code-router/logs/ccr-launcher.err"
            self.assertIn("ccr start failed", launcher_log.read_text(encoding="utf-8"))

    def test_bound_port_skips_start_idempotently(self) -> None:
        with isolated_environment(prefix="ccr idempotent ") as fixture:
            wrapper = self.render(fixture)
            marker = fixture.root / "ccr-called"
            write_executable(fixture.fake_bin / "lsof", "#!/bin/sh\nexit 0\n")
            write_executable(
                fixture.fake_bin / "ccr",
                f"#!/bin/sh\ntouch {str(marker)!r}\nexit 0\n",
            )

            result = run_script(wrapper, env=fixture.env)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(marker.exists())
            launcher_log = fixture.home / ".claude-code-router/logs/ccr-launcher.err"
            self.assertIn("already listening on port 4312", launcher_log.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
