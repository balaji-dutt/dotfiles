from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

from tests.support.fixtures import isolated_environment, read_json_lines, write_executable


REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE = REPO_ROOT / "bin/executable_devcontainer-launch.tmpl"


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
        "raise SystemExit(int(os.environ.get('FAKE_CLI_EXIT', '0')))\n",
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
        env = fixture.env | {"DEVCONTAINER_LAUNCH_MANIFEST": str(manifest)}
        return script, workspace, config, env

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
            base = ["--workspace-folder", str(workspace), "--config", str(config)]
            self.assertEqual(
                read_json_lines(log),
                [["up", *base], ["exec", *base, "zsh", "-l"]],
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
            base = ["--workspace-folder", str(workspace), "--config", str(config)]
            self.assertEqual(
                read_json_lines(log),
                [["up", *base], ["exec", *base, "bash", "-lc", "printf literal; value"]],
            )

    def test_exec_and_rebuild_preserve_arguments_flags_and_exit_status(self) -> None:
        with isolated_environment(prefix="devcontainer actions ") as fixture:
            script, workspace, config, env = self.prepare(fixture)
            log = fixture.root / "devcontainer.jsonl"
            cli = write_append_logger(fixture.root / "devcontainer", log)
            base = ["--workspace-folder", str(workspace), "--config", str(config)]

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
                    ["exec", *base, "python3", "argument with spaces", "semi;literal"],
                    ["up", *base, "--remove-existing-container", "--build-no-cache"],
                ],
            )

    def test_stop_and_down_target_only_manifest_matching_containers(self) -> None:
        with isolated_environment(prefix="devcontainer docker ") as fixture:
            script, workspace, config, env = self.prepare(fixture)
            log = fixture.root / "docker.jsonl"
            docker = write_executable(
                fixture.root / "docker tools/docker",
                f"#!{sys.executable}\n"
                "import json, pathlib, sys\n"
                f"log = pathlib.Path({str(log)!r})\n"
                "with log.open('a', encoding='utf-8') as stream:\n"
                "    stream.write(json.dumps(sys.argv[1:]) + '\\n')\n"
                "if sys.argv[1:2] == ['ps']:\n"
                "    print('container-one')\n"
                "    print('container two')\n",
            )
            expected_filters = [
                f"label=devcontainer.local_folder={workspace}",
                f"label=devcontainer.config_file={config}",
            ]

            stop = run_launcher(script, "sample", "stop", env=env | {"DOCKER_CLI": str(docker)})
            down = run_launcher(script, "sample", "down", env=env | {"DOCKER_CLI": str(docker)})

            self.assertEqual(stop.returncode, 0, stop.stderr)
            self.assertEqual(down.returncode, 0, down.stderr)
            calls = read_json_lines(log)
            self.assertEqual(calls[0], ["ps", "-q", "--filter", expected_filters[0], "--filter", expected_filters[1]])
            self.assertEqual(calls[1], ["stop", "container-one", "container two"])
            self.assertEqual(calls[2], ["ps", "-aq", "--filter", expected_filters[0], "--filter", expected_filters[1]])
            self.assertEqual(calls[3], ["rm", "-f", "container-one", "container two"])

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


if __name__ == "__main__":
    unittest.main()
