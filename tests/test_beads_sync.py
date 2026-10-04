from __future__ import annotations

import os
import re
import shutil
import subprocess
import unittest
from pathlib import Path

from tests.support.fixtures import init_git_repository, isolated_environment, write_executable


REPO_ROOT = Path(__file__).resolve().parents[1]
POWERSHELL_IMAGE = "local/powershell-audit:lts"
CLONE_LOCAL_FKS = (
    ("events", "fk_events_issue", "issue_id", "issues", "id"),
    ("wisp_dependencies", "fk_wisp_dep_issue", "issue_id", "wisps", "id"),
    ("wisp_dependencies", "fk_wisp_dep_wisp_target", "depends_on_wisp_id", "wisps", "id"),
    ("wisp_dependencies", "fk_wisp_dep_issue_target", "depends_on_issue_id", "issues", "id"),
    ("wisp_labels", "fk_wisp_labels_issue", "issue_id", "wisps", "id"),
    ("wisp_comments", "fk_wisp_comments_issue", "issue_id", "wisps", "id"),
    ("wisp_events", "fk_wisp_events_issue", "issue_id", "wisps", "id"),
    ("wisp_child_counters", "fk_wisp_child_counters_parent", "parent_id", "wisps", "id"),
)


def relink_sql(spec: tuple[str, str, str, str, str]) -> str:
    table, constraint, column, ref_table, ref_column = spec
    return (
        f"alter table {table} add constraint {constraint} foreign key ({column}) "
        f"references {ref_table} ({ref_column}) on delete cascade on update cascade;"
    )


def _read_calls(path: Path) -> list[tuple[str, list[str]]]:
    if not path.exists():
        return []
    calls: list[tuple[str, list[str]]] = []
    tool = ""
    arguments: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("CALL="):
            tool = line[5:]
            arguments = []
        elif line == "END" and tool:
            calls.append((tool, arguments))
            tool = ""
        elif line.startswith("ARG=") and tool:
            arguments.append(line[4:])
    return calls


def _write_fake_tools(fake_bin: Path) -> None:
    write_executable(
        fake_bin / "dolt",
        r"""#!/bin/sh
{
  printf 'CALL=dolt\n'
  for argument do printf 'ARG=%s\n' "$argument"; done
  printf 'END\n'
} >>"$BD_SYNC_TEST_LOG"

query=
take_query=0
for argument do
  if [ "$take_query" = 1 ]; then query=$argument; take_query=0; fi
  [ "$argument" != -q ] || take_query=1
done

case "$query" in
  'select 1;') exit "${DOLT_SERVER_RC:-0}" ;;
  *'information_schema.table_constraints'*)
    [ "${DOLT_FK_SCAN_RC:-0}" = 0 ] || exit "$DOLT_FK_SCAN_RC"
    printf 'item\n'
    for table in ${DOLT_FK_TABLES-events wisp_dependencies wisp_labels wisp_comments wisp_events wisp_child_counters}; do
      printf 'table:%s\n' "$table"
    done
    for fk in ${DOLT_FKS_PRESENT-events.fk_events_issue wisp_dependencies.fk_wisp_dep_issue wisp_dependencies.fk_wisp_dep_wisp_target wisp_dependencies.fk_wisp_dep_issue_target wisp_labels.fk_wisp_labels_issue wisp_comments.fk_wisp_comments_issue wisp_events.fk_wisp_events_issue wisp_child_counters.fk_wisp_child_counters_parent}; do
      printf 'fk:%s\n' "$fk"
    done
    ;;
  'select count(*) as n from '*)
    table=$(printf '%s' "$query" | sed -n 's/^select count(\*) as n from \([a-z_]*\) .*/\1/p')
    count=$(printf ' %s ' "${DOLT_ORPHANS:-}" | sed -n "s/.* $table=\([0-9]*\) .*/\1/p")
    printf 'n\n%s\n' "${count:-0}"
    ;;
  'alter table '*) exit "${DOLT_ALTER_RC:-0}" ;;
  "call dolt_merge('--abort');") exit "${DOLT_ABORT_RC:-0}" ;;
  *'from dolt_status'*)
    printf 'table_name,is_ignored\n'
    [ -z "${DOLT_DIRTY_ROWS:-}" ] || printf '%b\n' "$DOLT_DIRTY_ROWS"
    ;;
  'select name from dolt_remotes limit 1;')
    printf 'name\n'
    [ -z "${DOLT_REMOTE_NAME-origin}" ] || printf '%s\n' "${DOLT_REMOTE_NAME-origin}"
    ;;
  'select url from dolt_remotes limit 1;')
    printf 'url\n%s\n' "${DOLT_REMOTE_URL:-https://example.invalid/sync}"
    ;;
  'select active_branch() as branch;')
    printf 'branch\n'
    [ -z "${DOLT_BRANCH-main}" ] || printf '%s\n' "${DOLT_BRANCH-main}"
    ;;
  *'dolt_pull('* )
    [ -z "${DOLT_PULL_OUTPUT:-}" ] || printf '%s\n' "$DOLT_PULL_OUTPUT"
    exit "${DOLT_PULL_RC:-0}"
    ;;
  *'from dolt_conflicts;'*)
    printf 'table,num_conflicts\n'
    [ -z "${DOLT_CONFLICT_ROWS:-}" ] || printf '%b\n' "$DOLT_CONFLICT_ROWS"
    ;;
  *'from dolt_schema_conflicts;'*) printf 'table_name\n' ;;
  "show tables as of 'HEAD';")
    printf 'Tables_in_dots\n'
    printf '%b\n' "${DOLT_HEAD_TABLES-issues\nignored_schema_migrations\nwisp_events}"
    ;;
  *'from local_metadata'*)
    printf 'value\n'
    [ -z "${DOLT_BD_VERSION-1.2.2}" ] || printf '%s\n' "${DOLT_BD_VERSION-1.2.2}"
    ;;
  "show tables like 'wisp%';")
    count=${DOLT_WISPS_BEFORE:-6}
    if grep -q 'dolt_reset(' "$BD_SYNC_TEST_LOG"; then count=${DOLT_WISPS_AFTER:-$count}; fi
    printf 'Tables_in_dots (wisp%%)\n'
    index=0
    while [ "$index" -lt "$count" ]; do printf 'wisp_%s\n' "$index"; index=$((index + 1)); done
    ;;
  *'from issues;'*) printf 'n\n5\n' ;;
  *"_project_id"*) printf 'value\nlocal-id\n' ;;
esac
exit 0
""",
    )
    write_executable(
        fake_bin / "bd",
        r"""#!/bin/sh
{
  printf 'CALL=bd\n'
  for argument do printf 'ARG=%s\n' "$argument"; done
  printf 'END\n'
} >>"$BD_SYNC_TEST_LOG"

if [ "${1:-}" = export ]; then
  shift
  output=
  while [ "$#" -gt 0 ]; do
    if [ "$1" = -o ]; then output=$2; shift 2; else shift; fi
  done
  [ "${BD_EXPORT_MODE:-success}" != fail ] || exit 17
  case "${BD_EXPORT_MODE:-success}" in
    success) printf '%s\n' '{"id":"dots-1"}' >"$output" ;;
    empty) : >"$output" ;;
    invalid) printf '%s\n' '{invalid' >"$output" ;;
  esac
  exit 0
fi
if [ "${1:-}" = version ]; then
  printf '{"version":"%s"}\n' "${BD_VERSION_STRING:-1.2.2}"
  exit 0
fi
if [ "${1:-}" = init ]; then
  [ "${BD_INIT_RC:-0}" = 0 ] || exit "$BD_INIT_RC"
  mkdir -p .beads/dolt/dots
  exit 0
fi
if [ "${1:-}" = dolt ] && [ "${2:-}" = start ]; then
  exit "${BD_START_RC:-0}"
fi
if [ "${1:-}" = dolt ] && [ "${2:-}" = commit ]; then
  [ -z "${BD_COMMIT_OUTPUT:-}" ] || printf '%s\n' "$BD_COMMIT_OUTPUT"
  exit "${BD_COMMIT_RC:-0}"
fi
if [ "${1:-}" = dolt ] && [ "${2:-}" = push ]; then
  printf '%s\n' "${BD_NO_REMOTE_ADOPT-unset}" >"$BD_SYNC_TEST_LOG.push-env"
  [ -z "${BD_PUSH_OUTPUT:-}" ] || printf '%s\n' "$BD_PUSH_OUTPUT"
  exit "${BD_PUSH_RC:-0}"
fi
exit 0
""",
    )


class BeadsSyncFixture:
    def setUp(self) -> None:
        super().setUp()
        self._fixture_context = isolated_environment(prefix="beads-sync-test-")
        self.fixture = self._fixture_context.__enter__()
        self.addCleanup(self._fixture_context.__exit__, None, None, None)
        self.repo = self.fixture.root / "repo with spaces"
        init_git_repository(self.repo, env=self.fixture.env)
        assets = self.repo / "assets"
        assets.mkdir()
        for name in ("beads-sync.sh", "beads-sync.ps1"):
            shutil.copy2(REPO_ROOT / "assets" / name, assets / name)
        beads = self.repo / ".beads"
        (beads / "dolt/dots").mkdir(parents=True)
        (beads / "metadata.json").write_text(
            '{"dolt_database":"dots","dolt_server_host":"127.0.0.1",'
            '"dolt_server_user":"root","project_id":"local-id"}\n',
            encoding="utf-8",
        )
        (beads / "dolt-server.port").write_text("3307\n", encoding="utf-8")
        (beads / "config.yaml").write_text("database: dots\n", encoding="utf-8")
        subprocess.run(
            ["git", "add", ".beads"],
            cwd=self.repo,
            env=self.fixture.env,
            check=True,
            capture_output=True,
            text=True,
        )
        subprocess.run(
            ["git", "commit", "--quiet", "-m", "add beads fixture"],
            cwd=self.repo,
            env=self.fixture.env,
            check=True,
            capture_output=True,
            text=True,
        )
        _write_fake_tools(self.fixture.fake_bin)
        self.call_log = self.fixture.root / "sync-calls.log"
        self.snapshot_root = self.fixture.root / "snapshot share"
        self.snapshot_root.mkdir()
        self.env = dict(self.fixture.env)
        self.env.update(
            {
                "BD_AUTO_SNAPSHOT": "off",
                "BD_SNAPSHOT_MACHINE": "test-host",
                "BD_SNAPSHOT_ROOT": str(self.snapshot_root),
                "BD_SYNC_TEST_LOG": str(self.call_log),
                "BEADS_DOLT_SERVER_PORT": "3307",
                "DOLT_REMOTE_NAME": "origin",
                "DOLT_REMOTE_URL": "https://example.invalid/sync",
            }
        )

    def clear_calls(self) -> None:
        self.call_log.unlink(missing_ok=True)

    def calls(self, tool: str | None = None) -> list[tuple[str, list[str]]]:
        calls = _read_calls(self.call_log)
        if tool is None:
            return calls
        return [call for call in calls if call[0] == tool]

    def queries(self) -> list[str]:
        queries: list[str] = []
        for _, arguments in self.calls("dolt"):
            if "-q" in arguments:
                queries.append(arguments[arguments.index("-q") + 1])
        return queries

    def prepare_init_failure(self) -> None:
        shutil.rmtree(self.repo / ".beads/dolt")
        (self.repo / ".beads/config.local.yaml").write_text(
            "remote: git@example.invalid:private/dots\n", encoding="utf-8"
        )
        subprocess.run(
            ["git", "remote", "add", "origin", "https://example.invalid/dotfiles"],
            cwd=self.repo,
            env=self.fixture.env,
            check=True,
        )


class BeadsSyncUpgradeContract:
    """Behavior both scripts share for the bd 1.3 upgrade."""

    def test_reset_skips_ignored_tables_absent_from_head(self) -> None:
        dirty = {
            "DOLT_DIRTY_ROWS": "events,1\\nignored_schema_migrations,1",
            "DOLT_HEAD_TABLES": "issues\\nignored_schema_migrations",
        }
        status = self.run_sync("status", env_updates=dirty)
        self.assertEqual(status.returncode, 0, status.stderr)
        self.assertIn("events  (ignored, not at HEAD - clone-local, left alone)", status.stdout)

        self.clear_calls()
        pulled = self.run_sync("pull", env_updates=dirty)
        self.assertEqual(pulled.returncode, 0, pulled.stderr)
        self.assertIn("leaving clone-local table 'events' alone", pulled.stderr)
        pull_queries = [query for query in self.queries() if "dolt_pull(" in query]
        self.assertEqual(len(pull_queries), 1)
        self.assertIn("dolt_checkout('HEAD', '--', 'ignored_schema_migrations')", pull_queries[0])
        self.assertNotIn("'events'", pull_queries[0])

    def test_unlistable_head_stops_pull_before_merging(self) -> None:
        result = self.run_sync(
            "pull", env_updates={"DOLT_DIRTY_ROWS": "wisp_events,1", "DOLT_HEAD_TABLES": ""}
        )
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("could not list the tables at HEAD", result.stderr)
        self.assertFalse(any("dolt_pull(" in query for query in self.queries()))

    def test_push_sets_no_remote_adopt(self) -> None:
        result = self.run_sync("push")
        self.assertEqual(result.returncode, 0, result.stderr)
        push_env = Path(f"{self.call_log}.push-env").read_text(encoding="utf-8").strip()
        self.assertEqual(push_env, "1")
        self.assertIn(["dolt", "push"], [arguments for _, arguments in self.calls("bd")])

    def test_unreconciled_bd_version_refuses_pull_and_push(self) -> None:
        for command in ("pull", "push"):
            with self.subTest(command=command):
                self.clear_calls()
                result = self.run_sync(command, env_updates={"BD_VERSION_STRING": "1.3.1"})
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn("bd 1.3.1 has not been reconciled", result.stderr)
                self.assertIn("local_metadata.bd_version is 1.2.2", result.stderr)
                bd_calls = [arguments for _, arguments in self.calls("bd")]
                self.assertEqual(bd_calls, [["version", "--json"]])
                self.assertFalse(any("dolt_pull(" in query for query in self.queries()))

    def test_missing_recorded_bd_version_skips_the_check(self) -> None:
        result = self.run_sync("pull", env_updates={"DOLT_BD_VERSION": ""})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("no bd_version readable from local_metadata", result.stderr)
        self.assertTrue(any("dolt_pull(" in query for query in self.queries()))

    def test_init_compares_wisp_tables_before_and_after_reset(self) -> None:
        cases = (
            ("7", "7", 0, None),
            ("7", "5", 2, "bd init created 7 wisp tables but 5 survived the reset"),
            ("0", "0", 2, "bd init created no wisp tables"),
        )
        for before, after, returncode, message in cases:
            with self.subTest(before=before, after=after):
                shutil.rmtree(self.repo / ".beads/dolt", ignore_errors=True)
                self.clear_calls()
                result = self.run_sync(
                    "init",
                    env_updates={
                        "BD_SYNC_REMOTE": "https://example.invalid/sync",
                        "BEADS_DOLT_SERVER_PORT": "",
                        "DOLT_WISPS_AFTER": after,
                        "DOLT_WISPS_BEFORE": before,
                    },
                )
                self.assertEqual(result.returncode, returncode, result.stdout + result.stderr)
                if message:
                    self.assertIn(message, result.stderr)
                else:
                    self.assertIn("init complete: 5 issues adopted", result.stdout)

    def alter_queries(self) -> list[str]:
        return [query for query in self.queries() if query.startswith("alter table ")]

    def test_pull_relinks_only_severed_clone_local_fks(self) -> None:
        present = " ".join(
            f"{table}.{constraint}"
            for table, constraint, *_ in CLONE_LOCAL_FKS
            if constraint not in {"fk_events_issue", "fk_wisp_labels_issue"}
        )
        result = self.run_sync("pull", env_updates={"DOLT_FKS_PRESENT": present})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            self.alter_queries(),
            [relink_sql(CLONE_LOCAL_FKS[0]), relink_sql(CLONE_LOCAL_FKS[4])],
        )
        self.assertIn("re-linked events.fk_events_issue", result.stderr)
        self.assertIn("re-linked wisp_labels.fk_wisp_labels_issue", result.stderr)

    def test_intact_clone_local_fks_are_left_alone(self) -> None:
        result = self.run_sync("pull")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.alter_queries(), [])

    def test_orphans_are_deleted_before_relink(self) -> None:
        result = self.run_sync(
            "clean",
            env_updates={
                "DOLT_FKS_PRESENT": "events.fk_events_issue",
                "DOLT_FK_TABLES": "events wisp_labels",
                "DOLT_ORPHANS": "wisp_labels=2",
            },
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        queries = self.queries()
        delete_index = next(
            index for index, query in enumerate(queries) if query.startswith("delete from wisp_labels ")
        )
        self.assertEqual(self.alter_queries(), [relink_sql(CLONE_LOCAL_FKS[4])])
        self.assertLess(delete_index, queries.index(relink_sql(CLONE_LOCAL_FKS[4])))
        self.assertIn("removed 2 orphaned row(s) from wisp_labels", result.stderr)

    def test_clean_dry_run_lists_without_relinking(self) -> None:
        result = self.run_sync("clean", self.dry_run_flag, env_updates={"DOLT_FKS_PRESENT": ""})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.alter_queries(), [])
        self.assertEqual(result.stderr.count("[dry-run] would re-link "), len(CLONE_LOCAL_FKS))

    def test_status_reports_severed_clone_local_fks(self) -> None:
        result = self.run_sync(
            "status", env_updates={"DOLT_FKS_PRESENT": "events.fk_events_issue wisp_labels.fk_wisp_labels_issue"}
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Clone-local FKs: 6 severed - run 'clean' to re-link", result.stdout)
        self.assertEqual(self.alter_queries(), [])

        intact = self.run_sync("status")
        self.assertNotIn("Clone-local FKs", intact.stdout)

    def test_init_relinks_after_the_reset(self) -> None:
        shutil.rmtree(self.repo / ".beads/dolt", ignore_errors=True)
        result = self.run_sync(
            "init",
            env_updates={
                "BD_SYNC_REMOTE": "https://example.invalid/sync",
                "BEADS_DOLT_SERVER_PORT": "",
                "DOLT_FKS_PRESENT": "",
            },
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        queries = self.queries()
        reset_index = next(index for index, query in enumerate(queries) if "dolt_reset(" in query)
        alters = self.alter_queries()
        self.assertEqual(alters, [relink_sql(spec) for spec in CLONE_LOCAL_FKS])
        self.assertLess(reset_index, queries.index(alters[0]))

    def test_failed_relink_warns_without_failing_pull(self) -> None:
        result = self.run_sync(
            "pull", env_updates={"DOLT_FKS_PRESENT": "", "DOLT_ALTER_RC": "1"}
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("WARNING: could not re-link events.fk_events_issue", result.stderr)
        self.assertEqual(len(self.alter_queries()), len(CLONE_LOCAL_FKS))

    def test_failed_pull_still_relinks(self) -> None:
        result = self.run_sync("pull", env_updates={"DOLT_FKS_PRESENT": "", "DOLT_PULL_RC": "1"})
        self.assertNotEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(self.alter_queries()), len(CLONE_LOCAL_FKS))


class BeadsSyncPosixTests(BeadsSyncUpgradeContract, BeadsSyncFixture, unittest.TestCase):
    dry_run_flag = "--dry-run"
    def run_sync(
        self, *arguments: str, env_updates: dict[str, str] | None = None
    ) -> subprocess.CompletedProcess[str]:
        env = dict(self.env)
        if env_updates:
            env.update(env_updates)
        return subprocess.run(
            [str(self.repo / "assets/beads-sync.sh"), *arguments],
            cwd=self.repo,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_conflicting_pull_relinks_after_the_abort(self) -> None:
        result = self.run_sync(
            "pull", env_updates={"DOLT_CONFLICT_ROWS": "issues,1", "DOLT_FKS_PRESENT": ""}
        )
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("the merge produced conflicts", result.stderr)
        queries = self.queries()
        abort_index = queries.index("call dolt_merge('--abort');")
        alters = self.alter_queries()
        self.assertEqual(len(alters), len(CLONE_LOCAL_FKS))
        self.assertLess(abort_index, queries.index(alters[0]))

    def test_failed_abort_leaves_the_conflicted_working_set_alone(self) -> None:
        result = self.run_sync(
            "pull",
            env_updates={
                "DOLT_ABORT_RC": "1",
                "DOLT_CONFLICT_ROWS": "issues,1",
                "DOLT_FKS_PRESENT": "",
                "DOLT_ORPHANS": "wisp_labels=2",
            },
        )
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("this working set is STILL conflicted", result.stderr)
        self.assertEqual(self.alter_queries(), [])
        self.assertFalse(any(query.startswith("delete from ") for query in self.queries()))

    def test_status_and_dirty_reset_refusal(self) -> None:
        cases = (("", "Working set clean"), ("wisp_events,1", "safe to clean"))
        for rows, message in cases:
            with self.subTest(rows=rows):
                result = self.run_sync("status", env_updates={"DOLT_DIRTY_ROWS": rows})
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn(message, result.stdout)

        self.clear_calls()
        refused = self.run_sync(
            "clean", env_updates={"DOLT_DIRTY_ROWS": "issues,0\\nwisp_events,1"}
        )
        self.assertEqual(refused.returncode, 2)
        self.assertIn("issues", refused.stderr)
        self.assertFalse(any("dolt_checkout" in query for query in self.queries()))

    def test_pull_resets_and_merges_in_one_dolt_session(self) -> None:
        result = self.run_sync(
            "pull", env_updates={"DOLT_DIRTY_ROWS": "ignored_schema_migrations,1"}
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        pull_queries = [query for query in self.queries() if "dolt_pull(" in query]
        self.assertEqual(len(pull_queries), 1)
        self.assertIn("dolt_checkout('HEAD', '--', 'ignored_schema_migrations')", pull_queries[0])
        self.assertIn("dolt_pull('origin','main')", pull_queries[0])
        self.assertFalse(
            any("dolt_checkout" in query for query in self.queries() if query not in pull_queries)
        )

    def test_push_failure_redacts_every_supported_remote_shape(self) -> None:
        private_values = (
            "ssh://git@private.example:22/team/dots",
            "git+ssh://git@private.example/team/dots",
            "https://token@private.example/team/dots",
            "git@private.example:team/dots",
        )
        for private_value in private_values:
            with self.subTest(private_value=private_value):
                result = self.run_sync(
                    "push",
                    env_updates={"BD_PUSH_OUTPUT": private_value, "BD_PUSH_RC": "31"},
                )
                self.assertEqual(result.returncode, 31)
                combined = result.stdout + result.stderr
                self.assertNotIn(private_value, combined)
                self.assertIn("<REDACTED>", combined)

    def test_failed_init_restores_origin_and_local_config(self) -> None:
        self.prepare_init_failure()
        result = self.run_sync("init", env_updates={"BD_INIT_RC": "19"})
        self.assertNotEqual(result.returncode, 0)
        remote = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=self.repo,
            env=self.fixture.env,
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertEqual(remote.stdout.strip(), "https://example.invalid/dotfiles")
        self.assertTrue((self.repo / ".beads/config.local.yaml").is_file())
        self.assertFalse((self.repo / ".beads/config.local.yaml.init-hold").exists())
        remotes = subprocess.run(
            ["git", "remote"],
            cwd=self.repo,
            env=self.fixture.env,
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertNotIn("beads-init-hold", remotes.stdout.splitlines())


class BeadsSyncPowerShellTests(BeadsSyncUpgradeContract, BeadsSyncFixture, unittest.TestCase):
    dry_run_flag = "-DryRun"
    @classmethod
    def setUpClass(cls) -> None:
        if os.name == "nt":
            raise unittest.SkipTest("native Windows execution is opportunistic for this suite")
        cls.pwsh = shutil.which("pwsh")
        cls.docker = shutil.which("docker")
        if cls.pwsh:
            return
        if not cls.docker:
            raise unittest.SkipTest("pwsh and docker are unavailable")
        inspected = subprocess.run(
            [cls.docker, "image", "inspect", POWERSHELL_IMAGE],
            capture_output=True,
            text=True,
            check=False,
        )
        if inspected.returncode != 0:
            raise unittest.SkipTest(f"local image {POWERSHELL_IMAGE} is unavailable")

    def run_sync(
        self, *arguments: str, env_updates: dict[str, str] | None = None
    ) -> subprocess.CompletedProcess[str]:
        env = dict(self.env)
        if env_updates:
            env.update(env_updates)
        script = self.repo / "assets/beads-sync.ps1"
        if self.pwsh:
            return subprocess.run(
                [self.pwsh, "-NoProfile", "-File", str(script), *arguments],
                cwd=self.repo,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )

        def container_path(value: str) -> str:
            relative = Path(value).resolve().relative_to(self.fixture.root.resolve())
            return "/fixture/" + relative.as_posix()

        allowed_names = {
            "GIT_CONFIG_GLOBAL",
            "GIT_CONFIG_SYSTEM",
            "GIT_TERMINAL_PROMPT",
            "HOME",
            "PATH",
            "TEMP",
            "TMP",
            "TMPDIR",
            "USERPROFILE",
        }
        container_env = {
            name: value
            for name, value in env.items()
            if name in allowed_names
            or name.startswith(("BD_", "BEADS_", "DOLT_", "XDG_"))
        }
        for name in (
            "BD_SNAPSHOT_ROOT",
            "BD_SYNC_TEST_LOG",
            "GIT_CONFIG_GLOBAL",
            "GIT_CONFIG_SYSTEM",
            "HOME",
            "TEMP",
            "TMP",
            "TMPDIR",
            "USERPROFILE",
            "XDG_CACHE_HOME",
            "XDG_CONFIG_HOME",
        ):
            if container_env.get(name):
                container_env[name] = container_path(container_env[name])
        container_env["PATH"] = "/fixture/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
        argv = [
            self.docker,
            "run",
            "--rm",
            "--network",
            "none",
            "--user",
            f"{os.getuid()}:{os.getgid()}",
        ]
        argv.extend(
            [
                "--mount",
                f"type=bind,src={self.fixture.root},dst=/fixture",
                "--workdir",
                container_path(str(self.repo)),
            ]
        )
        for name, value in sorted(container_env.items()):
            argv.extend(["--env", f"{name}={value}"])
        argv.extend(
            [
                "--entrypoint",
                "pwsh",
                POWERSHELL_IMAGE,
                "-NoProfile",
                "-File",
                container_path(str(script)),
                *arguments,
            ]
        )
        return subprocess.run(argv, capture_output=True, text=True, check=False)

    def test_init_compares_wisp_tables_before_and_after_reset(self) -> None:
        if not self.pwsh:
            self.skipTest(f"{POWERSHELL_IMAGE} has no git, which init needs")
        super().test_init_compares_wisp_tables_before_and_after_reset()

    def test_init_relinks_after_the_reset(self) -> None:
        if not self.pwsh:
            self.skipTest(f"{POWERSHELL_IMAGE} has no git, which init needs")
        super().test_init_relinks_after_the_reset()

    def test_status_refusal_and_single_session_pull_match_posix_contract(self) -> None:
        refused = self.run_sync(
            "clean", env_updates={"DOLT_DIRTY_ROWS": "issues,0\\nwisp_events,1"}
        )
        self.assertEqual(refused.returncode, 2, refused.stderr)
        self.assertIn("issues", refused.stderr)
        self.assertFalse(any("dolt_checkout" in query for query in self.queries()))

        self.clear_calls()
        pulled = self.run_sync(
            "pull", env_updates={"DOLT_DIRTY_ROWS": "ignored_schema_migrations,1"}
        )
        self.assertEqual(pulled.returncode, 0, pulled.stderr)
        pull_queries = [query for query in self.queries() if "dolt_pull(" in query]
        self.assertEqual(len(pull_queries), 1)
        self.assertIn(
            "dolt_checkout('HEAD', '--', 'ignored_schema_migrations')",
            pull_queries[0],
        )

    def test_push_failure_redacts_scp_remote(self) -> None:
        private_value = "git@private.example:team/dots"
        result = self.run_sync(
            "push", env_updates={"BD_PUSH_OUTPUT": private_value, "BD_PUSH_RC": "31"}
        )
        self.assertNotEqual(result.returncode, 0)
        combined = result.stdout + result.stderr
        self.assertNotIn(private_value, combined)
        self.assertIn("<REDACTED>", combined)
        self.assertIn("bd dolt push failed", result.stderr)

    def test_failed_init_restores_origin_and_local_config(self) -> None:
        self.prepare_init_failure()
        result = self.run_sync("init", env_updates={"BD_INIT_RC": "19"})
        self.assertNotEqual(result.returncode, 0)
        remote = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=self.repo,
            env=self.fixture.env,
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertEqual(remote.stdout.strip(), "https://example.invalid/dotfiles")
        self.assertTrue((self.repo / ".beads/config.local.yaml").is_file())
        self.assertFalse((self.repo / ".beads/config.local.yaml.init-hold").exists())


class CloneLocalFkListParityTests(unittest.TestCase):
    def script_specs(self, name: str) -> list[tuple[str, ...]]:
        text = (REPO_ROOT / "assets" / name).read_text(encoding="utf-8")
        return [
            tuple(match.split("|"))
            for match in re.findall(r"""^\s+["']((?:[a-z_]+\|){4}[a-z_]+)["']\s*$""", text, re.MULTILINE)
        ]

    def test_both_scripts_carry_the_same_list(self) -> None:
        self.assertEqual(self.script_specs("beads-sync.sh"), list(CLONE_LOCAL_FKS))
        self.assertEqual(self.script_specs("beads-sync.ps1"), list(CLONE_LOCAL_FKS))
