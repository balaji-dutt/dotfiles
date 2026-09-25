from __future__ import annotations

import json
import os
import shutil
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
    cwd: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["/bin/bash", str(script), *args],
        cwd=cwd,
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
        "    'GIT_COMMITTER_EMAIL', 'AI_ATTESTATION_JSON', 'PLANNOTATOR_PORT',\n"
        "    'CLAUDE_PLANNOTATOR_POOL', 'OPENCODE_PLANNOTATOR_POOL',\n"
        "    'ANTHROPIC_SYSTEM_PROMPT_PATH',\n"
        "    'OPENCODE_DISABLE_CLAUDE_CODE_PROMPT',\n"
        "    'OPENCODE_DISABLE_CLAUDE_CODE_SKILLS',\n"
        "]\n"
        "payload = {'argv': sys.argv[1:], 'env': {key: os.environ.get(key) for key in keys}}\n"
        "path.write_text(json.dumps(payload), encoding='utf-8')\n"
        "raise SystemExit(int(os.environ.get('FAKE_EXIT', '0')))\n",
    )


def write_commit_git(path: Path, log_path: Path) -> Path:
    return write_executable(
        path,
        f"#!{sys.executable}\n"
        "import json, os, pathlib, sys\n"
        f"path = pathlib.Path({str(log_path)!r})\n"
        "path.parent.mkdir(parents=True, exist_ok=True)\n"
        "with path.open('a', encoding='utf-8') as handle:\n"
        "    handle.write(json.dumps({'argv': sys.argv[1:], 'ai': os.environ.get('AI_ATTESTATION_JSON'), 'author': os.environ.get('GIT_AUTHOR_NAME'), 'committer': os.environ.get('GIT_COMMITTER_NAME')}) + '\\n')\n"
        "if sys.argv[1:3] == ['rev-parse', '--git-path']:\n"
        "    state_path = os.environ.get('FAKE_STATE_PATH')\n"
        "    if state_path:\n"
        "        print(state_path)\n"
        "        raise SystemExit(0)\n"
        "    raise SystemExit(1)\n"
        "raise SystemExit(int(os.environ.get('FAKE_EXIT', '0')))\n",
    )


class CommitWrapperTests(unittest.TestCase):
    DIGEST = "sha256:" + "0123456789abcdef" * 4
    BOT = "Co-authored-by: opencode-agent[bot] <opencode-agent[bot]@users.noreply.github.com>"
    CASES = (
        ("executable_cc-commit", "Claude", "noreply@anthropic.com", "claude-code", "ai-attestation-claude-code.json"),
        ("executable_oc-commit", "OpenCode", "noreply@opencode.ai", "opencode", "ai-attestation-opencode.json"),
    )

    def init_repository(self, repo: Path, env: dict[str, str]) -> None:
        subprocess.run(["git", "init", "--quiet", str(repo)], env=env, check=True)
        subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test User"], env=env, check=True)
        subprocess.run(["git", "-C", str(repo), "config", "user.email", "test@example.com"], env=env, check=True)

    def stage_change(self, repo: Path, env: dict[str, str], name: str, content: str) -> None:
        (repo / name).write_text(content, encoding="utf-8")
        subprocess.run(["git", "-C", str(repo), "add", "--", name], env=env, check=True)

    def commit_message(self, repo: Path, env: dict[str, str]) -> str:
        return subprocess.run(
            ["git", "-C", str(repo), "log", "-1", "--format=%B"],
            env=env,
            check=True,
            text=True,
            stdout=subprocess.PIPE,
        ).stdout

    def parsed_trailers(self, repo: Path, env: dict[str, str]) -> list[str]:
        result = subprocess.run(
            ["git", "interpret-trailers", "--parse"],
            env=env,
            input=self.commit_message(repo, env),
            check=True,
            text=True,
            stdout=subprocess.PIPE,
        )
        return result.stdout.splitlines()

    def test_wrappers_forward_identity_arguments_and_exit_status(self) -> None:
        for filename, identity, _email, tool, state_name in self.CASES:
            with self.subTest(filename=filename), isolated_environment(
                prefix="commit wrapper "
            ) as fixture:
                log = fixture.root / "git.jsonl"
                write_commit_git(fixture.fake_bin / "git", log)
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
                calls = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
                self.assertEqual(calls[0]["argv"], ["rev-parse", "--git-path", state_name])
                payload = calls[-1]
                self.assertEqual(payload["argv"][-4:], ["-m", "subject with spaces", "--", "path;literal"])
                self.assertEqual(payload["argv"][0:2], ["-c", "trailer.separators=:"])
                self.assertIn("AI-Participant: tool=" + tool, payload["argv"])
                if identity == "OpenCode":
                    self.assertIn(self.BOT, payload["argv"])
                self.assertLess(payload["argv"].index("--trailer"), payload["argv"].index("--"))
                self.assertIsNone(payload["ai"])
                self.assertEqual(payload["author"], identity)
                self.assertEqual(payload["committer"], identity)

    def test_environment_records_are_normalized_deduplicated_and_parseable(self) -> None:
        for filename, identity, email, tool, _state_name in self.CASES:
            with self.subTest(filename=filename), isolated_environment(prefix="attestation env ") as fixture:
                repo = fixture.root / "repo"
                self.init_repository(repo, fixture.env)
                self.stage_change(repo, fixture.env, "path;literal", identity)
                rich = {
                    "tool": tool,
                    "agent": "build",
                    "role": "editor",
                    "model": "provider/model-v1",
                    "sourceDefinition": "agents/build.md",
                    "sourceDigest": self.DIGEST,
                }
                payload = json.dumps(
                    {
                        "schemaVersion": 1,
                        "participants": [
                            rich,
                            {
                                "tool": "review-tool",
                                "agent": "bad value",
                                "role": "Reviewer",
                                "model": "provider/reviewer",
                                "sourceDefinition": "agents/build.md",
                                "sourceDigest": self.DIGEST,
                            },
                            rich,
                        ],
                    }
                )
                result = run_script(
                    BIN / filename,
                    "-m",
                    f"Commit as {identity}",
                    "-m",
                    "body\n\nRefs: dots-test",
                    "--",
                    "path;literal",
                    env=fixture.env | {"AI_ATTESTATION_JSON": payload},
                    cwd=repo,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(
                    self.parsed_trailers(repo, fixture.env),
                    [
                        *([self.BOT] if identity == "OpenCode" else []),
                        "Refs: dots-test",
                        f"AI-Participant: tool={tool}; agent=build; role=editor; model=provider/model-v1",
                        "Source-Definition: agents/build.md",
                        f"Source-Digest: {self.DIGEST}",
                        "AI-Participant: tool=review-tool; model=provider/reviewer",
                        "Source-Definition: agents/build.md",
                        f"Source-Digest: {self.DIGEST}",
                    ],
                )
                identity_line = subprocess.run(
                    ["git", "-C", str(repo), "log", "-1", "--format=%an <%ae>|%cn <%ce>"],
                    env=fixture.env,
                    check=True,
                    text=True,
                    stdout=subprocess.PIPE,
                ).stdout.strip()
                self.assertEqual(identity_line, f"{identity} <{email}>|{identity} <{email}>")

    def test_state_is_one_shot_environment_wins_and_failed_retry_degrades(self) -> None:
        with isolated_environment(prefix="attestation state ") as fixture:
            log = fixture.root / "git.jsonl"
            state_path = fixture.root / "state.json"
            write_commit_git(fixture.fake_bin / "git", log)
            state_path.write_text(
                json.dumps({"schemaVersion": 1, "participants": [{"tool": "state-tool"}]}),
                encoding="utf-8",
            )
            environment_payload = json.dumps(
                {"schemaVersion": 1, "participants": [{"tool": "environment-tool"}]}
            )
            env = fixture.env | {
                "AI_ATTESTATION_JSON": environment_payload,
                "FAKE_STATE_PATH": str(state_path),
                "FAKE_EXIT": "27",
            }
            failed = run_script(BIN / "executable_oc-commit", "-m", "failure", env=env)
            self.assertEqual(failed.returncode, 27)
            self.assertFalse(state_path.exists())
            calls = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
            self.assertIn("AI-Participant: tool=environment-tool", calls[-1]["argv"])
            self.assertNotIn("AI-Participant: tool=state-tool", calls[-1]["argv"])
            self.assertIsNone(calls[-1]["ai"])

            retry = run_script(
                BIN / "executable_oc-commit",
                "-m",
                "retry",
                env=fixture.env | {"FAKE_STATE_PATH": str(state_path)},
            )
            self.assertEqual(retry.returncode, 0, retry.stderr)
            calls = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
            self.assertIn("AI-Participant: tool=opencode", calls[-1]["argv"])

    def test_invalid_oversized_and_unavailable_parser_inputs_degrade(self) -> None:
        payloads = (
            "{not json",
            json.dumps({"schemaVersion": 2, "participants": [{"tool": "other"}]}),
            json.dumps({"schemaVersion": 1, "participants": [{"tool": "other"}]}) + (" " * 16384),
            json.dumps({"schemaVersion": 1, "participants": [{"tool": "other"}], "extra": True}),
            json.dumps({"schemaVersion": 1, "participants": [{"tool": "other"}] * 9}),
            json.dumps({"schemaVersion": 1, "participants": [{"tool": "newline-tool\n"}]}),
        )
        for index, payload in enumerate(payloads):
            with self.subTest(index=index), isolated_environment(prefix="attestation invalid ") as fixture:
                log = fixture.root / "git.jsonl"
                write_commit_git(fixture.fake_bin / "git", log)
                result = run_script(
                    BIN / "executable_oc-commit",
                    "-m",
                    "subject",
                    env=fixture.env | {"AI_ATTESTATION_JSON": payload},
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                calls = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
                self.assertIn("AI-Participant: tool=opencode", calls[-1]["argv"])
                self.assertNotIn("AI-Participant: tool=other", calls[-1]["argv"])

        with isolated_environment(prefix="attestation no jq ") as fixture:
            repo = fixture.root / "repo"
            self.init_repository(repo, fixture.env)
            self.stage_change(repo, fixture.env, "no-jq.txt", "content")
            no_jq_bin = fixture.root / "no-jq-bin"
            no_jq_bin.mkdir()
            for command in ("env", "git", "rm", "tr", "wc"):
                executable = shutil.which(command)
                self.assertIsNotNone(executable)
                os.symlink(executable, no_jq_bin / command)
            payload = json.dumps({"schemaVersion": 1, "participants": [{"tool": "rich-tool"}]})
            result = run_script(
                BIN / "executable_oc-commit",
                "-m",
                "No jq",
                env=fixture.env | {"PATH": str(no_jq_bin), "AI_ATTESTATION_JSON": payload},
                cwd=repo,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(self.parsed_trailers(repo, fixture.env), [self.BOT, "AI-Participant: tool=opencode"])

    def test_command_local_trailer_policy_overrides_repository_defaults(self) -> None:
        with isolated_environment(prefix="attestation config ") as fixture:
            repo = fixture.root / "repo"
            self.init_repository(repo, fixture.env)
            subprocess.run(["git", "-C", str(repo), "config", "trailer.where", "start"], env=fixture.env, check=True)
            subprocess.run(["git", "-C", str(repo), "config", "trailer.ifexists", "replace"], env=fixture.env, check=True)
            subprocess.run(["git", "-C", str(repo), "config", "trailer.AI-Participant.where", "start"], env=fixture.env, check=True)
            subprocess.run(["git", "-C", str(repo), "config", "trailer.AI-Participant.cmd", "printf hostile"], env=fixture.env, check=True)
            subprocess.run(["git", "-C", str(repo), "config", "trailer.Co-authored-by.ifexists", "replace"], env=fixture.env, check=True)
            subprocess.run(["git", "-C", str(repo), "config", "trailer.Co-authored-by.cmd", "printf hostile"], env=fixture.env, check=True)
            self.stage_change(repo, fixture.env, "config.txt", "content")
            payload = json.dumps(
                {"schemaVersion": 1, "participants": [{"tool": "one"}, {"tool": "two"}]}
            )
            result = run_script(
                BIN / "executable_oc-commit",
                "-m",
                "Configured",
                "-m",
                "Refs: dots-test",
                env=fixture.env | {"AI_ATTESTATION_JSON": payload},
                cwd=repo,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                self.parsed_trailers(repo, fixture.env),
                [self.BOT, "Refs: dots-test", "AI-Participant: tool=one", "AI-Participant: tool=two"],
            )

    def test_opencode_amend_preserves_original_author_and_other_coauthors(self) -> None:
        with isolated_environment(prefix="opencode amend ") as fixture:
            repo = fixture.root / "repo"
            self.init_repository(repo, fixture.env)
            self.stage_change(repo, fixture.env, "original.txt", "original")
            human = fixture.env | {
                "GIT_AUTHOR_NAME": "Original Author",
                "GIT_AUTHOR_EMAIL": "original@example.com",
                "GIT_COMMITTER_NAME": "Original Author",
                "GIT_COMMITTER_EMAIL": "original@example.com",
            }
            subprocess.run(
                ["git", "-C", str(repo), "commit", "-m", "Original", "-m",
                 "Co-authored-by: Teammate <teammate@example.com>\nAI-Participant: tool=previous"],
                env=human, check=True, stdout=subprocess.PIPE,
            )
            for index in range(2):
                self.stage_change(repo, fixture.env, "original.txt", f"amended {index}")
                result = run_script(BIN / "executable_oc-commit", "--amend", "--no-edit", env=fixture.env, cwd=repo)
                self.assertEqual(result.returncode, 0, result.stderr)
                trailers = self.parsed_trailers(repo, fixture.env)
                self.assertEqual(trailers.count(self.BOT), 1)
                self.assertEqual(trailers[0], self.BOT)
                self.assertIn("Co-authored-by: Teammate <teammate@example.com>", trailers)
                self.assertEqual(trailers[-1], "AI-Participant: tool=opencode")
                self.assertEqual(
                    subprocess.run(
                        ["git", "-C", str(repo), "log", "-1", "--format=%an <%ae>|%cn <%ce>"],
                        env=fixture.env, check=True, text=True, stdout=subprocess.PIPE,
                    ).stdout.strip(),
                    "Original Author <original@example.com>|OpenCode <noreply@opencode.ai>",
                )

    def test_opencode_message_file_deduplicates_existing_bot(self) -> None:
        with isolated_environment(prefix="opencode message file ") as fixture:
            repo = fixture.root / "repo"
            self.init_repository(repo, fixture.env)
            self.stage_change(repo, fixture.env, "file.txt", "content")
            message_file = fixture.root / "message.txt"
            message_file.write_text(
                f"Message from file\n\nRefs: dots-test\n{self.BOT}\n"
                "Co-authored-by: Teammate <teammate@example.com>\n",
                encoding="utf-8",
            )
            result = run_script(BIN / "executable_oc-commit", "-F", str(message_file), env=fixture.env, cwd=repo)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                self.parsed_trailers(repo, fixture.env),
                ["Refs: dots-test", self.BOT, "Co-authored-by: Teammate <teammate@example.com>",
                "AI-Participant: tool=opencode"],
            )

    def test_legacy_gitconfig_author_aliases_keep_amend_semantics(self) -> None:
        for template in (
            REPO_ROOT / "dot_gitconfig.tmpl",
            REPO_ROOT / "private_Documents/development/container-dotfiles/dotfiles/dot_gitconfig.tmpl",
        ):
            with self.subTest(template=template):
                content = template.read_text(encoding="utf-8")
                self.assertIn(r'clauth = commit --amend --author=\"Claude <claude@anthropic.com>\" --no-edit', content)
                self.assertIn(r'ocauth = commit --amend --author=\"OpenCode <noreply@opencode.ai>\" --no-edit', content)

        with isolated_environment(prefix="legacy author alias ") as fixture:
            repo = fixture.root / "repo"
            self.init_repository(repo, fixture.env)
            self.stage_change(repo, fixture.env, "alias.txt", "original")
            subprocess.run(
                ["git", "-C", str(repo), "commit", "-m", "Original"],
                env=fixture.env, check=True, stdout=subprocess.PIPE,
            )
            self.stage_change(repo, fixture.env, "alias.txt", "amended")
            result = subprocess.run(
                ["git", "-C", str(repo), "-c",
                 'alias.ocauth=commit --amend --author="OpenCode <noreply@opencode.ai>" --no-edit',
                 "ocauth"],
                env=fixture.env, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                subprocess.run(
                    ["git", "-C", str(repo), "log", "-1", "--format=%an <%ae>|%cn <%ce>"],
                    env=fixture.env, check=True, text=True, stdout=subprocess.PIPE,
                ).stdout.strip(),
                "OpenCode <noreply@opencode.ai>|Test User <test@example.com>",
            )
            self.assertEqual(self.parsed_trailers(repo, fixture.env), [])

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

    def test_help_preserves_attestation_state(self) -> None:
        with isolated_environment(prefix="commit help state ") as fixture:
            state_path = fixture.root / "state.json"
            state_path.write_text("state", encoding="utf-8")
            result = run_script(
                BIN / "executable_oc-commit",
                "--help",
                env=fixture.env | {"FAKE_STATE_PATH": str(state_path)},
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(state_path.read_text(encoding="utf-8"), "state")


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


class WslOpenWrapperTests(unittest.TestCase):
    def test_handler_override_receives_url_verbatim_and_propagates_failure(self) -> None:
        with isolated_environment(prefix="wsl open ") as fixture:
            log = fixture.root / "handler.json"
            handler = write_env_logger(fixture.root / "Windows Handler/rundll32.exe", log)
            url = "https://example.com/?code=abc123&state=xyz;789"

            result = run_script(
                BIN / "executable_wsl-open",
                url,
                env=fixture.env | {"WSL_OPEN_HANDLER": str(handler), "FAKE_EXIT": "17"},
            )

            self.assertEqual(result.returncode, 17)
            payload = json.loads(log.read_text(encoding="utf-8"))
            self.assertEqual(payload["argv"], ["url.dll,FileProtocolHandler", url])

    def test_default_handler_is_the_windows_interop_rundll32(self) -> None:
        source = (BIN / "executable_wsl-open").read_text(encoding="utf-8")
        self.assertIn("WSL_OPEN_HANDLER:-/mnt/c/Windows/System32/rundll32.exe", source)

    def test_missing_url_fails_without_invoking_handler(self) -> None:
        with isolated_environment(prefix="wsl open usage ") as fixture:
            log = fixture.root / "handler.json"
            handler = write_env_logger(fixture.root / "rundll32.exe", log)

            result = run_script(
                BIN / "executable_wsl-open",
                env=fixture.env | {"WSL_OPEN_HANDLER": str(handler)},
            )

            self.assertEqual(result.returncode, 64)
            self.assertFalse(log.exists())
            self.assertIn("usage:", result.stderr)


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


if __name__ == "__main__":
    unittest.main()
