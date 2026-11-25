"""Forced-command entry point for a native Windows peer-env serving peer.

sshd gives an administrator who logs in with a key an elevated session. This
launcher and the interpreter that runs it are the only code that runs
elevated: it starts the checkout's ``assets/peer-env serve`` under a filtered,
medium-integrity copy of its own token inside a kill-on-close Job, relays the
exit code, and reads nothing from the checkout. Install a copy where only
administrators can write and name it, with the checkout root, in the key's
forced command:

    restrict,command="<python.exe> -I <launcher> <checkout root>" ssh-ed25519 ...

``<python.exe>`` must be an all-users install whose folder only administrators
can write. ``-I`` keeps the checkout, the working directory, user site-packages
and ``PYTHON*`` variables out, but not the interpreter's own standard library,
so a per-user Python would let any process running as the user gain elevation.
"""

from __future__ import annotations

import ctypes as C
from ctypes import wintypes as W
import msvcrt
import os
from pathlib import Path
import subprocess
import sys

EXIT_REFUSED = 2
LUA_TOKEN = 0x4
MEDIUM_INTEGRITY = "S-1-16-8192"
HIGH_INTEGRITY_RID = 0x3000
TOKEN_USER, TOKEN_DEFAULT_DACL, TOKEN_ELEVATION, TOKEN_INTEGRITY = 1, 6, 20, 25
P = C.c_void_p
DWORD = W.DWORD


class Refused(Exception):
    """The launcher could not start an unelevated serve."""


class SidAndAttributes(C.Structure):
    _fields_ = [("sid", P), ("attributes", DWORD)]


class StartupInfo(C.Structure):
    _fields_ = [("cb", DWORD), ("reserved", W.LPWSTR), ("desktop", W.LPWSTR), ("title", W.LPWSTR),
                ("x", DWORD), ("y", DWORD), ("x_size", DWORD), ("y_size", DWORD),
                ("x_chars", DWORD), ("y_chars", DWORD), ("fill", DWORD), ("flags", DWORD),
                ("show", W.WORD), ("reserved2_size", W.WORD), ("reserved2", P),
                ("stdin", W.HANDLE), ("stdout", W.HANDLE), ("stderr", W.HANDLE)]


class ProcessInformation(C.Structure):
    _fields_ = [("process", W.HANDLE), ("thread", W.HANDLE), ("pid", DWORD), ("tid", DWORD)]


class BasicLimits(C.Structure):
    _fields_ = [("process_time", C.c_int64), ("job_time", C.c_int64), ("flags", DWORD),
                ("minimum", C.c_size_t), ("maximum", C.c_size_t), ("active", DWORD),
                ("affinity", C.c_size_t), ("priority", DWORD), ("scheduling", DWORD)]


class ExtendedLimits(C.Structure):
    _fields_ = [("basic", BasicLimits), ("io", C.c_uint64 * 6),
                ("process_memory", C.c_size_t), ("job_memory", C.c_size_t),
                ("peak_process", C.c_size_t), ("peak_job", C.c_size_t)]


if os.name == "nt":
    kernel = C.WinDLL("kernel32", use_last_error=True)
    advapi = C.WinDLL("advapi32", use_last_error=True)

    def bind(dll, name, result, *args):
        function = getattr(dll, name)
        function.restype = result
        function.argtypes = args
        return function

    close = bind(kernel, "CloseHandle", W.BOOL, W.HANDLE)
    free = bind(kernel, "LocalFree", P, P)
    current_process = bind(kernel, "GetCurrentProcess", W.HANDLE)
    duplicate = bind(kernel, "DuplicateHandle", W.BOOL, W.HANDLE, W.HANDLE, W.HANDLE, C.POINTER(W.HANDLE),
                     DWORD, W.BOOL, DWORD)
    resume = bind(kernel, "ResumeThread", DWORD, W.HANDLE)
    wait = bind(kernel, "WaitForSingleObject", DWORD, W.HANDLE, DWORD)
    exit_code = bind(kernel, "GetExitCodeProcess", W.BOOL, W.HANDLE, C.POINTER(DWORD))
    terminate = bind(kernel, "TerminateProcess", W.BOOL, W.HANDLE, W.UINT)
    create_job = bind(kernel, "CreateJobObjectW", W.HANDLE, P, W.LPCWSTR)
    set_job = bind(kernel, "SetInformationJobObject", W.BOOL, W.HANDLE, C.c_int, P, DWORD)
    assign_job = bind(kernel, "AssignProcessToJobObject", W.BOOL, W.HANDLE, W.HANDLE)
    open_token = bind(advapi, "OpenProcessToken", W.BOOL, W.HANDLE, DWORD, C.POINTER(W.HANDLE))
    token_info = bind(advapi, "GetTokenInformation", W.BOOL, W.HANDLE, C.c_int, P, DWORD, C.POINTER(DWORD))
    set_token = bind(advapi, "SetTokenInformation", W.BOOL, W.HANDLE, C.c_int, P, DWORD)
    sid_to_string = bind(advapi, "ConvertSidToStringSidW", W.BOOL, P, C.POINTER(P))
    string_to_sid = bind(advapi, "ConvertStringSidToSidW", W.BOOL, W.LPCWSTR, C.POINTER(P))
    sid_length = bind(advapi, "GetLengthSid", DWORD, P)
    descriptor = bind(advapi, "ConvertStringSecurityDescriptorToSecurityDescriptorW", W.BOOL,
                      W.LPCWSTR, DWORD, C.POINTER(P), C.POINTER(DWORD))
    descriptor_dacl = bind(advapi, "GetSecurityDescriptorDacl", W.BOOL, P, C.POINTER(W.BOOL), C.POINTER(P),
                           C.POINTER(W.BOOL))
    restrict_token = bind(advapi, "CreateRestrictedToken", W.BOOL, W.HANDLE, DWORD, DWORD, P, DWORD, P, DWORD, P,
                          C.POINTER(W.HANDLE))
    create_as_user = bind(advapi, "CreateProcessAsUserW", W.BOOL, W.HANDLE, W.LPCWSTR, W.LPWSTR, P, P, W.BOOL,
                          DWORD, P, W.LPCWSTR, C.POINTER(StartupInfo), C.POINTER(ProcessInformation))


def checked(ok, operation):
    if not ok:
        raise Refused(f"{operation}: {C.FormatError(C.get_last_error()).strip()}")


def sid_text(sid):
    text = P()
    checked(sid_to_string(sid, C.byref(text)), "format security principal")
    try:
        return C.wstring_at(text)
    finally:
        free(text)


def token_sid(token, kind):
    size = DWORD()
    token_info(token, kind, None, 0, C.byref(size))
    buffer = C.create_string_buffer(size.value)
    checked(token_info(token, kind, buffer, size, C.byref(size)), "read token")
    return sid_text(P.from_buffer(buffer).value)


def token_elevated(token):
    elevation, size = DWORD(), DWORD()
    checked(token_info(token, TOKEN_ELEVATION, C.byref(elevation), C.sizeof(elevation), C.byref(size)), "read elevation")
    integrity = int(token_sid(token, TOKEN_INTEGRITY).rsplit("-", 1)[1])
    return bool(elevation.value) or integrity >= HIGH_INTEGRITY_RID


def filtered_token():
    source = W.HANDLE()
    checked(open_token(current_process(), 0xF01FF, C.byref(source)), "open process token")
    try:
        token = W.HANDLE()
        checked(restrict_token(source, LUA_TOKEN, 0, None, 0, None, 0, None, C.byref(token)), "filter token")
    finally:
        close(source)
    try:
        medium = P()
        checked(string_to_sid(MEDIUM_INTEGRITY, C.byref(medium)), "build integrity level")
        try:
            label = SidAndAttributes(medium, 0x20)
            checked(set_token(token, TOKEN_INTEGRITY, C.byref(label), C.sizeof(label) + sid_length(medium)),
                    "lower integrity level")
        finally:
            free(medium)
        # The elevated default DACL names only Administrators and SYSTEM, so the filtered process cannot open its own pipes.
        security = P()
        checked(descriptor(f"D:(A;;GA;;;{token_sid(token, TOKEN_USER)})(A;;GA;;;SY)", 1, C.byref(security), None),
                "build default access list")
        try:
            present, dacl, defaulted = W.BOOL(), P(), W.BOOL()
            checked(descriptor_dacl(security, C.byref(present), C.byref(dacl), C.byref(defaulted)), "read default access list")
            default = P(dacl.value)
            checked(set_token(token, TOKEN_DEFAULT_DACL, C.byref(default), C.sizeof(default)), "set default access list")
        finally:
            free(security)
        if token_elevated(token):
            raise Refused("the filtered token is still elevated")
    except BaseException:
        close(token)
        raise
    return token


def serve_command(root):
    if not os.path.isabs(root):
        raise Refused("the checkout root must be an absolute path")
    helper = Path(root) / "assets" / "peer-env"
    if not helper.is_file():
        raise Refused(f"no peer-env helper at {helper}")
    return [sys.executable, "-X", "utf8", str(helper), "serve"]


def run_filtered(command):
    token = filtered_token()
    info = ProcessInformation()
    inherited = []
    job = None
    try:
        job = create_job(None, None)
        checked(job, "create containment job")
        limits = ExtendedLimits()
        limits.basic.flags = 0x2000
        checked(set_job(job, 9, C.byref(limits), C.sizeof(limits)), "configure containment job")
        startup = StartupInfo()
        startup.cb = C.sizeof(startup)
        startup.flags = 0x100
        for field, fd in (("stdin", 0), ("stdout", 1), ("stderr", 2)):
            handle = W.HANDLE()
            try:
                source = msvcrt.get_osfhandle(fd)
            except OSError as error:
                raise Refused(f"standard handle {fd} is unusable: {error}") from error
            checked(duplicate(current_process(), source, current_process(), C.byref(handle), 0, True, 2),
                    "share standard handle")
            inherited.append(handle)
            setattr(startup, field, handle)
        line = C.create_unicode_buffer(subprocess.list2cmdline(command))
        checked(create_as_user(token, command[0], line, None, None, True, 0x4 | 0x400, None, None,
                               C.byref(startup), C.byref(info)), "start unelevated serve")
        try:
            checked(assign_job(job, info.process), "contain unelevated serve")
            checked(resume(info.thread) != 0xFFFFFFFF, "resume unelevated serve")
        except BaseException:
            terminate(info.process, EXIT_REFUSED)
            raise
        wait(info.process, 0xFFFFFFFF)
        code = DWORD()
        checked(exit_code(info.process, C.byref(code)), "read serve exit code")
        return code.value
    finally:
        for handle in (*inherited, info.thread, info.process, token, job):
            if handle:
                close(handle)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    try:
        if os.name != "nt":
            raise Refused("this launcher serves native Windows only")
        if len(argv) != 1:
            raise Refused("usage: peer_env_windows_launcher.py <checkout root>")
        return run_filtered(serve_command(argv[0]))
    except Refused as error:
        print(f"peer-env launcher: refused: {error}", file=sys.stderr)
        return EXIT_REFUSED


if __name__ == "__main__":
    sys.exit(main())
