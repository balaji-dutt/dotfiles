from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from functools import lru_cache
from pathlib import Path
from typing import Callable

from tests.support.powershell import parse_powershell, powershell_parser_command


REPO_ROOT = Path(__file__).resolve().parents[1]
CHEZMOI = shutil.which("chezmoi")
PLATFORMS = ("linux", "macos", "windows", "wsl2")


def template_paths() -> tuple[Path, ...]:
    paths: set[Path] = set()
    paths.update((REPO_ROOT / ".chezmoiscripts").glob("*.tmpl"))
    paths.update((REPO_ROOT / "bin").glob("*.tmpl"))
    paths.update((REPO_ROOT / "dot_local" / "bin").glob("*.tmpl"))
    paths.update((REPO_ROOT / "dot_local" / "share" / "zsh").glob("*"))
    paths.update((REPO_ROOT / "private_dot_config" / "powershell").glob("*.ps1.tmpl"))
    paths.update((REPO_ROOT / "private_Documents" / "PowerShell").rglob("*.ps1"))
    paths.update((REPO_ROOT / "private_Documents" / "PowerShell").rglob("*.ps1.tmpl"))
    paths.update((REPO_ROOT / "dot_local").glob("*.ps1"))
    paths.update(
        REPO_ROOT / name
        for name in ("dot_bashrc.tmpl", "dot_zshrc.tmpl", "symlink_dot_p10k.zsh.tmpl")
    )
    paths.update(
        REPO_ROOT / name
        for name in (
            "dot_local/config/dot_p10k.zsh",
            "dot_local/share/git-helpers.zsh",
            "dot_local/share/zsh-modern-cli-hints.zsh",
        )
    )
    return tuple(sorted(paths))


def fixture_data(platform: str, temporary_root: Path) -> dict[str, object]:
    is_windows = platform == "windows"
    is_wsl = platform == "wsl2"
    os_name = "darwin" if platform == "macos" else "windows" if is_windows else "linux"
    home = "C:\\Users\\Fixture" if is_windows else "/fixture/home"
    source = "C:\\Fixture\\Source" if is_windows else "/fixture/source"
    os_release = {
        "id": "windows" if is_windows else "darwin" if platform == "macos" else "debian",
        "name": platform,
        "version": "fixture-1",
        "versionID": "fixture-1",
    }
    windows = {
        "pageant_keys_allowlist": "C:\\Fixture\\keys.txt",
        "powershell_dir": "C:\\Fixture\\PowerShell",
        "powershell_scripts_dir": "C:\\Fixture\\PowerShell\\Scripts",
        "putty_dir": "C:\\Fixture\\PuTTY",
        "putty_keys_dir": "C:\\Fixture\\Keys",
        "wsl_agent_sock": "/fixture/run/ssh-agent.sock",
        "wsl_ssh_pageant_exe": "C:\\Fixture\\wsl-ssh-pageant.exe",
    }
    return {
        "CERTFILES": [],
        "CERTPATH": "",
        "beads_client": {
            "wsl_distro": "FixtureDistro",
            "wsl_repo_rel": "fixture/dotfiles",
        },
        "beads_version": "1.2.3-fixture",
        "browser_policies": {
            "justthebrowser": {
                "enabled": False,
                "macos_open_profiles_on_update": False,
            }
        },
        "ccr_port": 3456,
        "codebase_memory_mcp_version": "0.0.0-fixture",
        "data": {"windows": windows},
        "dolt_version": "0.0.0-fixture",
        "email": "fixture@example.invalid",
        "homelab": {
            "nfs_path": "/fixture/share",
            "nfs_server": "192.0.2.10",
            "npiperelay_path": "Fixture/npiperelay.exe",
            "windows_user": "Fixture",
        },
        "isDebianWSL2": is_wsl,
        "isDevcontainerHost": False,
        "isUbuntuWSL2": False,
        "isWSL": is_wsl,
        "isWSL2": is_wsl,
        "lazygit_version": "0.0.0-fixture",
        "macos_vdi": {
            "allow_downgrade": False,
            "citrix": {
                "default_dmg_path": "/fixture/Citrix.dmg",
                "desired_family": "0.0-fixture",
                "display_version": "0.0-fixture",
            },
            "enabled": False,
            "install": False,
            "zoom": {"desired_pkg_version": "0.0-fixture"},
        },
        "name": "Fixture User",
        "onepassword": {
            "email": "fixture@example.invalid",
            "url": "https://fixture.example.invalid",
        },
        "plannotator_port": 8999,
        "plannotator_ports": {
            "devcontainer": {
                "build": "10993-10998",
                "claude": "11014-11019",
                "custom": "11004-11009",
            },
            "host": {
                "build": "8993-8998",
                "claude": "9014-9019",
                "custom": "9004-9009",
            },
        },
        "plannotator_version": "0.0.0-fixture",
        "windows": windows,
        "chezmoi": {
            "arch": "amd64",
            "cacheDir": str(temporary_root / "cache"),
            "commandDir": str(temporary_root / "commands"),
            "configFile": str(temporary_root / "chezmoi.toml"),
            "destDir": home,
            "executable": "chezmoi.exe" if is_windows else "chezmoi",
            "fqdnHostname": "fixture.example.invalid",
            "gid": "1000",
            "homeDir": home,
            "hostname": "fixture-host",
            "kernel": {
                "osrelease": "6.6.0-microsoft-standard-WSL2" if is_wsl else "fixture-kernel",
                "ostype": os_name,
                "version": "fixture-kernel",
            },
            "os": os_name,
            "osRelease": os_release,
            "sourceDir": str(REPO_ROOT),
            "uid": "1000",
            "username": "fixture-user",
            "workingTree": source,
        },
    }


def render_template(
    relative: str,
    platform: str,
    configure: Callable[[dict[str, object]], None] | None = None,
    environment: dict[str, str] | None = None,
) -> str:
    if CHEZMOI is None:
        raise RuntimeError("chezmoi is unavailable")
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        config = root / "chezmoi.toml"
        override = root / "override.json"
        config.write_text("", encoding="utf-8")
        data = fixture_data(platform, root)
        if configure is not None:
            configure(data)
        override.write_text(json.dumps(data, sort_keys=True), encoding="utf-8")
        result = subprocess.run(
            [
                CHEZMOI,
                "--config",
                str(config),
                "--source",
                str(REPO_ROOT),
                "execute-template",
                "--override-data-file",
                str(override),
                "--file",
                str(REPO_ROOT / relative),
            ],
            cwd=REPO_ROOT,
            env={
                **(environment if environment is not None else os.environ),
                "CHEZMOI_NO_TTY": "1",
            },
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=120,
        )
    if result.returncode != 0:
        raise AssertionError(f"{platform} {relative}: {result.stderr}")
    return result.stdout


@lru_cache(maxsize=1)
def render_matrix() -> dict[tuple[str, str], bytes]:
    if CHEZMOI is None:
        raise RuntimeError("chezmoi is unavailable")
    rendered: dict[tuple[str, str], bytes] = {}
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        config = root / "chezmoi.toml"
        config.write_text("", encoding="utf-8")
        sources = template_paths()
        bundle = root / "render-matrix.tmpl"
        bundle.write_text(
            "".join(
                f'{{{{ printf "__DOTFILES_BEGIN_{index:04d}__\\n" }}}}'
                f'{{{{ includeTemplate {json.dumps(source.relative_to(REPO_ROOT).as_posix())} . }}}}'
                f'{{{{ printf "\\n__DOTFILES_END_{index:04d}__\\n" }}}}'
                for index, source in enumerate(sources)
            ),
            encoding="utf-8",
        )
        for platform in PLATFORMS:
            override = root / f"{platform}.json"
            override.write_text(
                json.dumps(fixture_data(platform, root), sort_keys=True),
                encoding="utf-8",
            )
            result = subprocess.run(
                [
                    CHEZMOI,
                    "--config",
                    str(config),
                    "--source",
                    str(REPO_ROOT),
                    "execute-template",
                    "--override-data-file",
                    str(override),
                    "--file",
                    str(bundle),
                ],
                cwd=REPO_ROOT,
                env={**os.environ, "CHEZMOI_NO_TTY": "1"},
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=120,
            )
            if result.returncode != 0:
                raise AssertionError(
                    f"{platform} fixture failed: "
                    f"{result.stderr.decode('utf-8', errors='replace')}"
                )
            for index, source in enumerate(sources):
                relative = source.relative_to(REPO_ROOT).as_posix()
                begin = f"__DOTFILES_BEGIN_{index:04d}__\n".encode()
                end = f"\n__DOTFILES_END_{index:04d}__\n".encode()
                start = result.stdout.find(begin)
                stop = result.stdout.find(end, start + len(begin))
                if start < 0 or stop < 0:
                    raise AssertionError(
                        f"{platform} fixture did not delimit {relative}"
                    )
                rendered[(platform, relative)] = result.stdout[start + len(begin) : stop]
    return rendered


@unittest.skipUnless(CHEZMOI, "chezmoi is required")
class LifecycleRenderMatrixTests(unittest.TestCase):
    def test_inventory_includes_every_lifecycle_template(self) -> None:
        lifecycle = tuple((REPO_ROOT / ".chezmoiscripts").glob("*.tmpl"))
        self.assertEqual(len(lifecycle), 47)
        self.assertEqual(sum(path.name.endswith(".ps1.tmpl") for path in lifecycle), 18)
        self.assertEqual(sum(path.name.endswith(".sh.tmpl") for path in lifecycle), 29)

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


if __name__ == "__main__":
    unittest.main()
