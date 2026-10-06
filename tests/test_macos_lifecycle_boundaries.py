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
from tests.test_chezmoi_lifecycle_render import CHEZMOI, REPO_ROOT, render_template


VDI = ".chezmoiscripts/run_onchange_after_macos-vdi-apps.sh.tmpl"
NFS = ".chezmoiscripts/run_after_macos-nfs-config.sh.tmpl"
COPYQ = ".chezmoiscripts/run_after_update_copyq.sh.tmpl"
AGENTS = ".chezmoiscripts/run_onchange_after_reload_launch_agents.sh.tmpl"
OPENUSAGE = ".chezmoiscripts/run_after_macos-openusage-integrations.sh.tmpl"
HOME_SSH = ".chezmoiscripts/run_after_macos-home-ssh.sh.tmpl"
HOME_SSH_SOURCES = ("configs/macos-home-ssh/home-ssh-toggle.sh",
                    "configs/macos-home-ssh/com.user.home-ssh.plist")

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
    HOME_SSH: Counter({"/usr/bin/env": 1, "/bin/rm": 4, "/usr/bin/printf": 11,
                       "/usr/bin/mktemp": 1, "/usr/bin/cmp": 2, "/usr/bin/sudo": 15,
                       "/usr/bin/install": 2, "/usr/bin/stat": 1, "/bin/launchctl": 8,
                       "/usr/sbin/sshd": 2, "/usr/bin/pkill": 1}),
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
    elif args[0].endswith('/install') and args[1] == '-d':
        assert args[2:8] == ['-o', 'root', '-g', 'wheel', '-m', '0755'] and len(args) == 9
        owned(args[8]).mkdir()
    elif args[0].endswith('/install'):
        assert args[1:6] == ['-o', 'root', '-g', 'wheel', '-m'] and args[6] in ('0644', '0755')
        shutil.copyfile(owned(args[7]), owned(args[8]))
        owned(args[8]).chmod(int(args[6], 8))
    else:
        os.execv(owned(args[0]), args)
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
elif name == 'launchctl' and (args[-1].startswith('system/') or args[:2] == ['bootstrap', 'system']):
    state_path = root / 'launchd.json'
    loaded = set(json.loads(state_path.read_text())) if state_path.exists() else set()
    label = args[-1].removeprefix('system/')
    if args[0] == 'print':
        sys.exit(0 if label in loaded else 113)
    elif args[0] == 'bootstrap':
        if os.environ.get('TEST_FAIL') == 'bootstrap': sys.exit(5)
        loaded.add(pathlib.Path(owned(args[2])).stem)
    elif args[0] == 'bootout':
        if label not in loaded: sys.exit(3)
        loaded.discard(label)
    elif args[0] == 'kickstart':
        assert args[1] == '-k' and label in loaded
    else:
        assert args[0] in ('enable', 'disable') and label == 'com.openssh.sshd'
    state_path.write_text(json.dumps(sorted(loaded)))
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
elif name == 'sshd':
    assert args[:1] == ['-t'] and len(args) in (1, 3)
    if len(args) == 3:
        assert args[1] == '-f' and owned(args[2]).read_text().endswith('Include /etc/ssh/sshd_config\n')
    if os.environ.get('TEST_FAIL') == ('sshd-candidate' if len(args) == 3 else 'sshd-live'): sys.exit(255)
elif name == 'pkill':
    assert args[0] == '-x' and args[1] in ('sshd', 'sshd-session', 'sshd-auth') and len(args) == 2
    sys.exit(1)
elif name == 'stat':
    assert args[:2] == ['-f', '%u %g %Lp'] and len(args) == 3
    owned(args[2])
    print(os.environ.get('TEST_STAT', '0 0 755'))
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
        HOME_SSH: {f'"{REPO_ROOT}/{HOME_SSH_SOURCES[0]}"': (1, f'"{root}/source/{HOME_SSH_SOURCES[0]}"'),
                   f'"{REPO_ROOT}/{HOME_SSH_SOURCES[1]}"': (1, f'"{root}/source/{HOME_SSH_SOURCES[1]}"'),
                   'LOCAL_ROOT="/usr/local"': (1, f'LOCAL_ROOT="{root}/usr-local"'),
                   'HELPER_PATH="/usr/local/libexec/': (1, f'HELPER_PATH="{root}/usr-local/libexec/'),
                   'CONFIG_PATH="/usr/local/etc/': (1, f'CONFIG_PATH="{root}/usr-local/etc/'),
                   'DROPIN_PATH="/etc/ssh/': (1, f'DROPIN_PATH="{root}/etc/ssh/'),
                   'PLIST_PATH="/Library/LaunchDaemons/': (1, f'PLIST_PATH="{root}/LaunchDaemons/'),
                   "/usr/bin/sudo": (15, root / 'bin/sudo'),
                   "/usr/bin/install": (2, root / 'bin/install'),
                   "/usr/bin/stat": (1, root / 'bin/stat'),
                   "/bin/launchctl": (8, root / 'bin/launchctl'),
                   "/usr/sbin/sshd": (2, root / 'bin/sshd'),
                   "/usr/bin/pkill": (1, root / 'bin/pkill'),
                   "/bin/rm": (4, root / 'bin/rm')},
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
                   '/usr/bin/hdiutil', '/usr/sbin/installer', '/usr/sbin/pkgutil',
                   '/usr/local/', '/etc/ssh/sshd_config.d', '/Library/LaunchDaemons',
                   '/bin/launchctl', '/usr/sbin/sshd', '/usr/bin/pkill', '/usr/bin/stat',
                   str(REPO_ROOT)):
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
                     'launchctl', 'id', 'rm', 'cp', 'xattr', 'codesign', 'sshd', 'pkill', 'stat'):
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

    def home_ssh_script(self, **values: object) -> str:
        f = self.fixture
        for relative in HOME_SSH_SOURCES:
            target = f.root / 'source' / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(REPO_ROOT / relative, target)
        for directory in ('etc/ssh/sshd_config.d', 'LaunchDaemons', 'usr-local'):
            (f.root / directory).mkdir(parents=True, exist_ok=True)
        def configure(data: dict[str, object]) -> None:
            data['macos_home_ssh'] = {'enabled': True, 'dhcp_domain': 'Home.Example.Invalid',
                                      'router_mac': '02:00:00:00:00:01', **values}
        return self.render(HOME_SSH, configure)

    def privileged_steps(self) -> list[tuple[str, ...]]:
        steps = []
        for args in self.calls('sudo'):
            name = Path(args[0]).name
            if name == 'install':
                steps.append(('install-d' if args[1] == '-d' else 'install', Path(args[-1]).name))
            elif name == 'rm':
                steps.append(('rm', *[Path(a).name for a in args[1:] if not a.startswith('-')]))
            else:
                steps.append((name, *args[1:2]))
        return steps

    def test_home_ssh_installs_hardening_first_and_converges(self) -> None:
        f = self.fixture
        script = self.home_ssh_script()
        self.assertIn('readonly SSH_USER="fixture-user"', script)
        first = self.run_hook(script)
        self.assertEqual(first.returncode, 0, first.stderr)
        dropin = f.root / 'etc/ssh/sshd_config.d/050-dotfiles-home-ssh.conf'
        self.assertEqual(dropin.read_text(), '# Managed by chezmoi: macos_home_ssh\n'
                         'PasswordAuthentication no\nKbdInteractiveAuthentication no\n'
                         'AuthenticationMethods publickey\nPermitRootLogin no\n'
                         'AllowUsers fixture-user\n')
        config = f.root / 'usr-local/etc/dotfiles-home-ssh.conf'
        self.assertEqual(config.read_text(), 'HOME_DHCP_DOMAIN=home.example.invalid\n'
                         'HOME_ROUTER_MAC=02:00:00:00:00:01\n')
        helper = f.root / 'usr-local/libexec/dotfiles-home-ssh'
        plist = f.root / 'LaunchDaemons/com.user.home-ssh.plist'
        self.assertEqual(helper.read_bytes(), (REPO_ROOT / HOME_SSH_SOURCES[0]).read_bytes())
        self.assertEqual(plist.read_bytes(), (REPO_ROOT / HOME_SSH_SOURCES[1]).read_bytes())
        self.assertEqual(helper.stat().st_mode & 0o777, 0o755)
        self.assertEqual(plist.stat().st_mode & 0o777, 0o644)
        self.assertEqual(self.privileged_steps(), [
            ('sshd', '-t'), ('install-d', 'libexec'), ('install-d', 'etc'),
            ('install', '050-dotfiles-home-ssh.conf'), ('sshd', '-t'),
            ('install', 'dotfiles-home-ssh.conf'), ('install', 'dotfiles-home-ssh'),
            ('install', 'com.user.home-ssh.plist'), ('launchctl', 'bootout'),
            ('launchctl', 'bootstrap')])
        self.assertEqual(self.calls('sshd')[0][:2], ['-t', '-f'])
        self.assertEqual(json.loads((f.root / 'launchd.json').read_text()), ['com.user.home-ssh'])
        self.assertFalse(list((f.root / 'tmp').glob('macos-home-ssh.*')))
        again = self.run_hook(script)
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertIn('already configured', again.stdout)
        self.assertEqual(len(self.calls('sudo')), 10)
        helper.write_text('drifted\n')
        repaired = self.run_hook(script)
        self.assertEqual(repaired.returncode, 0, repaired.stderr)
        self.assertEqual(self.privileged_steps()[10:], [('install', 'dotfiles-home-ssh'),
                                                       ('launchctl', 'kickstart')])
        self.assertEqual(helper.read_bytes(), (REPO_ROOT / HOME_SSH_SOURCES[0]).read_bytes())
        dropin.write_text('drifted\n')
        restored = self.run_hook(script)
        self.assertEqual(restored.returncode, 0, restored.stderr)
        self.assertEqual(self.privileged_steps()[12:], [('sshd', '-t'),
                                                       ('install', '050-dotfiles-home-ssh.conf'),
                                                       ('sshd', '-t')])
        self.env['TEST_FAIL'] = 'sshd-live'
        dropin.write_text('drifted\n')
        rolled_back = self.run_hook(script)
        self.assertNotEqual(rolled_back.returncode, 0)
        self.assertIn('unloaded com.user.home-ssh', rolled_back.stderr)
        self.assertFalse(dropin.exists())
        self.assertEqual(json.loads((f.root / 'launchd.json').read_text()), [])
        self.assertIn(['disable', 'system/com.openssh.sshd'], self.calls('launchctl'))
        self.env.pop('TEST_FAIL')
        recovered = self.run_hook(script)
        self.assertEqual(recovered.returncode, 0, recovered.stderr)
        self.assertTrue(dropin.exists())
        self.assertEqual(json.loads((f.root / 'launchd.json').read_text()), ['com.user.home-ssh'])

    def test_home_ssh_rejects_unsafe_inputs_before_installing(self) -> None:
        f = self.fixture
        for label, values, message in (
                ('domain', {'dhcp_domain': 'bad domain'}, 'dhcp_domain'),
                ('mac', {'router_mac': 'not-a-mac'}, 'router_mac')):
            with self.subTest(label):
                rejected = self.run_hook(self.home_ssh_script(**values))
                self.assertNotEqual(rejected.returncode, 0)
                self.assertIn(message, rejected.stderr)
        script = self.home_ssh_script()
        dropin = f.root / 'etc/ssh/sshd_config.d/050-dotfiles-home-ssh.conf'
        dropin.symlink_to(f.root / 'tmp')
        rejected = self.run_hook(script)
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn('not a regular file', rejected.stderr)
        dropin.unlink()
        self.env['TEST_STAT'] = '501 20 755'
        rejected = self.run_hook(script)
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn('owned by root', rejected.stderr)
        self.env.pop('TEST_STAT')
        self.assertFalse(self.calls('sudo'))
        self.env['TEST_FAIL'] = 'sshd-candidate'
        rejected = self.run_hook(script)
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn('nothing was installed', rejected.stderr)
        self.assertEqual(self.privileged_steps(), [('sshd', '-t')])
        self.env['TEST_FAIL'] = 'sudo'
        rejected = self.run_hook(script)
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn('nothing was installed', rejected.stderr)
        self.assertFalse(dropin.exists())
        self.env['TEST_FAIL'] = 'sshd-live'
        rejected = self.run_hook(script)
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn('turned Remote Login off', rejected.stderr)
        self.assertFalse(dropin.exists())
        self.assertFalse((f.root / 'LaunchDaemons/com.user.home-ssh.plist').exists())
        self.assertIn(['disable', 'system/com.openssh.sshd'], self.calls('launchctl'))
        self.assertFalse(list((f.root / 'tmp').glob('macos-home-ssh.*')))

    def test_home_ssh_disabled_removes_only_installed_state(self) -> None:
        f = self.fixture
        enabled = self.home_ssh_script()
        disabled = self.home_ssh_script(enabled=False)
        untouched = self.run_hook(disabled)
        self.assertEqual(untouched.returncode, 0, untouched.stderr)
        self.assertIn('is disabled', untouched.stdout)
        self.assertFalse(self.calls('sudo'))
        self.assertEqual(self.run_hook(enabled).returncode, 0)
        installed = len(self.calls('sudo'))
        removed = self.run_hook(disabled)
        self.assertEqual(removed.returncode, 0, removed.stderr)
        self.assertEqual(self.privileged_steps()[installed:], [
            ('launchctl', 'bootout'), ('rm', 'com.user.home-ssh.plist'),
            ('launchctl', 'bootout'), ('launchctl', 'disable'),
            ('pkill', '-x'), ('pkill', '-x'), ('pkill', '-x'),
            ('rm', 'dotfiles-home-ssh', 'dotfiles-home-ssh.conf', '050-dotfiles-home-ssh.conf')])
        self.assertEqual(json.loads((f.root / 'launchd.json').read_text()), [])
        for target in ('usr-local/libexec/dotfiles-home-ssh', 'usr-local/etc/dotfiles-home-ssh.conf',
                       'etc/ssh/sshd_config.d/050-dotfiles-home-ssh.conf',
                       'LaunchDaemons/com.user.home-ssh.plist'):
            self.assertFalse((f.root / target).exists(), target)
        default = self.render(HOME_SSH)
        self.assertIn('readonly ENABLED=false', default)
        self.assertIn('readonly HOME_DHCP_DOMAIN=""', default)


if __name__ == '__main__':
    unittest.main()
