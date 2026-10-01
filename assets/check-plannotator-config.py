#!/usr/bin/env python3
import argparse
import json
import os
import re
import shutil
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
PIN = "~/.config/dotfiles/versions/plannotator"
SPEC = f"@plannotator/opencode@{{file:{PIN}}}"
HOST = "private_dot_config/opencode/opencode.jsonc"
CONTAINER = (
    "private_Documents/development/container-dotfiles/dotfiles/"
    "private_dot_config/opencode/opencode.jsonc"
)
CLAUDE = "dot_claude/settings-base.json"
CONTAINER_CLAUDE = (
    "private_Documents/development/container-dotfiles/dotfiles/"
    "dot_claude/settings-base.json"
)
MANIFEST = "configs/host-ai-plugin-refresh.jsonc"


def single(pattern, text, label):
    matches = re.findall(pattern, text, flags=re.MULTILINE)
    if len(matches) != 1:
        raise ValueError(f"{label}: expected one version, found {len(matches)}")
    return matches[0]


def source_check(root=ROOT):
    def contents(relative):
        return (root / relative).read_text(encoding="utf-8")

    version = single(r'^plannotator_version: "([0-9]+\.[0-9]+\.[0-9]+)"', contents(".chezmoidata.yaml"), "CLI")
    manifest = contents(MANIFEST)
    claude = single(r'"id": "plannotator@plannotator",\s*"scope": "user",\s*"version": "([0-9.]+)"', manifest, "Claude sentinel")
    opencode = single(r'"package": "@plannotator/opencode",\s*"version": "([0-9.]+)"', manifest, "OpenCode sentinel")
    if not all(v == version for v in (claude, opencode)):
        raise ValueError("CLI, Claude marketplace and both refresh sentinels must match")
    for relative in (CLAUDE, CONTAINER_CLAUDE):
        settings = json.loads(contents(relative))
        marketplace = settings["extraKnownMarketplaces"]["plannotator"]
        if not settings["enabledPlugins"].get("plannotator@plannotator"):
            raise ValueError(f"{relative}: Claude Plannotator plugin must remain enabled")
        if marketplace["autoUpdate"] is not False or marketplace["source"]["ref"] != f"v{version}":
            raise ValueError(f"{relative}: Claude marketplace tag must match CLI and disable autoUpdate")
    expected = f'"{SPEC}"'
    for relative in (HOST, CONTAINER):
        if contents(relative).count(expected) != 1:
            raise ValueError(f"{relative}: expected one shared Plannotator plugin spec")
    if contents("private_dot_config/dotfiles/versions/plannotator.tmpl").strip() != "{{ .plannotator_version }}":
        raise ValueError("shared version template must follow canonical CLI pin")
    privacy = contents("private_dot_plannotator/modify_private_config.json")
    if 'set $config "share" "disabled"' not in privacy or 'set $config "jina" false' not in privacy:
        raise ValueError("host Plannotator privacy settings are missing")
    container_config = contents("private_Documents/development/container-dotfiles/devcontainers/gitlab.com/servers-homelab/homelab-IaC/dot_devcontainer/devcontainer.json.tmpl")
    for key, value in (("PLANNOTATOR_SHARE", "disabled"), ("PLANNOTATOR_JINA", "false")):
        if f'"{key}": "{value}"' not in container_config:
            raise ValueError(f"container {key} must be {value}")
    return version


def runtime_check(version, home=Path.home()):
    deployed = (home / ".config/dotfiles/versions/plannotator").read_text(encoding="utf-8").strip()
    if deployed != version:
        raise ValueError("deployed OpenCode pin differs from source CLI pin")
    binary = shutil.which("plannotator")
    if binary is None:
        raise ValueError("plannotator CLI is missing from PATH")
    result = subprocess.run([binary, "--version"], check=True, capture_output=True, text=True)
    actual = single(r"(?<![0-9])([0-9]+\.[0-9]+\.[0-9]+)(?![0-9])", result.stdout, "CLI runtime")
    if actual != version:
        raise ValueError("PATH CLI differs from source CLI pin")
    config = json.loads((home / ".plannotator/config.json").read_text(encoding="utf-8"))
    if not isinstance(config, dict) or config.get("share") != "disabled" or config.get("jina") is not False:
        raise ValueError("deployed Plannotator privacy config is not disabled")
    if os.environ.get("PLANNOTATOR_SHARE", "disabled") != "disabled":
        raise ValueError("PLANNOTATOR_SHARE overrides disabled sharing")
    if os.environ.get("PLANNOTATOR_JINA", "false").lower() in ("true", "1"):
        raise ValueError("PLANNOTATOR_JINA overrides disabled Jina")
    claude = shutil.which("claude")
    if claude is None:
        raise ValueError("Claude Code is missing from PATH; installed plugin cannot be verified")
    def inventory(*args):
        output = subprocess.run([claude, "plugin", *args, "--json"], check=True, capture_output=True, text=True)
        records = json.loads(output.stdout)
        if not isinstance(records, list):
            raise ValueError("Claude inventory must be an array")
        return records
    registrations = [entry for entry in inventory("marketplace", "list") if entry.get("name") == "plannotator"]
    if len(registrations) != 1 or registrations[0].get("ref") != f"v{version}":
        raise ValueError("Claude marketplace registration has the wrong tag")
    installs = [entry for entry in inventory("list") if entry.get("id") == "plannotator@plannotator"]
    if len(installs) != 1 or installs[0].get("version") != version or installs[0].get("enabled") is not True:
        raise ValueError("Claude installed Plannotator version/enablement differs from CLI")
    return binary


def main():
    parser = argparse.ArgumentParser(description="Check Plannotator version and privacy contracts")
    parser.add_argument("--runtime", action="store_true", help="also check this host's deployed CLI and settings")
    args = parser.parse_args()
    try:
        version = source_check()
        if args.runtime:
            binary = runtime_check(version)
            print(f"Plannotator {version}: source and {binary} agree")
        else:
            print(f"Plannotator {version}: source pins and privacy agree")
    except (OSError, KeyError, ValueError, subprocess.CalledProcessError, json.JSONDecodeError) as exc:
        parser.exit(1, f"ERROR: Plannotator check failed: {exc}\n")


if __name__ == "__main__":
    main()
