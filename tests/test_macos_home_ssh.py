from __future__ import annotations

import json
import plistlib
import re
import shutil
import subprocess
import unittest
from collections import Counter
from pathlib import Path

from tests.support.fixtures import isolated_environment, read_json_lines, write_executable


REPO_ROOT = Path(__file__).resolve().parents[1]
HELPER = REPO_ROOT / "configs/macos-home-ssh/home-ssh-toggle.sh"
PLIST = REPO_ROOT / "configs/macos-home-ssh/com.user.home-ssh.plist"
BASH = "/bin/bash" if Path("/bin/bash").exists() else shutil.which("bash")

COMMAND_PATTERN = re.compile(r"(?<![\w/}])/(?:usr/(?:bin|sbin)|s?bin)/[\w.-]+")
FAKED = Counter({"/bin/launchctl": 6, "/bin/sleep": 3, "/sbin/ifconfig": 1,
                 "/usr/bin/id": 1, "/usr/bin/logger": 1, "/usr/bin/pgrep": 2,
                 "/usr/bin/pkill": 2, "/usr/bin/stat": 1, "/usr/sbin/arp": 1,
                 "/usr/sbin/ipconfig": 2, "/usr/sbin/networksetup": 1})
KEPT = Counter({"/bin/bash": 2, "/usr/bin/awk": 5, "/usr/bin/tr": 1})
CONFIG_ANCHOR = 'readonly CONFIG_FILE="/usr/local/etc/dotfiles-home-ssh.conf"'
DROPIN_ANCHOR = 'readonly DROPIN_FILE="/etc/ssh/sshd_config.d/050-dotfiles-home-ssh.conf"'

DOMAIN = "home.example.invalid"
ROUTER_MAC = "02:00:00:00:00:01"
ROUTER = "192.0.2.1"

FAKE = r'''
import json, os, pathlib, sys
root = pathlib.Path(os.environ['FIXTURE_ROOT'])
name = pathlib.Path(sys.argv[0]).name
args = sys.argv[1:]
with (root / 'calls.jsonl').open('a') as handle:
    handle.write(json.dumps({'command': name, 'args': args}) + '\n')
scenario = json.loads((root / 'scenario.json').read_text())
state_path = root / 'state.json'
state = json.loads(state_path.read_text())
def save():
    state_path.write_text(json.dumps(state))
def network():
    samples = scenario['samples']
    return samples[min(state['sample'], len(samples) - 1)]
if name == 'networksetup':
    assert args == ['-listallhardwareports']
    state['sample'] += 1
    save()
    current = network()
    if current.get('networksetup_fails'): sys.exit(1)
    for port, device in current['ports']:
        print('')
        print('Hardware Port: ' + port)
        print('Device: ' + device)
        print('Ethernet Address: 02:00:00:00:00:ff')
    print('')
    print('VLAN Configurations')
    print('===================')
elif name == 'ifconfig':
    assert len(args) == 1
    iface = network()['interfaces'].get(args[0])
    if iface is None: sys.exit(1)
    print(args[0] + ': flags=8863<UP,BROADCAST,RUNNING> mtu 1500')
    if iface.get('inet'): print('\tinet ' + iface['inet'] + ' netmask 0xffffff00')
    print('\tstatus: ' + ('active' if iface.get('active', True) else 'inactive'))
elif name == 'ipconfig':
    assert args[0] == 'getoption' and len(args) == 3
    value = network()['interfaces'].get(args[1], {}).get(args[2])
    if value is None: sys.exit(1)
    print(value)
elif name == 'arp':
    assert args[0] == '-n' and len(args) == 2
    for mac, device in network().get('arp', {}).get(args[1], []):
        print('? (%s) at %s on %s ifscope [ethernet]' % (args[1], mac, device))
elif name == 'launchctl':
    label = 'system/com.openssh.sshd'
    if args == ['print-disabled', 'system']:
        print('disabled services = {')
        if state['override']: print('\t"com.openssh.sshd" => ' + state['override'])
        print('}')
    elif args == ['print', label]:
        sys.exit(0 if state['loaded'] else 113)
    elif args == ['enable', label]:
        state['override'] = 'enabled'
    elif args == ['disable', label]:
        state['override'] = 'disabled'
    elif args[:2] == ['bootstrap', 'system'] and len(args) == 3:
        assert args[2] == '/System/Library/LaunchDaemons/ssh.plist'
        if os.environ.get('TEST_FAIL') == 'bootstrap': sys.exit(5)
        if os.environ.get('TEST_FAIL') != 'bootstrap-silent': state['loaded'] = True
    elif args == ['bootout', label]:
        state['loaded'] = False
    else:
        sys.exit(97)
    save()
elif name == 'pgrep':
    assert args[0] == '-x' and len(args) == 2
    sys.exit(0 if args[1] in state['processes'] else 1)
elif name == 'pkill':
    assert args[0] in ('-TERM', '-KILL') and args[1] == '-x' and len(args) == 3
    if args[2] in state['processes']:
        if args[0] == '-KILL' or args[2] not in state.get('stubborn', []):
            state['processes'].remove(args[2])
    save()
elif name == 'stat':
    assert args[:2] == ['-f', '%u %g %Lp'] and len(args) == 3
    print(os.environ.get('TEST_STAT', '0 0 644'))
elif name == 'id':
    assert args == ['-u']
    print(os.environ.get('TEST_UID', '0'))
elif name == 'sleep':
    if os.environ.get('TEST_FAIL') == 'term':
        import signal
        os.kill(os.getppid(), signal.SIGTERM)
elif name == 'logger':
    pass
else:
    sys.exit(98)
'''


def network(*, ethernet=True, wifi=None, domain=DOMAIN, mac=ROUTER_MAC,
            arp_device="en7", extra_ports=(), extra_interfaces=None):
    ports = [["Ethernet Adapter (en4)", "en4"], ["Belkin USB-C LAN", "en7"],
             ["Thunderbolt Bridge", "bridge0"], ["Wi-Fi", "en0"], ["Thunderbolt 1", "en1"],
             *extra_ports]
    interfaces = {"en4": {"active": False}, "bridge0": {"active": True},
                  "en1": {"active": True}, "en0": {"active": False}}
    if ethernet:
        interfaces["en7"] = {"active": True, "inet": "192.0.2.10", "domain_name": domain,
                             "router": ROUTER}
    else:
        interfaces["en7"] = {"active": False}
    if wifi is not None:
        interfaces["en0"] = {"active": True, "inet": "192.0.2.11", "domain_name": wifi,
                             "router": ROUTER}
    interfaces.update(extra_interfaces or {})
    arp = {ROUTER: [[mac, arp_device]]} if mac else {}
    return {"ports": ports, "interfaces": interfaces, "arp": arp}


def confine(script: str, root: Path, fake_bin: Path) -> str:
    observed = Counter(COMMAND_PATTERN.findall(script))
    if observed != FAKED + KEPT:
        raise AssertionError(f"helper absolute command drift: {observed - (FAKED + KEPT)}, "
                             f"{(FAKED + KEPT) - observed}")
    if script.count(CONFIG_ANCHOR) != 1 or script.count(DROPIN_ANCHOR) != 1:
        raise AssertionError("helper config path drift")
    script = script.replace(CONFIG_ANCHOR, f'readonly CONFIG_FILE="{root}/home-ssh.conf"')
    script = script.replace(DROPIN_ANCHOR, f'readonly DROPIN_FILE="{root}/dropin.conf"')
    for command in FAKED:
        pattern = re.compile(r"(?<![\w/}])" + re.escape(command) + r"(?![\w.-])")
        script = pattern.sub(str(fake_bin / Path(command).name), script)
    remaining = Counter(COMMAND_PATTERN.findall(script.replace(str(fake_bin), "")))
    if remaining != KEPT or "/usr/local/" in script or "/etc/ssh/" in script:
        raise AssertionError(f"helper still references host paths: {remaining - KEPT}")
    return script


@unittest.skipUnless(BASH, "bash is required")
class HomeSshHelperTests(unittest.TestCase):
    def setUp(self) -> None:
        context = isolated_environment(prefix="macos-home-ssh-")
        self.fixture = context.__enter__()
        self.addCleanup(context.__exit__, None, None, None)
        f = self.fixture
        for name in ("networksetup", "ifconfig", "ipconfig", "arp", "launchctl", "pgrep",
                     "pkill", "stat", "id", "logger", "sleep"):
            write_executable(f.fake_bin / name, "#!/usr/bin/env python3\n" + FAKE)
        self.script = f.root / "helper.sh"
        self.script.write_text(confine(HELPER.read_text(encoding="utf-8"), f.root, f.fake_bin),
                               encoding="utf-8")
        self.write_config(f"HOME_DHCP_DOMAIN={DOMAIN}\nHOME_ROUTER_MAC={ROUTER_MAC}\n")
        (f.root / "dropin.conf").write_text("PasswordAuthentication no\n", encoding="utf-8")
        self.env = {"PATH": "/usr/bin:/bin", "LC_ALL": "C", "FIXTURE_ROOT": str(f.root)}

    def write_config(self, text: str) -> None:
        (self.fixture.root / "home-ssh.conf").write_text(text, encoding="utf-8")

    def run_helper(self, *samples, loaded=False, override="disabled", processes=(),
                   stubborn=()) -> subprocess.CompletedProcess[str]:
        root = self.fixture.root
        (root / "calls.jsonl").unlink(missing_ok=True)
        (root / "scenario.json").write_text(json.dumps({"samples": list(samples)}))
        (root / "state.json").write_text(json.dumps({
            "sample": -1, "loaded": loaded, "override": override,
            "processes": list(processes), "stubborn": list(stubborn)}))
        return subprocess.run([BASH, str(self.script)], cwd=root, env=self.env, text=True,
                              capture_output=True, check=False, timeout=60)

    def state(self) -> dict[str, object]:
        return json.loads((self.fixture.root / "state.json").read_text())

    def calls(self, name: str) -> list[list[str]]:
        return [entry["args"] for entry in read_json_lines(self.fixture.root / "calls.jsonl")
                if entry["command"] == name]

    def mutations(self) -> list[list[str]]:
        return [args for args in self.calls("launchctl")
                if args[0] in ("enable", "disable", "bootstrap", "bootout")]

    def assert_on(self, *samples) -> None:
        result = self.run_helper(*samples)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.state()["override"], "enabled")
        self.assertTrue(self.state()["loaded"])
        self.assertFalse(self.calls("pkill"))

    def assert_off(self, *samples) -> None:
        result = self.run_helper(*samples, loaded=True, override="enabled")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.state()["override"], "disabled")
        self.assertFalse(self.state()["loaded"])
        self.assertNotIn(["bootstrap", "system", "/System/Library/LaunchDaemons/ssh.plist"],
                         self.calls("launchctl"))

    def test_home_ethernet_turns_remote_login_on(self) -> None:
        cases = {
            "wifi down": network(),
            "wifi on home": network(wifi=DOMAIN),
            "case and trailing dot": network(domain="Home.Example.Invalid."),
            "leading zeros dropped": network(mac="2:0:0:0:0:1"),
            "wifi router not in arp": network(wifi=DOMAIN, arp_device="en7"),
        }
        for label, scenario in cases.items():
            with self.subTest(label):
                self.assert_on(scenario)
        self.assertEqual(self.mutations(), [["enable", "system/com.openssh.sshd"],
                                            ["bootstrap", "system",
                                             "/System/Library/LaunchDaemons/ssh.plist"]])

    def test_foreign_or_missing_home_signal_turns_remote_login_off(self) -> None:
        cases = {
            "wifi foreign": network(wifi="coffee.example.invalid"),
            "domain mismatch": network(domain="other.example.invalid"),
            "router mac mismatch": network(mac="02:00:00:00:00:02"),
            "router not in arp": network(mac=""),
            "arp only on wifi": network(arp_device="en0"),
            "no ethernet": network(ethernet=False),
            "home wifi only": network(ethernet=False, wifi=DOMAIN),
            "link-local only": network(extra_interfaces={
                "en7": {"active": True, "inet": "169.254.4.2", "domain_name": DOMAIN,
                        "router": ROUTER}}),
            "home dhcp on bridge only": network(ethernet=False, extra_interfaces={
                "bridge0": {"active": True, "inet": "192.0.2.12", "domain_name": DOMAIN,
                            "router": ROUTER},
                "en1": {"active": True, "inet": "192.0.2.13", "domain_name": DOMAIN,
                        "router": ROUTER}}),
            "second ethernet foreign": network(
                extra_ports=[["USB 10/100/1000 LAN", "en8"]],
                extra_interfaces={"en8": {"active": True, "inet": "198.51.100.2",
                                          "domain_name": "hotel.example.invalid",
                                          "router": "198.51.100.1"}}),
            "discovery fails": {**network(), "networksetup_fails": True},
        }
        for label, scenario in cases.items():
            with self.subTest(label):
                self.assert_off(scenario)

    def test_samples_must_agree(self) -> None:
        self.assert_off(network(), network(wifi="coffee.example.invalid"))
        self.assert_on(network(), network())
        self.assertEqual(len(self.calls("networksetup")), 2)
        self.assertEqual(self.calls("sleep"), [["5"], ["3"]])

    def test_untrusted_config_fails_closed(self) -> None:
        configs = {
            "malformed domain": "HOME_DHCP_DOMAIN=bad domain\nHOME_ROUTER_MAC=\n",
            "malformed mac": f"HOME_DHCP_DOMAIN={DOMAIN}\nHOME_ROUTER_MAC=not-a-mac\n",
            "empty domain": "HOME_ROUTER_MAC=\n",
            "command substitution": "HOME_DHCP_DOMAIN=$(touch pwned)\n",
        }
        for label, text in configs.items():
            with self.subTest(label):
                self.write_config(text)
                self.assert_off(network())
        self.assertFalse((self.fixture.root / "pwned").exists())
        self.write_config(f"HOME_DHCP_DOMAIN={DOMAIN}\nHOME_ROUTER_MAC=\n")
        self.assert_on(network(mac="02:00:00:00:00:09"))
        for label, stat in {"user owned": "501 0 644", "group writable": "0 0 664",
                            "world writable": "0 0 646"}.items():
            with self.subTest(label):
                self.env["TEST_STAT"] = stat
                self.assert_off(network())
        self.env.pop("TEST_STAT")
        (self.fixture.root / "home-ssh.conf").unlink()
        self.assert_off(network())

    def test_missing_hardening_dropin_keeps_remote_login_off(self) -> None:
        dropin = self.fixture.root / "dropin.conf"
        dropin.unlink()
        self.assert_off(network())
        dropin.symlink_to(self.fixture.root / "home-ssh.conf")
        self.assert_off(network())

    def test_launchd_stop_leaves_state_untouched(self) -> None:
        self.env["TEST_FAIL"] = "term"
        result = self.run_helper(network(ethernet=False), loaded=True, override="enabled",
                                 processes=("sshd-session",))
        self.assertEqual(result.returncode, 143, result.stderr)
        self.assertFalse(self.mutations())
        self.assertFalse(self.calls("pkill"))
        self.assertTrue(self.state()["loaded"])

    def test_requires_root(self) -> None:
        self.env["TEST_UID"] = "501"
        result = self.run_helper(network(), loaded=True, override="enabled")
        self.assertEqual(result.returncode, 1)
        self.assertIn("must run as root", result.stderr)
        self.assertFalse(self.calls("launchctl"))
        self.assertTrue(self.state()["loaded"])

    def test_converged_state_is_not_mutated(self) -> None:
        on = self.run_helper(network(), loaded=True, override="enabled")
        self.assertEqual(on.returncode, 0, on.stderr)
        self.assertFalse(self.mutations())
        self.assertFalse(self.calls("logger"))
        off = self.run_helper(network(ethernet=False))
        self.assertEqual(off.returncode, 0, off.stderr)
        self.assertFalse(self.mutations())
        self.assertFalse(self.calls("pkill"))
        self.assertFalse(self.calls("logger"))

    def test_off_terminates_lingering_sessions(self) -> None:
        result = self.run_helper(network(ethernet=False),
                                 processes=("sshd-session", "sshd-auth", "sshd"),
                                 stubborn=("sshd-session",))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.state()["processes"], [])
        self.assertEqual(self.calls("pkill"), [["-TERM", "-x", "sshd"],
                                               ["-TERM", "-x", "sshd-session"],
                                               ["-TERM", "-x", "sshd-auth"],
                                               ["-KILL", "-x", "sshd-session"]])
        self.assertFalse(self.mutations())

    def test_failed_enable_falls_back_to_off(self) -> None:
        for failure in ("bootstrap", "bootstrap-silent"):
            with self.subTest(failure):
                self.env["TEST_FAIL"] = failure
                result = self.run_helper(network(), processes=("sshd-session",))
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(self.state()["override"], "disabled")
                self.assertFalse(self.state()["loaded"])
                self.assertEqual(self.state()["processes"], [])

    def test_logs_never_contain_network_identity(self) -> None:
        self.assert_on(network())
        self.assert_off(network(wifi="coffee.example.invalid"))
        logged = json.dumps(self.calls("logger"))
        self.assertIn("dotfiles-home-ssh", logged)
        for secret in (DOMAIN, ROUTER_MAC, ROUTER, "coffee.example.invalid"):
            self.assertNotIn(secret, logged)


class HomeSshStaticContractTests(unittest.TestCase):
    def test_launch_daemon_contract(self) -> None:
        payload = plistlib.loads(PLIST.read_bytes())
        self.assertEqual(payload["Label"], "com.user.home-ssh")
        self.assertEqual(payload["ProgramArguments"],
                         ["/bin/bash", "/usr/local/libexec/dotfiles-home-ssh"])
        self.assertIs(payload["RunAtLoad"], True)
        self.assertEqual(payload["StartInterval"], 300)
        event = "com.apple.system.config.network_change"
        self.assertEqual(payload["LaunchEvents"],
                         {"com.apple.notifyd.matching": {event: {"Notification": event}}})
        for key in ("KeepAlive", "UserName", "GroupName"):
            self.assertNotIn(key, payload)

    def test_helper_stays_bash_32_compatible(self) -> None:
        source = HELPER.read_text(encoding="utf-8")
        self.assertTrue(source.startswith("#!/bin/bash\n"))
        for construct in (r"declare -A", r"\bmapfile\b", r"\breadarray\b", r"\$\{\w+,,",
                          r"\$\{\w+\^\^", r"\|&", r"\bsource\b", r"\beval\b"):
            self.assertIsNone(re.search(construct, source), construct)


if __name__ == "__main__":
    unittest.main()
