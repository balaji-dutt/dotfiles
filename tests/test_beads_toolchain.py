from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import json
import os
import shlex
import subprocess
import sys
import tarfile
import tempfile
import unittest
import urllib.error
from pathlib import Path
from typing import Any
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "assets/beads-toolchain.py"


def load_module() -> Any:
    spec = importlib.util.spec_from_file_location("beads_toolchain", SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


TOOLCHAIN = load_module()
BD = TOOLCHAIN.TOOLS["bd"]
PINS = (
    'beads_version: "1.3.4" # renovate: datasource=github-releases\n'
    "other_version: \"9.9.9\"\n"
    "dolt_version: '2.7.3'\n"
)


class FakeResponse:
    def __init__(self, body: bytes, url: str) -> None:
        self.body = io.BytesIO(body)
        self.url = url

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *args: Any) -> None:
        return None

    def geturl(self) -> str:
        return self.url

    def read(self, size: int = -1) -> bytes:
        return self.body.read(size)


class FakeOpener:
    def __init__(self, response: FakeResponse | None = None, error: Exception | None = None) -> None:
        self.response = response
        self.error = error
        self.requests: list[Any] = []

    def open(self, request: Any, *, timeout: float) -> FakeResponse:
        del timeout
        self.requests.append(request)
        if self.error:
            raise self.error
        assert self.response is not None
        return self.response


def release_payload(name: str, **overrides: Any) -> bytes:
    asset = {
        "name": name,
        "digest": "sha256:" + "a" * 64,
        "browser_download_url": (
            f"https://github.com/gastownhall/beads/releases/download/v1.3.4/{name}"
        ),
        **overrides,
    }
    return json.dumps({"assets": [{"name": "checksums.txt"}, asset]}).encode("utf-8")


def make_archive(path: Path, members: dict[str, bytes | None]) -> str:
    with tarfile.open(path, "w:gz") as tar:
        for name, content in members.items():
            info = tarfile.TarInfo(name)
            if content is None or content in (tarfile.LNKTYPE, tarfile.CHRTYPE):
                info.type = content or tarfile.SYMTYPE
                info.linkname = "/bin/sh"
                tar.addfile(info)
            else:
                info.size = len(content)
                info.mode = 0o755
                tar.addfile(info, io.BytesIO(content))
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fake_binary(version: str) -> bytes:
    return (
        "#!/bin/sh\n"
        f"echo 'bd version {version} (abc123)'\n"
        "echo 'Warning: you are on an old version. The newest version is 9.9.9.'\n"
    ).encode("utf-8")


class PinTests(unittest.TestCase):
    def test_pins_are_parsed_from_text_and_from_a_git_ref(self) -> None:
        self.assertEqual(TOOLCHAIN.parse_pins(PINS, "x"), {"bd": "1.3.4", "dolt": "2.7.3"})

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
            (root / ".chezmoidata.yaml").write_text(PINS, encoding="utf-8")
            for argv in (
                ["git", "init", "-q", "-b", "main"],
                ["git", "add", ".chezmoidata.yaml"],
                [
                    "git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                    "commit", "-q", "-m", "pins",
                ],
            ):
                subprocess.run(argv, cwd=root, env=env, check=True, capture_output=True)
            (root / ".chezmoidata.yaml").write_text(
                PINS.replace("1.3.4", "1.4.0"), encoding="utf-8"
            )

            for ref in ("HEAD", "HEAD~0", "main^0"):
                with self.subTest(ref=ref):
                    self.assertEqual(TOOLCHAIN.read_pins(root, ref)["bd"], "1.3.4")
            self.assertEqual(TOOLCHAIN.read_pins(root, None)["bd"], "1.4.0")
            with self.assertRaisesRegex(TOOLCHAIN.ToolchainError, "git could not read"):
                TOOLCHAIN.read_pins(root, "origin/missing")

    def test_untrusted_pins_and_refs_are_refused(self) -> None:
        cases = (
            (PINS.replace("dolt_version", "dolt_pin"), "exactly one dolt_version"),
            (PINS + 'beads_version: "1.3.5"\n', "exactly one beads_version"),
            (PINS.replace("1.3.4", "1.3"), "invalid beads_version version"),
            (PINS.replace("2.7.3", "2.7.3;rm"), "invalid dolt_version version"),
        )
        for text, message in cases:
            with self.subTest(message=message):
                with self.assertRaisesRegex(TOOLCHAIN.ToolchainError, message):
                    TOOLCHAIN.parse_pins(text, "x")
        self.assertEqual(
            TOOLCHAIN.parse_pins(PINS.replace("\n", "\r\n"), "x")["dolt"], "2.7.3"
        )
        for value in ("\u0661.\u0663.\u0664", "1.3.4\n", "v1.3.4"):
            with self.subTest(value=value):
                with self.assertRaisesRegex(TOOLCHAIN.ToolchainError, "invalid bd version"):
                    TOOLCHAIN.require_version(value, "bd")
        for ref in ("--upload-pack=x", "main:other", "a b"):
            with self.subTest(ref=ref):
                with self.assertRaisesRegex(TOOLCHAIN.ToolchainError, "refusing git ref"):
                    TOOLCHAIN.read_pins(REPO_ROOT, ref)


class AssetLookupTests(unittest.TestCase):
    NAME = "beads_1.3.4_linux_amd64.tar.gz"

    def lookup(self, payload: bytes) -> tuple[str, str]:
        opener = FakeOpener(FakeResponse(payload, TOOLCHAIN.release_api_url(BD, "1.3.4")))
        result = TOOLCHAIN.find_asset(BD, "1.3.4", self.NAME, 1.0, opener=opener)
        self.assertEqual(
            opener.requests[0].full_url,
            "https://api.github.com/repos/gastownhall/beads/releases/tags/v1.3.4",
        )
        return result

    def test_asset_names_follow_upstream_layouts(self) -> None:
        self.assertEqual(TOOLCHAIN.asset_name(BD, "1.3.4", "linux", "amd64"), self.NAME)
        self.assertEqual(
            TOOLCHAIN.asset_name(TOOLCHAIN.TOOLS["dolt"], "2.7.3", "darwin", "arm64"),
            "dolt-darwin-arm64.tar.gz",
        )

    def test_published_digest_and_canonical_url_are_returned(self) -> None:
        url, sha256 = self.lookup(release_payload(self.NAME))
        self.assertEqual(sha256, "a" * 64)
        self.assertTrue(url.endswith(f"/v1.3.4/{self.NAME}"))

    def test_missing_digest_asset_or_foreign_url_is_refused(self) -> None:
        cases = (
            (release_payload(self.NAME, digest=None), "no sha256 digest"),
            (release_payload(self.NAME, digest="md5:abc"), "no sha256 digest"),
            (release_payload("other.tar.gz"), "publishes no asset named"),
            (
                release_payload(self.NAME, browser_download_url="https://evil.example/x"),
                "unexpected download URL",
            ),
            (b"[]", "has no assets"),
            (b"{broken", "request for gastownhall/beads failed"),
        )
        for payload, message in cases:
            with self.subTest(message=message):
                with self.assertRaisesRegex(TOOLCHAIN.ToolchainError, message):
                    self.lookup(payload)

    def test_token_goes_to_the_api_and_never_to_the_download(self) -> None:
        patched = mock.patch.dict(os.environ, {"GITHUB_TOKEN": "secret-token"})
        patched.start()
        self.addCleanup(patched.stop)

        api = FakeOpener(
            FakeResponse(release_payload(self.NAME), TOOLCHAIN.release_api_url(BD, "1.3.4"))
        )
        url, _ = TOOLCHAIN.find_asset(BD, "1.3.4", self.NAME, 1.0, opener=api)
        self.assertEqual(api.requests[0].get_header("Authorization"), "Bearer secret-token")

        body = b"x"
        fetch = FakeOpener(FakeResponse(body, url))
        with tempfile.TemporaryDirectory() as tmp:
            TOOLCHAIN.download(
                url, hashlib.sha256(body).hexdigest(), Path(tmp) / "a", 1.0, opener=fetch
            )
        headers = {key.lower(): value for key, value in fetch.requests[0].header_items()}
        self.assertNotIn("authorization", headers)
        self.assertNotIn("secret-token", " ".join(headers.values()))

        with mock.patch.dict(os.environ, {"GH_TOKEN": "fallback-token"}):
            os.environ.pop("GITHUB_TOKEN")
            fallback = FakeOpener(
                FakeResponse(release_payload(self.NAME), TOOLCHAIN.release_api_url(BD, "1.3.4"))
            )
            TOOLCHAIN.find_asset(BD, "1.3.4", self.NAME, 1.0, opener=fallback)
        self.assertEqual(
            fallback.requests[0].get_header("Authorization"), "Bearer fallback-token"
        )

    def test_redirects_are_refused_for_the_api_and_limited_for_downloads(self) -> None:
        request = TOOLCHAIN.urllib.request.Request(DownloadTests.URL)
        with self.assertRaisesRegex(TOOLCHAIN.ToolchainError, "redirected unexpectedly"):
            TOOLCHAIN.NoRedirects().redirect_request(
                request, None, 302, "Found", {}, "https://api.github.com/other"
            )
        with self.assertRaisesRegex(TOOLCHAIN.ToolchainError, "outside GitHub"):
            TOOLCHAIN.DownloadRedirects().redirect_request(
                request, None, 302, "Found", {}, "https://evil.example/x"
            )
        followed = TOOLCHAIN.DownloadRedirects().redirect_request(
            request, None, 302, "Found", {},
            "https://release-assets.githubusercontent.com/x?sig=1",
        )
        self.assertEqual(followed.host, "release-assets.githubusercontent.com")

    def test_api_errors_are_reported_without_a_traceback(self) -> None:
        error = urllib.error.HTTPError("u", 404, "Not Found", {}, io.BytesIO())
        try:
            with self.assertRaisesRegex(TOOLCHAIN.ToolchainError, "HTTP 404"):
                TOOLCHAIN.find_asset(BD, "9.9.9", self.NAME, 1.0, opener=FakeOpener(error=error))
        finally:
            error.close()


class DownloadTests(unittest.TestCase):
    URL = "https://github.com/gastownhall/beads/releases/download/v1.3.4/a.tar.gz"

    def test_download_urls_are_limited_to_github_release_hosts(self) -> None:
        for url in (
            self.URL,
            "https://release-assets.githubusercontent.com/x?sig=1",
            "https://objects.githubusercontent.com/x",
        ):
            self.assertEqual(TOOLCHAIN.require_download_url(url), url)
        for url in (
            "http://github.com/x",
            "https://github.com.evil.example/x",
            "https://user@github.com/x",
            "https://github.com:8443/x",
            "file:///etc/passwd",
        ):
            with self.subTest(url=url):
                with self.assertRaisesRegex(TOOLCHAIN.ToolchainError, "outside GitHub"):
                    TOOLCHAIN.require_download_url(url)

    def test_download_verifies_digest_final_url_and_size(self) -> None:
        body = b"archive bytes"
        good = hashlib.sha256(body).hexdigest()
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "a.tar.gz"
            TOOLCHAIN.download(
                self.URL, good, target, 1.0, opener=FakeOpener(FakeResponse(body, self.URL))
            )
            self.assertEqual(target.read_bytes(), body)

            with self.assertRaisesRegex(TOOLCHAIN.ToolchainError, "sha256 mismatch"):
                TOOLCHAIN.download(
                    self.URL, "b" * 64, target, 1.0,
                    opener=FakeOpener(FakeResponse(body, self.URL)),
                )
            with self.assertRaisesRegex(TOOLCHAIN.ToolchainError, "outside GitHub"):
                TOOLCHAIN.download(
                    self.URL, good, target, 1.0,
                    opener=FakeOpener(FakeResponse(body, "https://evil.example/a")),
                )
            for error, message in (
                (urllib.error.HTTPError(self.URL, 404, "Not Found", {}, io.BytesIO()), "HTTP 404"),
                (urllib.error.URLError("connection reset"), "failed: .*connection reset"),
            ):
                with self.subTest(message=message):
                    try:
                        with self.assertRaisesRegex(TOOLCHAIN.ToolchainError, message):
                            TOOLCHAIN.download(
                                self.URL, good, target, 1.0, opener=FakeOpener(error=error)
                            )
                    finally:
                        if isinstance(error, urllib.error.HTTPError):
                            error.close()
            original = TOOLCHAIN.MAX_ARCHIVE_BYTES
            TOOLCHAIN.MAX_ARCHIVE_BYTES = 4
            self.addCleanup(setattr, TOOLCHAIN, "MAX_ARCHIVE_BYTES", original)
            with self.assertRaisesRegex(TOOLCHAIN.ToolchainError, "larger than"):
                TOOLCHAIN.download(
                    self.URL, good, target, 1.0,
                    opener=FakeOpener(FakeResponse(body, self.URL)),
                )


class ExtractAndInstallTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_only_one_regular_file_with_the_tool_name_is_extracted(self) -> None:
        archive = self.root / "a.tar.gz"
        make_archive(archive, {"dolt-linux-amd64/bin/bd": b"payload", "../escape": b"x"})
        target = self.root / "bd"
        TOOLCHAIN.extract_binary(archive, "bd", target)
        self.assertEqual(target.read_bytes(), b"payload")
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), ["a.tar.gz", "bd"])

        for members, message in (
            ({"README": b"x"}, "found 0"),
            ({"bd": b"a", "other/bd": b"b"}, "found 2"),
            ({"bd": None}, "found 0"),
            ({"bd": tarfile.LNKTYPE}, "found 0"),
            ({"bd": tarfile.CHRTYPE}, "found 0"),
        ):
            with self.subTest(members=list(members)):
                make_archive(archive, members)
                with self.assertRaisesRegex(TOOLCHAIN.ToolchainError, message):
                    TOOLCHAIN.extract_binary(archive, "bd", target)

        make_archive(archive, {"bd": b"0123456789"})
        original = TOOLCHAIN.MAX_BINARY_BYTES
        TOOLCHAIN.MAX_BINARY_BYTES = 4
        self.addCleanup(setattr, TOOLCHAIN, "MAX_BINARY_BYTES", original)
        with self.assertRaisesRegex(TOOLCHAIN.ToolchainError, "unexpectedly large"):
            TOOLCHAIN.extract_binary(archive, "bd", target)

        archive.write_bytes(b"not a tarball")
        with self.assertRaisesRegex(TOOLCHAIN.ToolchainError, "cannot extract"):
            TOOLCHAIN.extract_binary(archive, "bd", target)

    def stub_network(self, reported: str) -> list[str]:
        source = self.root / "source.tar.gz"
        sha256 = make_archive(source, {"bd": fake_binary(reported)})
        downloads: list[str] = []

        def find_asset(tool: Any, version: str, name: str, timeout: float) -> tuple[str, str]:
            del tool, version, timeout
            return f"https://github.com/x/releases/download/{name}", sha256

        def download(url: str, digest: str, destination: Path, timeout: float) -> None:
            del timeout
            self.assertEqual(digest, sha256)
            downloads.append(url)
            destination.write_bytes(source.read_bytes())

        for name, stub in (
            ("find_asset", find_asset),
            ("download", download),
            ("host_platform", lambda: ("linux", "amd64")),
        ):
            self.addCleanup(setattr, TOOLCHAIN, name, getattr(TOOLCHAIN, name))
            setattr(TOOLCHAIN, name, stub)
        return downloads

    @unittest.skipIf(os.name == "nt", "the fetcher supports linux and macOS only")
    def test_install_verifies_the_reported_version_and_reuses_a_matching_prefix(self) -> None:
        downloads = self.stub_network("1.3.4")
        prefix = self.root / "prefix"
        empty = self.root / "empty"
        empty.mkdir()
        with contextlib.redirect_stdout(io.StringIO()) as stdout:
            binary = TOOLCHAIN.install(BD, "1.3.4", prefix, 1.0)
            again = TOOLCHAIN.install(BD, "1.3.4", prefix, 1.0)
            self.assertEqual(len(downloads), 1)
            self.assertEqual(
                TOOLCHAIN.install(BD, "1.3.4", empty, 1.0), empty / "bin" / "bd"
            )
            self.assertEqual(len(downloads), 2)

        self.assertEqual(binary, prefix / "bin" / "bd")
        self.assertEqual(again, binary)
        self.assertTrue(os.access(binary, os.X_OK))
        self.assertIn("already present", stdout.getvalue())
        self.assertEqual(TOOLCHAIN.reported_version(BD, binary), "1.3.4")
        self.assertEqual(
            sorted(p.name for p in prefix.iterdir()), [".beads-toolchain", "bd.json", "bin"]
        )

    @unittest.skipIf(os.name == "nt", "the fetcher supports linux and macOS only")
    def test_install_downloads_again_when_the_prefix_does_not_match(self) -> None:
        downloads = self.stub_network("1.3.4")
        prefix = self.root / "prefix"
        with contextlib.redirect_stdout(io.StringIO()):
            binary = TOOLCHAIN.install(BD, "1.3.4", prefix, 1.0)

            binary.write_bytes(fake_binary("1.3.3"))
            TOOLCHAIN.install(BD, "1.3.4", prefix, 1.0)
            self.assertEqual(len(downloads), 2)
            self.assertEqual(TOOLCHAIN.reported_version(BD, binary), "1.3.4")

            stamp = prefix / "bd.json"
            stamp.write_text(
                json.dumps({**json.loads(stamp.read_text()), "sha256": "c" * 64}),
                encoding="utf-8",
            )
            TOOLCHAIN.install(BD, "1.3.4", prefix, 1.0)
            self.assertEqual(len(downloads), 3)

    def test_a_prefix_this_helper_did_not_create_is_refused(self) -> None:
        self.stub_network("1.3.4")
        occupied = self.root / "local"
        (occupied / "bin").mkdir(parents=True)
        installed = occupied / "bin" / "bd"
        installed.write_bytes(b"the installed bd")
        with self.assertRaisesRegex(TOOLCHAIN.ToolchainError, "was not created by this helper"):
            TOOLCHAIN.install(BD, "1.3.4", occupied, 1.0)
        self.assertEqual(installed.read_bytes(), b"the installed bd")
        self.assertFalse((occupied / "bd.json").exists())

        a_file = self.root / "file"
        a_file.write_text("x", encoding="utf-8")
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = TOOLCHAIN.main(["fetch", "--bd", "1.3.4", "--prefix", str(a_file)])
        self.assertEqual(code, 2)
        self.assertIn("is not a directory", stderr.getvalue())

    @unittest.skipIf(os.name == "nt", "the fetcher supports linux and macOS only")
    def test_version_probe_runs_with_telemetry_disabled_in_a_private_home(self) -> None:
        probe = self.root / "bd"
        probe.write_text(
            "#!/bin/sh\n"
            'test "$BD_DISABLE_METRICS" = 1 || exit 3\n'
            'grep -qF \'"metrics.disabled": "true"\' "$HOME/.dolt/config_global.json" || exit 4\n'
            f'test "$HOME" != {shlex.quote(str(Path.home()))} || exit 5\n'
            "echo 'bd version 1.3.4 (abc123)'\n",
            encoding="utf-8",
        )
        probe.chmod(0o755)
        self.assertEqual(TOOLCHAIN.reported_version(BD, probe), "1.3.4")

    @unittest.skipIf(os.name == "nt", "the fetcher supports linux and macOS only")
    def test_install_refuses_a_binary_reporting_another_version(self) -> None:
        self.stub_network("1.3.3")
        prefix = self.root / "prefix"
        with self.assertRaisesRegex(TOOLCHAIN.ToolchainError, "reports version 1.3.3"):
            TOOLCHAIN.install(BD, "1.3.4", prefix, 1.0)
        self.assertFalse((prefix / "bin" / "bd").exists())
        self.assertFalse((prefix / "bd.json").exists())

    def test_cli_exit_codes(self) -> None:
        def run(*argv: str) -> tuple[int, str, str]:
            stdout, stderr = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                code = TOOLCHAIN.main(list(argv))
            return code, stdout.getvalue(), stderr.getvalue()

        (self.root / ".chezmoidata.yaml").write_text(PINS, encoding="utf-8")
        code, stdout, _ = run("pins", "--repo-root", str(self.root))
        self.assertEqual((code, json.loads(stdout)), (0, {"bd": "1.3.4", "dolt": "2.7.3"}))

        for argv, message in (
            (("fetch", "--prefix", str(self.root / "p")), "needs --bd, --dolt, or both"),
            (("fetch", "--bd", "latest", "--prefix", str(self.root / "p")), "invalid bd version"),
            (("pins", "--repo-root", str(self.root / "missing")), "cannot read"),
        ):
            with self.subTest(argv=argv):
                code, _, stderr = run(*argv)
                self.assertEqual(code, 2)
                self.assertIn(message, stderr)
        self.assertFalse((self.root / "p").exists())


if __name__ == "__main__":
    unittest.main()
