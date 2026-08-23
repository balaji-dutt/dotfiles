from __future__ import annotations

import re
import tomllib
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
CBM_TOOL = "github:DeusData/codebase-memory-mcp"
CBM_TEMPLATE_VALUE = "{{ .codebase_memory_mcp_version }}"


def read_text(relative_path: str) -> str:
    return (REPO_ROOT / relative_path).read_text(encoding="utf-8")


class CodebaseMemoryMcpPinTests(unittest.TestCase):
    def test_central_pin_is_an_exact_semantic_version(self) -> None:
        data = read_text(".chezmoidata.yaml")
        matches = re.findall(
            r'^codebase_memory_mcp_version: "([^"]+)".*$',
            data,
            flags=re.MULTILINE,
        )

        self.assertEqual(len(matches), 1)
        self.assertRegex(matches[0], r"^\d+\.\d+\.\d+$")
        pin_line = next(
            line
            for line in data.splitlines()
            if line.startswith("codebase_memory_mcp_version:")
        )
        self.assertEqual(pin_line.count("renovate:"), 1)
        self.assertIn("depName=DeusData/codebase-memory-mcp", pin_line)

    def test_base_mise_config_has_no_floating_or_duplicate_pin(self) -> None:
        mise = tomllib.loads(read_text("configs/mise.toml"))

        self.assertNotIn(CBM_TOOL, mise["tools"])

    def test_macos_fragment_uses_shared_pin_and_is_darwin_only(self) -> None:
        fragment = read_text(
            "private_dot_config/mise/conf.d/96-codebase-memory-mcp.toml.tmpl"
        )
        ignore = read_text(".chezmoiignore")
        non_darwin = ignore.split("{{ if not $isDarwin }}", 1)[1].split(
            "{{ end }}", 1
        )[0]

        self.assertIn(f'"{CBM_TOOL}" = "{CBM_TEMPLATE_VALUE}"', fragment)
        self.assertIn(".config/mise/conf.d/96-codebase-memory-mcp.toml", non_darwin)

    def test_wsl_writes_shared_pin_before_mise_install(self) -> None:
        hook = read_text(
            ".chezmoiscripts/run_onchange_before_00-wsl-provision.sh.tmpl"
        )
        ansible = read_text("ansible/wsl-playbook.yml")

        self.assertIn(
            '-e "codebase_memory_mcp_version={{ .codebase_memory_mcp_version }}"',
            hook,
        )
        self.assertIn(
            'codebase_memory_mcp_version: "{{ codebase_memory_mcp_version '
            "| default('') }}\"",
            ansible,
        )
        self.assertIn("Validate codebase-memory-mcp pin", ansible)
        self.assertIn(
            '(codebase_memory_mcp_version | default(\'\')) | length > 0',
            ansible,
        )
        self.assertIn(
            "dest: \"{{ ansible_env.HOME }}/.config/mise/conf.d/"
            "96-codebase-memory-mcp.toml\"",
            ansible,
        )
        self.assertIn(
            f'"{CBM_TOOL}" = "{{{{ codebase_memory_mcp_version }}}}"',
            ansible,
        )

        assert_position = ansible.index("Validate codebase-memory-mcp pin")
        fragment_position = ansible.index(
            "Write mise codebase-memory-mcp pin fragment"
        )
        install_position = ansible.index("Install mise tools from global config")
        self.assertLess(assert_position, fragment_position)
        self.assertLess(fragment_position, install_position)

    def test_devcontainer_uses_shared_pin_without_renovate_marker(self) -> None:
        devcontainer = read_text(
            "private_Documents/development/container-dotfiles/devcontainers/"
            "gitlab.com/servers-homelab/homelab-IaC/dot_devcontainer/"
            "devcontainer.json.tmpl"
        )
        cbm_line = next(
            line for line in devcontainer.splitlines() if '"CBM_VERSION"' in line
        )

        self.assertIn(f'"CBM_VERSION": "{CBM_TEMPLATE_VALUE}"', cbm_line)
        self.assertNotIn("renovate:", cbm_line)
        self.assertNotRegex(cbm_line, r'"CBM_VERSION": "\d')

    def test_windows_installer_uses_shared_pin(self) -> None:
        installer = read_text(
            ".chezmoiscripts/"
            "run_onchange_after_install_codebase-memory-mcp.ps1.tmpl"
        )

        self.assertIn(
            '$CbmVersion = "{{ .codebase_memory_mcp_version }}"', installer
        )


if __name__ == "__main__":
    unittest.main()
