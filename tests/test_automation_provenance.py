from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.support.fixtures import init_git_repository, run_git, write_executable, write_json


REPO_ROOT = Path(__file__).resolve().parents[1]
CHECKER = REPO_ROOT / "assets/check-automation-provenance.py"
STATUSLINE_SYNC = REPO_ROOT / "assets/sync-statusline.sh"


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
        self.root = init_git_repository(root)
        self.write("configs/schemas/automation-provenance.v1.schema.json", "{}\n")

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

        dependencies = {"@example/sdk": "1.2.3", "promptfoo": "4.5.6"}
        write_json(
            self.root / "configs/promptfoo-runtime/package.json",
            {"private": True, "dependencies": dependencies},
        )
        write_json(
            self.root / "configs/promptfoo-runtime/package-lock.json",
            {"packages": {"": {"dependencies": dependencies}}},
        )
        self.write(
            "container/npm_packages.txt",
            "@example/sdk@1.2.3\npromptfoo@4.5.6\n",
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
        run_git(self.root, "add", "--all")

    def write(self, relative: str, content: str, *, executable: bool = False) -> Path:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        if executable:
            path.chmod(0o755)
        return path

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
            "$schema": "./schemas/automation-provenance.v1.schema.json",
            "schema_version": 1,
            "generated": {
                "manifest": ".agentic-tooling/generated-manifest.json",
                "accepted_divergences": [],
            },
            "mirrors": {"manifest": "configs/devcontainer-sync.jsonc"},
            "promptfoo": {
                "manifest": "configs/promptfoo-runtime/package.json",
                "lockfile": "configs/promptfoo-runtime/package-lock.json",
                "devcontainer_packages": "container/npm_packages.txt",
            },
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

    def test_generated_drift_requires_an_exact_nonstale_exception(self) -> None:
        generated = self.fixture.root / "generated/tool.sh"
        manifest = json.loads(
            (self.fixture.root / ".agentic-tooling/generated-manifest.json").read_text()
        )
        source_digest = manifest["files"][0]["sourceDigest"]
        generated.write_text(
            "#!/bin/sh\n"
            "# source: tools/tool.yaml\n"
            f"# sourceDigest: {source_digest}\n"
            "# local-adoption\n",
            encoding="utf-8",
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

        generated.write_bytes(b"#!/bin/sh\necho generated\n")
        self.assert_failure("exception is stale")

    def test_mirror_content_mode_and_cleanup_drift_fail(self) -> None:
        target = self.fixture.root / "container/tool.sh"
        target.write_text("different\n", encoding="utf-8")
        self.assert_failure("mirror content drift")
        target.write_text("#!/bin/sh\necho mirror\n", encoding="utf-8")
        target.chmod(0o644)
        run_git(self.fixture.root, "add", "container/tool.sh")
        self.assert_failure("mirror mode drift")
        target.chmod(0o755)
        run_git(self.fixture.root, "add", "container/tool.sh")
        self.fixture.write("container/tool-stale.sh", "stale\n")
        run_git(self.fixture.root, "add", "container/tool-stale.sh")
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

    def test_promptfoo_pins_are_derived_from_package_manifest(self) -> None:
        dependencies = {"@example/sdk": "2.0.0", "promptfoo": "4.5.6"}
        write_json(
            self.fixture.root / "configs/promptfoo-runtime/package.json",
            {"private": True, "dependencies": dependencies},
        )
        self.assert_failure("lockfile direct dependencies differ")
        write_json(
            self.fixture.root / "configs/promptfoo-runtime/package-lock.json",
            {"packages": {"": {"dependencies": dependencies}}},
        )
        self.fixture.write(
            "container/npm_packages.txt",
            "@example/sdk@2.0.0\npromptfoo@4.5.6\n",
        )
        result = self.fixture.run()
        self.assertEqual(result.returncode, 0, result.stderr)

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
        run_git(self.fixture.root, "add", "unslop/copy-a/helpers/config.json")
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
