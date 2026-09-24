from __future__ import annotations

import subprocess
import unittest
from pathlib import Path

from tests.support.fixtures import isolated_environment, write_executable


REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE = (
    REPO_ROOT / ".chezmoiscripts/run_after_zz-configure-codebase-memory-mcp.sh.tmpl"
)
REFUSAL = (
    "codebase-memory-mcp: CBM CLI could not start because an active "
    "pre-coordination or unverified CBM daemon is running."
)


def supported_script() -> str:
    source = SOURCE.read_text(encoding="utf-8")
    body = source[source.index("#!/usr/bin/env bash") :]
    body = body.rsplit("{{- end -}}", 1)[0]
    if "{{" in body:
        raise AssertionError(
            f"{SOURCE.name} gained a template action; these tests slice the "
            "POSIX branch instead of rendering it, so bash would run the "
            "unrendered text. Render it here or cover it in the render suite."
        )
    return body


class CbmAutoIndexHookTests(unittest.TestCase):
    def prepare(self, fixture, *, cbm_body: str, sessions_active: bool):
        cbm = write_executable(fixture.root / "cbm-install/codebase-memory-mcp", cbm_body)
        write_executable(
            fixture.fake_bin / "mise",
            "#!/bin/sh\n"
            f'if [ "$1" = "which" ]; then printf \'%s\\n\' "{cbm}"; fi\n'
            "exit 0\n",
        )
        write_executable(
            fixture.fake_bin / "pgrep",
            "#!/bin/sh\n"
            f'printf \'%s\\n\' "$*" >> "{fixture.root / "pgrep.log"}"\n'
            f"exit {0 if sessions_active else 1}\n",
        )
        return write_executable(fixture.root / "configure-cbm", supported_script())

    def run_script(self, fixture, script) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", str(script)],
            capture_output=True,
            text=True,
            env=fixture.env,
            cwd=fixture.root,
        )

    def calls(self, fixture) -> list[str]:
        log = fixture.root / "cbm.log"
        return log.read_text(encoding="utf-8").splitlines() if log.exists() else []

    def logging_cbm(self, fixture, body: str) -> str:
        return (
            "#!/bin/sh\n"
            f'printf \'%s\\n\' "$*" >> "{fixture.root / "cbm.log"}"\n'
            f"{body}"
        )

    def test_skips_without_failing_when_cbm_sessions_are_active(self) -> None:
        with isolated_environment() as fixture:
            script = self.prepare(
                fixture,
                cbm_body=f"#!/bin/sh\nprintf '%s\\n' '{REFUSAL}' >&2\nexit 1\n",
                sessions_active=True,
            )
            result = self.run_script(fixture, script)

        self.assertEqual(result.returncode, 0)
        self.assertIn("WARNING:", result.stderr)
        self.assertIn("auto_index was not verified this run", result.stderr)
        self.assertIn(REFUSAL, result.stderr)

    def test_fails_when_cli_fails_and_no_cbm_session_explains_it(self) -> None:
        with isolated_environment() as fixture:
            script = self.prepare(
                fixture,
                cbm_body="#!/bin/sh\nprintf '%s\\n' 'config db is corrupt' >&2\nexit 1\n",
                sessions_active=False,
            )
            result = self.run_script(fixture, script)

        self.assertEqual(result.returncode, 1)
        self.assertIn("ERROR:", result.stderr)
        self.assertIn("config get auto_index", result.stderr)
        self.assertIn("config db is corrupt", result.stderr)
        self.assertNotIn("WARNING:", result.stderr)

    def test_already_enabled_cache_is_left_untouched(self) -> None:
        with isolated_environment() as fixture:
            script = self.prepare(
                fixture,
                cbm_body=self.logging_cbm(
                    fixture, '[ "$2" = "get" ] && printf \'%s\\n\' true\nexit 0\n'
                ),
                sessions_active=False,
            )
            result = self.run_script(fixture, script)
            calls = self.calls(fixture)

        self.assertEqual(result.returncode, 0)
        self.assertIn("INFO:", result.stdout)
        self.assertEqual(calls, ["config get auto_index"])

    def test_disabled_cache_is_set_then_reverified(self) -> None:
        with isolated_environment() as fixture:
            state = fixture.root / "auto_index"
            script = self.prepare(
                fixture,
                cbm_body=self.logging_cbm(
                    fixture,
                    f'if [ "$2" = "set" ]; then printf \'%s\\n\' true > "{state}"; exit 0; fi\n'
                    f'if [ "$2" = "get" ]; then\n'
                    f'  if [ -f "{state}" ]; then cat "{state}"; else printf \'%s\\n\' false; fi\n'
                    f"  exit 0\n"
                    f"fi\n"
                    f"exit 0\n",
                ),
                sessions_active=False,
            )
            result = self.run_script(fixture, script)
            calls = self.calls(fixture)

        self.assertEqual(result.returncode, 0)
        self.assertIn("INFO:", result.stdout)
        self.assertEqual(
            calls,
            [
                "config get auto_index",
                "config set auto_index true",
                "config get auto_index",
            ],
        )

    def test_value_that_never_becomes_true_is_reported_as_failure(self) -> None:
        with isolated_environment() as fixture:
            script = self.prepare(
                fixture,
                cbm_body=self.logging_cbm(
                    fixture, '[ "$2" = "get" ] && printf \'%s\\n\' false\nexit 0\n'
                ),
                sessions_active=False,
            )
            result = self.run_script(fixture, script)

        self.assertEqual(result.returncode, 1)
        self.assertIn("auto_index verification failed", result.stderr)

    def test_session_probe_survives_linux_comm_truncation(self) -> None:
        """Linux truncates /proc/<pid>/comm to 15 characters, so a probe for the
        full 19-character binary name silently matches nothing on WSL2."""
        with isolated_environment() as fixture:
            script = self.prepare(
                fixture,
                cbm_body=f"#!/bin/sh\nprintf '%s\\n' '{REFUSAL}' >&2\nexit 1\n",
                sessions_active=True,
            )
            self.run_script(fixture, script)
            probes = (fixture.root / "pgrep.log").read_text(encoding="utf-8").split()

        self.assertEqual(len(probes), 1, "probe must pass a bare pattern, no flags")
        pattern = probes[0]
        self.assertLessEqual(len(pattern), 15)
        self.assertRegex("codebase-memory", pattern)
        self.assertRegex("codebase-memory-mcp", pattern)


if __name__ == "__main__":
    unittest.main()
