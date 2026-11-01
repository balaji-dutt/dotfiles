"""Native Windows security and process primitives for the peer-env client."""

from __future__ import annotations

import contextlib
import ctypes as C
from ctypes import wintypes as W
import json
import msvcrt
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import threading
import time

kernel = C.WinDLL("kernel32", use_last_error=True)
advapi = C.WinDLL("advapi32", use_last_error=True)
P = C.c_void_p
DWORD = W.DWORD


def bind(dll, name, result, *args):
    function = getattr(dll, name)
    function.restype = result
    function.argtypes = args
    return function


close = bind(kernel, "CloseHandle", W.BOOL, W.HANDLE)
free = bind(kernel, "LocalFree", P, P)
open_token = bind(advapi, "OpenProcessToken", W.BOOL, W.HANDLE, DWORD, C.POINTER(W.HANDLE))
current_process = bind(kernel, "GetCurrentProcess", W.HANDLE)
token_info = bind(advapi, "GetTokenInformation", W.BOOL, W.HANDLE, C.c_int, P, DWORD, C.POINTER(DWORD))
sid_string = bind(advapi, "ConvertSidToStringSidW", W.BOOL, P, C.POINTER(P))
descriptor = bind(advapi, "ConvertStringSecurityDescriptorToSecurityDescriptorW", W.BOOL,
                  W.LPCWSTR, DWORD, C.POINTER(P), C.POINTER(DWORD))
create_directory = bind(kernel, "CreateDirectoryW", W.BOOL, W.LPCWSTR, P)
drive_type = bind(kernel, "GetDriveTypeW", W.UINT, W.LPCWSTR)
create_file = bind(kernel, "CreateFileW", W.HANDLE, W.LPCWSTR, DWORD, DWORD, P, DWORD, DWORD, W.HANDLE)
security_info = bind(advapi, "GetSecurityInfo", DWORD, W.HANDLE, C.c_int, DWORD,
                     C.POINTER(P), P, C.POINTER(P), P, C.POINTER(P))
get_ace = bind(advapi, "GetAce", W.BOOL, P, DWORD, C.POINTER(P))
sd_control = bind(advapi, "GetSecurityDescriptorControl", W.BOOL, P, C.POINTER(W.WORD), C.POINTER(DWORD))


class SecurityAttributes(C.Structure):
    _fields_ = [("length", DWORD), ("descriptor", P), ("inherit", W.BOOL)]


class FileInformation(C.Structure):
    _fields_ = [("attributes", DWORD), ("creation", W.FILETIME), ("access", W.FILETIME),
                ("write", W.FILETIME), ("volume", DWORD), ("size_high", DWORD),
                ("size_low", DWORD), ("links", DWORD), ("index_high", DWORD), ("index_low", DWORD)]


file_information = bind(kernel, "GetFileInformationByHandle", W.BOOL, W.HANDLE, C.POINTER(FileInformation))
TRUSTED = {"S-1-5-18", "S-1-5-32-544"}
INSTALLER = "S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464"
INVALID_HANDLE = P(-1).value
CANCEL = threading.Event()
STOP_GRACE = 10


class Refused(OSError):
    """A client prerequisite or private-state safety check could not be satisfied."""


def checked(ok, operation):
    if not ok:
        raise C.WinError(C.get_last_error(), operation)


def sid_text(sid):
    text = P()
    checked(sid_string(sid, C.byref(text)), "read security principal")
    try:
        return C.wstring_at(text)
    finally:
        free(text)


def user_sid():
    token = W.HANDLE()
    checked(open_token(current_process(), 8, C.byref(token)), "open user token")
    try:
        size = DWORD()
        token_info(token, 1, None, 0, C.byref(size))
        buffer = C.create_string_buffer(size.value)
        checked(token_info(token, 1, buffer, size, C.byref(size)), "read user token")
        return sid_text(P.from_buffer(buffer).value)
    finally:
        close(token)


@contextlib.contextmanager
def private_attributes():
    sid = user_sid()
    text = f"O:{sid}D:P" + "".join(f"(A;OICI;FA;;;{principal})" for principal in (sid, *sorted(TRUSTED)))
    sd = P()
    checked(descriptor(text, 1, C.byref(sd), None), "create private security descriptor")
    try:
        yield SecurityAttributes(C.sizeof(SecurityAttributes), sd, False)
    finally:
        free(sd)


def validate(handle, path, *, private, directory):
    info = FileInformation()
    checked(file_information(handle, C.byref(info)), f"inspect state {path}")
    if info.attributes & 0x400 or bool(info.attributes & 0x10) != directory or (not directory and info.links != 1):
        raise Refused(f"unsafe state type, reparse point, or hard link: {path}")
    owner, dacl, sd = P(), P(), P()
    code = security_info(handle, 1, 5, C.byref(owner), None, C.byref(dacl), None, C.byref(sd))
    if code:
        raise Refused(f"cannot inspect state security: {path}: {C.FormatError(code).strip()}")
    try:
        sid = user_sid()
        trusted = TRUSTED | {sid}
        if private and sid_text(owner) != sid:
            raise Refused(f"state must be owned by the current user: {path}")
        if not private:
            trusted.add(INSTALLER)
            if sid_text(owner) not in trusted:
                raise Refused(f"untrusted state ancestor owner: {path}")
        trusted.add("S-1-3-4")
        control, revision = W.WORD(), DWORD()
        checked(sd_control(sd, C.byref(control), C.byref(revision)), "inspect DACL protection")
        if not dacl or (private and not control.value & 0x1000):
            raise Refused(f"state requires a protected, non-null DACL: {path}")
        count = C.c_ushort.from_address(dacl.value + 4).value
        for index in range(count):
            ace = P()
            checked(get_ace(dacl, index, C.byref(ace)), "inspect state access")
            kind, flags = C.string_at(ace, 2)
            if flags & 8:  # INHERIT_ONLY_ACE does not grant access to this object.
                continue
            if kind == 1:
                continue
            if kind != 0:
                raise Refused(f"unsupported state access rule: {path}")
            mask = DWORD.from_address(ace.value + 4).value
            principal = sid_text(ace.value + 8)
            # Directory creation on ancestors does not permit replacement of this chain.
            dangerous = mask if private else mask & (0x10000000 | 0x40000000 | 0xD0152)
            if principal not in trusted and dangerous:
                raise Refused(f"state access granted to an untrusted principal: {path}")
    finally:
        free(sd)


def open_path(path, access=0x20080, disposition=3, attributes=None):
    handle = create_file(str(path), access, 3, C.byref(attributes) if attributes else None,
                         disposition, 0x02200000, None)
    if handle == INVALID_HANDLE:
        raise C.WinError(C.get_last_error(), f"cannot open private state {path}")
    return handle


@contextlib.contextmanager
def directory_guard(path, *, create=True):
    path = Path(os.path.abspath(path))
    if str(path).startswith("\\\\"):
        raise Refused("private peer-env state requires a local filesystem")
    if drive_type(path.anchor) != 3:
        raise Refused("private peer-env state requires a local fixed drive")
    handles = []
    try:
        chain = [*reversed(path.parents), path]
        for component in chain:
            if not component.exists() and not component.is_symlink():
                if not create:
                    raise FileNotFoundError(str(component))
                with private_attributes() as attributes:
                    if not create_directory(str(component), C.byref(attributes)) and C.get_last_error() != 183:
                        checked(False, f"create private directory {component}")
            handle = open_path(component)
            handles.append(handle)
            validate(handle, component, private=component == path, directory=True)
        yield path
    finally:
        for handle in reversed(handles):
            close(handle)


def private_directory(path):
    with directory_guard(path):
        pass


def validate_file(path):
    handle = open_path(path)
    try:
        validate(handle, path, private=True, directory=False)
    finally:
        close(handle)


def read_private(path):
    with directory_guard(path.parent):
        handle = open_path(path, 0x80020080)
        try:
            validate(handle, path, private=True, directory=False)
            fd = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
        except BaseException:
            close(handle)
            raise
        with os.fdopen(fd, "r", encoding="utf-8") as stream:
            return stream.read()


def atomic_write(path, text):
    with directory_guard(path.parent):
        if path.exists() or path.is_symlink():
            validate_file(path)
        temp = path.parent / (".probe-" + secrets.token_hex(16))
        with private_attributes() as attributes:
            handle = open_path(temp, 0xC0020080, 1, attributes)
        try:
            fd = msvcrt.open_osfhandle(handle, os.O_WRONLY | os.O_BINARY)
        except BaseException:
            close(handle)
            temp.unlink()
            raise
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
                stream.write(text)
                stream.flush()
                os.fsync(stream.fileno())
            validate_file(temp)
            os.replace(temp, path)
        finally:
            temp.unlink(missing_ok=True)


def unlink_private(path):
    with directory_guard(path.parent):
        validate_file(path)
        path.unlink()


@contextlib.contextmanager
def private_tempdir(root):
    with directory_guard(root):
        temp = root / ("index-" + secrets.token_hex(16))
        with private_attributes() as attributes:
            checked(create_directory(str(temp), C.byref(attributes)), "create private index directory")
        try:
            yield str(temp)
        finally:
            shutil.rmtree(temp)


def ssh_executable(environ):
    root = environ.get("SystemRoot") or os.environ.get("SystemRoot")
    if not root:
        raise Refused("SystemRoot is required to locate native Windows OpenSSH")
    executable = Path(root) / "System32" / "OpenSSH" / "ssh.exe"
    if not executable.is_file():
        raise Refused(f"native Windows OpenSSH is required (use 64-bit Python): {executable}")
    return str(executable)


def git_executable(environ):
    for entry in environ.get("PATH", os.defpath).split(os.pathsep):
        directory = Path(entry.strip('"'))
        if entry and directory.is_absolute():
            executable = directory / "git.exe"
            if executable.is_file():
                return str(executable)
    raise Refused("Git for Windows git.exe is required on PATH")


class BasicLimits(C.Structure):
    _fields_ = [("process_time", C.c_int64), ("job_time", C.c_int64), ("flags", DWORD),
                ("minimum", C.c_size_t), ("maximum", C.c_size_t), ("active", DWORD),
                ("affinity", C.c_size_t), ("priority", DWORD), ("scheduling", DWORD)]


class ExtendedLimits(C.Structure):
    _fields_ = [("basic", BasicLimits), ("io", C.c_uint64 * 6),
                ("process_memory", C.c_size_t), ("job_memory", C.c_size_t),
                ("peak_process", C.c_size_t), ("peak_job", C.c_size_t)]


class Accounting(C.Structure):
    _fields_ = [("times", C.c_int64 * 4), ("faults", DWORD), ("total", DWORD),
                ("active", DWORD), ("terminated", DWORD)]


create_job = bind(kernel, "CreateJobObjectW", W.HANDLE, P, W.LPCWSTR)
set_job = bind(kernel, "SetInformationJobObject", W.BOOL, W.HANDLE, C.c_int, P, DWORD)
assign_job = bind(kernel, "AssignProcessToJobObject", W.BOOL, W.HANDLE, W.HANDLE)
query_job = bind(kernel, "QueryInformationJobObject", W.BOOL, W.HANDLE, C.c_int, P, DWORD, P)
terminate_job = bind(kernel, "TerminateJobObject", W.BOOL, W.HANDLE, W.UINT)
open_process = bind(kernel, "OpenProcess", W.HANDLE, DWORD, W.BOOL, DWORD)
in_job = bind(kernel, "IsProcessInJob", W.BOOL, W.HANDLE, W.HANDLE, C.POINTER(W.BOOL))
wait_handle = bind(kernel, "WaitForSingleObject", DWORD, W.HANDLE, DWORD)


class Job:
    def __init__(self):
        self.handle = create_job(None, None)
        checked(self.handle, "create process containment job")
        limits = ExtendedLimits()
        limits.basic.flags = 0x2000
        try:
            checked(set_job(self.handle, 9, C.byref(limits), C.sizeof(limits)), "configure process containment")
        except BaseException:
            close(self.handle)
            raise

    def assign(self, process):
        checked(assign_job(self.handle, int(process._handle)), "contain gated worker before launch")

    def stop(self):
        deadline = time.monotonic() + STOP_GRACE
        handles = []
        try:
            try:
                buffer = C.create_string_buffer(8 + C.sizeof(C.c_size_t) * 65536)
                checked(query_job(self.handle, 3, buffer, C.sizeof(buffer), None), "enumerate owned job members")
                count = DWORD.from_buffer(buffer, 4).value
                for index in range(count):
                    pid = C.c_size_t.from_buffer(buffer, 8 + index * C.sizeof(C.c_size_t)).value
                    handle = open_process(0x101000, False, pid)
                    if not handle:
                        if C.get_last_error() == 87:
                            continue
                        checked(False, "open owned job member")
                    belongs = W.BOOL()
                    try:
                        checked(in_job(handle, self.handle, C.byref(belongs)), "verify owned job member")
                        if belongs.value:
                            handles.append(handle)
                            handle = None
                    finally:
                        if handle:
                            close(handle)
            finally:
                checked(terminate_job(self.handle, 125), "terminate owned command tree")
            while True:
                info = Accounting()
                checked(query_job(self.handle, 1, C.byref(info), C.sizeof(info), None), "account for owned command tree")
                if info.active == 0:
                    break
                if time.monotonic() >= deadline:
                    raise OSError("owned command tree shutdown unverified after cleanup deadline")
                time.sleep(0.025)
            for handle in handles:
                remaining = max(0, int((deadline - time.monotonic()) * 1000))
                if wait_handle(handle, remaining) != 0:
                    raise OSError("owned job member exit unverified after cleanup deadline")
        finally:
            for handle in handles:
                close(handle)

    def close(self):
        close(self.handle)


WORKER = """import json, subprocess, sys
request = sys.stdin.buffer.readline()
if not request:
    sys.exit(125)
payload = json.loads(request)
try:
    child = subprocess.Popen(payload['argv'], stdin=subprocess.PIPE if payload['input'] is not None else subprocess.DEVNULL)
    child.communicate(payload['input'].encode('utf-8') if payload['input'] is not None else None)
    sys.exit(child.returncode)
except OSError as error:
    print('peer-env: cannot launch command: ' + str(error), file=sys.stderr)
    sys.exit(2)
"""


def capture(argv, *, timeout, input_text=None, **kwargs):
    if timeout <= 0:
        raise subprocess.TimeoutExpired(argv, timeout)
    kwargs.pop("stdin", None)
    kwargs.setdefault("text", True)
    if kwargs["text"]:
        kwargs.setdefault("encoding", "utf-8")
    job = Job()
    process = None
    primary = None
    assigned = False
    deadline = time.monotonic() + timeout
    try:
        process = subprocess.Popen([sys.executable, "-I", "-X", "utf8", "-c", WORKER],
                                   stdin=subprocess.PIPE, creationflags=subprocess.CREATE_NEW_PROCESS_GROUP, **kwargs)
        job.assign(process)
        assigned = True
        request = json.dumps({"argv": list(map(str, argv)), "input": input_text}) + "\n"
        pending = request if kwargs["text"] else request.encode("utf-8")
        while True:
            if CANCEL.is_set():
                raise InterruptedError("peer probe cancelled")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(argv, timeout)
            try:
                output, error = process.communicate(pending, timeout=min(remaining, 0.05))
                break
            except subprocess.TimeoutExpired:
                pending = None
                if process.poll() is not None:
                    job.stop()
                    output, error = process.communicate(timeout=STOP_GRACE)
                    break
        return subprocess.CompletedProcess(argv, process.returncode, output, error)
    except BaseException as error:
        primary = error
        raise
    finally:
        try:
            if assigned:
                job.stop()
            elif process is not None:
                process.stdin.close()
                process.kill()
            if process is not None:
                process.wait(timeout=STOP_GRACE)
                if assigned:
                    process.communicate(timeout=STOP_GRACE)
        except (OSError, subprocess.TimeoutExpired) as error:
            if primary is None:
                raise OSError(f"local command shutdown unverified: {error}") from error
            print(f"peer-env: local command shutdown unverified: {error}", file=sys.stderr)
        finally:
            job.close()
            if process is not None:
                for stream in (process.stdin, process.stdout, process.stderr):
                    if stream is not None:
                        stream.close()
