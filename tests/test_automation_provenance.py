from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests.support.fixtures import init_git_repository, run_git, write_executable, write_json


REPO_ROOT = Path(__file__).resolve().parents[1]
CHECKER = REPO_ROOT / "assets/check-automation-provenance.py"
STATUSLINE_SYNC = REPO_ROOT / "assets/sync-statusline.sh"
CHECKER_SPEC = importlib.util.spec_from_file_location("automation_provenance", CHECKER)
assert CHECKER_SPEC is not None and CHECKER_SPEC.loader is not None
checker = importlib.util.module_from_spec(CHECKER_SPEC)
sys.modules[CHECKER_SPEC.name] = checker
CHECKER_SPEC.loader.exec_module(checker)


def digest(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def tree_digest(root: Path) -> str:
    value = hashlib.sha256()
    for path in sorted(path for path in root.rglob("*") if path.is_file()):
        name = path.relative_to(root).as_posix()
        value.update((name + "\0").encode())
        value.update(path.read_bytes())
    return "sha256:" + value.hexdigest()


class ProvenanceFixture:
    def __init__(self, root: Path) -> None:
        self.env = {
            key: value for key, value in os.environ.items() if not key.startswith("GIT_")
        }
        self.env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull)
        self.root = init_git_repository(root, env=self.env)
        self.git("config", "core.autocrlf", "false")
        self.write(".gitattributes", "*.sh text eol=lf\nunslop/** text=auto eol=lf\n")
        self.write("configs/schemas/automation-provenance.v2.schema.json", "{}\n")

        generated = b"#!/bin/sh\necho generated\n"
        self.write_bytes("generated/tool.sh", generated, executable=True)
        write_json(
            self.root / ".agentic-tooling/generated-manifest.json",
            {
                "schemaVersion": 1,
                "files": [
                    {
                        "path": "generated/tool.sh",
                        "source": "tools/tool.yaml",
                        "sourceDigest": digest(b"source"),
                        "digest": digest(generated),
                    }
                ],
            },
        )

        self.write("host/tool.sh", "#!/bin/sh\necho mirror\n", executable=True)
        self.write("container/tool.sh", "#!/bin/sh\necho mirror\n", executable=True)
        self.write(
            "configs/devcontainer-sync.jsonc",
            """{
  "$schema": "./schemas/devcontainer-sync.v1.schema.json",
  "schema_version": 1,
  // Fixture mirror authority.
  "shared": {"mirrors": [{
    "name": "fixture",
    "source_root": "host",
    "target_root": "container",
    "include": ["tool*.sh"],
    "exclude": [],
    "cleanup_managed": true,
  }]},
}
""",
        )

        self.write("configs/espanso/tool.py", "print('espanso')\n")
        self.write(
            ".chezmoitemplates/espanso/tool.py.tmpl",
            '{{- include "configs/espanso/tool.py" -}}\n',
        )
        for path in ("platform/macos/tool.py.tmpl", "platform/windows/tool.py.tmpl"):
            self.write(path, '{{- template "espanso/tool.py.tmpl" . -}}\n')

        statusline = (
            '#!/bin/sh\n# renovate: datasource=github-releases depName=example/statusline\n'
            'CLAUDE_PACE_VERSION="v1.2.3"\necho status\n'
        )
        self.write("host/statusline.sh", statusline, executable=True)
        self.write("container/statusline.sh", statusline, executable=True)
        self.write("assets/sync-statusline.sh", "#!/bin/sh\nexit 0\n", executable=True)

        for root_name in ("unslop/canonical", "unslop/copy-a", "unslop/copy-b"):
            for name in ("__init__.py", "__main__.py", "cli.py"):
                self.write(f"{root_name}/{name}", f"# {name}\n")
        self.write("docs/unslop-fork-status.md", "# Fixture snapshot\n")

        write_json(
            self.root / "configs/test-suites.json",
            {
                "$schema": "./schemas/test-suites.v1.schema.json",
                "schema_version": 1,
                "capabilities": {},
                "steps": [
                    {
                        "id": "fixture",
                        "suites": ["provenance"],
                        "argv": ["{python}", "-m", "unittest", "tests.test_fixture"],
                        "covers": ["tests/test_fixture.py"],
                    }
                ],
            },
        )
        self.write("tests/test_fixture.py", "# fixture test\n")
        write_json(
            self.root / "configs/automation-test-inventory.json",
            {
                "$schema": "./schemas/automation-test-inventory.v1.schema.json",
                "schema_version": 1,
                "candidate_digest": digest(b"fixture"),
                "entries": [
                    {
                        "id": "archived",
                        "classification": "archived",
                        "paths": ["archive/**"],
                    }
                ],
            },
        )
        self.write_policy()
        self.git("add", "--all")

    def git(self, *args: str) -> subprocess.CompletedProcess[str]:
        return run_git(self.root, *args, env=self.env)

    def write(self, relative: str, content: str, *, executable: bool = False) -> Path:
        return self.write_bytes(relative, content.encode("utf-8"), executable=executable)

    def write_bytes(self, relative: str, content: bytes, *, executable: bool = False) -> Path:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        if executable:
            path.chmod(0o755)
        return path

    def policy_payload(self) -> dict[str, object]:
        canonical = self.root / "unslop/canonical"
        return {
            "$schema": "./schemas/automation-provenance.v2.schema.json",
            "schema_version": 2,
            "generated": {
                "manifest": ".agentic-tooling/generated-manifest.json",
                "accepted_divergences": [],
            },
            "mirrors": {"manifest": "configs/devcontainer-sync.jsonc"},
            "espanso": {
                "chains": [
                    {
                        "source": "configs/espanso/tool.py",
                        "template": ".chezmoitemplates/espanso/tool.py.tmpl",
                        "renders": [
                            "platform/macos/tool.py.tmpl",
                            "platform/windows/tool.py.tmpl",
                        ],
                    }
                ]
            },
            "statusline": {
                "copies": ["container/statusline.sh", "host/statusline.sh"],
                "sync_command": ["assets/sync-statusline.sh", "--check"],
            },
            "unslop": {
                "canonical_root": "unslop/canonical",
                "copies": ["unslop/copy-a", "unslop/copy-b"],
                "entrypoints": ["__init__.py", "__main__.py", "cli.py"],
                "snapshot": "docs/unslop-fork-status.md",
                "tree_digest": tree_digest(canonical),
            },
            "forbidden_behavioral_roots": [".opencode/node_modules/", "archive/"],
        }

    def write_policy(self, payload: dict[str, object] | None = None) -> Path:
        return write_json(
            self.root / "configs/automation-provenance.json",
            payload or self.policy_payload(),
        )

    def run(self) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                str(CHECKER),
                "--repo-root",
                str(self.root),
                "--policy",
                str(self.root / "configs/automation-provenance.json"),
            ],
            check=False,
            env=self.env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )


class AutomationProvenanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.fixture = ProvenanceFixture(Path(self.temporary.name) / "repo")

    def assert_failure(self, fragment: str) -> None:
        result = self.fixture.run()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn(fragment, result.stderr)

    def test_minimal_repository_passes(self) -> None:
        result = self.fixture.run()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Automation provenance OK", result.stdout)

    def convert_to_crlf(self, *relatives: str) -> None:
        for relative in relatives:
            path = self.fixture.root / relative
            path.write_bytes(path.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))

    def set_generated_digest(self, payload: bytes) -> None:
        path = self.fixture.root / ".agentic-tooling/generated-manifest.json"
        manifest = json.loads(path.read_text(encoding="utf-8"))
        manifest["files"][0]["digest"] = digest(payload)
        write_json(path, manifest)

    def test_generated_crlf_checkout_passes_without_mutation(self) -> None:
        self.convert_to_crlf("generated/tool.sh")
        path = self.fixture.root / "generated/tool.sh"
        before = path.read_bytes()
        index = self.fixture.git("ls-files", "--stage").stdout
        for autocrlf in ("false", "true"):
            with self.subTest(autocrlf=autocrlf):
                self.fixture.git("config", "core.autocrlf", autocrlf)
                result = self.fixture.run()
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(path.read_bytes(), before)
                self.assertEqual(self.fixture.git("ls-files", "--stage").stdout, index)

    def test_unslop_crlf_checkout_preserves_tree_digest(self) -> None:
        self.convert_to_crlf("unslop/canonical/cli.py", "unslop/copy-a/__main__.py")
        result = self.fixture.run()
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_statusline_normalizes_different_checkout_endings(self) -> None:
        self.convert_to_crlf("container/statusline.sh")
        result = self.fixture.run()
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_crlf_does_not_hide_unstaged_generated_drift(self) -> None:
        self.convert_to_crlf("generated/tool.sh")
        path = self.fixture.root / "generated/tool.sh"
        path.write_bytes(path.read_bytes() + b"echo changed\r\n")
        self.assert_failure("generated output drifted")

    def test_crlf_does_not_mask_mirror_drift(self) -> None:
        self.convert_to_crlf("generated/tool.sh")
        self.fixture.write("container/tool.sh", "different\n")
        result = self.fixture.run()
        self.assertEqual(result.returncode, 1)
        self.assertIn("mirror content drift", result.stderr)
        self.assertNotIn("generated output drifted", result.stderr)

    def test_independent_failures_are_reported_in_section_order(self) -> None:
        self.fixture.write("generated/tool.sh", "different\n")
        self.fixture.write("container/tool.sh", "different\n")
        result = self.fixture.run()
        self.assertEqual(result.returncode, 1)
        self.assertIn("generated output drifted", result.stderr)
        self.assertIn("mirror content drift", result.stderr)
        self.assertLess(
            result.stderr.index("generated output drifted"),
            result.stderr.index("mirror content drift"),
        )
        self.assertNotIn("Automation provenance OK", result.stdout)

    def test_mirror_comparison_remains_byte_exact(self) -> None:
        self.convert_to_crlf("container/tool.sh")
        self.assert_failure("mirror content drift")

    def test_raw_endings_remain_significant_when_git_does_not_normalize(self) -> None:
        self.convert_to_crlf("generated/tool.sh")
        for attributes in ("-text", "!text !eol"):
            with self.subTest(attributes=attributes):
                self.fixture.write(".gitattributes", f"generated/tool.sh {attributes}\n")
                self.assert_failure("generated output drifted")

    def test_unspecified_text_obeys_autocrlf(self) -> None:
        self.fixture.write(".gitattributes", "generated/tool.sh !text !eol\n")
        self.fixture.git("config", "core.autocrlf", "true")
        self.convert_to_crlf("generated/tool.sh")
        result = self.fixture.run()
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_auto_binary_and_explicit_binary_preserve_raw_bytes(self) -> None:
        payload = b"binary\0data\r\n"
        self.fixture.write_bytes("generated/tool.sh", payload)
        self.set_generated_digest(payload)
        for attributes in ("-text", "text=auto eol=lf"):
            with self.subTest(attributes=attributes):
                self.fixture.write(".gitattributes", f"generated/tool.sh {attributes}\n")
                result = self.fixture.run()
                self.assertEqual(result.returncode, 0, result.stderr)
        self.set_generated_digest(payload.replace(b"\r\n", b"\n"))
        self.assert_failure("generated output drifted")

    def test_auto_uses_cleaned_worktree_not_existing_crlf_index(self) -> None:
        self.fixture.write(".gitattributes", "generated/tool.sh -text\n")
        self.convert_to_crlf("generated/tool.sh")
        path = self.fixture.root / "generated/tool.sh"
        self.fixture.git("add", "generated/tool.sh")
        self.fixture.write(".gitattributes", "generated/tool.sh text=auto eol=lf\n")
        cleaned = self.fixture.git("hash-object", "--path=generated/tool.sh", "generated/tool.sh")
        staged = self.fixture.git("rev-parse", ":generated/tool.sh")
        self.assertNotEqual(cleaned.stdout, staged.stdout)
        result = self.fixture.run()
        self.assertEqual(result.returncode, 0, result.stderr)
        path.write_bytes(path.read_bytes() + b"echo changed\r\n")
        self.assert_failure("generated output drifted")

    def test_mixed_endings_preserve_lone_carriage_returns(self) -> None:
        payload = b"first\r\nsecond\nthird\rfourth\r\n"
        self.fixture.write_bytes("generated/tool.sh", payload)
        self.set_generated_digest(payload.replace(b"\r\n", b"\n"))
        result = self.fixture.run()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.set_generated_digest(payload.replace(b"\r\n", b"\n").replace(b"\r", b"\n"))
        self.assert_failure("generated output drifted")

    @unittest.skipIf(os.name == "nt", "requires POSIX symlink creation")
    def test_symlink_target_bytes_are_not_normalized(self) -> None:
        path = self.fixture.root / "generated/tool.sh"
        path.unlink()
        path.symlink_to("target\r\nname")
        self.fixture.git("add", "generated/tool.sh")
        self.set_generated_digest(b"target\r\nname")
        result = self.fixture.run()
        self.assertEqual(result.returncode, 0, result.stderr)
        path.unlink()
        path.write_bytes(b"target\r\nname")
        result = self.fixture.run()
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_normalization_git_failure_is_reported(self) -> None:
        self.convert_to_crlf("generated/tool.sh")
        original = checker.run_git

        def failing_git(
            root: Path, *args: str, **kwargs: object
        ) -> subprocess.CompletedProcess[bytes]:
            if args[0] == "hash-object":
                raise checker.CheckFailure("git hash-object failed: fixture failure")
            return original(root, *args, **kwargs)

        with (
            mock.patch.dict(os.environ, self.fixture.env, clear=True),
            mock.patch.object(checker, "run_git", side_effect=failing_git),
        ):
            result = checker.check_repository(self.fixture.root)
        self.assertEqual(result.errors, ("git hash-object failed: fixture failure",))

    def test_invalid_policy_is_a_fatal_prerequisite(self) -> None:
        policy = self.fixture.policy_payload()
        policy["schema_version"] = 999
        self.fixture.write_policy(policy)
        self.fixture.write("generated/tool.sh", "different\n")
        result = self.fixture.run()
        self.assertEqual(result.returncode, 1)
        self.assertIn("policy schema_version must be 2", result.stderr)
        self.assertNotIn("generated output drifted", result.stderr)
        self.assertNotIn("Automation provenance OK", result.stdout)

    def test_generated_drift_requires_an_exact_nonstale_exception(self) -> None:
        generated = self.fixture.root / "generated/tool.sh"
        manifest = json.loads(
            (self.fixture.root / ".agentic-tooling/generated-manifest.json").read_text()
        )
        source_digest = manifest["files"][0]["sourceDigest"]
        self.fixture.write(
            "generated/tool.sh",
            "#!/bin/sh\n"
            "# source: tools/tool.yaml\n"
            f"# sourceDigest: {source_digest}\n"
            "# local-adoption\n",
        )
        self.assert_failure("generated output drifted")

        policy = self.fixture.policy_payload()
        policy["generated"]["accepted_divergences"] = [  # type: ignore[index]
            {
                "path": "generated/tool.sh",
                "accepted_digest": digest(generated.read_bytes()),
                "required_markers": ["local-adoption"],
                "rationale": "Fixture adaptation.",
            }
        ]
        self.fixture.write_policy(policy)
        accepted = self.fixture.run()
        self.assertEqual(accepted.returncode, 0, accepted.stderr)

        self.convert_to_crlf("generated/tool.sh")
        accepted = self.fixture.run()
        self.assertEqual(accepted.returncode, 0, accepted.stderr)

        generated.write_bytes(b"#!/bin/sh\necho generated\n")
        self.assert_failure("exception is stale")

    def test_mirror_content_mode_and_cleanup_drift_fail(self) -> None:
        target = self.fixture.root / "container/tool.sh"
        target.write_text("different\n", encoding="utf-8")
        self.assert_failure("mirror content drift")
        self.fixture.write("container/tool.sh", "#!/bin/sh\necho mirror\n")
        target.chmod(0o644)
        self.fixture.git("add", "container/tool.sh")
        self.assert_failure("mirror mode drift")
        target.chmod(0o755)
        self.fixture.git("add", "container/tool.sh")
        self.fixture.write("container/tool-stale.sh", "stale\n")
        self.fixture.git("add", "container/tool-stale.sh")
        self.assert_failure("stale tracked target")

    def test_mirror_manifest_contract_marker_fails_closed(self) -> None:
        manifest = self.fixture.root / "configs/devcontainer-sync.jsonc"
        manifest.write_text(
            manifest.read_text(encoding="utf-8").replace(
                '"schema_version": 1', '"schema_version": 2'
            ),
            encoding="utf-8",
        )
        self.assert_failure("mirror manifest schema_version must be 1")

    def test_espanso_statusline_and_unslop_drift_fail(self) -> None:
        cases = (
            ("platform/macos/tool.py.tmpl", "wrong\n", "Espanso platform wrapper"),
            ("container/statusline.sh", "wrong\n", "statusline copy must declare"),
            ("unslop/copy-a/cli.py", "different\n", "unslop content drift"),
        )
        for relative, content, message in cases:
            with self.subTest(relative=relative):
                original = (self.fixture.root / relative).read_text(encoding="utf-8")
                try:
                    (self.fixture.root / relative).write_text(content, encoding="utf-8")
                    self.assert_failure(message)
                finally:
                    (self.fixture.root / relative).write_text(original, encoding="utf-8")

    def test_unslop_tracks_nested_non_python_membership(self) -> None:
        self.fixture.write("unslop/copy-a/helpers/config.json", "{}\n")
        self.fixture.git("add", "unslop/copy-a/helpers/config.json")
        self.assert_failure("unslop tree membership drift")

    def test_statusline_version_accepts_crlf_checkout(self) -> None:
        for relative in ("container/statusline.sh", "host/statusline.sh"):
            path = self.fixture.root / relative
            path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))
        result = self.fixture.run()
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_forbidden_behavioral_roots_fail(self) -> None:
        registry = json.loads(
            (self.fixture.root / "configs/test-suites.json").read_text(encoding="utf-8")
        )
        registry["steps"][0]["covers"] = ["archive/test_tool.py"]
        write_json(self.fixture.root / "configs/test-suites.json", registry)
        self.assert_failure("covers forbidden root")

    def test_current_repository_passes(self) -> None:
        result = subprocess.run(
            [sys.executable, str(CHECKER)],
            cwd=REPO_ROOT,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.assertEqual(result.returncode, 0, result.stderr)


@unittest.skipUnless(shutil.which("bash"), "bash is required")
class StatuslineSyncProvenanceTests(unittest.TestCase):
    def test_check_mode_uses_pinned_fake_upstream_without_network(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            assets = root / "assets"
            assets.mkdir()
            shutil.copy2(STATUSLINE_SYNC, assets / "sync-statusline.sh")
            upstream = root / "upstream.sh"
            upstream.write_text("#!/usr/bin/env bash\necho upstream\n", encoding="utf-8")
            vendored = (
                "#!/usr/bin/env bash\n"
                "# renovate: datasource=github-releases depName=Astro-Han/claude-pace\n"
                'CLAUDE_PACE_VERSION="v1.2.3"\n'
                "echo upstream\n"
            )
            host = root / "dot_claude/executable_statusline.sh"
            container = root / (
                "private_Documents/development/container-dotfiles/dotfiles/"
                "dot_claude/executable_statusline.sh"
            )
            for path in (host, container):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(vendored, encoding="utf-8")
            fake_bin = root / "fake-bin"
            fake_curl = write_executable(
                fake_bin / "curl",
                """#!/bin/sh
output=
while [ "$#" -gt 0 ]; do
  if [ "$1" = "-o" ]; then output=$2; shift 2; else shift; fi
done
cp "$FAKE_UPSTREAM" "$output"
""",
            )
            self.assertTrue(fake_curl.is_file())
            env = os.environ.copy()
            env["FAKE_UPSTREAM"] = str(upstream)
            env["PATH"] = str(fake_bin) + os.pathsep + env["PATH"]
            result = subprocess.run(
                ["bash", str(assets / "sync-statusline.sh"), "--check"],
                cwd=root,
                env=env,
                check=False,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            container.write_text(vendored + "# drift\n", encoding="utf-8")
            drift = subprocess.run(
                ["bash", str(assets / "sync-statusline.sh"), "--check"],
                cwd=root,
                env=env,
                check=False,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            self.assertEqual(drift.returncode, 1)
            self.assertIn("vendored file drifted", drift.stderr)


if __name__ == "__main__":
    unittest.main()
