from __future__ import annotations

import json
import shutil
import subprocess
import unittest

from tests.support.chezmoi_render import (
    CHEZMOI,
    PLATFORMS,
    REPO_ROOT,
    render_matrix,
    render_template,
    template_paths,
)
from tests.support.powershell import parse_powershell, powershell_parser_command


@unittest.skipUnless(CHEZMOI, "chezmoi is required")
class LifecycleRenderMatrixTests(unittest.TestCase):
    def test_inventory_includes_every_lifecycle_template(self) -> None:
        lifecycle = tuple((REPO_ROOT / ".chezmoiscripts").glob("*.tmpl"))
        self.assertEqual(len(lifecycle), 51)
        self.assertEqual(sum(path.name.endswith(".ps1.tmpl") for path in lifecycle), 19)
        self.assertEqual(sum(path.name.endswith(".sh.tmpl") for path in lifecycle), 32)

    def test_all_sources_render_for_each_fixture(self) -> None:
        matrix = render_matrix()
        expected = len(template_paths()) * len(PLATFORMS)
        self.assertEqual(len(matrix), expected)
        for (platform, relative), content in matrix.items():
            with self.subTest(platform=platform, source=relative):
                decoded = content.decode("utf-8")
                self.assertNotIn("\x00", decoded)
                self.assertNotRegex(decoded, r"\r(?!\n)")

    def test_lifecycle_templates_select_only_applicable_platforms(self) -> None:
        matrix = render_matrix()

        windows_bootstrap = (
            ".chezmoiscripts/run_onchange_after_windows-bootstrap.ps1.tmpl"
        )
        for platform in ("linux", "macos", "wsl2"):
            self.assertEqual(matrix[(platform, windows_bootstrap)], b"")
        self.assertIn(b"Windows bootstrap", matrix[("windows", windows_bootstrap)])

        wsl_provision = ".chezmoiscripts/run_onchange_before_00-wsl-provision.sh.tmpl"
        for platform in ("linux", "macos", "windows"):
            self.assertNotIn(b"ansible-playbook", matrix[(platform, wsl_provision)])
        self.assertIn(b"ansible-playbook", matrix[("wsl2", wsl_provision)])

        macos_nfs = ".chezmoiscripts/run_after_macos-nfs-config.sh.tmpl"
        for platform in ("linux", "windows", "wsl2"):
            self.assertEqual(matrix[(platform, macos_nfs)].strip(), b"")
        self.assertIn(b"NFS_CONFIG_FILE", matrix[("macos", macos_nfs)])

        macos_home_ssh = ".chezmoiscripts/run_after_macos-home-ssh.sh.tmpl"
        for platform in ("linux", "windows", "wsl2"):
            self.assertEqual(matrix[(platform, macos_home_ssh)].strip(), b"")
        self.assertIn(b"readonly ENABLED=false", matrix[("macos", macos_home_ssh)])

        wsl_gitignore = ".chezmoiscripts/run_copy_win_gitignore.sh.tmpl"
        for platform in ("linux", "macos", "windows"):
            self.assertEqual(matrix[(platform, wsl_gitignore)], b"")
        self.assertIn(b"cp /fixture/source/", matrix[("wsl2", wsl_gitignore)])


@unittest.skipUnless(CHEZMOI and shutil.which("bash") and shutil.which("zsh"), "Bash and Zsh are required")
class LifecyclePosixSyntaxTests(unittest.TestCase):
    def assert_syntax(self, shell: str, platform: str, relative: str, content: bytes) -> None:
        result = subprocess.run(
            [shell, "-n"],
            cwd=REPO_ROOT,
            input=content,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.assertEqual(
            result.returncode,
            0,
            f"{platform} {relative}: {result.stderr.decode('utf-8', errors='replace')}",
        )

    def test_rendered_lifecycle_shell_scripts_parse(self) -> None:
        for (platform, relative), content in render_matrix().items():
            if platform == "windows" or not relative.startswith(".chezmoiscripts/"):
                continue
            if relative.endswith(".sh.tmpl") and content.strip():
                with self.subTest(platform=platform, source=relative):
                    self.assert_syntax("bash", platform, relative, content)

    def test_rendered_startup_fragments_parse(self) -> None:
        for (platform, relative), content in render_matrix().items():
            if platform == "windows" or not content.strip():
                continue
            shell = None
            if relative == "dot_bashrc.tmpl":
                shell = "bash"
            elif relative == "dot_zshrc.tmpl" or relative.endswith((".zsh", ".zsh.tmpl")):
                shell = "zsh"
            if shell:
                with self.subTest(platform=platform, source=relative):
                    self.assert_syntax(shell, platform, relative, content)


@unittest.skipUnless(CHEZMOI, "chezmoi is required")
class LifecyclePowerShellSyntaxTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        if powershell_parser_command(
            REPO_ROOT,
            "probe",
            provision_container=False,
        ) is None:
            raise unittest.SkipTest("PowerShell parser is unavailable")

    def test_rendered_powershell_sources_parse(self) -> None:
        for (platform, relative), content in render_matrix().items():
            if platform != "windows" or not content.strip():
                continue
            if not relative.endswith((".ps1", ".ps1.tmpl")):
                continue
            result = parse_powershell(
                content.decode("utf-8"),
                relative,
                repo_root=REPO_ROOT,
            )
            with self.subTest(source=relative):
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_private_chezmoi_function_forwards_config_and_arguments(self) -> None:
        relative = "private_dot_config/powershell/aliases.ps1.tmpl"
        source = render_template(relative, "windows")
        resolved = powershell_parser_command(REPO_ROOT, relative, provision_container=False)
        self.assertIsNotNone(resolved)
        command, env = resolved
        script = (
            "$source = [Console]::In.ReadToEnd(); "
            "function global:Get-Command { param($Name, $CommandType); "
            "if ($Name -eq 'chezmoi') { [pscustomobject]@{ Source = 'Capture-Args' } } }; "
            "function global:Capture-Args { "
            "[Console]::Out.WriteLine((ConvertTo-Json -InputObject @($args) -Compress)) }; "
            "$env:USERPROFILE = 'C:\\Users\\Fixture Home'; "
            ". ([scriptblock]::Create($source)); "
            "czp source-path 'two words'"
        )
        result = subprocess.run(
            [*command[:-1], script],
            cwd=REPO_ROOT,
            env=env,
            input=source,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=60,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(
            json.loads(result.stdout),
            ["--config", r"C:\Users\Fixture Home\.config\chezmoi-private\chezmoi.toml",
             "source-path", "two words"],
        )


if __name__ == "__main__":
    unittest.main()
