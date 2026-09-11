"""Synthetic GitHub API responses and a credential-free gh executable."""

from __future__ import annotations

import json
import os
import shutil
import sys
import urllib.parse
from pathlib import Path

from tests.support.fixtures import write_executable, write_json


REPO_ROOT = Path(__file__).resolve().parents[2]
SHA = "a" * 40
REF = "refs/heads/topic/example"


def policy_data(**changes):
    result = {
        "$schema": "./schemas/pipeline-guard.v1.schema.json", "schema_version": 1,
        "provider": "github", "host": "github.com", "repository": "sample/project",
        "guarded_remote": "origin", "guarded_ref": "refs/heads/main",
        "workflows": [17], "timeout_seconds": 5, "max_pages": 3,
    }
    result.update(changes)
    return result


def run_data(*, workflow=17, run_id=41, attempt=1, sha=SHA, ref=REF, repository="sample/project", path=".github/workflows/build.yml", **changes):
    result = {
        "id": run_id, "workflow_id": workflow, "run_attempt": attempt,
        "repository": {"id": 29, "full_name": repository},
        "head_repository": {"id": 29, "full_name": repository},
        "head_sha": sha, "head_branch": ref.removeprefix("refs/heads/"),
        "event": "push", "path": path, "status": "completed", "conclusion": "success",
        "created_at": "2026-01-01T00:00:00Z",
    }
    result.update(changes)
    return result


def job_data(run, *, job_id=51, name="arbitrary matrix (new-runtime)", **changes):
    result = {
        "id": job_id, "run_id": run["id"], "run_attempt": run["run_attempt"],
        "head_sha": run["head_sha"], "head_branch": run["head_branch"],
        "name": name, "status": "completed", "conclusion": "success",
    }
    result.update(changes)
    return result


def runs_endpoint(run):
    query = urllib.parse.urlencode({"event": "push", "branch": run["head_branch"], "head_sha": run["head_sha"]})
    return f"repos/{run['repository']['full_name']}/actions/workflows/{run['workflow_id']}/runs?{query}&per_page=100&page=1"


def responses_for(run=None, jobs=None):
    run = run or run_data()
    jobs = [job_data(run)] if jobs is None else jobs
    repo = f"repos/{run['repository']['full_name']}"
    prefix = f"{repo}/actions/runs/{run['id']}"
    workflow = {"id": run["workflow_id"], "path": run["path"], "state": "active"}
    result = {
        repo: run["repository"],
        f"{repo}/actions/workflows/{run['workflow_id']}": workflow,
        f"{repo}/actions/workflows/{Path(run['path']).name}": workflow,
        runs_endpoint(run): {"total_count": 1, "workflow_runs": [run]},
        prefix: run,
        f"{prefix}/attempts/{run['run_attempt']}": run,
    }
    for offset in range(0, max(1, len(jobs)), 100):
        result[f"{prefix}/attempts/{run['run_attempt']}/jobs?per_page=100&page={offset // 100 + 1}"] = {"total_count": len(jobs), "jobs": jobs[offset:offset + 100]}
    return result


def install_fake_gh(directory: Path, state_path: Path, log_path: Path) -> Path:
    script = write_executable(directory / "gh", f"#!{sys.executable}\n" + '''
import json
import os
import sys
import time
from pathlib import Path

state = json.loads(Path(os.environ["FAKE_GH_STATE"]).read_text())
args = sys.argv[1:]
log = Path(os.environ["FAKE_GH_LOG"])
calls = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
entry = {"argv": args, "debug": "GH_DEBUG" in os.environ, "prompt_disabled": os.environ.get("GH_PROMPT_DISABLED") == "1"}
entry["token_matches"] = {name: os.environ.get(name) == value for name, value in state.get("expected_env", {}).items()}
with log.open("a") as stream:
    stream.write(json.dumps(entry) + "\\n")
if args[:2] == ["auth", "token"]:
    user = args[args.index("--user") + 1]
    value = state.get("accounts", {}).get(user)
    if value is None:
        print("missing synthetic account", file=sys.stderr)
        raise SystemExit(1)
    print(value)
    raise SystemExit(0)
if args[0] != "api" or args[args.index("--method") + 1] != "GET":
    raise SystemExit(98)
endpoint = args[args.index("--method") + 2]
response = state["responses"].get(endpoint)
if response is None:
    print("unexpected fake endpoint " + endpoint, file=sys.stderr)
    raise SystemExit(97)
if isinstance(response, list):
    count = sum(call["argv"] == args for call in calls)
    response = response[min(count, len(response) - 1)]
if "fake_sleep" in response:
    time.sleep(response["fake_sleep"])
if "fake_exit" in response:
    print(state.get("error_text", "synthetic API failure"), file=sys.stderr)
    raise SystemExit(response["fake_exit"])
if "fake_raw" in response:
    print(response["fake_raw"])
else:
    print(json.dumps(response))
''')
    if os.name == "nt":
        write_executable(directory / "gh.cmd", f'@"{sys.executable}" "{script}" %*\r\n@exit /b %ERRORLEVEL%\r\n')
    return script


def write_state(path: Path, responses, **extra):
    return write_json(path, {"responses": responses, **extra})


RUNTIME_FILES = ("pipeline_policy.py", "github_pipeline.py", "pipeline_runtime.py", "pipeline_evidence.py", "gitlab_pipeline_runtime.py", "pipeline_guard.py", "check-pipeline.py", "check-gitlab-pipeline.py", "resolve-python3")


def configure_github_fixture(fixture):
    for name in RUNTIME_FILES:
        shutil.copy2(REPO_ROOT / "assets" / name, fixture.main / "assets" / name)
    write_json(fixture.main / "configs/pipeline-guard.json", policy_data())
    fixture.commit_all(fixture.main, "configure synthetic workflow policy")
    fixture.git(fixture.main, "push", "origin", "main")
    fixture.git(fixture.feature, "merge", "--ff-only", "main")
    ssh = write_executable(fixture.fake_bin / "synthetic-ssh", f"#!{sys.executable}\n" + f'''
import shlex
import subprocess
import sys
if "-G" in sys.argv:
    raise SystemExit(1)
args = shlex.split(sys.argv[-1])
if len(args) != 2 or args[0] not in {{"git-upload-pack", "git-receive-pack"}} or args[1] != "sample/project.git":
    raise SystemExit(98)
raise SystemExit(subprocess.call([args[0], {str(fixture.remote)!r}]))
''')
    fixture.git(fixture.main, "config", "core.sshCommand", f'"{sys.executable}" "{ssh}"')
    fixture.git(fixture.main, "remote", "set-url", "origin", "git@github.com:sample/project.git")
    api = fixture.root / "fake-api.json"
    log = fixture.root / "fake-api-log.jsonl"
    install_fake_gh(fixture.fake_bin, api, log)
    fixture.env["FAKE_GH_STATE"], fixture.env["FAKE_GH_LOG"] = str(api), str(log)
    for name in ("GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN", "GITHUB_ENTERPRISE_TOKEN", "GH_DEBUG"):
        fixture.env.pop(name, None)
    return api, log
