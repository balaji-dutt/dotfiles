"""Fixture-local kernel and container inputs for notification scripts."""

from __future__ import annotations

import shlex
from dataclasses import dataclass
from pathlib import Path

from tests.support.fixtures import write_executable


WSL2_RELEASE = "5.15.167.4-microsoft-standard-WSL2"


@dataclass(frozen=True)
class NotificationPlatform:
    root: Path
    fake_bin: Path

    def simulate(self, system: str, *, kernel_release: str, container: bool = False) -> None:
        write_executable(
            self.fake_bin / "uname",
            f"#!/bin/sh\nprintf '%s\\n' {shlex.quote(system)}\n",
        )
        (self.root / "kernel-osrelease").write_text(kernel_release + "\n", encoding="utf-8")
        for marker in ("dockerenv", "containerenv"):
            (self.root / marker).unlink(missing_ok=True)
        if container:
            (self.root / "dockerenv").touch()


def copy_notification_script(
    source: Path, destination: Path, fixture_root: Path, fake_bin: Path
) -> NotificationPlatform:
    platform_root = fixture_root / "notification-platform with spaces"
    platform_root.mkdir()
    contents = source.read_text(encoding="utf-8")
    for original, replacement, expected in (
        ("/proc/sys/kernel/osrelease", platform_root / "kernel-osrelease", 2),
        ("/.dockerenv", platform_root / "dockerenv", 1),
        ("/run/.containerenv", platform_root / "containerenv", 1),
    ):
        actual = contents.count(original)
        if actual != expected:
            raise AssertionError(f"{source}: expected {expected} occurrences of {original}, got {actual}")
        contents = contents.replace(original, shlex.quote(str(replacement)))
    write_executable(destination, contents)
    platform = NotificationPlatform(platform_root, fake_bin)
    platform.simulate("Darwin", kernel_release="23.0.0")
    return platform
