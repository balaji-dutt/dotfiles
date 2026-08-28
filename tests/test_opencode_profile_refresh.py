from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import textwrap
import time
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
HOST_HELPER = REPO_ROOT / "private_dot_config/opencode/opencode-profile.sh"
CONTAINER_HELPER = (
    REPO_ROOT
    / "private_Documents/development/container-dotfiles/dotfiles"
    / "private_dot_config/opencode/opencode-profile.sh"
)
SYNC_HELPER = (
    REPO_ROOT
    / "private_dot_config/opencode/opencode-sync-workspace-overrides.sh"
)
POWERSHELL_ENV_TEMPLATE = (
    REPO_ROOT / "private_dot_config/powershell/opencode-env.ps1.tmpl"
)
POWERSHELL_PROMPT_TEMPLATE = (
    REPO_ROOT / "private_dot_config/powershell/prompt.ps1.tmpl"
)
PWSH = shutil.which("pwsh")
ZSH = shutil.which("zsh")


def shell_quote(value: str | Path) -> str:
    return "'" + str(value).replace("'", "'\"'\"'") + "'"


def ps_quote(value: str | Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def render_windows_template(
    source_path: Path,
    destination: Path,
    *,
    omit_starship_init: bool = False,
) -> None:
    controls = {
        '{{- if ne .chezmoi.os "windows" -}}',
        "{{- /* not Windows */ -}}",
        "{{- else -}}",
        "{{- end -}}",
    }
    source = source_path.read_text(encoding="utf-8")
    source = "\n".join(
        line for line in source.splitlines() if line not in controls
    )
    source = source.replace("{{ .plannotator_port }}", "9999")
    if omit_starship_init:
        source = source.replace(
            '$starship = Get-Command starship -CommandType Application -ErrorAction SilentlyContinue\n'
            'if ($starship) {\n'
            '  Invoke-Expression (& $starship.Source init powershell)\n'
            '}\n',
            "",
        )
    destination.write_text(source + "\n", encoding="utf-8")


class OpenCodeProfileRefreshTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.home = self.root / "home"
        self.config_home = self.root / "config"
        self.opencode_home = self.config_home / "opencode"
        self.workspace = self.root / "workspace"
        self.profile_dir = self.opencode_home / "profiles" / "defaults"
        self.overlay_dir = self.opencode_home / "profiles" / "review"
        self.profile_dir.mkdir(parents=True)
        self.overlay_dir.mkdir(parents=True)
        self.workspace.mkdir()
        subprocess.run(
            ["git", "init", "-q"], cwd=self.workspace, check=True
        )
        (self.profile_dir / "opencode.jsonc").write_text(
            "{}\n", encoding="utf-8"
        )
        (self.overlay_dir / "opencode.jsonc").write_text(
            "{}\n", encoding="utf-8"
        )
        self.write_canonical("3.1.13")

    def write_canonical(self, version: str) -> None:
        config = {
            "$schema": "https://opencode.ai/config.json",
            "plugin": [f"@tarquinen/opencode-dcp@{version}"],
            "agent": {"build": {"model": "openai/base"}},
        }
        path = self.opencode_home / "opencode.jsonc"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(config) + "\n", encoding="utf-8")

    def install_sync_helper(self, *, container_fallback: bool) -> Path:
        if container_fallback:
            destination = (
                self.home
                / ".local/bin/opencode-sync-workspace-overrides"
            )
        else:
            destination = (
                self.opencode_home
                / "opencode-sync-workspace-overrides.sh"
            )
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(SYNC_HELPER, destination)
        destination.chmod(0o755)
        return destination

    def install_counting_sync_helper(
        self, *, container_fallback: bool
    ) -> tuple[Path, Path]:
        destination = self.install_sync_helper(
            container_fallback=container_fallback
        )
        marker = self.root / (
            "container-sync-called" if container_fallback else "host-sync-called"
        )
        runtime = self.root / (
            "container-generated" if container_fallback else "host-generated"
        )
        destination.write_text(
            textwrap.dedent(
                f"""\
                #!/usr/bin/env bash
                printf 'called\n' >> {shell_quote(marker)}
                mkdir -p {shell_quote(runtime)}
                printf '%s\n' {shell_quote(runtime)}
                """
            ),
            encoding="utf-8",
        )
        destination.chmod(0o755)
        return destination, marker

    def shell_env(self, profiles: str = "defaults") -> dict[str, str]:
        env = os.environ.copy()
        env.update(
            {
                "HOME": str(self.home),
                "XDG_CONFIG_HOME": str(self.config_home),
                "OPENCODE_PROFILES": profiles,
                "OPENCODE_PROFILE": profiles.split()[0],
            }
        )
        env.pop("OPENCODE_CONFIG_DIR", None)
        env.pop("_OPENCODE_PROFILE_CONTEXT_SIGNATURE", None)
        return env

    def run_shell(
        self,
        executable: str,
        script: str,
        *,
        profiles: str = "defaults",
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [executable, "-c", script],
            cwd=self.workspace,
            env=self.shell_env(profiles),
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    def test_same_directory_refresh_and_unchanged_noop(self) -> None:
        for helper, container_fallback in (
            (HOST_HELPER, False),
            (CONTAINER_HELPER, True),
        ):
            with self.subTest(helper=helper):
                self.write_canonical("3.1.13")
                canonical = self.opencode_home / "opencode.jsonc"
                now = time.time()
                os.utime(canonical, (now, now))
                sync_helper = self.install_sync_helper(
                    container_fallback=container_fallback
                )
                script = textwrap.dedent(
                    f"""
                    source {shell_quote(helper)}
                    runtime="$OPENCODE_CONFIG_DIR/opencode.jsonc"
                    before_mtime="$(RUNTIME="$runtime" python3 -c 'import os; print(os.stat(os.environ["RUNTIME"]).st_mtime_ns)')"
                    CANONICAL={shell_quote(canonical)} python3 -c 'import json, os, time; p=os.environ["CANONICAL"]; data=json.load(open(p)); data["plugin"]=["@tarquinen/opencode-dcp@3.1.15"]; open(p,"w").write(json.dumps(data)+"\\n"); future=time.time()+3; os.utime(p,(future,future))'
                    _opencode_refresh_profile
                    refreshed_mtime="$(RUNTIME="$runtime" python3 -c 'import os; print(os.stat(os.environ["RUNTIME"]).st_mtime_ns)')"
                    _opencode_refresh_profile
                    noop_mtime="$(RUNTIME="$runtime" python3 -c 'import os; print(os.stat(os.environ["RUNTIME"]).st_mtime_ns)')"
                    RUNTIME="$runtime" BEFORE="$before_mtime" REFRESHED="$refreshed_mtime" NOOP="$noop_mtime" SYNC={shell_quote(sync_helper)} python3 -c 'import json, os; data=json.load(open(os.environ["RUNTIME"])); print(json.dumps({{"plugin": data["plugin"], "before": int(os.environ["BEFORE"]), "refreshed": int(os.environ["REFRESHED"]), "noop": int(os.environ["NOOP"]), "sync": os.environ["SYNC"]}}))'
                    """
                )
                result = self.run_shell(
                    "bash", script, profiles="defaults review"
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                data = json.loads(result.stdout.splitlines()[-1])
                self.assertEqual(
                    data["plugin"], ["@tarquinen/opencode-dcp@3.1.15"]
                )
                self.assertNotEqual(data["before"], data["refreshed"])
                self.assertEqual(data["refreshed"], data["noop"])

    def test_exact_defaults_and_chatgpt_use_native_mode(self) -> None:
        for helper, container_fallback in (
            (HOST_HELPER, False),
            (CONTAINER_HELPER, True),
        ):
            for profiles in ("defaults", "chatgpt"):
                with self.subTest(helper=helper, profiles=profiles):
                    _, marker = self.install_counting_sync_helper(
                        container_fallback=container_fallback
                    )
                    script = textwrap.dedent(
                        f"""
                        export OPENCODE_CONFIG_DIR={shell_quote(self.root / "stale")}
                        export ANTHROPIC_API_KEY=synthetic-managed-key
                        export _OPENCODE_ANTHROPIC_API_MANAGED=1
                        source {shell_quote(helper)}
                        _opencode_refresh_profile
                        CONFIG_SET="${{OPENCODE_CONFIG_DIR+x}}" \
                          PROFILES="$OPENCODE_PROFILES" \
                          PROFILE="$OPENCODE_PROFILE" \
                          SIGNATURE="${{_OPENCODE_PROFILE_CONTEXT_SIGNATURE:-}}" \
                          KEY_SET="${{ANTHROPIC_API_KEY+x}}" \
                          MANAGED_SET="${{_OPENCODE_ANTHROPIC_API_MANAGED+x}}" \
                          python3 -c 'import json, os; print(json.dumps({{key: os.environ.get(key, "") for key in ("CONFIG_SET", "PROFILES", "PROFILE", "SIGNATURE", "KEY_SET", "MANAGED_SET")}}))'
                        """
                    )
                    result = self.run_shell(
                        "bash", script, profiles=profiles
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
                    data = json.loads(result.stdout.splitlines()[-1])
                    self.assertEqual(data["CONFIG_SET"], "")
                    self.assertEqual(data["PROFILES"], "defaults")
                    self.assertEqual(data["PROFILE"], "defaults")
                    self.assertEqual(data["SIGNATURE"], "native|defaults")
                    self.assertEqual(data["KEY_SET"], "")
                    self.assertEqual(data["MANAGED_SET"], "")
                    self.assertFalse(marker.exists())

    def test_switching_back_to_defaults_clears_generated_state(self) -> None:
        for helper, container_fallback in (
            (HOST_HELPER, False),
            (CONTAINER_HELPER, True),
        ):
            with self.subTest(helper=helper):
                self.install_sync_helper(
                    container_fallback=container_fallback
                )
                script = textwrap.dedent(
                    f"""
                    source {shell_quote(helper)}
                    previous="$OPENCODE_CONFIG_DIR"
                    export OPENCODE_PROFILES=defaults
                    export OPENCODE_PROFILE=defaults
                    _opencode_refresh_profile
                    PREVIOUS="$previous" \
                      CONFIG_SET="${{OPENCODE_CONFIG_DIR+x}}" \
                      PROFILES="$OPENCODE_PROFILES" \
                      PROFILE="$OPENCODE_PROFILE" \
                      SIGNATURE="${{_OPENCODE_PROFILE_CONTEXT_SIGNATURE:-}}" \
                      python3 -c 'import json, os; print(json.dumps({{key: os.environ.get(key, "") for key in ("PREVIOUS", "CONFIG_SET", "PROFILES", "PROFILE", "SIGNATURE")}}))'
                    """
                )
                result = self.run_shell(
                    "bash", script, profiles="defaults review"
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                data = json.loads(result.stdout.splitlines()[-1])
                self.assertTrue(data["PREVIOUS"])
                self.assertEqual(data["CONFIG_SET"], "")
                self.assertEqual(data["PROFILES"], "defaults")
                self.assertEqual(data["PROFILE"], "defaults")
                self.assertEqual(data["SIGNATURE"], "native|defaults")

    def test_single_nondefault_profile_retains_runtime_generation(self) -> None:
        for helper, container_fallback in (
            (HOST_HELPER, False),
            (CONTAINER_HELPER, True),
        ):
            with self.subTest(helper=helper):
                self.install_sync_helper(
                    container_fallback=container_fallback
                )
                script = textwrap.dedent(
                    f"""
                    source {shell_quote(helper)}
                    test -n "$OPENCODE_CONFIG_DIR"
                    test -f "$OPENCODE_CONFIG_DIR/opencode.jsonc"
                    printf '%s\n' "$OPENCODE_PROFILE"
                    """
                )
                result = self.run_shell(
                    "bash", script, profiles="anthropic-api"
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.splitlines()[-1], "anthropic-api")

    def test_stacked_profile_and_workspace_agent_override_survive(self) -> None:
        self.install_sync_helper(container_fallback=False)
        (self.overlay_dir / "opencode.jsonc").write_text(
            json.dumps(
                {"agent": {"build": {"permission": {"edit": "deny"}}}}
            )
            + "\n",
            encoding="utf-8",
        )
        workspace_config_dir = self.workspace / ".opencode"
        workspace_config_dir.mkdir()
        (workspace_config_dir / "opencode.jsonc").write_text(
            json.dumps(
                {"agent": {"build": {"model": "openai/workspace"}}}
            )
            + "\n",
            encoding="utf-8",
        )
        canonical = self.opencode_home / "opencode.jsonc"
        script = textwrap.dedent(
            f"""
            source {shell_quote(HOST_HELPER)}
            CANONICAL={shell_quote(canonical)} python3 -c 'import json, os, time; p=os.environ["CANONICAL"]; data=json.load(open(p)); data["plugin"]=["@tarquinen/opencode-dcp@3.1.15"]; open(p,"w").write(json.dumps(data)+"\\n"); future=time.time()+3; os.utime(p,(future,future))'
            _opencode_refresh_profile
            python3 -c 'import json, os; print(json.dumps(json.load(open(os.path.join(os.environ["OPENCODE_CONFIG_DIR"],"opencode.jsonc")))))'
            """
        )
        result = self.run_shell("bash", script, profiles="defaults review")
        self.assertEqual(result.returncode, 0, result.stderr)
        config = json.loads(result.stdout.splitlines()[-1])
        self.assertEqual(
            config["plugin"], ["@tarquinen/opencode-dcp@3.1.15"]
        )
        self.assertEqual(config["agent"]["build"]["model"], "openai/workspace")
        self.assertEqual(
            config["agent"]["build"]["permission"], {"edit": "deny"}
        )

    def test_bash_prompt_hook_is_idempotent_and_preserves_status(self) -> None:
        self.install_sync_helper(container_fallback=False)
        script = textwrap.dedent(
            f"""
            source {shell_quote(HOST_HELPER)}
            source {shell_quote(HOST_HELPER)}
            false
            _opencode_refresh_profile
            callback_status=$?
            printf '%s\n%s\n' "$PROMPT_COMMAND" "$callback_status"
            """
        )
        result = self.run_shell("bash", script)
        self.assertEqual(result.returncode, 0, result.stderr)
        lines = result.stdout.splitlines()
        self.assertEqual(lines[-1], "1")
        self.assertEqual(
            lines[-2].split(";").count("_opencode_refresh_profile"), 1
        )

    @unittest.skipUnless(ZSH, "zsh is not installed")
    def test_zsh_hooks_are_idempotent_and_preserve_status(self) -> None:
        for helper, container_fallback in (
            (HOST_HELPER, False),
            (CONTAINER_HELPER, True),
        ):
            with self.subTest(helper=helper):
                self.install_sync_helper(
                    container_fallback=container_fallback
                )
                script = textwrap.dedent(
                    f"""
                    source {shell_quote(helper)}
                    source {shell_quote(helper)}
                    _later_prompt_hook() {{ return 0 }}
                    add-zsh-hook precmd _later_prompt_hook
                    _opencode_install_shell_hooks
                    chpwd_count=${{#${{(M)chpwd_functions:#_opencode_refresh_profile}}}}
                    precmd_count=${{#${{(M)precmd_functions:#_opencode_refresh_profile}}}}
                    last_precmd=${{precmd_functions[-1]}}
                    false
                    _opencode_refresh_profile
                    callback_status=$?
                    printf '%s %s %s %s\n' "$chpwd_count" "$precmd_count" "$callback_status" "$last_precmd"
                    """
                )
                result = self.run_shell(ZSH, script)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(
                    result.stdout.splitlines()[-1],
                    "1 1 1 _opencode_refresh_profile",
                )


@unittest.skipUnless(PWSH, "native pwsh is not installed")
class PowerShellOpenCodeProfileRefreshTests(OpenCodeProfileRefreshTests):
    def setUp(self) -> None:
        super().setUp()
        self.install_sync_helper(container_fallback=False)
        self.rendered_env = self.root / "opencode-env.ps1"
        self.rendered_prompt = self.root / "prompt.ps1"
        render_windows_template(POWERSHELL_ENV_TEMPLATE, self.rendered_env)
        render_windows_template(
            POWERSHELL_PROMPT_TEMPLATE,
            self.rendered_prompt,
            omit_starship_init=True,
        )

    def test_exact_defaults_and_chatgpt_remove_config_dir(self) -> None:
        for profiles in ("defaults", "chatgpt"):
            with self.subTest(profiles=profiles):
                _, marker = self.install_counting_sync_helper(
                    container_fallback=False
                )
                body = textwrap.dedent(
                    f"""
                    $ErrorActionPreference = 'Stop'
                    . {ps_quote(self.rendered_env)}
                    Update-OpenCodeProfileEnvironment
                    [pscustomobject]@{{
                      ConfigSet = [bool](Test-Path Env:OPENCODE_CONFIG_DIR)
                      Profiles = $env:OPENCODE_PROFILES
                      Profile = $env:OPENCODE_PROFILE
                      Signature = $global:_OpenCodeProfileContextSignature
                    }} | ConvertTo-Json -Compress
                    """
                )
                env = self.shell_env(profiles)
                env["OPENCODE_CONFIG_DIR"] = str(self.root / "stale")
                result = subprocess.run(
                    [PWSH, "-NoProfile", "-Command", body],
                    cwd=self.workspace,
                    env=env,
                    check=False,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                data = json.loads(result.stdout.splitlines()[-1])
                self.assertFalse(data["ConfigSet"])
                self.assertEqual(data["Profiles"], "defaults")
                self.assertEqual(data["Profile"], "defaults")
                self.assertEqual(data["Signature"], "native|defaults")
                self.assertFalse(marker.exists())

    def test_prompt_refresh_is_memoized_idempotent_and_preserves_exit_code(
        self,
    ) -> None:
        canonical = self.opencode_home / "opencode.jsonc"
        body = textwrap.dedent(
            f"""
            $ErrorActionPreference = 'Stop'
            . {ps_quote(self.rendered_env)}
            $runtime = Join-Path $env:OPENCODE_CONFIG_DIR 'opencode.jsonc'
            $before = (Get-Item -LiteralPath $runtime).LastWriteTimeUtc.Ticks
            $config = Get-Content -Raw -LiteralPath {ps_quote(canonical)} | ConvertFrom-Json
            $config.plugin = @('@tarquinen/opencode-dcp@3.1.15')
            $config | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath {ps_quote(canonical)}
            (Get-Item -LiteralPath {ps_quote(canonical)}).LastWriteTimeUtc = [datetime]::UtcNow.AddSeconds(3)
            function global:prompt {{ "ORIGINAL:$global:LASTEXITCODE`:$($global:?)" }}
            . {ps_quote(self.rendered_prompt)}
            $firstWrapper = (Get-Command prompt -CommandType Function).ScriptBlock.ToString()
            $global:LASTEXITCODE = 37
            Get-Item -LiteralPath (Join-Path {ps_quote(self.root)} 'missing') -ErrorAction SilentlyContinue | Out-Null
            $promptOutput = prompt
            $after = (Get-Item -LiteralPath $runtime).LastWriteTimeUtc.Ticks
            . {ps_quote(self.rendered_prompt)}
            $secondWrapper = (Get-Command prompt -CommandType Function).ScriptBlock.ToString()
            $global:LASTEXITCODE = 37
            Get-Item -LiteralPath (Join-Path {ps_quote(self.root)} 'missing') -ErrorAction SilentlyContinue | Out-Null
            $secondPromptOutput = prompt
            $noop = (Get-Item -LiteralPath $runtime).LastWriteTimeUtc.Ticks
            $runtimeConfig = Get-Content -Raw -LiteralPath $runtime | ConvertFrom-Json
            [pscustomobject]@{{
              Plugin = @($runtimeConfig.plugin)
              Before = $before
              After = $after
              Noop = $noop
              PromptOutput = $promptOutput
              SecondPromptOutput = $secondPromptOutput
              ExitCode = $global:LASTEXITCODE
              SameWrapper = ($firstWrapper -eq $secondWrapper)
            }} | ConvertTo-Json -Compress
            """
        )
        result = subprocess.run(
            [PWSH, "-NoProfile", "-Command", body],
            cwd=self.workspace,
            env=self.shell_env("defaults review"),
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout.splitlines()[-1])
        self.assertEqual(
            data["Plugin"], ["@tarquinen/opencode-dcp@3.1.15"]
        )
        self.assertNotEqual(data["Before"], data["After"])
        self.assertEqual(data["After"], data["Noop"])
        self.assertEqual(data["PromptOutput"], "ORIGINAL:37:False")
        self.assertEqual(data["SecondPromptOutput"], "ORIGINAL:37:False")
        self.assertEqual(data["ExitCode"], 37)
        self.assertTrue(data["SameWrapper"])


if __name__ == "__main__":
    unittest.main()
