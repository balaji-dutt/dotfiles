<!-- markdownlint-disable MD007 MD013 MD023 MD031 MD032 MD034 MD040 MD041 MD051 -->
<!-- markdownlint-configure-file
{
  "no-trailing-spaces": false,
  "no-hard-tabs": true
}
-->

# WSL2 Desktop Integration

How GUI apps, browser launching, and the clipboard work on Debian and Ubuntu
WSL2, and what to check when they stop working. Provisioning lives in
`ansible/tasks/wsl-desktop.yml`, driven by `wsl_desktop_apt_packages` in
`configs/packages.yaml`.

## Browser launching

Command-line tools open URLs through `xdg-open`, which resolves `$BROWSER`.
`$BROWSER` points at `~/bin/wsl-open` (source: `bin/executable_wsl-open`),
which hands the URL to the Windows default browser over interop:

```sh
rundll32.exe url.dll,FileProtocolHandler <url>
```

`rundll32` rather than `explorer.exe` because `explorer.exe` exits non-zero
even when it succeeds, and callers such as Claude Code treat a non-zero exit as
a failed launch. Override the handler with `WSL_OPEN_HANDLER` for testing.

The default handler path assumes the stock `automount.root = /mnt/` and Windows
on `C:`. A host that remaps either gets exit 127 and a bare `No such file` from
bash; point `WSL_OPEN_HANDLER` at the real `rundll32.exe` path in that case.

Known consumers of `$BROWSER` / `xdg-open`:

- Claude Code — spawns `xdg-open <url>` during `claude auth login`
- glab — `browser` key written by `private_dot_config/glab-cli/modify_private_config.yml`
- gh — `browser` left empty in `private_dot_config/gh/private_config.yml`, so it
  falls through to `$BROWSER`

### Why not wslu / wslview

`wslview` came from the `wslu` package. Upstream archived the project in March
2025, and Debian 13 trixie carries no candidate for it, so Ubuntu had working
browser integration and Debian had none. `wsl-open` replaces it on both
distros; `ansible/tasks/ubuntu-extras.yml` removes `wslu` where it is still
installed. The removal lives there, behind the existing `is_ubuntu` gate,
because `apt` cannot resolve the name at all on Debian.

Removing `wslu` also drops its `x-www-browser` alternative, so `$BROWSER` is
now the only route from `xdg-open` to a browser. If a URL fails to open, check
that `$BROWSER` is exported before assuming `wsl-open` is broken:

```sh
echo "$BROWSER"
~/bin/wsl-open "https://example.com"; echo "rc=$?"
```

`wslu` also provided `wslvar`, `wslupath`, `wslsys`, `wslfetch`, `wslact`, and
`wslusc`. Nothing in this repo uses them.

## Clipboard

`wl-clipboard` and `xclip` are both installed. Callers pick `wl-copy` when
`WAYLAND_DISPLAY` is set and `xclip` under X11, and WSLg exposes both, so
installing only one leaves half the callers broken. Claude Code's copy-to-
clipboard prompt during `claude auth login` disappears entirely when neither is
present.

## Fonts

Debian's WSL image ships only DejaVu. `fonts-noto-core` and `fonts-liberation`
bring it closer to Ubuntu's default coverage. Check with `fc-list | wc -l`.

## Troubleshooting: WSLg copy mode

Symptom: a GUI app launches but paints a blank window, and its taskbar title is
prefixed `[WARN:COPY MODE]`.

That prefix comes from WSLg, not the application. It means Weston failed to
allocate shared memory at startup and fell back to copying pixels over RDP.
OpenGL applications — Sublime Merge among them — render nothing at all in that
mode.

Each distro runs its own Weston instance and writes its own log, so check the
log inside the affected distro:

```sh
grep -E "rdp_allocate_shared_memory|use_gfxredir" /mnt/wslg/weston.log
```

- `use_gfxredir = 1` — healthy, shared memory in use.
- `use_gfxredir = 0`, usually preceded by
  `rdp_allocate_shared_memory: Failed to open "/mnt/shared_memory/{...}" with error: Input/output error`
  — copy mode.

The remedy is `wsl --shutdown` followed by restarting the distro, then
re-checking the log. Note this terminates every running distro including
`docker-desktop`.

Observed once on WSL 2.7.13.0 / WSLg 1.0.73.2, where Debian hit it and Ubuntu
on the same host did not. Start order was not the trigger: after the shutdown,
both distros allocated successfully regardless of which started first. If it
recurs, it is worth reporting to microsoft/wslg, since a cold boot should not
drop the allocation.

`LIBGL_ALWAYS_SOFTWARE=1` does not work around it — the failure is in the
surface transport, not the GL driver.
