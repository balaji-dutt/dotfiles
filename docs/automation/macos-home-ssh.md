# macOS Home SSH Toggle

An opt-in root LaunchDaemon turns macOS Remote Login (sshd) on while the Mac is
plugged into the home wired network and off everywhere else. It exists so other
machines on the home network can SSH in with Full Disk Access without leaving
sshd reachable on other networks.

## Decision Rule

The helper turns Remote Login on only when both conditions hold:

- An Ethernet port (a hardware port whose name contains `Ethernet` or `LAN`, on
  an `enN` device) has an IPv4 address whose DHCP `domain_name` matches
  `macos_home_ssh.dhcp_domain`. When `macos_home_ssh.router_mac` is set, the
  router's ARP entry on that port must also match it.
- Every other Ethernet or Wi-Fi interface with an IPv4 address reports the same
  DHCP domain. Wi-Fi is compared by domain only.

Wi-Fi that is off or unaddressed does not block the decision. The Wi-Fi network
name is not used because macOS redacts SSIDs from command-line tools.
Thunderbolt Bridge, Thunderbolt ports, and VPN tunnels are ignored.

Everything else turns Remote Login off: a foreign network, missing or untrusted
configuration, a missing sshd hardening drop-in, a failed command, or two
samples taken three seconds apart that disagree. While off, the helper also ends any remaining `sshd`, `sshd-session`,
and `sshd-auth` processes.

The DHCP domain and router MAC can be copied by anyone who controls a network,
so they prevent accidental exposure. They do not make a hostile network safe.
Router defaults such as `lan`, `home`, or `localdomain` are common on other
people's networks; with one of those, set `router_mac` as well.

## Installed Files

`.chezmoiscripts/run_after_macos-home-ssh.sh.tmpl` runs on every apply and
installs these root-owned files:

| Path | Source |
| :--- | :--- |
| `/etc/ssh/sshd_config.d/050-dotfiles-home-ssh.conf` | rendered by the hook |
| `/usr/local/etc/dotfiles-home-ssh.conf` | rendered by the hook from local data |
| `/usr/local/libexec/dotfiles-home-ssh` | `configs/macos-home-ssh/home-ssh-toggle.sh` |
| `/Library/LaunchDaemons/com.user.home-ssh.plist` | `configs/macos-home-ssh/com.user.home-ssh.plist` |

The sshd drop-in enforces key-only login (`AuthenticationMethods publickey`),
disables root login, and limits logins to the chezmoi username with
`AllowUsers`. It must sort before Apple's `100-macos.conf` because sshd keeps
the first value it reads. The hook validates the drop-in with `sshd -t` before
installing it. `sshd -t` also fails when the host keys under `/etc/ssh` do not
exist yet; turning Remote Login on once in System Settings generates them. If
the installed configuration still fails validation, the hook unloads the
daemon, removes the drop-in, and turns Remote Login off.

The hook calls sudo only when a file differs or the daemon is not loaded.
It refuses to install when `/usr/local`, `/usr/local/libexec`, or
`/usr/local/etc` is not owned by `root:wheel` or is writable by group or other.
Intel Homebrew installs in `/usr/local` and owns those directories as the user,
so the feature does not install on such Macs.

The LaunchDaemon runs the helper at load, on every
`com.apple.system.config.network_change` notification, and every 300 seconds.
Decisions and actions are logged under the `dotfiles-home-ssh` tag without the
domain or MAC:

```sh
log show --last 1h --predicate 'eventMessage CONTAINS "dotfiles-home-ssh"' --info
```

## Setup

1. Find the home values while plugged in at home:

   ```sh
   ipconfig getoption en7 domain_name
   arp -n "$(ipconfig getoption en7 router)"
   ```

   Replace `en7` with the Ethernet device from
   `networksetup -listallhardwareports`.
2. Run `chezmoi init` and answer the `macos_home_ssh` prompts. The values are
   stored only in the local chezmoi config, never in this repository.
3. Run `chezmoi apply`. The hook asks for the administrator password when it
   installs or changes files.
4. Enable Full Disk Access for SSH once in System Settings > General > Sharing >
   Remote Login (i) > "Allow full disk access for remote users". This is a TCC
   grant to `/usr/libexec/sshd-keygen-wrapper`, which SIP prevents scripts from
   writing. The grant stays in place while the daemon turns sshd off and on.

Clients must authenticate with a key. A client whose SSH agent offers more than
six keys is disconnected with "Too many authentication failures" before it
reaches the right key, so set `IdentitiesOnly yes` and an `IdentityFile` for
this host in the client's `~/.ssh/config`.

## Caveats

- Turning Remote Login on or off in System Settings lasts only until the next
  network change or 300-second run.
- When launchd stops or restarts the helper mid-run, it exits without changing
  Remote Login, so `chezmoi apply` over SSH does not end its own session.
- A Wi-Fi reconnect at home can briefly show an IPv4 address before its DHCP
  options arrive. That reads as foreign, so the helper turns sshd off and ends
  open sessions; the next run turns it back on.
- If the router's ARP entry expires on an idle Ethernet link, the MAC check
  fails and sshd turns off until traffic refreshes it. Leave `router_mac` empty
  if that happens often.

## Disable

Set `macos_home_ssh.enabled = false` in the local chezmoi config and run
`chezmoi apply`. The hook unloads and removes the daemon, turns Remote Login off,
ends SSH sessions, and removes the helper, its configuration, and the sshd
drop-in. Machines that never enabled the feature are not touched.
