from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
CONTAINER_BIN = (
    REPO_ROOT
    / "private_Documents/development/container-dotfiles/dotfiles/dot_local/bin"
)
GUARD = CONTAINER_BIN / "executable_opencode-project-deps-guard"
WRAPPERS = (
    CONTAINER_BIN / "executable_opencode-plannotator.tmpl",
    CONTAINER_BIN / "executable_opencode-plannotator-custom.tmpl",
)
PLUGIN = "@opencode-ai/plugin"


def write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


class GuardFixture:
    def __init__(self, root: Path, version: str = "1.18.15") -> None:
        self.root = root
        self.version = version
        self.repo = root / "repo"
        self.bin_dir = root / "bin"
        self.call_log = root / "calls.jsonl"
        self.repo.mkdir()
        self.bin_dir.mkdir()
        subprocess.run(
            ["git", "init", "--quiet"],
            cwd=self.repo,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.opencode = self.bin_dir / "opencode"
        write_executable(
            self.opencode,
            "#!/usr/bin/env python3\n"
            "import json, os, pathlib, sys\n"
            "path = pathlib.Path(os.environ['GUARD_TEST_CALL_LOG'])\n"
            "with path.open('a', encoding='utf-8') as stream:\n"
            "    stream.write(json.dumps({'tool': 'opencode', 'args': sys.argv[1:]}) + '\\n')\n"
            "if sys.argv[1:] == ['--version']:\n"
            "    print(os.environ.get('GUARD_TEST_CLI_VERSION', '1.18.15'))\n"
            "    raise SystemExit(0)\n"
            "raise SystemExit(int(os.environ.get('GUARD_TEST_OPENCODE_EXIT', '0')))\n",
        )
        self.npm = self.bin_dir / "npm"
        write_executable(
            self.npm,
            "#!/usr/bin/env python3\n"
            "import json, os, pathlib, sys\n"
            "path = pathlib.Path(os.environ['GUARD_TEST_CALL_LOG'])\n"
            "with path.open('a', encoding='utf-8') as stream:\n"
            "    stream.write(json.dumps({'tool': 'npm', 'args': sys.argv[1:]}) + '\\n')\n"
            "if os.environ.get('GUARD_TEST_NPM_MUTATE') == '1':\n"
            "    pathlib.Path('package.json').write_text('{}\\n', encoding='utf-8')\n"
            "if os.environ.get('GUARD_TEST_NPM_FAIL') == '1':\n"
            "    print('fake npm failure', file=sys.stderr)\n"
            "    raise SystemExit(42)\n"
            "version = os.environ.get('GUARD_TEST_INSTALLED_VERSION', '1.18.15')\n"
            "installed = pathlib.Path('node_modules/@opencode-ai/plugin/package.json')\n"
            "installed.parent.mkdir(parents=True, exist_ok=True)\n"
            "installed.write_text(json.dumps({'version': version}) + '\\n', encoding='utf-8')\n",
        )

    @property
    def env(self) -> dict[str, str]:
        env = os.environ.copy()
        env["PATH"] = os.pathsep.join([str(self.bin_dir), env.get("PATH", "")])
        env["GUARD_TEST_CALL_LOG"] = str(self.call_log)
        env["GUARD_TEST_CLI_VERSION"] = self.version
        env["GUARD_TEST_INSTALLED_VERSION"] = self.version
        return env

    def metadata(self, *, version: str | None = None) -> tuple[bytes, bytes]:
        selected = version or self.version
        opencode_dir = self.repo / ".opencode"
        opencode_dir.mkdir(exist_ok=True)
        manifest = {
            "private": True,
            "dependencies": {PLUGIN: selected},
        }
        lockfile = {
            "name": "fixture",
            "lockfileVersion": 3,
            "packages": {
                "": {"dependencies": {PLUGIN: selected}},
                f"node_modules/{PLUGIN}": {"version": selected},
            },
        }
        manifest_raw = (json.dumps(manifest, indent=2) + "\n").encode()
        lockfile_raw = (json.dumps(lockfile, indent=2) + "\n").encode()
        (opencode_dir / "package.json").write_bytes(manifest_raw)
        (opencode_dir / "package-lock.json").write_bytes(lockfile_raw)
        return manifest_raw, lockfile_raw

    def track(self, *relative_paths: str) -> None:
        subprocess.run(
            ["git", "add", "--", *relative_paths],
            cwd=self.repo,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    def run_guard(
        self,
        *,
        cwd: Path | None = None,
        env_updates: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        env = self.env
        if env_updates:
            env.update(env_updates)
        return subprocess.run(
            [
                sys.executable,
                str(GUARD),
                "--opencode-bin",
                str(self.opencode),
                "--cwd",
                str(cwd or self.repo),
            ],
            cwd=cwd or self.repo,
            env=env,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    def calls(self) -> list[dict[str, object]]:
        if not self.call_log.exists():
            return []
        return [json.loads(line) for line in self.call_log.read_text().splitlines()]


class OpenCodeProjectDepsGuardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.fixture = GuardFixture(Path(self.temporary.name))

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def track_metadata(self, *, version: str | None = None) -> tuple[bytes, bytes]:
        raw = self.fixture.metadata(version=version)
        self.fixture.track(".opencode/package.json", ".opencode/package-lock.json")
        return raw

    def test_non_git_and_untracked_projects_pass_without_probing_tools(self) -> None:
        outside = Path(self.temporary.name) / "outside"
        outside.mkdir()
        result = self.fixture.run_guard(cwd=outside)
        self.assertEqual(result.returncode, 0, result.stderr)

        self.fixture.metadata()
        result = self.fixture.run_guard()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.fixture.calls(), [])

    def test_nested_directory_discovers_worktree_and_hydrates(self) -> None:
        self.track_metadata()
        nested = self.fixture.repo / "src/deep"
        nested.mkdir(parents=True)
        result = self.fixture.run_guard(cwd=nested)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([call["tool"] for call in self.fixture.calls()], ["opencode", "npm"])

    def test_one_sided_tracked_metadata_fails_closed(self) -> None:
        self.fixture.metadata()
        self.fixture.track(".opencode/package.json")
        result = self.fixture.run_guard()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("metadata is incomplete", result.stderr)
        self.assertEqual(self.fixture.calls(), [])

    def test_missing_or_malformed_tracked_metadata_fails_closed(self) -> None:
        self.track_metadata()
        (self.fixture.repo / ".opencode/package-lock.json").unlink()
        result = self.fixture.run_guard()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("tracked package-lock.json is unavailable", result.stderr)

        self.fixture.metadata()
        (self.fixture.repo / ".opencode/package.json").write_text("{broken\n")
        result = self.fixture.run_guard()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("tracked package.json is malformed", result.stderr)

    def test_non_exact_and_inconsistent_metadata_fails_closed(self) -> None:
        self.track_metadata()
        manifest_path = self.fixture.repo / ".opencode/package.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["dependencies"][PLUGIN] = "^1.18.15"
        manifest_path.write_text(json.dumps(manifest) + "\n")
        result = self.fixture.run_guard()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("must be an exact semantic version", result.stderr)

        self.fixture.metadata()
        lock_path = self.fixture.repo / ".opencode/package-lock.json"
        lockfile = json.loads(lock_path.read_text())
        lockfile["packages"][f"node_modules/{PLUGIN}"]["version"] = "1.18.14"
        lock_path.write_text(json.dumps(lockfile) + "\n")
        result = self.fixture.run_guard()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("versions do not match", result.stderr)

    def test_cli_mismatch_blocks_before_npm_or_actual_launch(self) -> None:
        self.track_metadata(version="1.15.6")
        result = self.fixture.run_guard()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("OpenCode CLI: 1.18.15", result.stderr)
        self.assertIn("package.json: 1.15.6", result.stderr)
        self.assertIn("npm --prefix .opencode install", result.stderr)
        self.assertEqual(
            self.fixture.calls(),
            [{"tool": "opencode", "args": ["--version"]}],
        )

    def test_fresh_hydration_uses_safe_flags_and_preserves_metadata(self) -> None:
        manifest_raw, lockfile_raw = self.track_metadata()
        result = self.fixture.run_guard()
        self.assertEqual(result.returncode, 0, result.stderr)
        npm_calls = [call for call in self.fixture.calls() if call["tool"] == "npm"]
        self.assertEqual(
            npm_calls,
            [
                {
                    "tool": "npm",
                    "args": ["ci", "--ignore-scripts", "--no-audit", "--no-fund"],
                }
            ],
        )
        self.assertEqual(
            (self.fixture.repo / ".opencode/package.json").read_bytes(),
            manifest_raw,
        )
        self.assertEqual(
            (self.fixture.repo / ".opencode/package-lock.json").read_bytes(),
            lockfile_raw,
        )
        self.assertTrue(
            (self.fixture.repo / ".opencode/node_modules/.opencode-project-deps-guard.sha256").is_file()
        )

    def test_fingerprint_fast_path_skips_second_npm_run(self) -> None:
        self.track_metadata()
        first = self.fixture.run_guard()
        second = self.fixture.run_guard()
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(
            [call["tool"] for call in self.fixture.calls()],
            ["opencode", "npm", "opencode"],
        )

    def test_stale_installed_version_is_rehydrated(self) -> None:
        self.track_metadata()
        installed = self.fixture.repo / ".opencode/node_modules/@opencode-ai/plugin/package.json"
        installed.parent.mkdir(parents=True)
        installed.write_text('{"version":"1.15.6"}\n')
        result = self.fixture.run_guard()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(installed.read_text())["version"], "1.18.15")
        self.assertEqual([call["tool"] for call in self.fixture.calls()], ["opencode", "npm"])

    def test_npm_failure_or_wrong_installed_version_blocks(self) -> None:
        self.track_metadata()
        failed = self.fixture.run_guard(env_updates={"GUARD_TEST_NPM_FAIL": "1"})
        self.assertNotEqual(failed.returncode, 0)
        self.assertIn("fake npm failure", failed.stderr)

        self.fixture.call_log.unlink()
        wrong = self.fixture.run_guard(
            env_updates={"GUARD_TEST_INSTALLED_VERSION": "1.18.14"}
        )
        self.assertNotEqual(wrong.returncode, 0)
        self.assertIn("expected 1.18.15", wrong.stderr)

    def test_npm_metadata_mutation_is_restored_and_blocked(self) -> None:
        manifest_raw, lockfile_raw = self.track_metadata()
        result = self.fixture.run_guard(env_updates={"GUARD_TEST_NPM_MUTATE": "1"})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("original bytes were restored", result.stderr)
        self.assertEqual(
            (self.fixture.repo / ".opencode/package.json").read_bytes(),
            manifest_raw,
        )
        self.assertEqual(
            (self.fixture.repo / ".opencode/package-lock.json").read_bytes(),
            lockfile_raw,
        )


class OpenCodePlannotatorWrapperTests(unittest.TestCase):
    def render_wrapper(self, source: Path, destination: Path) -> None:
        rendered = source.read_text(encoding="utf-8")
        replacements = {
            "{{ .plannotator_ports.host.build }}": "8993-8998",
            "{{ .plannotator_ports.host.custom }}": "9004-9009",
            "{{ .plannotator_ports.devcontainer.build }}": "9993-9998",
            "{{ .plannotator_ports.devcontainer.custom }}": "10004-10009",
        }
        for template, value in replacements.items():
            rendered = rendered.replace(template, value)
        write_executable(destination, rendered)

    def test_wrappers_block_exec_on_guard_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            opencode = root / "opencode"
            marker = root / "opencode-ran"
            write_executable(opencode, f"#!/bin/sh\ntouch '{marker}'\n")
            write_executable(root / "opencode-project-deps-guard", "#!/bin/sh\nexit 23\n")
            wrapper = root / "opencode-plannotator"
            self.render_wrapper(WRAPPERS[0], wrapper)
            env = os.environ.copy()
            env["OPENCODE_BIN"] = str(opencode)
            result = subprocess.run(
                [str(wrapper), "--agent", "build"],
                env=env,
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            self.assertEqual(result.returncode, 23)
            self.assertFalse(marker.exists())

    def test_wrappers_forward_selected_binary_cwd_and_arguments(self) -> None:
        for source in WRAPPERS:
            with self.subTest(wrapper=source.name), tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                guard_log = root / "guard.json"
                opencode_log = root / "opencode.json"
                opencode = root / "selected-opencode"
                write_executable(
                    opencode,
                    "#!/usr/bin/env python3\n"
                    "import json, os, pathlib, sys\n"
                    f"pathlib.Path({str(opencode_log)!r}).write_text(json.dumps({{'args': sys.argv[1:], 'pool': os.environ.get('OPENCODE_PLANNOTATOR_POOL')}}))\n",
                )
                write_executable(
                    root / "opencode-project-deps-guard",
                    "#!/usr/bin/env python3\n"
                    "import json, pathlib, sys\n"
                    f"pathlib.Path({str(guard_log)!r}).write_text(json.dumps(sys.argv[1:]))\n",
                )
                wrapper_name = source.name.removeprefix("executable_").removesuffix(".tmpl")
                wrapper = root / wrapper_name
                self.render_wrapper(source, wrapper)
                env = os.environ.copy()
                env["OPENCODE_BIN"] = str(opencode)
                result = subprocess.run(
                    [str(wrapper), "--agent", "build", "two words"],
                    cwd=root,
                    env=env,
                    check=False,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(
                    json.loads(guard_log.read_text()),
                    ["--opencode-bin", str(opencode), "--cwd", os.path.realpath(root)],
                )
                launched = json.loads(opencode_log.read_text())
                self.assertEqual(launched["args"], ["--agent", "build", "two words"])
                expected_pool = "custom" if wrapper_name.endswith("-custom") else "build"
                self.assertEqual(launched["pool"], expected_pool)

    def test_dry_run_exits_before_guard_resolution(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            wrapper = root / "opencode-plannotator"
            self.render_wrapper(WRAPPERS[0], wrapper)
            env = os.environ.copy()
            env["OPENCODE_PLANNOTATOR_DRY_RUN"] = "1"
            env["OPENCODE_BIN"] = str(root / "does-not-exist")
            result = subprocess.run(
                [str(wrapper)],
                env=env,
                check=False,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("profile=build", result.stderr)


if __name__ == "__main__":
    unittest.main()
