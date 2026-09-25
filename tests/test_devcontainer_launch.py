from __future__ import annotations

import fcntl
import json
import os
import pty
import select
import struct
import subprocess
import sys
import termios
import time
import unittest
from pathlib import Path

from tests.support.fixtures import isolated_environment, read_json_lines, write_executable


REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE = REPO_ROOT / "bin/executable_devcontainer-launch.tmpl"
CONTAINER_ID = "a" * 64
SECOND_ID = "b" * 64
FOLDER = "devcontainer.local_folder"
CONFIG = "devcontainer.config_file"


def container(workspace, config, *, identifier=CONTAINER_ID, state="running", extra=None):
    return {"id": identifier, "state": state,
            "labels": {FOLDER: str(workspace), CONFIG: str(config)} | (extra or {})}


def run_launcher(
    script: Path,
    *args: str,
    env: dict[str, str],
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["/bin/bash", str(script), *args],
        env=env,
        check=False,
        text=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def write_append_logger(path: Path, log: Path) -> Path:
    return write_executable(
        path,
        f"#!{sys.executable}\n"
        "import json, os, pathlib, sys\n"
        f"log = pathlib.Path({str(log)!r})\n"
        "with log.open('a', encoding='utf-8') as stream:\n"
        "    stream.write(json.dumps(sys.argv[1:]) + '\\n')\n"
        "if sys.argv[1] == 'up' and 'FAKE_AFTER_UP' in os.environ:\n"
        "    pathlib.Path(os.environ['FAKE_CONTAINERS']).write_text(os.environ['FAKE_AFTER_UP'])\n"
        "code = os.environ.get('FAKE_' + sys.argv[1].upper() + '_EXIT', os.environ.get('FAKE_CLI_EXIT', '0'))\n"
        "raise SystemExit(int(code))\n",
    )


class DevcontainerLaunchTests(unittest.TestCase):
    def prepare(self, fixture) -> tuple[Path, Path, Path, dict[str, str]]:
        content = SOURCE.read_text(encoding="utf-8").replace(
            "{{ .chezmoi.homeDir }}", str(fixture.home)
        )
        script = write_executable(fixture.root / "devcontainer-launch", content)
        write_executable(fixture.fake_bin / "uname", "#!/bin/sh\nprintf 'Darwin\\n'\n")

        workspace = fixture.home / "Workspace with spaces"
        config = workspace / ".devcontainer/devcontainer.json"
        config.parent.mkdir(parents=True)
        config.write_text("{}\n", encoding="utf-8")

        manifest = fixture.root / "launch manifest.jsonc"
        manifest.write_text(
            json.dumps(
                {
                    "$schema": "./schemas/devcontainer-sync.v1.schema.json",
                    "schema_version": 1,
                    "devcontainers": {
                        "sample-tool": {
                            "launcher": {
                                "enabled": True,
                                "display_name": "Sample Tool",
                                "aliases": ["sample", "st"],
                                "env_prefix": "SAMPLE_TOOL",
                                "shell": ["zsh", "-l"],
                                "platforms": {
                                    "darwin": {
                                        "workspace_folder": "{home}/Workspace with spaces",
                                        "config": "{workspace}/.devcontainer/devcontainer.json",
                                    }
                                },
                            }
                        },
                        "disabled": {
                            "launcher": {
                                "enabled": False,
                                "platforms": {
                                    "darwin": {"workspace_folder": "/unused", "config": "/unused"}
                                },
                            }
                        },
                    },
                },
                indent=2,
            )
            + "\n// trailing JSONC comment\n",
            encoding="utf-8",
        )
        docker = write_executable(
            fixture.fake_bin / "docker",
            f"#!{sys.executable}\n" + r'''
import json, os, pathlib, re, sys
args = sys.argv[1:]
with pathlib.Path(os.environ['FAKE_DOCKER_LOG']).open('a') as stream:
    stream.write(json.dumps(args) + '\n')
if os.environ.get('FAKE_DOCKER_FAIL') == args[0]:
    print('SECRET-DOCKER-ERROR', file=sys.stderr)
    sys.exit(42)
data = json.loads(pathlib.Path(os.environ['FAKE_CONTAINERS']).read_text())
if data is None:
    data = [{'id': 'a' * 64, 'state': 'running', 'labels': {
        'devcontainer.local_folder': os.environ.get('SAMPLE_TOOL_WORKSPACE', os.environ['FAKE_WORKSPACE']),
        'devcontainer.config_file': os.environ.get('SAMPLE_TOOL_CONFIG', os.environ['FAKE_CONFIG'])}}]
if args[0] == 'ps':
    filters = [args[index + 1][6:].split('=', 1) for index, value in enumerate(args) if value == '--filter']
    for item in data:
        if all(item['labels'].get(key) == value for key, value in filters):
            print(item['id'])
elif args[0] == 'inspect':
    item = next(item for item in data if item['id'] == args[-1])
    template = args[args.index('--format') + 1]
    keys = re.findall(r'index .Config.Labels "([^"]+)"', template)
    print(json.dumps({'id': item['id'], 'state': item['state'],
                      'labels': {key: item['labels'].get(key) for key in keys}}))
elif args[0] not in ('stop', 'rm'):
    sys.exit(99)
''',
        )
        data = fixture.root / "containers.json"
        data.write_text("null")
        cli = write_append_logger(fixture.fake_bin / "devcontainer", fixture.root / "devcontainer.jsonl")
        env = {key: value for key, value in fixture.env.items()
               if not key.startswith(("FAKE_", "SAMPLE_TOOL_"))}
        env.update({"DEVCONTAINER_LAUNCH_MANIFEST": str(manifest),
                    "DOCKER_CLI": str(docker), "DEVCONTAINER_CLI": str(cli),
                    "FAKE_DOCKER_LOG": str(fixture.root / "docker.jsonl"),
                    "FAKE_CONTAINERS": str(data), "FAKE_WORKSPACE": str(workspace),
                    "FAKE_CONFIG": str(config)})
        return script, workspace, config, env

    def base_args(self, workspace, config, env, labels=None):
        labels = labels or {FOLDER: str(workspace), CONFIG: str(config)}
        return ["--workspace-folder", str(workspace), "--config", str(config),
                "--docker-path", env["DOCKER_CLI"],
                *[arg for key, value in labels.items() for arg in ("--id-label", f"{key}={value}")]]

    def configure_identity(self, env, labels, platform="darwin"):
        manifest = Path(env["DEVCONTAINER_LAUNCH_MANIFEST"])
        payload = json.loads(manifest.read_text().split("\n//")[0])
        platforms = payload["devcontainers"]["sample-tool"]["launcher"]["platforms"]
        spec = platforms.pop(next(iter(platforms)))
        spec["identity_labels"] = labels
        platforms[platform] = spec
        manifest.write_text(json.dumps(payload))

    def set_containers(self, env, data):
        Path(env["FAKE_CONTAINERS"]).write_text(json.dumps(data))

    def assert_no_mutation(self, fixture):
        self.assertEqual(read_json_lines(fixture.root / "devcontainer.jsonl"), [])
        self.assertTrue(all(call[0] in ("ps", "inspect") for call in read_json_lines(fixture.root / "docker.jsonl")))

    def test_list_parses_jsonc_and_reports_aliases(self) -> None:
        with isolated_environment(prefix="devcontainer list ") as fixture:
            script, _, _, env = self.prepare(fixture)

            result = run_launcher(script, "--list", env=env)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, "sample-tool\tSample Tool aliases=sample,st\n")
            self.assertNotIn("disabled", result.stdout)

    def test_alias_shell_action_runs_up_then_exact_shell_argv(self) -> None:
        with isolated_environment(prefix="devcontainer shell ") as fixture:
            script, workspace, config, env = self.prepare(fixture)
            log = fixture.root / "devcontainer.jsonl"
            cli = write_append_logger(fixture.root / "tools with spaces/devcontainer", log)

            result = run_launcher(
                script,
                "st",
                env=env | {"DEVCONTAINER_CLI": str(cli)},
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            base = self.base_args(workspace, config, env)
            self.assertEqual(
                read_json_lines(log),
                [["up", *base], ["exec", *base, "--container-id", CONTAINER_ID, "--", "zsh", "-l"]],
            )

    def test_environment_overrides_paths_and_shell_without_word_loss(self) -> None:
        with isolated_environment(prefix="devcontainer overrides ") as fixture:
            script, _, _, env = self.prepare(fixture)
            workspace = fixture.root / "override workspace"
            config = fixture.root / "override config/devcontainer.json"
            workspace.mkdir()
            config.parent.mkdir()
            config.write_text("{}\n", encoding="utf-8")
            log = fixture.root / "devcontainer.jsonl"
            cli = write_append_logger(fixture.root / "devcontainer", log)

            result = run_launcher(
                script,
                "sample",
                "shell",
                env=env
                | {
                    "DEVCONTAINER_CLI": str(cli),
                    "SAMPLE_TOOL_WORKSPACE": str(workspace),
                    "SAMPLE_TOOL_CONFIG": str(config),
                    "SAMPLE_TOOL_SHELL": "bash -lc 'printf literal; value'",
                },
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            base = self.base_args(workspace, config, env)
            self.assertEqual(
                read_json_lines(log),
                [["up", *base], ["exec", *base, "--container-id", CONTAINER_ID, "--", "bash", "-lc", "printf literal; value"]],
            )

    def test_exec_and_rebuild_preserve_arguments_flags_and_exit_status(self) -> None:
        with isolated_environment(prefix="devcontainer actions ") as fixture:
            script, workspace, config, env = self.prepare(fixture)
            log = fixture.root / "devcontainer.jsonl"
            cli = write_append_logger(fixture.root / "devcontainer", log)
            base = self.base_args(workspace, config, env)

            exec_result = run_launcher(
                script,
                "sample-tool",
                "exec",
                "--",
                "python3",
                "argument with spaces",
                "semi;literal",
                env=env | {"DEVCONTAINER_CLI": str(cli)},
            )
            rebuild_result = run_launcher(
                script,
                "sample-tool",
                "rebuild-no-cache",
                env=env | {"DEVCONTAINER_CLI": str(cli), "FAKE_CLI_EXIT": "29"},
            )

            self.assertEqual(exec_result.returncode, 0, exec_result.stderr)
            self.assertEqual(rebuild_result.returncode, 29)
            self.assertEqual(
                read_json_lines(log),
                [
                    ["up", *base],
                    ["exec", *base, "--container-id", CONTAINER_ID, "--", "python3", "argument with spaces", "semi;literal"],
                    ["up", *base, "--remove-existing-container", "--build-no-cache"],
                ],
            )

    def test_stop_and_down_target_only_manifest_matching_containers(self) -> None:
        with isolated_environment(prefix="devcontainer docker ") as fixture:
            script, workspace, config, env = self.prepare(fixture)
            log = fixture.root / "docker.jsonl"
            self.set_containers(env, [container(workspace, config),
                                     container(workspace, "/other/config", identifier=SECOND_ID)])
            stop = run_launcher(script, "sample", "stop", env=env)
            down = run_launcher(script, "sample", "down", env=env)

            self.assertEqual(stop.returncode, 0, stop.stderr)
            self.assertEqual(down.returncode, 0, down.stderr)
            calls = read_json_lines(log)
            self.assertEqual([call for call in calls if call[0] in ("stop", "rm")],
                             [["stop", CONTAINER_ID], ["rm", "-f", CONTAINER_ID]])
            self.assertEqual(calls[0], ["ps", "-aq", "--no-trunc", "--filter",
                                       f"label={FOLDER}={workspace}", "--filter", f"label={CONFIG}={config}"])

    def test_invalid_launcher_and_cli_fail_before_external_execution(self) -> None:
        with isolated_environment(prefix="devcontainer refusal ") as fixture:
            script, _, _, env = self.prepare(fixture)

            unknown = run_launcher(script, "not-a-launcher", "up", env=env)
            missing_cli = run_launcher(
                script,
                "sample",
                "up",
                env=env | {"DEVCONTAINER_CLI": str(fixture.root / "missing cli")},
            )
            harmless_cli = write_executable(fixture.root / "harmless-cli", "#!/bin/sh\nexit 0\n")
            missing_command = run_launcher(
                script,
                "sample",
                "exec",
                env=env | {"DEVCONTAINER_CLI": str(harmless_cli)},
            )

            self.assertEqual(unknown.returncode, 1)
            self.assertIn("failed to resolve launcher", unknown.stderr)
            self.assertEqual(missing_cli.returncode, 1)
            self.assertIn("DEVCONTAINER_CLI is not executable", missing_cli.stderr)
            self.assertEqual(missing_command.returncode, 1)
            self.assertIn("exec action requires a command", missing_command.stderr)

    def test_identity_templates_follow_overrides_and_preserve_literals(self):
        with isolated_environment(prefix="devcontainer identity ") as fixture:
            script, _, _, env = self.prepare(fixture)
            workspace = fixture.root / "space = ' $(touch injected) {config}"
            workspace.mkdir()
            config = workspace / "devcontainer.json"
            config.write_text("{}")
            templates = {FOLDER: "{workspace}", CONFIG: "{config}",
                         "custom.owner": "{home}/value=one; $(false) ' \\ two"}
            self.configure_identity(env, templates)
            labels = {FOLDER: str(workspace), CONFIG: str(config),
                      "custom.owner": str(fixture.home) + "/value=one; $(false) ' \\ two"}
            self.set_containers(env, [container(workspace, config, extra={"custom.owner": labels["custom.owner"]})])
            env |= {"SAMPLE_TOOL_WORKSPACE": str(workspace), "SAMPLE_TOOL_CONFIG": str(config)}
            result = run_launcher(script, "sample", "exec", "--existing", "--", "printf", "--existing", "a=b;literal", env=env)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(read_json_lines(fixture.root / "devcontainer.jsonl"), [
                ["exec", *self.base_args(workspace, config, env, labels), "--container-id", CONTAINER_ID,
                 "--", "printf", "--existing", "a=b;literal"]])
            self.assertFalse((workspace / "injected").exists())

    def test_invalid_identity_fails_before_docker(self):
        invalid = [None, [], {}, {FOLDER: "x"}, {FOLDER: "", CONFIG: "x"},
                   {FOLDER: "x\n", CONFIG: "x"}, {FOLDER: 3, CONFIG: "x"},
                   {FOLDER: "x", CONFIG: "x", "bad=key": "x"},
                   {FOLDER: "x", CONFIG: "x", "devcontainer.metadata": "SECRET"}]
        for labels in invalid:
            with self.subTest(labels=labels), isolated_environment() as fixture:
                script, _, _, env = self.prepare(fixture)
                self.configure_identity(env, labels)
                result = run_launcher(script, "sample", "up", env=env)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(read_json_lines(fixture.root / "docker.jsonl"), [])
                self.assert_no_mutation(fixture)

    def test_status_selection_matrix_is_observational(self):
        for states, outcome in (([], "missing"), (["running"], "unique"),
                                (["exited"], "unique"), (["running", "exited"], "ambiguous")):
            with self.subTest(states=states), isolated_environment() as fixture:
                script, workspace, config, env = self.prepare(fixture)
                self.set_containers(env, [container(workspace, config, state=state, identifier=identifier)
                                         for state, identifier in zip(states, (CONTAINER_ID, SECOND_ID))])
                config.unlink()
                result = run_launcher(script, "sample", "status", "--json",
                                      env=env | {"DEVCONTAINER_CLI": "/not-installed"})
                self.assertEqual(result.returncode, 0, result.stderr)
                report = json.loads(result.stdout)
                self.assertEqual(report["schema_version"], 1)
                self.assertEqual(report["selection"], outcome)
                self.assertEqual(report["ambiguous"], len(states) > 1)
                self.assertEqual(len(report["matches"]), len(states))
                self.assertEqual(report["identity_labels"], {FOLDER: str(workspace), CONFIG: str(config)})
                self.assertEqual(result.stderr, "")
                plain = run_launcher(script, "sample", "status", "--plain", env=env)
                self.assertEqual(plain.returncode, 0, plain.stderr)
                self.assertIn(outcome, plain.stdout)
                self.assertNotIn("\x1b", plain.stdout)
                self.assert_no_mutation(fixture)

    def test_all_lifecycle_actions_refuse_running_plus_stopped_duplicates(self):
        for action in ("up", "shell", "rebuild", "rebuild-no-cache", "stop", "down", "exec"):
            with self.subTest(action=action), isolated_environment() as fixture:
                script, workspace, config, env = self.prepare(fixture)
                self.set_containers(env, [container(workspace, config),
                                         container(workspace, config, identifier=SECOND_ID, state="exited")])
                args = ("--", "true") if action == "exec" else ()
                result = run_launcher(script, "sample", action, *args, env=env)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("ambiguous identity", result.stderr)
                self.assertIn(SECOND_ID, result.stderr)
                self.assert_no_mutation(fixture)

    def test_missing_and_stopped_existing_exec_never_ensures_up(self):
        for state in (None, "exited", "paused", "restarting"):
            with self.subTest(state=state), isolated_environment() as fixture:
                script, workspace, config, env = self.prepare(fixture)
                self.set_containers(env, [] if state is None else [container(workspace, config, state=state)])
                result = run_launcher(script, "sample", "exec", "--existing", "--", "true", env=env)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("run up separately", result.stderr)
                self.assert_no_mutation(fixture)

    def test_existing_exec_preserves_failure_and_no_fallback(self):
        for code in (17, 125):
            with self.subTest(code=code), isolated_environment() as fixture:
                script, workspace, config, env = self.prepare(fixture)
                result = run_launcher(script, "sample", "exec", "--existing", "--", "sh", "-c", "exit 17",
                                      env=env | {"FAKE_EXEC_EXIT": str(code)})
                self.assertEqual(result.returncode, code)
                calls = read_json_lines(fixture.root / "devcontainer.jsonl")
                self.assertEqual(len(calls), 1)
                self.assertEqual(calls[0], ["exec", *self.base_args(workspace, config, env),
                                            "--container-id", CONTAINER_ID, "--", "sh", "-c", "exit 17"])
                self.assertTrue(all(call[0] in ("ps", "inspect") for call in read_json_lines(fixture.root / "docker.jsonl")))

    def test_ensure_up_transitions_and_failure(self):
        for after in ("running", "exited", "missing", "duplicate", "up-failure"):
            with self.subTest(after=after), isolated_environment() as fixture:
                script, workspace, config, env = self.prepare(fixture)
                self.set_containers(env, [])
                data = [] if after == "missing" else [container(workspace, config, state="exited" if after == "exited" else "running")]
                if after == "duplicate":
                    data.append(container(workspace, config, identifier=SECOND_ID))
                env |= {"FAKE_AFTER_UP": json.dumps(data)}
                if after == "up-failure":
                    env["FAKE_UP_EXIT"] = "23"
                result = run_launcher(script, "sample", "exec", "true", env=env)
                calls = read_json_lines(fixture.root / "devcontainer.jsonl")
                self.assertEqual([call[0] for call in calls], ["up", "exec"] if after == "running" else ["up"])
                if after == "running":
                    self.assertEqual(result.returncode, 0, result.stderr)
                else:
                    self.assertNotEqual(result.returncode, 0)
                if after == "up-failure":
                    self.assertEqual(result.returncode, 23)

    def test_up_and_rebuild_share_identity_even_when_missing_or_stopped(self):
        for action, flags in (("up", []), ("rebuild", ["--remove-existing-container"]),
                              ("rebuild-no-cache", ["--remove-existing-container", "--build-no-cache"])):
            for state in (None, "exited"):
                with self.subTest(action=action, state=state), isolated_environment() as fixture:
                    script, workspace, config, env = self.prepare(fixture)
                    self.set_containers(env, [] if state is None else [container(workspace, config, state=state)])
                    result = run_launcher(script, "sample", action, env=env)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(read_json_lines(fixture.root / "devcontainer.jsonl"),
                                     [["up", *self.base_args(workspace, config, env), *flags]])

    def test_stop_and_down_missing_noops_and_stopped_behavior(self):
        for state in (None, "exited", "paused", "restarting"):
            with self.subTest(state=state), isolated_environment() as fixture:
                script, workspace, config, env = self.prepare(fixture)
                self.set_containers(env, [] if state is None else [container(workspace, config, state=state)])
                for action in ("stop", "down"):
                    result = run_launcher(script, "sample", action, env=env)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    if action == "stop":
                        self.assertIn("sample-tool", result.stderr)
                        if state is not None:
                            self.assertIn(f"is {state}; no stop requested", result.stderr)
                calls = read_json_lines(fixture.root / "docker.jsonl")
                self.assertEqual([call for call in calls if call[0] not in ("ps", "inspect")],
                                 [] if state is None else [["rm", "-f", CONTAINER_ID]])

    def test_wsl_unc_identity_and_narrow_conflicts(self):
        for variant in ("exact", "native", "legacy-unc", "unc-config", "other-config", "other-workspace", "extra-label"):
            with self.subTest(variant=variant), isolated_environment() as fixture:
                script, workspace, config, env = self.prepare(fixture)
                script.write_text(script.read_text().replace('platform="$(detect_platform)"', 'platform="wsl2-debian"'))
                unc = "\\\\wsl.localhost\\Debian" + str(workspace).replace("/", "\\")
                self.configure_identity(env, {FOLDER: "\\\\wsl.localhost\\Debian{workspace_backslashes}",
                                              CONFIG: "{config}", "custom.owner": "terminal"}, "wsl2-debian")
                item = container(unc, config, extra={"custom.owner": "terminal"})
                if variant == "native": item["labels"][FOLDER] = str(workspace)
                if variant == "legacy-unc": item["labels"][FOLDER] = unc.replace("wsl.localhost", "wsl$")
                if variant == "unc-config": item["labels"][CONFIG] = "\\\\wsl$\\Debian" + str(config).replace("/", "\\")
                if variant == "other-config": item["labels"][CONFIG] = "/other/config"
                if variant == "other-workspace": item["labels"][FOLDER] = "/another/Workspace with spaces"
                if variant == "extra-label": item["labels"].pop("custom.owner")
                self.set_containers(env, [item])
                result = run_launcher(script, "sample", "status", "--json", env=env)
                self.assertEqual(result.returncode, 0, result.stderr)
                report = json.loads(result.stdout)
                expected = "unique" if variant == "exact" else "missing" if variant.startswith("other-") else "conflict"
                self.assertEqual(report["selection"], expected)
                self.assertEqual(report["identity_labels"][FOLDER], unc)
                if expected == "conflict":
                    for action, args in (("up", ()), ("stop", ()), ("down", ()), ("exec", ("--existing", "--", "true"))):
                        refused = run_launcher(script, "sample", action, *args, env=env)
                        self.assertNotEqual(refused.returncode, 0)
                        self.assertIn("conflict identity", refused.stderr)
                    self.assert_no_mutation(fixture)
                elif expected == "unique":
                    existing = run_launcher(script, "sample", "exec", "--existing", "--", "true", env=env)
                    self.assertEqual(existing.returncode, 0, existing.stderr)
                    calls = read_json_lines(fixture.root / "devcontainer.jsonl")
                    self.assertIn(f"{FOLDER}={unc}", calls[0])
                    self.assertEqual(len(report["matches"]), 1)

    def test_docker_errors_and_disappearance_fail_closed(self):
        for operation in ("ps", "inspect"):
            for action, args in (("status", ("--json",)), ("exec", ("--existing", "--", "true")), ("up", ())):
                with self.subTest(operation=operation, action=action), isolated_environment() as fixture:
                    script, _, _, env = self.prepare(fixture)
                    result = run_launcher(script, "sample", action, *args, env=env | {"FAKE_DOCKER_FAIL": operation})
                    self.assertNotEqual(result.returncode, 0)
                    self.assertEqual(result.stdout, "")
                    self.assertNotIn("SECRET", result.stderr)
                    self.assert_no_mutation(fixture)

    def test_status_only_exposes_identity_and_escapes_terminal_controls(self):
        with isolated_environment() as fixture:
            script, workspace, config, env = self.prepare(fixture)
            self.configure_identity(env, {FOLDER: "{workspace}", CONFIG: "{config}", "custom.owner": "expected"})
            item = container(workspace, config, extra={"custom.owner": "\x1b]2;unsafe\x07",
                                                       "devcontainer.metadata": "SECRET-METADATA", "unrelated": "SECRET-LABEL"})
            item["env"] = ["SECRET=ENV"]
            self.set_containers(env, [item])
            for mode in ("--json", "--plain"):
                result = run_launcher(script, "sample", "status", mode, env=env)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertNotIn("\x1b", result.stdout)
                self.assertNotIn("SECRET", result.stdout + result.stderr)
            inspect = next(call for call in read_json_lines(fixture.root / "docker.jsonl") if call[0] == "inspect")
            self.assertNotIn(".Config.Env", inspect[4])
            self.assertNotIn("devcontainer.metadata", inspect[4])

    def test_status_presentation_matrix(self):
        for mode, tty, term, no_color, gum_state, styled in (
            (None, True, "xterm", "", "ok", True),
            (None, False, "xterm", "", "ok", False),
            ("--json", True, "xterm", "", "ok", False),
            ("--plain", True, "xterm", "", "ok", False),
            (None, True, "xterm", "1", "ok", False),
            (None, True, "dumb", "", "ok", False),
            (None, True, "", "", "ok", False),
            (None, True, None, "", "ok", False),
            (None, True, "xterm", "", "fail", False),
            (None, True, "xterm", "", "missing", False),
        ):
            with self.subTest(mode=mode, tty=tty, term=term, gum=gum_state), isolated_environment() as fixture:
                script, workspace, _, env = self.prepare(fixture)
                gum_log = fixture.root / "gum.jsonl"
                write_executable(fixture.fake_bin / "gum", f"#!{sys.executable}\n"
                                 "import json, pathlib, sys\n"
                                 f"pathlib.Path({str(gum_log)!r}).write_text(json.dumps(sys.argv[1:]) + '\\n')\n"
                                 "assert sys.stdin.read() == ''\n"
                                 "print('STYLED ' + sys.argv[-1])\n"
                                 f"sys.exit({1 if gum_state == 'fail' else 0})\n")
                if gum_state == "missing":
                    script.write_text(script.read_text().replace('resolve_native_path_command "$platform" gum;', 'false;'))
                env |= {"NO_COLOR": no_color}
                if term is None: env.pop("TERM", None)
                else: env["TERM"] = term
                args = ["sample", "status"] + ([mode] if mode else [])
                if tty:
                    result, _ = self.run_in_pty(script, args, env, stdin=subprocess.DEVNULL)
                else:
                    result = run_launcher(script, *args, env=env)
                output = result.stdout
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual("STYLED" in output, styled)
                invoked = styled or gum_state == "fail"
                self.assertEqual(bool(read_json_lines(gum_log)), invoked)
                self.assertIn(str(workspace), output)
                self.assertIn(CONTAINER_ID, output)
                if mode == "--json": self.assertEqual(json.loads(output)["selection"], "unique")
                self.assert_no_mutation(fixture)

    def test_output_flags_are_exclusive(self):
        with isolated_environment() as fixture:
            script, _, _, env = self.prepare(fixture)
            result = run_launcher(script, "sample", "status", "--json", "--plain", env=env)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(read_json_lines(fixture.root / "docker.jsonl"), [])

    def test_native_path_classification_and_resolution(self):
        with isolated_environment() as fixture:
            script, _, _, env = self.prepare(fixture)
            source = script.read_text().rsplit('main "$@"', 1)[0]
            script.write_text(source + '''
is_windows_mounted_path wsl2-debian /mnt/c/tools/docker
! is_windows_mounted_path wsl2-debian /mnt/devdrive/docker
! is_windows_mounted_path darwin /mnt/c/tools/docker
resolve_native_path_command wsl2-debian docker
[[ "$RESOLVED_NATIVE_PATH" == "$DOCKER_CLI" ]]
resolve_docker_command wsl2-debian
[[ "${DOCKER_CMD[0]}" == "$DOCKER_CLI" ]]
resolve_devcontainer_command wsl2-debian
[[ "${DEVCONTAINER_CMD[0]}" == "$DEVCONTAINER_CLI" ]]
''')
            result = run_launcher(script, env=env)
            self.assertEqual(result.returncode, 0, result.stderr)

    def prepare_terminal(self, fixture, **terminal):
        script, workspace, config, env = self.prepare(fixture)
        record = fixture.root / "exec-record.json"
        ready = fixture.root / "exec-ready"
        cli = write_executable(fixture.fake_bin / "devcontainer-tty", f"#!{sys.executable}\n" + r'''
import json, os, pathlib, signal, sys, time
args = sys.argv[1:]
with pathlib.Path(os.environ['FAKE_CLI_LOG']).open('a') as stream:
    stream.write(json.dumps(args) + '\n')
if args[0] != 'exec':
    sys.exit(0)
record = {'stdin_tty': os.isatty(0), 'stdout_tty': os.isatty(1), 'pid': os.getpid(),
          'size': list(os.get_terminal_size(1)) if os.isatty(1) else None}
if os.environ.get('FAKE_WAIT_RESIZE'):
    resized = []
    signal.signal(signal.SIGWINCH, lambda *_: resized.append(1))
    pathlib.Path(os.environ['FAKE_READY']).write_text('ready')
    deadline = time.monotonic() + 10
    while not resized and time.monotonic() < deadline:
        time.sleep(0.01)
    record['resize_signal'] = bool(resized)
    record['resized'] = list(os.get_terminal_size(1))
pathlib.Path(os.environ['FAKE_RECORD']).write_text(json.dumps(record))
sys.exit(int(os.environ.get('FAKE_EXEC_EXIT', '0')))
''')
        write_executable(fixture.fake_bin / "tput",
                         "#!/bin/sh\n[ -n \"$FAKE_TPUT_COLORS\" ] || exit 1\nprintf '%s\\n' \"$FAKE_TPUT_COLORS\"\n")
        env = {key: value for key, value in env.items()
               if key not in ("TERM", "COLORTERM", "DEVCONTAINER_LAUNCH_TERM", "DEVCONTAINER_LAUNCH_COLORTERM")}
        env |= {"DEVCONTAINER_CLI": str(cli), "FAKE_CLI_LOG": str(fixture.root / "devcontainer.jsonl"),
                "FAKE_RECORD": str(record), "FAKE_READY": str(ready)}
        env |= terminal
        return script, workspace, config, env, record, ready

    def run_in_pty(self, script, args, env, *, rows=31, cols=97, resize=None, ready=None, stdin=None, stdout=None):
        master, slave = pty.openpty()

        def controlling_terminal():
            os.setsid()
            fcntl.ioctl(slave, termios.TIOCSCTTY, 0)

        try:
            fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
            process = subprocess.Popen(["/bin/bash", str(script), *args], env=env,
                                       stdin=slave if stdin is None else stdin,
                                       stdout=slave if stdout is None else stdout,
                                       stderr=subprocess.PIPE, preexec_fn=controlling_terminal)
            if resize:
                deadline = time.monotonic() + 10
                while not ready.exists():
                    self.assertIsNone(process.poll(), "launcher exited before the resize check")
                    self.assertLess(time.monotonic(), deadline, "fake devcontainer never became ready")
                    time.sleep(0.02)
                fcntl.ioctl(master, termios.TIOCSWINSZ, struct.pack("HHHH", *resize, 0, 0))
            os.close(slave)
            slave = None
            chunks = []
            deadline = time.monotonic() + 10
            while True:
                if not select.select([master], [], [], max(0, deadline - time.monotonic()))[0]:
                    process.kill()
                    process.wait()
                    self.fail("launcher kept the PTY open past the deadline")
                try: chunk = os.read(master, 65536)
                except OSError: break
                if not chunk: break
                chunks.append(chunk)
            piped, stderr = process.communicate(timeout=10)
            output = (piped if stdout is not None else b"".join(chunks)).decode().replace("\r\n", "\n")
            return subprocess.CompletedProcess(process.args, process.returncode, output, stderr.decode()), process.pid
        finally:
            os.close(master)
            if slave is not None: os.close(slave)

    def exec_calls(self, fixture):
        return [call for call in read_json_lines(fixture.root / "devcontainer.jsonl") if call[0] == "exec"]

    def remote_env(self, call):
        return [call[index + 1] for index, value in enumerate(call) if value == "--remote-env"]

    def test_tty_shell_and_exec_keep_pty_dimensions_resize_and_exit_status(self):
        for args in (["sample"], ["sample", "exec", "--existing", "--", "zsh", "-ic", "true"]):
            with self.subTest(args=args), isolated_environment() as fixture:
                script, workspace, config, env, record, ready = self.prepare_terminal(
                    fixture, TERM="xterm-256color", COLORTERM="truecolor", FAKE_WAIT_RESIZE="1", FAKE_EXEC_EXIT="23")
                result, pid = self.run_in_pty(script, args, env, resize=(40, 132), ready=ready)
                self.assertEqual(result.returncode, 23, result.stderr)
                observed = json.loads(record.read_text())
                self.assertEqual(observed["pid"], pid)
                self.assertTrue(observed["stdin_tty"] and observed["stdout_tty"])
                self.assertEqual(observed["size"], [97, 31])
                self.assertTrue(observed["resize_signal"])
                self.assertEqual(observed["resized"], [132, 40])
                call = self.exec_calls(fixture)[0]
                self.assertEqual(self.remote_env(call), ["TERM=xterm-256color", "COLORTERM=truecolor"])
                tail = call[call.index("--container-id"):]
                self.assertEqual(tail[:3], ["--container-id", CONTAINER_ID, "--"])
                self.assertNotIn("--remote-env", tail)

    def test_tty_terminal_capability_policy(self):
        cases = (
            ({"TERM": "xterm-256color"}, ["TERM=xterm-256color"]),
            ({"TERM": "xterm-256color", "COLORTERM": "24bit"}, ["TERM=xterm-256color", "COLORTERM=24bit"]),
            ({"TERM": "xterm"}, []),
            ({"TERM": "xterm-direct"}, ["TERM=xterm-256color"]),
            ({"TERM": "xterm", "FAKE_TPUT_COLORS": "256"}, ["TERM=xterm-256color"]),
            ({"TERM": "xterm-ghostty", "COLORTERM": "truecolor"}, ["TERM=xterm-256color", "COLORTERM=truecolor"]),
            ({"TERM": "xterm-256color", "COLORTERM": "yes"}, ["TERM=xterm-256color"]),
            ({"COLORTERM": "truecolor"}, []),
            ({"TERM": "dumb", "COLORTERM": "truecolor"}, []),
            ({"TERM": "xterm", "DEVCONTAINER_LAUNCH_TERM": "screen-256color",
              "DEVCONTAINER_LAUNCH_COLORTERM": "truecolor"}, ["TERM=screen-256color", "COLORTERM=truecolor"]),
            ({"TERM": "xterm-256color", "COLORTERM": "truecolor", "DEVCONTAINER_LAUNCH_TERM": "",
              "DEVCONTAINER_LAUNCH_COLORTERM": ""}, []),
        )
        for terminal, expected in cases:
            with self.subTest(terminal=terminal), isolated_environment() as fixture:
                script, _, _, env, _, _ = self.prepare_terminal(fixture, **terminal)
                result, _ = self.run_in_pty(script, ["sample", "exec", "--existing", "--", "true"], env)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(self.remote_env(self.exec_calls(fixture)[0]), expected)

    def test_terminal_override_control_characters_fail_before_docker(self):
        cases = [(name, args) for name in ("DEVCONTAINER_LAUNCH_TERM", "DEVCONTAINER_LAUNCH_COLORTERM")
                 for args in (["sample"], ["sample", "exec", "--existing", "--", "true"])]
        for name, args in cases:
            with self.subTest(name=name, args=args), isolated_environment() as fixture:
                script, _, _, env, _, _ = self.prepare_terminal(fixture, TERM="xterm-256color", **{name: "xterm\x1b[31m"})
                result, _ = self.run_in_pty(script, args, env)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(f"{name} must not contain control characters", result.stderr)
                self.assertNotIn("\x1b", result.stderr)
                self.assertEqual(read_json_lines(fixture.root / "docker.jsonl"), [])
                self.assertEqual(read_json_lines(fixture.root / "devcontainer.jsonl"), [])

    def test_non_tty_exec_adds_no_terminal_environment(self):
        with isolated_environment() as fixture:
            script, workspace, config, env, record, _ = self.prepare_terminal(
                fixture, TERM="xterm-256color", COLORTERM="truecolor", DEVCONTAINER_LAUNCH_TERM="screen-256color",
                FAKE_EXEC_EXIT="17")
            result = run_launcher(script, "sample", "exec", "--existing", "--", "sh", "-c", "exit 17", env=env)
            self.assertEqual(result.returncode, 17, result.stderr)
            self.assertEqual(self.exec_calls(fixture), [["exec", *self.base_args(workspace, config, env),
                                                         "--container-id", CONTAINER_ID, "--", "sh", "-c", "exit 17"]])
            observed = json.loads(record.read_text())
            self.assertFalse(observed["stdin_tty"] or observed["stdout_tty"])

    def test_tty_stdout_without_tty_stdin_adds_no_terminal_environment(self):
        with isolated_environment() as fixture:
            script, _, _, env, record, _ = self.prepare_terminal(fixture, TERM="xterm-256color", COLORTERM="truecolor")
            result, _ = self.run_in_pty(script, ["sample", "exec", "--existing", "--", "true"], env,
                                        stdin=subprocess.DEVNULL)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(self.remote_env(self.exec_calls(fixture)[0]), [])
            observed = json.loads(record.read_text())
            self.assertFalse(observed["stdin_tty"])
            self.assertTrue(observed["stdout_tty"])

    def test_tty_stdin_with_piped_stdout_adds_no_terminal_environment(self):
        with isolated_environment() as fixture:
            script, _, _, env, record, _ = self.prepare_terminal(fixture, TERM="xterm-256color", COLORTERM="truecolor")
            result, _ = self.run_in_pty(script, ["sample", "exec", "--existing", "--", "true"], env,
                                        stdout=subprocess.PIPE)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(self.remote_env(self.exec_calls(fixture)[0]), [])
            observed = json.loads(record.read_text())
            self.assertTrue(observed["stdin_tty"])
            self.assertFalse(observed["stdout_tty"])

    def test_lifecycle_actions_add_no_terminal_environment(self):
        for action in ("up", "rebuild"):
            with self.subTest(action=action), isolated_environment() as fixture:
                script, _, _, env, _, _ = self.prepare_terminal(fixture, TERM="xterm-256color", COLORTERM="truecolor")
                result, _ = self.run_in_pty(script, ["sample", action], env)
                self.assertEqual(result.returncode, 0, result.stderr)
                calls = read_json_lines(fixture.root / "devcontainer.jsonl")
                self.assertEqual([call[0] for call in calls], ["up"])
                self.assertNotIn("--remote-env", calls[0])


if __name__ == "__main__":
    unittest.main()
