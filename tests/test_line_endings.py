from __future__ import annotations

import subprocess
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SHELL_SUFFIXES = (".sh", ".sh.tmpl", ".bash", ".zsh", ".zsh.tmpl")
SHELL_RC_NAMES = frozenset(
    name + suffix
    for name in (".envrc", "dot_bashrc", "dot_zshrc", "dot_zpreztorc")
    for suffix in ("", ".tmpl")
)
SYMLINK_MODE = "120000"


def git(*args: str, stdin: str | None = None) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=REPO_ROOT,
        input=stdin,
        text=True,
        encoding="utf-8",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    ).stdout


def tracked_modes() -> dict[str, str]:
    modes: dict[str, str] = {}
    for entry in git("ls-files", "-z", "--stage").split("\0"):
        if entry:
            metadata, relpath = entry.split("\t", 1)
            modes[relpath] = metadata.split()[0]
    return modes


def starts_with_shebang(relpath: str) -> bool:
    try:
        with (REPO_ROOT / relpath).open("rb") as stream:
            return stream.read(2) == b"#!"
    except OSError:
        return False


def is_shell_dispatched(relpath: str) -> bool:
    name = relpath.rsplit("/", 1)[-1]
    return (
        name.endswith(SHELL_SUFFIXES)
        or name in SHELL_RC_NAMES
        or starts_with_shebang(relpath)
    )


def shell_dispatched_paths() -> list[str]:
    return sorted(
        relpath
        for relpath, mode in tracked_modes().items()
        if mode != SYMLINK_MODE and is_shell_dispatched(relpath)
    )


def attributes(paths: list[str]) -> dict[str, dict[str, str]]:
    fields = git("check-attr", "--stdin", "-z", "text", "eol", stdin="\0".join(paths) + "\0")
    parts = fields.split("\0")
    result: dict[str, dict[str, str]] = {}
    for index in range(0, len(parts) - 2, 3):
        relpath, name, value = parts[index : index + 3]
        result.setdefault(relpath, {})[name] = value
    return result


def index_endings(paths: list[str]) -> dict[str, str]:
    wanted = set(paths)
    endings: dict[str, str] = {}
    for entry in git("ls-files", "-z", "--eol").split("\0"):
        if entry:
            metadata, relpath = entry.split("\t", 1)
            if relpath in wanted:
                endings[relpath] = metadata.split()[0]
    return endings


class ShellLineEndingTests(unittest.TestCase):
    maxDiff = None

    @classmethod
    def setUpClass(cls) -> None:
        cls.paths = shell_dispatched_paths()

    def test_detection_finds_known_shell_entry_points(self) -> None:
        for relpath in (
            "assets/guarded-main-sync",
            "dot_claude/hooks/executable_resolve-python3",
            "private_dot_config/git/template/hooks/executable_pre-push",
            "dot_zshrc.tmpl",
        ):
            self.assertIn(relpath, self.paths)

    def test_shell_dispatched_files_are_pinned_to_lf(self) -> None:
        missing = [
            relpath
            for relpath, values in attributes(self.paths).items()
            if values.get("text") != "set" or values.get("eol") != "lf"
        ]
        self.assertEqual(
            missing,
            [],
            "add a `text eol=lf` rule to .gitattributes for these shell-dispatched files",
        )

    def test_shell_dispatched_files_are_stored_as_lf(self) -> None:
        stored_crlf = [
            relpath
            for relpath, ending in index_endings(self.paths).items()
            if ending not in {"i/lf", "i/none"}
        ]
        self.assertEqual(
            stored_crlf,
            [],
            "run `git add --renormalize` on these files so the index holds LF",
        )


if __name__ == "__main__":
    unittest.main()
