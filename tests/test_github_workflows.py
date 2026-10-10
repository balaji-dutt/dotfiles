from __future__ import annotations

import re
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "platform-fast.yml"
GITLAB_CI_PATH = REPO_ROOT / ".gitlab-ci.yml"
GITHUB_POLICY_PATH = REPO_ROOT / "configs" / "pipeline-guard.json"
RUNNERS = ("ubuntu-latest", "macos-latest", "windows-latest")


def workflow_text() -> str:
    return WORKFLOW_PATH.read_text(encoding="utf-8")


def gitlab_node_version() -> str:
    match = re.search(r"node-v(\d+\.\d+\.\d+)-linux-x64\.tar\.xz", GITLAB_CI_PATH.read_text(encoding="utf-8"))
    if match is None:
        raise AssertionError("linux-fast no longer pins a Node tarball")
    return match.group(1)


class PlatformFastWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.text = workflow_text()
        self.lines = self.text.splitlines()

    def index_of(self, needle: str) -> int:
        for index, line in enumerate(self.lines):
            if needle in line:
                return index
        raise AssertionError(f"{needle!r} not found in workflow")

    def test_triggers_are_push_and_dispatch_without_renovate(self) -> None:
        self.assertIn("\non:\n  push:\n    branches-ignore:\n      - 'renovate/**'\n  workflow_dispatch:\n", self.text)
        self.assertNotIn("pull_request", self.text)
        self.assertNotIn("schedule:", self.text)

    def test_permissions_and_concurrency_are_restrictive(self) -> None:
        self.assertIn("permissions:\n  contents: read\n", self.text)
        self.assertNotIn("contents: write", self.text)
        self.assertIn("cancel-in-progress: ${{ github.ref != 'refs/heads/main' }}", self.text)
        self.assertIn("persist-credentials: false", self.text)

    def test_matrix_covers_three_runners_without_fail_fast(self) -> None:
        self.assertIn("fail-fast: false", self.text)
        for runner in RUNNERS:
            self.assertIn(runner, self.text)
        self.assertIn("timeout-minutes: 15", self.text)

    def test_only_windows_is_advisory(self) -> None:
        self.assertEqual(self.text.count("continue-on-error:"), 1)
        self.assertIn("continue-on-error: ${{ matrix.os == 'windows-latest' }}", self.text)

    def test_autocrlf_is_disabled_before_checkout_on_windows(self) -> None:
        autocrlf = self.index_of("git config --global core.autocrlf false")
        checkout = self.index_of("uses: actions/checkout@")
        self.assertLess(autocrlf, checkout)
        self.assertIn("if: runner.os == 'Windows'", "\n".join(self.lines[autocrlf - 3 : autocrlf]))

    def test_runtimes_match_the_gitlab_lane(self) -> None:
        self.assertIn("python-version: '3.13'", self.text)
        self.assertIn(f"node-version: '{gitlab_node_version()}'", self.text)

    def test_fast_suite_runs_with_required_capabilities_on_every_shell(self) -> None:
        self.assertIn(
            './assets/run-tests.sh fast --require-capabilities --report-file "ci-artifacts/fast-${{ matrix.os }}.json"',
            self.text,
        )
        self.assertIn(
            'pwsh -NoProfile -File ./assets/run-tests.ps1 fast --require-capabilities --report-file "ci-artifacts/fast-${{ matrix.os }}.json"',
            self.text,
        )
        self.assertNotIn("run-tests.sh all", self.text)
        self.assertNotIn("run-tests.ps1 all", self.text)

    def test_windows_gets_sh_only_when_missing(self) -> None:
        self.assertIn("Get-Command sh -ErrorAction SilentlyContinue", self.text)
        self.assertIn(r"C:\Program Files\Git\usr\bin", self.text)

    def test_reports_are_uploaded_even_on_failure(self) -> None:
        upload = self.index_of("uses: actions/upload-artifact@")
        following = "\n".join(self.lines[upload : upload + 6])
        self.assertIn("if: always()", following)
        self.assertIn("path: ci-artifacts/fast-${{ matrix.os }}.json", following)
        self.assertIn("retention-days: 7", following)

    def test_actions_are_pinned_to_major_tags(self) -> None:
        for line in self.lines:
            if "uses:" in line:
                self.assertRegex(line.strip(), r"^- uses: actions/[a-z-]+@v\d+$", line)

    def test_workflow_does_not_opt_into_gating(self) -> None:
        self.assertFalse(GITHUB_POLICY_PATH.exists(), "configs/pipeline-guard.json would switch the merge helper to GitHub")


if __name__ == "__main__":
    unittest.main()
