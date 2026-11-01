from __future__ import annotations

import base64
import copy
import fnmatch
import json
import os
import re
import runpy
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from tests.support.powershell import POWERSHELL_AUDIT_IMAGE, require_fixture_powershell


ROOT = Path(__file__).resolve().parents[1]
LOAD_JSON = runpy.run_path(str(ROOT / 'assets/check-ai-tooling.py'))['load_json']
CONTAINER = 'private_Documents/development/container-dotfiles/devcontainers/gitlab.com/servers-homelab/homelab-IaC'
MISE = 'configs/mise_wsl2.toml'
NPM = f'{CONTAINER}/configs/npm_packages.txt'
MAC = '.chezmoiscripts/run_after_macos-opencode-pin.sh.tmpl'
WINDOWS = '.chezmoiscripts/run_after_windows-opencode-pin.ps1.tmpl'


def render(template: str, target_os: str) -> str:
    result = subprocess.run(
        ['chezmoi', '--source', str(ROOT), '--override-data',
         json.dumps({'chezmoi': {'os': target_os}}), 'execute-template',
         '-f', str(ROOT / template)],
        text=True, capture_output=True, check=True,
    )
    return result.stdout


class OpenCodePolicyTests(unittest.TestCase):
    def assert_sol_agent_policy(self, config, file_name):
        expected = {'plan', 'plan-GPT-xhigh', 'build', 'special-builder', 'agent-engineer'}
        agents = config['agent']
        self.assertEqual(config['model'], 'openai/gpt-6.1-sol')
        self.assertEqual(config['small_model'], 'openai/gpt-6-luna')
        self.assertIn('model: openai/gpt-6-astra', (ROOT / file_name).parent.joinpath('agents/plan-reviewer.md').read_text())
        for name in expected:
            self.assertEqual(agents[name]['model'], 'openai/gpt-6.1-sol', name)
        for name, agent in agents.items():
            if agent.get('model') == 'openai/gpt-6.1-sol':
                self.assertNotIn('temperature', agent, name)

    def setUp(self):
        text = (ROOT / 'renovate.json5').read_text(encoding='utf-8')
        self.config = json.loads(re.sub(r'^\s*//.*$', '', text, flags=re.M))

    def extract(self, file_name):
        dependencies = []
        for manager in self.config['customManagers']:
            if not any(re.search(pattern[1:-1], file_name)
                       for pattern in manager['managerFilePatterns']):
                continue
            for pattern in manager['matchStrings']:
                pattern = re.sub(r'\(\?<([A-Za-z]+)>', r'(?P<\1>', pattern)
                for match in re.finditer(pattern, (ROOT / file_name).read_text(encoding='utf-8')):
                    dep = match.groupdict()
                    dep.setdefault('depName', manager.get('depNameTemplate'))
                    dependencies.append((dep, manager))
        return dependencies

    def effective_policy(self, name, file_name, update_type):
        policy = {key: value for key, value in self.config.items() if key != 'packageRules'}
        for rule in self.config['packageRules']:
            checks = (
                ('matchManagers', 'custom.regex'), ('matchDepNames', name),
                ('matchFileNames', file_name), ('matchUpdateTypes', update_type),
            )
            if all(key not in rule or any(fnmatch.fnmatchcase(value, item) for item in rule[key])
                   for key, value in checks):
                policy.update(rule)
        return policy

    def test_exact_equal_stable_v1_pins_and_extractors(self):
        wsl = self.extract(MISE)
        self.assertEqual(len(wsl), 1)
        self.assertEqual(wsl[0][0]['depName'], 'anomalyco/opencode')
        self.assertEqual(wsl[0][1]['datasourceTemplate'], 'github-releases')
        self.assertEqual(wsl[0][1]['extractVersionTemplate'], '^v(?<version>.*)$')
        container = [(dep, manager) for dep, manager in self.extract(NPM)
                     if dep['depName'] == 'opencode-ai']
        self.assertEqual(len(container), 1)
        self.assertEqual(container[0][1]['datasourceTemplate'], 'npm')
        version = wsl[0][0]['currentValue']
        self.assertRegex(version, r'^1\.\d+\.\d+$')
        self.assertEqual(version, container[0][0]['currentValue'])

    def test_review_loop_matcher_is_container_pinned(self):
        packages = (ROOT / NPM).read_text(encoding='utf-8').splitlines()
        pins = [item for item in packages if item.startswith('picomatch@')]
        self.assertEqual(len(pins), 1)
        self.assertRegex(pins[0], r'^picomatch@\d+\.\d+\.\d+$')

    def test_cli_rules_override_patch_automerge_and_grouping(self):
        for name, file_name in [('anomalyco/opencode', MISE), ('opencode-ai', NPM)]:
            for update in ('patch', 'minor', 'major'):
                with self.subTest(name=name, update=update):
                    policy = self.effective_policy(name, file_name, update)
                    self.assertEqual(policy['allowedVersions'], '>=1.0.0 <2.0.0')
                    self.assertTrue(policy['ignoreUnstable'])
                    self.assertIsNone(policy['minimumReleaseAge'])
                    self.assertFalse(policy['automerge'])
                    self.assertEqual(policy['minimumGroupSize'], 2)
                    self.assertEqual(policy['groupName'], 'opencode v1 cli')

    def test_quota_v4_registrations_and_refresh_policy(self):
        runtime_files = (
            'private_dot_config/opencode/opencode.jsonc',
            'private_dot_config/opencode/tui.json',
            'private_Documents/development/container-dotfiles/dotfiles/private_dot_config/opencode/opencode.jsonc',
            'private_Documents/development/container-dotfiles/dotfiles/private_dot_config/opencode/tui.json',
        )
        for file_name in runtime_files:
            with self.subTest(file_name=file_name):
                config = LOAD_JSON(ROOT / file_name, jsonc=file_name.endswith('.jsonc'))
                self.assertEqual(config['plugin'].count('@slkiser/opencode-quota@4'), 1)
                self.assertFalse(any(dep['depName'] == '@slkiser/opencode-quota'
                                     for dep, _ in self.extract(file_name)))
                self.assertTrue(any(dep['depName'] == '@tarquinen/opencode-dcp'
                                    for dep, _ in self.extract(file_name)))

        sentinel = 'configs/host-ai-plugin-refresh.jsonc'
        manifest = LOAD_JSON(ROOT / sentinel, jsonc=True)
        versions = [plugin['version'] for plugin in manifest['opencode']['plugins']
                    if plugin['package'] == '@slkiser/opencode-quota']
        self.assertEqual(len(versions), 1)
        self.assertRegex(versions[0], r'^4\.\d+\.\d+$')
        quota_deps = [dep for dep, _ in self.extract(sentinel)
                      if dep['depName'] == '@slkiser/opencode-quota']
        self.assertEqual([dep['currentValue'] for dep in quota_deps], versions)
        for update in ('patch', 'minor', 'major'):
            with self.subTest(update=update):
                policy = self.effective_policy('@slkiser/opencode-quota', sentinel, update)
                self.assertEqual(policy['allowedVersions'], '>=4.0.0 <5.0.0')

    def test_unrelated_policies_and_self_update_remain_intact(self):
        self.assertEqual(self.config['minimumReleaseAge'], '7 days')
        self.assertEqual(self.effective_policy('@beads/bd', NPM, 'patch')['minimumReleaseAge'], '14 days')
        self.assertEqual(self.effective_policy('some-tool', NPM, 'patch')['minimumReleaseAge'], '7 days')
        for file_name in (
            'private_dot_config/opencode/opencode.jsonc',
            'private_Documents/development/container-dotfiles/dotfiles/private_dot_config/opencode/opencode.jsonc',
        ):
            self.assertRegex((ROOT / file_name).read_text(encoding='utf-8'), r'"autoupdate"\s*:\s*false')

    def test_sol_agents_have_no_temperature_setting(self):
        for file_name in (
            'private_dot_config/opencode/opencode.jsonc',
            'private_Documents/development/container-dotfiles/dotfiles/private_dot_config/opencode/opencode.jsonc',
        ):
            with self.subTest(file_name=file_name):
                config = LOAD_JSON(ROOT / file_name, jsonc=True)
                self.assert_sol_agent_policy(config, file_name)
                synthetic = dict(config, agent=dict(config['agent'], **{
                    'future-sol': {'model': 'openai/gpt-6.1-sol', 'temperature': 0.2},
                }))
                with self.assertRaises(AssertionError):
                    self.assert_sol_agent_policy(synthetic, file_name)

    def assert_plan_trial_policy(self, global_config, project_config):
        def merge(base, overlay):
            result = copy.deepcopy(base)
            for key, value in overlay.items():
                if isinstance(result.get(key), dict) and isinstance(value, dict):
                    result[key] = merge(result[key], value)
                else:
                    result[key] = copy.deepcopy(value)
            return result

        self.assertNotIn('Plan-Trial', global_config['agent'])
        plan = global_config['agent']['plan']
        self.assertEqual(plan['prompt'], '{file:./prompts/plan-agent.md}')
        self.assertEqual(plan['reasoningEffort'], 'high')
        trial = project_config['agent']['Plan-Trial']
        self.assertIsInstance(trial['description'], str)
        self.assertIn('trial', trial['description'].lower())
        expected = merge(plan, project_config['agent']['plan'])
        expected.update(
            model='openai/gpt-6.1-sol', description=trial['description'],
            prompt='{file:~/.config/opencode/prompts/plan-agent.md}',
        )
        native_plan_permissions = {'question': 'allow', 'plan_exit': 'allow'}
        expected['permission'].update(native_plan_permissions)
        self.assertEqual(trial, expected)

        def plannotator(config):
            registrations = [entry for entry in config.get('plugin', [])
                             if (entry[0] if isinstance(entry, list) else entry)
                             .startswith('@plannotator/opencode@')]
            self.assertEqual(len(registrations), 1)
            self.assertIsInstance(registrations[0], list)
            return registrations[0]

        global_plugin = plannotator(global_config)
        self.assertEqual(global_plugin[0],
                          '@plannotator/opencode@{env:PLANNOTATOR_PIN_VERSION}')
        expected_plugin = copy.deepcopy(global_plugin)
        self.assertNotIn('Plan-Trial', expected_plugin[1]['planningAgents'])
        expected_plugin[1]['planningAgents'].append('Plan-Trial')
        self.assertEqual(plannotator(project_config), expected_plugin)

    def test_repo_local_plan_trial_matches_ordinary_plan(self):
        project_config = LOAD_JSON(ROOT / '.opencode/opencode.jsonc', jsonc=True)
        for file_name in (
            'private_dot_config/opencode/opencode.jsonc',
            'private_Documents/development/container-dotfiles/dotfiles/private_dot_config/opencode/opencode.jsonc',
        ):
            with self.subTest(file_name=file_name):
                global_config = LOAD_JSON(ROOT / file_name, jsonc=True)
                self.assert_sol_agent_policy(global_config, file_name)
                self.assert_plan_trial_policy(global_config, project_config)

    def test_plan_trial_policy_rejects_drift(self):
        global_config = LOAD_JSON(ROOT / 'private_dot_config/opencode/opencode.jsonc', jsonc=True)
        project_config = LOAD_JSON(ROOT / '.opencode/opencode.jsonc', jsonc=True)
        self.assert_plan_trial_policy(global_config, project_config)
        mutations = (
            (('agent', 'Plan-Trial', 'reasoningEffort'), 'xhigh'),
            (('agent', 'Plan-Trial', 'permission', 'task', 'plan-reviewer'), 'allow'),
            (('agent', 'Plan-Trial', 'permission', 'question'), 'deny'),
            (('agent', 'Plan-Trial', 'permission', 'edit'), 'allow'),
            (('agent', 'Plan-Trial', 'tools', 'cbm_index_repository'), False),
            (('agent', 'Plan-Trial', 'prompt'), '{file:./prompts/trial-copy.md}'),
            (('plugin', 0, 1, 'planningAgents'), ['plan']),
            (('plugin', 0, 0), '@plannotator/opencode@latest'),
        )
        for keys, value in mutations:
            with self.subTest(keys=keys):
                mutated = copy.deepcopy(project_config)
                target = mutated
                for key in keys[:-1]:
                    target = target[key]
                target[keys[-1]] = value
                with self.assertRaises(AssertionError):
                    self.assert_plan_trial_policy(global_config, mutated)

    def test_policy_ci_runs_on_renovate_branches(self):
        ci = (ROOT / '.gitlab-ci.yml').read_text()
        job = re.split(r'\n\S', ci.split('\nopencode-v1-policy:\n', 1)[1], maxsplit=1)[0]
        self.assertIn('python3 -m unittest tests.test_opencode_v1.OpenCodePolicyTests', job)
        self.assertIn("- if: '$CI_PIPELINE_SOURCE == \"merge_request_event\"'", job)
        self.assertIn("- if: '$CI_PIPELINE_SOURCE == \"push\"'", job)
        self.assertNotIn('when: never', job)


@unittest.skipUnless(shutil.which('chezmoi'), 'chezmoi is unavailable')
class OpenCodeRenderTests(unittest.TestCase):
    def test_platform_gates(self):
        for template, active_os in ((MAC, 'darwin'), (WINDOWS, 'windows')):
            for target_os in ('darwin', 'linux', 'windows'):
                with self.subTest(template=template, target_os=target_os):
                    self.assertEqual(bool(render(template, target_os).strip()), target_os == active_os)

    def test_shebang_and_windows_admission(self):
        self.assertTrue(render(MAC, 'darwin').startswith('#!/usr/bin/env bash\n'))
        rules = render('.chezmoiignore', 'windows').splitlines()
        self.assertIn('.chezmoiscripts/**', rules)
        admission = '!.chezmoiscripts/windows-opencode-pin.ps1'
        self.assertIn(admission, rules)
        self.assertGreater(rules.index(admission), rules.index('.chezmoiscripts/**'))


BREW = '''#!/bin/sh
printf '%s\n' "$*" >> "$FIXTURE/calls"
[ "$*" != "$FAIL_QUERY" ] || exit 7
case "$*" in
  'list --formula') printf '%s\n' "$FORMULAS" ;;
  'list --versions opencode') printf '%s\n' "$INSTALLED" ;;
  '--prefix opencode') printf '%s/owned\n' "$FIXTURE" ;;
  'list --pinned') /bin/cat "$FIXTURE/pins" ;;
  'pin opencode')
    [ "$PIN_RC" = 0 ] || exit "$PIN_RC"
    [ "$NO_PIN" = 1 ] || printf 'opencode\n' >> "$FIXTURE/pins" ;;
  *) exit 99 ;;
esac
'''
CHOCO = '''#!/bin/sh
printf '%s\n' "$*" >> "$FIXTURE/calls"
[ "$*" != "$FAIL_QUERY" ] || exit 7
case "$*" in
  'list --exact opencode --limit-output')
    if [ -f "$FIXTURE/list-ran" ]; then
      printf '%s\n' "$LIST_DIAGNOSTICS_AFTER" "${INSTALLED_AFTER-$INSTALLED}"
    else
      printf '%s\n' "$LIST_DIAGNOSTICS" "$INSTALLED"
      : > "$FIXTURE/list-ran"
    fi
    exit "$LIST_RC" ;;
  'pin list --limit-output') /bin/cat "$FIXTURE/pins" ;;
  'pin add --name=opencode --yes')
    [ "$PIN_RC" = 0 ] || [ "$PIN_RC" = 2 ] || exit "$PIN_RC"
    [ "$NO_PIN" = 1 ] || printf '%s\n' "$INSTALLED" >> "$FIXTURE/pins"
    exit "$PIN_RC" ;;
  *) exit 99 ;;
esac
'''
CLI = '''#!/bin/sh
[ "$1" = --version ] || exit 99
printf '%s\n' "$ACTUAL"
exit "$CLI_RC"
'''


CLEANUP_DIAGNOSTICS = r'''This is try 1/3. Retrying after 300 milliseconds.
Error converted to warning:
Access to the path 'C:\ProgramData\chocolatey\choco.exe.old' is denied.
This is try 2/3. Retrying after 400 milliseconds.
Error converted to warning:
Access to the path 'C:\ProgramData\chocolatey\choco.exe.old' is denied.
Maximum tries of 3 reached. Throwing error.'''


@unittest.skipUnless(shutil.which('chezmoi'), 'chezmoi is unavailable')
class WindowsInstalledOutputTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.backend = require_fixture_powershell(image=POWERSHELL_AUDIT_IMAGE)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='opencode-list-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'hook.ps1').write_text(render(WINDOWS, 'windows'))
        (self.root / 'harness.ps1').write_text(r'''
. "$PSScriptRoot/hook.ps1"
$cases = Get-Content -Raw -LiteralPath "$PSScriptRoot/cases.json" | ConvertFrom-Json
$results = @(
  foreach ($text in $cases) {
    $warnings = @()
    try {
      $value = @(ConvertFrom-OpenCodeInstalledOutput $text -WarningVariable warnings)
      @{ accepted = $true; value = $value; warnings = @($warnings | ForEach-Object { $_.ToString() }) }
    } catch {
      @{ accepted = $false; error = $_.ToString(); warnings = @($warnings | ForEach-Object { $_.ToString() }) }
    }
  }
)
ConvertTo-Json -InputObject $results -Depth 5 | Set-Content -LiteralPath "$PSScriptRoot/results.json"
''')

    def parse_cases(self, cases):
        (self.root / 'cases.json').write_text(json.dumps(cases))
        result = self.backend.run(self.root, script=self.root / 'harness.ps1', timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return json.loads((self.root / 'results.json').read_text(encoding='utf-8-sig')), result

    def test_clean_and_noisy_records_return_only_canonical_data(self):
        record = 'opencode|1.19.5'
        cases = [record, f'{CLEANUP_DIAGNOSTICS}\n{record}',
                 f'{record}\n{CLEANUP_DIAGNOSTICS}',
                 f'\n \n{CLEANUP_DIAGNOSTICS}\n\n{record}\n \n']
        cases += [text.replace('\n', '\r\n') for text in cases]
        results, process = self.parse_cases(cases)
        for text, parsed in zip(cases, results, strict=True):
            with self.subTest(text=text):
                self.assertTrue(parsed['accepted'], parsed)
                self.assertEqual(parsed['value'], [record])
                self.assertEqual(bool(parsed['warnings']), CLEANUP_DIAGNOSTICS in text.replace('\r\n', '\n'))
        self.assertIn('Chocolatey list diagnostic:', process.stdout)
        self.assertIn('choco.exe.old', process.stdout)

    def test_empty_output_means_absent(self):
        results, _ = self.parse_cases(['', '\n \r\n\t'])
        for parsed in results:
            self.assertTrue(parsed['accepted'], parsed)
            self.assertEqual(parsed['value'], [''])
            self.assertEqual(parsed['warnings'], [])

    def test_diagnostic_only_output_is_not_absence(self):
        results, _ = self.parse_cases([CLEANUP_DIAGNOSTICS, 'unparseable output'])
        for parsed in results:
            self.assertFalse(parsed['accepted'], parsed)
            self.assertIn('Expected one stable v1', parsed['error'])
            self.assertTrue(parsed['warnings'])

    def test_reject_unsupported_malformed_and_ambiguous_records(self):
        invalid = ['opencode|2.0.12', 'opencode|1.2.3-beta.1', 'opencode|unknown',
                   'opencode|', 'opencode', 'opencode 1.2.3', 'opencode|1.2.3|extra',
                   'OpenCode|1.2.3', 'other|1.2.3', ' opencode|1.2.3',
                   'opencode|1.2.3\nopencode|1.2.3', 'opencode|1.2.3\nopencode|1.2.4']
        cases = invalid + [f'opencode|1.19.5\n{text}' for text in invalid]
        results, _ = self.parse_cases(cases)
        for text, parsed in zip(cases, results, strict=True):
            with self.subTest(text=text):
                self.assertFalse(parsed['accepted'], parsed)
                self.assertIn('Expected one stable v1', parsed['error'])


class FixtureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='opencode-hold-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'bin').mkdir()
        (self.root / 'owned/bin').mkdir(parents=True)
        (self.root / 'pins').write_text('unrelated|4.0.0\n' if self.windows else 'unrelated\n')
        self.env = dict(os.environ, FIXTURE=str(self.root), FORMULAS='opencode',
                        INSTALLED='opencode|1.18.31' if self.windows else 'opencode 1.18.31',
                        ACTUAL='1.18.31', FAIL_QUERY='', PIN_RC='0', NO_PIN='0', CLI_RC='0',
                        ELEVATED='1')
        self.env.pop('BASH_ENV', None)

    def executable(self, relpath, text):
        file_path = self.root / relpath
        file_path.write_text(text)
        file_path.chmod(0o755)
        return file_path

    def calls(self):
        file_path = self.root / 'calls'
        return file_path.read_text().splitlines() if file_path.exists() else []

    def assert_result(self, success=True):
        result = self.run_hook()
        self.assertEqual(result.returncode == 0, success, result.stdout + result.stderr)
        allowed = ({'list --exact opencode --limit-output', 'pin list --limit-output', 'pin add --name=opencode --yes'}
                   if self.windows else {'list --formula', 'list --versions opencode', '--prefix opencode', 'list --pinned', 'pin opencode'})
        self.assertTrue(set(self.calls()) <= allowed, self.calls())
        self.assertIn('unrelated', (self.root / 'pins').read_text())
        return result

    def check_idempotency(self):
        self.assert_result()
        self.assert_result()
        pin_call = 'pin add --name=opencode --yes' if self.windows else 'pin opencode'
        self.assertEqual(self.calls().count(pin_call), 1)
        (self.root / 'pins').write_text('unrelated\n')
        self.assert_result()
        self.assertEqual(self.calls().count(pin_call), 2)

    def check_bad_versions(self):
        for version in ('2.0.12', '1.2.3-beta.1', 'unknown', '1.2.3 1.2.4', '1.2.3\nopencode|1.2.4'):
            with self.subTest(version=version):
                self.env['INSTALLED'] = ('opencode|' if self.windows else 'opencode ') + version
                self.assert_result(False)
        self.assertFalse(any(call.startswith(('pin add', 'pin opencode')) for call in self.calls()))

    def check_failures(self):
        original = self.env.copy()
        query = 'pin list --limit-output' if self.windows else 'list --pinned'
        for changes in ({'FAIL_QUERY': query}, {'PIN_RC': '1'}, {'NO_PIN': '1'},
                        {'CLI_RC': '1'}, {'ACTUAL': '2.0.0'}):
            with self.subTest(changes=changes):
                self.env = dict(original, **changes)
                self.assert_result(False)


@unittest.skipUnless(os.name != 'nt' and shutil.which('chezmoi'), 'POSIX fixture with chezmoi required')
class MacHoldTests(FixtureTests):
    windows = False

    def setUp(self):
        super().setUp()
        self.executable('bin/brew', BREW)
        cli = self.executable('owned/bin/opencode', CLI)
        (self.root / 'bin/opencode').symlink_to(cli)
        self.env['PATH'] = str(self.root / 'bin')
        self.script = self.root / 'hook.sh'
        self.script.write_text(render(MAC, 'darwin'))

    def run_hook(self):
        return subprocess.run(['/bin/bash', str(self.script)], env=self.env, text=True, capture_output=True)

    def test_pin_idempotency_and_reassertion(self): self.check_idempotency()
    def test_reject_unsupported_versions(self): self.check_bad_versions()
    def test_failures_do_not_claim_protection(self): self.check_failures()

    def test_absent_manager_or_package(self):
        self.env['FORMULAS'] = 'other'
        self.assertIn('not established', self.assert_result().stdout)
        (self.root / 'bin/brew').unlink()
        self.assertIn('not established', self.assert_result().stdout)

    def test_same_version_shadow_and_missing_executable(self):
        (self.root / 'bin/opencode').unlink()
        self.assert_result(False)
        self.executable('bin/opencode', CLI)
        self.assertIn('PATH conflict', self.assert_result(False).stderr)


@unittest.skipUnless(os.name != 'nt' and shutil.which('chezmoi'), 'POSIX executable fixtures required; native Windows smoke remains separate')
class WindowsHoldTests(FixtureTests):
    windows = True

    @classmethod
    def setUpClass(cls):
        cls.backend = require_fixture_powershell(image=POWERSHELL_AUDIT_IMAGE)

    def setUp(self):
        super().setUp()
        self.env.update(LIST_DIAGNOSTICS='', LIST_DIAGNOSTICS_AFTER='', LIST_RC='0')
        self.executable('bin/choco.exe', CHOCO)
        self.executable('bin/opencode.exe', CLI)
        (self.root / 'hook.ps1').write_text(render(WINDOWS, 'windows'))
        (self.root / 'harness.ps1').write_text(r'''
. "$env:FIXTURE/hook.ps1"
function Test-IsElevated { $env:ELEVATED -eq '1' }
function Get-Command {
  param($Name, $CommandType, [switch]$All, $ErrorAction)
  if ($Name -eq 'choco.exe') {
    if (Test-Path "$env:FIXTURE/bin/choco.exe") { [pscustomobject]@{ Source = "$env:FIXTURE/bin/choco.exe" } }
  } elseif ($Name -eq 'opencode') {
    if ($env:SHADOW -eq '1') { [pscustomobject]@{ Source = "$env:FIXTURE/other/opencode.exe" } }
    elseif (Test-Path "$env:FIXTURE/bin/opencode.exe") { [pscustomobject]@{ Source = "$env:FIXTURE/bin/opencode.exe" } }
  } else { throw "Unexpected command lookup: $Name" }
}
Set-Content -LiteralPath "$env:FIXTURE/harness-ran" -Value ''
try { Set-OpenCodeV1Hold } catch { [Console]::Error.WriteLine($_.Exception.Message); exit 1 }
''')

    def run_hook(self):
        (self.root / 'list-ran').unlink(missing_ok=True)
        sentinel = self.root / 'harness-ran'
        sentinel.unlink(missing_ok=True)
        result = self.backend.run(
            self.root, script=self.root / 'harness.ps1', environ=self.env,
            env_keys=('FIXTURE', 'INSTALLED', 'ACTUAL', 'FAIL_QUERY', 'PIN_RC', 'NO_PIN', 'CLI_RC', 'SHADOW', 'ELEVATED',
                      'LIST_DIAGNOSTICS', 'LIST_DIAGNOSTICS_AFTER', 'INSTALLED_AFTER', 'LIST_RC'),
            path_keys=('FIXTURE',), timeout=60,
        )
        self.assertTrue(sentinel.exists(),
                        f'harness did not execute (rc={result.returncode}):\n{result.stdout}{result.stderr}')
        return result

    def test_pin_idempotency_and_reassertion(self): self.check_idempotency()
    def test_reject_unsupported_versions(self): self.check_bad_versions()
    def test_failures_do_not_claim_protection(self): self.check_failures()

    def test_absent_manager_or_package(self):
        self.env['INSTALLED'] = ''
        self.assertIn('not established', self.assert_result().stdout)
        (self.root / 'bin/choco.exe').unlink()
        self.assertIn('not established', self.assert_result().stdout)

    def test_same_version_shadow_and_missing_executable(self):
        self.env['SHADOW'] = '1'
        self.assertIn('PATH conflict', self.assert_result(False).stderr)
        self.env['SHADOW'] = ''
        (self.root / 'bin/opencode.exe').unlink()
        self.assert_result(False)

    def test_enhanced_no_change_code_requires_verified_pin(self):
        self.env['PIN_RC'] = '2'
        self.assert_result()
        (self.root / 'pins').write_text('unrelated\n')
        self.env['NO_PIN'] = '1'
        self.assert_result(False)

    def test_conflicting_existing_pin_is_not_removed(self):
        (self.root / 'pins').write_text('unrelated\nopencode|1.18.30\n')
        self.assert_result(False)
        self.assertNotIn('pin add --name=opencode --yes', self.calls())

    def test_cleanup_diagnostics_can_differ_between_queries(self):
        self.env.update(LIST_DIAGNOSTICS=CLEANUP_DIAGNOSTICS,
                        LIST_DIAGNOSTICS_AFTER='A different Chocolatey diagnostic')
        result = self.assert_result()
        self.assertIn('is held', result.stdout)
        self.assertIn('choco.exe.old', result.stdout)
        self.assertIn('A different Chocolatey diagnostic', result.stdout)
        self.assertEqual(self.calls().count('list --exact opencode --limit-output'), 2)
        self.assertEqual(self.calls().count('pin add --name=opencode --yes'), 1)

    def test_changed_installed_record_during_pinning_fails(self):
        self.env.update(LIST_DIAGNOSTICS=CLEANUP_DIAGNOSTICS, INSTALLED_AFTER='opencode|1.19.5')
        self.assertIn('changed during pinning', self.assert_result(False).stderr)

    def test_nonzero_list_with_valid_record_fails_before_pinning(self):
        self.env.update(LIST_DIAGNOSTICS=CLEANUP_DIAGNOSTICS, LIST_RC='7')
        self.assertIn('failed (exit 7)', self.assert_result(False).stderr)
        self.assertEqual(self.calls(), ['list --exact opencode --limit-output'])

    def test_invalid_final_list_cannot_claim_a_hold(self):
        for text in ('opencode|1.18.31\nopencode|1.18.31', 'opencode|2.0.0',
                     CLEANUP_DIAGNOSTICS, ''):
            with self.subTest(text=text):
                self.env['INSTALLED_AFTER'] = text
                result = self.assert_result(False)
                self.assertNotIn('is held', result.stdout)

    def test_unelevated_missing_pin_hands_off_instead_of_writing(self):
        self.env['ELEVATED'] = ''
        self.env['LIST_DIAGNOSTICS'] = CLEANUP_DIAGNOSTICS
        result = self.assert_result()
        self.assertFalse(any(call.startswith('pin add') for call in self.calls()), self.calls())
        self.assertEqual((self.root / 'pins').read_text(), 'unrelated|4.0.0\n')
        self.assertIn('not established', result.stdout + result.stderr)
        encoded = re.search(r"'-EncodedCommand','([A-Za-z0-9+/=]+)'", result.stdout)
        self.assertIsNotNone(encoded, result.stdout)
        decoded = base64.b64decode(encoded.group(1)).decode('utf-16-le')
        self.assertIn('pin add --name=opencode --yes', decoded)
        self.assertIn('/bin/choco.exe', decoded)
        self.assertIn('pin list --limit-output', decoded)
        self.assertIn('$LASTEXITCODE -notin 0, 2', decoded)

    def test_unelevated_established_pin_needs_no_handoff(self):
        self.env['ELEVATED'] = ''
        self.env['LIST_DIAGNOSTICS'] = CLEANUP_DIAGNOSTICS
        (self.root / 'pins').write_text('unrelated|4.0.0\nopencode|1.18.31\n')
        result = self.assert_result()
        self.assertFalse(any(call.startswith('pin add') for call in self.calls()), self.calls())
        self.assertNotIn('-EncodedCommand', result.stdout)
        self.assertIn('is held', result.stdout)

    def test_unelevated_conflicting_pin_still_fails(self):
        self.env['ELEVATED'] = ''
        self.env['LIST_DIAGNOSTICS'] = CLEANUP_DIAGNOSTICS
        (self.root / 'pins').write_text('unrelated|4.0.0\nopencode|1.18.30\n')
        result = self.assert_result(False)
        self.assertNotIn('-EncodedCommand', result.stdout)


if __name__ == '__main__':
    unittest.main()
