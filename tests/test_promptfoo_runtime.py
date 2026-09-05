from __future__ import annotations

import json
import re
import tomllib
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
HOST_RUNTIME_DIR = REPO_ROOT / "configs/promptfoo-runtime"
HOMELAB_ROOT = REPO_ROOT / (
    "private_Documents/development/container-dotfiles/devcontainers/"
    "gitlab.com/servers-homelab/homelab-IaC"
)
DEVCONTAINER_RUNTIME_DIR = HOMELAB_ROOT / "configs/promptfoo-runtime"
DEVCONTAINER_NPM = HOMELAB_ROOT / "configs/npm_packages.txt"
RUNTIME_PACKAGE_NAMES = {
    "@anthropic-ai/claude-agent-sdk",
    "@anthropic-ai/sdk",
    "@opencode-ai/sdk",
    "promptfoo",
}
EXPECTED_SCRIPT_APPROVALS = {
    "@playwright/browser-chromium",
    "@swc/core",
    "esbuild",
    "onnxruntime-node",
    "protobufjs",
    "sharp",
}
HOST_CLAUDE_PLATFORM_PACKAGES = {
    "@anthropic-ai/claude-agent-sdk-darwin-arm64",
    "@anthropic-ai/claude-agent-sdk-darwin-x64",
    "@anthropic-ai/claude-agent-sdk-linux-arm64",
    "@anthropic-ai/claude-agent-sdk-linux-x64",
}
DEVCONTAINER_CLAUDE_PLATFORM_PACKAGES = {
    "@anthropic-ai/claude-agent-sdk-linux-arm64",
    "@anthropic-ai/claude-agent-sdk-linux-x64",
}
EXPECTED_LINUX_BINDINGS = {
    "@libsql/linux-arm64-gnu",
    "@libsql/linux-x64-gnu",
}
RENOVATE_RUNTIMES = {
    "promptfoo host runtime": "configs/promptfoo-runtime/package.json",
    "promptfoo devcontainer runtime": (
        "private_Documents/development/container-dotfiles/devcontainers/"
        "gitlab.com/servers-homelab/homelab-IaC/configs/"
        "promptfoo-runtime/package.json"
    ),
}


def read_text(relative_path: str) -> str:
    return (REPO_ROOT / relative_path).read_text(encoding="utf-8")


def read_json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def read_rule_array(rule: str, key: str) -> list[str]:
    match = re.search(rf'"{re.escape(key)}"\s*:\s*(\[[^]]*\])', rule)
    if match is None:
        raise AssertionError(f"Renovate rule has no {key} array")
    return json.loads(match.group(1))


def renovate_rules(renovate: str) -> list[str]:
    return re.findall(r"^    \{\n.*?^    \}", renovate, flags=re.MULTILINE | re.DOTALL)


class PromptfooRuntimeTests(unittest.TestCase):
    def runtime_cases(self) -> tuple[tuple[str, Path], ...]:
        return (
            ("host", HOST_RUNTIME_DIR),
            ("devcontainer", DEVCONTAINER_RUNTIME_DIR),
        )

    def test_manifests_pin_private_npm_runtime_dependencies(self) -> None:
        names: set[str] = set()
        for environment, runtime_dir in self.runtime_cases():
            with self.subTest(environment=environment):
                manifest = read_json(runtime_dir / "package.json")
                dependencies = manifest["dependencies"]
                self.assertIsInstance(dependencies, dict)
                self.assertEqual(set(dependencies), RUNTIME_PACKAGE_NAMES)
                self.assertEqual(
                    manifest["$schema"],
                    "https://json.schemastore.org/package.json",
                )
                self.assertTrue(manifest["private"])
                self.assertIsInstance(manifest["name"], str)
                names.add(manifest["name"])
                for package_name, version in dependencies.items():
                    with self.subTest(environment=environment, package=package_name):
                        self.assertRegex(version, r"^\d+\.\d+\.\d+$")
        self.assertEqual(len(names), 2)

    def test_each_manifest_matches_its_own_lockfile(self) -> None:
        for environment, runtime_dir in self.runtime_cases():
            with self.subTest(environment=environment):
                manifest = read_json(runtime_dir / "package.json")
                lock = read_json(runtime_dir / "package-lock.json")
                self.assertEqual(
                    lock["packages"][""]["dependencies"],
                    manifest["dependencies"],
                )

    def assert_claude_platform_entries(
        self,
        runtime_dir: Path,
        expected_packages: set[str],
    ) -> None:
        lock = read_json(runtime_dir / "package-lock.json")
        packages = lock["packages"]
        sdk = packages["node_modules/@anthropic-ai/claude-agent-sdk"]
        optional_dependencies = sdk["optionalDependencies"]

        for package_name in expected_packages:
            with self.subTest(runtime=runtime_dir.name, package=package_name):
                expected_version = optional_dependencies[package_name]
                entry = packages[f"node_modules/{package_name}"]
                self.assertEqual(entry["version"], expected_version)
                self.assertTrue(entry["optional"])

    def test_host_lockfile_covers_supported_host_platforms(self) -> None:
        self.assert_claude_platform_entries(
            HOST_RUNTIME_DIR,
            HOST_CLAUDE_PLATFORM_PACKAGES,
        )

    def test_devcontainer_lockfile_covers_supported_linux_platforms(self) -> None:
        self.assert_claude_platform_entries(
            DEVCONTAINER_RUNTIME_DIR,
            DEVCONTAINER_CLAUDE_PLATFORM_PACKAGES,
        )

    def test_install_script_policies_are_environment_specific(self) -> None:
        host = read_json(HOST_RUNTIME_DIR / "package.json")["allowScripts"]
        devcontainer = read_json(DEVCONTAINER_RUNTIME_DIR / "package.json")[
            "allowScripts"
        ]

        self.assertEqual(
            host,
            {**{package: True for package in EXPECTED_SCRIPT_APPROVALS}, "fsevents": False},
        )
        self.assertEqual(
            devcontainer,
            {package: True for package in EXPECTED_SCRIPT_APPROVALS},
        )

    def test_lockfiles_contain_supported_linux_bindings(self) -> None:
        for environment, runtime_dir in self.runtime_cases():
            packages = read_json(runtime_dir / "package-lock.json")["packages"]
            for package_name in EXPECTED_LINUX_BINDINGS:
                with self.subTest(environment=environment, package=package_name):
                    entry = packages[f"node_modules/{package_name}"]
                    self.assertRegex(entry["version"], r"^\d+\.\d+\.\d+$")
                    self.assertTrue(entry["optional"])

    def test_devcontainer_global_packages_exclude_runtime_dependencies(self) -> None:
        package_names = {
            line.rsplit("@", 1)[0]
            for line in DEVCONTAINER_NPM.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.startswith("#") and "@" in line
        }
        self.assertTrue(RUNTIME_PACKAGE_NAMES.isdisjoint(package_names))

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
        for package_name in RUNTIME_PACKAGE_NAMES:
            self.assertIn(package_name, macos_hook)
            self.assertIn(package_name, ansible)

    def test_devcontainer_hydrates_its_own_runtime_root(self) -> None:
        post_create = read_text(
            "private_Documents/development/container-dotfiles/devcontainers/"
            "gitlab.com/servers-homelab/homelab-IaC/dot_devcontainer/postCreate.sh"
        )
        self.assertIn(
            "/tmp/host-homelab-configs/promptfoo-runtime",
            post_create,
        )
        self.assertNotIn(
            "/tmp/host-dotfiles/configs/promptfoo-runtime",
            post_create,
        )
        self.assertNotIn("npm_package_belongs_to_promptfoo_runtime", post_create)

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

    def test_renovate_scopes_runtime_groups_independently(self) -> None:
        renovate = read_text("renovate.json5")
        rules = renovate_rules(renovate)

        self.assertIn('"enabledManagers": ["custom.regex", "npm"]', renovate)
        self.assertNotIn('"groupName": "promptfoo runtime"', renovate)

        for group_name, filename in RENOVATE_RUNTIMES.items():
            escaped_pattern = filename.replace("/", "\\\\/").replace(".", "\\\\.")
            with self.subTest(group=group_name):
                self.assertIn('"/^' + escaped_pattern + '$/"', renovate)
                matching_rules = [rule for rule in rules if f'"{filename}"' in rule]
                self.assertEqual(len(matching_rules), 2)
                base_rule = next(
                    rule
                    for rule in matching_rules
                    if f'"groupName": "{group_name}"' in rule
                )
                patch_rule = next(
                    rule
                    for rule in matching_rules
                    if '"matchUpdateTypes": ["patch", "digest"]' in rule
                )
                for rule in matching_rules:
                    self.assertEqual(read_rule_array(rule, "matchManagers"), ["npm"])
                    self.assertCountEqual(
                        read_rule_array(rule, "matchDepNames"),
                        RUNTIME_PACKAGE_NAMES,
                    )
                    self.assertEqual(read_rule_array(rule, "matchFileNames"), [filename])
                self.assertIn('"automerge": false', base_rule)
                self.assertIn('"automerge": true', patch_rule)
                self.assertIn('"automergeType": "pr"', patch_rule)
                self.assertIn('"platformAutomerge": true', patch_rule)


if __name__ == "__main__":
    unittest.main()
