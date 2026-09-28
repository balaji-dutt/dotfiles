from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import unittest
import uuid
from pathlib import Path

from tests.support.fixtures import isolated_environment, read_json_lines, write_executable


REPO_ROOT = Path(__file__).resolve().parents[1]
ASSETS = REPO_ROOT / "assets"
WRAPPER_TEMPLATE = REPO_ROOT / "bin" / "executable_sync-devcontainer-all.sh.tmpl"
BASH = shutil.which("bash")


@unittest.skipUnless(os.name != "nt" and BASH, "POSIX bash is required")
class SyncToolingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.environment_context = isolated_environment(prefix="sync-tooling-")
        self.fixture = self.environment_context.__enter__()
        self.addCleanup(self.environment_context.__exit__, None, None, None)
        self.root = self.fixture.root / "repo with spaces"
        self.root.mkdir()
        self.env = dict(self.fixture.env)
        self.env["PATH"] = os.pathsep.join(
            (str(self.fixture.fake_bin), "/usr/bin", "/bin")
        )
        write_executable(
            self.fixture.fake_bin / "uname",
            "#!/bin/sh\nprintf '%s\\n' Darwin\n",
        )

    def copy_asset(self, name: str) -> Path:
        target = self.root / "assets" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ASSETS / name, target)
        return target

    def run_bash(
        self,
        script: Path,
        *args: str,
        env: dict[str, str] | None = None,
        cwd: Path | None = None,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [BASH or "bash", str(script), *args],
            cwd=cwd or self.root,
            env=self.env if env is None else env,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    def write_sync_manifest(
        self,
        *,
        source_root: str = "source with spaces",
        schema: str = "./schemas/devcontainer-sync.v1.schema.json",
    ) -> None:
        manifest = self.root / "configs" / "devcontainer-sync.jsonc"
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text(
            f"""{{
  "$schema": {json.dumps(schema)},
  "schema_version": 1,
  // Comments and trailing commas are part of the supported JSONC contract.
  "shared": {{
    "mirrors": [{{
      "name": "fixture mirror",
      "source_root": {json.dumps(source_root)},
      "target_root": "target with spaces",
      "cleanup_managed": true,
      "include": ["**",],
      "exclude": ["excluded/**",],
    }},],
  }},
}}
""",
            encoding="utf-8",
        )

    def target_snapshot(self, root: Path) -> dict[str, tuple[str, bytes | str]]:
        snapshot: dict[str, tuple[str, bytes | str]] = {}
        for path in sorted(root.rglob("*")):
            relative = path.relative_to(root).as_posix()
            if path.is_symlink():
                snapshot[relative] = ("symlink", os.readlink(path))
            elif path.is_dir():
                snapshot[relative] = ("dir", "")
            elif path.is_file():
                snapshot[relative] = ("file", path.read_bytes())
        return snapshot

    def test_asset_sync_handles_jsonc_symlinks_stale_files_and_noop(self) -> None:
        script = self.copy_asset("sync-devcontainer-assets.sh")
        self.write_sync_manifest()
        source = self.root / "source with spaces"
        target = self.root / "target with spaces"
        (source / "nested").mkdir(parents=True)
        (source / "excluded").mkdir()
        (source / "keep.txt").write_text("keep\n", encoding="utf-8")
        (source / "nested" / "child.txt").write_text("child\n", encoding="utf-8")
        (source / "excluded" / "ignored.txt").write_text("ignored\n", encoding="utf-8")
        os.symlink("keep.txt", source / "keep-link")
        (target / "nested").mkdir(parents=True)
        (target / "excluded").mkdir()
        (target / "nested" / "stale.txt").write_text("stale\n", encoding="utf-8")
        (target / "excluded" / "preserved.txt").write_text("preserved\n", encoding="utf-8")

        first = self.run_bash(script)
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertIn("3 file(s) updated, 1 stale file(s) removed", first.stdout)
        self.assertEqual((target / "keep.txt").read_text(encoding="utf-8"), "keep\n")
        self.assertEqual((target / "nested" / "child.txt").read_text(encoding="utf-8"), "child\n")
        self.assertTrue((target / "keep-link").is_symlink())
        self.assertEqual(os.readlink(target / "keep-link"), "keep.txt")
        self.assertFalse((target / "nested" / "stale.txt").exists())
        self.assertFalse((target / "excluded" / "ignored.txt").exists())
        self.assertTrue((target / "excluded" / "preserved.txt").is_file())

        before = self.target_snapshot(target)
        second = self.run_bash(script)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertIn("Devcontainer asset sync complete: no changes.", second.stdout)
        self.assertEqual(self.target_snapshot(target), before)

    def test_asset_sync_rejects_missing_roots_and_invalid_schema(self) -> None:
        script = self.copy_asset("sync-devcontainer-assets.sh")
        self.write_sync_manifest(source_root="missing source with spaces")
        missing = self.run_bash(script)
        self.assertNotEqual(missing.returncode, 0)
        self.assertIn("Missing source root:", missing.stderr)
        self.assertFalse((self.root / "target with spaces").exists())

        self.write_sync_manifest(schema="./schemas/wrong.json")
        invalid = self.run_bash(script)
        self.assertNotEqual(invalid.returncode, 0)
        self.assertIn("Manifest $schema must be", invalid.stderr)
        self.assertFalse((self.root / "target with spaces").exists())

    def test_sync_all_sequences_steps_and_stops_after_sync_failure(self) -> None:
        workflow = self.copy_asset("sync-devcontainer-all.sh")
        log = self.fixture.root / "sequence.log"
        self.env["SYNC_SEQUENCE_LOG"] = str(log)
        write_executable(
            self.root / "assets" / "sync-devcontainer-assets.sh",
            """#!/bin/sh
printf '%s\n' assets >> "$SYNC_SEQUENCE_LOG"
[ "${FAIL_ASSET_SYNC:-0}" = 0 ] || exit 23
""",
        )
        write_executable(
            self.root / "assets" / "render-container-configs.sh",
            """#!/bin/sh
printf '%s\n' render >> "$SYNC_SEQUENCE_LOG"
""",
        )

        success = self.run_bash(workflow)
        self.assertEqual(success.returncode, 0, success.stderr)
        self.assertEqual(log.read_text(encoding="utf-8").splitlines(), ["assets", "render"])
        self.assertIn("workflow complete", success.stdout)

        log.unlink()
        failed_env = dict(self.env)
        failed_env["FAIL_ASSET_SYNC"] = "1"
        failed = self.run_bash(workflow, env=failed_env)
        self.assertEqual(failed.returncode, 23)
        self.assertEqual(log.read_text(encoding="utf-8").splitlines(), ["assets"])
        self.assertNotIn("workflow complete", failed.stdout)

    def test_render_writes_only_fixture_scoped_synthetic_secrets(self) -> None:
        script = self.copy_asset("render-container-configs.sh")
        source_template = self.root / "private_dot_config" / "opencode" / "private_opencode.env.tmpl"
        source_template.parent.mkdir(parents=True)
        source_template.write_text("fixture template without secret values\n", encoding="utf-8")
        log = self.fixture.root / "chezmoi-calls.jsonl"
        secret_a = f"fixture-container-{uuid.uuid4().hex}"
        secret_b = f"fixture-opencode-{uuid.uuid4().hex}"
        render_env = dict(self.env)
        render_env.update(
            {
                "FIXTURE_CONTAINER_SECRET": secret_a,
                "FIXTURE_OPENCODE_SECRET": secret_b,
                "FIXTURE_CHEZMOI_LOG": str(log),
            }
        )
        write_executable(
            self.fixture.fake_bin / "op",
            "#!/bin/sh\nexit 0\n",
        )
        write_executable(
            self.fixture.fake_bin / "chezmoi",
            f"""#!{sys.executable}
import json
import os
import pathlib
import sys

log = pathlib.Path(os.environ["FIXTURE_CHEZMOI_LOG"])
with log.open("a", encoding="utf-8") as handle:
    handle.write(json.dumps({{
        "argv": sys.argv[1:],
        "for_container": os.environ.get("FOR_CONTAINER"),
        "non_interactive": os.environ.get("NON_INTERACTIVE_MODE"),
    }}, sort_keys=True) + "\\n")
if len(sys.argv) > 2:
    print(os.environ["FIXTURE_CONTAINER_SECRET"])
else:
    sys.stdin.read()
    print(os.environ["FIXTURE_OPENCODE_SECRET"])
""",
        )
        caller = self.fixture.root / "caller with spaces"
        caller.mkdir()

        result = self.run_bash(script, env=render_env, cwd=caller)
        self.assertEqual(result.returncode, 0, result.stderr)
        config_root = self.root / "private_Documents/development/container-dotfiles/dotfiles/configs"
        container_env = config_root / "container_env"
        opencode_env = config_root / "opencode.env"
        self.assertEqual(
            container_env.read_text(encoding="utf-8"),
            f"OP_SERVICE_ACCOUNT_TOKEN={secret_a}\n",
        )
        self.assertEqual(opencode_env.read_text(encoding="utf-8"), f"{secret_b}\n")
        calls = read_json_lines(log)
        self.assertEqual(len(calls), 2)
        self.assertIsNone(calls[0]["for_container"])
        self.assertEqual(calls[1]["for_container"], "true")
        self.assertEqual(calls[1]["non_interactive"], "true")

        secret_files: dict[str, list[str]] = {secret_a: [], secret_b: []}
        for path in self.fixture.root.rglob("*"):
            if not path.is_file():
                continue
            payload = path.read_bytes()
            for secret in secret_files:
                if secret.encode() in payload:
                    secret_files[secret].append(path.relative_to(self.fixture.root).as_posix())
        self.assertEqual(secret_files[secret_a], [container_env.relative_to(self.fixture.root).as_posix()])
        self.assertEqual(secret_files[secret_b], [opencode_env.relative_to(self.fixture.root).as_posix()])

        shutil.rmtree(config_root)
        (self.fixture.fake_bin / "op").unlink()
        missing_bin = self.fixture.root / "missing-op-bin"
        write_executable(
            missing_bin / "uname",
            "#!/bin/sh\nprintf '%s\\n' Darwin\n",
        )
        write_executable(
            missing_bin / "dirname",
            "#!/bin/sh\nexec /usr/bin/dirname \"$@\"\n",
        )
        write_executable(missing_bin / "chezmoi", "#!/bin/sh\nexit 99\n")
        missing_env = dict(render_env)
        missing_env["PATH"] = str(missing_bin)
        missing_op = self.run_bash(script, env=missing_env, cwd=caller)
        self.assertEqual(missing_op.returncode, 1)
        self.assertIn("1Password CLI not found", missing_op.stdout)
        self.assertFalse(config_root.exists())

    def test_managed_wrapper_forwards_override_and_uses_default_repo(self) -> None:
        wrapper_template = WRAPPER_TEMPLATE.read_text(encoding="utf-8")
        rendered_wrapper = wrapper_template.replace(
            "{{ .chezmoi.homeDir }}", str(self.fixture.home)
        )
        self.assertNotEqual(rendered_wrapper, wrapper_template)
        self.assertIn(str(self.fixture.home), rendered_wrapper)
        wrapper = write_executable(
            self.fixture.root / "bin" / "sync-devcontainer-all.sh",
            rendered_wrapper,
        )
        log = self.fixture.root / "wrapper-calls.jsonl"
        wrapper_env = dict(self.env)
        wrapper_env["WRAPPER_LOG"] = str(log)

        def write_target(repo_root: Path) -> None:
            write_executable(
                repo_root / "assets" / "sync-devcontainer-all.sh",
                f"""#!/bin/sh
"{sys.executable}" - "$WRAPPER_LOG" "$@" <<'PY'
import json
import pathlib
import sys
with pathlib.Path(sys.argv[1]).open("a", encoding="utf-8") as handle:
    handle.write(json.dumps(sys.argv[2:]) + "\\n")
PY
""",
            )

        override_root = self.fixture.root / "override repo with spaces"
        write_target(override_root)
        override_env = dict(wrapper_env)
        override_env["DOTFILES_REPO_ROOT"] = str(override_root)
        override = self.run_bash(
            wrapper,
            "--check",
            "value with spaces",
            env=override_env,
        )
        self.assertEqual(override.returncode, 0, override.stderr)
        self.assertEqual(read_json_lines(log), [["--check", "value with spaces"]])

        default_root = self.fixture.home / "Documents" / "development" / "dotfiles"
        write_target(default_root)
        default = self.run_bash(wrapper, "default value", env=wrapper_env)
        self.assertEqual(default.returncode, 0, default.stderr)
        self.assertEqual(read_json_lines(log)[1], ["default value"])

        missing_env = dict(wrapper_env)
        missing_env["DOTFILES_REPO_ROOT"] = str(self.fixture.root / "missing repo")
        missing = self.run_bash(wrapper, env=missing_env)
        self.assertEqual(missing.returncode, 1)
        self.assertIn("Set DOTFILES_REPO_ROOT", missing.stderr)

    def test_statusline_version_mismatch_stops_before_download(self) -> None:
        script = self.copy_asset("sync-statusline.sh")
        host = self.root / "dot_claude" / "executable_statusline.sh"
        container = self.root / (
            "private_Documents/development/container-dotfiles/dotfiles/"
            "dot_claude/executable_statusline.sh"
        )
        vendored = (
            "#!/bin/sh\n"
            "# renovate: datasource=github-releases depName=Astro-Han/claude-pace\n"
            "CLAUDE_PACE_VERSION=\"{}\"\n"
            "echo fixture\n"
        )
        for path, version in ((host, "v1.0.0"), (container, "v2.0.0")):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(vendored.format(version), encoding="utf-8")
        marker = self.fixture.root / "curl-was-called"
        mismatch_env = dict(self.env)
        mismatch_env["CURL_MARKER"] = str(marker)
        write_executable(
            self.fixture.fake_bin / "curl",
            "#!/bin/sh\n: > \"$CURL_MARKER\"\nexit 99\n",
        )

        result = self.run_bash(script, "--check", env=mismatch_env)
        self.assertEqual(result.returncode, 1)
        self.assertIn("version mismatch between vendored copies", result.stderr)
        self.assertFalse(marker.exists())


@unittest.skipUnless(os.name != "nt" and BASH, "POSIX bash is required")
class KanbanPinTests(unittest.TestCase):
    SITES = (
        ".chezmoiscripts/run_onchange_after_install_better_beads_kanban.sh.tmpl",
        ".chezmoiscripts/run_onchange_after_install_better_beads_kanban.ps1.tmpl",
        "private_Documents/development/container-dotfiles/devcontainers/"
        "gitlab.com/servers-homelab/homelab-IaC/dot_devcontainer/devcontainer-common.sh",
    )
    def setUp(self) -> None:
        context = isolated_environment(prefix="kanban-pin-")
        self.fixture = context.__enter__()
        self.addCleanup(context.__exit__, None, None, None)
        self.repo = self.fixture.root / "repo with spaces"
        self.script = self.repo / "assets/sync-beads-kanban-pin.sh"
        self.script.parent.mkdir(parents=True)
        shutil.copy2(ASSETS / self.script.name, self.script)
        self.sites = [self.repo / name for name in self.SITES]
        for source_name, target in zip(self.SITES, self.sites):
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(REPO_ROOT / source_name, target)
        source = self.sites[0].read_bytes()
        version_match = re.search(rb'^FORK_VERSION="([^"]+)"', source, re.MULTILINE)
        sha_match = re.search(rb'^EXPECTED_SHA="([0-9a-f]{64})"', source, re.MULTILINE)
        assert version_match and sha_match
        self.version = version_match.group(1).decode("ascii")
        self.asset_name = f"better-beads-kanban-{self.version}.vsix"
        self.old_sha = sha_match.group(1).decode("ascii")
        self.asset = b"fixture VSIX content\n"
        self.sha = hashlib.sha256(self.asset).hexdigest()
        self.assertNotEqual(self.old_sha, self.sha)
        self.log = self.fixture.root / "curl.jsonl"
        self.env = {
            "HOME": str(self.fixture.home),
            "TMPDIR": str(self.fixture.root / "tmp"),
            "PATH": f"{self.fixture.fake_bin}:/usr/bin:/bin",
            "LC_ALL": "C",
            "FIXTURE_CURL_LOG": str(self.log),
            "FIXTURE_ASSET_HEX": self.asset.hex(),
            "FIXTURE_ASSET_NAME": self.asset_name,
        }
        write_executable(
            self.fixture.fake_bin / "curl",
            f"#!{sys.executable}\n"
            "import hashlib, json, os, pathlib, sys\n"
            "args = sys.argv[1:]\n"
            "with open(os.environ['FIXTURE_CURL_LOG'], 'a', encoding='utf-8') as handle:\n"
            "    handle.write(json.dumps(args) + '\\n')\n"
            "url, target = args[1], pathlib.Path(args[args.index('-o') + 1])\n"
            "mode = os.environ.get('FIXTURE_CURL_MODE', 'manifest')\n"
            "asset = bytes.fromhex(os.environ['FIXTURE_ASSET_HEX'])\n"
            "if url.endswith('/SHA256SUMS'):\n"
            "    if mode in ('manifest_fail', 'asset_fail'):\n"
            "        sys.exit(22)\n"
            "    digest = 'invalid' if mode == 'manifest_bad' else hashlib.sha256(asset).hexdigest()\n"
            "    name = 'other.vsix' if mode == 'manifest_missing' else os.environ['FIXTURE_ASSET_NAME']\n"
            "    separator = ' *' if mode == 'manifest_star' else '  '\n"
            "    target.write_text(digest + separator + name + '\\n', encoding='utf-8')\n"
            "elif mode == 'asset_fail':\n"
            "    sys.exit(22)\n"
            "else:\n"
            "    target.write_bytes(asset)\n",
        )

    def run_pin(self, *args: str, mode: str = "manifest") -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [BASH or "bash", str(self.script), *args],
            cwd=self.repo,
            env=self.env | {"FIXTURE_CURL_MODE": mode},
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=20,
        )

    def snapshot(self) -> list[bytes]:
        return [site.read_bytes() for site in self.sites]

    def test_check_write_default_and_help_keep_other_content(self) -> None:
        before = self.snapshot()
        check = self.run_pin("--check")
        self.assertEqual(check.returncode, 1)
        self.assertEqual(check.stderr.count("EXPECTED_SHA drifted"), 3)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(len(read_json_lines(self.log)), 1)

        help_result = self.run_pin("--help")
        self.assertEqual(help_result.returncode, 0, help_result.stderr)
        self.assertIn("--check", help_result.stdout)
        self.assertEqual(len(read_json_lines(self.log)), 1)

        write = self.run_pin("--write")
        self.assertEqual(write.returncode, 0, write.stderr)
        self.assertEqual(write.stdout.count("wrote "), 3)
        after = self.snapshot()
        for original, updated in zip(before, after):
            self.assertEqual(original.count(self.old_sha.encode()), 1)
            self.assertEqual(updated, original.replace(self.old_sha.encode(), self.sha.encode()))
        self.assertEqual(self.run_pin("--check").returncode, 0)
        self.assertIn("no changes", self.run_pin().stdout)
        self.assertEqual(self.snapshot(), after)

    def test_manifest_fallback_hashes_asset_and_reports_download_failure(self) -> None:
        for mode in ("manifest_missing", "manifest_fail", "manifest_star"):
            with self.subTest(mode=mode):
                before = self.snapshot()
                result = self.run_pin("--write", mode=mode)
                self.assertEqual(result.returncode, 0, result.stderr)
                calls = read_json_lines(self.log)
                if mode == "manifest_star":
                    self.assertTrue(calls[-1][1].endswith("/SHA256SUMS"))
                else:
                    self.assertIn("hashing the asset instead", result.stdout)
                    self.assertTrue(calls[-2][1].endswith("/SHA256SUMS"))
                    self.assertTrue(calls[-1][1].endswith("/" + self.asset_name))
                for updated, original in zip(self.snapshot(), before):
                    self.assertEqual(updated, original.replace(self.old_sha.encode(), self.sha.encode()))
                for site, original in zip(self.sites, before):
                    site.write_bytes(original)

        before = self.snapshot()
        failure = self.run_pin("--write", mode="asset_fail")
        self.assertEqual(failure.returncode, 1)
        self.assertIn("failed downloading", failure.stderr)
        self.assertEqual(self.snapshot(), before)

    def test_bad_inputs_and_missing_sites_fail_without_writing(self) -> None:
        before = self.snapshot()
        unknown = self.run_pin("--wrong")
        self.assertEqual(unknown.returncode, 2)
        self.assertIn("unknown argument", unknown.stderr)
        self.assertFalse(self.log.exists())

        bad_sum = self.run_pin("--write", mode="manifest_bad")
        self.assertEqual(bad_sum.returncode, 1)
        self.assertIn("could not resolve a sha256", bad_sum.stderr)
        self.assertEqual(self.snapshot(), before)

        second = self.sites[1]
        original = second.read_bytes()
        second.write_bytes(original.replace(b'"' + self.version.encode() + b'"', b'"9.9.9"', 1))
        mismatch = self.run_pin("--check")
        self.assertEqual(mismatch.returncode, 1)
        self.assertIn("pinned version mismatch", mismatch.stderr)
        second.write_bytes(original)
        second.unlink()
        missing = self.run_pin("--check")
        self.assertEqual(missing.returncode, 1)
        self.assertIn("missing install site", missing.stderr)
        self.assertEqual(self.sites[0].read_bytes(), before[0])
        self.assertEqual(self.sites[2].read_bytes(), before[2])

    def test_sentinel_and_rewrite_failure_preserve_fixture_sources(self) -> None:
        before = self.snapshot()
        first = self.sites[0]
        first.write_bytes(before[0].replace(b'EXPECTED_SHA="' + self.old_sha.encode() + b'"', b'EXPECTED_SHA="missing"'))
        missing_sha = self.run_pin("--write")
        self.assertEqual(missing_sha.returncode, 1)
        self.assertIn("no expected-sha sentinel", missing_sha.stderr)
        first.write_bytes(before[0])

        write_executable(
            self.fixture.fake_bin / "sed",
            f"#!{sys.executable}\n"
            "import os, sys\n"
            "if len(sys.argv) > 2 and sys.argv[2].startswith('s#'):\n"
            "    sys.exit(73)\n"
            "os.execv('/usr/bin/sed', ['sed', *sys.argv[1:]])\n",
        )
        failure = self.run_pin("--write")
        self.assertEqual(failure.returncode, 73)
        self.assertEqual(self.snapshot(), before)


if __name__ == "__main__":
    unittest.main()
