"""Disposable main/feature Git worktrees with a fake bd for merge-helper tests."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from tests.support.fixtures import (
    load_script_module,
    run_git,
    write_executable,
    write_json,
    write_python_command,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
SOURCE_HELPER = REPO_ROOT / "assets" / "agent-wt-merge"
SOURCE_RESOLVER = REPO_ROOT / "assets" / "resolve-python3"
SOURCE_RUNTIME = REPO_ROOT / "assets" / "gitlab_pipeline_runtime.py"
# Windows cannot start a shebang script by path, so the helper's #!/bin/sh entry
# point is handed to sh explicitly. Git Bash's bash drops positional arguments
# when invoked this way, so sh is the only dependable launcher here.
HELPER_LAUNCH_PREFIX = ["sh"] if os.name == "nt" else []
EXEC_ENV_KEYS = (
    "AGENT_WT_MERGE_EXEC_CHAIN",
    "AGENT_WT_MERGE_DELEGATED_FROM",
    "AGENT_WT_MERGE_REEXEC_AFTER_UPDATE",
)


def load_helper_module():
    return load_script_module(SOURCE_HELPER, "agent_wt_merge_test_module", add_parent_to_path=True)


class GitFixture:
    """Main and feature worktrees of one repository, pushed to a bare remote.

    The environment is copied from the process, which the runner or the tests
    package has already replaced with the synthetic test environment.
    """

    def __init__(self, *, feature_commit: bool = True, ci_gated: bool = True) -> None:
        self.ci_gated = ci_gated
        self._temporary = tempfile.TemporaryDirectory(prefix="agent wt merge ")
        self.root = Path(self._temporary.name).resolve()
        self.main = self.root / "main worktree"
        self.feature = self.root / "feature worktree"
        self.remote = self.root / "origin remote.git"
        self.fake_bin = self.root / "fake bin"
        self.bd_log = self.root / "bd log.json"
        self.bd_issue = self.root / "bd issue.json"
        self.git_config = self.root / "isolated gitconfig"
        self.git_config.write_text("", encoding="utf-8")
        self.env = os.environ.copy()
        self.env["GIT_CONFIG_GLOBAL"] = str(self.git_config)
        self.env["GIT_CONFIG_SYSTEM"] = os.devnull
        self.env["GIT_TERMINAL_PROMPT"] = "0"

        self.main.mkdir()
        self.git(self.main, "init", "-b", "main")
        self.git(self.main, "config", "user.name", "Test User")
        self.git(self.main, "config", "user.email", "test@example.com")
        self.install_helper(self.main)
        if ci_gated:
            self.install_pipeline_files(self.main)
        (self.main / "base.txt").write_text("base\n", encoding="utf-8")
        self.commit_all(self.main, "initial")

        self.git(self.root, "init", "--bare", "-b", "main", str(self.remote))
        self.git(self.main, "remote", "add", "origin", str(self.remote))
        self.git(self.main, "push", "-u", "origin", "main")
        self.git(self.main, "worktree", "add", "-b", "feature", str(self.feature))
        self.git(self.feature, "config", "user.name", "Test User")
        self.git(self.feature, "config", "user.email", "test@example.com")
        if feature_commit:
            self.commit_feature("feature.txt", "feature\n", "feature")
        elif ci_gated:
            self.publish_feature()
        self.set_override(ci_gated)

        self.fake_bin.mkdir()
        if os.name == "nt":
            for actor in ("oc", "cc"):
                shutil.copy2(REPO_ROOT / "dot_local" / f"executable_{actor}-commit.ps1",
                             self.fake_bin / f"{actor}-commit.ps1")
        else:
            for actor in ("oc", "cc"):
                wrapper = self.fake_bin / f"{actor}-commit"
                shutil.copy2(REPO_ROOT / "bin" / f"executable_{actor}-commit", wrapper)
                wrapper.chmod(0o700)
        write_python_command(
            self.fake_bin,
            "bd",
            """#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path

log_path = os.environ.get("FAKE_BD_LOG")
issue_path = Path(os.environ["FAKE_BD_ISSUE"])
args = sys.argv[1:]
if args[0] == "show":
    issue = json.loads(issue_path.read_text(encoding="utf-8")) if issue_path.exists() else {"id": args[1], "status": "in_progress", "notes": ""}
    if os.environ.get("FAKE_BD_CHANGE_ANCHOR") and os.environ.get("FAKE_BD_SHOW_COUNT"):
        count_path = Path(os.environ["FAKE_BD_SHOW_COUNT"])
        count = int(count_path.read_text(encoding="utf-8")) if count_path.exists() else 0
        count_path.write_text(str(count + 1), encoding="utf-8")
        if count == 1:
            anchor = json.loads(issue["notes"].removeprefix("beads-work anchor: "))
            anchor["started_at"] = "2026-09-30T13:00:00Z"
            issue["notes"] = "beads-work anchor: " + json.dumps(anchor)
            issue_path.write_text(json.dumps(issue), encoding="utf-8")
    if os.environ.get("FAKE_BD_SHOW_FAIL") or (os.environ.get("FAKE_BD_READBACK_FAIL") and issue["status"] == "closed"):
        raise SystemExit(17)
    if os.environ.get("FAKE_BD_ENVELOPE"):
        print(json.dumps({"schema_version": 1, "data": [issue]}))
    else:
        print(json.dumps([issue]))
    raise SystemExit(0)
if log_path:
    Path(log_path).write_text(json.dumps(args), encoding="utf-8")
if os.environ.get("FAKE_BD_FAIL"):
    print("simulated bd failure", file=sys.stderr)
    raise SystemExit(17)
if os.environ.get("FAKE_BD_CLOSE_REFUSAL"):
    print(os.environ["FAKE_BD_CLOSE_REFUSAL"], file=sys.stderr)
    raise SystemExit(1)
issue = json.loads(issue_path.read_text(encoding="utf-8")) if issue_path.exists() else {"id": args[1], "notes": ""}
issue.update(status="closed", close_reason=args[args.index("--reason") + 1])
issue_path.write_text(json.dumps(issue), encoding="utf-8")
if os.environ.get("FAKE_BD_REMOVE_STATE"):
    Path(os.environ["FAKE_BD_REMOVE_STATE"]).unlink(missing_ok=True)
if os.environ.get("FAKE_BD_REPLACE_STATE"):
    state_path = Path(os.environ["FAKE_BD_REPLACE_STATE"])
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["started_at"] = "2026-09-30T13:00:00Z"
    state_path.write_text(json.dumps(state), encoding="utf-8")
""",
        )

    @property
    def main_helper(self) -> Path:
        return self.main / "assets" / "agent-wt-merge"

    @property
    def feature_helper(self) -> Path:
        return self.feature / "assets" / "agent-wt-merge"

    def cleanup(self) -> None:
        self._temporary.cleanup()

    def git(self, cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        return run_git(cwd, *args, env=self.env, check=check)

    def output(self, cwd: Path, *args: str) -> str:
        return self.git(cwd, *args).stdout.strip()

    def install_helper(self, worktree: Path, text: str | None = None) -> Path:
        helper = worktree / "assets" / "agent-wt-merge"
        helper.parent.mkdir(parents=True, exist_ok=True)
        # The helper's sh entry point execs the interpreter this resolver picks,
        # so it ships with every copy regardless of pipeline mode.
        shutil.copy2(SOURCE_RESOLVER, helper.parent / SOURCE_RESOLVER.name)
        if text is None:
            shutil.copy2(SOURCE_HELPER, helper)
            if self.ci_gated:
                shutil.copy2(SOURCE_RUNTIME, helper.parent / SOURCE_RUNTIME.name)
        else:
            write_executable(helper, text)
        return helper

    def install_pipeline_files(self, worktree: Path) -> None:
        write_json(
            worktree / "configs" / "gitlab-pipeline-guard.json",
            {
                "$schema": "./schemas/gitlab-pipeline-guard.v1.schema.json",
                "schema_version": 1,
                "api_url": "http://127.0.0.1:1/api/v4",
                "project_id": 44618209,
                "guarded_remote": "origin",
                "guarded_ref": "refs/heads/main",
                "required_job": "linux-fast",
                "timeout_seconds": 1,
            },
        )
        write_executable(
            worktree / "assets" / "check-gitlab-pipeline.py",
            """#!/usr/bin/env python3
import json
import os
import subprocess
import sys
from pathlib import Path

if "--check-sha" not in sys.argv:
    raise SystemExit(0)
sha = sys.argv[sys.argv.index("--check-sha") + 1]
repo = Path(sys.argv[sys.argv.index("--repo-root") + 1])
common = subprocess.run(
    ["git", "-C", str(repo), "rev-parse", "--path-format=absolute", "--git-common-dir"],
    check=True,
    text=True,
    stdout=subprocess.PIPE,
).stdout.strip()
index = -1
if (Path(common) / "pipeline-guard.override").is_file():
    outcome = "bypass"
else:
    sequence = [item for item in os.environ.get("FAKE_PIPELINE_SEQUENCE", "").split(",") if item]
    if sequence:
        counter_path = Path(os.environ["FAKE_PIPELINE_COUNTER"])
        try:
            index = int(counter_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            index = 0
        counter_path.write_text(str(index + 1), encoding="utf-8")
        outcome = sequence[min(index, len(sequence) - 1)]
    else:
        outcome = os.environ.get("FAKE_PIPELINE_OUTCOME", "success")
move_to = os.environ.get("FAKE_PIPELINE_MOVE_FEATURE_TO")
if move_to:
    subprocess.run(
        ["git", "-C", str(repo), "push", "--force", "origin", f"{move_to}:refs/heads/feature"],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
move_main_to = os.environ.get("FAKE_PIPELINE_MOVE_MAIN_TO")
if move_main_to:
    subprocess.run(
        ["git", "-C", str(repo), "push", "--force", "origin", f"{move_main_to}:refs/heads/main"],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
move_main_ci_to = os.environ.get("FAKE_PIPELINE_MOVE_MAIN_CI_TO")
move_main_ci_on_index = os.environ.get("FAKE_PIPELINE_MOVE_MAIN_CI_ON_INDEX")
if move_main_ci_to and (not move_main_ci_on_index or move_main_ci_on_index == str(index)):
    subprocess.run(
        [
            "git", "-C", str(repo), "push", "--force", "origin",
            f"{move_main_ci_to}:refs/heads/ci/main/{sha}",
        ],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
payload = {
    "schema_version": 1,
    "outcome": outcome,
    "sha": sha,
    "required_job": None if outcome in {"bypass", "error"} else "linux-fast",
    "detail": f"fake {outcome}",
    "pipeline_url": "https://gitlab.example/pipeline/1" if outcome == "success" else None,
}
print(json.dumps(payload, sort_keys=True))
raise SystemExit(0 if outcome in {"success", "bypass"} else 1)
""",
        )

    def commit_all(self, cwd: Path, message: str) -> str:
        self.git(cwd, "add", "-A")
        self.git(cwd, "commit", "-m", message)
        return self.output(cwd, "rev-parse", "HEAD")

    def commit_feature(self, name: str, content: str, message: str) -> str:
        (self.feature / name).write_text(content, encoding="utf-8")
        sha = self.commit_all(self.feature, message)
        if self.ci_gated:
            self.publish_feature()
        return sha

    def commit_main(self, name: str, content: str, message: str) -> str:
        (self.main / name).write_text(content, encoding="utf-8")
        return self.commit_all(self.main, message)

    def publish_feature(self) -> None:
        self.git(self.feature, "push", "origin", "HEAD:refs/heads/feature")

    def remote_feature_sha(self) -> str | None:
        return self.remote_ref_sha("refs/heads/feature")

    def remote_ref_sha(self, remote_ref: str) -> str | None:
        result = self.git(
            self.main,
            "ls-remote",
            "--heads",
            "origin",
            remote_ref,
        )
        line = result.stdout.strip()
        return line.split()[0] if line else None

    def set_override(self, enabled: bool) -> Path:
        common_dir = Path(
            self.output(
                self.feature,
                "rev-parse",
                "--path-format=absolute",
                "--git-common-dir",
            )
        )
        override = common_dir / "pipeline-guard.override"
        if enabled:
            override.touch()
        else:
            override.unlink(missing_ok=True)
        return override

    def run_helper(
        self,
        helper: Path,
        cwd: Path,
        *args: str,
        extra_env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        env = self.env.copy()
        for key in EXEC_ENV_KEYS:
            env.pop(key, None)
        env["PATH"] = str(self.fake_bin) + os.pathsep + env.get("PATH", "")
        env["FAKE_BD_LOG"] = str(self.bd_log)
        env["FAKE_BD_ISSUE"] = str(self.bd_issue)
        if extra_env:
            env.update(extra_env)
        return subprocess.run(
            [*HELPER_LAUNCH_PREFIX, str(helper), *args],
            cwd=str(cwd),
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

    def write_state(self, *, started_sha: str, issue_id: str = "dots-test") -> Path:
        state_path = self.feature / ".beads" / "in-progress-opencode.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)
        write_json(
            state_path,
            {
                "id": issue_id,
                "branch": "feature",
                "worktree_path": str(self.feature),
                "started_sha": started_sha,
            },
        )
        return state_path

    def write_anchor(self, *, actor: str = "OpenCode", issue_id: str = "dots-test", **overrides) -> dict:
        anchor = {
            "id": issue_id, "agent": actor, "branch": "feature",
            "worktree_path": str(self.feature),
            "started_sha": self.output(self.main, "rev-parse", "HEAD"),
            "started_at": "2026-09-30T12:00:00Z",
        }
        anchor.update(overrides)
        write_json(self.bd_issue, {"id": issue_id, "status": "in_progress",
                                   "notes": "beads-work anchor: " + json.dumps(anchor)})
        return anchor

    def write_ai_wt_session(self, session_id: str = "test-session") -> Path:
        metadata_path = self.main / ".ai-wt" / "sessions" / f"{session_id}.json"
        metadata_path.parent.mkdir(parents=True, exist_ok=True)
        write_json(
            metadata_path,
            {
                "session_id": session_id,
                "worktree_path": str(self.feature),
                "branch_created": True,
            },
        )
        return metadata_path

    def upstream_clone(self) -> Path:
        upstream = self.root / "upstream clone"
        self.git(self.root, "clone", str(self.remote), str(upstream))
        self.git(upstream, "config", "user.name", "Upstream User")
        self.git(upstream, "config", "user.email", "upstream@example.com")
        return upstream
