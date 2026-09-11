from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

from tests.support.fixtures import isolated_environment, write_executable


REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE = (
    REPO_ROOT
    / ".chezmoiscripts/run_once_after_97-retire-codebase-memory-mcp-ubi.sh.tmpl"
)
LEGACY_TOOL = "ubi:DeusData/codebase-memory-mcp"
REPLACEMENT_TOOL = "github:DeusData/codebase-memory-mcp"


def supported_script() -> str:
    source = SOURCE.read_text(encoding="utf-8")
    body = source[source.index("#!/usr/bin/env bash") :]
    return body.rsplit("{{- end -}}", 1)[0]


def read_json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_state(path: Path, state: dict[str, object]) -> None:
    path.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")


def command_log(path: Path) -> list[list[str]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines()]


def render_for_platform(
    os_name: str,
    *,
    is_wsl2: bool = False,
    kernel_release: str = "",
) -> str:
    detected_wsl2 = is_wsl2
    if os_name == "linux":
        release = kernel_release.lower()
        detected_wsl2 = detected_wsl2 or (
            "microsoft" in release and "wsl2" in release
        )
    return supported_script() if os_name == "darwin" or detected_wsl2 else ""


class RetireLegacyCbmMiseTests(unittest.TestCase):
    def prepare(
        self,
        fixture,
        *,
        legacy_versions: list[str] | None = None,
    ) -> tuple[Path, Path, Path, dict[str, str], Path]:
        script = write_executable(fixture.root / "retire-cbm-ubi", supported_script())
        state_path = fixture.root / "mise-state.json"
        log_path = fixture.root / "mise-calls.jsonl"
        replacement_root = fixture.home / ".local/share/mise/installs/github-cbm/0.10.8"
        cbm = write_executable(
            replacement_root / "codebase-memory-mcp",
            "#!/bin/sh\nprintf '%s\\n' 'codebase-memory-mcp 0.10.8'\n",
        )
        versions = ["0.9.0"] if legacy_versions is None else legacy_versions
        legacy_roots: dict[str, str] = {}
        for version in versions:
            root = fixture.home / f".local/share/mise/installs/ubi-cbm/{version}"
            root.mkdir(parents=True)
            legacy_roots[version] = str(root)

        state: dict[str, object] = {
            "legacy_versions": versions,
            "legacy_roots": legacy_roots,
            "legacy_current": False,
            "legacy_listing": None,
            "github_configured": True,
            "github_version": "0.10.8",
            "github_root": str(replacement_root),
            "which_path": str(cbm),
            "which_after_path": None,
            "fail": None,
            "retain_after_uninstall": False,
        }
        write_state(state_path, state)

        fake_mise = write_executable(
            fixture.fake_bin / "mise",
            f"#!{sys.executable}\n"
            "import json, os, pathlib, shutil, sys\n"
            f"legacy_tool = {LEGACY_TOOL!r}\n"
            f"replacement_tool = {REPLACEMENT_TOOL!r}\n"
            "state_path = pathlib.Path(os.environ['FAKE_MISE_STATE'])\n"
            "log_path = pathlib.Path(os.environ['FAKE_MISE_LOG'])\n"
            "state = json.loads(state_path.read_text())\n"
            "args = sys.argv[1:]\n"
            "with log_path.open('a', encoding='utf-8') as handle:\n"
            "    handle.write(json.dumps(args) + '\\n')\n"
            "def save():\n"
            "    state_path.write_text(json.dumps(state, sort_keys=True))\n"
            "def failed(name):\n"
            "    return state.get('fail') == name\n"
            "if args == ['ls', '--installed', '--no-header', legacy_tool]:\n"
            "    if failed('list-installed'):\n"
            "        raise SystemExit(4)\n"
            "    listing = state.get('legacy_listing')\n"
            "    if listing is not None:\n"
            "        print(listing)\n"
            "    else:\n"
            "        for version in state['legacy_versions']:\n"
            "            print(f'{legacy_tool}  {version}')\n"
            "elif args == ['ls', '--current', '--no-header', legacy_tool]:\n"
            "    if failed('list-current-legacy'):\n"
            "        raise SystemExit(5)\n"
            "    if state['legacy_current']:\n"
            "        print(f'{legacy_tool}  {state[\"legacy_versions\"][0]}  ~/.config/mise.toml')\n"
            "elif args == ['ls', '--current', '--no-header', replacement_tool]:\n"
            "    if failed('list-current-github'):\n"
            "        raise SystemExit(6)\n"
            "    if state['github_configured']:\n"
            "        print(f'{replacement_tool}  {state[\"github_version\"]}  ~/.config/mise/conf.d/96-codebase-memory-mcp.toml')\n"
            "elif len(args) == 2 and args[0] == 'where':\n"
            "    spec = args[1]\n"
            "    if spec.startswith(legacy_tool + '@'):\n"
            "        version = spec.split('@', 1)[1]\n"
            "        if version not in state['legacy_versions'] or failed('where-legacy'):\n"
            "            raise SystemExit(1)\n"
            "        print(state['legacy_roots'][version])\n"
            "    elif spec == f'{replacement_tool}@{state[\"github_version\"]}':\n"
            "        if failed('where-github'):\n"
            "            raise SystemExit(1)\n"
            "        print(state['github_root'])\n"
            "    else:\n"
            "        raise SystemExit(1)\n"
            "elif args == ['which', 'codebase-memory-mcp']:\n"
            "    if failed('which'):\n"
            "        raise SystemExit(7)\n"
            "    which_path = state.get('which_after_path') if not state['legacy_versions'] else None\n"
            "    print(which_path or state['which_path'])\n"
            "elif len(args) == 3 and args[:2] == ['uninstall', '--yes']:\n"
            "    if failed('uninstall'):\n"
            "        raise SystemExit(8)\n"
            "    spec = args[2]\n"
            "    if not spec.startswith(legacy_tool + '@'):\n"
            "        raise SystemExit(9)\n"
            "    version = spec.split('@', 1)[1]\n"
            "    if version not in state['legacy_versions']:\n"
            "        raise SystemExit(10)\n"
            "    if not state['retain_after_uninstall']:\n"
            "        state['legacy_versions'].remove(version)\n"
            "        shutil.rmtree(state['legacy_roots'][version])\n"
            "        save()\n"
            "elif args == ['reshim']:\n"
            "    if failed('reshim'):\n"
            "        raise SystemExit(11)\n"
            "else:\n"
            "    raise SystemExit(f'unexpected mise arguments: {args!r}')\n",
        )
        env = fixture.env | {
            "CBM_RETIRE_MISE_BIN": str(fake_mise),
            "FAKE_MISE_STATE": str(state_path),
            "FAKE_MISE_LOG": str(log_path),
        }
        unrelated = fixture.home / ".local/share/mise/installs/ubi-other/7.0.0/marker"
        unrelated.parent.mkdir(parents=True)
        unrelated.write_text("keep", encoding="utf-8")
        return script, state_path, log_path, env, unrelated

    def run_script(
        self, script: Path, env: dict[str, str]
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["/bin/bash", str(script)],
            env=env,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=10,
        )

    def test_removes_all_discovered_versions_and_second_run_is_noop(self) -> None:
        with isolated_environment(prefix="retire cbm success ") as fixture:
            script, state_path, log_path, env, unrelated = self.prepare(
                fixture, legacy_versions=["0.8.1", "0.9.0"]
            )

            first = self.run_script(script, env)
            second = self.run_script(script, env)

            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertEqual(read_json(state_path)["legacy_versions"], [])
            calls = command_log(log_path)
            self.assertEqual(
                [call for call in calls if call[:2] == ["uninstall", "--yes"]],
                [
                    ["uninstall", "--yes", f"{LEGACY_TOOL}@0.8.1"],
                    ["uninstall", "--yes", f"{LEGACY_TOOL}@0.9.0"],
                ],
            )
            self.assertEqual(calls.count(["reshim"]), 1)
            self.assertEqual(unrelated.read_text(), "keep")

    def test_absent_backend_is_noop_without_replacement(self) -> None:
        with isolated_environment(prefix="retire cbm absent ") as fixture:
            script, state_path, log_path, env, unrelated = self.prepare(
                fixture, legacy_versions=[]
            )
            state = read_json(state_path)
            state["github_configured"] = False
            state["which_path"] = str(fixture.root / "missing")
            write_state(state_path, state)

            result = self.run_script(script, env)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                command_log(log_path),
                [["ls", "--installed", "--no-header", LEGACY_TOOL]],
            )
            self.assertEqual(unrelated.read_text(), "keep")

    def test_unavailable_mise_preserves_legacy_state(self) -> None:
        with isolated_environment(prefix="retire cbm no mise ") as fixture:
            script, state_path, log_path, env, unrelated = self.prepare(fixture)
            env["CBM_RETIRE_MISE_BIN"] = str(fixture.root / "missing-mise")

            result = self.run_script(script, env)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("INFO: mise is unavailable", result.stdout)
            self.assertEqual(read_json(state_path)["legacy_versions"], ["0.9.0"])
            self.assertEqual(command_log(log_path), [])
            self.assertEqual(unrelated.read_text(), "keep")

    def test_active_or_untrusted_listing_fails_before_uninstall(self) -> None:
        cases: list[tuple[str, object]] = [
            ("active", True),
            ("list failure", "list-installed"),
            ("current failure", "list-current-legacy"),
            ("extra column", f"{LEGACY_TOOL}  0.9.0  unexpected"),
            ("wrong backend", "ubi:Other/tool  0.9.0"),
            ("unsafe version", f"{LEGACY_TOOL}  ../../escape"),
        ]
        for label, condition in cases:
            with self.subTest(label=label), isolated_environment(
                prefix="retire cbm reject listing "
            ) as fixture:
                script, state_path, log_path, env, unrelated = self.prepare(fixture)
                state = read_json(state_path)
                if condition is True:
                    state["legacy_current"] = True
                elif condition in {"list-installed", "list-current-legacy"}:
                    state["fail"] = condition
                else:
                    state["legacy_listing"] = condition
                write_state(state_path, state)

                result = self.run_script(script, env)

                self.assertNotEqual(result.returncode, 0)
                self.assertIn("ERROR:", result.stderr)
                self.assertFalse(
                    any(call[:2] == ["uninstall", "--yes"] for call in command_log(log_path))
                )
                self.assertEqual(unrelated.read_text(), "keep")

    def test_unhealthy_replacement_fails_before_uninstall(self) -> None:
        cases = (
            "unconfigured",
            "missing-root",
            "wrong-resolution",
            "non-executable",
            "version-failure",
            "list-current-github",
            "where-github",
            "which",
        )
        for condition in cases:
            with self.subTest(condition=condition), isolated_environment(
                prefix="retire cbm reject replacement "
            ) as fixture:
                script, state_path, log_path, env, _unrelated = self.prepare(fixture)
                state = read_json(state_path)
                cbm = Path(str(state["which_path"]))
                if condition == "unconfigured":
                    state["github_configured"] = False
                elif condition == "missing-root":
                    Path(str(state["github_root"])).rename(fixture.root / "moved-root")
                elif condition == "wrong-resolution":
                    wrong = write_executable(
                        fixture.root / "wrong/codebase-memory-mcp",
                        "#!/bin/sh\nexit 0\n",
                    )
                    state["which_path"] = str(wrong)
                elif condition == "non-executable":
                    cbm.chmod(0o644)
                elif condition == "version-failure":
                    cbm.write_text("#!/bin/sh\nexit 12\n", encoding="utf-8")
                else:
                    state["fail"] = condition
                write_state(state_path, state)

                result = self.run_script(script, env)

                self.assertNotEqual(result.returncode, 0)
                self.assertIn("ERROR:", result.stderr)
                self.assertFalse(
                    any(call[:2] == ["uninstall", "--yes"] for call in command_log(log_path))
                )

    def test_mutation_and_postcondition_failures_propagate(self) -> None:
        for condition in (
            "where-legacy",
            "uninstall",
            "reshim",
            "postcondition",
            "replacement-changed",
        ):
            with self.subTest(condition=condition), isolated_environment(
                prefix="retire cbm mutation failure "
            ) as fixture:
                script, state_path, _log_path, env, _unrelated = self.prepare(fixture)
                state = read_json(state_path)
                if condition == "postcondition":
                    state["retain_after_uninstall"] = True
                elif condition == "replacement-changed":
                    state["which_after_path"] = str(
                        write_executable(
                            fixture.root / "changed/codebase-memory-mcp",
                            "#!/bin/sh\nexit 0\n",
                        )
                    )
                else:
                    state["fail"] = condition
                write_state(state_path, state)

                result = self.run_script(script, env)

                self.assertNotEqual(result.returncode, 0)
                self.assertIn("ERROR:", result.stderr)

    def test_template_limits_execution_to_darwin_and_wsl2(self) -> None:
        source = SOURCE.read_text(encoding="utf-8")

        self.assertIn('$isWSL2 := get . "isWSL2" | default false', source)
        self.assertIn(
            'if or (eq .chezmoi.os "darwin") $isWSL2',
            source,
        )
        self.assertNotIn("0.9.0", supported_script())
        self.assertTrue(render_for_platform("darwin").startswith("#!/usr/bin/env bash"))
        self.assertTrue(
            render_for_platform("linux", is_wsl2=True).startswith("#!/usr/bin/env bash")
        )
        self.assertTrue(
            render_for_platform(
                "linux", kernel_release="5.15.0-microsoft-standard-WSL2"
            ).startswith("#!/usr/bin/env bash")
        )
        self.assertEqual(render_for_platform("linux"), "")
        self.assertEqual(render_for_platform("linux", kernel_release="devcontainer"), "")
        self.assertEqual(render_for_platform("windows"), "")


if __name__ == "__main__":
    unittest.main()
