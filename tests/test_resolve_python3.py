from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.support.fixtures import write_executable


REPO_ROOT = Path(__file__).resolve().parents[1]
HOOKS = REPO_ROOT / "dot_claude" / "hooks"
RESOLVE_PYTHON3 = HOOKS / "executable_resolve-python3"
SETTINGS_BASE = REPO_ROOT / "dot_claude" / "settings-base.json"
MIRROR_SETTINGS_BASE = (
    REPO_ROOT
    / "private_Documents"
    / "development"
    / "container-dotfiles"
    / "dotfiles"
    / "dot_claude"
    / "settings-base.json"
)
# Windows CreateProcess does not read shebangs, and the real hooks are
# dispatched by a shell, so the shim is always invoked through sh.
SH = shutil.which("sh")


@unittest.skipIf(SH is None, "sh is required")
class ResolvePython3Tests(unittest.TestCase):
    """The Claude Bash hooks run through this, so a wrong pick silences them."""

    def _run(self, path_dir: Path, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [SH, str(RESOLVE_PYTHON3), *args],
            env={"PATH": str(path_dir), "SYSTEMROOT": os.environ.get("SYSTEMROOT", "")},
            check=False,
            text=True,
            capture_output=True,
        )

    def _fake_bin(self, root: Path) -> Path:
        path_dir = root / "fake bin"
        path_dir.mkdir(parents=True)
        return path_dir

    def test_broken_python3_shim_is_rejected_and_py_is_used(self) -> None:
        with tempfile.TemporaryDirectory(prefix="resolve python3 ") as raw:
            root = Path(raw).resolve()
            path_dir = self._fake_bin(root)
            chosen = root / "chosen.txt"
            write_executable(
                path_dir / "python3",
                '#!/bin/sh\necho "Python was not found" >&2\nexit 49\n',
            )
            write_executable(
                path_dir / "py",
                f'#!/bin/sh\n[ "$1" = "-3" ] && shift\n'
                f"printf py > {shlex.quote(str(chosen))}\n"
                f'exec {shlex.quote(sys.executable)} "$@"\n',
            )

            # Quote-free: MSYS rewrites argv when sh execs a native python.exe.
            result = self._run(path_dir, "--", "-c", "print(6*7)")

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), "42")
            self.assertEqual(chosen.read_text(encoding="utf-8"), "py")

    def test_exit_status_propagates_from_the_resolved_interpreter(self) -> None:
        with tempfile.TemporaryDirectory(prefix="resolve python3 ") as raw:
            path_dir = self._fake_bin(Path(raw).resolve())
            write_executable(
                path_dir / "python3",
                f'#!/bin/sh\nexec {shlex.quote(sys.executable)} "$@"\n',
            )

            result = self._run(path_dir, "--", "-c", "raise SystemExit(23)")

            self.assertEqual(result.returncode, 23)

    def test_bare_python_is_the_last_resort(self) -> None:
        with tempfile.TemporaryDirectory(prefix="resolve python3 ") as raw:
            root = Path(raw).resolve()
            path_dir = self._fake_bin(root)
            chosen = root / "chosen.txt"
            write_executable(path_dir / "python3", "#!/bin/sh\nexit 49\n")
            write_executable(
                path_dir / "python",
                f"#!/bin/sh\nprintf python > {shlex.quote(str(chosen))}\n"
                f'exec {shlex.quote(sys.executable)} "$@"\n',
            )

            result = self._run(path_dir, "--", "-c", "print(6*7)")

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), "42")
            self.assertEqual(chosen.read_text(encoding="utf-8"), "python")

    def test_no_working_interpreter_fails_loudly(self) -> None:
        with tempfile.TemporaryDirectory(prefix="resolve python3 ") as raw:
            path_dir = self._fake_bin(Path(raw).resolve())
            write_executable(path_dir / "python3", "#!/bin/sh\nexit 49\n")

            result = self._run(path_dir, "--", "-c", "print(1)")

            self.assertEqual(result.returncode, 127)
            self.assertIn("no working Python 3 interpreter", result.stderr)

    def test_missing_arguments_are_refused(self) -> None:
        with tempfile.TemporaryDirectory(prefix="resolve python3 ") as raw:
            path_dir = self._fake_bin(Path(raw).resolve())

            result = self._run(path_dir)

            self.assertEqual(result.returncode, 64, "2 is Claude Code's blocking code")
            self.assertIn("requires a Python script", result.stderr)


class HookWiringTests(unittest.TestCase):
    """The regression was a command string, not the shim, so pin the wiring."""

    def _bash_hook_commands(self, settings: Path) -> list[str]:
        payload = json.loads(settings.read_text(encoding="utf-8"))
        found = []
        for entries in (payload.get("hooks") or {}).values():
            for entry in entries:
                # Substring, so an alternation matcher such as "Bash|Edit" counts.
                if "Bash" not in (entry.get("matcher") or ""):
                    continue
                for hook in entry.get("hooks", []):
                    found.append(hook.get("command", ""))
        return found

    def test_python_hooks_are_invoked_through_the_resolver(self) -> None:
        for settings in (SETTINGS_BASE, MIRROR_SETTINGS_BASE):
            with self.subTest(settings=settings.name):
                commands = self._bash_hook_commands(settings)
                self.assertTrue(commands, f"no Bash hooks found in {settings}")
                for command in commands:
                    if ".py" not in command:
                        continue
                    with self.subTest(command=command):
                        self.assertTrue(
                            command.startswith(
                                '"$HOME/.claude/hooks/resolve-python3" --'
                            ),
                            "a .py hook must resolve its interpreter, not a shebang",
                        )


if __name__ == "__main__":
    unittest.main()
