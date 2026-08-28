from __future__ import annotations

import os
import shutil
import subprocess
import unittest
from pathlib import Path

from tests.support.fixtures import init_git_repository, isolated_environment, write_executable


REPO_ROOT = Path(__file__).resolve().parents[1]
POWERSHELL_IMAGE = "local/powershell-audit:lts"


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
  *'from dolt_conflicts;'*) printf 'table,num_conflicts\n' ;;
  *'from dolt_schema_conflicts;'*) printf 'table_name\n' ;;
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
if [ "${1:-}" = init ]; then exit "${BD_INIT_RC:-0}"; fi
if [ "${1:-}" = dolt ] && [ "${2:-}" = start ]; then
  exit "${BD_START_RC:-0}"
fi
if [ "${1:-}" = dolt ] && [ "${2:-}" = commit ]; then
  [ -z "${BD_COMMIT_OUTPUT:-}" ] || printf '%s\n' "$BD_COMMIT_OUTPUT"
  exit "${BD_COMMIT_RC:-0}"
fi
if [ "${1:-}" = dolt ] && [ "${2:-}" = push ]; then
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


class BeadsSyncPosixTests(BeadsSyncFixture, unittest.TestCase):
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


class BeadsSyncPowerShellTests(BeadsSyncFixture, unittest.TestCase):
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
