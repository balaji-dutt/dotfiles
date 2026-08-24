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
) -> Path:
    """Stub curl so no test reaches the network. Returns the call-log path."""
    log_path = environment.root / "curl-calls.jsonl"
    body = (
        f"#!{sys.executable}\n"
        "import json, pathlib, sys\n"
        "stdin = sys.stdin.read()\n"
        f"log = pathlib.Path({str(log_path)!r})\n"
        "with log.open('a', encoding='utf-8') as handle:\n"
        "    handle.write(json.dumps({'argv': sys.argv[1:], 'stdin': stdin}) + '\\n')\n"
        f"sys.stdout.write({status!r})\n"
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
            self.assertEqual(curl_calls(log_path), [])

    def test_accepted_credential_is_valid(self) -> None:
        with isolated_environment() as environment:
            stub_claude(environment, {"loggedIn": True})
            stub_security(environment)
            stub_curl(environment, status="200")
            write_opencode_auth(environment)

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
                    write_opencode_auth(environment)

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
                    write_opencode_auth(environment)

                    result = run_script(environment)

                    self.assertEqual(result.returncode, EXIT_INDETERMINATE)
                    self.assertNotIn("INVALID", result.stdout)

    def test_network_failure_is_indeterminate(self) -> None:
        with isolated_environment() as environment:
            stub_claude(environment, {"loggedIn": True})
            stub_security(environment)
            stub_curl(environment, status="", exit_code=7)
            write_opencode_auth(environment)

            result = run_script(environment)

            self.assertEqual(result.returncode, EXIT_INDETERMINATE)
            self.assertNotIn("INVALID", result.stdout)

    def test_unreachable_claude_binary_still_probes(self) -> None:
        """An unknown local state must not block the authoritative check."""
        with isolated_environment() as environment:
            stub_claude(environment, None)
            stub_security(environment)
            stub_curl(environment, status="200")
            write_opencode_auth(environment)

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

    def test_opencode_credential_takes_precedence(self) -> None:
        with isolated_environment() as environment:
            stub_claude(environment, {"loggedIn": True})
            stub_security(environment)
            log_path = stub_curl(environment, status="200")
            write_opencode_auth(environment, token="opencode-token")
            write_json(
                environment.home / ".claude/.credentials.json",
                {"claudeAiOauth": {"accessToken": "credentials-file-token"}},
            )

            result = run_script(environment)

            self.assertEqual(result.returncode, EXIT_VALID)
            self.assertIn("opencode-token", curl_calls(log_path)[0]["stdin"])

    def test_token_is_never_exposed_in_output_or_argv(self) -> None:
        with isolated_environment() as environment:
            stub_claude(environment, {"loggedIn": True})
            stub_security(environment)
            log_path = stub_curl(environment, status="200")
            write_opencode_auth(environment)

            result = run_script(environment, "--debug")

            self.assertEqual(result.returncode, EXIT_VALID)
            self.assertNotIn(FAKE_TOKEN, result.stdout)
            self.assertNotIn(FAKE_TOKEN, result.stderr)

            call = curl_calls(log_path)[0]
            self.assertNotIn(FAKE_TOKEN, " ".join(call["argv"]))
            self.assertIn(FAKE_TOKEN, call["stdin"])

    def test_debug_reports_source_without_token(self) -> None:
        with isolated_environment() as environment:
            stub_claude(environment, {"loggedIn": True})
            stub_security(environment)
            stub_curl(environment, status="200")
            write_opencode_auth(environment)

            result = run_script(environment, "--debug")

            self.assertIn("credential source: opencode", result.stderr)
            self.assertNotIn(FAKE_TOKEN, result.stderr)

    @unittest.skipUnless(sys.platform == "darwin", "Keychain discovery is macOS-only")
    def test_keychain_is_used_when_opencode_has_no_credential(self) -> None:
        with isolated_environment() as environment:
            stub_claude(environment, {"loggedIn": True})
            stub_security(
                environment,
                json.dumps({"claudeAiOauth": {"accessToken": FAKE_TOKEN}}),
            )
            log_path = stub_curl(environment, status="200")

            result = run_script(environment)

            self.assertEqual(result.returncode, EXIT_VALID)
            self.assertIn(FAKE_TOKEN, curl_calls(log_path)[0]["stdin"])

    def test_container_mirror_is_byte_identical(self) -> None:
        self.assertTrue(MIRROR.exists(), f"missing container mirror: {MIRROR}")
        self.assertEqual(SCRIPT.read_bytes(), MIRROR.read_bytes())


if __name__ == "__main__":
    unittest.main()
