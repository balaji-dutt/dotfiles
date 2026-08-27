from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

from tests.support.fixtures import (
    IsolatedEnvironment,
    isolated_environment,
    write_executable,
    write_json,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "bin/executable_claude-token-check"
MIRROR = REPO_ROOT / (
    "private_Documents/development/container-dotfiles/dotfiles"
    "/dot_local/bin/executable_claude-token-check"
)

EXIT_VALID = 0
EXIT_INVALID = 1
EXIT_INDETERMINATE = 2

FAKE_TOKEN = "sk-ant-oat-FAKETOKENFORTESTSONLY-0123456789"
CLAUDE_TOKEN = "sk-ant-oat-CLAUDECODEFAKETOKEN-0123456789"
OPENCODE_TOKEN = "sk-ant-oat-OPENCODEFAKETOKEN-0123456789"


def stub_claude(environment: IsolatedEnvironment, payload: object | None) -> None:
    """Stub `claude auth status --json`. None means the command fails."""
    if payload is None:
        body = "#!/bin/sh\nexit 1\n"
    else:
        body = "#!/bin/sh\ncat <<'JSON'\n" + json.dumps(payload) + "\nJSON\n"
    write_executable(environment.fake_bin / "claude", body)


def stub_security(environment: IsolatedEnvironment, payload: str | None = None) -> None:
    """Stub the macOS Keychain read. None means 'no such credential'."""
    if payload is None:
        body = "#!/bin/sh\nexit 1\n"
    else:
        body = "#!/bin/sh\ncat <<'SECRET'\n" + payload + "\nSECRET\n"
    write_executable(environment.fake_bin / "security", body)


def stub_curl(
    environment: IsolatedEnvironment,
    *,
    status: str = "200",
    exit_code: int = 0,
    statuses: dict[str, str] | None = None,
) -> Path:
    """Stub curl so no test reaches the network. Returns the call-log path.

    `statuses` maps a token substring to the status returned when that token is
    the one being probed, which is how a divergence between sources is
    expressed. `status` is the fallback for anything unmatched.
    """
    log_path = environment.root / "curl-calls.jsonl"
    body = (
        f"#!{sys.executable}\n"
        "import json, pathlib, sys\n"
        "stdin = sys.stdin.read()\n"
        f"log = pathlib.Path({str(log_path)!r})\n"
        "with log.open('a', encoding='utf-8') as handle:\n"
        "    handle.write(json.dumps({'argv': sys.argv[1:], 'stdin': stdin}) + '\\n')\n"
        f"mapped = {dict(statuses or {})!r}\n"
        f"status = {status!r}\n"
        "for needle, value in mapped.items():\n"
        "    if needle in stdin:\n"
        "        status = value\n"
        "        break\n"
        "sys.stdout.write(status)\n"
        f"raise SystemExit({exit_code})\n"
    )
    write_executable(environment.fake_bin / "curl", body)
    return log_path


def curl_calls(log_path: Path) -> list[dict]:
    if not log_path.exists():
        return []
    return [json.loads(line) for line in log_path.read_text("utf-8").splitlines() if line]


def write_opencode_auth(
    environment: IsolatedEnvironment,
    *,
    token: str = FAKE_TOKEN,
    expires: int | None = None,
) -> None:
    entry: dict[str, object] = {"type": "oauth", "access": token}
    if expires is not None:
        entry["expires"] = expires
    write_json(
        environment.home / ".local/share/opencode/auth.json",
        {"anthropic": entry},
    )


def write_claude_credentials(
    environment: IsolatedEnvironment, *, token: str = FAKE_TOKEN
) -> None:
    """Write the credentials-file credential.

    With `security` stubbed to fail, this is the Claude Code source on macOS as
    well as everywhere else, so tests built on it run identically on both.
    """
    write_json(
        environment.home / ".claude/.credentials.json",
        {"claudeAiOauth": {"accessToken": token}},
    )


def run_script(
    environment: IsolatedEnvironment, *args: str
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(SCRIPT), *args],
        env=environment.env,
        cwd=str(environment.root),
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def line_for(stdout: str, source: str) -> str:
    """Return the single reported line naming `source`, or '' when absent."""
    matches = [line for line in stdout.splitlines() if source in line]
    return matches[0] if len(matches) == 1 else ""


class ClaudeTokenCheckTests(unittest.TestCase):
    """Every test stubs claude, security, and curl so none touches a real
    credential store or the network."""

    def test_help_exits_zero_and_documents_exit_codes(self) -> None:
        with isolated_environment() as environment:
            stub_claude(environment, {"loggedIn": True})
            stub_security(environment)
            stub_curl(environment)

            result = run_script(environment, "--help")

            self.assertEqual(result.returncode, EXIT_VALID)
            self.assertIn("usage: claude-token-check", result.stdout)
            self.assertIn("Indeterminate", result.stdout)

    def test_unknown_argument_is_indeterminate_not_invalid(self) -> None:
        with isolated_environment() as environment:
            stub_claude(environment, {"loggedIn": True})
            stub_security(environment)
            stub_curl(environment)

            result = run_script(environment, "--not-a-flag")

            self.assertEqual(result.returncode, EXIT_INDETERMINATE)

    def test_logged_out_short_circuits_without_probing(self) -> None:
        with isolated_environment() as environment:
            stub_claude(environment, {"loggedIn": False})
            stub_security(environment)
            log_path = stub_curl(environment)
            write_opencode_auth(environment)

            result = run_script(environment)

            self.assertEqual(result.returncode, EXIT_INVALID)
            self.assertIn("INVALID", result.stdout)
            self.assertEqual(curl_calls(log_path), [])

    def test_missing_credential_is_indeterminate_not_invalid(self) -> None:
        with isolated_environment() as environment:
            stub_claude(environment, {"loggedIn": True})
            stub_security(environment)
            log_path = stub_curl(environment)

            result = run_script(environment)

            self.assertEqual(result.returncode, EXIT_INDETERMINATE)
            self.assertIn("INDETERMINATE", result.stdout)
            self.assertIn("claude-code", result.stdout)
            self.assertEqual(curl_calls(log_path), [])

    def test_accepted_credential_is_valid(self) -> None:
        with isolated_environment() as environment:
            stub_claude(environment, {"loggedIn": True})
            stub_security(environment)
            stub_curl(environment, status="200")
            write_claude_credentials(environment)

            result = run_script(environment)

            self.assertEqual(result.returncode, EXIT_VALID)
            self.assertIn("VALID", result.stdout)

    def test_rejected_status_codes_are_invalid(self) -> None:
        for status in ("401", "403"):
            with self.subTest(status=status):
                with isolated_environment() as environment:
                    stub_claude(environment, {"loggedIn": True})
                    stub_security(environment)
                    stub_curl(environment, status=status)
                    write_claude_credentials(environment)

                    result = run_script(environment)

                    self.assertEqual(result.returncode, EXIT_INVALID)
                    self.assertIn("claude auth login", result.stdout)

    def test_transient_failures_are_indeterminate_never_invalid(self) -> None:
        """A rate-limited or broken endpoint must not read as a dead token."""
        for status in ("429", "500", "502", "503"):
            with self.subTest(status=status):
                with isolated_environment() as environment:
                    stub_claude(environment, {"loggedIn": True})
                    stub_security(environment)
                    stub_curl(environment, status=status)
                    write_claude_credentials(environment)

                    result = run_script(environment)

                    self.assertEqual(result.returncode, EXIT_INDETERMINATE)
                    self.assertNotIn("INVALID", result.stdout)

    def test_network_failure_is_indeterminate(self) -> None:
        with isolated_environment() as environment:
            stub_claude(environment, {"loggedIn": True})
            stub_security(environment)
            stub_curl(environment, status="", exit_code=7)
            write_claude_credentials(environment)

            result = run_script(environment)

            self.assertEqual(result.returncode, EXIT_INDETERMINATE)
            self.assertNotIn("INVALID", result.stdout)

    def test_unreachable_claude_binary_still_probes(self) -> None:
        """An unknown local state must not block the authoritative check."""
        with isolated_environment() as environment:
            stub_claude(environment, None)
            stub_security(environment)
            stub_curl(environment, status="200")
            write_claude_credentials(environment)

            result = run_script(environment)

            self.assertEqual(result.returncode, EXIT_VALID)

    def test_expired_opencode_entry_is_skipped(self) -> None:
        with isolated_environment() as environment:
            stub_claude(environment, {"loggedIn": True})
            stub_security(environment)
            log_path = stub_curl(environment)
            write_opencode_auth(environment, expires=1)

            result = run_script(environment)

            self.assertEqual(result.returncode, EXIT_INDETERMINATE)
            self.assertEqual(curl_calls(log_path), [])
            self.assertNotIn("opencode", result.stdout)

    def test_credentials_file_shapes_are_accepted(self) -> None:
        shapes: tuple[dict, ...] = (
            {"claudeAiOauth": {"accessToken": FAKE_TOKEN}},
            {"oauth": {"access_token": FAKE_TOKEN}},
            {"token": FAKE_TOKEN},
            {"accessToken": FAKE_TOKEN},
        )
        for shape in shapes:
            with self.subTest(shape=sorted(shape)):
                with isolated_environment() as environment:
                    stub_claude(environment, {"loggedIn": True})
                    stub_security(environment)
                    log_path = stub_curl(environment, status="200")
                    write_json(environment.home / ".claude/.credentials.json", shape)

                    result = run_script(environment)

                    self.assertEqual(result.returncode, EXIT_VALID)
                    calls = curl_calls(log_path)
                    self.assertEqual(len(calls), 1)
                    self.assertIn(FAKE_TOKEN, calls[0]["stdin"])

    def test_claude_code_credential_decides_exit_code(self) -> None:
        """Discovery must not answer with OpenCode's token when Claude Code has
        one of its own; they are separate credentials."""
        with isolated_environment() as environment:
            stub_claude(environment, {"loggedIn": True})
            stub_security(environment)
            log_path = stub_curl(
                environment,
                statuses={CLAUDE_TOKEN: "200", OPENCODE_TOKEN: "200"},
            )
            write_claude_credentials(environment, token=CLAUDE_TOKEN)
            write_opencode_auth(environment, token=OPENCODE_TOKEN)

            result = run_script(environment)

            self.assertEqual(result.returncode, EXIT_VALID)
            probed = " ".join(call["stdin"] for call in curl_calls(log_path))
            self.assertIn(CLAUDE_TOKEN, probed)
            self.assertIn(OPENCODE_TOKEN, probed)

    def test_both_sources_are_reported_on_their_own_lines(self) -> None:
        with isolated_environment() as environment:
            stub_claude(environment, {"loggedIn": True})
            stub_security(environment)
            stub_curl(
                environment,
                statuses={CLAUDE_TOKEN: "200", OPENCODE_TOKEN: "200"},
            )
            write_claude_credentials(environment, token=CLAUDE_TOKEN)
            write_opencode_auth(environment, token=OPENCODE_TOKEN)

            result = run_script(environment)

            self.assertEqual(result.returncode, EXIT_VALID)
            self.assertTrue(line_for(result.stdout, "claude-code").startswith("VALID:"))
            self.assertTrue(line_for(result.stdout, "opencode").startswith("VALID:"))

    def test_rejected_claude_code_credential_beats_healthy_opencode(self) -> None:
        """A live OpenCode token must not mask a Claude Code credential the
        server has stopped accepting."""
        with isolated_environment() as environment:
            stub_claude(environment, {"loggedIn": True})
            stub_security(environment)
            stub_curl(
                environment,
                statuses={CLAUDE_TOKEN: "401", OPENCODE_TOKEN: "200"},
            )
            write_claude_credentials(environment, token=CLAUDE_TOKEN)
            write_opencode_auth(environment, token=OPENCODE_TOKEN)

            result = run_script(environment)

            self.assertEqual(result.returncode, EXIT_INVALID)
            self.assertTrue(
                line_for(result.stdout, "claude-code").startswith("INVALID:")
            )
            self.assertIn("claude auth login", line_for(result.stdout, "claude-code"))
            self.assertTrue(line_for(result.stdout, "opencode").startswith("VALID:"))

    def test_rejected_opencode_does_not_fail_the_preflight(self) -> None:
        with isolated_environment() as environment:
            stub_claude(environment, {"loggedIn": True})
            stub_security(environment)
            stub_curl(
                environment,
                statuses={CLAUDE_TOKEN: "200", OPENCODE_TOKEN: "401"},
            )
            write_claude_credentials(environment, token=CLAUDE_TOKEN)
            write_opencode_auth(environment, token=OPENCODE_TOKEN)

            result = run_script(environment)

            self.assertEqual(result.returncode, EXIT_VALID)
            self.assertTrue(line_for(result.stdout, "opencode").startswith("INVALID:"))
            self.assertIn("opencode auth login", line_for(result.stdout, "opencode"))

    def test_missing_claude_code_credential_is_indeterminate_despite_opencode(
        self,
    ) -> None:
        with isolated_environment() as environment:
            stub_claude(environment, {"loggedIn": True})
            stub_security(environment)
            log_path = stub_curl(environment, status="200")
            write_opencode_auth(environment, token=OPENCODE_TOKEN)

            result = run_script(environment)

            self.assertEqual(result.returncode, EXIT_INDETERMINATE)
            self.assertTrue(
                line_for(result.stdout, "claude-code").startswith("INDETERMINATE:")
            )
            self.assertTrue(line_for(result.stdout, "opencode").startswith("VALID:"))
            calls = curl_calls(log_path)
            self.assertEqual(len(calls), 1)
            self.assertIn(OPENCODE_TOKEN, calls[0]["stdin"])

    def test_shared_token_is_probed_once_and_reported_once(self) -> None:
        with isolated_environment() as environment:
            stub_claude(environment, {"loggedIn": True})
            stub_security(environment)
            log_path = stub_curl(environment, status="200")
            write_claude_credentials(environment, token=FAKE_TOKEN)
            write_opencode_auth(environment, token=FAKE_TOKEN)

            result = run_script(environment)

            self.assertEqual(result.returncode, EXIT_VALID)
            self.assertEqual(len(curl_calls(log_path)), 1)
            self.assertEqual(len(result.stdout.splitlines()), 1)
            self.assertIn("+ opencode", result.stdout)

    def test_token_is_never_exposed_in_output_or_argv(self) -> None:
        with isolated_environment() as environment:
            stub_claude(environment, {"loggedIn": True})
            stub_security(environment)
            log_path = stub_curl(
                environment,
                statuses={CLAUDE_TOKEN: "200", OPENCODE_TOKEN: "200"},
            )
            write_claude_credentials(environment, token=CLAUDE_TOKEN)
            write_opencode_auth(environment, token=OPENCODE_TOKEN)

            result = run_script(environment, "--debug")

            self.assertEqual(result.returncode, EXIT_VALID)
            for token in (CLAUDE_TOKEN, OPENCODE_TOKEN):
                self.assertNotIn(token, result.stdout)
                self.assertNotIn(token, result.stderr)

            calls = curl_calls(log_path)
            self.assertEqual(len(calls), 2)
            for call in calls:
                self.assertNotIn(CLAUDE_TOKEN, " ".join(call["argv"]))
                self.assertNotIn(OPENCODE_TOKEN, " ".join(call["argv"]))

    def test_debug_reports_each_source_without_token(self) -> None:
        with isolated_environment() as environment:
            stub_claude(environment, {"loggedIn": True})
            stub_security(environment)
            stub_curl(
                environment,
                statuses={CLAUDE_TOKEN: "200", OPENCODE_TOKEN: "200"},
            )
            write_claude_credentials(environment, token=CLAUDE_TOKEN)
            write_opencode_auth(environment, token=OPENCODE_TOKEN)

            result = run_script(environment, "--debug")

            self.assertIn("credential: claude-code", result.stderr)
            self.assertIn("credential: opencode", result.stderr)
            self.assertNotIn(CLAUDE_TOKEN, result.stderr)
            self.assertNotIn(OPENCODE_TOKEN, result.stderr)

    @unittest.skipUnless(sys.platform == "darwin", "Keychain discovery is macOS-only")
    def test_keychain_is_the_claude_code_source_on_macos(self) -> None:
        with isolated_environment() as environment:
            stub_claude(environment, {"loggedIn": True})
            stub_security(
                environment,
                json.dumps({"claudeAiOauth": {"accessToken": CLAUDE_TOKEN}}),
            )
            log_path = stub_curl(
                environment,
                statuses={CLAUDE_TOKEN: "200", OPENCODE_TOKEN: "200"},
            )
            write_claude_credentials(environment, token=FAKE_TOKEN)
            write_opencode_auth(environment, token=OPENCODE_TOKEN)

            result = run_script(environment)

            self.assertEqual(result.returncode, EXIT_VALID)
            self.assertIn("keychain", line_for(result.stdout, "claude-code"))
            probed = " ".join(call["stdin"] for call in curl_calls(log_path))
            self.assertIn(CLAUDE_TOKEN, probed)
            self.assertNotIn(FAKE_TOKEN, probed)

    def test_container_mirror_is_byte_identical(self) -> None:
        self.assertTrue(MIRROR.exists(), f"missing container mirror: {MIRROR}")
        self.assertEqual(SCRIPT.read_bytes(), MIRROR.read_bytes())


if __name__ == "__main__":
    unittest.main()
