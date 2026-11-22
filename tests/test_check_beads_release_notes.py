from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import re
import runpy
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
CLONE_LOCAL_FKS = (
    ("events", "fk_events_issue", "issue_id", "issues", "id"),
    ("wisp_dependencies", "fk_wisp_dep_issue", "issue_id", "wisps", "id"),
    ("wisp_labels", "fk_wisp_labels_issue", "issue_id", "wisps", "id"),
)


def upstream_source(specs: tuple[tuple[str, ...], ...] = CLONE_LOCAL_FKS) -> str:
    entries = "\n".join(
        f'\t{{Table: "{t}", Constraint: "{c}", Column: "{col}", '
        f'RefTable: "{rt}", RefColumn: "{rc}"}},'
        for t, c, col, rt, rc in specs
    )
    return f"package fix\n\nvar CloneLocalFKs = []CloneLocalFK{{\n{entries}\n}}\n"


def sync_script(specs: tuple[tuple[str, ...], ...] = CLONE_LOCAL_FKS, quote: str = '"') -> str:
    entries = "\n".join(f"  {quote}{'|'.join(spec)}{quote}" for spec in specs)
    return f"CLONE_LOCAL_FKS=(\n{entries}\n)\n"


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
        (self.repo_root / "assets").mkdir()
        self.write_sync_lists()
        self.upstream_path = self.repo_root / "clone_local_fks.go"
        self.upstream_path.write_text(upstream_source(), encoding="utf-8")

    def write_sync_lists(self, specs: tuple[tuple[str, ...], ...] = CLONE_LOCAL_FKS) -> None:
        (self.repo_root / "assets/beads-sync.sh").write_text(sync_script(specs), encoding="utf-8")
        (self.repo_root / "assets/beads-sync.ps1").write_text(
            sync_script(specs, quote="'"), encoding="utf-8"
        )

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

    def commit_base_pin(self, host: str) -> None:
        env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
        proposed = (self.repo_root / ".chezmoidata.yaml").read_text(encoding="utf-8")
        (self.repo_root / ".chezmoidata.yaml").write_text(host + "\n", encoding="utf-8")
        for argv in (
            ["git", "init", "-q", "-b", "main"],
            ["git", "add", ".chezmoidata.yaml"],
            [
                "git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                "commit", "-q", "-m", "base pin",
            ],
        ):
            subprocess.run(argv, cwd=self.repo_root, env=env, check=True, capture_output=True)
        (self.repo_root / ".chezmoidata.yaml").write_text(proposed, encoding="utf-8")

    def write_acks(self, payload: Any) -> None:
        path = self.repo_root / HELPER.ACKS_PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            payload if isinstance(payload, str) else json.dumps(payload), encoding="utf-8"
        )

    def run_helper(
        self,
        releases: Any,
        *,
        raw_json: str | None = None,
        extra: tuple[str, ...] = (),
        ci: dict[str, str] | None = None,
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
                "--clone-local-fks-file",
                str(self.upstream_path),
                *extra,
            ],
            env={
                **{
                    key: value
                    for key, value in os.environ.items()
                    if not key.startswith("CI_")
                },
                **(ci or {}),
                "GIT_CEILING_DIRECTORIES": str(self.repo_root.parent),
                "GIT_CONFIG_GLOBAL": os.devnull,
                "GIT_CONFIG_NOSYSTEM": "1",
            },
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

    def test_matching_clone_local_fk_list_passes(self) -> None:
        completed = self.run_helper([release("v1.2.2")])

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("clone-local FK list matches bd 1.2.2", completed.stdout)

    def test_added_upstream_clone_local_fk_fails(self) -> None:
        added = ("wisp_notes", "fk_wisp_notes_issue", "issue_id", "wisps", "id")
        self.upstream_path.write_text(upstream_source(CLONE_LOCAL_FKS + (added,)), encoding="utf-8")

        completed = self.run_helper([release("v1.2.2")])

        self.assertEqual(completed.returncode, 1)
        for path in ("assets/beads-sync.sh", "assets/beads-sync.ps1"):
            self.assertIn(
                f"defines clone-local FK wisp_notes.fk_wisp_notes_issue that {path} does not re-link",
                completed.stderr,
            )

    def test_removed_upstream_clone_local_fk_fails(self) -> None:
        self.upstream_path.write_text(upstream_source(CLONE_LOCAL_FKS[:2]), encoding="utf-8")

        completed = self.run_helper([release("v1.2.2")])

        self.assertEqual(completed.returncode, 1)
        self.assertIn(
            "re-links wisp_labels.fk_wisp_labels_issue, which bd 1.2.2 does not define",
            completed.stderr,
        )

    def test_unreadable_clone_local_fk_sources_fail_closed(self) -> None:
        cases = (
            ("package fix\n", None, "cannot find CloneLocalFKs"),
            (
                upstream_source().replace('RefColumn: "id"}', 'RefColumn: "id", OnDelete: "x"}', 1),
                None,
                "cannot parse every CloneLocalFKs entry",
            ),
            (upstream_source(), "", "no clone-local FK list found in assets/beads-sync.sh"),
        )
        for upstream, script, message in cases:
            with self.subTest(message=message):
                self.upstream_path.write_text(upstream, encoding="utf-8")
                if script is not None:
                    (self.repo_root / "assets/beads-sync.sh").write_text(script, encoding="utf-8")

                completed = self.run_helper([release("v1.2.2")])

                self.assertEqual(completed.returncode, 2)
                self.assertIn(message, completed.stderr)

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


    def adopted_range_fixture(self) -> tuple[list[dict[str, Any]], tuple[str, ...]]:
        self.commit_base_pin('beads_version: "1.2.0"')
        changelog = self.repo_root / "CHANGELOG.md"
        changelog.write_text(
            "\n".join(
                [
                    "# Changelog",
                    "## [Unreleased]",
                    "### Upgrade notes",
                    "unreleased note must stay hidden",
                    "## [1.2.2] - 2026-08-20",
                    "Same code as [1.2.2-rc.1]; the full notes are in that section.",
                    "## [1.2.2-rc.1] - 2026-08-19",
                    "First candidate.",
                    "### Fixed",
                    "- fixes a data loss bug in export",
                    "### Upgrade notes",
                    "- run bd recompute-blocked once",
                    "#### Details",
                    "- nested detail stays in the section",
                    "### Known issues",
                    "- compact is unsafe after 0062",
                    "## [1.2.1] - 2026-08-10",
                    "### Changed",
                    "- nothing to act on",
                    "## [1.2.0] - 2026-08-01",
                    "### Upgrade notes",
                    "current pin note must stay hidden",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        releases = [
            release("v1.2.3"),
            release(
                "v1.2.2",
                body=(
                    "## beads v1.2.2\n### Release notes\nRead the rc section.\n"
                    "### Known issue found after release: env is ignored\nworkaround text\n"
                    "### Installation\ninstall text must stay hidden\n"
                    "## Changelog\n* abc recover from a crash\n"
                ),
            ),
            release("v1.2.2-rc.1"),
            release("v1.2.1"),
            release("v1.2.1-rc.1"),
            release("v1.2.0"),
        ]
        return releases, ("--base-ref", "HEAD", "--changelog-file", str(changelog))

    def test_adopted_range_prints_notes_between_current_and_proposed_pins(self) -> None:
        releases, extra = self.adopted_range_fixture()

        completed = self.run_helper(releases, extra=extra)

        self.assertEqual(completed.returncode, 0, completed.stderr)
        output = completed.stdout
        self.assertIn("adopts 4 Beads release(s) after 1.2.0 (HEAD), up to 1.2.2", output)
        order = [
            output.index(f"adopted Beads release {version}:")
            for version in ("1.2.1-rc.1", "1.2.1", "1.2.2-rc.1", "1.2.2")
        ]
        self.assertEqual(order, sorted(order))
        self.assertNotIn("adopted Beads release 1.2.0:", output)
        self.assertNotIn("adopted Beads release 1.2.3:", output)
        for expected in (
            "CHANGELOG Upgrade notes:",
            "| - run bd recompute-blocked once",
            "| - nested detail stays in the section",
            "CHANGELOG Known issues:",
            "| - compact is unsafe after 0062",
            "| Same code as [1.2.2-rc.1]; the full notes are in that section.",
            "Release page Release notes:",
            "Release page Known issue found after release: env is ignored:",
            "| workaround text",
            "mentions 'data loss': - fixes a data loss bug in export",
            "no upgrade-notes or known-issues section found",
            "blob/v1.2.2/CHANGELOG.md",
        ):
            self.assertIn(expected, output)
        for hidden in ("must stay hidden", "recover from a crash"):
            self.assertNotIn(hidden, output)
        self.assertNotIn("ERROR", completed.stderr)

    def test_adopted_range_never_changes_the_exit_status(self) -> None:
        releases, extra = self.adopted_range_fixture()
        releases[0] = release("v1.2.3", body="possible data loss")

        completed = self.run_helper(releases, extra=extra)

        self.assertEqual(completed.returncode, 1)
        self.assertIn("adopts 4 Beads release(s)", completed.stdout)

        missing = self.run_helper(
            releases[1:], extra=("--base-ref", "HEAD")
        )
        self.assertEqual(missing.returncode, 0, missing.stderr)
        self.assertIn("CHANGELOG.md not read", missing.stdout)
        self.assertIn("adopted Beads release 1.2.2:", missing.stdout)
        self.assertIn("Release page Known issue found after release", missing.stdout)

    def test_unchanged_or_lowered_pin_has_no_adopted_range(self) -> None:
        self.commit_base_pin('beads_version: "1.2.2"')
        same = self.run_helper([release("v1.2.2")], extra=("--base-ref", "HEAD"))
        self.assertEqual(same.returncode, 0, same.stderr)
        self.assertIn("unchanged from HEAD; no adopted range", same.stdout)

        self.write_pins(host='beads_version: "1.2.1"', npm="@beads/bd@1.2.1")
        lowered = self.run_helper(
            [release("v1.2.2"), release("v1.2.1")], extra=("--base-ref", "HEAD")
        )
        self.assertEqual(lowered.returncode, 0, lowered.stderr)
        self.assertIn("moves down from 1.2.2 (HEAD) to 1.2.1", lowered.stdout)

    def test_unreadable_base_ref_skips_the_range_report(self) -> None:
        for extra, message in (
            ((), "git could not read .chezmoidata.yaml at origin/main"),
            (("--base-ref=--upload-pack=x",), "refusing base ref"),
        ):
            with self.subTest(extra=extra):
                completed = self.run_helper([release("v1.2.2")], extra=extra)
                self.assertEqual(completed.returncode, 0, completed.stderr)
                self.assertIn("adopted-range report skipped", completed.stdout)
                self.assertIn(message, completed.stdout)

    def test_acknowledged_lines_pass_and_new_lines_block(self) -> None:
        commit = "* 056f47a test(ci): assert corrupt-metadata refusals (#7744)"
        other = "* b184dae fix(dbproxy): recover onto a free port (#7184)"
        releases = [
            release("v1.2.2"),
            release("v1.2.3-rc.1", body=f"## Changelog\n{commit}\n"),
            release("v1.2.3-rc.2", body=f"## Changelog\n{commit}\n{other}\n"),
        ]
        ack = {
            "line": commit.replace(" test(ci)", "   test(ci)"),
            "pattern": "corruption",
            "reason": "test-only commit",
            "reviewed": "2026-11-22",
        }
        self.write_acks({"schema_version": 1, "acks": [ack]})

        blocked = self.run_helper(releases)

        self.assertEqual(blocked.returncode, 1)
        self.assertEqual(blocked.stdout.count("acknowledged 'corruption' match"), 1)
        self.assertNotIn("corrupt-metadata", blocked.stderr)
        self.assertIn(f"v1.2.3-rc.2 release notes match 'recovery': {other}", blocked.stderr)
        suggestion = next(
            line for line in blocked.stderr.splitlines() if "to acknowledge after review" in line
        )
        suggested = json.loads(suggestion.split(": ", 2)[2])
        self.assertEqual((suggested["line"], suggested["pattern"]), (other, "recovery"))

        stale = {**ack, "line": "* gone commit recovers nothing", "pattern": "recovery"}
        self.write_acks(
            {
                "schema_version": 1,
                "acks": [ack, stale, {**ack, "line": other, "pattern": "recovery"}],
            }
        )
        passed = self.run_helper(releases)
        self.assertEqual(passed.returncode, 0, passed.stderr)
        self.assertIn("contain no unacknowledged hazard terms", passed.stdout)
        self.assertIn("stale acknowledgement", passed.stdout)
        self.assertIn("* gone commit recovers nothing", passed.stdout)

    def test_report_failures_are_swallowed_and_output_is_sanitised(self) -> None:
        releases, extra = self.adopted_range_fixture()
        changelog = Path(extra[-1])
        text = changelog.read_text(encoding="utf-8").replace(
            "- run bd recompute-blocked once",
            "- run \x1b[31mbd\x00 recompute-blocked\u202e once",
        )
        changelog.write_text(text, encoding="utf-8")

        sanitised = self.run_helper(releases, extra=extra)
        self.assertEqual(sanitised.returncode, 0, sanitised.stderr)
        self.assertIn("| - run bd  recompute-blocked  once", sanitised.stdout)
        for character in ("\x1b", "\x00", "\u202e"):
            self.assertNotIn(character, sanitised.stdout)

        changelog.write_bytes(b"## [1.2.2]\n\xff\xfe not utf-8\n")
        unreadable = self.run_helper(releases, extra=extra)
        self.assertEqual(unreadable.returncode, 0, unreadable.stderr)
        self.assertIn("CHANGELOG.md not read", unreadable.stdout)
        self.assertIn("adopted Beads release 1.2.2:", unreadable.stdout)
        self.assertNotIn("Traceback", unreadable.stderr)

    def test_report_swallows_unexpected_exceptions(self) -> None:
        releases, extra = self.adopted_range_fixture()
        releases_path = self.repo_root / "releases.json"
        releases_path.write_text(json.dumps(releases), encoding="utf-8")
        original = HELPER.report_adopted_range

        def explode(*args: Any) -> None:
            del args
            raise RuntimeError("unexpected\nfailure")

        HELPER.report_adopted_range = explode
        self.addCleanup(setattr, HELPER, "report_adopted_range", original)
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = HELPER.main(
                [
                    "--repo-root", str(self.repo_root),
                    "--releases-file", str(releases_path),
                    "--clone-local-fks-file", str(self.upstream_path),
                    *extra,
                ]
            )

        self.assertEqual(code, 0, stderr.getvalue())
        self.assertIn("adopted-range report skipped: unexpected failure", stdout.getvalue())
        self.assertIn("clone-local FK list matches", stdout.getvalue())

    def test_hazard_term_wrapped_across_lines_still_blocks(self) -> None:
        completed = self.run_helper(
            [
                release("v1.2.2"),
                release("v1.2.3", body="- export may cause data\r\n  loss on a full disk\nclean"),
            ]
        )

        self.assertEqual(completed.returncode, 1)
        self.assertIn(
            "match 'data loss': - export may cause data loss on a full disk",
            completed.stderr,
        )

    def test_ci_branches_name_the_rehearsal_command_and_other_runs_do_not(self) -> None:
        command = "python3 assets/beads-upgrade-rehearsal.py --ref origin/{} --stop-server"
        merge_request = self.run_helper(
            [release("v1.2.2")],
            ci={
                "CI_MERGE_REQUEST_SOURCE_BRANCH_NAME": "renovate/beads-core-clients",
                "CI_COMMIT_BRANCH": "ignored",
            },
        )
        self.assertEqual(merge_request.returncode, 0, merge_request.stderr)
        self.assertIn(command.format("renovate/beads-core-clients"), merge_request.stdout)

        pushed = self.run_helper(
            [release("v1.2.2")], ci={"CI_COMMIT_BRANCH": "renovate/beads-core-dolt-engine"}
        )
        self.assertIn(command.format("renovate/beads-core-dolt-engine"), pushed.stdout)

        for ci in (None, {"CI_COMMIT_BRANCH": "x; rm -rf ~"}, {"CI_COMMIT_BRANCH": "-oops"}):
            with self.subTest(ci=ci):
                local = self.run_helper([release("v1.2.2")], ci=ci)
                self.assertEqual(local.returncode, 0, local.stderr)
                self.assertNotIn("beads-upgrade-rehearsal", local.stdout)

        blocked = self.run_helper(
            [release("v1.2.2"), release("v1.2.3", body="data loss")],
            ci={"CI_COMMIT_BRANCH": "renovate/beads-core-clients"},
        )
        self.assertEqual(blocked.returncode, 1)
        self.assertIn("beads-upgrade-rehearsal", blocked.stdout)

    def test_every_unacknowledged_line_in_a_release_is_reported(self) -> None:
        completed = self.run_helper(
            [
                release("v1.2.2"),
                release("v1.2.3", body="first corrupt row\nsecond corrupted table\nclean line"),
            ]
        )

        self.assertEqual(completed.returncode, 1)
        self.assertIn("match 'corruption': first corrupt row", completed.stderr)
        self.assertIn("match 'corruption': second corrupted table", completed.stderr)

    def test_an_acknowledgement_covers_only_its_own_pattern(self) -> None:
        line = "recover from data loss"
        self.write_acks(
            {
                "schema_version": 1,
                "acks": [
                    {"line": line, "pattern": "recovery", "reason": "r", "reviewed": "2026-11-22"}
                ],
            }
        )

        completed = self.run_helper([release("v1.2.2"), release("v1.2.3", body=line)])

        self.assertEqual(completed.returncode, 1)
        self.assertIn("match 'data loss'", completed.stderr)
        self.assertNotIn("match 'recovery'", completed.stderr)

    def test_malformed_acknowledgement_file_fails_closed(self) -> None:
        good = {"line": "x", "pattern": "recovery", "reason": "r", "reviewed": "2026-11-22"}
        cases = (
            ("{broken", "invalid JSON"),
            ([], "schema_version 1"),
            ({"schema_version": 2, "acks": []}, "schema_version 1"),
            ({"schema_version": 1}, "acks array"),
            ({"schema_version": 1, "acks": ["x"]}, "is not an object"),
            ({"schema_version": 1, "acks": [{**good, "reason": " "}]}, "missing reason"),
            ({"schema_version": 1, "acks": [{**good, "pattern": "typo"}]}, "unknown pattern"),
            ({"schema_version": 1, "acks": [{**good, "reviewed": "today"}]}, "YYYY-MM-DD"),
        )
        for payload, message in cases:
            with self.subTest(message=message):
                self.write_acks(payload)
                completed = self.run_helper([release("v1.2.2")])
                self.assertEqual(completed.returncode, 2)
                self.assertIn(message, completed.stderr)

        (self.repo_root / HELPER.ACKS_PATH).write_bytes(b"\xff\xfe")
        completed = self.run_helper([release("v1.2.2")])
        self.assertEqual(completed.returncode, 2)
        self.assertIn("cannot read", completed.stderr)
        self.assertNotIn("Traceback", completed.stderr)


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

    def test_fetch_follows_numeric_and_mixed_release_pages(self) -> None:
        numeric = "https://api.github.com/repositories/1074561042/releases?per_page=100&page=2"
        named = HELPER.API_URL + "&page=3"
        opener = FakeOpener(
            [
                FakeResponse(
                    [release("v1.2.2")],
                    url=numeric,
                    link=f'<{numeric}>; rel="next"',
                ),
                FakeResponse(
                    [release("v1.2.3")],
                    url=HELPER.API_URL,
                    link=f'<{named}>; rel="next"',
                ),
                FakeResponse([release("v1.2.4")], url=numeric),
            ]
        )

        releases = HELPER.fetch_releases(1.0, opener=opener)

        self.assertEqual([item["tag_name"] for item in releases], ["v1.2.2", "v1.2.3", "v1.2.4"])
        self.assertEqual(opener.requests, [HELPER.API_URL, numeric, named])

    def test_fetch_rejects_untrusted_release_links_before_request(self) -> None:
        bad_urls = (
            "https://api.github.com/repositories/1074561043/releases?page=2",
            "https://api.github.com/repositories/1074561042/releases/1?page=2",
            "https://api.github.com/repositories/1074561042/releases%2F?page=2",
            "https://api.github.com/repositories/1074561042/contents/file?page=2",
            "https://api.github.com/repos/gastownhall/beads/releases/1?page=2",
            "https://example.com/repositories/1074561042/releases?page=2",
            "https://api.github.com.evil.invalid/repositories/1074561042/releases?page=2",
            "http://api.github.com/repositories/1074561042/releases?page=2",
            "https://api.github.com:443/repositories/1074561042/releases?page=2",
            "https://user:pass@api.github.com/repositories/1074561042/releases?page=2",
            "https://api.github.com/repositories/1074561042/releases?page=2#fragment",
        )
        for bad_url in bad_urls:
            with self.subTest(url=bad_url):
                opener = FakeOpener(
                    [FakeResponse([], url=HELPER.API_URL, link=f'<{bad_url}>; rel="next"')]
                )
                with self.assertRaisesRegex(HELPER.GuardError, "outside the Beads GitHub API"):
                    HELPER.fetch_releases(1.0, opener=opener)
                self.assertEqual(opener.requests, [HELPER.API_URL])

    def test_fetch_rejects_untrusted_release_response_url(self) -> None:
        for bad_url in (
            "https://api.github.com/repositories/1074561043/releases",
            "https://api.github.com/repositories/1074561042/releases/1",
            "https://api.github.com/repositories/1074561042/releases%2F",
            "https://example.com/repositories/1074561042/releases",
        ):
            with self.subTest(url=bad_url):
                opener = FakeOpener([FakeResponse([], url=bad_url)])
                with self.assertRaisesRegex(HELPER.GuardError, "outside the Beads GitHub API"):
                    HELPER.fetch_releases(1.0, opener=opener)
                self.assertEqual(opener.requests, [HELPER.API_URL])

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

    def test_clone_local_fks_source_is_fetched_from_the_pinned_tag_only(self) -> None:
        version = HELPER.SemVer.parse("1.3.1")
        url = f"https://api.github.com{HELPER.CONTENTS_API_PATH}?ref=v1.3.1"
        response = FakeResponse(None, url=url)
        response.payload = upstream_source().encode("utf-8")
        opener = FakeOpener([response])

        source = HELPER.fetch_clone_local_fks_source(version, 1.0, opener=opener)

        self.assertEqual(opener.requests, [url])
        self.assertEqual(HELPER.parse_upstream_clone_local_fks(source), set(CLONE_LOCAL_FKS))

        moved = FakeResponse(None, url="https://api.github.com/repos/gastownhall/beads/contents/README.md")
        with self.assertRaisesRegex(HELPER.GuardError, "contents URL outside the Beads GitHub API"):
            HELPER.fetch_clone_local_fks_source(version, 1.0, opener=FakeOpener([moved]))

        numeric = FakeResponse(None, url="https://api.github.com/repositories/1074561042/releases")
        numeric_opener = FakeOpener([numeric])
        with self.assertRaisesRegex(HELPER.GuardError, "contents URL outside the Beads GitHub API"):
            HELPER.fetch_clone_local_fks_source(version, 1.0, opener=numeric_opener)
        self.assertEqual(numeric_opener.requests, [url])

    def test_changelog_is_fetched_from_the_proposed_tag_only(self) -> None:
        version = HELPER.SemVer.parse("1.3.4")
        url = f"https://api.github.com{HELPER.CHANGELOG_API_PATH}?ref=v1.3.4"
        response = FakeResponse(None, url=url)
        response.payload = b"## [1.3.4] - 2026-11-04\n"
        opener = FakeOpener([response])

        self.assertEqual(
            HELPER.fetch_changelog(version, 1.0, opener=opener), "## [1.3.4] - 2026-11-04\n"
        )
        self.assertEqual(opener.requests, [url])

        moved = FakeResponse(None, url=f"https://api.github.com{HELPER.CONTENTS_API_PATH}")
        with self.assertRaisesRegex(HELPER.GuardError, "contents URL outside the Beads GitHub API"):
            HELPER.fetch_changelog(version, 1.0, opener=FakeOpener([moved]))

    def test_missing_clone_local_fks_source_fails_closed(self) -> None:
        error = urllib.error.HTTPError(HELPER.CONTENTS_API_PATH, 404, "Not Found", {}, io.BytesIO())
        try:
            with self.assertRaisesRegex(HELPER.GuardError, "HTTP 404 .* re-verify the clone-local FK list"):
                HELPER.fetch_clone_local_fks_source(
                    HELPER.SemVer.parse("9.9.9"), 1.0, opener=FakeOpener(error=error)
                )
        finally:
            error.close()

    def test_fetch_reports_rate_limit_exhaustion(self) -> None:
        error = urllib.error.HTTPError(
            HELPER.API_URL,
            403,
            "Forbidden",
            {"X-RateLimit-Remaining": "0"},
            io.BytesIO(),
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

    def test_dolt_has_its_own_gated_group_on_the_beads_branch_prefix(self) -> None:
        load_json = runpy.run_path(str(REPO_ROOT / "assets/check-ai-tooling.py"))["load_json"]
        rules = load_json(REPO_ROOT / "renovate.json5", jsonc=True)["packageRules"]
        dependency_keys = {
            "matchDepNames", "matchPackageNames", "matchDepPatterns",
            "matchPackagePatterns", "matchPackagePrefixes", "matchSourceUrls",
        }
        naming_dolt = [
            index for index, rule in enumerate(rules)
            if any("dolthub/dolt" in rule.get(key, []) for key in dependency_keys)
        ]
        self.assertEqual(len(naming_dolt), 1)
        dolt_index = naming_dolt[0]
        later = rules[dolt_index + 1 :]
        self.assertEqual(
            [rule.get("matchDepNames") for rule in later], [["@beads/bd", "gastownhall/beads"]]
        )
        self.assertEqual(
            rules[dolt_index],
            {
                "matchManagers": ["custom.regex"],
                "matchDepNames": ["dolthub/dolt"],
                "minimumReleaseAge": "14 days",
                "minimumGroupSize": 2,
                "groupName": "beads dolt engine",
                "groupSlug": "dolt-engine",
                "additionalBranchPrefix": "beads-core-",
                "prBodyNotes": rules[-1]["prBodyNotes"],
                "automerge": False,
                "platformAutomerge": False,
            },
        )
        notes = rules[-1]["prBodyNotes"]
        self.assertEqual(len(notes), 1)
        self.assertIn(
            "`python3 assets/beads-upgrade-rehearsal.py --ref origin/{{{branchName}}} "
            "--stop-server`",
            notes[0],
        )
        self.assertIn("RESULT: PASS", notes[0])
        self.assertTrue((REPO_ROOT / "assets/beads-upgrade-rehearsal.py").is_file())

        pins = [
            REPO_ROOT / ".chezmoidata.yaml",
            REPO_ROOT / "private_Documents/development/container-dotfiles/devcontainers"
            "/gitlab.com/servers-homelab/homelab-IaC/dot_devcontainer/devcontainer.json.tmpl",
        ]
        for path in pins:
            with self.subTest(path=path.name):
                annotations = re.findall(
                    r"depName=dolthub/dolt(?=\s|$)", path.read_text(encoding="utf-8"), re.M
                )
                self.assertEqual(len(annotations), 1)

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
