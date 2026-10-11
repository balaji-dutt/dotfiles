import importlib.util
import copy
import fnmatch
import json
import os
import re
import runpy
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.support.fixtures import bash_argv, compatible_bash, write_executable
from tests.test_chezmoi_lifecycle_render import render_template


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("plannotator_config", ROOT / "assets/check-plannotator-config.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
LOAD_JSON = runpy.run_path(str(ROOT / "assets/check-ai-tooling.py"))["load_json"]
PIN_FILES = (".chezmoidata.yaml", MODULE.MANIFEST, MODULE.CLAUDE, MODULE.CONTAINER_CLAUDE)


def matches_pattern(value, pattern):
    if pattern.startswith("/") and pattern.endswith("/"):
        return re.search(pattern[1:-1], value) is not None
    return fnmatch.fnmatchcase(value, pattern)


class PlannotatorConfigTests(unittest.TestCase):
    def source_version(self):
        return re.search(r'^plannotator_version: "([0-9.]+)"', (ROOT / ".chezmoidata.yaml").read_text(encoding="utf-8"), re.MULTILINE).group(1)

    def test_source_agreement(self):
        self.assertEqual(MODULE.source_check(), self.source_version())

    def test_renovate_extracts_claude_marketplace_ref(self):
        config = LOAD_JSON(ROOT / "renovate.json5", jsonc=True)
        occurrences = self.plannotator_occurrences(config)
        self.assertCountEqual(occurrences, [
            (".chezmoidata.yaml", "backnotprop/plannotator"),
            (MODULE.MANIFEST, "backnotprop/plannotator"),
            (MODULE.MANIFEST, "@plannotator/opencode"),
            (MODULE.CLAUDE, "backnotprop/plannotator"),
            (MODULE.CONTAINER_CLAUDE, "backnotprop/plannotator"),
        ])

    def plannotator_occurrences(self, config):
        occurrences = []
        for relative in PIN_FILES:
            text = (ROOT / relative).read_text(encoding="utf-8")
            for manager in config["customManagers"]:
                if not any(matches_pattern(relative, pattern) for pattern in manager["managerFilePatterns"]):
                    continue
                for expression in manager["matchStrings"]:
                    pattern = re.sub(r"\(\?<([A-Za-z_]+)>", r"(?P<\1>", expression)
                    for match in re.finditer(pattern, text):
                        dep_name = manager.get("depNameTemplate", match.groupdict().get("depName"))
                        if dep_name in ("backnotprop/plannotator", "@plannotator/opencode"):
                            self.assertEqual(match.group("currentValue"), self.source_version())
                            occurrences.append((relative, dep_name))
        return occurrences

    def assert_aligned_group(self, config):
        for relative, dep_name in self.plannotator_occurrences(config):
            for update_type in ("patch", "minor", "major"):
                effective = {key: config.get(key) for key in (
                    "groupName", "additionalBranchPrefix", "minimumReleaseAge", "automerge",
                )}
                selectors = {
                    "matchManagers": "custom.regex", "matchFileNames": relative,
                    "matchDepNames": dep_name, "matchUpdateTypes": update_type,
                }
                for rule in config["packageRules"]:
                    self.assertFalse({key for key in rule if key.startswith("match")} - selectors.keys())
                    if all(key not in rule or any(matches_pattern(value, pattern) for pattern in rule[key])
                           for key, value in selectors.items()):
                        effective.update({key: rule[key] for key in effective if key in rule})
                self.assertEqual(effective["groupName"], "plannotator aligned", (relative, dep_name, update_type))
                self.assertEqual(effective["additionalBranchPrefix"], "plannotator-")
                self.assertEqual(effective["minimumReleaseAge"], "7 days")
                self.assertEqual(effective["automerge"], update_type == "patch")

    def test_renovate_groups_every_plannotator_pin(self):
        config = LOAD_JSON(ROOT / "renovate.json5", jsonc=True)
        rule = next(rule for rule in config["packageRules"] if rule.get("groupName") == "plannotator aligned")
        self.assertEqual(rule["matchManagers"], ["custom.regex"])
        self.assertCountEqual(rule["matchDepNames"], ["backnotprop/plannotator", "@plannotator/opencode"])
        self.assertCountEqual(rule["matchFileNames"], PIN_FILES)
        self.assert_aligned_group(config)

    def test_missing_container_group_and_later_override_are_detected(self):
        config = LOAD_JSON(ROOT / "renovate.json5", jsonc=True)
        missing = copy.deepcopy(config)
        rule = next(rule for rule in missing["packageRules"] if rule.get("groupName") == "plannotator aligned")
        rule["matchFileNames"].remove(MODULE.CONTAINER_CLAUDE)
        with self.assertRaises(AssertionError):
            self.assert_aligned_group(missing)
        config["packageRules"].append({"matchFileNames": [MODULE.CONTAINER_CLAUDE], "groupName": "separate"})
        with self.assertRaises(AssertionError):
            self.assert_aligned_group(config)

    def test_mismatched_sentinel_fails(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for relative in (
                ".chezmoidata.yaml", MODULE.MANIFEST, MODULE.CLAUDE, MODULE.CONTAINER_CLAUDE, MODULE.HOST,
                MODULE.PROJECT, MODULE.CONTAINER, "private_dot_config/dotfiles/versions/plannotator.tmpl",
                "private_dot_plannotator/modify_private_config.json",
                "private_Documents/development/container-dotfiles/devcontainers/gitlab.com/servers-homelab/homelab-IaC/dot_devcontainer/devcontainer.json.tmpl",
            ):
                target = root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes((ROOT / relative).read_bytes())
            version = self.source_version()
            self.assertEqual(MODULE.source_check(root), version)
            sentinel = root / MODULE.MANIFEST
            original = sentinel.read_text(encoding="utf-8")
            changed = original.replace(f'"version": "{version}" // renovate: datasource=npm depName=@plannotator/opencode', '"version": "0.0.0" // renovate: datasource=npm depName=@plannotator/opencode')
            self.assertNotEqual(original, changed)
            sentinel.write_text(changed, encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "must match"):
                MODULE.source_check(root)

    def test_container_claude_mirror_must_match(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for relative in (
                ".chezmoidata.yaml", MODULE.MANIFEST, MODULE.CLAUDE, MODULE.CONTAINER_CLAUDE,
                MODULE.HOST, MODULE.PROJECT, MODULE.CONTAINER,
                "private_dot_config/dotfiles/versions/plannotator.tmpl",
                "private_dot_plannotator/modify_private_config.json",
                "private_Documents/development/container-dotfiles/devcontainers/gitlab.com/servers-homelab/homelab-IaC/dot_devcontainer/devcontainer.json.tmpl",
            ):
                target = root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes((ROOT / relative).read_bytes())
            mirror = root / MODULE.CONTAINER_CLAUDE
            settings = json.loads(mirror.read_text(encoding="utf-8"))
            settings["extraKnownMarketplaces"]["plannotator"]["source"]["ref"] = "v0.0.0"
            mirror.write_text(json.dumps(settings), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Claude marketplace tag must match CLI"):
                MODULE.source_check(root)

    def test_runtime_missing_pin_is_read_only(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(FileNotFoundError):
                MODULE.runtime_check(self.source_version(), Path(temp))
            self.assertEqual(list(Path(temp).iterdir()), [])

    def test_runtime_requires_matching_export_before_cli_probe(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            pin = root / ".config/dotfiles/versions/plannotator"
            pin.parent.mkdir(parents=True)
            pin.write_text(self.source_version() + "\n", encoding="utf-8")
            for env, message in (({}, "missing or invalid"),
                                 ({"PLANNOTATOR_VERSION": self.source_version()}, "missing or invalid"),
                                 ({"PLANNOTATOR_PIN_VERSION": ""}, "missing or invalid"),
                                 ({"PLANNOTATOR_PIN_VERSION": "0.0.0"}, "differs"),
                                 ({"PLANNOTATOR_PIN_VERSION": "v0.27.22"}, "missing or invalid")):
                with self.subTest(env=env), patch.dict("os.environ", env, clear=True), patch.object(MODULE.shutil, "which") as which:
                    with self.assertRaisesRegex(ValueError, message):
                        MODULE.runtime_check(self.source_version(), root)
                    which.assert_not_called()

    def test_runtime_matching_export_reaches_cli_probe(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            pin = root / ".config/dotfiles/versions/plannotator"
            pin.parent.mkdir(parents=True)
            pin.write_text(self.source_version() + "\r\n", encoding="utf-8")
            with patch.dict("os.environ", {"PLANNOTATOR_PIN_VERSION": self.source_version()}, clear=True), patch.object(MODULE.shutil, "which", return_value=None):
                with self.assertRaisesRegex(ValueError, "CLI is missing"):
                    MODULE.runtime_check(self.source_version(), root)

    def test_malformed_source_claude_settings_fails_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for relative in (".chezmoidata.yaml", MODULE.MANIFEST):
                target = root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes((ROOT / relative).read_bytes())
            target = root / MODULE.CLAUDE
            target.parent.mkdir(parents=True)
            target.write_text(json.dumps({"enabledPlugins": {}}), encoding="utf-8")
            with self.assertRaises(KeyError):
                MODULE.source_check(root)


@unittest.skipUnless(shutil.which("chezmoi"), "chezmoi is unavailable")
class PlannotatorDeploymentTests(unittest.TestCase):
    def test_platform_admission(self):
        version = PlannotatorConfigTests().source_version()
        for platform in ("windows", "linux", "wsl2", "macos"):
            with self.subTest(platform=platform), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                source = root / "source"
                source.mkdir()
                (source / ".chezmoiignore").write_text(
                    render_template(".chezmoiignore", platform), encoding="utf-8"
                )
                versions = source / "private_dot_config/dotfiles/versions"
                versions.mkdir(parents=True)
                (versions / "plannotator.tmpl").write_bytes(
                    (ROOT / "private_dot_config/dotfiles/versions/plannotator.tmpl").read_bytes()
                )
                (versions / "unrelated").write_text("not a pin\n", encoding="utf-8")
                mise = source / "private_dot_config/mise"
                mise.mkdir(parents=True)
                (mise / "unrelated.toml").write_text("unrelated = true\n", encoding="utf-8")
                config = root / "chezmoi.toml"
                config.write_text("", encoding="utf-8")
                destination = root / "home"
                destination.mkdir()
                command = [
                    "chezmoi", "--config", str(config), "--source", str(source),
                    "--destination", str(destination), "--override-data",
                    json.dumps({"plannotator_version": version}),
                ]

                def run(*args):
                    result = subprocess.run(
                        [*command, *args], capture_output=True, text=True, check=False
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
                    return result.stdout

                managed = run("managed", "--include", "files").splitlines()
                pin = ".config/dotfiles/versions/plannotator"
                self.assertIn(pin, managed)
                self.assertEqual(run("cat", str(destination / pin)).strip(), version)
                if platform == "windows":
                    rules = (source / ".chezmoiignore").read_text(encoding="utf-8")
                    self.assertFalse(any(line.startswith(("/", "!/")) for line in rules.splitlines()))
                    ignored = run("ignored").splitlines()
                    self.assertIn(".config/dotfiles/versions/unrelated", ignored)
                    self.assertIn(".config/mise", ignored)
                    self.assertNotIn(".config/dotfiles/versions/unrelated", managed)
                    for exception in (
                        "!.config/dotfiles/",
                        "!.config/dotfiles/versions/",
                        "!.config/dotfiles/versions/plannotator",
                    ):
                        self.assertIn(exception + "\n", rules)
                        rules = rules.replace(exception + "\n", "")
                    (source / ".chezmoiignore").write_text(rules, encoding="utf-8")
                    self.assertNotIn(pin, run("managed", "--include", "files").splitlines())


class PlannotatorWrapperTests(unittest.TestCase):
    def test_wrappers_validate_pin_before_dry_run(self):
        paths = (
            "bin/executable_opencode-plannotator.tmpl",
            "bin/executable_opencode-plannotator-custom.tmpl",
            "private_Documents/development/container-dotfiles/dotfiles/dot_local/bin/executable_opencode-plannotator.tmpl",
            "private_Documents/development/container-dotfiles/dotfiles/dot_local/bin/executable_opencode-plannotator-custom.tmpl",
        )
        for relative in paths:
            with self.subTest(relative=relative), tempfile.TemporaryDirectory() as temporary:
                home = Path(temporary)
                rendered = (ROOT / relative).read_text(encoding="utf-8")
                for template, value in (
                    ("{{ .plannotator_ports.host.build }}", "8993-8998"),
                    ("{{ .plannotator_ports.host.custom }}", "9004-9009"),
                    ("{{ .plannotator_ports.devcontainer.build }}", "9993-9998"),
                    ("{{ .plannotator_ports.devcontainer.custom }}", "10004-10009"),
                ):
                    rendered = rendered.replace(template, value)
                self.assertNotIn("{{", rendered)
                wrapper = write_executable(home / Path(relative).name.removeprefix("executable_").removesuffix(".tmpl"), rendered)
                bash = compatible_bash(ROOT / relative, home)
                self.assertIsNotNone(bash, "requires Bash with access to the checkout and fixture")
                pin = home / ".config/dotfiles/versions/plannotator"
                env = {**os.environ, "HOME": str(home), "PLANNOTATOR_PIN_VERSION": "0.0.0", "OPENCODE_PLANNOTATOR_DRY_RUN": "1"}

                def run():
                    command = bash_argv(bash, wrapper) if os.name == "nt" else [str(wrapper)]
                    return subprocess.run(command, env=env, capture_output=True,
                                           text=True, encoding="utf-8", check=False)

                self.assertNotEqual(run().returncode, 0)
                pin.parent.mkdir(parents=True)
                pin.write_text("0.27.22\n0.27.23\n", encoding="utf-8")
                self.assertIn("invalid Plannotator pin", run().stderr)
                pin.write_text(" 0.27.22\r\n", encoding="utf-8")
                result = run()
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("version=0.27.22", result.stderr)


if __name__ == "__main__":
    unittest.main()
