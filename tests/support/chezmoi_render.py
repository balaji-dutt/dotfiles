"""Render chezmoi templates against synthetic per-platform data for tests."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from functools import lru_cache
from pathlib import Path
from typing import Callable


REPO_ROOT = Path(__file__).resolve().parents[2]
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
        "macos_home_ssh": {"dhcp_domain": "", "enabled": False, "router_mac": ""},
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
