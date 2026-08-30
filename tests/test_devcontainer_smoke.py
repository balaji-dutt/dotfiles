from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "assets/devcontainer-smoke.py"
RUNTIME = (
    REPO_ROOT
    / "private_Documents/development/container-dotfiles/devcontainers"
    / "gitlab.com/servers-homelab/homelab-IaC/dot_devcontainer"
)
TEMPLATE = RUNTIME / "devcontainer.json.tmpl"

SPEC = importlib.util.spec_from_file_location("devcontainer_smoke", SCRIPT)
assert SPEC and SPEC.loader
SMOKE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SMOKE)


class FakeRunner:
    instances: list[FakeRunner] = []

    def __init__(self, artifacts: Path, timeout: int) -> None:
        self.artifacts = artifacts
        self.timeout = timeout
        self.calls: list[tuple[list[str], bool]] = []
        artifacts.mkdir(parents=True, exist_ok=True)
        self.__class__.instances.append(self)

    def run(
        self,
        argv: list[str],
        *,
        check: bool = True,
        timeout: int | None = None,
    ) -> subprocess.CompletedProcess[str]:
        del timeout
        self.calls.append((argv, check))
        if argv[:2] == ["devcontainer", "up"]:
            raise SMOKE.SmokeFailure("synthetic up failure")
        return subprocess.CompletedProcess(argv, 0, "", "")


class DevcontainerSmokeContractTests(unittest.TestCase):
    def test_probe_without_opt_in_has_no_command_or_filesystem_side_effects(self) -> None:
        with tempfile.TemporaryDirectory(prefix="devcontainer-smoke-probe-") as temp_name:
            root = Path(temp_name)
            bin_dir = root / "bin"
            bin_dir.mkdir()
            marker = root / "command.log"
            for command in ("docker", "devcontainer"):
                path = bin_dir / command
                path.write_text(
                    f"#!/bin/sh\nprintf '%s\\n' {command} >>{marker}\n",
                    encoding="utf-8",
                )
                path.chmod(0o755)
            before = sorted(path.relative_to(root) for path in root.rglob("*"))
            env = os.environ.copy()
            env.pop("DEVCONTAINER_SMOKE", None)
            env["PATH"] = str(bin_dir)
            env["TMPDIR"] = str(root)

            result = subprocess.run(
                [sys.executable, str(SCRIPT), "--probe"],
                cwd=REPO_ROOT,
                env=env,
                check=False,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            self.assertEqual(result.returncode, 3, result.stderr)
            self.assertIn("DEVCONTAINER_SMOKE=1", result.stderr)
            self.assertFalse(marker.exists())
            self.assertEqual(before, sorted(path.relative_to(root) for path in root.rglob("*")))

    def test_generated_fixture_is_disposable_and_tracks_production_contract(self) -> None:
        with tempfile.TemporaryDirectory(prefix="devcontainer-smoke-config-") as temp_name:
            root = Path(temp_name)
            workspace = SMOKE.write_fixture(
                root,
                REPO_ROOT,
                "abc123",
                "dotfiles-devcontainer-smoke-abc123",
            )
            config = json.loads(
                (workspace / ".devcontainer/devcontainer.json").read_text(encoding="utf-8")
            )

            self.assertEqual(config["image"], "homelab-iac:base")
            self.assertIn("--pull=never", config["runArgs"])
            self.assertIn("--network=none", config["runArgs"])
            self.assertEqual(config["remoteUser"], "vscode")
            self.assertEqual(
                config["postCreateCommand"],
                [
                    "bash",
                    "/tmp/devcontainer-smoke/lifecycle.sh",
                    "post-create",
                    "${containerWorkspaceFolder}",
                ],
            )
            self.assertEqual(
                config["postStartCommand"],
                [
                    "bash",
                    "/tmp/devcontainer-smoke/lifecycle.sh",
                    "post-start",
                    "${containerWorkspaceFolder}",
                ],
            )
            for mount in config["mounts"]:
                if "type=bind" not in mount:
                    self.assertIn("dotfiles-devcontainer-smoke-abc123", mount)
                    continue
                source = Path(mount.split(",", 1)[0].removeprefix("source="))
                self.assertTrue(
                    source.is_relative_to(root) or source == RUNTIME,
                    mount,
                )
                self.assertTrue(mount.endswith(",readonly"), mount)

            lifecycle = root / "fixture/smoke/lifecycle.sh"
            syntax = subprocess.run(
                ["bash", "-n", str(lifecycle)],
                check=False,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            self.assertEqual(syntax.returncode, 0, syntax.stderr)

            template = TEMPLATE.read_text(encoding="utf-8")
            self.assertIn('"image": "homelab-iac:base"', template)
            self.assertIn("target=/tmp/host-homelab-devcontainer,type=bind,readonly", template)
            self.assertIn(
                '"postCreateCommand": ["bash", "/tmp/host-homelab-devcontainer/postCreate.sh"',
                template,
            )
            self.assertIn(
                '"postStartCommand": ["bash", "/tmp/host-homelab-devcontainer/postStart.sh"',
                template,
            )

    def test_runtime_configuration_composes_smoke_safe_phases_in_order(self) -> None:
        command = f"""source {RUNTIME / 'postStart.sh'}
post_start_persistence_phase() {{ printf 'persistence:%s\\n' "$1"; }}
post_start_optional_integrations_phase() {{ printf 'optional\\n'; }}
post_start_materialization_phase() {{ printf 'materialization:%s\\n' "$1"; }}
post_start_runtime_configuration '/workspace with spaces'
"""
        result = subprocess.run(
            ["bash", "-c", command],
            cwd=REPO_ROOT,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.stdout.splitlines(),
            [
                "persistence:/workspace with spaces",
                "optional",
                "materialization:/workspace with spaces",
            ],
        )

    def test_failed_up_collects_diagnostics_and_cleans_owned_resources(self) -> None:
        FakeRunner.instances.clear()
        with tempfile.TemporaryDirectory(prefix="devcontainer-smoke-failure-") as temp_name:
            artifacts = Path(temp_name) / "artifacts"
            env = {
                "DEVCONTAINER_SMOKE_ARTIFACTS": str(artifacts),
                "DEVCONTAINER_SMOKE_TIMEOUT_SECONDS": "60",
            }
            with mock.patch.object(SMOKE, "CommandRunner", FakeRunner):
                with self.assertRaisesRegex(SMOKE.SmokeFailure, "synthetic up failure"):
                    SMOKE.run_smoke(REPO_ROOT, env)

            runner = FakeRunner.instances[-1]
            argv = [call for call, _ in runner.calls]
            self.assertTrue(any(call[:3] == ["docker", "ps", "-a"] for call in argv))
            self.assertTrue(any(call[:3] == ["docker", "ps", "-aq"] for call in argv))
            self.assertTrue(any(call[:3] == ["docker", "volume", "rm"] for call in argv))
            run_artifacts = next(artifacts.iterdir())
            self.assertTrue((run_artifacts / "devcontainer.json").is_file())
            self.assertTrue((run_artifacts / "lifecycle.sh").is_file())


if __name__ == "__main__":
    unittest.main()
