from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

from tests.support.fixtures import init_git_repository, isolated_environment, write_executable


REPO_ROOT = Path(__file__).resolve().parents[1]
POWERSHELL_IMAGE = "local/powershell-audit:lts"


def _read_calls(path: Path) -> list[list[str]]:
    if not path.exists():
        return []
    calls: list[list[str]] = []
    current: list[str] | None = None
    for line in path.read_text(encoding="utf-8").splitlines():
        if line == "CALL":
            current = []
        elif line == "END" and current is not None:
            calls.append(current)
            current = None
        elif current is not None and line.startswith("ARG="):
            current.append(line[4:])
    return calls


def _write_fake_chezmoi(fake_bin: Path) -> None:
    write_executable(
        fake_bin / "chezmoi",
        r"""#!/bin/sh
{
  printf '%s\n' CALL
  for argument do printf 'ARG=%s\n' "$argument"; done
  printf '%s\n' END
} >>"$AUDIT_CALL_LOG"

command_name=
target_argument=
take_target=0
for argument do
  if [ "$take_target" = 1 ]; then target_argument=$argument; take_target=0; fi
  case "$argument" in
    source-path|managed|execute-template|doctor|diff|apply) command_name=$argument ;;
    target-path) command_name=target-path; take_target=1 ;;
  esac
done

case "$command_name" in
  source-path) printf '%s\n' "$AUDIT_SOURCE_DIR" ;;
  target-path)
    if [ -z "$target_argument" ]; then
      printf '%s\n' "$AUDIT_TARGET_DIR"
    elif [ "${target_argument##*/}" = dot_managed ]; then
      printf '%s\n' "$AUDIT_TARGET_DIR/managed-target"
    else
      printf '%s/%s\n' "$AUDIT_TARGET_DIR" "${target_argument##*/}"
    fi
    ;;
  managed) printf '%s\n' "${AUDIT_MANAGED:-}" ;;
  execute-template) printf '%s\n' "${AUDIT_TEMPLATE_OUTPUT:-# rendered}" ;;
  doctor)
    [ -z "${AUDIT_DOCTOR_OUTPUT:-}" ] || printf '%s\n' "$AUDIT_DOCTOR_OUTPUT" >&2
    exit "${AUDIT_DOCTOR_RC:-0}"
    ;;
esac
""",
    )
    (fake_bin / "chezmoi.cmd").write_text(
        r"""@echo off
setlocal EnableExtensions EnableDelayedExpansion
>>"%AUDIT_CALL_LOG%" echo CALL
set "command_name="
set "target_argument="
set "take_target=0"
:arguments
if "%~1"=="" goto dispatch
>>"%AUDIT_CALL_LOG%" echo ARG=%~1
if "!take_target!"=="1" (
  set "target_argument=%~1"
  set "take_target=0"
)
if "%~1"=="source-path" set "command_name=source-path"
if "%~1"=="target-path" (
  set "command_name=target-path"
  set "take_target=1"
)
if "%~1"=="managed" set "command_name=managed"
if "%~1"=="execute-template" set "command_name=execute-template"
if "%~1"=="doctor" set "command_name=doctor"
if "%~1"=="diff" set "command_name=diff"
if "%~1"=="apply" set "command_name=apply"
shift
goto arguments
:dispatch
>>"%AUDIT_CALL_LOG%" echo END
if "!command_name!"=="source-path" echo %AUDIT_SOURCE_DIR%
if "!command_name!"=="target-path" (
  if "!target_argument!"=="" (
    echo %AUDIT_TARGET_DIR%
  ) else if "!target_argument:~-11!"=="dot_managed" (
    echo %AUDIT_TARGET_DIR%\managed-target
  ) else (
    for %%F in ("!target_argument!") do echo %AUDIT_TARGET_DIR%\%%~nxF
  )
)
if "!command_name!"=="managed" echo %AUDIT_MANAGED%
if "!command_name!"=="execute-template" echo # rendered
if "!command_name!"=="doctor" (
  if not "%AUDIT_DOCTOR_OUTPUT%"=="" 1>&2 echo %AUDIT_DOCTOR_OUTPUT%
  if not "%AUDIT_DOCTOR_RC%"=="" exit /b %AUDIT_DOCTOR_RC%
)
exit /b 0
""",
        encoding="utf-8",
    )


class CzAuditFixture:
    def setUp(self) -> None:
        super().setUp()
        self._fixture_context = isolated_environment(prefix="cz-audit-test-")
        self.fixture = self._fixture_context.__enter__()
        self.addCleanup(self._fixture_context.__exit__, None, None, None)
        self.repo = self.fixture.root / "repo with spaces"
        init_git_repository(self.repo, env=self.fixture.env)
        assets = self.repo / "assets"
        assets.mkdir(parents=True)
        for name in ("cz-audit.sh", "cz-audit.ps1", "cz-audit.env"):
            shutil.copy2(REPO_ROOT / "assets" / name, assets / name)
        self.source = self.repo / "source with spaces"
        self.target = self.fixture.root / "target with spaces"
        self.source.mkdir()
        self.target.mkdir()
        self.call_log = self.fixture.root / "chezmoi-calls.log"
        _write_fake_chezmoi(self.fixture.fake_bin)
        self.env = dict(self.fixture.env)
        if os.name == "nt":
            audit_path = self.fixture.env.get("PATH", os.defpath)
        else:
            audit_path = f"{self.fixture.fake_bin}{os.pathsep}/usr/bin{os.pathsep}/bin"
        self.env.update(
            {
                "AUDIT_CALL_LOG": str(self.call_log),
                "AUDIT_MANAGED": "managed-target",
                "AUDIT_SOURCE_DIR": str(self.source),
                "AUDIT_TARGET_DIR": str(self.target),
                "CHEZMOI_SOURCE_DIR": str(self.source),
                "CZ_AUDIT_LOGDIR": str(self.fixture.root / "audit logs"),
                "PATH": audit_path,
            }
        )

    def write_ansible_fake(self, name: str) -> None:
        script = self.fixture.fake_bin / name
        write_executable(
            script,
            r"""#!/bin/sh
{
  printf '%s\n' CALL
  for argument do printf 'ARG=%s\n' "$argument"; done
  printf '%s\n' END
} >>"$AUDIT_ANSIBLE_LOG"
[ "${1:-}" != image ] || exit 0
syntax=0
for argument do
  [ "$argument" != --syntax-check ] || syntax=1
  last=$argument
done
if [ "$syntax" = 1 ]; then
  cp -- "$last" "$AUDIT_WRAPPER_LOG" || exit 98
  printf '%s\n' 'fixture syntax diagnostic'
  exit "${AUDIT_SYNTAX_RC:-0}"
fi
printf '%s\n' 'fixture lint diagnostic'
exit "${AUDIT_LINT_RC:-0}"
""",
        )
        if os.name == "nt":
            python_script = script.with_suffix(".py")
            python_script.write_text(
                "import os, pathlib, shutil, sys\n"
                "args = sys.argv[1:]\n"
                "with open(os.environ['AUDIT_ANSIBLE_LOG'], 'a', encoding='utf-8') as log:\n"
                "    log.write('CALL\\n' + ''.join('ARG=' + a + '\\n' for a in args) + 'END\\n')\n"
                "if args[0] == 'image': sys.exit(0)\n"
                "syntax = '--syntax-check' in args\n"
                "if syntax: shutil.copyfile(args[-1], os.environ['AUDIT_WRAPPER_LOG'])\n"
                "print('fixture syntax diagnostic' if syntax else 'fixture lint diagnostic')\n"
                "sys.exit(int(os.environ.get('AUDIT_SYNTAX_RC' if syntax else 'AUDIT_LINT_RC', '0')))\n",
                encoding="utf-8",
            )
            script.with_suffix(".cmd").write_text(
                f'@"{sys.executable}" "{python_script}" %*\r\n@exit /b %ERRORLEVEL%\r\n',
                encoding="utf-8",
            )

    def prepare_ansible(self, *, runtime: str | None = None) -> None:
        self.ansible_log = self.fixture.root / "ansible-calls.log"
        self.wrapper_log = self.fixture.root / "wrapper.yml"
        self.env.update({
            "AUDIT_ANSIBLE_LOG": str(self.ansible_log),
            "AUDIT_WRAPPER_LOG": str(self.wrapper_log),
        })
        self.write_ansible_fake(runtime or "ansible-playbook")
        self.write_ansible_fake("ansible-lint")

    def assert_ansible_cleanup(self) -> None:
        scratch = self.repo / ".cz-audit"
        self.assertEqual(list(scratch.iterdir()) if scratch.exists() else [], [])
        self.assertFalse(any(
            "apply" in call or "diff" in call for call in _read_calls(self.call_log)
        ))

    def test_ansible_task_dispatch_and_lint_subject(self) -> None:
        self.prepare_ansible()
        for relsrc in ("ansible/tasks/example.yml", "ansible/tasks/sub dir/it's a task.yaml"):
            with self.subTest(relsrc=relsrc):
                task = self.repo / relsrc
                task.parent.mkdir(parents=True, exist_ok=True)
                content = "- name: Example\n  ansible.builtin.debug:\n    msg: example\n"
                task.write_text(content, encoding="utf-8")
                result = self.run_audit("check", ".\\" + relsrc.replace("/", "\\"))
                self.assertEqual(result.returncode, 0, result.stderr)
                calls = _read_calls(self.ansible_log)
                syntax_call, lint_call = calls[-2:]
                wrapper = syntax_call[-1]
                self.assertNotEqual(wrapper, relsrc)
                self.assertFalse(Path(wrapper).is_absolute())
                self.assertIn("--syntax-check", syntax_call)
                text = self.wrapper_log.read_text(encoding="utf-8-sig")
                self.assertIn("hosts: localhost", text)
                self.assertIn("gather_facts: false", text)
                self.assertIn("ansible.builtin.import_tasks:", text)
                quoted = text.split("file:", 1)[1].strip()
                self.assertTrue(quoted.startswith("'") and quoted.endswith("'"), quoted)
                imported = quoted[1:-1].replace("''", "'")
                self.assertEqual((self.repo / wrapper).parent.joinpath(imported).resolve(), task.resolve())
                self.assertEqual(lint_call[-1], relsrc)
                self.assertEqual(task.read_text(encoding="utf-8"), content)
                self.assert_ansible_cleanup()

    def test_ansible_playbooks_stay_direct(self) -> None:
        self.prepare_ansible()
        for relsrc in (
            "ansible/wsl-playbook.yml", "ansible/tasks-example.yml",
            "ansible/Tasks/example.yml", "ansible/tasks/example.YML",
        ):
            with self.subTest(relsrc=relsrc):
                playbook = self.repo / relsrc
                playbook.parent.mkdir(parents=True, exist_ok=True)
                playbook.write_text("- hosts: localhost\n  tasks: []\n", encoding="utf-8")
                result = self.run_audit("check", relsrc)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(_read_calls(self.ansible_log)[-2], ["-i", "localhost,", "--syntax-check", relsrc])
                self.assertFalse((self.repo / ".cz-audit").exists())
                self.assert_ansible_cleanup()

    def test_ansible_container_task_dispatch(self) -> None:
        for runtime in ("docker", "podman"):
            with self.subTest(runtime=runtime):
                self.prepare_ansible(runtime=runtime)
                task = self.repo / "ansible/tasks/example.yml"
                task.parent.mkdir(parents=True, exist_ok=True)
                task.write_text("- ansible.builtin.debug: {}\n", encoding="utf-8")
                result = self.run_audit("check", "ansible/tasks/example.yml")
                self.assertEqual(result.returncode, 0, result.stderr)
                calls = _read_calls(self.ansible_log)
                syntax = [call for call in calls if "--syntax-check" in call][-1]
                self.assertIn("local/ansible-syntax:repo", syntax)
                self.assertIn("/work", syntax)
                self.assertTrue(any(arg.endswith(":/work") for arg in syntax))
                self.assertNotIn("-t", syntax)
                self.assertNotEqual(syntax[-1], "ansible/tasks/example.yml")
                self.assertIn("ansible.builtin.import_tasks:", self.wrapper_log.read_text(encoding="utf-8-sig"))
                self.assert_ansible_cleanup()
                for suffix in ("", ".cmd", ".py"):
                    (self.fixture.fake_bin / (runtime + suffix)).unlink(missing_ok=True)

    def test_ansible_syntax_failures_and_cleanup(self) -> None:
        self.prepare_ansible()
        task = self.repo / "ansible/tasks/example.yml"
        task.parent.mkdir(parents=True)
        task.write_text("- ansible.builtin.debug: {}\n", encoding="utf-8")
        for rc in (2, 127):
            for strict in ("0", "1"):
                with self.subTest(rc=rc, strict=strict):
                    self.env.update({"AUDIT_SYNTAX_RC": str(rc), "CZ_AUDIT_STRICT": strict})
                    result = self.run_audit("check", "ansible/tasks/example.yml")
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("fixture syntax diagnostic", result.stderr)
                    self.assertTrue(all("--syntax-check" in call for call in _read_calls(self.ansible_log)))
                    self.assert_ansible_cleanup()

    def test_ansible_task_lint_advisory_and_strict(self) -> None:
        self.prepare_ansible()
        task = self.repo / "ansible/tasks/example.yml"
        task.parent.mkdir(parents=True)
        task.write_text("- ansible.builtin.debug: {}\n", encoding="utf-8")
        self.env["AUDIT_LINT_RC"] = "3"
        advisory = self.run_audit("check", "ansible/tasks/example.yml")
        self.assertEqual(advisory.returncode, 0, advisory.stderr)
        self.assertIn("ANSIBLE_LINT found issues (advisory)", advisory.stderr)
        self.env["CZ_AUDIT_STRICT_ANSIBLE_LINT"] = "1"
        strict = self.run_audit("check", "ansible/tasks/example.yml")
        self.assertNotEqual(strict.returncode, 0)
        self.assertIn("fixture lint diagnostic", strict.stderr)
        self.assert_ansible_cleanup()

    def test_ansible_wrapper_creation_failure_blocks_validation(self) -> None:
        self.prepare_ansible()
        task = self.repo / "ansible/tasks/example.yml"
        task.parent.mkdir(parents=True)
        task.write_text("- ansible.builtin.debug: {}\n", encoding="utf-8")
        scratch = self.repo / ".cz-audit"
        scratch.write_text("not a directory\n", encoding="utf-8")
        result = self.run_audit("check", "ansible/tasks/example.yml")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(_read_calls(self.ansible_log), [])
        self.assertEqual(scratch.read_text(encoding="utf-8"), "not a directory\n")

    def test_ansible_cleanup_preserves_other_invocations(self) -> None:
        self.prepare_ansible()
        task = self.repo / "ansible/tasks/example.yml"
        task.parent.mkdir(parents=True)
        task.write_text("- ansible.builtin.debug: {}\n", encoding="utf-8")
        other = self.repo / ".cz-audit/ansible.other/playbook.yml"
        other.parent.mkdir(parents=True)
        other.write_text("other invocation\n", encoding="utf-8")
        for rc in (0, 2):
            with self.subTest(rc=rc):
                self.env["AUDIT_SYNTAX_RC"] = str(rc)
                result = self.run_audit("check", "ansible/tasks/example.yml")
                self.assertEqual(result.returncode == 0, rc == 0, result.stderr)
                self.assertEqual(list(other.parent.parent.iterdir()), [other.parent])
                self.assertEqual(other.read_text(encoding="utf-8"), "other invocation\n")


class CzAuditPosixTests(CzAuditFixture, unittest.TestCase):
    def test_real_ansible_task_syntax_without_execution(self) -> None:
        validator = shutil.which("ansible-playbook")
        if validator:
            command = [validator]
        else:
            runtime = shutil.which("docker") or shutil.which("podman")
            image = "local/ansible-syntax:repo"
            if not runtime or subprocess.run(
                [runtime, "image", "inspect", image],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            ).returncode:
                self.skipTest("local Ansible and retained syntax image are unavailable")
            command = [runtime, "run", "--rm", "--network", "none", "-v", f"{self.repo}:/work", "-w", "/work", image, "ansible-playbook"]

        self.prepare_ansible()
        write_executable(
            self.fixture.fake_bin / "ansible-playbook",
            "#!/bin/sh\nexec " + shlex.join(command) + ' "$@"\n',
        )
        task = self.repo / "ansible/tasks/sub dir/it's a task.yaml"
        task.parent.mkdir(parents=True)
        sentinel = self.repo / "must-not-exist"
        sentinel_path = str(sentinel) if validator else "/work/must-not-exist"
        child = task.parent / "child.yml"
        child.write_text(
            "- name: Do not execute\n"
            "  ansible.builtin.copy:\n"
            "    content: unexpected execution\n"
            f"    dest: '{sentinel_path.replace(chr(39), chr(39) * 2)}'\n",
            encoding="utf-8",
        )
        cases = (
            ("- ansible.builtin.debug:\n    msg: '{{ runtime_only_variable }}'\n", True),
            ("- ansible.builtin.import_tasks: child.yml\n", True),
            ("- name: [broken YAML\n", False),
            ("- name: Unknown action\n  nonexistent_audit_module: {}\n", False),
        )
        for content, valid in cases:
            with self.subTest(content=content):
                task.write_text(content, encoding="utf-8")
                result = self.run_audit("check", task.relative_to(self.repo).as_posix())
                self.assertEqual(result.returncode == 0, valid, result.stderr)
                if not valid:
                    self.assertIn(task.name, result.stderr)
                self.assertEqual(task.read_text(encoding="utf-8"), content)
                self.assertFalse(sentinel.exists())
                self.assert_ansible_cleanup()

        child.write_text("- nonexistent_audit_module: {}\n", encoding="utf-8")
        task.write_text("- ansible.builtin.import_tasks: child.yml\n", encoding="utf-8")
        result = self.run_audit("check", task.relative_to(self.repo).as_posix())
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(child.name, result.stderr)
        self.assertFalse(sentinel.exists())
        self.assert_ansible_cleanup()

    def run_audit(
        self, command: str, relsrc: str, *, env: dict[str, str] | None = None
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["/bin/bash", "assets/cz-audit.sh", command, relsrc],
            cwd=self.repo,
            env=env or self.env,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    def test_classification_normalization_and_precedence(self) -> None:
        (self.source / "dot_managed").write_text("managed\n", encoding="utf-8")
        (self.source / ".chezmoiscripts").mkdir()
        (self.source / ".chezmoiscripts" / "hook.sh.tmpl").write_text(
            "#!/bin/bash\n", encoding="utf-8"
        )
        cases = {
            r".\docs\guide.md": "docs:docs/guide.md",
            "/.chezmoiignore.tmpl": "chezmoi-config:.chezmoiignore.tmpl",
            ".chezmoiscripts/hook.sh.tmpl": "chezmoiscript:.chezmoiscripts/hook.sh.tmpl",
            "dot_managed": f"managed:{self.target}/managed-target",
            "ansible/site.yml": "ansible:ansible/site.yml",
            "assets/tool.py": "assets:assets/tool.py",
            "configs/tool.toml": "configs:configs/tool.toml",
            "README.md": "docs:README.md",
            "other.file": "repo:other.file",
        }
        for relsrc, expected in cases.items():
            with self.subTest(relsrc=relsrc):
                result = self.run_audit("classify", relsrc)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.strip(), expected)

    def test_managed_check_uses_only_dry_run_apply(self) -> None:
        (self.source / "dot_managed").write_text("managed\n", encoding="utf-8")
        result = self.run_audit("check", "dot_managed")
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = _read_calls(self.call_log)
        apply_calls = [call for call in calls if "apply" in call]
        self.assertEqual(len(apply_calls), 1)
        self.assertIn("--dry-run", apply_calls[0])
        self.assertEqual(len([call for call in calls if "diff" in call]), 1)
        self.assertIn("INFO: Classification: managed:", result.stderr)

    def test_template_config_renders_and_never_applies(self) -> None:
        (self.source / ".chezmoiignore.tmpl").write_text("{{ true }}\n", encoding="utf-8")
        result = self.run_audit("check", ".chezmoiignore.tmpl")
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = _read_calls(self.call_log)
        self.assertTrue(any("execute-template" in call for call in calls))
        self.assertTrue(any("doctor" in call for call in calls))
        self.assertFalse(any("apply" in call for call in calls))
        self.assertIn("INFO: Template renders OK", result.stderr)

    def test_advisory_output_is_logged_and_per_check_strict_wins(self) -> None:
        script = self.repo / "assets" / "fixture.sh"
        script.write_text("#!/bin/bash\nexit 0\n", encoding="utf-8")
        write_executable(
            self.fixture.fake_bin / "shellcheck",
            "#!/bin/sh\nprintf '%s\\n' 'fixture shellcheck finding' >&2\nexit 3\n",
        )
        result = self.run_audit("check", "assets/fixture.sh")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("INFO: SHELLCHECK found issues (advisory)", result.stderr)
        logs = list((self.fixture.root / "audit logs").glob("SHELLCHECK.*.log"))
        self.assertEqual(len(logs), 1)
        self.assertIn("fixture shellcheck finding", logs[0].read_text(encoding="utf-8"))

        strict_env = dict(self.env)
        strict_env["CZ_AUDIT_STRICT_SHELLCHECK"] = "1"
        strict = self.run_audit("check", "assets/fixture.sh", env=strict_env)
        self.assertEqual(strict.returncode, 3)
        self.assertIn("fixture shellcheck finding", strict.stderr)

        override_env = dict(strict_env)
        override_env.update({"CZ_AUDIT_STRICT": "1", "CZ_AUDIT_STRICT_SHELLCHECK": "0"})
        overridden = self.run_audit("check", "assets/fixture.sh", env=override_env)
        self.assertEqual(overridden.returncode, 0, overridden.stderr)

    def test_required_powershell_parser_fails_when_unavailable(self) -> None:
        hook = self.repo / ".chezmoiscripts" / "hook.ps1"
        hook.parent.mkdir()
        hook.write_text("Write-Output 'ok'\n", encoding="utf-8")
        tools = self.fixture.root / "required-tools"
        tools.mkdir()
        for command in ("cp", "dirname", "grep", "mkdir", "mktemp", "rm", "tr"):
            resolved = shutil.which(command)
            if resolved is None:
                self.fail(f"required fixture command is unavailable: {command}")
            (tools / command).symlink_to(resolved)
        unavailable_env = dict(self.env)
        write_executable(self.fixture.fake_bin / "grep", "#!/bin/sh\nexit 1\n")
        unavailable_env["PATH"] = f"{self.fixture.fake_bin}{os.pathsep}{tools}"
        result = self.run_audit("check", ".chezmoiscripts/hook.ps1", env=unavailable_env)
        self.assertEqual(result.returncode, 127)
        self.assertIn("pwsh and docker/podman are unavailable", result.stderr)

    def test_explicit_and_auto_detected_source_directories_prefix_calls(self) -> None:
        explicit = self.run_audit("classify", "docs/guide.md")
        self.assertEqual(explicit.returncode, 0, explicit.stderr)
        self.assertIn("INFO: CHEZMOI_SOURCE_DIR override:", explicit.stderr)
        self.assertTrue(all(call[:2] == ["--source", str(self.source)] for call in _read_calls(self.call_log)))

        self.call_log.unlink()
        configured = self.fixture.root / "configured source"
        configured.mkdir()
        auto_env = dict(self.env)
        auto_env.pop("CHEZMOI_SOURCE_DIR")
        auto_env["AUDIT_SOURCE_DIR"] = str(configured)
        auto = self.run_audit("classify", "docs/guide.md", env=auto_env)
        self.assertEqual(auto.returncode, 0, auto.stderr)
        resolved_repo = str(self.repo.resolve())
        self.assertIn(f"INFO: CHEZMOI_SOURCE_DIR auto-detected: {resolved_repo}", auto.stderr)
        calls = _read_calls(self.call_log)
        self.assertNotIn("--source", calls[0])
        self.assertTrue(all(call[:2] == ["--source", resolved_repo] for call in calls[1:]))


class CzAuditPowerShellTests(CzAuditFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.local_pwsh = shutil.which("pwsh")
        cls.docker = shutil.which("docker")
        if cls.local_pwsh:
            return
        if not cls.docker:
            raise unittest.SkipTest("pwsh and Docker are unavailable")
        inspected = subprocess.run(
            [cls.docker, "image", "inspect", POWERSHELL_IMAGE],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if inspected.returncode != 0:
            raise unittest.SkipTest(f"local image {POWERSHELL_IMAGE} is unavailable")

    def run_audit(
        self, command: str, relsrc: str, *, env: dict[str, str] | None = None
    ) -> subprocess.CompletedProcess[str]:
        child_env = dict(env or self.env)
        if self.local_pwsh:
            argv = [
                self.local_pwsh,
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-File",
                str(self.repo / "assets" / "cz-audit.ps1"),
                command,
                relsrc,
            ]
            return subprocess.run(
                argv,
                cwd=self.repo,
                env=child_env,
                check=False,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

        fixture_root = self.fixture.root.resolve()

        def container_path(value: str) -> str:
            try:
                relative = Path(value).resolve().relative_to(fixture_root)
            except (OSError, ValueError):
                return value
            return "/fixture/" + relative.as_posix()

        container_env = {
            key: container_path(value)
            for key, value in child_env.items()
            if key.startswith(("AUDIT_", "CHEZMOI_", "CZ_AUDIT_")) or key in {"HOME", "TMPDIR"}
        }
        container_env["PATH"] = "/fixture/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
        argv = [
            self.docker,
            "run",
            "--rm",
            "--network",
            "none",
            "--mount",
            f"type=bind,source={fixture_root},target=/fixture",
            "--workdir",
            f"/fixture/{self.repo.name}",
        ]
        if hasattr(os, "getuid"):
            argv.extend(["--user", f"{os.getuid()}:{os.getgid()}"])
        for key, value in sorted(container_env.items()):
            argv.extend(["--env", f"{key}={value}"])
        argv.extend(
            [
                "--entrypoint",
                "pwsh",
                POWERSHELL_IMAGE,
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-File",
                container_path(str(self.repo / "assets" / "cz-audit.ps1")),
                command,
                relsrc,
            ]
        )
        return subprocess.run(
            argv,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    def test_classification_normalization_matches_posix_contract(self) -> None:
        cases = {
            r".\docs\guide.md": "docs:docs/guide.md",
            r"\.chezmoi.toml.tmpl": "chezmoi-config:.chezmoi.toml.tmpl",
            r".chezmoiscripts\hook.ps1": "chezmoiscript:.chezmoiscripts/hook.ps1",
            "assets/tool.py": "assets:assets/tool.py",
            "README.md": "docs:README.md",
        }
        for relsrc, expected in cases.items():
            with self.subTest(relsrc=relsrc):
                result = self.run_audit("classify", relsrc)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.strip(), expected)

    def test_managed_check_uses_only_dry_run_apply(self) -> None:
        (self.source / "dot_managed").write_text("managed\n", encoding="utf-8")
        result = self.run_audit("check", "dot_managed")
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = _read_calls(self.call_log)
        apply_calls = [call for call in calls if "apply" in call]
        self.assertEqual(len(apply_calls), 1)
        self.assertIn("--dry-run", apply_calls[0])
        self.assertEqual(len([call for call in calls if "diff" in call]), 1)

    def test_in_process_parser_reports_invalid_powershell(self) -> None:
        hook = self.repo / ".chezmoiscripts" / "hook.ps1"
        hook.parent.mkdir()
        hook.write_text("Write-Output 'unterminated\n", encoding="utf-8")
        result = self.run_audit("check", ".chezmoiscripts/hook.ps1")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("ERROR: .chezmoiscripts/hook.ps1", result.stderr)

    def test_doctor_advisory_logging_and_strict_failure(self) -> None:
        (self.source / ".chezmoiignore").write_text("fixture\n", encoding="utf-8")
        advisory_env = dict(self.env)
        advisory_env.update({"AUDIT_DOCTOR_OUTPUT": "fixture doctor warning", "AUDIT_DOCTOR_RC": "4"})
        result = self.run_audit("check", ".chezmoiignore", env=advisory_env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("INFO: CHEZMOI_DOCTOR found issues (advisory)", result.stderr)
        logs = list((self.fixture.root / "audit logs").glob("CHEZMOI_DOCTOR.*.log"))
        self.assertEqual(len(logs), 1)
        self.assertIn("fixture doctor warning", logs[0].read_text(encoding="utf-8-sig"))

        strict_env = dict(advisory_env)
        strict_env["CZ_AUDIT_STRICT_CHEZMOI_DOCTOR"] = "1"
        strict = self.run_audit("check", ".chezmoiignore", env=strict_env)
        self.assertNotEqual(strict.returncode, 0)
        self.assertIn("fixture doctor warning", strict.stderr)


if __name__ == "__main__":
    unittest.main()
