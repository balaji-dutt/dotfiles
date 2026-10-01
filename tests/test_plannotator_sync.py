from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import os
import sys
import unittest
import urllib.error
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from tests.support.fixtures import isolated_environment


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "assets" / "sync-plannotator-assets.py"
SCHEMA = "./schemas/plannotator-assets.v1.schema.json"
BASE_URL = "https://raw.githubusercontent.com/backnotprop/plannotator/v1.2.3/"


@unittest.skipIf(os.name == "nt", "POSIX fixture is required")
class PlannotatorSyncTests(unittest.TestCase):
    def setUp(self) -> None:
        context = isolated_environment(prefix="plannotator-sync-")
        self.fixture = context.__enter__()
        self.addCleanup(context.__exit__, None, None, None)
        self.repo = self.fixture.root / "repo with spaces"
        (self.repo / "configs").mkdir(parents=True)
        previous_cwd = Path.cwd()
        os.chdir(self.repo)
        self.addCleanup(os.chdir, previous_cwd)
        (self.repo / ".chezmoidata.yaml").write_text(
            "plannotator_version: 1.2.3\n", encoding="utf-8"
        )
        spec = importlib.util.spec_from_file_location("plannotator_sync", SCRIPT)
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        self.addCleanup(sys.modules.pop, spec.name, None)
        spec.loader.exec_module(module)
        self.script = module
        self.artifacts = [
            {
                "path": "dot_claude/skills/sample/SKILL.md",
                "source_path": "skills/sample/SKILL.md",
                "sha256": hashlib.sha256(b"old skill\n").hexdigest(),
            },
            {
                "path": "private_dot_config/opencode/commands/sample.md",
                "source_path": "commands/sample.md",
                "sha256": hashlib.sha256(b"old command\n").hexdigest(),
            },
        ]
        self.payloads = [b"new skill\n", b"new command\n"]
        self.manifest_file = self.repo / "configs/plannotator-assets.json"
        self.write_manifest()
        for artifact, content in zip(self.artifacts, (b"old skill\n", b"old command\n")):
            target = self.repo / artifact["path"]
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)

    def write_manifest(self, **updates: object) -> None:
        manifest = {"$schema": SCHEMA, "schema_version": 1, "artifacts": self.artifacts}
        manifest.update(updates)
        self.manifest_file.write_text(
            json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
        )

    def run_sync(self, *args: str, payloads: list[bytes | Exception] | None = None):
        requests: list[tuple[str, int, str]] = []
        responses = iter(self.payloads if payloads is None else payloads)

        def fetch(request, timeout):
            requests.append((request.full_url, timeout, request.get_header("User-agent")))
            response = next(responses)
            if isinstance(response, Exception):
                raise response
            return io.BytesIO(response)

        with patch.object(self.script.urllib.request, "urlopen", side_effect=fetch):
            stdout, stderr = io.StringIO(), io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                result = self.script.main(list(args))
        return result, stdout.getvalue(), stderr.getvalue(), requests

    def test_check_reports_drift_and_missing_artifact_without_writing(self) -> None:
        before = self.manifest_file.read_bytes()
        result, _, errors, requests = self.run_sync("--check")
        self.assertEqual(result, 1)
        self.assertIn("artifact drift", errors)
        self.assertIn("--- local/", errors)
        self.assertEqual([url for url, _, _ in requests], [
            BASE_URL + "skills/sample/SKILL.md",
            BASE_URL + "commands/sample.md",
        ])
        self.assertEqual([timeout for _, timeout, _ in requests], [60, 60])
        self.assertEqual([agent for _, _, agent in requests], [
            "dotfiles-sync-plannotator-assets"
        ] * 2)
        self.assertEqual(self.manifest_file.read_bytes(), before)

        (self.repo / self.artifacts[0]["path"]).unlink()
        result, _, errors, _ = self.run_sync("--check")
        self.assertEqual(result, 1)
        self.assertIn("missing artifact: " + self.artifacts[0]["path"], errors)

    def test_write_updates_hashes_and_repeat_check_is_clean(self) -> None:
        result, output, errors, _ = self.run_sync("--write")
        self.assertEqual(result, 0, errors)
        self.assertIn("updated configs/plannotator-assets.json", output)
        manifest = json.loads(self.manifest_file.read_text(encoding="utf-8"))
        for artifact, content in zip(manifest["artifacts"], self.payloads):
            self.assertEqual((self.repo / artifact["path"]).read_bytes(), content)
            self.assertEqual(artifact["sha256"], hashlib.sha256(content).hexdigest())
        snapshot = self.manifest_file.read_bytes()
        result, output, errors, _ = self.run_sync("--check")
        self.assertEqual(result, 0, errors)
        self.assertIn("match v1.2.3", output)
        self.assertEqual(self.run_sync("--write")[0], 0)
        self.assertEqual(self.manifest_file.read_bytes(), snapshot)

    def test_opencode_commands_keep_frontmatter_only(self) -> None:
        target = "private_dot_config/opencode/commands/plannotator-last.md"
        self.artifacts[1]["path"] = target
        self.payloads[1] = b"---\ndescription: Annotate last response\n---\n\nRun something else\n"
        self.write_manifest()
        result, _, errors, _ = self.run_sync("--write")
        self.assertEqual(result, 0, errors)
        self.assertEqual((self.repo / target).read_bytes(), b"---\ndescription: Annotate last response\n---\n\n")
        self.assertEqual(self.run_sync("--check")[0], 0)

    def test_invalid_manifest_version_and_paths_do_not_fetch(self) -> None:
        for updates, data, expected in (
            ({"$schema": "wrong"}, None, "manifest $schema"),
            ({"schema_version": 2}, None, "manifest schema_version"),
            ({"artifacts": []}, "other: 1\n", "plannotator_version not found"),
            ({"artifacts": [{"path": "../outside", "source_path": "skills/a"}]}, None, "artifact path must stay repo-relative"),
            ({"artifacts": [{"path": self.artifacts[0]["path"], "source_path": "../outside"}]}, None, "source path must stay repo-relative"),
            ({"artifacts": [{"path": "unmanaged/file", "source_path": "skills/a"}]}, None, "artifact path must start"),
        ):
            with self.subTest(updates=updates, data=data):
                self.write_manifest(**updates)
                (self.repo / ".chezmoidata.yaml").write_text(
                    data if data is not None else "plannotator_version: 1.2.3\n",
                    encoding="utf-8",
                )
                result, _, errors, requests = self.run_sync("--write")
                self.assertEqual(result, 1)
                self.assertIn(expected, errors)
                self.assertEqual(requests, [])
        self.manifest_file.write_text("{invalid", encoding="utf-8")
        result, _, errors, requests = self.run_sync("--check")
        self.assertEqual(result, 1)
        self.assertIn("ERROR:", errors)
        self.assertEqual(requests, [])

    def test_missing_inputs_fail_before_fetching(self) -> None:
        for file_path in (self.manifest_file, self.repo / ".chezmoidata.yaml"):
            with self.subTest(file_path=file_path):
                original = file_path.read_bytes()
                file_path.unlink()
                try:
                    result, _, errors, requests = self.run_sync("--write")
                    self.assertEqual(result, 1)
                    self.assertIn("ERROR:", errors)
                    self.assertEqual(requests, [])
                finally:
                    file_path.write_bytes(original)

    def test_missing_remote_and_failed_download_leave_files_unchanged(self) -> None:
        before = self.manifest_file.read_bytes()
        targets = [(self.repo / item["path"]).read_bytes() for item in self.artifacts]
        with patch.object(
            self.script.urllib.request,
            "urlopen",
            side_effect=urllib.error.URLError("offline fixture"),
        ):
            stderr = io.StringIO()
            with redirect_stderr(stderr):
                self.assertEqual(self.script.main(["--write"]), 1)
            self.assertIn("failed to download", stderr.getvalue())
        self.assertEqual(self.manifest_file.read_bytes(), before)
        self.assertEqual(
            [(self.repo / item["path"]).read_bytes() for item in self.artifacts], targets
        )
        result, _, errors, _ = self.run_sync(
            "--write", payloads=[self.payloads[0], urllib.error.URLError("second fetch offline")]
        )
        self.assertEqual(result, 1)
        self.assertIn("failed to download", errors)
        self.assertIn("second fetch offline", errors)
        self.assertEqual(self.manifest_file.read_bytes(), before)
        self.assertEqual(
            [(self.repo / item["path"]).read_bytes() for item in self.artifacts], targets
        )

    def test_symlink_escape_and_partial_write(self) -> None:
        target = self.repo / self.artifacts[0]["path"]
        outside = self.fixture.root / "outside"
        outside.write_bytes(b"sentinel")
        target.unlink()
        target.symlink_to(outside)
        result, _, errors, _ = self.run_sync("--write")
        self.assertEqual(result, 1)
        self.assertIn("artifact path escapes repo root", errors)
        self.assertEqual(outside.read_bytes(), b"sentinel")

        target.unlink()
        target.write_bytes(b"old skill\n")
        before = self.manifest_file.read_bytes()
        original_write = Path.write_bytes
        second = (self.repo / self.artifacts[1]["path"]).resolve()

        def write_or_fail(path: Path, data: bytes):
            if path == second:
                raise OSError("fixture disk full")
            return original_write(path, data)

        with patch.object(Path, "write_bytes", write_or_fail):
            result, _, errors, _ = self.run_sync("--write")
        self.assertEqual(result, 1)
        self.assertIn("fixture disk full", errors)
        self.assertEqual(target.read_bytes(), self.payloads[0])
        self.assertEqual(second.read_bytes(), b"old command\n")
        self.assertEqual(self.manifest_file.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
