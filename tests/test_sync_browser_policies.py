from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import json
import shutil
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
SYNC_SCRIPT = REPO_ROOT / "assets" / "sync-browser-policies.py"


class BrowserPolicyFixture:
    VERSION = "v1.2.3"
    ARTIFACTS = {
        "chrome/install.reg": b"Windows Registry Editor Version 5.00\n",
        "firefox/firefox.mobileconfig": (
            b"\xff\xfe<\x00?\x00x\x00m\x00l\x00 \x00v\x00e\x00r\x00s\x00i\x00o\x00n\x00"
            b"=\x00\"\x001\x00.\x000\x00\"\x00?\x00>\x00\n\x00"
        ),
    }

    def __init__(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temporary.name)
        self.root = self.workspace / "repo with spaces"
        script = self.root / "assets" / SYNC_SCRIPT.name
        script.parent.mkdir(parents=True)
        shutil.copy2(SYNC_SCRIPT, script)

        module_name = f"sync_browser_policies_{uuid.uuid4().hex}"
        spec = importlib.util.spec_from_file_location(module_name, script)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"could not import {script}")
        self.module_name = module_name
        self.module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = self.module
        spec.loader.exec_module(self.module)

        self.policy_dir = self.root / self.module.POLICY_DIR
        self.manifest_path = self.root / self.module.MANIFEST_PATH
        self.write_manifest(self.ARTIFACTS)
        self.write_artifacts(self.ARTIFACTS)

    def cleanup(self) -> None:
        sys.modules.pop(self.module_name, None)
        self.temporary.cleanup()

    def manifest(self, artifacts: dict[str, bytes]) -> dict[str, object]:
        return {
            "$schema": self.module.SCHEMA_REF,
            "schema_version": 1,
            "name": "justthebrowser",
            "upstream": {
                "repo": self.module.REPO,
                "version": self.VERSION,
            },
            "artifacts": [
                {
                    "path": artifact_path,
                    "source_path": artifact_path,
                    "sha256": hashlib.sha256(data).hexdigest(),
                }
                for artifact_path, data in artifacts.items()
            ],
        }

    def write_manifest(self, artifacts: dict[str, bytes]) -> None:
        self.write_manifest_dict(self.manifest(artifacts))

    def write_manifest_dict(self, manifest: object) -> None:
        self.manifest_path.parent.mkdir(parents=True, exist_ok=True)
        self.manifest_path.write_text(
            json.dumps(manifest, indent=2) + "\n",
            encoding="utf-8",
        )

    def write_artifacts(self, artifacts: dict[str, bytes]) -> None:
        for artifact_path, data in artifacts.items():
            target = self.policy_dir / artifact_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)

    def run_main(
        self,
        argv: list[str],
        upstream: dict[str, bytes] | None = None,
        *,
        fetch_side_effect: object | None = None,
    ) -> tuple[int, str, str]:
        upstream = self.ARTIFACTS if upstream is None else upstream

        def fetch(url: str) -> bytes:
            relative = url.removeprefix(
                self.module.expected_source_base_url(self.VERSION)
            )
            if relative not in upstream:
                raise RuntimeError(f"unexpected download: {url}")
            return upstream[relative]

        output = io.StringIO()
        errors = io.StringIO()
        effect = fetch_side_effect or fetch
        with (
            mock.patch.object(self.module.Path, "cwd", return_value=self.root),
            mock.patch.object(self.module, "fetch_bytes", side_effect=effect),
            contextlib.redirect_stdout(output),
            contextlib.redirect_stderr(errors),
        ):
            return_code = self.module.main(argv)
        return return_code, output.getvalue(), errors.getvalue()

    def snapshot(self) -> dict[str, bytes]:
        return {
            path.relative_to(self.root).as_posix(): path.read_bytes()
            for path in sorted(self.root.rglob("*"))
            if path.is_file()
        }


class BrowserPolicySyncTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = BrowserPolicyFixture()

    def tearDown(self) -> None:
        self.fixture.cleanup()

    def test_helpers_validate_paths_manifests_and_text_encodings(self) -> None:
        module = self.fixture.module
        self.assertEqual(module.sha256_bytes(b"policy"), hashlib.sha256(b"policy").hexdigest())
        self.assertEqual(
            module.expected_source_base_url("v9"),
            "https://raw.githubusercontent.com/corbindavenport/just-the-browser/v9/",
        )
        self.assertEqual(module.normalize_artifact_path("chrome/install.reg"), "chrome/install.reg")
        for unsafe_path in ("/absolute.reg", "../escape.reg", "chrome/../../escape.reg"):
            with self.subTest(path=unsafe_path), self.assertRaises(ValueError):
                module.normalize_artifact_path(unsafe_path)

        utf16_lines = module.decode_for_diff("policy\n".encode("utf-16"))
        self.assertEqual(utf16_lines, ["policy\n"])
        local = "".join(f"old-{index}\n" for index in range(80)).encode()
        upstream = "".join(f"new-{index}\n" for index in range(80)).encode()
        preview = module.diff_preview("policy.reg", local, upstream)
        self.assertEqual(len(preview), module.DIFF_LINE_LIMIT + 1)
        self.assertIn("more diff lines hidden", preview[-1])

        invalid_manifests = (
            [],
            {"$schema": "wrong", "schema_version": 1},
            {"$schema": module.SCHEMA_REF, "schema_version": 2},
        )
        for manifest in invalid_manifests:
            with self.subTest(manifest=manifest):
                self.fixture.manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
                with self.assertRaises(ValueError):
                    module.read_manifest(self.fixture.root)

    def test_check_is_an_offline_noop_when_bytes_and_hashes_match(self) -> None:
        before = self.fixture.snapshot()
        return_code, output, errors = self.fixture.run_main(["--check"])
        self.assertEqual(return_code, 0, errors)
        self.assertIn(f"match {self.fixture.VERSION}", output)
        self.assertEqual(errors, "")
        self.assertEqual(self.fixture.snapshot(), before)

    def test_check_reports_missing_checksum_and_upstream_drift(self) -> None:
        manifest = self.fixture.manifest(self.fixture.ARTIFACTS)
        manifest["artifacts"][0]["sha256"] = "0" * 64
        self.fixture.write_manifest_dict(manifest)
        first_path = next(iter(self.fixture.ARTIFACTS))
        (self.fixture.policy_dir / first_path).write_bytes(b"local drift\n")
        second_path = list(self.fixture.ARTIFACTS)[1]
        (self.fixture.policy_dir / second_path).unlink()

        return_code, output, errors = self.fixture.run_main(["--check"])
        self.assertEqual(return_code, 1)
        self.assertEqual(output, "")
        self.assertIn("ERROR: Just the Browser policy artifacts are out of sync", errors)
        self.assertIn(f"sha256 drift for {first_path}", errors)
        self.assertIn(f"artifact drift for {first_path}", errors)
        self.assertIn(f"  --- local/{first_path}", errors)
        self.assertIn(f"manifest hash for {first_path} does not match upstream", errors)
        self.assertIn(f"missing artifact: {second_path}", errors)
        self.assertIn("--write", errors)

    def test_default_and_explicit_write_update_deterministically(self) -> None:
        upstream = {
            artifact_path: data + b"updated\n"
            for artifact_path, data in self.fixture.ARTIFACTS.items()
        }
        return_code, output, errors = self.fixture.run_main([], upstream)
        self.assertEqual(return_code, 0, errors)
        self.assertEqual(errors, "")
        self.assertIn("synced chrome/install.reg", output)
        self.assertIn(f"updated {self.fixture.module.MANIFEST_PATH}", output)
        for artifact_path, data in upstream.items():
            self.assertEqual((self.fixture.policy_dir / artifact_path).read_bytes(), data)

        manifest = json.loads(self.fixture.manifest_path.read_text(encoding="utf-8"))
        hashes = {item["path"]: item["sha256"] for item in manifest["artifacts"]}
        self.assertEqual(
            hashes,
            {
                artifact_path: hashlib.sha256(data).hexdigest()
                for artifact_path, data in upstream.items()
            },
        )
        first_write = self.fixture.snapshot()
        return_code, _, errors = self.fixture.run_main(["--write"], upstream)
        self.assertEqual(return_code, 0, errors)
        self.assertEqual(self.fixture.snapshot(), first_write)

    def test_download_failure_leaves_all_vendored_files_unchanged(self) -> None:
        before = self.fixture.snapshot()
        calls = 0

        def fail_second_download(url: str) -> bytes:
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError(f"failed to download {url}: offline fixture")
            return b"replacement that must stay temporary\n"

        return_code, output, errors = self.fixture.run_main(
            ["--write"], fetch_side_effect=fail_second_download
        )
        self.assertEqual(return_code, 1)
        self.assertEqual(output, "")
        self.assertIn("ERROR: failed to download", errors)
        self.assertEqual(self.fixture.snapshot(), before)

    def test_path_traversal_is_rejected_before_any_target_write(self) -> None:
        manifest = self.fixture.manifest(self.fixture.ARTIFACTS)
        manifest["artifacts"][0]["path"] = "../escaped.reg"
        self.fixture.write_manifest_dict(manifest)
        before = self.fixture.snapshot()

        return_code, output, errors = self.fixture.run_main(["--write"])
        self.assertEqual(return_code, 1)
        self.assertEqual(output, "")
        self.assertIn("artifact path must stay inside policy dir", errors)
        self.assertEqual(self.fixture.snapshot(), before)
        self.assertFalse((self.fixture.policy_dir.parent / "escaped.reg").exists())

    def test_cli_rejects_conflicting_or_unknown_modes(self) -> None:
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as conflict:
                self.fixture.module.parse_args(["--check", "--write"])
            with self.assertRaises(SystemExit) as unknown:
                self.fixture.module.parse_args(["--download"])
        self.assertEqual(conflict.exception.code, 2)
        self.assertEqual(unknown.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
