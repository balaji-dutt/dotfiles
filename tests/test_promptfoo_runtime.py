from __future__ import annotations

import json
import tomllib
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_DIR = REPO_ROOT / "configs/promptfoo-runtime"
DEVCONTAINER_NPM = REPO_ROOT / (
    "private_Documents/development/container-dotfiles/devcontainers/"
    "gitlab.com/servers-homelab/homelab-IaC/configs/npm_packages.txt"
)
# package.json is the pin Renovate updates for this bundle; every other location
# has to agree with it. Reading it here instead of restating the versions keeps
# this file out of the set that would have to be bumped in lockstep. Version
# changes stay human-reviewed through automerge:false on the "promptfoo runtime"
# group in renovate.json5.
EXPECTED_DEPENDENCIES: dict[str, str] = json.loads(
    (RUNTIME_DIR / "package.json").read_text(encoding="utf-8")
)["dependencies"]


def read_text(relative_path: str) -> str:
    return (REPO_ROOT / relative_path).read_text(encoding="utf-8")


class PromptfooRuntimeTests(unittest.TestCase):
    def test_manifest_pins_every_dependency_exactly(self) -> None:
        self.assertTrue(EXPECTED_DEPENDENCIES, "package.json declares no dependencies")
        for package_name, version in EXPECTED_DEPENDENCIES.items():
            with self.subTest(package=package_name):
                self.assertRegex(version, r"^\d+\.\d+\.\d+$")

    def test_manifest_and_lockfile_have_exact_direct_dependencies(self) -> None:
        lock = json.loads((RUNTIME_DIR / "package-lock.json").read_text(encoding="utf-8"))

        self.assertEqual(lock["packages"][""]["dependencies"], EXPECTED_DEPENDENCIES)

    def test_devcontainer_pins_match_host_bundle(self) -> None:
        pins: dict[str, str] = {}
        for line in DEVCONTAINER_NPM.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "@" not in line:
                continue
            package_name, version = line.rsplit("@", 1)
            if package_name in EXPECTED_DEPENDENCIES:
                pins[package_name] = version

        self.assertEqual(pins, EXPECTED_DEPENDENCIES)

    def test_promptfoo_is_not_an_isolated_mise_tool(self) -> None:
        mise = tomllib.loads(read_text("configs/mise.toml"))
        self.assertNotIn("npm:promptfoo", mise["tools"])
        self.assertNotIn("npm:@opencode-ai/sdk", mise["tools"])

    def test_host_hydration_uses_one_runtime_root(self) -> None:
        wrapper = read_text("bin/executable_promptfoo")
        macos_hook = read_text(
            ".chezmoiscripts/run_onchange_after_install_promptfoo_runtime.sh.tmpl"
        )
        wsl_hook = read_text(
            ".chezmoiscripts/run_onchange_before_00-wsl-provision.sh.tmpl"
        )
        ansible = read_text("ansible/wsl-playbook.yml")

        runtime_path = ".local/share/promptfoo-runtime"
        self.assertIn(runtime_path, wrapper)
        self.assertIn(runtime_path, macos_hook)
        self.assertTrue(
            macos_hook.startswith('{{- if eq .chezmoi.os "darwin" -}}\n#!/bin/bash')
        )
        self.assertIn('cd "$runtime_dir"', macos_hook)
        self.assertIn("mise exec -- npm ci", macos_hook)
        self.assertIn("--input-type=module", macos_hook)
        self.assertIn("import.meta.resolve(packageName)", macos_hook)
        self.assertIn('cd "$runtime_dir"', ansible)
        self.assertIn("--input-type=module", ansible)
        self.assertIn("import.meta.resolve(packageName)", ansible)
        self.assertNotIn("createRequire", macos_hook)
        self.assertNotIn("createRequire", ansible)
        for filename in ("package.json", "package-lock.json"):
            source_path = f"configs/promptfoo-runtime/{filename}"
            self.assertIn(source_path, macos_hook)
            self.assertIn(source_path, wsl_hook)
            self.assertIn(source_path, ansible)
        for package_name in EXPECTED_DEPENDENCIES:
            self.assertIn(package_name, macos_hook)
            self.assertIn(package_name, ansible)

    def test_generated_verifiers_use_esm_resolution(self) -> None:
        skill_roots = (
            "dot_claude/skills/prompt-evaluator/references",
            "private_dot_config/opencode/skills/prompt-evaluator/references",
            "private_Documents/development/container-dotfiles/dotfiles/"
            "dot_claude/skills/prompt-evaluator/references",
            "private_Documents/development/container-dotfiles/dotfiles/"
            "private_dot_config/opencode/skills/prompt-evaluator/references",
        )

        for skill_root in skill_roots:
            with self.subTest(skill_root=skill_root, platform="posix"):
                verifier = read_text(f"{skill_root}/install-promptfoo.sh")
                self.assertIn('cd "$runtime_dir"', verifier)
                self.assertIn("--input-type=module", verifier)
                self.assertIn("import.meta.resolve(packageName)", verifier)
                self.assertNotIn("createRequire", verifier)
                self.assertIn(") || return 1", verifier)
                self.assertIn(
                    '"$promptfoo_bin" --version >/dev/null || return 1',
                    verifier,
                )

            with self.subTest(skill_root=skill_root, platform="powershell"):
                verifier = read_text(f"{skill_root}/install-promptfoo.ps1")
                self.assertIn("--input-type=module", verifier)
                self.assertIn("import.meta.resolve(packageName)", verifier)
                self.assertNotIn("createRequire", verifier)
                self.assertIn(
                    "Push-Location -LiteralPath $PackageRoot -ErrorAction Stop",
                    verifier,
                )

    def test_renovate_scope_and_group_are_narrow(self) -> None:
        renovate = read_text("renovate.json5")

        self.assertIn('"enabledManagers": ["custom.regex", "npm"]', renovate)
        self.assertIn(
            '"/^configs\\\\/promptfoo-runtime\\\\/package\\\\.json$/"',
            renovate,
        )
        self.assertIn('"groupName": "promptfoo runtime"', renovate)
        runtime_rule = renovate.rsplit('"groupName": "promptfoo runtime"', 1)[0]
        runtime_rule = runtime_rule.rsplit("{", 1)[-1]
        for package_name in EXPECTED_DEPENDENCIES:
            self.assertIn(f'"{package_name}"', runtime_rule)
        runtime_rule_tail = renovate.rsplit('"groupName": "promptfoo runtime"', 1)[1]
        self.assertIn('"automerge": false', runtime_rule_tail)


if __name__ == "__main__":
    unittest.main()
