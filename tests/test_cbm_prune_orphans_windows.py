from __future__ import annotations

import contextlib
import io
import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.support.chezmoi_render import CHEZMOI, render_template
from tests.support.fixtures import isolated_environment, load_script_module
from tests.support.powershell import require_fixture_powershell


REPO_ROOT = Path(__file__).resolve().parents[1]
HELPER = REPO_ROOT / "assets/cbm-prune-orphans.py"
HOOK = ".chezmoiscripts/run_after_zz-prune-codebase-memory-mcp.ps1.tmpl"


@unittest.skipUnless(sys.platform == "win32", "native Windows paths and processes required")
class WindowsCbmPruneOrphansTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = load_script_module(HELPER, "cbm_prune_windows")
        self.fixture = self.enterContext(isolated_environment(prefix="cbm-prune-win-"))
        self.root = self.fixture.root
        self.projects = []
        self.calls = []
        self.failure = None

    def add(self, name: str, root: Path) -> None:
        self.projects.append({"name": name, "root_path": str(root)})

    def fake_run(self, argv, **kwargs):
        self.calls.append(argv)
        self.assertEqual(argv[1:3], ["cli", "--json"])
        tool = argv[3]
        if tool == self.failure:
            return subprocess.CompletedProcess(argv, 1, "", "fixture failure")
        flags = dict(zip(argv[4::2], argv[5::2]))
        if tool == "list_projects":
            offset, limit = int(flags["--offset"]), int(flags["--limit"])
            more = offset + limit < len(self.projects)
            content = {"projects": self.projects[offset:offset + limit], "has_more": more,
                       "next_offset": offset + limit if more else None}
        else:
            self.assertEqual(tool, "delete_project")
            self.projects = [p for p in self.projects if p["name"] != flags["--project"]]
            content = {"status": "deleted"}
        return subprocess.CompletedProcess(argv, 0, json.dumps({"structuredContent": content}), "")

    def run_main(self, *args, active=False):
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.object(self.module.subprocess, "run", side_effect=self.fake_run), \
                patch.object(self.module, "sessions_active", return_value=active), \
                contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = self.module.main(["--cbm", "fixture-cbm.exe", *args])
        return code, stdout.getvalue(), stderr.getvalue()

    def test_dry_run_and_apply_preserve_live_and_case_insensitive_keep(self) -> None:
        (self.root / "live").mkdir()
        self.add("live", self.root / "live")
        self.add("gone", self.root / "gone")
        self.add("protected", self.root / "protected")
        kept = str(self.root / "protected").swapcase().replace("\\", "/")
        code, output, error = self.run_main("--keep", kept)
        self.assertEqual(code, 0, error)
        self.assertIn("would delete gone", output)
        self.assertFalse(any(call[3] == "delete_project" for call in self.calls))
        self.assertEqual(len(self.projects), 3)
        code, output, error = self.run_main("--apply", "--keep", kept)
        self.assertEqual(code, 0, error)
        self.assertEqual([p["name"] for p in self.projects], ["live", "protected"])
        self.assertIn("1 of 3 indexes deleted", output)
        self.assertTrue(all(call[0] == "fixture-cbm.exe" for call in self.calls))

    def test_empty_and_missing_parents_are_kept(self) -> None:
        (self.root / "empty").mkdir()
        for root in (self.root / "empty" / "gone", self.root / "missing" / "gone"):
            with self.subTest(root=root):
                verdict = self.module.classify(self.module.Project("kept", str(root)), set())
                self.assertFalse(verdict.delete)
                self.assertTrue(verdict.report)

    def test_qualified_paths_and_volume_roots_are_conservative(self) -> None:
        roots = ("", "relative/repo", "C:repo", "\\repo", "/repo", "Z:/", "Z:\\",
                 "//unavailable.invalid/share", "\\\\unavailable.invalid\\share\\")
        with patch.object(self.module.os, "lstat") as lstat, \
                patch.object(self.module.os, "scandir") as scandir:
            for root in roots:
                with self.subTest(root=root):
                    self.assertFalse(self.module.classify(self.module.Project("kept", root), set()).delete)
            lstat.assert_not_called()
            scandir.assert_not_called()

    def test_unavailable_mapped_and_unc_parents_are_kept_without_network_access(self) -> None:
        for root in ("Z:/unavailable/repo", "//unavailable.invalid/share/parent/repo"):
            for error in (FileNotFoundError(), PermissionError(13, "denied"), OSError(123, "offline")):
                with self.subTest(root=root, error=error), \
                        patch.object(self.module.os, "lstat", side_effect=FileNotFoundError()), \
                        patch.object(self.module, "normalize", return_value=root), \
                        patch.object(self.module.os, "scandir", side_effect=error):
                    verdict = self.module.classify(self.module.Project("kept", root), set())
                    self.assertFalse(verdict.delete)
                    self.assertTrue(verdict.report)

    def test_root_access_errors_are_kept(self) -> None:
        with patch.object(self.module.os, "lstat", side_effect=PermissionError(13, "denied")):
            verdict = self.module.classify(self.module.Project("kept", str(self.root / "gone")), set())
        self.assertFalse(verdict.delete)
        self.assertIn("cannot be checked", verdict.reason)

    def test_managed_binary_preference_fallback_and_missing_skip(self) -> None:
        managed = str(self.root / ".local" / "codebase-memory-mcp.exe")
        fallback = str(self.root / "path" / "codebase-memory-mcp.exe")
        with patch.object(self.module.Path, "home", return_value=self.root), \
                patch.object(self.module.shutil, "which", return_value=fallback) as which, \
                patch.object(self.module, "is_native_candidate", side_effect=lambda p, w: p == managed):
            self.assertEqual(self.module.resolve_cbm(), managed)
            which.assert_not_called()
        with patch.object(self.module.Path, "home", return_value=self.root), \
                patch.object(self.module.shutil, "which", return_value=fallback), \
                patch.object(self.module, "is_native_candidate", side_effect=lambda p, w: p == fallback):
            self.assertEqual(self.module.resolve_cbm(), fallback)
        with patch.object(self.module, "resolve_cbm", return_value=None), \
                patch.object(self.module, "run_tool") as tool, contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(self.module.main([]), 0)
            self.assertIn("not available; skipping", out.getvalue())
            tool.assert_not_called()

    def test_tasklist_requires_exact_image_and_successful_structured_response(self) -> None:
        cases = [
            ('"CODEBASE-MEMORY-MCP.EXE","123","Console","1","12,000 K"', 0, True),
            ('"other-codebase-memory-mcp.exe","123","Console","1","12 K"', 0, False),
            ('"codebase-memory-mcp.exe","bad","Console","1","12 K"', 0, False),
            ("INFO: No tasks are running which match the specified criteria.", 0, False),
            ("codebase-memory-mcp.exe", 0, False),
            ('"codebase-memory-mcp.exe","123","Console","1","12 K"', 1, False),
            ('"unterminated', 0, False),
        ]
        for output, code, expected in cases:
            with self.subTest(output=output, code=code), \
                    patch.object(self.module.shutil, "which", return_value="tasklist.exe"), \
                    patch.object(self.module.subprocess, "run", return_value=
                                 subprocess.CompletedProcess([], code, output, "")) as run:
                self.assertEqual(self.module.sessions_active(), expected)
                self.assertEqual(run.call_args.args[0], ["tasklist.exe", "/FI",
                                 "IMAGENAME eq codebase-memory-mcp.exe", "/FO", "CSV", "/NH"])
                self.assertEqual(run.call_args.kwargs["timeout"], self.module.LOOKUP_TIMEOUT_SECONDS)
                self.assertEqual(run.call_args.kwargs["errors"], "replace")
        for error in (OSError("missing"), subprocess.TimeoutExpired("tasklist", 30)):
            with patch.object(self.module.shutil, "which", return_value="tasklist.exe"), \
                    patch.object(self.module.subprocess, "run", side_effect=error):
                self.assertFalse(self.module.sessions_active())
        with patch.object(self.module.shutil, "which", return_value=None):
            self.assertFalse(self.module.sessions_active())

    def test_tasklist_localized_bytes_do_not_raise_decoding_errors(self) -> None:
        original_run = subprocess.run

        def localized_probe(argv, **kwargs):
            return original_run([sys.executable, "-I", "-c",
                                 "import sys; sys.stdout.buffer.write(b'INFO: t\\xe2che')"],
                                **kwargs)

        with patch.object(self.module.shutil, "which", return_value="tasklist.exe"), \
                patch.object(self.module.subprocess, "run", side_effect=localized_probe):
            self.assertFalse(self.module.sessions_active())

    def test_list_and_delete_failures_stop_mutations_with_or_without_sessions(self) -> None:
        (self.root / "sibling").mkdir()
        self.add("first", self.root / "first")
        self.add("second", self.root / "second")
        for operation in ("list_projects", "delete_project"):
            for active in (False, True):
                with self.subTest(operation=operation, active=active):
                    self.failure, self.calls = operation, []
                    code, output, error = self.run_main("--apply", active=active)
                    self.assertEqual(code, 0 if active else 1)
                    self.assertIn("WARNING:" if active else "ERROR:", error)
                    self.assertIn("fixture failure", error)
                    self.assertNotIn("indexes deleted", output)
                    self.assertEqual(sum(c[3] == "delete_project" for c in self.calls),
                                     1 if operation == "delete_project" else 0)
                    self.assertEqual(len(self.projects), 2)

    def test_pagination_finishes_before_deletion(self) -> None:
        (self.root / "live").mkdir()
        self.projects = [{"name": f"live-{i}", "root_path": str(self.root / "live")} for i in range(1001)]
        self.add("gone", self.root / "gone")
        code, output, error = self.run_main("--apply")
        self.assertEqual(code, 0, error)
        self.assertEqual([call[3] for call in self.calls], ["list_projects"] * 3 + ["delete_project"])
        self.assertEqual([call[-1] for call in self.calls[:3]], ["0", "500", "1000"])
        self.assertIn("1 of 1002 indexes deleted", output)

    def test_native_timeout_reaps_child_before_session_probe(self) -> None:
        original_run, original_popen = subprocess.run, subprocess.Popen
        children = []

        def spawn(*args, **kwargs):
            child = original_popen(*args, **kwargs)
            children.append(child)
            return child

        def hung_cli(argv, **kwargs):
            return original_run([sys.executable, "-I", "-c", "import time; time.sleep(30)"], **kwargs)

        def probe():
            self.assertTrue(children)
            self.assertIsNotNone(children[-1].poll())
            return active

        for active in (False, True):
            with self.subTest(active=active), \
                    patch.object(self.module.subprocess, "run", side_effect=hung_cli), \
                    patch.object(self.module.subprocess, "Popen", side_effect=spawn), \
                    patch.object(self.module, "CLI_TIMEOUT_SECONDS", 0.2), \
                    patch.object(self.module, "sessions_active", side_effect=probe), \
                    contextlib.redirect_stderr(io.StringIO()) as error:
                self.assertEqual(self.module.main(["--cbm", "fixture.exe"]), 0 if active else 1)
                self.assertIn("did not finish within", error.getvalue())


@unittest.skipUnless(CHEZMOI, "chezmoi is required")
class WindowsCbmPruneHookTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.backend = require_fixture_powershell()

    def run_hook(self, candidates, *, helper_exit=0, helper_warning=False):
        with isolated_environment(prefix="cbm-prune-ps-") as fixture:
            source = fixture.root / "source's dir"
            working = fixture.root / "working's tree"
            source.mkdir()
            working.mkdir()
            mapped_source = self.backend.path(fixture.root, source)
            mapped_working = self.backend.path(fixture.root, working)

            def configure(data):
                data["chezmoi"]["sourceDir"] = mapped_source
                data["chezmoi"]["workingTree"] = mapped_working

            rendered = fixture.root / "prune.ps1"
            rendered.write_text(render_template(HOOK, "windows", configure, environment=fixture.env), encoding="utf-8")
            command = """
              $global:Candidates = ConvertFrom-Json -AsHashtable -InputObject 'CANDIDATES';
              function Get-Command {
                param($Name, $ErrorAction)
                [Console]::Out.WriteLine('lookup:' + $Name)
                if ($global:Candidates.ContainsKey($Name)) {
                  [pscustomobject]@{ Source = 'Invoke-FixturePython'; Name = $Name }
                  $global:Current = $Name
                }
              }
              function Invoke-FixturePython {
                if ($args -contains '-c') {
                  [Console]::Out.WriteLine('probe:' + (ConvertTo-Json -Compress -InputObject @($args)))
                  if ($global:Candidates[$global:Current] -eq -1) { throw 'unusable alias' }
                  $global:LASTEXITCODE = $global:Candidates[$global:Current]
                } else {
                  [Console]::Out.WriteLine('helper:' + (ConvertTo-Json -Compress -InputObject @($args)))
                  if (HELPER_WARNING) { Write-Warning 'fixture contention skip' }
                  $global:LASTEXITCODE = HELPER_EXIT
                }
              }
              & SCRIPT
            """.replace("CANDIDATES", json.dumps(candidates)).replace("HELPER_EXIT", str(helper_exit))
            command = command.replace("HELPER_WARNING", "$true" if helper_warning else "$false")
            command = command.replace("SCRIPT", self.backend.quote(fixture.root, rendered))
            result = self.backend.run(fixture.root, command=command, environ=fixture.env,
                                      env_keys=("HOME", "USERPROFILE"), path_keys=("HOME", "USERPROFILE"))
            expected = ["-I", mapped_source + "/assets/cbm-prune-orphans.py", "--apply", "--keep", mapped_working]
            return result, expected

    def test_dispatch_arguments_and_interpreter_preference(self) -> None:
        result, expected = self.run_hook({"python3": 0, "python": 0, "py": 0})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("lookup:python", result.stdout.splitlines())
        helper = next(line.removeprefix("helper:") for line in result.stdout.splitlines() if line.startswith("helper:"))
        actual = json.loads(helper)
        self.assertEqual([value.replace("\\", "/") for value in actual],
                         [value.replace("\\", "/") for value in expected])
        self.assertIn("sys.version_info >= (3, 9)", result.stdout)

    def test_alias_and_old_python_fall_back_to_launcher(self) -> None:
        result, _ = self.run_hook({"python3": -1, "python": 1, "py": 0})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        for name in ("python3", "python", "py"):
            self.assertIn("lookup:" + name, result.stdout)
        helper = next(line[7:] for line in result.stdout.splitlines() if line.startswith("helper:"))
        self.assertEqual(json.loads(helper)[:2], ["-3", "-I"])

    def test_missing_or_unusable_interpreters_warn_and_skip(self) -> None:
        for candidates in ({}, {"python3": 1, "python": -1, "py": 1}):
            with self.subTest(candidates=candidates):
                result, _ = self.run_hook(candidates)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("Python 3.9 or newer is unavailable", result.stdout)
                self.assertNotIn("helper:", result.stdout)

    def test_helper_failure_propagates_and_contention_skip_succeeds(self) -> None:
        failed, _ = self.run_hook({"python": 0}, helper_exit=7)
        self.assertNotEqual(failed.returncode, 0)
        self.assertIn("CBM index pruning failed with exit code 7", failed.stdout + failed.stderr)
        skipped, _ = self.run_hook({"python": 0}, helper_warning=True)
        self.assertEqual(skipped.returncode, 0, skipped.stderr)
        self.assertIn("fixture contention skip", skipped.stdout)


if __name__ == "__main__":
    unittest.main()
