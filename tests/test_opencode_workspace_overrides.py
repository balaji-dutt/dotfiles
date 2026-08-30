from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import subprocess
import unittest
from pathlib import Path

from tests.support.fixtures import isolated_environment


REPO_ROOT = Path(__file__).resolve().parents[1]
HOST_SCRIPT = REPO_ROOT / "private_dot_config/opencode/opencode-sync-workspace-overrides.sh"
CONTAINER_SCRIPT = (
    REPO_ROOT
    / "private_Documents/development/container-dotfiles/devcontainers"
    / "gitlab.com/servers-homelab/homelab-IaC/dot_devcontainer"
    / "opencode-sync-workspace-overrides.sh"
)
BASH = shutil.which("bash")


@unittest.skipUnless(os.name != "nt" and BASH, "POSIX bash is required")
class OpenCodeWorkspaceOverrideTests(unittest.TestCase):
    def setUp(self) -> None:
        self.environment_context = isolated_environment(prefix="workspace-overrides-")
        self.fixture = self.environment_context.__enter__()
        self.addCleanup(self.environment_context.__exit__, None, None, None)

    def case_paths(self, label: str) -> tuple[Path, Path, Path]:
        root = self.fixture.root / f"{label} fixture with spaces"
        home = root / "home"
        config_home = root / "config home"
        workspace = root / "workspace with spaces"
        home.mkdir(parents=True)
        workspace.mkdir(parents=True)
        return home, config_home, workspace

    def run_script(
        self,
        script: Path,
        profiles: str,
        workspace: Path | None,
        home: Path,
        config_home: Path,
        *,
        cwd: Path | None = None,
    ) -> subprocess.CompletedProcess[str]:
        env = dict(self.fixture.env)
        env.update({"HOME": str(home), "XDG_CONFIG_HOME": str(config_home)})
        argv = [BASH or "bash", str(script), profiles]
        if workspace is not None:
            argv.append(str(workspace))
        return subprocess.run(
            argv,
            cwd=cwd or self.fixture.root,
            env=env,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    def write_jsonc(self, path: Path, payload: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(payload, encoding="utf-8")

    def runtime_path(self, config_home: Path, profiles_key: str, workspace: Path) -> Path:
        workspace_hash = hashlib.sha256(str(workspace).encode("utf-8")).hexdigest()[:16]
        return (
            config_home
            / "opencode"
            / "runtime"
            / profiles_key
            / workspace_hash
            / "opencode.jsonc"
        )

    def install_merge_fixture(self, config_home: Path, workspace: Path) -> None:
        opencode_home = config_home / "opencode"
        self.write_jsonc(
            opencode_home / "opencode.jsonc",
            """{
  // Global values load before profile overlays.
  "plugin": ["global",],
  "agent": {
    "build": {
      "model": "anthropic/claude-global",
      "permission": {"edit": "ask",},
    },
    "fallback": {"model": "moonshot/kimi-k3",},
  },
  "nested": {"global": true, "shared": {"one": 1,},},
  "array": ["global",],
  "scalar": "global",
  "instructions": "{file:./instructions/global.md}",
}
""",
        )
        (opencode_home / "opencode.json").write_text("{}\n", encoding="utf-8")
        self.write_jsonc(
            opencode_home / "profiles" / "defaults" / "opencode.jsonc",
            """{
  /* Defaults overlay the global configuration. */
  "plugin": ["defaults"],
  "agent": {"build": {"permission": {"bash": "deny"}}},
  "nested": {"defaults": true, "shared": {"two": 2}},
  "array": ["defaults"],
  "scalar": {"defaults": true}
}
""",
        )
        self.write_jsonc(
            opencode_home / "profiles" / "review" / "opencode.jsonc",
            """{
  "agent": {"build": {"temperature": 0.4}},
  "nested": {"review": true, "shared": {"three": 3}},
  "array": ["review"],
  "scalar": "review"
}
""",
        )
        workspace_config = workspace / ".opencode"
        self.write_jsonc(
            workspace_config / "opencode.jsonc",
            """{
  "plugin": ["ignored-workspace"],
  "nested": {"ignored": true},
  "agent": {
    "build": {
      "model": "anthropic/claude-workspace",
      "permission": {"edit": "allow"}
    },
    "new": {"model": "google/gemini-3.6-flash"}
  }
}
""",
        )
        (workspace_config / "opencode.json").write_text(
            json.dumps({"agent": {"build": {"model": "ignored/json"}}}) + "\n",
            encoding="utf-8",
        )

    def test_host_and_container_scripts_remain_identical(self) -> None:
        self.assertEqual(HOST_SCRIPT.read_bytes(), CONTAINER_SCRIPT.read_bytes())

    def test_jsonc_merge_remapping_rebasing_and_idempotency(self) -> None:
        for index, script in enumerate((HOST_SCRIPT, CONTAINER_SCRIPT)):
            with self.subTest(script=script):
                home, config_home, workspace = self.case_paths(f"merge-{index}")
                self.install_merge_fixture(config_home, workspace)
                profiles = "chatgpt review review anthropic-api api-fallback"
                result = self.run_script(script, profiles, workspace, home, config_home)
                self.assertEqual(result.returncode, 0, result.stderr)
                active_path = Path(result.stdout.splitlines()[-1]) / "opencode.jsonc"
                self.assertEqual(
                    active_path,
                    self.runtime_path(
                        config_home,
                        "defaults--review--anthropic-api--api-fallback",
                        workspace,
                    ),
                )
                config = json.loads(active_path.read_text(encoding="utf-8"))
                self.assertEqual(config["plugin"], ["defaults"])
                self.assertEqual(config["array"], ["review"])
                self.assertEqual(config["scalar"], "review")
                self.assertEqual(
                    config["nested"],
                    {
                        "global": True,
                        "defaults": True,
                        "review": True,
                        "shared": {"one": 1, "two": 2, "three": 3},
                    },
                )
                self.assertEqual(
                    config["agent"]["build"],
                    {
                        "model": "anthropic-api/claude-workspace",
                        "permission": {"edit": "allow", "bash": "deny"},
                        "temperature": 0.4,
                    },
                )
                self.assertEqual(
                    config["agent"]["fallback"]["model"],
                    "openrouter/moonshotai/kimi-k3",
                )
                self.assertEqual(
                    config["agent"]["new"]["model"],
                    "openrouter/z-ai/glm-5.2:exacto",
                )
                self.assertNotIn("ignored", config["nested"])
                expected_ref = os.path.relpath(
                    config_home / "opencode" / "instructions" / "global.md",
                    active_path.parent,
                )
                self.assertEqual(
                    config["instructions"], f"{{file:{Path(expected_ref).as_posix()}}}"
                )
                self.assertEqual(stat.S_IMODE(active_path.stat().st_mode), 0o600)
                self.assertFalse((config_home / "opencode" / "opencode.json").exists())
                first = active_path.read_bytes()
                repeated = self.run_script(
                    script, profiles, workspace, home, config_home
                )
                self.assertEqual(repeated.returncode, 0, repeated.stderr)
                self.assertEqual(active_path.read_bytes(), first)
                self.assertEqual(list(active_path.parent.glob(".opencode.jsonc.*.tmp")), [])

    def test_profile_fallback_missing_inputs_and_invalid_names(self) -> None:
        for index, script in enumerate((HOST_SCRIPT, CONTAINER_SCRIPT)):
            with self.subTest(script=script):
                home, config_home, workspace = self.case_paths(f"fallback-{index}")
                review = (
                    config_home
                    / "opencode"
                    / "profiles"
                    / "review"
                    / "opencode.jsonc"
                )
                self.write_jsonc(review, '{"agent":{"build":{"model":"openai/review"}}}\n')
                fallback = self.run_script(
                    script, "review review", workspace, home, config_home
                )
                self.assertEqual(fallback.returncode, 0, fallback.stderr)
                active_path = Path(fallback.stdout.splitlines()[-1]) / "opencode.jsonc"
                self.assertEqual(active_path.parent.parent.name, "review")
                self.assertEqual(
                    json.loads(active_path.read_text())["agent"]["build"]["model"],
                    "openai/review",
                )

                review.unlink()
                missing = self.run_script(script, "review", workspace, home, config_home)
                self.assertEqual(missing.returncode, 0, missing.stderr)
                self.assertEqual(
                    missing.stdout.strip(),
                    str(config_home / "opencode" / "profiles" / "review"),
                )
                invalid = self.run_script(
                    script, "defaults ../unsafe", workspace, home, config_home
                )
                self.assertNotEqual(invalid.returncode, 0)
                self.assertIn("Invalid OpenCode profile", invalid.stderr)

    def test_git_workspace_discovery_and_legacy_runtime_cleanup(self) -> None:
        for index, script in enumerate((HOST_SCRIPT, CONTAINER_SCRIPT)):
            with self.subTest(script=script):
                home, config_home, workspace = self.case_paths(f"git-{index}")
                subprocess.run(
                    ["git", "init", "-q"],
                    cwd=workspace,
                    env=self.fixture.env,
                    check=True,
                )
                discovered_workspace = Path(
                    subprocess.run(
                        ["git", "rev-parse", "--show-toplevel"],
                        cwd=workspace,
                        env=self.fixture.env,
                        check=True,
                        text=True,
                        stdout=subprocess.PIPE,
                    ).stdout.strip()
                )
                self.write_jsonc(
                    config_home
                    / "opencode"
                    / "profiles"
                    / "defaults"
                    / "opencode.jsonc",
                    "{}\n",
                )
                active_path = self.runtime_path(
                    config_home, "defaults", discovered_workspace
                )
                active_path.parent.mkdir(parents=True)
                legacy = active_path.with_suffix(".json")
                legacy.write_text("{}\n", encoding="utf-8")
                result = self.run_script(
                    script,
                    "defaults",
                    None,
                    home,
                    config_home,
                    cwd=workspace,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(Path(result.stdout.strip()), active_path.parent)
                self.assertTrue(active_path.is_file())
                self.assertFalse(legacy.exists())

    def test_failures_preserve_output_and_remove_temporary_files(self) -> None:
        for index, script in enumerate((HOST_SCRIPT, CONTAINER_SCRIPT)):
            with self.subTest(script=script):
                home, config_home, workspace = self.case_paths(f"failure-{index}")
                self.install_merge_fixture(config_home, workspace)
                profiles = "defaults review"
                initial = self.run_script(
                    script, profiles, workspace, home, config_home
                )
                self.assertEqual(initial.returncode, 0, initial.stderr)
                active_path = Path(initial.stdout.splitlines()[-1]) / "opencode.jsonc"
                valid_output = active_path.read_bytes()
                review = (
                    config_home
                    / "opencode"
                    / "profiles"
                    / "review"
                    / "opencode.jsonc"
                )
                review.write_text('{"agent":', encoding="utf-8")
                malformed = self.run_script(
                    script, profiles, workspace, home, config_home
                )
                self.assertNotEqual(malformed.returncode, 0)
                self.assertIn("Failed to parse profile JSONC", malformed.stderr)
                self.assertEqual(active_path.read_bytes(), valid_output)

                review.write_text("{}\n", encoding="utf-8")
                active_path.unlink()
                active_path.mkdir()
                marker = active_path / "preserved"
                marker.write_text("keep\n", encoding="utf-8")
                replacement = self.run_script(
                    script, profiles, workspace, home, config_home
                )
                self.assertNotEqual(replacement.returncode, 0)
                self.assertIn("Failed to replace active OpenCode config", replacement.stderr)
                self.assertEqual(marker.read_text(encoding="utf-8"), "keep\n")
                self.assertEqual(list(active_path.parent.glob(".opencode.jsonc.*.tmp")), [])


if __name__ == "__main__":
    unittest.main()
