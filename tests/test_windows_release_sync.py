from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest
import zipfile

from tests.support.powershell import resolve_powershell_runtime


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / ".chezmoiscripts" / "run_after_windows-release-sync.ps1.tmpl"
PWSH = resolve_powershell_runtime()
REPO = "owner/repo"
TEMPLATE_CONTROLS = {
    '{{- if ne .chezmoi.os "windows" -}}',
    "{{- /* not Windows: do nothing */ -}}",
    "{{- else -}}",
    "{{- end -}}",
}
FIXTURE_STUBS = r'''
function Invoke-RestMethod {
  param($Uri, $Headers, $TimeoutSec)
  Add-Content -LiteralPath (Join-Path $env:RELEASE_SYNC_FIXTURES 'api-calls.log') -Value $Uri
  if (Test-Path -LiteralPath (Join-Path $env:RELEASE_SYNC_FIXTURES 'api-fail')) { throw 'fixture API failure' }
  Get-Content -LiteralPath (Join-Path $env:RELEASE_SYNC_FIXTURES 'releases.json') -Raw | ConvertFrom-Json
}
function Invoke-WebRequest {
  param($Uri, $OutFile, $Headers, $TimeoutSec)
  Add-Content -LiteralPath (Join-Path $env:RELEASE_SYNC_FIXTURES 'downloads.log') -Value $Uri
  $segments = ([uri] $Uri).Segments
  $source = Join-Path (Join-Path (Join-Path $env:RELEASE_SYNC_FIXTURES 'assets') $segments[-2].TrimEnd('/')) $segments[-1]
  Copy-Item -LiteralPath $source -Destination $OutFile
}
'''
POST_INSTALL = r'''
param([string] $ReleaseDir, [string] $Tag, [string] $Dest)
$counterPath = Join-Path $env:RELEASE_SYNC_FIXTURES 'post-count.txt'
$count = 0
if (Test-Path -LiteralPath $counterPath) { $count = [int] (Get-Content -LiteralPath $counterPath -Raw) }
$count++
Set-Content -LiteralPath $counterPath -Value $count
Add-Content -LiteralPath (Join-Path $env:RELEASE_SYNC_FIXTURES 'post-args.log') -Value "$ReleaseDir|$Tag|$Dest"
$codes = @((Get-Content -LiteralPath (Join-Path $env:RELEASE_SYNC_FIXTURES 'post-codes.txt') -Raw).Trim() -split ',')
exit [int] $codes[[Math]::Min($count, $codes.Count) - 1]
'''


def zip_bytes(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return buffer.getvalue()


def is_reparse_point(path: Path) -> bool:
    try:
        attributes = os.lstat(path).st_file_attributes
    except FileNotFoundError:
        return False
    return bool(attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT)


@unittest.skipUnless(os.name == "nt" and PWSH, "native Windows PowerShell is required")
class WindowsReleaseSyncTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="release-sync-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.fixtures = self.root / "fixtures"
        self.home = self.root / "home"
        self.localappdata = self.root / "localappdata"
        self.temp = self.root / "temp"
        self.tools = self.root / "tools"
        self.dest = self.tools / "example"
        for directory in (self.fixtures, self.home, self.localappdata, self.temp, self.tools):
            directory.mkdir()
        self.config_path = self.root / "release-sync.json"
        self.state_path = self.localappdata / "dotfiles" / "release-sync" / "example.json"
        self.releases: list[dict[str, object]] = []

        source = TEMPLATE.read_text(encoding="utf-8")
        body = "\n".join(line for line in source.splitlines() if line not in TEMPLATE_CONTROLS)
        self.script = self.root / "release-sync.ps1"
        self.script.write_text(FIXTURE_STUBS + "\n" + body + "\n", encoding="utf-8")

    def env(self, **extra: str) -> dict[str, str]:
        base = {
            name: os.environ[name]
            for name in ("PATH", "PATHEXT", "SystemRoot", "WINDIR", "ComSpec")
            if name in os.environ
        }
        base.update({
            "HOME": str(self.home),
            "USERPROFILE": str(self.home),
            "LOCALAPPDATA": str(self.localappdata),
            "TEMP": str(self.temp),
            "TMP": str(self.temp),
            "GITHUB_API_TOKEN": "",
            "DOTFILES_RELEASE_SYNC_CONFIG": str(self.config_path),
            "RELEASE_SYNC_FIXTURES": str(self.fixtures),
        })
        base.update(extra)
        return base

    def run_hook(self, **extra: str) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            [PWSH, "-NoLogo", "-NoProfile", "-NonInteractive", "-File", str(self.script)],
            env=self.env(**extra),
            cwd=self.root,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=120,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stderr.strip(), "", result.stderr)
        return result

    def write_config(self, **overrides: object) -> dict[str, object]:
        entry: dict[str, object] = {
            "name": "example",
            "repo": REPO,
            "channel": "prerelease",
            "asset_pattern": r"^Bundle.*\.zip$",
            "dest": str(self.dest),
            "keep": 3,
        }
        entry.update(overrides)
        entry = {key: value for key, value in entry.items() if value is not None}
        self.write_raw_config({"schema_version": 1, "releases": [entry]})
        return entry

    def write_raw_config(self, config: object) -> None:
        self.config_path.write_text(json.dumps(config), encoding="utf-8")

    def add_release(
        self,
        tag: str,
        published: str,
        *,
        files: dict[str, bytes] | None = None,
        prerelease: bool = False,
        draft: bool = False,
        asset_names: tuple[str, ...] = ("Bundle.zip",),
        digest: str | None = "valid",
        payload: bytes | None = None,
        url_prefix: str = f"https://github.com/{REPO}/releases/download",
    ) -> None:
        content = payload if payload is not None else zip_bytes(files or {"tool.exe": f"{tag}-tool".encode()})
        asset_dir = self.fixtures / "assets" / tag
        asset_dir.mkdir(parents=True, exist_ok=True)
        assets = []
        for name in asset_names:
            (asset_dir / name).write_bytes(content)
            asset: dict[str, object] = {
                "name": name,
                "browser_download_url": f"{url_prefix}/{tag}/{name}",
            }
            if digest == "valid":
                asset["digest"] = "sha256:" + hashlib.sha256(content).hexdigest()
            elif digest == "wrong":
                asset["digest"] = "sha256:" + "0" * 64
            assets.append(asset)
        self.releases = [release for release in self.releases if release["tag_name"] != tag]
        self.releases.append({
            "tag_name": tag,
            "draft": draft,
            "prerelease": prerelease,
            "published_at": published,
            "assets": assets,
        })
        (self.fixtures / "releases.json").write_text(json.dumps(self.releases), encoding="utf-8")

    def api_calls(self) -> int:
        log = self.fixtures / "api-calls.log"
        return len(log.read_text(encoding="utf-8").splitlines()) if log.exists() else 0

    def state(self) -> dict[str, object]:
        return json.loads(self.state_path.read_text(encoding="utf-8-sig"))

    def assert_current(self, tag: str) -> None:
        current = self.dest / "current"
        self.assertTrue(is_reparse_point(current), "current is not a junction")
        self.assertEqual(current.resolve(), (self.dest / "releases" / tag).resolve())

    def test_absent_config_is_a_silent_no_op(self) -> None:
        result = self.run_hook()

        self.assertEqual(result.stdout.strip(), "")
        self.assertFalse((self.localappdata / "dotfiles").exists())
        self.assertEqual(self.api_calls(), 0)

    def test_installs_newest_prerelease_and_preserves_user_files(self) -> None:
        self.dest.mkdir()
        (self.dest / "user-data.txt").write_text("user data", encoding="utf-8")
        (self.dest / "output").mkdir()
        (self.dest / "output" / "result.bin").write_text("output", encoding="utf-8")
        self.add_release("v1.0.0", "2026-01-01T00:00:00Z")
        self.add_release("v1.1.0", "2026-02-01T00:00:00Z", prerelease=True)
        self.add_release("v2.0.0", "2026-03-01T00:00:00Z", draft=True, prerelease=True)
        self.write_config()

        result = self.run_hook()

        self.assertIn("installed v1.1.0", result.stdout)
        self.assert_current("v1.1.0")
        self.assertEqual((self.dest / "current" / "tool.exe").read_bytes(), b"v1.1.0-tool")
        self.assertEqual((self.dest / "user-data.txt").read_text(encoding="utf-8"), "user data")
        self.assertEqual((self.dest / "output" / "result.bin").read_text(encoding="utf-8"), "output")
        state = self.state()
        self.assertEqual(state["current_tag"], "v1.1.0")
        self.assertEqual([record["tag"] for record in state["installed"]], ["v1.1.0"])
        self.assertEqual(sorted(path.name for path in (self.dest / "releases").iterdir()), ["v1.1.0"])
        self.assertEqual(list(self.temp.iterdir()), [])

    def test_stable_channel_skips_prereleases(self) -> None:
        self.add_release("v1.0.0", "2026-01-01T00:00:00Z")
        self.add_release("v1.1.0", "2026-02-01T00:00:00Z", prerelease=True)
        self.write_config(channel="stable")

        self.run_hook()

        self.assert_current("v1.0.0")

    def test_throttle_skips_recent_checks_until_forced_or_stale(self) -> None:
        self.add_release("v1.0.0", "2026-01-01T00:00:00Z")
        self.write_config()

        self.run_hook()
        self.assertEqual(self.api_calls(), 1)
        result = self.run_hook()
        self.assertEqual(self.api_calls(), 1)
        self.assertEqual(result.stdout.strip(), "")
        self.run_hook(DOTFILES_RELEASE_SYNC_FORCE="1")
        self.assertEqual(self.api_calls(), 2)

        state = self.state()
        state["last_check_utc"] = "2020-01-01T00:00:00.0000000Z"
        self.state_path.write_text(json.dumps(state), encoding="utf-8")
        self.run_hook()
        self.assertEqual(self.api_calls(), 3)
        self.assertEqual(len((self.fixtures / "downloads.log").read_text(encoding="utf-8").splitlines()), 1)

    def test_digest_mismatch_installs_nothing_and_retries(self) -> None:
        self.add_release("v1.0.0", "2026-01-01T00:00:00Z", digest="wrong")
        self.write_config()

        result = self.run_hook()

        self.assertIn("WARNING: release-sync: example: could not install v1.0.0", result.stdout)
        self.assertIn("failed verification", result.stdout)
        self.assertFalse((self.dest / "current").exists())
        self.assertEqual(list((self.dest / "releases").iterdir()), [])
        self.assertIsNone(self.state()["last_check_utc"])

    def test_missing_digest_is_refused(self) -> None:
        self.add_release("v1.0.0", "2026-01-01T00:00:00Z", digest=None)
        self.write_config()

        result = self.run_hook()

        self.assertIn("no sha256 digest", result.stdout)
        self.assertFalse((self.fixtures / "downloads.log").exists())
        self.assertFalse((self.dest / "releases").exists())

    def test_asset_pattern_must_match_exactly_one_asset(self) -> None:
        self.add_release("v1.0.0", "2026-01-01T00:00:00Z", asset_names=("Bundle-a.zip", "Bundle-b.zip"))
        self.write_config()
        self.assertIn("has 2 assets matching asset_pattern", self.run_hook().stdout)

        self.add_release("v1.0.0", "2026-01-01T00:00:00Z", asset_names=("Other.zip",))
        self.assertIn("has 0 assets matching asset_pattern", self.run_hook(DOTFILES_RELEASE_SYNC_FORCE="1").stdout)
        self.assertFalse((self.fixtures / "downloads.log").exists())

    def test_unexpected_download_url_is_refused(self) -> None:
        self.add_release(
            "v1.0.0", "2026-01-01T00:00:00Z",
            url_prefix="https://github.com/other/repo/releases/download",
        )
        self.write_config()

        result = self.run_hook()

        self.assertIn("unexpected download URL", result.stdout)
        self.assertFalse((self.fixtures / "downloads.log").exists())
        self.assertFalse((self.dest / "releases").exists())
        self.assertIsNotNone(self.state()["last_check_utc"])

    def test_download_url_with_dot_segments_is_refused(self) -> None:
        self.add_release(
            "v1.0.0", "2026-01-01T00:00:00Z",
            url_prefix=f"https://github.com/{REPO}/releases/download/../../../other/repo/releases/download",
        )
        self.write_config()

        result = self.run_hook()

        self.assertIn("unexpected download URL", result.stdout)
        self.assertFalse((self.fixtures / "downloads.log").exists())

    def test_tags_windows_cannot_store_faithfully_are_refused(self) -> None:
        self.write_config()
        for tag in ("v1.", "con", "LPT1.txt"):
            with self.subTest(tag=tag):
                release = {
                    "tag_name": tag,
                    "draft": False,
                    "prerelease": False,
                    "published_at": "2026-01-01T00:00:00Z",
                    "assets": [{
                        "name": "Bundle.zip",
                        "browser_download_url": f"https://github.com/{REPO}/releases/download/{tag}/Bundle.zip",
                        "digest": "sha256:" + "0" * 64,
                    }],
                }
                (self.fixtures / "releases.json").write_text(json.dumps([release]), encoding="utf-8")
                result = self.run_hook(DOTFILES_RELEASE_SYNC_FORCE="1")
                self.assertIn("is not usable as a folder name", result.stdout)
        self.assertFalse((self.fixtures / "downloads.log").exists())

    def test_non_zip_asset_is_refused_before_download(self) -> None:
        self.add_release("v1.0.0", "2026-01-01T00:00:00Z", asset_names=("Bundle.tar.gz",))
        self.write_config(asset_pattern=r"^Bundle")

        result = self.run_hook()

        self.assertIn("is not a .zip archive", result.stdout)
        self.assertFalse((self.fixtures / "downloads.log").exists())

    def test_archive_with_parent_traversal_is_rejected(self) -> None:
        self.add_release("v1.0.0", "2026-01-01T00:00:00Z", files={"../escaped.txt": b"bad", "tool.exe": b"x"})
        self.write_config()

        result = self.run_hook()

        self.assertIn("escapes the release folder", result.stdout)
        self.assertFalse((self.dest / "escaped.txt").exists())
        self.assertFalse((self.dest / "releases" / "escaped.txt").exists())
        self.assertEqual(list((self.dest / "releases").iterdir()), [])
        self.assertFalse((self.dest / "current").exists())

    def test_existing_non_junction_current_is_refused(self) -> None:
        (self.dest / "current").mkdir(parents=True)
        (self.dest / "current" / "keep.txt").write_text("mine", encoding="utf-8")
        self.add_release("v1.0.0", "2026-01-01T00:00:00Z")
        self.write_config()

        result = self.run_hook()

        self.assertIn("is not a junction", result.stdout)
        self.assertEqual(self.api_calls(), 0)
        self.assertEqual((self.dest / "current" / "keep.txt").read_text(encoding="utf-8"), "mine")
        self.assertFalse((self.dest / "releases").exists())

    def test_prune_keeps_recent_recorded_releases_only(self) -> None:
        self.write_config(keep=2)
        (self.dest / "releases" / "manual").mkdir(parents=True)
        for index, tag in enumerate(("v1.0.0", "v2.0.0", "v3.0.0"), start=1):
            self.add_release(tag, f"2026-0{index}-01T00:00:00Z")
            self.run_hook(DOTFILES_RELEASE_SYNC_FORCE="1")

        self.assert_current("v3.0.0")
        self.assertEqual(
            sorted(path.name for path in (self.dest / "releases").iterdir()),
            ["manual", "v2.0.0", "v3.0.0"],
        )
        self.assertEqual([record["tag"] for record in self.state()["installed"]], ["v2.0.0", "v3.0.0"])

    def test_manual_pin_survives_checks_and_pruning_until_next_release(self) -> None:
        self.write_config(keep=3)
        for index, tag in enumerate(("v1.0.0", "v2.0.0", "v3.0.0"), start=1):
            self.add_release(tag, f"2026-0{index}-01T00:00:00Z")
            self.run_hook(DOTFILES_RELEASE_SYNC_FORCE="1")
        current = self.dest / "current"
        os.rmdir(current)
        subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(current), str(self.dest / "releases" / "v1.0.0")],
            check=True, stdout=subprocess.DEVNULL,
        )
        self.write_config(keep=1)

        self.run_hook(DOTFILES_RELEASE_SYNC_FORCE="1")

        self.assert_current("v1.0.0")
        self.assertEqual(sorted(path.name for path in (self.dest / "releases").iterdir()), ["v1.0.0", "v3.0.0"])

        self.add_release("v4.0.0", "2026-04-01T00:00:00Z")
        self.run_hook(DOTFILES_RELEASE_SYNC_FORCE="1")

        self.assert_current("v4.0.0")
        self.assertEqual(sorted(path.name for path in (self.dest / "releases").iterdir()), ["v4.0.0"])

    def test_missing_current_is_recreated_on_up_to_date_check(self) -> None:
        self.add_release("v1.0.0", "2026-01-01T00:00:00Z")
        self.write_config()
        self.run_hook()
        os.rmdir(self.dest / "current")

        self.run_hook(DOTFILES_RELEASE_SYNC_FORCE="1")

        self.assert_current("v1.0.0")
        self.assertEqual(len((self.fixtures / "downloads.log").read_text(encoding="utf-8").splitlines()), 1)

    def test_dry_run_does_not_recreate_missing_current(self) -> None:
        self.add_release("v1.0.0", "2026-01-01T00:00:00Z")
        self.write_config()
        self.run_hook()
        os.rmdir(self.dest / "current")
        state_before = self.state_path.read_bytes()

        result = self.run_hook(DOTFILES_RELEASE_SYNC_FORCE="1", DOTFILES_RELEASE_SYNC_DRY_RUN="1")

        self.assertIn("dry run: would recreate", result.stdout)
        self.assertFalse(is_reparse_point(self.dest / "current"))
        self.assertFalse((self.dest / "current").exists())
        self.assertEqual(self.state_path.read_bytes(), state_before)

    def test_leftover_staging_and_retired_folders_are_swept(self) -> None:
        self.add_release("v1.0.0", "2026-01-01T00:00:00Z")
        self.write_config()
        leftovers = [
            self.dest / "releases" / (".staging-" + "a" * 32),
            self.dest / "releases" / (".retired-" + "b" * 32),
        ]
        unrelated = self.dest / "releases" / ".staging-mine"
        for directory in (*leftovers, unrelated):
            directory.mkdir(parents=True)
            (directory / "file.txt").write_text("x", encoding="utf-8")

        self.run_hook()

        for directory in leftovers:
            self.assertFalse(directory.exists(), directory)
        self.assertTrue(unrelated.exists())
        self.assert_current("v1.0.0")

    def test_prune_honours_a_relative_symlink_pin(self) -> None:
        self.write_config(keep=3)
        for index, tag in enumerate(("v1.0.0", "v2.0.0", "v3.0.0"), start=1):
            self.add_release(tag, f"2026-0{index}-01T00:00:00Z")
            self.run_hook(DOTFILES_RELEASE_SYNC_FORCE="1")
        current = self.dest / "current"
        os.rmdir(current)
        try:
            os.symlink(Path("releases") / "v1.0.0", current, target_is_directory=True)
        except OSError as error:
            self.skipTest(f"directory symlinks unavailable: {error}")
        self.write_config(keep=1)

        self.run_hook(DOTFILES_RELEASE_SYNC_FORCE="1")

        self.assertEqual(sorted(path.name for path in (self.dest / "releases").iterdir()), ["v1.0.0", "v3.0.0"])

    def test_unrecorded_release_folder_is_not_overwritten(self) -> None:
        (self.dest / "releases" / "v1.0.0").mkdir(parents=True)
        (self.dest / "releases" / "v1.0.0" / "mine.txt").write_text("mine", encoding="utf-8")
        self.add_release("v1.0.0", "2026-01-01T00:00:00Z")
        self.write_config()

        result = self.run_hook()

        self.assertIn("was not installed by release-sync", result.stdout)
        self.assertEqual(
            [path.name for path in (self.dest / "releases" / "v1.0.0").iterdir()],
            ["mine.txt"],
        )

    def test_replaced_asset_on_same_tag_is_reinstalled(self) -> None:
        self.add_release("v1.0.0", "2026-01-01T00:00:00Z", files={"tool.exe": b"first"})
        self.write_config()
        self.run_hook()

        self.add_release("v1.0.0", "2026-01-01T00:00:00Z", files={"tool.exe": b"second"})
        self.run_hook(DOTFILES_RELEASE_SYNC_FORCE="1")

        self.assert_current("v1.0.0")
        self.assertEqual((self.dest / "current" / "tool.exe").read_bytes(), b"second")
        self.assertEqual(
            sorted(path.name for path in (self.dest / "releases").iterdir()),
            ["v1.0.0"],
        )

    def test_failed_same_tag_reinstall_keeps_the_previous_release(self) -> None:
        self.add_release("v1.0.0", "2026-01-01T00:00:00Z", files={"tool.exe": b"first"})
        self.write_config()
        self.run_hook()

        self.add_release("v1.0.0", "2026-01-01T00:00:00Z", files={"tool.exe": b"second"}, digest="wrong")
        result = self.run_hook(DOTFILES_RELEASE_SYNC_FORCE="1")

        self.assertIn("failed verification", result.stdout)
        self.assert_current("v1.0.0")
        self.assertEqual((self.dest / "current" / "tool.exe").read_bytes(), b"first")
        self.assertEqual(sorted(path.name for path in (self.dest / "releases").iterdir()), ["v1.0.0"])

    def test_post_install_defers_then_records_success_without_network(self) -> None:
        post_install = self.root / "post.ps1"
        post_install.write_text(POST_INSTALL, encoding="utf-8")
        (self.fixtures / "post-codes.txt").write_text("75,0", encoding="utf-8")
        self.add_release("v1.0.0", "2026-01-01T00:00:00Z")
        self.write_config(post_install=str(post_install))

        first = self.run_hook()
        self.assertIn("post_install deferred for v1.0.0", first.stdout)
        self.assertIsNone(self.state()["post_install_done_tag"])

        (self.fixtures / "api-fail").write_text("", encoding="utf-8")
        second = self.run_hook()
        self.assertIn("post_install finished for v1.0.0", second.stdout)
        self.assertEqual(self.state()["post_install_done_tag"], "v1.0.0")
        self.assertEqual(self.api_calls(), 1)

        self.run_hook()
        self.assertEqual((self.fixtures / "post-count.txt").read_text(encoding="utf-8").strip(), "2")
        release_dir, tag, dest = (self.fixtures / "post-args.log").read_text(encoding="utf-8").splitlines()[0].split("|")
        self.assertEqual(Path(release_dir), self.dest / "releases" / "v1.0.0")
        self.assertEqual(tag, "v1.0.0")
        self.assertEqual(Path(dest), self.dest)

    def test_post_install_failure_warns_and_retries(self) -> None:
        post_install = self.root / "post.ps1"
        post_install.write_text(POST_INSTALL, encoding="utf-8")
        (self.fixtures / "post-codes.txt").write_text("3,0", encoding="utf-8")
        self.add_release("v1.0.0", "2026-01-01T00:00:00Z")
        self.write_config(post_install=str(post_install))

        self.assertIn("post_install exited 3 for v1.0.0", self.run_hook().stdout)
        self.assertIn("post_install finished", self.run_hook().stdout)

    def test_dry_run_writes_nothing(self) -> None:
        self.add_release("v1.0.0", "2026-01-01T00:00:00Z")
        self.write_config()

        result = self.run_hook(DOTFILES_RELEASE_SYNC_DRY_RUN="1")

        self.assertIn("dry run: would install v1.0.0", result.stdout)
        self.assertFalse(self.dest.exists())
        self.assertFalse(self.state_path.exists())
        self.assertFalse((self.fixtures / "downloads.log").exists())

    def test_api_failure_warns_and_retries_on_next_apply(self) -> None:
        (self.fixtures / "api-fail").write_text("", encoding="utf-8")
        self.add_release("v1.0.0", "2026-01-01T00:00:00Z")
        self.write_config()

        result = self.run_hook()
        self.assertIn("could not query owner/repo releases", result.stdout)
        self.assertIsNone(self.state()["last_check_utc"])

        (self.fixtures / "api-fail").unlink()
        self.run_hook()
        self.assert_current("v1.0.0")

    def test_missing_dest_parent_skips_entry(self) -> None:
        self.add_release("v1.0.0", "2026-01-01T00:00:00Z")
        self.write_config(dest=str(self.root / "unmounted" / "example"))

        result = self.run_hook()

        self.assertIn("does not exist; skipping", result.stdout)
        self.assertEqual(self.api_calls(), 0)
        self.assertFalse((self.root / "unmounted").exists())

    def test_invalid_entries_are_skipped_and_valid_entries_still_run(self) -> None:
        self.add_release("v1.0.0", "2026-01-01T00:00:00Z")
        valid = {
            "name": "example",
            "repo": REPO,
            "channel": "prerelease",
            "asset_pattern": r"^Bundle.*\.zip$",
            "dest": str(self.dest),
        }
        self.write_raw_config({
            "schema_version": 1,
            "releases": [
                {**valid, "name": "typo", "post-install": "x"},
                {**valid, "name": "relative", "dest": "relative\\path"},
                {**valid, "name": "badchannel", "channel": "nightly"},
                valid,
                {**valid, "name": "duplicate-dest"},
            ],
        })

        result = self.run_hook()

        self.assertIn("unknown key 'post-install'", result.stdout)
        self.assertIn("dest must be an absolute path", result.stdout)
        self.assertIn("channel must be 'prerelease' or 'stable'", result.stdout)
        self.assertIn("duplicate-dest: duplicate name or dest", result.stdout)
        self.assert_current("v1.0.0")
        self.assertEqual(self.api_calls(), 1)

    def test_malformed_config_warns_without_failing(self) -> None:
        self.config_path.write_text("{not json", encoding="utf-8")

        result = self.run_hook()

        self.assertIn("could not parse", result.stdout)
        self.assertEqual(self.api_calls(), 0)


if __name__ == "__main__":
    unittest.main()
