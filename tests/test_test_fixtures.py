from __future__ import annotations

import ast
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests.support.fixtures import (
    BROKEN_RESOLVERS,
    bash_argv,
    compatible_bash,
    init_git_repository,
    isolated_environment,
    load_script_module,
    read_json,
    read_json_lines,
    run_git,
    write_fake_command,
    write_json,
)


class TestFixtureTests(unittest.TestCase):
    def test_isolated_environment_uses_synthetic_home_and_path(self) -> None:
        real_home = Path.home()
        leaked = {
            "BD_CONFIG": "real-beads",
            "BEADS_DB": "real-beads-db",
            "DOLT_REMOTE_PASSWORD": "secret",
            "GIT_DIR": "/real/repository",
            "GIT_INDEX_FILE": "/real/index",
            "GIT_AUTHOR_NAME": "Real User",
            "GIT_WORK_TREE": "/real/worktree",
            "GIT_SSH_COMMAND": "unsafe-ssh",
        }
        with mock.patch.dict(os.environ, leaked), isolated_environment() as fixture:
            self.assertNotEqual(fixture.home, real_home)
            self.assertEqual(fixture.env["HOME"], str(fixture.home))
            self.assertEqual(fixture.env["USERPROFILE"], str(fixture.home))
            self.assertEqual(fixture.env["PATH"].split(os.pathsep)[0], str(fixture.fake_bin))
            self.assertTrue(Path(fixture.env["GIT_CONFIG_GLOBAL"]).is_file())
            self.assertTrue(set(leaked).isdisjoint(fixture.env))
            bd_command = shutil.which("bd", path=fixture.env["PATH"])
            if bd_command is None:
                self.fail("bd guard command was not found")
            blocked = subprocess.run(
                [bd_command, "--version"],
                env=fixture.env,
                check=False,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            self.assertEqual(blocked.returncode, 97)
            self.assertIn("blocked real bd invocation", blocked.stderr)
        self.assertFalse(fixture.root.exists())

    def test_fake_command_logs_argv_and_exit_status(self) -> None:
        with isolated_environment() as fixture:
            log = fixture.root / "calls.jsonl"
            write_fake_command(
                fixture.fake_bin,
                "fixture-command",
                log_path=log,
                exit_code=7,
                stdout="hello\n",
            )
            fixture_command = shutil.which("fixture-command", path=fixture.env["PATH"])
            if fixture_command is None:
                self.fail("fake command was not found")
            result = subprocess.run(
                [fixture_command, "two words", "literal;value"],
                cwd=fixture.root,
                env=fixture.env,
                check=False,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            self.assertEqual(result.returncode, 7)
            self.assertEqual(result.stdout, "hello\n")
            calls = read_json_lines(log)
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0]["argv"], ["two words", "literal;value"])
            self.assertTrue(Path(calls[0]["cwd"]).samefile(fixture.root))

    def test_bash_launcher_probes_paths_and_preserves_arguments(self) -> None:
        with isolated_environment() as fixture:
            source = fixture.root / "checkout with spaces ü.sh"
            source.write_text('printf "%s\\n" "$1" "$2"\n', encoding="utf-8")
            self.assertIsNone(compatible_bash(fixture.root / "absent.sh", fixture.root))
            bash = compatible_bash(source, fixture.root)
            if bash is None:
                self.skipTest("no Bash can reach the fixture")
            result = subprocess.run(bash_argv(bash, source, "two words", "ü;literal"),
                                    capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.splitlines(), ["two words", "ü;literal"])

    def test_json_helpers_are_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            document = root / "nested" / "document.json"
            log = root / "calls.jsonl"
            write_json(document, {"z": 1, "a": [2]})
            log.write_text('{"second": 2}\n\n{"first": 1}\n', encoding="utf-8")
            self.assertEqual(read_json(document), {"a": [2], "z": 1})
            self.assertEqual(
                read_json_lines(log),
                [{"second": 2}, {"first": 1}],
            )
            self.assertTrue(document.read_text(encoding="utf-8").endswith("\n"))

    def test_git_repository_has_local_identity_and_isolated_config(self) -> None:
        with isolated_environment() as fixture:
            repo = init_git_repository(fixture.root / "repo", env=fixture.env)
            (repo / "tracked.txt").write_text("fixture\n", encoding="utf-8")
            run_git(repo, "add", "tracked.txt", env=fixture.env)
            run_git(repo, "commit", "-m", "fixture", env=fixture.env)
            self.assertEqual(run_git(repo, "log", "-1", "--format=%an", env=fixture.env).stdout.strip(), "Test User")
            self.assertEqual(run_git(repo, "status", "--short", env=fixture.env).stdout, "")

    def test_broken_resolvers_include_one_that_defines_no_function(self) -> None:
        self.assertIn(":\n", BROKEN_RESOLVERS.values())



REPO_ROOT = Path(__file__).resolve().parents[1]
TEST_MODULE = re.compile(r"^tests\.test_\w+")


def imported_test_modules(source: str) -> list[str]:
    found: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if node.level:
                targets = [module] if module else [alias.name for alias in node.names]
                found.extend(f"relative:{target}" for target in targets
                             if target.split(".")[0].startswith("test_"))
                continue
            names = [module, *(f"{module}.{alias.name}" for alias in node.names)]
        else:
            continue
        found.extend(name for name in names if TEST_MODULE.match(name))
    return found


class TestPackageBoundaryTests(unittest.TestCase):
    def test_no_module_imports_a_test_module(self) -> None:
        sources = [REPO_ROOT / "tests" / "__init__.py",
                   *sorted((REPO_ROOT / "tests").glob("test_*.py")),
                   *sorted((REPO_ROOT / "tests" / "support").glob("*.py"))]
        offenders = {
            path.relative_to(REPO_ROOT).as_posix(): imported
            for path in sources
            if (imported := imported_test_modules(path.read_text(encoding="utf-8")))
        }
        self.assertEqual(offenders, {}, "move shared helpers into tests/support/")

    def test_boundary_check_sees_every_import_form(self) -> None:
        source = (
            "from tests.test_alpha import helper\n"
            "import tests.test_beta\n"
            "from tests import test_gamma\n"
            "from .test_delta import other\n"
            "def lazy():\n"
            "    from tests.test_epsilon import late\n"
            "from tests.support.fixtures import write_json\n"
        )
        self.assertEqual(
            imported_test_modules(source),
            ["tests.test_alpha", "tests.test_alpha.helper", "tests.test_beta",
             "tests.test_gamma", "relative:test_delta",
             "tests.test_epsilon", "tests.test_epsilon.late"],
        )


class DirectRunSandboxTests(unittest.TestCase):
    PROBE = (
        "import json, os, tempfile, tests\n"
        "print(json.dumps({'home': os.environ.get('HOME'),"
        " 'secret': os.environ.get('FIXTURE_SECRET_TOKEN'),"
        " 'git_global': os.environ.get('GIT_CONFIG_GLOBAL'),"
        " 'marker': os.environ.get('DOTFILES_TEST_SANDBOX'),"
        " 'guard': os.environ['PATH'].split(os.pathsep)[0],"
        " 'tmp': tempfile.gettempdir()}))\n"
    )

    def run_probe(self, hostile: Path, *, marker: bool) -> dict[str, str | None]:
        env = {key: value for key, value in os.environ.items() if key != "DOTFILES_TEST_SANDBOX"}
        env.update({
            "HOME": str(hostile / "home"),
            "USERPROFILE": str(hostile / "home"),
            "GIT_CONFIG_GLOBAL": str(hostile / "gitconfig"),
            "FIXTURE_SECRET_TOKEN": "hostile-secret",
        })
        if marker:
            env["DOTFILES_TEST_SANDBOX"] = "1"
        result = subprocess.run([sys.executable, "-c", self.PROBE], cwd=REPO_ROOT, env=env,
                                capture_output=True, text=True, timeout=60, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_direct_run_replaces_a_hostile_parent_environment(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            hostile = Path(temp_dir)
            observed = self.run_probe(hostile, marker=False)

        sandbox = Path(observed["guard"]).parent
        self.assertEqual(Path(observed["guard"]).name, "guard-bin")
        self.assertEqual(observed["marker"], "1")
        self.assertIsNone(observed["secret"])
        self.assertEqual(Path(observed["home"]), sandbox / "home")
        self.assertEqual(Path(observed["git_global"]), sandbox / "empty-gitconfig")
        self.assertEqual(Path(observed["tmp"]), sandbox / "tmp")
        self.assertFalse(sandbox.exists())

    def test_runner_marker_leaves_the_environment_alone(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            hostile = Path(temp_dir)
            observed = self.run_probe(hostile, marker=True)

            self.assertEqual(observed["home"], str(hostile / "home"))
            self.assertEqual(observed["secret"], "hostile-secret")
            self.assertEqual(observed["git_global"], str(hostile / "gitconfig"))

    def test_runner_and_package_agree_on_the_marker(self) -> None:
        import tests

        runner = load_script_module(REPO_ROOT / "assets" / "run-tests.py", "_marker_runner")
        self.assertEqual(runner.SANDBOX_MARKER, tests.SANDBOX_MARKER)

    @unittest.skipIf(os.name == "nt", "POSIX mise symlink fixture")
    def test_direct_import_resolves_node_for_outer_and_nested_sandboxes(self) -> None:
        with tempfile.TemporaryDirectory(prefix="node fixture ") as temp_dir:
            root = Path(temp_dir).resolve()
            shim_bin = root / "custom-shims"
            shim_bin.mkdir()
            host = root / "host"
            host.mkdir()
            sentinel = host / "trust-state"
            sentinel.write_text("unchanged", encoding="utf-8")
            runtime = root / "installed-node"
            runtime.write_text("#!/bin/sh\nprintf 'installed-node'\n", encoding="utf-8")
            runtime.chmod(0o755)
            mise = shim_bin / "mise"
            mise.write_text(
                f"#!{sys.executable}\nimport os, pathlib, sys\n"
                "assert sys.argv[1:] == ['which', 'node']\n"
                f"assert os.environ['HOME'] == {str(host)!r}\n"
                f"assert os.environ['XDG_STATE_HOME'] == {str(host)!r}\n"
                f"print({str(runtime)!r})\n",
                encoding="utf-8",
            )
            mise.chmod(0o755)
            (shim_bin / "node").symlink_to(mise)
            env = {key: value for key, value in os.environ.items() if key != "DOTFILES_TEST_SANDBOX"}
            env.update({"PATH": str(shim_bin), "HOME": str(host), "XDG_STATE_HOME": str(host),
                        "MISE_TRUSTED_CONFIG_PATHS": str(host), "FIXTURE_SECRET_TOKEN": "host-only"})
            probe = (
                "import os, pathlib, shutil, subprocess, tests\n"
                "from tests.support.fixtures import isolated_environment\n"
                "assert subprocess.check_output(['node'], text=True) == 'installed-node'\n"
                f"assert pathlib.Path(shutil.which('node')).resolve() == pathlib.Path({str(runtime)!r})\n"
                "assert pathlib.Path(os.environ['XDG_STATE_HOME']).is_relative_to(pathlib.Path.home())\n"
                "assert 'FIXTURE_SECRET_TOKEN' not in os.environ\n"
                "assert not any(key.startswith('MISE_') for key in os.environ)\n"
                "with isolated_environment() as fixture:\n"
                "    assert fixture.env['HOME'] != os.environ['HOME']\n"
                "    assert subprocess.check_output(['node'], env=fixture.env, text=True) == 'installed-node'\n"
                "    for command in ('bd', 'dolt'):\n"
                "        assert subprocess.run([command], env=fixture.env, capture_output=True).returncode == 97\n"
            )
            result = subprocess.run([sys.executable, "-c", probe], cwd=REPO_ROOT, env=env,
                                    capture_output=True, text=True, timeout=60, check=False)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "unchanged")


if __name__ == "__main__":
    unittest.main()
