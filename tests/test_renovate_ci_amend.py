from __future__ import annotations

import os
import json
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

from tests.test_gitlab_ci import CI_PATH, top_level_block


REPO_ROOT = CI_PATH.parent
SERVICE_NAME = "Renovate Bot"
SERVICE_EMAIL = (
    "service_account_group_65498163_3cf840e6e3ac6e34bec3beb607caff8a"
    "@noreply.gitlab.com"
)
TRAILER = "Co-authored-by: renovate[bot] <29139614+renovate[bot]@users.noreply.github.com>"
TOKEN = "synthetic-fixture-token"
JOBS = {
    "statusline-sync": (
        "renovate/statusline-fixture",
        (
            "dot_claude/executable_statusline.sh",
            "private_Documents/development/container-dotfiles/dotfiles/dot_claude/executable_statusline.sh",
        ),
        "assets/sync-statusline.sh",
    ),
    "beads-kanban-pin-sync": (
        "renovate/beads-kanban-fixture",
        (
            ".chezmoiscripts/run_onchange_after_install_better_beads_kanban.sh.tmpl",
            ".chezmoiscripts/run_onchange_after_install_better_beads_kanban.ps1.tmpl",
            "private_Documents/development/container-dotfiles/devcontainers/gitlab.com/servers-homelab/homelab-IaC/dot_devcontainer/devcontainer-common.sh",
        ),
        "assets/sync-beads-kanban-pin.sh",
    ),
    "browser-policy-sync": (
        "renovate/browser-policies-fixture",
        ("configs/browser-policies/justthebrowser/manifest.json",),
        "assets/sync-browser-policies.py",
    ),
}


def job_script(name: str) -> str:
    block = top_level_block(CI_PATH.read_text(encoding="utf-8"), name)
    script = block.split("  script:\n", 1)[1].split("  rules:\n", 1)[0]
    match = re.fullmatch(r"(?:    #[^\n]*\n)*    - \|\n((?:      [^\n]*\n)+)", script)
    if match is None:
        raise AssertionError(f"unexpected script format for {name}")
    return textwrap.dedent(match.group(1))


class AmendmentFixture:
    def __init__(self, name: str, *, legacy: bool = False, job=None) -> None:
        self.name = name
        self.branch, self.paths, self.sync_script = JOBS[name] if job is None else job
        self.target = self.paths[0]
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.shims = self.root / "shims"
        self.shims.mkdir()
        self.real_git = shutil.which("git")
        if self.real_git is None:
            raise RuntimeError("git is required")
        self.remote_log = self.root / ".git" / "remote.log"
        self.sync_log = self.root / ".git" / "sync.log"
        (self.shims / "git").write_text(
            "#!/bin/sh\n"
            'if [ "$1" = remote ] && [ "$2" = set-url ]; then\n'
            '  printf "remote|%s|%s\\n" "$3" "$4" >> "$REMOTE_LOG"\n'
            "  exit 0\n"
            "fi\n"
            'if [ "$1" = push ]; then\n'
            '  printf "push|%s|%s|%s\\n" "$2" "$3" "$4" >> "$REMOTE_LOG"\n'
            "  exit 0\n"
            "fi\n"
            'exec "$REAL_GIT" "$@"\n',
            encoding="utf-8",
        )
        for command, executable in (("bash", "/bin/bash"), ("python3", sys.executable)):
            (self.shims / command).write_text(
                "#!/bin/sh\n"
                'if [ "$1" = "$SYNC_SCRIPT" ]; then\n'
                '  printf "%s\\n" "${2:-run}" >> "$SYNC_LOG"\n'
                '  if [ "${SYNC_CHANGE:-0}" = 1 ] && [ "${2:-}" != --check ]; then\n'
                '    printf "synced\\n" >> "$SYNC_TARGET"\n'
                '    if [ -n "${SYNC_EXTRA_TARGET:-}" ]; then printf "synced\\n" >> "$SYNC_EXTRA_TARGET"; fi\n'
                '    if [ "${SYNC_UNRELATED:-0}" = 1 ]; then printf "unexpected\\n" >> unrelated.txt; fi\n'
                "  fi\n"
                '  exit "${SYNC_FAILURE:-0}"\n'
                "fi\n"
                'if [ "$1" = "assets/check-plannotator-config.py" ]; then\n'
                '  printf "config\\n" >> "$SYNC_LOG"\n'
                '  exit "${CONFIG_FAILURE:-0}"\n'
                "fi\n"
                f'exec "{executable}" "$@"\n',
                encoding="utf-8",
            )
        for shim in self.shims.iterdir():
            shim.chmod(0o755)
        self.env = {
            **os.environ,
            "PATH": f"{self.shims}{os.pathsep}{os.environ['PATH']}",
            "REAL_GIT": self.real_git,
            "REMOTE_LOG": str(self.remote_log),
            "SYNC_LOG": str(self.sync_log),
            "SYNC_SCRIPT": self.sync_script,
            "SYNC_TARGET": self.target,
            "SYNC_CHANGE": "1",
            "SYNC_FAILURE": "0",
            "CONFIG_FAILURE": "0",
            "SYNC_UNRELATED": "0",
            "SYNC_EXTRA_TARGET": "",
            "CI_PIPELINE_SOURCE": "push",
            "CI_COMMIT_REF_NAME": self.branch,
            "CI_PROJECT_PATH": "fixture/dotfiles",
            "VENDOREDFILE_SYNC_TOKEN": TOKEN,
        }
        for key in ("GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL"):
            self.env.pop(key, None)
        self.git("init", "-q")
        self.git("config", "user.name", "Fixture User")
        self.git("config", "user.email", "fixture@example.test")
        self.git("config", "core.hooksPath", "/dev/null")
        for path in self.paths:
            target = self.root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("original\n", encoding="utf-8")
        (self.root / "unrelated.txt").write_text("base\n", encoding="utf-8")
        self.git("add", ".")
        self.git("commit", "-q", "-m", "base")
        (self.root / self.target).write_text("pinned\n", encoding="utf-8")
        self.git("add", self.target)
        identity = {"GIT_AUTHOR_NAME": "Legacy User", "GIT_AUTHOR_EMAIL": "legacy@example.test"} if legacy else {
            "GIT_AUTHOR_NAME": SERVICE_NAME,
            "GIT_AUTHOR_EMAIL": SERVICE_EMAIL,
        }
        self.git(
            "commit", "-q", "-m", "chore(deps): bump fixture",
            *([] if legacy else ["-m", TRAILER]),
            env={**self.env, **identity},
        )
        self.sha = self.git("rev-parse", "HEAD").stdout.strip()
        self.message = self.git("show", "-s", "--format=%B", "HEAD").stdout
        self.env["CI_COMMIT_SHA"] = self.sha

    def git(self, *args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [self.real_git, *args], cwd=self.root, env=env or self.env,
            text=True, capture_output=True, check=True,
        )

    def run(self) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["/bin/sh", "-eu", "-c", job_script(self.name)], cwd=self.root,
            env=self.env, text=True, capture_output=True, check=False,
        )

    def close(self) -> None:
        self.temp.cleanup()


class PlannotatorAmendTests(unittest.TestCase):
    def fixture(self, *, legacy=False):
        manifest = json.loads((REPO_ROOT / "configs/plannotator-assets.json").read_text(encoding="utf-8"))
        canonical = tuple(artifact["path"] for artifact in manifest["artifacts"])
        prefix = "private_Documents/development/container-dotfiles/dotfiles/"
        paths = ("configs/plannotator-assets.json", *canonical,
                 *(prefix + path for path in canonical), prefix + "dot_claude/settings-base.json")
        fixture = AmendmentFixture("plannotator-sync", legacy=legacy,
                                   job=("renovate/plannotator-fixture", paths, "assets/sync-plannotator-assets.py"))
        self.addCleanup(fixture.close)
        fixture.env["SYNC_EXTRA_TARGET"] = prefix + canonical[-1]
        return fixture

    def assert_not_published(self, fixture):
        self.assertEqual(fixture.git("rev-parse", "HEAD").stdout.strip(), fixture.sha)
        self.assertFalse(fixture.remote_log.exists())
        self.assertEqual(fixture.git("diff", "--cached", "--name-only").stdout, "")

    def test_matching_push_amends_only_allowlisted_outputs_with_exact_lease(self):
        for legacy in (False, True):
            with self.subTest(legacy=legacy):
                fixture = self.fixture(legacy=legacy)
                result = fixture.run()
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertNotEqual(fixture.git("rev-parse", "HEAD").stdout.strip(), fixture.sha)
                self.assertEqual(fixture.git("show", "-s", "--format=%B", "HEAD").stdout, fixture.message)
                for identity in ("%an <%ae>", "%cn <%ce>"):
                    expected = "Legacy User <legacy@example.test>" if legacy and identity.startswith("%an") else f"{SERVICE_NAME} <{SERVICE_EMAIL}>"
                    self.assertEqual(fixture.git("show", "-s", f"--format={identity}", "HEAD").stdout.strip(),
                                     expected)
                self.assertCountEqual(fixture.git("diff", "--name-only", fixture.sha, "HEAD").stdout.splitlines(),
                                      [fixture.target, fixture.env["SYNC_EXTRA_TARGET"]])
                self.assertEqual(fixture.git("status", "--short").stdout, "")
                self.assertEqual(fixture.sync_log.read_text(encoding="utf-8"), "--write\nconfig\n")
                self.assertEqual(fixture.remote_log.read_text(encoding="utf-8").splitlines(), [
                    f"remote|origin|https://oauth2:{TOKEN}@gitlab.com/fixture/dotfiles.git",
                    f"push|--force-with-lease=refs/heads/{fixture.branch}:{fixture.sha}|origin|HEAD:{fixture.branch}",
                ])

    def test_mr_and_nonmatching_push_are_read_only_even_with_token(self):
        for source, branch in (("merge_request_event", "renovate/plannotator-fixture"),
                               ("push", "renovate/backnotprop-plannotator-0.x")):
            with self.subTest(source=source, branch=branch):
                fixture = self.fixture()
                fixture.env.update(CI_PIPELINE_SOURCE=source, CI_COMMIT_REF_NAME=branch)
                result = fixture.run()
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(fixture.sync_log.read_text(encoding="utf-8"), "config\n--check\n")
                self.assertEqual(fixture.git("status", "--short").stdout, "")
                self.assert_not_published(fixture)

    def test_no_diff_needs_no_token(self):
        fixture = self.fixture()
        fixture.env.update(SYNC_CHANGE="0", VENDOREDFILE_SYNC_TOKEN="")
        self.assertEqual(fixture.run().returncode, 0)
        self.assert_not_published(fixture)

    def test_write_failures_and_unrelated_changes_stop_publication(self):
        for updates, expected in (({"VENDOREDFILE_SYNC_TOKEN": ""}, "variable is not set"),
                                  ({"SYNC_FAILURE": "1"}, ""),
                                  ({"CONFIG_FAILURE": "1"}, ""),
                                  ({"SYNC_UNRELATED": "1"}, "sync modified unrelated path: unrelated.txt")):
            with self.subTest(updates=updates):
                fixture = self.fixture()
                fixture.env.update(updates)
                result = fixture.run()
                self.assertEqual(result.returncode, 1)
                self.assertIn(expected, result.stderr)
                self.assert_not_published(fixture)

    def test_read_only_check_failures_stop_without_publication(self):
        for updates, log in (({"SYNC_FAILURE": "1"}, "config\n--check\n"),
                             ({"CONFIG_FAILURE": "1"}, "config\n")):
            with self.subTest(updates=updates):
                fixture = self.fixture()
                fixture.env.update(CI_PIPELINE_SOURCE="merge_request_event", **updates)
                self.assertEqual(fixture.run().returncode, 1)
                self.assertEqual(fixture.sync_log.read_text(encoding="utf-8"), log)
                self.assertEqual(fixture.git("status", "--short").stdout, "")
                self.assert_not_published(fixture)


class RenovateAmendTests(unittest.TestCase):
    def test_repo_commit_body_is_bot_coauthor_without_author_override(self) -> None:
        text = (REPO_ROOT / "renovate.json5").read_text(encoding="utf-8")
        self.assertRegex(text, rf'(?m)^  "commitBody": "{re.escape(TRAILER)}",$')
        self.assertNotRegex(text, r'(?m)^\s*"(?:gitAuthor|gitIgnoredAuthors)":')
        self.assertEqual(len(re.findall(r'"commitBody"\s*:', text)), 1)

    def test_amend_preserves_author_message_and_scoped_tree(self) -> None:
        for name in JOBS:
            for legacy in (False, True):
                with self.subTest(job=name, legacy=legacy):
                    fixture = AmendmentFixture(name, legacy=legacy)
                    try:
                        (fixture.root / "unrelated.txt").write_text("unstaged\n", encoding="utf-8")
                        result = fixture.run()
                        self.assertEqual(result.returncode, 0, result.stderr)
                        self.assertNotEqual(fixture.git("rev-parse", "HEAD").stdout.strip(), fixture.sha)
                        self.assertEqual(fixture.git("show", "-s", "--format=%B", "HEAD").stdout, fixture.message)
                        author = fixture.git("show", "-s", "--format=%an <%ae>", "HEAD").stdout.strip()
                        expected = "Legacy User <legacy@example.test>" if legacy else f"{SERVICE_NAME} <{SERVICE_EMAIL}>"
                        self.assertEqual(author, expected)
                        self.assertEqual(
                            fixture.git("show", "-s", "--format=%cn <%ce>", "HEAD").stdout.strip(),
                            f"{SERVICE_NAME} <{SERVICE_EMAIL}>",
                        )
                        self.assertIn("synced", (fixture.root / fixture.target).read_text(encoding="utf-8"))
                        self.assertEqual(fixture.git("show", "HEAD:unrelated.txt").stdout, "base\n")
                        self.assertEqual(fixture.git("status", "--short").stdout, " M unrelated.txt\n")
                        self.assertEqual(
                            fixture.remote_log.read_text(encoding="utf-8").splitlines(),
                            [
                                f"remote|origin|https://oauth2:{TOKEN}@gitlab.com/fixture/dotfiles.git",
                                f"push|--force-with-lease=refs/heads/{fixture.branch}:{fixture.sha}|origin|HEAD:{fixture.branch}",
                            ],
                        )
                        self.assertEqual(fixture.sync_log.read_text(encoding="utf-8").strip(),
                                         "run" if name == "statusline-sync" else "--write")
                        if legacy:
                            self.assertNotIn(TRAILER, fixture.message)
                        else:
                            self.assertEqual(fixture.message.count(TRAILER), 1)
                    finally:
                        fixture.close()

    def test_no_change_or_token_stops_before_amend_and_push(self) -> None:
        for name in JOBS:
            for change, token, rc in (("0", TOKEN, 0), ("1", "", 1)):
                with self.subTest(job=name, change=change, token_present=bool(token)):
                    fixture = AmendmentFixture(name)
                    try:
                        fixture.env["SYNC_CHANGE"] = change
                        fixture.env["VENDOREDFILE_SYNC_TOKEN"] = token
                        result = fixture.run()
                        self.assertEqual(result.returncode, rc)
                        self.assertEqual(fixture.git("rev-parse", "HEAD").stdout.strip(), fixture.sha)
                        self.assertFalse(fixture.remote_log.exists())
                        if rc:
                            self.assertIn("VENDOREDFILE_SYNC_TOKEN CI variable is not set", result.stderr)
                    finally:
                        fixture.close()

    def test_nonmatching_branch_only_checks(self) -> None:
        for name in JOBS:
            with self.subTest(job=name):
                fixture = AmendmentFixture(name)
                try:
                    fixture.env["CI_COMMIT_REF_NAME"] = "feature/not-renovate"
                    fixture.env["VENDOREDFILE_SYNC_TOKEN"] = ""
                    result = fixture.run()
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(fixture.sync_log.read_text(encoding="utf-8"), "--check\n")
                    self.assertEqual(fixture.git("rev-parse", "HEAD").stdout.strip(), fixture.sha)
                    self.assertFalse(fixture.remote_log.exists())
                finally:
                    fixture.close()


if __name__ == "__main__":
    unittest.main()
