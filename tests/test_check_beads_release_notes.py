from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "assets/check-beads-release-notes.py"
NPM_PIN_PATH = Path(
    "private_Documents/development/container-dotfiles/devcontainers/"
    "gitlab.com/servers-homelab/homelab-IaC/configs/npm_packages.txt"
)


def load_helper_module() -> Any:
    spec = importlib.util.spec_from_file_location("beads_release_guard", SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


HELPER = load_helper_module()


def release(
    tag: str,
    *,
    name: str | None = "Routine release",
    body: str | None = "Dependency updates and bug fixes.",
    draft: bool = False,
) -> dict[str, Any]:
    return {
        "tag_name": tag,
        "name": name,
        "body": body,
        "draft": draft,
        "published_at": "2026-08-23T00:00:00Z",
        "html_url": f"https://github.com/gastownhall/beads/releases/tag/{tag}",
    }


class BeadsReleaseNotesHelperTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.repo_root = Path(self.temporary_directory.name)
        (self.repo_root / NPM_PIN_PATH).parent.mkdir(parents=True)
        self.write_pins()

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def write_pins(
        self,
        host: str = 'beads_version: "1.2.2"',
        npm: str = "@beads/bd@1.2.2",
    ) -> None:
        (self.repo_root / ".chezmoidata.yaml").write_text(
            host + ("\n" if host else ""), encoding="utf-8"
        )
        (self.repo_root / NPM_PIN_PATH).write_text(
            npm + ("\n" if npm else ""), encoding="utf-8"
        )

    def run_helper(
        self, releases: Any, *, raw_json: str | None = None
    ) -> subprocess.CompletedProcess[str]:
        releases_path = self.repo_root / "releases.json"
        releases_path.write_text(
            raw_json if raw_json is not None else json.dumps(releases),
            encoding="utf-8",
        )
        return subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--repo-root",
                str(self.repo_root),
                "--releases-file",
                str(releases_path),
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )

    def test_no_successor_ignores_proposed_recovery_notes(self) -> None:
        completed = self.run_helper(
            [
                release(
                    "v1.2.2",
                    name="Recovery release",
                    body="Recovers from corruption and retracts v1.2.1.",
                )
            ]
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("pins agree at 1.2.2", completed.stdout)
        self.assertIn("no published Beads releases are newer", completed.stdout)

    def test_pin_drift_fails_closed(self) -> None:
        self.write_pins(npm="@beads/bd@1.2.3")

        completed = self.run_helper([release("v1.2.2")])

        self.assertEqual(completed.returncode, 2)
        self.assertIn("Beads pins disagree", completed.stderr)

    def test_missing_duplicate_and_malformed_pins_fail_closed(self) -> None:
        cases = (
            ("", "@beads/bd@1.2.2", "active beads_version"),
            (
                'beads_version: "1.2.2"\nbeads_version: "1.2.3"',
                "@beads/bd@1.2.2",
                "active beads_version",
            ),
            ("beads_version: value with spaces", "@beads/bd@1.2.2", "malformed"),
            ('beads_version: "1.2"', "@beads/bd@1.2.2", "invalid beads_version"),
            ('beads_version: "1.2.2"', "", "active @beads/bd"),
            (
                'beads_version: "1.2.2"',
                "@beads/bd@1.2.2\n@beads/bd@1.2.3",
                "active @beads/bd",
            ),
            ('beads_version: "1.2.2"', "@beads/bd@latest", "invalid @beads/bd"),
        )
        for host, npm, message in cases:
            with self.subTest(host=host, npm=npm):
                self.write_pins(host=host, npm=npm)
                completed = self.run_helper([release("v1.2.2")])
                self.assertEqual(completed.returncode, 2)
                self.assertIn(message, completed.stderr)

    def test_semver_orders_prereleases_and_stable_releases(self) -> None:
        self.write_pins(
            host='beads_version: "1.2.3-rc.1"', npm="@beads/bd@1.2.3-rc.1"
        )
        completed = self.run_helper(
            [
                release("v1.2.4-alpha.1"),
                release("v1.2.3"),
                release("v1.2.3-rc.10"),
                release("v1.2.3-rc.2"),
                release("v1.2.3-rc.1"),
                release("v1.2.3-beta.9"),
            ]
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        output = completed.stdout
        self.assertLess(output.index("v1.2.3-rc.2"), output.index("v1.2.3-rc.10"))
        self.assertLess(output.index("v1.2.3-rc.10"), output.index("v1.2.3 published"))
        self.assertLess(output.index("v1.2.3 published"), output.index("v1.2.4-alpha.1"))
        self.assertNotIn("v1.2.3-beta.9 published", output)

    def test_each_hazard_family_blocks_a_newer_release(self) -> None:
        cases = (
            "possible data-loss during migration",
            "database CORRUPTION was observed",
            "this version retracts the prior release",
            "recovery instructions are available",
        )
        for body in cases:
            with self.subTest(body=body):
                completed = self.run_helper(
                    [release("v1.2.2"), release("v1.2.3-rc.1", body=body)]
                )
                self.assertEqual(completed.returncode, 1)
                self.assertIn("v1.2.3-rc.1", completed.stdout)
                self.assertIn("require manual incident review", completed.stderr)

    def test_clean_successor_passes_and_nullable_notes_are_accepted(self) -> None:
        completed = self.run_helper(
            [release("v1.2.2"), release("v1.2.3", name=None, body=None)]
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("1 newer Beads release(s)", completed.stdout)

    def test_drafts_are_ignored_and_non_semver_tags_warn(self) -> None:
        completed = self.run_helper(
            [
                release("v1.2.2"),
                release("v1.2.3", body="data loss", draft=True),
                release("nightly", body="data loss"),
            ]
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("ignoring non-SemVer", completed.stderr)
        self.assertIn("no published Beads releases are newer", completed.stdout)

    def test_missing_proposed_release_fails_closed(self) -> None:
        completed = self.run_helper([release("v1.2.3")])

        self.assertEqual(completed.returncode, 2)
        self.assertIn("absent from GitHub releases", completed.stderr)

    def test_duplicate_semver_release_fails_closed(self) -> None:
        completed = self.run_helper(
            [release("v1.2.2+build.1"), release("v1.2.2+build.2")]
        )

        self.assertEqual(completed.returncode, 2)
        self.assertIn("multiple published releases", completed.stderr)

    def test_malformed_release_data_and_json_fail_closed(self) -> None:
        malformed_record = release("v1.2.2")
        malformed_record["body"] = ["not", "text"]
        for releases, raw_json, message in (
            ({"not": "an array"}, None, "must be a JSON array"),
            ([malformed_record], None, "invalid body"),
            (None, "{broken", "invalid JSON"),
        ):
            with self.subTest(message=message):
                completed = self.run_helper(releases, raw_json=raw_json)
                self.assertEqual(completed.returncode, 2)
                self.assertIn(message, completed.stderr)

    def test_hazard_excerpt_removes_terminal_control_characters(self) -> None:
        completed = self.run_helper(
            [
                release("v1.2.2"),
                release("v1.2.3", body="prefix \x1b[31mdata loss\x00 suffix"),
            ]
        )

        self.assertEqual(completed.returncode, 1)
        self.assertNotIn("\x1b", completed.stderr)
        self.assertNotIn("\x00", completed.stderr)
        self.assertIn("data loss", completed.stderr)


class FakeResponse:
    def __init__(self, payload: Any, *, url: str, link: str | None = None) -> None:
        self.payload = json.dumps(payload).encode("utf-8")
        self.url = url
        self.headers = {"Link": link} if link else {}

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *args: Any) -> None:
        return None

    def geturl(self) -> str:
        return self.url

    def read(self) -> bytes:
        return self.payload


class FakeOpener:
    def __init__(
        self,
        responses: list[FakeResponse] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.responses = list(responses or [])
        self.error = error
        self.requests: list[str] = []

    def open(self, request: Any, *, timeout: float) -> FakeResponse:
        del timeout
        self.requests.append(request.full_url)
        if self.error:
            raise self.error
        return self.responses.pop(0)


class BeadsReleaseFetchTests(unittest.TestCase):
    def test_fetch_follows_trusted_pagination(self) -> None:
        page_two = HELPER.API_URL + "&page=2"
        opener = FakeOpener(
            [
                FakeResponse(
                    [release("v1.2.2")],
                    url=HELPER.API_URL,
                    link=f'<{page_two}>; rel="next"',
                ),
                FakeResponse([release("v1.2.3")], url=page_two),
            ]
        )

        releases = HELPER.fetch_releases(1.0, opener=opener)

        self.assertEqual(len(releases), 2)
        self.assertEqual(opener.requests, [HELPER.API_URL, page_two])

    def test_fetch_rejects_untrusted_pagination_and_redirects(self) -> None:
        opener = FakeOpener(
            [
                FakeResponse(
                    [release("v1.2.2")],
                    url=HELPER.API_URL,
                    link='<https://example.com/releases?page=2>; rel="next"',
                )
            ]
        )
        with self.assertRaisesRegex(HELPER.GuardError, "outside the Beads GitHub API"):
            HELPER.fetch_releases(1.0, opener=opener)

        handler = HELPER.RejectRedirects()
        with self.assertRaisesRegex(HELPER.GuardError, "redirected unexpectedly"):
            handler.redirect_request(None, None, 302, "Found", {}, HELPER.API_URL)

    def test_fetch_reports_rate_limit_exhaustion(self) -> None:
        error = urllib.error.HTTPError(
            HELPER.API_URL,
            403,
            "Forbidden",
            {"X-RateLimit-Remaining": "0"},
            None,
        )
        try:
            with self.assertRaisesRegex(HELPER.GuardError, "rate limit exhausted"):
                HELPER.fetch_releases(1.0, opener=FakeOpener(error=error))
        finally:
            error.close()


class BeadsRenovatePolicyTests(unittest.TestCase):
    def test_final_beads_rule_overrides_generic_grouping_and_automerge(self) -> None:
        renovate = (REPO_ROOT / "renovate.json5").read_text(encoding="utf-8")
        rule_start = renovate.rfind(
            '"matchDepNames": ["@beads/bd", "gastownhall/beads"]'
        )
        self.assertGreater(
            rule_start,
            renovate.rfind('"groupName": "promptfoo devcontainer runtime"'),
        )
        rule = renovate[rule_start:]

        self.assertIn('"minimumReleaseAge": "14 days"', rule)
        self.assertIn('"minimumGroupSize": 2', rule)
        self.assertIn('"groupName": "beads clients"', rule)
        self.assertIn('"groupSlug": "clients"', rule)
        self.assertIn('"additionalBranchPrefix": "beads-core-"', rule)
        self.assertIn('"platformAutomerge": false', rule)
        self.assertIn(
            '"allowedVersions": "!/^(1\\\\.0\\\\.5|1\\\\.2\\\\.0|1\\\\.2\\\\.1|1\\\\.3\\\\.0)$/"',
            rule,
        )
        self.assertNotIn('"allowedVersions": "!/^1\\\\.0\\\\.5$/"', renovate)

        allowed_versions_match = re.search(
            r'"allowedVersions":\s*("(?:\\.|[^"\\])*")', rule
        )
        self.assertIsNotNone(allowed_versions_match)
        allowed_versions = json.loads(allowed_versions_match.group(1))
        self.assertTrue(allowed_versions.startswith("!/"))
        self.assertTrue(allowed_versions.endswith("/"))
        excluded_versions = re.compile(allowed_versions[2:-1])
        for version in ("1.0.5", "1.2.0", "1.2.1", "1.3.0"):
            with self.subTest(blocked_version=version):
                self.assertRegex(version, excluded_versions)
        for version in ("1.2.2", "1.3.1", "1.3.10"):
            with self.subTest(allowed_version=version):
                self.assertNotRegex(version, excluded_versions)

    def test_ci_dispatch_is_narrow_and_excludes_beads_kanban(self) -> None:
        pipeline = (REPO_ROOT / ".gitlab-ci.yml").read_text(encoding="utf-8")
        self.assertIn("beads-release-notes:", pipeline)
        self.assertIn(
            '$CI_MERGE_REQUEST_SOURCE_BRANCH_NAME =~ /^renovate\\/beads-core-/',
            pipeline,
        )
        self.assertIn('$CI_COMMIT_BRANCH =~ /^renovate\\/beads-core-/', pipeline)
        self.assertEqual(pipeline.count("^renovate\\/beads-core-"), 2)

        dispatch = re.compile(r"^renovate/beads-core-")
        self.assertRegex("renovate/beads-core-clients", dispatch)
        self.assertNotRegex("renovate/beads-kanban-vsix", dispatch)


if __name__ == "__main__":
    unittest.main()
