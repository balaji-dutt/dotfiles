from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import unittest

from tests.test_chezmoi_lifecycle_render import render_template


ROOT = Path(__file__).resolve().parents[1]
ACCOUNT = "smoketest"
OPT_IN = "DOTFILES_MACOS_SMOKE"
BUNDLE_ROOT = Path("/Users/Shared/dotfiles-shell-smoke")
CHEZMOI_SOURCE = Path("/opt/homebrew/bin/chezmoi")
CHEZMOI_BUNDLE = "bin/chezmoi"
BUNDLE_FILES = (
    "tests/support/__init__.py",
    "tests/support/powershell.py",
    "tests/test_chezmoi_lifecycle_render.py",
    "tests/test_macos_shell_startup_smoke.py",
    "dot_bashrc.tmpl",
    "dot_zshrc.tmpl",
    "dot_local/share/zsh/30-opencode-env.zsh.tmpl",
    "dot_local/share/zsh/40-session-env.zsh.tmpl",
    "dot_local/share/zsh/50-visuals-editor.zsh.tmpl",
    "dot_local/share/zsh/80-path.zsh.tmpl",
    "dot_local/share/zsh/90-late-integrations.zsh.tmpl",
)
ZSH_BEFORE_PROMPT = (
    "01-history-and-aliases",
    "zsh-modern-cli-hints",
    "beads-helpers",
)
ZSH_BEFORE_PROFILE = (
    "20-fzf-and-macos",
    "30-opencode-env",
)
ZSH_AFTER_PROFILE = (
    "40-session-env",
    "50-visuals-editor",
    "55-terminal-title",
    "60-wsl-native-commands",
    "65-package-wrappers",
    "70-git-custom-aliases",
    "80-path",
    "90-late-integrations",
)
ZSH_HELPERS = (*ZSH_BEFORE_PROMPT, *ZSH_BEFORE_PROFILE, *ZSH_AFTER_PROFILE)


def fingerprint(files: dict[str, str]) -> str:
    payload = json.dumps(files, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()


def verified_bundle(root: Path = ROOT) -> None:
    manifest = json.loads((root / "smoke-bundle.json").read_text(encoding="utf-8"))
    files = manifest["files"]
    if set(files) != set((*BUNDLE_FILES, CHEZMOI_BUNDLE)):
        raise AssertionError("smoke bundle file list differs from the allowlist")
    for relative in files:
        candidate = root / relative
        if not candidate.is_file() or candidate.is_symlink():
            raise AssertionError(f"invalid smoke bundle source: {relative}")
        digest = hashlib.sha256(candidate.read_bytes()).hexdigest()
        if digest != files[relative]:
            raise AssertionError(f"smoke bundle content differs: {relative}")
    if fingerprint(files) != manifest["fingerprint"]:
        raise AssertionError("smoke bundle fingerprint differs")


def stage_bundle(destination: Path) -> None:
    parent = destination.parent
    if (destination != BUNDLE_ROOT
            or parent.is_symlink() or not parent.is_dir()):
        raise ValueError("stage only into /Users/Shared/dotfiles-shell-smoke")
    if destination.is_symlink():
        raise ValueError("smoke bundle destination is a symlink")
    if destination.exists():
        if destination.stat().st_uid != os.getuid():
            raise ValueError("smoke bundle destination is not owned by the staging user")
        existing = json.loads((destination / "smoke-bundle.json").read_text(encoding="utf-8"))
        old_files = existing["files"]
        if set(old_files) not in (set(BUNDLE_FILES), set((*BUNDLE_FILES, CHEZMOI_BUNDLE))):
            raise ValueError("existing bundle is not a recognized smoke snapshot")
        for relative, digest in old_files.items():
            candidate = destination / relative
            if candidate.is_symlink() or hashlib.sha256(candidate.read_bytes()).hexdigest() != digest:
                raise ValueError("existing smoke bundle content differs")
        if fingerprint(old_files) != existing["fingerprint"]:
            raise ValueError("existing smoke bundle fingerprint differs")
        allowed = {Path("smoke-bundle.json"), *(Path(name) for name in old_files)}
        allowed.update(parent for file_path in tuple(allowed) for parent in file_path.parents if parent != Path("."))
        entries = tuple(destination.rglob("*"))
        if (any(file_path.is_symlink() for file_path in entries)
                or {file_path.relative_to(destination) for file_path in entries} != allowed):
            raise ValueError("existing smoke bundle contains unexpected paths")
    staging = Path(tempfile.mkdtemp(prefix=".shell-smoke-", dir=parent))
    backup = None
    replaced = False
    try:
        files: dict[str, str] = {}
        for relative in BUNDLE_FILES:
            source = ROOT / relative
            if not source.is_file() or source.is_symlink():
                raise ValueError(f"invalid source: {relative}")
            target = staging / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            target.chmod(0o644)
            digest = hashlib.sha256(source.read_bytes()).hexdigest()
            if hashlib.sha256(target.read_bytes()).hexdigest() != digest:
                raise AssertionError(f"staged source differs: {relative}")
            files[relative] = digest
        executable = staging / CHEZMOI_BUNDLE
        executable.parent.mkdir(parents=True, exist_ok=True)
        if not CHEZMOI_SOURCE.is_file():
            raise ValueError("Homebrew chezmoi is required to stage the smoke bundle")
        shutil.copyfile(CHEZMOI_SOURCE, executable)
        executable.chmod(0o755)
        files[CHEZMOI_BUNDLE] = hashlib.sha256(executable.read_bytes()).hexdigest()
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, check=True,
            capture_output=True, text=True, timeout=10,
        ).stdout.strip()
        manifest = {"source_head": head, "files": files, "fingerprint": fingerprint(files)}
        (staging / "smoke-bundle.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        for directory in (staging, *staging.rglob("*")):
            if directory.is_dir():
                directory.chmod(0o755)
        verified_bundle(staging)
        if destination.exists():
            backup = Path(tempfile.mkdtemp(prefix=".shell-smoke-old-", dir=parent))
            backup.rmdir()
            destination.rename(backup)
        try:
            staging.rename(destination)
        except Exception:
            if backup is not None:
                backup.rename(destination)
            raise
        replaced = True
        print(f"staged smoke bundle: {manifest['fingerprint']} (HEAD {head})")
    finally:
        if staging.exists():
            shutil.rmtree(staging)
        if replaced and backup is not None:
            shutil.rmtree(backup)


@unittest.skipUnless(sys.platform == "darwin", "native macOS required")
@unittest.skipUnless(os.environ.get(OPT_IN) == "1", "macOS smoke requires explicit opt-in")
class MacosShellStartupSmoke(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        import pwd

        if os.getuid() == 0 or os.geteuid() != os.getuid():
            raise AssertionError("run as an unprivileged logged-in account")
        if pwd.getpwuid(os.getuid()).pw_name != ACCOUNT:
            raise AssertionError("run only from the dedicated macOS smoke account")
        if ROOT != BUNDLE_ROOT:
            raise AssertionError("run only from the staged smoke bundle")
        verified_bundle()
        if shutil.which("chezmoi") != str(ROOT / CHEZMOI_BUNDLE):
            raise AssertionError("use the verified smoke bundle chezmoi executable")

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="macos-shell-smoke-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.home = self.root / "home"
        self.bin = self.root / "bin"
        self.tmp = self.root / "tmp"
        self.log = self.root / "startup.log"
        for directory in (self.home, self.bin, self.tmp):
            directory.mkdir()
        self.env = {
            "HOME": str(self.home),
            "ZDOTDIR": str(self.home),
            "XDG_CONFIG_HOME": str(self.home / ".config"),
            "XDG_CACHE_HOME": str(self.home / ".cache"),
            "XDG_DATA_HOME": str(self.home / ".local/share"),
            "TMPDIR": str(self.tmp),
            "PATH": f"{self.bin}:/usr/bin:/bin",
            "TERM": "dumb",
            "LC_ALL": "C",
            "USER": ACCOUNT,
            "LOGNAME": ACCOUNT,
            "SMOKE_LOG": str(self.log),
        }
        self.write(".config/opencode/opencode.env", "SMOKE_ENV_VALUE=fixture_loaded\n")
        self.write(".config/opencode/opencode-profile.sh", 'printf "profile\\n" >> "$SMOKE_LOG"\n')
        self.command("python3", "#!/bin/sh\n[ \"$1\" = -c ] || exit 97\nprintf '3.11\\n'\n")

    def write(self, relative: str, text: str) -> Path:
        destination = self.home / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(text, encoding="utf-8")
        return destination

    def command(self, name: str, body: str) -> None:
        destination = self.bin / name
        destination.write_text(body, encoding="utf-8")
        destination.chmod(0o755)

    def render(self, relative: str) -> str:
        def home_data(data: dict[str, object]) -> None:
            chezmoi = data["chezmoi"]
            assert isinstance(chezmoi, dict)
            chezmoi["homeDir"] = str(self.home)
            chezmoi["destDir"] = str(self.home)

        return render_template(relative, "macos", configure=home_data, environment=self.env)

    def guard_commands(self) -> None:
        for name in (
            "bd", "dolt", "op", "pbcopy", "pbpaste", "curl", "wget", "ssh",
            "security", "osascript", "open", "npm", "git", "nc", "scp",
            "softwareupdate", "defaults",
        ):
            self.command(name, '#!/bin/sh\nprintf "forbidden:%s\\n" "$0" >> "$SMOKE_LOG"\nexit 97\n')

    def direnv(self) -> None:
        self.command(
            "direnv",
            '#!/bin/sh\nprintf "direnv:%s %s\\n" "$1" "$2" >> "$SMOKE_LOG"\n'
            'case "$1:$2" in export:zsh) printf "export SMOKE_DIRENV=fixture\\n" ;;\n'
            'hook:*) printf ":\\n" ;;\n'
            '*) exit 97 ;; esac\n',
        )

    def run_shell(self, argv: list[str], script: str) -> tuple[list[str], str]:
        proc = subprocess.Popen(
            [*argv, script], cwd=self.root, env=self.env, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            start_new_session=True,
        )
        try:
            stdout, stderr = proc.communicate(timeout=8)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.communicate()
            self.fail(f"{argv[0]} interactive startup timed out")
        self.assertEqual(proc.returncode, 0, stderr)
        if argv[0] == "/bin/bash":
            stderr = re.sub(
                r"\Abash: cannot set terminal process group \(-?\d+\): [^\n]+\n"
                r"bash: no job control in this shell\n", "", stderr,
            )
        self.assertEqual(stderr, "")
        self.assertTrue(self.log.is_file(), "startup did not reach fixture helpers")
        events = self.log.read_text(encoding="utf-8").splitlines()
        self.assertFalse(any(event.startswith("forbidden:") for event in events), events)
        return events, stdout

    def test_bash_optional_tools_absent(self) -> None:
        self.guard_commands()
        self.write(".local/share/beads-helpers.bash", 'printf "beads\\n" >> "$SMOKE_LOG"\n')
        self.write(".bash_aliases", 'printf "aliases\\n" >> "$SMOKE_LOG"\n')
        self.write(".bashrc", self.render("dot_bashrc.tmpl"))
        events, output = self.run_shell(
            ["/bin/bash", "--noprofile", "-ic"],
            'printf "result:%s|%s|%s|%s\\n" "$-" "$SMOKE_ENV_VALUE" "${PLANNOTATOR_PORT:-}" "${OP_BIOMETRIC_UNLOCK_ENABLED:-}"',
        )
        self.assertEqual(events, ["beads", "aliases", "profile"])
        self.assertRegex(output, r"^result:[^|]*i[^|]*\|fixture_loaded\|8999\|true\n$")

    def test_bash_fake_direnv_runs_after_environment(self) -> None:
        self.guard_commands()
        self.direnv()
        self.write(".local/share/beads-helpers.bash", 'printf "beads\\n" >> "$SMOKE_LOG"\n')
        self.write(".bash_aliases", 'printf "aliases\\n" >> "$SMOKE_LOG"\n')
        (self.home / ".local/share/mise/shims").mkdir(parents=True)
        self.write(".bashrc", self.render("dot_bashrc.tmpl"))
        events, output = self.run_shell(
            ["/bin/bash", "--noprofile", "-ic"],
            'printf "result:%s|%s|%s\\n" "$-" "$PATH" "$SMOKE_ENV_VALUE"',
        )
        self.assertEqual(events, ["beads", "aliases", "profile", "direnv:hook bash"])
        parts = output.removeprefix("result:").rstrip("\n").split("|")
        self.assertIn("i", parts[0])
        self.assertEqual(parts[1].split(":").count(str(self.home / ".local/share/mise/shims")), 1)
        self.assertEqual(parts[2], "fixture_loaded")

    def prepare_zsh(self, *, with_direnv: bool) -> None:
        self.guard_commands()
        if with_direnv:
            self.direnv()
        self.command("nano", "#!/bin/sh\nexit 0\n")
        for helper in ZSH_HELPERS:
            if helper == "zsh-modern-cli-hints":
                relative = ".local/share/zsh-modern-cli-hints.zsh"
            elif helper == "beads-helpers":
                relative = ".local/share/beads-helpers.zsh"
            else:
                relative = f".local/share/zsh/{helper}.zsh"
            if helper in {"30-opencode-env", "40-session-env", "50-visuals-editor", "80-path", "90-late-integrations"}:
                body = self.render(f"dot_local/share/zsh/{helper}.zsh.tmpl")
            else:
                body = ""
            body = f'print -r -- "{helper}" >> "$SMOKE_LOG"\n' + body
            if helper == "90-late-integrations":
                body += '_opencode_install_shell_hooks() { print -r -- "opencode" >> "$SMOKE_LOG"; }\n'
            self.write(relative, body)
        self.write(
            f".cache/p10k-instant-prompt-{ACCOUNT}.zsh",
            'print -r -- "instant-prompt" >> "$SMOKE_LOG"\n',
        )
        self.write(".zshrc", self.render("dot_zshrc.tmpl"))

    def test_zsh_optional_tools_absent(self) -> None:
        self.prepare_zsh(with_direnv=False)
        events, output = self.run_shell(
            ["/bin/zsh", "-ic"],
            'print -r -- "result:$-|$SMOKE_ENV_VALUE|$PLANNOTATOR_PORT|$OP_BIOMETRIC_UNLOCK_ENABLED|$HISTFILE|$EDITOR|${PLANNOTATOR_REMOTE:-}"',
        )
        self.assertEqual(events, [*ZSH_BEFORE_PROMPT, "instant-prompt", *ZSH_BEFORE_PROFILE,
                                  "profile", *ZSH_AFTER_PROFILE, "opencode"])
        parts = output.removeprefix("result:").rstrip("\n").split("|")
        self.assertIn("i", parts[0])
        self.assertEqual(parts[1:4], ["fixture_loaded", "8999", "true"])
        self.assertEqual(parts[4], str(self.home / ".m4mbp_zsh_history"))
        self.assertEqual(parts[5], str(self.bin / "nano"))
        self.assertEqual(parts[6], "")

    def test_zsh_fake_direnv_exports_before_instant_prompt(self) -> None:
        self.prepare_zsh(with_direnv=True)
        events, output = self.run_shell(
            ["/bin/zsh", "-ic"],
            'print -r -- "result:$-|$SMOKE_DIRENV|$SMOKE_ENV_VALUE|$PLANNOTATOR_PORTS_BUILD|$PYTHON_USER_BIN"',
        )
        self.assertEqual(events, [*ZSH_BEFORE_PROMPT, "direnv:export zsh", "instant-prompt",
                                  *ZSH_BEFORE_PROFILE, "profile", *ZSH_AFTER_PROFILE,
                                  "direnv:hook zsh", "opencode"])
        parts = output.removeprefix("result:").rstrip("\n").split("|")
        self.assertIn("i", parts[0])
        self.assertEqual(parts[1:4], ["fixture", "fixture_loaded", "8993-8998"])
        self.assertEqual(parts[4], str(self.home / "Library/Python/3.11/bin"))


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--stage":
        stage_bundle(Path(sys.argv[2]))
    else:
        unittest.main()
