from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import unittest
from collections import Counter
from pathlib import Path

from tests.support.fixtures import isolated_environment, read_json_lines, write_executable
from tests.test_chezmoi_lifecycle_render import CHEZMOI, render_template


VDI = ".chezmoiscripts/run_onchange_after_macos-vdi-apps.sh.tmpl"
NFS = ".chezmoiscripts/run_after_macos-nfs-config.sh.tmpl"
COPYQ = ".chezmoiscripts/run_after_update_copyq.sh.tmpl"
AGENTS = ".chezmoiscripts/run_onchange_after_reload_launch_agents.sh.tmpl"
OPENUSAGE = ".chezmoiscripts/run_after_macos-openusage-integrations.sh.tmpl"

ABSOLUTE_COMMANDS = {
    VDI: Counter({"/bin/bash": 1, "/bin/rm": 1, "/usr/sbin/pkgutil": 1,
                  "/usr/bin/awk": 2, "/usr/bin/defaults": 1, "/usr/bin/mktemp": 2,
                  "/usr/bin/hdiutil": 2, "/usr/bin/shasum": 1, "/usr/bin/tr": 2,
                  "/usr/bin/basename": 1, "/usr/bin/curl": 1, "/usr/bin/sudo": 1,
                  "/usr/sbin/installer": 1, "/usr/bin/find": 2}),
    NFS: Counter({"/usr/bin/env": 1, "/bin/rm": 1, "/usr/bin/awk": 1,
                  "/usr/bin/printf": 7, "/usr/bin/mktemp": 1, "/usr/bin/cmp": 2,
                  "/usr/bin/sudo": 1, "/usr/bin/install": 1}),
    COPYQ: Counter({"/bin/bash": 1}),
    AGENTS: Counter({"/bin/bash": 1}),
    OPENUSAGE: Counter({"/bin/bash": 1}),
}

FAKE_COMMAND = r'''
import json, os, pathlib, shutil, sys
root = pathlib.Path(os.environ['FIXTURE_ROOT'])
name = pathlib.Path(sys.argv[0]).name
args = sys.argv[1:]
with (root / 'calls.jsonl').open('a') as handle:
    handle.write(json.dumps({'command': name, 'args': args}) + '\n')
def owned(value):
    target = pathlib.Path(value)
    if not target.is_absolute() or not target.resolve().is_relative_to(root.resolve()):
        sys.exit(96)
    return target
if name == 'pkgutil':
    assert args[:1] == ['--pkg-info'] and len(args) == 2
    version = json.loads(os.environ.get('TEST_VERSIONS', '{}')).get(args[1], '')
    if version: print('version: ' + version)
elif name == 'defaults':
    assert args[0] == 'read' and len(args) == 3
    owned(args[1])
elif name == 'op':
    assert args[0] == 'read' and len(args) == 2
    assert args[1] in ('op://fixture/zoom-url', 'op://fixture/citrix-url')
    print('https://fixture.example.invalid/' + ('zoom.pkg' if args[1].endswith('zoom-url') else 'citrix.dmg'))
elif name == 'curl':
    if os.environ.get('TEST_FAIL') == 'curl': sys.exit(44)
    if args[:2] == ['-sL', 'https://api.github.com/repos/hluk/CopyQ/releases/latest']:
        print(json.dumps({'tag_name': 'v1.2.3', 'assets': [{'name': 'copyq-macos-12.dmg', 'browser_download_url': 'https://fixture.example.invalid/copyq.dmg'}]}))
    else:
        assert args[0] == '-fsSL'
        dest = args[args.index('--output') + 1] if '--output' in args else args[args.index('-o') + 1]
        owned(dest).write_bytes(b'fixture package\n')
elif name == 'sudo':
    if os.environ.get('TEST_FAIL') == 'sudo': sys.exit(42)
    assert len(args) >= 2
    if args[0].endswith('/installer'):
        assert args[1] == '-pkg' and args[-2:] == ['-target', '/']
        owned(args[2])
    else:
        assert args[0].endswith('/install') and args[1:7] == ['-o', 'root', '-g', 'wheel', '-m', '0644']
        shutil.copyfile(owned(args[7]), owned(args[8]))
elif name == 'hdiutil':
    if args[0] == 'attach':
        if '-mountpoint' in args:
            mount = owned(args[args.index('-mountpoint') + 1])
            (mount / 'CitrixFixture.pkg').write_text('fixture package\n')
        else:
            assert args[-2:] == ['-nobrowse', '-plist']
            owned(args[1])
            mount = root / 'mount'
            print('<key>mount-point</key>\n<string>' + str(mount) + '</string>')
    else:
        assert args[0] == 'detach' and len(args) in (2, 3)
        owned(args[1])
elif name == 'brew':
    assert args == ['--prefix', 'janekbaraniewski/tap/openusage']
    if os.environ.get('TEST_FAIL') == 'brew': sys.exit(45)
    print(root / 'brew-prefix')
elif name == 'launchctl':
    assert args[0] in ('bootout', 'remove', 'list', 'load', 'unload')
    if args[0] in ('load', 'unload'): owned(args[1])
    if args[0] == 'list': sys.exit(0 if args[1] == 'com.user.vncmonitor' else 1)
    if args[0] == 'load' and os.environ.get('TEST_FAIL') == 'load': sys.exit(43)
elif name == 'id':
    assert args == ['-u']
    print('1000')
elif name in ('rm', 'cp'):
    operands = [a for a in args if not a.startswith('-')]
    assert operands
    for value in operands: owned(value)
    if name == 'rm':
        for value in operands:
            target = owned(value)
            if target.is_dir() and not target.is_symlink(): shutil.rmtree(target)
            elif target.exists() or target.is_symlink(): target.unlink()
    else:
        assert len(operands) == 2
        shutil.copytree(owned(operands[0]), owned(operands[1]) / pathlib.Path(operands[0]).name)
elif name in ('xattr', 'codesign'):
    owned(args[-1])
else:
    sys.exit(97)
'''

OPENUSAGE_COMMAND = r'''
import os, pathlib, sys
args = sys.argv[1:]
if args == ['version']:
    print('1.2.3 fixture')
elif args[:2] == ['integrations', 'install'] and len(args) == 3:
    root = pathlib.Path(os.environ['FIXTURE_ROOT']).resolve()
    config = pathlib.Path(os.environ['XDG_CONFIG_HOME']).resolve()
    assert config.is_relative_to(root) and pathlib.Path(os.environ['CLAUDE_SETTINGS_FILE']).resolve().is_relative_to(root)
    if args[2] == 'claude_code':
        target = config / 'openusage/hooks/claude-hook.sh'
    elif args[2] == 'opencode':
        target = config / 'opencode/plugins/openusage-telemetry.ts'
    else:
        sys.exit(98)
    target.parent.mkdir(parents=True, exist_ok=True)
    version = '0.0.0' if os.environ.get('TEST_FAIL') == 'version' else '1.2.3'
    target.write_text('openusage-integration-version: ' + version + '\n')
else:
    sys.exit(99)
'''


def confine(script: str, hook: str, root: Path, home: Path) -> str:
    observed = Counter(re.findall(r"(?<![\w/}])/(?:usr/(?:bin|sbin)|bin)/[\w.-]+", script))
    if observed != ABSOLUTE_COMMANDS[hook]:
        raise AssertionError(f"{hook} absolute command drift: {observed - ABSOLUTE_COMMANDS[hook]}, {ABSOLUTE_COMMANDS[hook] - observed}")
    redirects = {
        VDI: {"/usr/sbin/pkgutil": (1, root / 'bin/pkgutil'),
              "/usr/bin/defaults": (1, root / 'bin/defaults'),
              "/usr/bin/curl": (1, root / 'bin/curl'),
              "/usr/bin/hdiutil": (2, root / 'bin/hdiutil'),
              "/usr/bin/sudo": (1, root / 'bin/sudo'),
              "/usr/sbin/installer": (1, root / 'bin/installer'),
              "/bin/rm": (1, root / 'bin/rm'),
              "/Applications/": (4, f'{root}/applications/')},
        NFS: {'NFS_CONFIG_FILE="/etc/nfs.conf"': (1, f'NFS_CONFIG_FILE="{root}/etc/nfs.conf"'),
              "/usr/bin/sudo": (1, root / 'bin/sudo'),
              "/usr/bin/install": (1, root / 'bin/install'),
              "/bin/rm": (1, root / 'bin/rm')},
        COPYQ: {'APP_PATH="/Applications/$APP_NAME"': (1, f'APP_PATH="{root}/applications/$APP_NAME"'),
                '/Applications/': (1, f'{root}/applications/'),
                '/tmp/copyq.XXXXXX': (1, root / 'tmp/copyq.XXXXXX')},
        AGENTS: {'/tmp/macos-ssh-agent': (1, root / 'tmp/macos-ssh-agent'),
                 '/tmp/com.user.ssh-agent-relay.out': (1, root / 'tmp/com.user.ssh-agent-relay.out'),
                 '/tmp/com.user.ssh-agent-relay.err': (1, root / 'tmp/com.user.ssh-agent-relay.err')},
        OPENUSAGE: {},
    }
    if hook == AGENTS:
        anchor = 'HOME_DIR="/fixture/home"'
        if script.count(anchor) != 1:
            raise AssertionError('LaunchAgents home drift')
        script = script.replace(anchor, f'HOME_DIR="{home}"')
    for original, (count, replacement) in redirects[hook].items():
        if script.count(original) != count:
            raise AssertionError(f"{hook} redirect drift: {original}")
        script = script.replace(original, str(replacement))
    for unsafe in ('/Applications/', '/etc/nfs.conf', '/tmp/copyq.', '/tmp/macos-ssh-agent',
                   '/tmp/com.user.ssh-agent-relay', '/usr/bin/curl', '/usr/bin/sudo',
                   '/usr/bin/hdiutil', '/usr/sbin/installer', '/usr/sbin/pkgutil'):
        if re.search(r'(?<![\w/])' + re.escape(unsafe), script):
            raise AssertionError(f"{hook} still references {unsafe}")
    return script


@unittest.skipUnless(CHEZMOI, 'chezmoi is required')
class MacosLifecycleBoundaries(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture_context = isolated_environment(prefix='macos-lifecycle-')
        self.fixture = self.fixture_context.__enter__()
        self.addCleanup(self.fixture_context.__exit__, None, None, None)
        f = self.fixture
        for directory in ('applications', 'etc', 'mount', 'brew-prefix/bin'):
            (f.root / directory).mkdir(parents=True)
        (f.root / 'mount/CopyQ.app').mkdir()
        for name in ('pkgutil', 'defaults', 'op', 'curl', 'sudo', 'hdiutil', 'brew',
                     'launchctl', 'id', 'rm', 'cp', 'xattr', 'codesign'):
            write_executable(f.fake_bin / name, '#!/usr/bin/env python3\n' + FAKE_COMMAND)
        write_executable(f.root / 'brew-prefix/bin/openusage', '#!/usr/bin/env python3\n' + OPENUSAGE_COMMAND)
        self.env = {'HOME': str(f.home), 'PATH': f'{f.fake_bin}:/usr/bin:/bin',
                    'TMPDIR': str(f.root / 'tmp'), 'LC_ALL': 'C', 'CHEZMOI_NO_TTY': '1',
                    'FIXTURE_ROOT': str(f.root)}

    def render(self, hook: str, configure=None) -> str:
        return confine(render_template(hook, 'macos', configure, environment=self.env),
                       hook, self.fixture.root, self.fixture.home)

    def run_hook(self, script: str) -> subprocess.CompletedProcess[str]:
        f = self.fixture
        target = f.root / 'lifecycle.sh'
        target.write_text(script, encoding='utf-8')
        if sys.platform == 'darwin':
            docker_bin = shutil.which('docker')
            if not docker_bin:
                self.skipTest('Linux container required for active macOS hook simulation')
            image = 'mcr.microsoft.com/devcontainers/python:3.12-bookworm'
            available = subprocess.run([docker_bin, 'image', 'inspect', image],
                                       capture_output=True, check=False, timeout=10)
            if available.returncode != 0:
                self.skipTest('Local Linux test image required; no image pull during tests')
            command = [docker_bin, 'run', '--rm', '--pull', 'never', '--network', 'none',
                       '--read-only', '--cap-drop', 'ALL',
                       '--security-opt', 'no-new-privileges', '--mount',
                       f'type=bind,source={f.root},target={f.root}', '--workdir', str(f.root)]
            for name, value in self.env.items():
                command += ['--env', f'{name}={value}']
            command += [image, '/bin/bash', str(target)]
        elif sys.platform.startswith('linux'):
            command = ['/bin/bash', str(target)]
        else:
            self.skipTest('Linux execution host required')
        host_env = os.environ.copy() if sys.platform == 'darwin' else self.env
        return subprocess.run(command, cwd=f.root, env=host_env, text=True,
                              capture_output=True, check=False, timeout=60)

    def calls(self, name: str) -> list[list[str]]:
        return [entry['args'] for entry in read_json_lines(self.fixture.root / 'calls.jsonl')
                if entry['command'] == name]

    def test_redirects_fail_closed_on_source_drift(self) -> None:
        script = render_template(NFS, 'macos', environment=self.env)
        with self.assertRaisesRegex(AssertionError, 'redirect drift'):
            confine(script.replace('/etc/nfs.conf', '/etc/other.conf'), NFS,
                    self.fixture.root, self.fixture.home)
        with self.assertRaisesRegex(AssertionError, 'absolute command drift'):
            confine(script + '\n/usr/bin/curl https://example.invalid\n', NFS,
                    self.fixture.root, self.fixture.home)

    def test_vdi_gates_and_synthetic_installer(self) -> None:
        f = self.fixture
        sha = hashlib.sha256(b'fixture package\n').hexdigest()
        (f.root / 'Citrix.dmg').write_bytes(b'fixture package\n')
        def configure(data: dict[str, object]) -> None:
            vdi = data['macos_vdi']
            vdi.update({'enabled': True, 'install': True})
            vdi['citrix'].update({'desired_family': '26.03', 'display_version': '26.03',
                                  'default_dmg_path': str(f.root / 'Citrix.dmg'), 'dmg_sha256': sha})
            vdi['zoom'].update({'desired_pkg_version': '6.4', 'pkg_url_op_ref': 'op://fixture/zoom-url',
                                 'pkg_sha256': sha})
        script = self.render(VDI, configure)
        disabled = script.replace('VDI_ENABLED=true', 'VDI_ENABLED=false')
        self.assertNotEqual(disabled, script)
        self.assertEqual(self.run_hook(disabled).returncode, 0)
        self.assertFalse(self.calls('op'))
        report = script.replace('INSTALL_ENABLED=true', 'INSTALL_ENABLED=false')
        self.assertNotEqual(report, script)
        self.assertEqual(self.run_hook(report).returncode, 0)
        self.assertFalse(self.calls('curl'))
        self.env['TEST_VERSIONS'] = json.dumps({'us.zoom.ZoomVDI': '7.0', 'com.citrix.ICAClient': '27.0'})
        refused = self.run_hook(script)
        self.assertEqual(refused.returncode, 0, refused.stderr)
        self.assertIn('refusing downgrade', refused.stderr)
        self.assertFalse(self.calls('sudo'))
        self.env.pop('TEST_VERSIONS')
        installed = self.run_hook(script)
        self.assertEqual(installed.returncode, 0, installed.stderr)
        self.assertEqual(len(self.calls('sudo')), 2)
        self.assertEqual(len(self.calls('curl')), 1)
        self.assertEqual(self.calls('op'), [['read', 'op://fixture/zoom-url']])
        self.assertEqual(len(self.calls('hdiutil')), 2)
        self.assertFalse(list((f.root / 'tmp').glob('macos-vdi.*')))
        self.env['TEST_FAIL'] = 'curl'
        skipped = self.run_hook(script)
        self.assertEqual(skipped.returncode, 0, skipped.stderr)
        self.assertIn('Could not download zoom-vdi', skipped.stderr)
        self.assertEqual(len(self.calls('sudo')), 3)
        self.env['TEST_FAIL'] = 'sudo'
        failed = self.run_hook(script)
        self.assertEqual(failed.returncode, 42)

    def test_nfs_confines_install_and_preserves_unrelated_lines(self) -> None:
        f = self.fixture
        config = f.root / 'etc/nfs.conf'
        config.write_text('other = keep\nnfs.client.mount.options = vers=3\n', encoding='utf-8')
        script = self.render(NFS)
        first = self.run_hook(script)
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(config.read_text(), 'other = keep\n# Managed by chezmoi: prefer NFSv4\nnfs.client.mount.options = vers=4\n')
        self.assertEqual(self.run_hook(script).returncode, 0)
        self.assertEqual(len(self.calls('sudo')), 1)
        self.assertFalse(list((f.root / 'tmp').glob('nfs.conf.*')))
        config.unlink()
        config.mkdir()
        rejected = self.run_hook(script)
        self.assertNotEqual(rejected.returncode, 0)
        if sys.platform.startswith('linux'):
            self.assertIn('not a regular file', rejected.stderr)
        self.assertEqual(len(self.calls('sudo')), 1)
        config.rmdir()
        config.write_text('other = keep\n')
        self.env['TEST_FAIL'] = 'sudo'
        failed = self.run_hook(script)
        self.assertEqual(failed.returncode, 42)
        self.assertEqual(config.read_text(), 'other = keep\n')

    def test_vdi_remote_citrix_and_checksum_failure(self) -> None:
        f = self.fixture
        sha = hashlib.sha256(b'fixture package\n').hexdigest()
        def configure(data: dict[str, object]) -> None:
            vdi = data['macos_vdi']
            vdi.update({'enabled': True, 'install': True})
            vdi['citrix'].update({'dmg_url_op_ref': 'op://fixture/citrix-url',
                                  'dmg_sha256': sha})
        script = self.render(VDI, configure)
        installed = self.run_hook(script)
        self.assertEqual(installed.returncode, 0, installed.stderr)
        self.assertEqual(self.calls('op'), [['read', 'op://fixture/citrix-url']])
        self.assertEqual(len(self.calls('sudo')), 1)
        self.assertFalse(list((f.root / 'tmp').glob('macos-vdi.*')))
        wrong_checksum = '0' * 64
        wrong = script.replace(f'CITRIX_DMG_SHA256="{sha}"', f'CITRIX_DMG_SHA256="{wrong_checksum}"')
        self.assertNotEqual(wrong, script)
        rejected = self.run_hook(wrong)
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn('SHA-256 mismatch', rejected.stderr)
        self.assertEqual(len(self.calls('sudo')), 1)
        self.assertFalse(list((f.root / 'tmp').glob('macos-vdi.*')))

    def test_copyq_rate_limit_and_install_stay_in_fixture(self) -> None:
        f = self.fixture
        script = self.render(COPYQ)
        first = self.run_hook(script)
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertTrue((f.root / 'applications/CopyQ.app').is_dir())
        self.assertEqual(self.calls('curl')[0], ['-sL', 'https://api.github.com/repos/hluk/CopyQ/releases/latest'])
        self.assertEqual(len(self.calls('curl')), 2)
        self.assertEqual(len(self.calls('hdiutil')), 2)
        self.assertFalse(list((f.root / 'tmp').glob('copyq.*')))
        self.assertEqual(self.run_hook(script).returncode, 0)
        self.assertEqual(len(self.calls('curl')), 2)
        self.assertEqual(len(self.calls('codesign')), 1)
        self.env['TEST_FAIL'] = 'curl'
        (f.home / '.cache/chezmoi-copyq/last-check').unlink()
        failed = self.run_hook(script)
        self.assertNotEqual(failed.returncode, 0)
        self.assertEqual(len(self.calls('hdiutil')), 2)
        self.assertTrue((f.root / 'applications/CopyQ.app').is_dir())

    def test_launch_agents_retires_only_fixture_targets(self) -> None:
        f = self.fixture
        agents = f.home / 'Library/LaunchAgents'
        agents.mkdir(parents=True)
        for name in ('com.user.vncmonitor', 'com.ssh-add-keychain', 'Environment', 'com.user.nfs-dot-clean',
                     'com.user.ssh-agent-relay'):
            (agents / f'{name}.plist').write_text('fixture\n')
        helper = f.home / 'bin/macos_ssh_agent_relay.sh'
        helper.parent.mkdir()
        helper.write_text('fixture\n')
        unrelated = f.root / 'tmp/unrelated'
        unrelated.write_text('keep\n')
        script = self.render(AGENTS)
        result = self.run_hook(script)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((agents / 'com.user.ssh-agent-relay.plist').exists())
        self.assertFalse(helper.exists())
        self.assertEqual(unrelated.read_text(), 'keep\n')
        self.assertEqual(len(self.calls('launchctl')), 10)
        self.env['TEST_FAIL'] = 'load'
        failed = self.run_hook(script)
        self.assertEqual(failed.returncode, 0, failed.stderr)
        self.assertIn('Failed to load', failed.stdout)

    def test_openusage_only_installs_verified_fixture_artifacts(self) -> None:
        f = self.fixture
        script = self.render(OPENUSAGE)
        first = self.run_hook(script)
        self.assertEqual(first.returncode, 0, first.stderr)
        hook = f.home / '.config/openusage/hooks/claude-hook.sh'
        plugin = f.home / '.config/opencode/plugins/openusage-telemetry.ts'
        for target in (hook, plugin):
            self.assertEqual(target.read_text(), 'openusage-integration-version: 1.2.3\n')
        self.assertEqual(hook.stat().st_mode & 0o777, 0o755)
        self.assertEqual(plugin.stat().st_mode & 0o777, 0o644)
        again = self.run_hook(script)
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertIn('already match', again.stdout)
        self.env['TEST_FAIL'] = 'version'
        rejected = self.run_hook(script)
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn('does not match', rejected.stderr)
        self.assertEqual(hook.read_text(), 'openusage-integration-version: 1.2.3\n')
        self.assertEqual(plugin.read_text(), 'openusage-integration-version: 1.2.3\n')
        self.assertFalse(list((f.root / 'tmp').glob('openusage-integrations.*')))
        self.env['TEST_FAIL'] = 'brew'
        skipped = self.run_hook(script)
        self.assertEqual(skipped.returncode, 0, skipped.stderr)
        self.assertIn('is not installed yet', skipped.stderr)


if __name__ == '__main__':
    unittest.main()
