from __future__ import annotations

import os
import shlex
import shutil
import subprocess
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


class CzAuditPosixTests(CzAuditFixture, unittest.TestCase):
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
        grep_command = os.readlink(tools / "grep")
        write_executable(
            self.fixture.fake_bin / "grep",
            "#!/bin/sh\n"
            "case \"$*\" in\n"
            "  *'/proc/sys/kernel/osrelease'*) exit 1 ;;\n"
            "esac\n"
            f"exec {shlex.quote(grep_command)} \"$@\"\n",
        )
        unavailable_env = dict(self.env)
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
