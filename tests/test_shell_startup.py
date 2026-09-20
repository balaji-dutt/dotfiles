from __future__ import annotations

import os
from pathlib import Path
import shlex
import shutil
import subprocess
import unittest

from tests.support.fixtures import isolated_environment, write_executable
from tests.test_chezmoi_lifecycle_render import render_template


REPO_ROOT = Path(__file__).resolve().parents[1]
BASH = shutil.which("bash")
ZSH = shutil.which("zsh")


class ShellStartupHarness(unittest.TestCase):
    def setUp(self) -> None:
        context = isolated_environment(prefix="shell-startup-")
        self.fixture = context.__enter__()
        self.addCleanup(context.__exit__, None, None, None)

    def write_home(self, relative: str, content: str) -> Path:
        destination = self.fixture.home / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(content, encoding="utf-8")
        return destination

    def run_bash(
        self,
        script: str,
        *,
        env_updates: dict[str, str] | None = None,
        interactive: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        env = self.fixture.env.copy()
        if env_updates:
            env.update(env_updates)
        arguments = [BASH, "--noprofile", "--norc"]
        arguments.extend(["-ic" if interactive else "-c", script])
        return subprocess.run(
            arguments,
            cwd=self.fixture.root,
            env=env,
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
        )

    def run_zsh(
        self,
        script: str,
        *,
        env_updates: dict[str, str] | None = None,
        interactive: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        env = self.fixture.env.copy()
        if env_updates:
            env.update(env_updates)
        return subprocess.run(
            [ZSH, "-df" + ("i" if interactive else ""), "-c", script],
            cwd=self.fixture.root,
            env=env,
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
        )

    def render_with_path(self, relative: str, platform: str) -> str:
        env = os.environ.copy()
        env["PATH"] = self.fixture.env["PATH"]
        return render_template(relative, platform, environment=env)


@unittest.skipUnless(BASH, "bash is required")
class BashStartupTests(ShellStartupHarness):
    def test_noninteractive_startup_loads_only_beads_helper(self) -> None:
        self.write_home(
            ".local/share/beads-helpers.bash",
            "bd() { printf 'fixture-bd:%s\\n' \"$*\"; }\n",
        )
        bashrc = self.write_home(".bashrc", render_template("dot_bashrc.tmpl", "linux"))
        result = self.run_bash(f'source "{bashrc}"; bd show dots-1; printf "after\\n"')

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "fixture-bd:show dots-1\nafter\n")
        self.assertEqual(result.stderr, "")

    def test_noninteractive_startup_is_silent_without_optional_files(self) -> None:
        bashrc = self.write_home(".bashrc", render_template("dot_bashrc.tmpl", "linux"))
        result = self.run_bash(f'source "{bashrc}"')

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "")

    def test_wsl_interactive_environment_and_missing_pageant_tools_are_safe(self) -> None:
        calls = self.fixture.root / "calls.log"
        write_executable(
            self.fixture.fake_bin / "direnv",
            f"#!/bin/sh\nprintf 'direnv:%s\\n' \"$*\" >> {shlex.quote(str(calls))}\n",
        )
        write_executable(self.fixture.fake_bin / "nano", "#!/bin/sh\nexit 0\n")
        write_executable(self.fixture.fake_bin / "visudo", "#!/bin/sh\nexit 0\n")
        bashrc = self.write_home(".bashrc", render_template("dot_bashrc.tmpl", "wsl2"))
        result = self.run_bash(
            f'source "{bashrc}"; printf "%s\\n" "$LANG|$EDITOR|$SSH_AUTH_SOCK|$PLANNOTATOR_REMOTE"',
            interactive=True,
            env_updates={"TERM": "dumb"},
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        values = result.stdout.strip().split("|")
        self.assertEqual(values[0], "en_GB.UTF-8")
        self.assertNotEqual(values[1], "")
        self.assertEqual(values[2], "/tmp/wsl2-ssh-agent/ssh-agent.sock")
        self.assertEqual(values[3], "1")
        self.assertEqual(calls.read_text(encoding="utf-8"), "direnv:hook bash\n")

    def test_mise_shims_are_prepended_once(self) -> None:
        shims = self.fixture.home / ".local/share/mise/shims"
        shims.mkdir(parents=True)
        write_executable(self.fixture.fake_bin / "direnv", "#!/bin/sh\nexit 0\n")
        bashrc = self.write_home(".bashrc", render_template("dot_bashrc.tmpl", "macos"))
        initial = f"{self.fixture.fake_bin}:/usr/bin:{shims}:/bin"
        result = self.run_bash(
            f'source "{bashrc}"; source "{bashrc}"; printf "%s\\n" "$PATH"',
            interactive=True,
            env_updates={"PATH": initial, "TERM": "dumb"},
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        entries = result.stdout.strip().split(os.pathsep)
        self.assertEqual(entries.count(str(shims)), 1)


@unittest.skipUnless(ZSH, "zsh is required")
class ZshStartupOrderTests(ShellStartupHarness):
    ORDERED_HELPERS = (
        ".local/share/zsh/01-history-and-aliases.zsh",
        ".local/share/zsh-modern-cli-hints.zsh",
        ".local/share/beads-helpers.zsh",
        ".local/share/zsh/20-fzf-and-macos.zsh",
        ".local/share/zsh/30-opencode-env.zsh",
        ".local/share/zsh/40-session-env.zsh",
        ".local/share/zsh/50-visuals-editor.zsh",
        ".local/share/zsh/55-terminal-title.zsh",
        ".local/share/zsh/60-wsl-native-commands.zsh",
        ".local/share/zsh/65-package-wrappers.zsh",
        ".local/share/zsh/70-git-custom-aliases.zsh",
        ".local/share/zsh/80-path.zsh",
        ".local/share/zsh/90-late-integrations.zsh",
    )

    def test_missing_optional_files_produce_no_output_or_hang(self) -> None:
        zshrc = self.write_home(".zshrc", render_template("dot_zshrc.tmpl", "linux"))
        result = self.run_zsh(f'source "{zshrc}"')

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "")

    def test_helpers_load_in_order_and_opencode_installs_last(self) -> None:
        log = self.fixture.root / "startup.log"
        for relative in self.ORDERED_HELPERS:
            token = Path(relative).name
            body = f"print -r -- {token} >> $STARTUP_LOG\n"
            if relative.endswith("90-late-integrations.zsh"):
                body += "_opencode_install_shell_hooks() { print -r -- opencode >> $STARTUP_LOG; }\n"
            self.write_home(relative, body)
        zshrc = self.write_home(".zshrc", render_template("dot_zshrc.tmpl", "linux"))
        result = self.run_zsh(
            f'source "{zshrc}"', env_updates={"STARTUP_LOG": str(log)}
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        expected = [Path(relative).name for relative in self.ORDERED_HELPERS]
        expected.append("opencode")
        self.assertEqual(log.read_text(encoding="utf-8").splitlines(), expected)

    def test_wsl_antidote_fsck_scope_and_empty_cache_cleanup(self) -> None:
        antidote_log = self.fixture.root / "antidote.log"
        self.write_home(
            ".antidote/antidote.zsh",
            "antidote() { print -r -- \"${GIT_CONFIG_COUNT:-unset}|$*\" >> $ANTIDOTE_LOG; }\n",
        )
        self.write_home(".zsh_plugins.txt", "plugin/example\n")
        static_cache = self.write_home(".zsh_plugins.zsh", "")
        zshrc = self.write_home(".zshrc", render_template("dot_zshrc.tmpl", "wsl2"))
        result = self.run_zsh(
            f'source "{zshrc}"; print -r -- "outside:${{GIT_CONFIG_COUNT:-unset}}"',
            env_updates={"ANTIDOTE_LOG": str(antidote_log), "WSL_DISTRO_NAME": "Debian"},
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "outside:unset\n")
        self.assertEqual(
            antidote_log.read_text(encoding="utf-8").splitlines(),
            [f"3|load {self.fixture.home}/.zsh_plugins.txt {self.fixture.home}/.zsh_plugins.zsh"],
        )
        self.assertFalse(static_cache.exists())


@unittest.skipUnless(ZSH, "zsh is required")
class ModernCliHintTests(ShellStartupHarness):
    def source_hints(
        self,
        command: str,
        *,
        env_updates: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        hints = REPO_ROOT / "dot_local/share/zsh-modern-cli-hints.zsh"
        return self.run_zsh(f'source "{hints}"; {command}', env_updates=env_updates)

    def test_aliases_and_rgf_use_available_tools(self) -> None:
        log = self.fixture.root / "fzf.log"
        write_executable(
            self.fixture.fake_bin / "rg",
            "#!/bin/sh\nprintf 'alpha\\nbeta\\n'\n",
        )
        write_executable(
            self.fixture.fake_bin / "fzf",
            f"#!/bin/sh\nprintf '%s\\n' \"$*\" > {shlex.quote(str(log))}\nhead -n 1\n",
        )
        result = self.source_hints("alias ri; rgf needle")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("rg -i", result.stdout)
        self.assertTrue(result.stdout.endswith("alpha\n"), result.stdout)
        self.assertEqual(log.read_text(encoding="utf-8"), "--query needle\n")

    def test_rgf_falls_back_without_fzf_and_fails_without_rg(self) -> None:
        write_executable(self.fixture.fake_bin / "rg", "#!/bin/sh\nprintf 'file.txt\\n'\nexit 7\n")
        isolated_path = {"PATH": str(self.fixture.fake_bin)}
        fallback = self.source_hints("rgf", env_updates=isolated_path)
        self.assertEqual(fallback.returncode, 7)
        self.assertIn("falling back", fallback.stderr)
        self.assertEqual(fallback.stdout, "file.txt\n")

        (self.fixture.fake_bin / "rg").unlink()
        missing = self.source_hints("rgf", env_updates=isolated_path)
        self.assertEqual(missing.returncode, 127)
        self.assertIn("rg is not available", missing.stderr)

    def test_antipattern_hints_skip_disabled_and_benign_commands(self) -> None:
        write_executable(self.fixture.fake_bin / "rg", "#!/bin/sh\nexit 0\n")
        write_executable(self.fixture.fake_bin / "pgrep", "#!/bin/sh\nexit 0\n")
        result = self.source_hints(
            "_nag_antipatterns 'cat file | grep term'; "
            "_nag_antipatterns 'grep term file | wc -l'; "
            "_nag_antipatterns 'ps aux | grep daemon'; "
            "_nag_antipatterns 'printf cat | grep'; "
            "ZSH_ANTIPATTERN_NAGS=0 _nag_antipatterns 'cat file | grep term'"
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr.count("hint:"), 3, result.stderr)

    def test_preexec_hook_is_interactive_only(self) -> None:
        hints = REPO_ROOT / "dot_local/share/zsh-modern-cli-hints.zsh"
        registration = (
            'if (( ${preexec_functions[(Ie)_nag_antipatterns]} )); '
            "then print -r -- registered; else print -r -- absent; fi"
        )
        noninteractive = self.run_zsh(
            f'source "{hints}"; {registration}'
        )
        interactive = self.run_zsh(
            f'source "{hints}"; {registration}',
            interactive=True,
            env_updates={"TERM": "dumb"},
        )

        self.assertEqual(noninteractive.returncode, 0, noninteractive.stderr)
        self.assertEqual(noninteractive.stdout.strip(), "absent")
        self.assertEqual(interactive.returncode, 0, interactive.stderr)
        self.assertEqual(interactive.stdout.strip(), "registered")


@unittest.skipUnless(ZSH, "zsh is required")
class ZshFragmentContractTests(ShellStartupHarness):
    def test_git_helper_exposes_function_and_rejects_bad_arguments(self) -> None:
        helper = REPO_ROOT / "dot_local/share/git-helpers.zsh"
        result = self.run_zsh(
            f'source "{helper}"; '
            'print -r -- "${+functions[git_pull_rebase_then_apply_stash]}"; '
            "git_pull_rebase_then_apply_stash --unknown"
        )

        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "1\n")
        self.assertIn("unknown option", result.stderr)

    def test_git_helper_debug_exports_trace_to_git(self) -> None:
        log = self.fixture.root / "git.log"
        write_executable(
            self.fixture.fake_bin / "git",
            f"""#!/bin/sh
printf '%s|%s|%s\\n' "${{GIT_TRACE:-}}" "${{GIT_TRACE_SETUP:-}}" "$*" >> {shlex.quote(str(log))}
case "$*" in
  'rev-parse --is-inside-work-tree') printf 'true\\n' ;;
  'rev-parse --show-toplevel') pwd ;;
  'rev-parse --path-format=absolute --git-common-dir') printf '%s/.git\\n' "$PWD" ;;
  'status --porcelain') : ;;
esac
exit 0
""",
        )
        helper = REPO_ROOT / "dot_local/share/git-helpers.zsh"
        result = self.run_zsh(f'source "{helper}"; git_pull_rebase_then_apply_stash --debug')

        self.assertEqual(result.returncode, 0, result.stderr)
        lines = log.read_text(encoding="utf-8").splitlines()
        self.assertTrue(lines)
        self.assertTrue(all(line.startswith("1|1|") for line in lines), lines)
        self.assertTrue(any(line.endswith("pull --rebase") for line in lines), lines)

    def test_wsl_command_resolution_prefers_linux_home_and_deduplicates_path(self) -> None:
        home_bin = self.fixture.home / "bin"
        home_bin.mkdir()
        preferred = write_executable(home_bin / "npm", "#!/bin/sh\nexit 0\n")
        fragment = self.write_home(
            "60-wsl-native-commands.zsh",
            render_template("dot_local/share/zsh/60-wsl-native-commands.zsh.tmpl", "wsl2"),
        )
        result = self.run_zsh(
            f'source "{fragment}"; '
            '_wsl2_prepend_path_entry "$HOME/bin"; _wsl2_prepend_path_entry "$HOME/bin"; '
            'print -r -- "$(_wsl2_resolve_native_path_command npm)"; '
            'print -r -- "${(j:|:)path}"'
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        lines = result.stdout.splitlines()
        self.assertEqual(lines[0], str(preferred))
        self.assertEqual(lines[1].split("|").count(str(home_bin)), 1)

    def test_package_wrapper_propagates_failure_without_manifest_update(self) -> None:
        write_executable(self.fixture.fake_bin / "npm", "#!/bin/sh\nexit 23\n")
        fragment = self.write_home(
            "65-package-wrappers.zsh",
            self.render_with_path("dot_local/share/zsh/65-package-wrappers.zsh.tmpl", "linux"),
        )
        dotfiles = self.fixture.root / "dotfiles"
        (dotfiles / "configs").mkdir(parents=True)
        script = (
            '_resolve_preferred_command_path() { command -v "$1" >/dev/null; }; '
            '_run_preferred_command() { command "$@"; }; '
            f'source "{fragment}"; DOTFILES_REPO="{dotfiles}"; npm install -g fixture'
        )
        result = self.run_zsh(script)

        self.assertEqual(result.returncode, 23, result.stderr)
        self.assertFalse((dotfiles / "configs/npm_globals.txt").exists())

    def test_session_and_editor_fragments_select_expected_values(self) -> None:
        write_executable(self.fixture.fake_bin / "nano", "#!/bin/sh\nexit 0\n")
        write_executable(self.fixture.fake_bin / "code", "#!/bin/sh\nexit 0\n")
        session = self.write_home(
            "40-session-env.zsh",
            render_template("dot_local/share/zsh/40-session-env.zsh.tmpl", "wsl2"),
        )
        editor = self.write_home(
            "50-visuals-editor.zsh",
            self.render_with_path("dot_local/share/zsh/50-visuals-editor.zsh.tmpl", "wsl2"),
        )
        result = self.run_zsh(
            "promptinit() { :; }; autoload() { :; }; "
            f'source "{session}"; source "{editor}"; '
            'print -r -- "$HISTFILE|$EDITOR|$VISUAL|$LANG"'
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        values = result.stdout.strip().split("|")
        self.assertTrue(values[0].endswith(".debianwsl2_zsh_history"), values[0])
        self.assertEqual(values[1], f"{self.fixture.fake_bin}/code --wait")
        self.assertEqual(values[2], values[1])
        self.assertEqual(values[3], "en_GB.UTF-8")


if __name__ == "__main__":
    unittest.main()
