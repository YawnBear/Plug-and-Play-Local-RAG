from __future__ import annotations

import base64
import ctypes
import ipaddress
import json
import os
import re
import socket
import ssl
import subprocess
import time
import urllib.error
import urllib.request
from collections import deque
from collections.abc import Callable
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .manifest import RestartPolicy, Service


class RuntimeError(Exception):
    """Supervisor runtime failure."""


class ProcessCleanupError(RuntimeError):
    """A suspended child could not be conclusively terminated or closed."""

    def __init__(
        self,
        process: ManagedProcess,
        *,
        start_error: BaseException,
        cleanup_error: BaseException,
    ) -> None:
        self.process = process
        self.start_error = start_error
        self.cleanup_error = cleanup_error
        super().__init__(
            "suspended child cleanup failed; retained handles "
            f"process={process.process_handle} thread={process.thread_handle} "
            f"pid={process.process_id}"
        )


class ChildReadinessError(RuntimeError):
    """A child exited or timed out before satisfying its readiness contract."""

    def __init__(self, service: str, exit_code: int | None) -> None:
        self.service = service
        self.exit_code = exit_code
        state = "still running" if exit_code is None else f"exit code {exit_code}"
        super().__init__(f"{service} did not become ready ({state})")


def _startup_exception_item(error: BaseException) -> dict[str, object]:
    item: dict[str, object] = {"type": type(error).__name__}
    if isinstance(error, OSError):
        if isinstance(error.errno, int):
            item["errno"] = error.errno
        winerror = getattr(error, "winerror", None)
        if isinstance(winerror, int):
            item["winerror"] = winerror
    exit_code = getattr(error, "exit_code", None)
    if isinstance(exit_code, int):
        item["exit_code"] = exit_code
    return item


def startup_failure_payload(
    service: str,
    error: BaseException,
) -> dict[str, object]:
    chain: list[dict[str, object]] = []
    current: BaseException | None = error
    seen: set[int] = set()
    while current is not None and len(chain) < 8 and id(current) not in seen:
        seen.add(id(current))
        chain.append(_startup_exception_item(current))
        current = current.__cause__ or current.__context__
    return {
        "schema_version": 1,
        "service": service[:128],
        "exception_chain": chain,
    }


def remove_startup_diagnostic(path: Path | None) -> None:
    if path is None:
        return
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def write_startup_diagnostic(
    path: Path | None,
    service: str,
    error: BaseException,
) -> None:
    if path is None:
        return
    temporary_path = path.with_name(f"{path.name}.tmp")
    try:
        encoded = json.dumps(
            startup_failure_payload(service, error),
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        if len(encoded) > MAXIMUM_STARTUP_DIAGNOSTIC_BYTES:
            return
        temporary_path.write_bytes(encoded)
        temporary_path.replace(path)
    except OSError:
        remove_startup_diagnostic(temporary_path)


SAFE_DEPLOYMENT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
SUPERVISOR_MUTEX_SDDL = "D:P(A;;GA;;;SY)(A;;GA;;;BA)"
SUPERVISOR_JOB_SDDL = "D:P(A;;GA;;;SY)(A;;0x0004;;;BA)"
MAXIMUM_ENVIRONMENT_FILE_BYTES = 64 * 1024
MAXIMUM_STARTUP_DIAGNOSTIC_BYTES = 64 * 1024
WINDOWS_PASSWORD_KEY = "RAG_WINDOWS_ACCOUNT_PASSWORD"
READ_DATA_RIGHT = 0x00000001
WRITE_CAPABLE_RIGHTS_MASK = (
    0x00000002  # WriteData / CreateFiles
    | 0x00000004  # AppendData / CreateDirectories
    | 0x00000010  # WriteEA
    | 0x00000040  # DeleteChild
    | 0x00000100  # WriteAttributes
    | 0x00010000  # Delete
    | 0x00040000  # ChangePermissions
    | 0x00080000  # TakeOwnership
)
INHERITED_ENVIRONMENT_KEYS = (
    "COMSPEC",
    "NUMBER_OF_PROCESSORS",
    "PROCESSOR_ARCHITECTURE",
    "SystemDrive",
    "SystemRoot",
)


@dataclass(frozen=True, slots=True)
class RestartDecision:
    allowed: bool
    delay_seconds: int | None
    attempts_in_window: int


@dataclass(slots=True)
class ManagedProcess:
    process_handle: int
    thread_handle: int
    process_id: int


class Win32AdapterProtocol(Protocol):
    def create_mutex(self, name: str, sddl: str) -> tuple[int, bool]: ...

    def create_job(self, name: str, sddl: str) -> int: ...

    def assign_to_job(self, job_handle: int, process_handle: int) -> None: ...

    def create_suspended(
        self,
        command: list[str],
        *,
        cwd: str,
        environment: dict[str, str],
        identity_token: int,
    ) -> ManagedProcess: ...

    def logon_service(self, identity: str, password: str) -> int: ...

    def resume(self, process: ManagedProcess) -> None: ...

    def poll(self, process: ManagedProcess) -> int | None: ...

    def terminate(self, process: ManagedProcess) -> None: ...

    def wait(self, process: ManagedProcess, timeout_milliseconds: int) -> bool: ...

    def close_process(self, process: ManagedProcess) -> None: ...

    def close_handle(self, handle: int) -> None: ...


class EnvironmentFileValidator(Protocol):
    def __call__(self, path: Path, identity: str) -> None: ...


def windows_tcp_listener_owned_by(
    local_address: str,
    local_port: int,
    process_id: int,
) -> bool:
    """Return true only for a Windows listener in the managed process tree."""
    if os.name != "nt" or process_id <= 0:
        return False
    try:
        expected_address = ipaddress.ip_address(local_address)
    except ValueError:
        return False
    address_family = (
        socket.AF_INET if expected_address.version == 4 else socket.AF_INET6
    )

    class Tcp4RowOwnerPid(ctypes.Structure):
        _fields_ = (
            ("state", wintypes.DWORD),
            ("local_address", wintypes.DWORD),
            ("local_port", wintypes.DWORD),
            ("remote_address", wintypes.DWORD),
            ("remote_port", wintypes.DWORD),
            ("owning_process_id", wintypes.DWORD),
        )

    class Tcp6RowOwnerPid(ctypes.Structure):
        _fields_ = (
            ("local_address", ctypes.c_ubyte * 16),
            ("local_scope_id", wintypes.DWORD),
            ("local_port", wintypes.DWORD),
            ("remote_address", ctypes.c_ubyte * 16),
            ("remote_scope_id", wintypes.DWORD),
            ("remote_port", wintypes.DWORD),
            ("state", wintypes.DWORD),
            ("owning_process_id", wintypes.DWORD),
        )

    ip_helper = ctypes.WinDLL("iphlpapi", use_last_error=True)
    get_table = ip_helper.GetExtendedTcpTable
    get_table.argtypes = (
        ctypes.c_void_p,
        ctypes.POINTER(wintypes.DWORD),
        wintypes.BOOL,
        wintypes.ULONG,
        ctypes.c_int,
        wintypes.ULONG,
    )
    get_table.restype = wintypes.DWORD
    table_size = wintypes.DWORD()
    owner_pid_listener_table = 3
    error_insufficient_buffer = 122
    try:
        result = int(
            get_table(
                None,
                ctypes.byref(table_size),
                False,
                address_family,
                owner_pid_listener_table,
                0,
            )
        )
        if result not in {0, error_insufficient_buffer} or table_size.value < 4:
            return False
        buffer = ctypes.create_string_buffer(table_size.value)
        result = int(
            get_table(
                buffer,
                ctypes.byref(table_size),
                False,
                address_family,
                owner_pid_listener_table,
                0,
            )
        )
        if result != 0:
            return False
        entry_count = wintypes.DWORD.from_buffer_copy(buffer.raw[:4]).value
        row_type = Tcp4RowOwnerPid if expected_address.version == 4 else Tcp6RowOwnerPid
        row_size = ctypes.sizeof(row_type)
        if 4 + (entry_count * row_size) > table_size.value:
            return False
        for index in range(entry_count):
            offset = 4 + (index * row_size)
            row = row_type.from_buffer_copy(buffer.raw[offset : offset + row_size])
            if expected_address.version == 4:
                row_address = ipaddress.IPv4Address(
                    int(row.local_address).to_bytes(4, "little")
                )
            else:
                row_address = ipaddress.IPv6Address(bytes(row.local_address))
            row_port = socket.ntohs(int(row.local_port) & 0xFFFF)
            if (
                row_address == expected_address
                and row_port == local_port
                and _windows_process_is_or_descends_from(
                    int(row.owning_process_id),
                    process_id,
                )
            ):
                return True
    except (OSError, OverflowError, ValueError):
        return False
    return False


def _windows_process_is_or_descends_from(
    process_id: int,
    ancestor_process_id: int,
) -> bool:
    if os.name != "nt" or process_id <= 0 or ancestor_process_id <= 0:
        return False
    if process_id == ancestor_process_id:
        return True

    class ProcessEntry32W(ctypes.Structure):
        _fields_ = (
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", wintypes.LONG),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", wintypes.WCHAR * 260),
        )

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_snapshot = kernel32.CreateToolhelp32Snapshot
    create_snapshot.argtypes = (wintypes.DWORD, wintypes.DWORD)
    create_snapshot.restype = wintypes.HANDLE
    process_first = kernel32.Process32FirstW
    process_first.argtypes = (wintypes.HANDLE, ctypes.POINTER(ProcessEntry32W))
    process_first.restype = wintypes.BOOL
    process_next = kernel32.Process32NextW
    process_next.argtypes = (wintypes.HANDLE, ctypes.POINTER(ProcessEntry32W))
    process_next.restype = wintypes.BOOL
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = (wintypes.HANDLE,)
    close_handle.restype = wintypes.BOOL

    snapshot = create_snapshot(0x00000002, 0)
    invalid_handle = ctypes.c_void_p(-1).value
    if not snapshot or int(snapshot) == invalid_handle:
        return False
    parents: dict[int, int] = {}
    try:
        entry = ProcessEntry32W()
        entry.dwSize = ctypes.sizeof(entry)
        if not process_first(snapshot, ctypes.byref(entry)):
            return False
        while True:
            parents[int(entry.th32ProcessID)] = int(entry.th32ParentProcessID)
            entry.dwSize = ctypes.sizeof(entry)
            if not process_next(snapshot, ctypes.byref(entry)):
                break
    finally:
        close_handle(snapshot)

    current = process_id
    visited: set[int] = set()
    while current not in visited and current > 0:
        if current == ancestor_process_id:
            return True
        visited.add(current)
        current = parents.get(current, 0)
    return False


def load_service_environment(
    service: Service,
    *,
    acl_validator: EnvironmentFileValidator,
    inherited: dict[str, str] | None = None,
) -> tuple[dict[str, str], str]:
    if service.environment_file is None:
        raise RuntimeError(f"{service.name} has no environment file")
    path = Path(service.environment_file)
    try:
        before = path.stat(follow_symlinks=False)
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise RuntimeError(f"{service.name} environment file is unavailable") from exc
    if (
        path.is_symlink()
        or not path.is_file()
        or bool(getattr(before, "st_file_attributes", 0) & 0x400)
        or before.st_size > MAXIMUM_ENVIRONMENT_FILE_BYTES
    ):
        raise RuntimeError(
            f"{service.name} environment file must be a bounded regular "
            "non-reparse file"
        )
    acl_validator(resolved, service.identity)
    try:
        raw = resolved.read_bytes()
        after = resolved.stat(follow_symlinks=False)
    except OSError as exc:
        raise RuntimeError(
            f"{service.name} environment file could not be read"
        ) from exc
    if (
        len(raw) > MAXIMUM_ENVIRONMENT_FILE_BYTES
        or before.st_dev != after.st_dev
        or before.st_ino != after.st_ino
        or before.st_size != after.st_size
        or before.st_mtime_ns != after.st_mtime_ns
        or bool(getattr(after, "st_file_attributes", 0) & 0x400)
    ):
        raise RuntimeError(f"{service.name} environment file changed while loading")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise RuntimeError(f"{service.name} environment file must be UTF-8") from exc
    if text.startswith("\ufeff"):
        raise RuntimeError(f"{service.name} environment file must not contain a BOM")
    allowed = set(service.environment_keys)
    parsed: dict[str, str] = {}
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise RuntimeError(
                f"{service.name} environment line {line_number} is invalid"
            )
        key, value = line.split("=", 1)
        if key not in allowed:
            raise RuntimeError(
                f"{service.name} environment contains unknown key {key!r}"
            )
        if key in parsed:
            raise RuntimeError(
                f"{service.name} environment contains duplicate key {key!r}"
            )
        if "\x00" in value:
            raise RuntimeError(f"{service.name} environment contains a NUL value")
        parsed[key] = value
    missing = set(service.environment_keys) - parsed.keys()
    if missing:
        raise RuntimeError(
            f"{service.name} environment is missing keys: {', '.join(sorted(missing))}"
        )
    source = inherited if inherited is not None else os.environ
    environment = {
        key: source[key] for key in INHERITED_ENVIRONMENT_KEYS if key in source
    }
    environment.update(parsed)
    environment["RAG_SUPERVISOR_SERVICE"] = service.name
    environment["RAG_SUPERVISOR_PARENT_PID"] = str(os.getpid())
    return environment, ""


def load_identity_password(
    service: Service,
    *,
    acl_validator: EnvironmentFileValidator,
) -> str:
    if service.identity_secret_file is None:
        raise RuntimeError(f"{service.name} has no identity secret file")
    path = Path(service.identity_secret_file)
    try:
        stat = path.stat(follow_symlinks=False)
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise RuntimeError(f"{service.name} identity secret is unavailable") from exc
    if (
        path.is_symlink()
        or not path.is_file()
        or bool(getattr(stat, "st_file_attributes", 0) & 0x400)
        or stat.st_size > 16 * 1024
    ):
        raise RuntimeError(
            f"{service.name} identity secret must be a bounded regular non-reparse file"
        )
    acl_validator(resolved, "")
    raw = resolved.read_bytes()
    after = resolved.stat(follow_symlinks=False)
    if (
        stat.st_dev != after.st_dev
        or stat.st_ino != after.st_ino
        or stat.st_size != after.st_size
        or stat.st_mtime_ns != after.st_mtime_ns
    ):
        raise RuntimeError(f"{service.name} identity secret changed while loading")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise RuntimeError(f"{service.name} identity secret must be UTF-8") from exc
    if text.startswith("\ufeff"):
        raise RuntimeError(f"{service.name} identity secret must not contain a BOM")
    lines = text.splitlines()
    if (
        len(lines) != 1
        or not lines[0].startswith(WINDOWS_PASSWORD_KEY + "=")
        or lines[0].count("=") != 1
    ):
        raise RuntimeError(f"{service.name} identity secret format is invalid")
    password = lines[0].split("=", 1)[1]
    if len(password) < 14:
        raise RuntimeError(
            f"{service.name} Windows service-account password is missing or too short"
        )
    return password


def validate_windows_secret_acl(path: Path, identity: str) -> None:
    if os.name != "nt":
        raise RuntimeError("Windows secret ACL validation is unavailable")
    encoded_path = base64.b64encode(str(path).encode("utf-8")).decode("ascii")
    command = (
        "$ErrorActionPreference='Stop';"
        "$path=[Text.Encoding]::UTF8.GetString("
        f"[Convert]::FromBase64String('{encoded_path}'));"
        "$acl=Get-Acl -LiteralPath $path;"
        "$parent=Get-Acl -LiteralPath (Split-Path -Parent $path);"
        "$current=[Security.Principal.WindowsIdentity]::GetCurrent().Name;"
        "[pscustomobject]@{Owner=$acl.Owner;Protected=$acl.AreAccessRulesProtected;"
        "Current=$current;Rules=@($acl.Access|ForEach-Object{"
        "[pscustomobject]@{Identity=$_.IdentityReference.Value;"
        "Type=$_.AccessControlType.ToString();Inherited=$_.IsInherited;"
        "RightsValue=[long]$_.FileSystemRights}});"
        "ParentOwner=$parent.Owner;"
        "ParentProtected=$parent.AreAccessRulesProtected;"
        "ParentRules=@($parent.Access|ForEach-Object{"
        "[pscustomobject]@{Identity=$_.IdentityReference.Value;"
        "Type=$_.AccessControlType.ToString();Inherited=$_.IsInherited;"
        "RightsValue=[long]$_.FileSystemRights}})}|ConvertTo-Json -Depth 5 -Compress"
    )
    encoded_command = base64.b64encode(command.encode("utf-16-le")).decode("ascii")
    powershell = Path(os.environ.get("SystemRoot", r"C:\Windows")) / (
        r"System32\WindowsPowerShell\v1.0\powershell.exe"
    )
    result = subprocess.run(
        [
            str(powershell),
            "-NoProfile",
            "-NonInteractive",
            "-EncodedCommand",
            encoded_command,
        ],
        capture_output=True,
        check=False,
        text=True,
        timeout=15,
    )
    if result.returncode != 0:
        raise RuntimeError("secret ACL inspection failed")
    try:
        acl = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("secret ACL inspection returned invalid evidence") from exc
    rules = acl.get("Rules")
    parent_rules = acl.get("ParentRules")
    if (
        not acl.get("Protected")
        or not acl.get("ParentProtected")
        or not isinstance(rules, list)
        or not rules
        or not isinstance(parent_rules, list)
        or not parent_rules
    ):
        raise RuntimeError("secret file DACL must be present and protected")
    expanded_identity = (
        os.environ.get("COMPUTERNAME", "") + identity[1:]
        if identity.startswith(".\\")
        else identity
    )
    allowed = {
        r"BUILTIN\Administrators".casefold(),
        r"NT AUTHORITY\SYSTEM".casefold(),
    }
    if not identity:
        allowed.add(r"NT SERVICE\RagSupervisor".casefold())
    if identity:
        allowed.add(expanded_identity.casefold())
    owner_allowed = {
        r"BUILTIN\Administrators".casefold(),
        r"NT AUTHORITY\SYSTEM".casefold(),
    }
    if (
        str(acl.get("Owner", "")).casefold() not in owner_allowed
        or str(acl.get("ParentOwner", "")).casefold() not in owner_allowed
    ):
        raise RuntimeError("secret file owner is not approved")
    service_read = not identity
    supervisor_read = False
    service_principal_pattern = re.compile(
        rf"^{re.escape(os.environ.get('COMPUTERNAME', ''))}\\rag[a-z0-9]+svc$",
        re.IGNORECASE,
    )
    for scope, rule in [
        *(("file", rule) for rule in rules),
        *(("parent", rule) for rule in parent_rules),
    ]:
        principal = str(rule.get("Identity", "")).casefold()
        rights = rule.get("RightsValue")
        shared_parent_service = (
            bool(identity)
            and scope == "parent"
            and service_principal_pattern.fullmatch(str(rule.get("Identity", "")))
            is not None
        )
        if (
            (principal not in allowed and not shared_parent_service)
            or rule.get("Type") != "Allow"
            or rule.get("Inherited") is not False
            or not isinstance(rights, int)
            or isinstance(rights, bool)
        ):
            raise RuntimeError("secret file contains an unsafe ACL entry")
        if (
            scope == "file"
            and principal == r"NT AUTHORITY\SYSTEM".casefold()
            and rights & READ_DATA_RIGHT
        ):
            supervisor_read = True
        if (
            identity
            and (principal == expanded_identity.casefold() or shared_parent_service)
            and rights & WRITE_CAPABLE_RIGHTS_MASK
        ):
            raise RuntimeError("service identity has write-capable secret ACL rights")
        if (
            identity
            and scope == "file"
            and principal == expanded_identity.casefold()
            and rights & READ_DATA_RIGHT
        ):
            service_read = True
    if not supervisor_read:
        raise RuntimeError("secret file does not grant supervisor read access")
    if not service_read:
        raise RuntimeError(
            "secret file does not grant the service identity read access"
        )


class RestartBudget:
    def __init__(self, policy: RestartPolicy) -> None:
        self._policy = policy
        self._attempts: deque[float] = deque()

    def record_failure(self, now: float) -> RestartDecision:
        cutoff = now - self._policy.window_seconds
        while self._attempts and self._attempts[0] <= cutoff:
            self._attempts.popleft()
        if len(self._attempts) >= self._policy.maximum_restarts:
            return RestartDecision(False, None, len(self._attempts))
        self._attempts.append(now)
        delay_index = min(
            len(self._attempts) - 1, len(self._policy.backoff_seconds) - 1
        )
        return RestartDecision(
            True, self._policy.backoff_seconds[delay_index], len(self._attempts)
        )


class SingleInstance:
    """ACL-protected, machine-wide named mutex."""

    def __init__(
        self,
        deployment_id: str,
        adapter: Win32AdapterProtocol,
        *,
        sddl: str = SUPERVISOR_MUTEX_SDDL,
    ) -> None:
        if SAFE_DEPLOYMENT_ID.fullmatch(deployment_id) is None:
            raise RuntimeError("deployment ID is unsafe for a global object name")
        self.name = f"Global\\LocalRagSupervisor-{deployment_id}"
        self._adapter = adapter
        self._sddl = sddl
        self._handle: int | None = None

    def __enter__(self) -> SingleInstance:
        handle, already_exists = self._adapter.create_mutex(self.name, self._sddl)
        if already_exists:
            self._adapter.close_handle(handle)
            raise RuntimeError(
                "another machine-wide supervisor instance owns this deployment"
            )
        self._handle = handle
        return self

    def __exit__(self, *_: object) -> None:
        if self._handle is not None:
            self._adapter.close_handle(self._handle)
            self._handle = None


class JobObject:
    """Global Job Object whose close terminates all assigned descendants."""

    def __init__(
        self,
        deployment_id: str,
        adapter: Win32AdapterProtocol,
        *,
        sddl: str = SUPERVISOR_JOB_SDDL,
    ) -> None:
        if SAFE_DEPLOYMENT_ID.fullmatch(deployment_id) is None:
            raise RuntimeError("deployment ID is unsafe for a global object name")
        self.name = f"Global\\LocalRagSupervisorJob-{deployment_id}"
        self._adapter = adapter
        self._sddl = sddl
        self._handle: int | None = None

    def __enter__(self) -> JobObject:
        self._handle = self._adapter.create_job(self.name, self._sddl)
        return self

    def assign(self, process: ManagedProcess) -> None:
        if self._handle is None:
            raise RuntimeError("job object is not open")
        self._adapter.assign_to_job(self._handle, process.process_handle)

    def __exit__(self, *_: object) -> None:
        if self._handle is not None:
            self._adapter.close_handle(self._handle)
            self._handle = None


class _SecurityAttributes(ctypes.Structure):
    _fields_ = [
        ("nLength", wintypes.DWORD),
        ("lpSecurityDescriptor", ctypes.c_void_p),
        ("bInheritHandle", wintypes.BOOL),
    ]


class _StartupInfo(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("lpReserved", wintypes.LPWSTR),
        ("lpDesktop", wintypes.LPWSTR),
        ("lpTitle", wintypes.LPWSTR),
        ("dwX", wintypes.DWORD),
        ("dwY", wintypes.DWORD),
        ("dwXSize", wintypes.DWORD),
        ("dwYSize", wintypes.DWORD),
        ("dwXCountChars", wintypes.DWORD),
        ("dwYCountChars", wintypes.DWORD),
        ("dwFillAttribute", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("wShowWindow", wintypes.WORD),
        ("cbReserved2", wintypes.WORD),
        ("lpReserved2", ctypes.POINTER(ctypes.c_byte)),
        ("hStdInput", wintypes.HANDLE),
        ("hStdOutput", wintypes.HANDLE),
        ("hStdError", wintypes.HANDLE),
    ]


class _ProcessInformation(ctypes.Structure):
    _fields_ = [
        ("hProcess", wintypes.HANDLE),
        ("hThread", wintypes.HANDLE),
        ("dwProcessId", wintypes.DWORD),
        ("dwThreadId", wintypes.DWORD),
    ]


class _BasicLimitInformation(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_int64),
        ("PerJobUserTimeLimit", ctypes.c_int64),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class _IoCounters(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_uint64),
        ("WriteOperationCount", ctypes.c_uint64),
        ("OtherOperationCount", ctypes.c_uint64),
        ("ReadTransferCount", ctypes.c_uint64),
        ("WriteTransferCount", ctypes.c_uint64),
        ("OtherTransferCount", ctypes.c_uint64),
    ]


class _ExtendedLimitInformation(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _BasicLimitInformation),
        ("IoInfo", _IoCounters),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


class Win32Adapter:
    """Typed Win32 calls used only with an installer-verified manifest."""

    ERROR_ALREADY_EXISTS = 183
    JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
    JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
    CREATE_SUSPENDED = 0x00000004
    CREATE_UNICODE_ENVIRONMENT = 0x00000400
    CREATE_NO_WINDOW = 0x08000000
    LOGON32_LOGON_SERVICE = 5
    LOGON32_PROVIDER_DEFAULT = 0
    MUTEX_ALL_ACCESS = 0x001F0001
    STILL_ACTIVE = 259
    WAIT_OBJECT_0 = 0
    WAIT_TIMEOUT = 258
    SDDL_REVISION_1 = 1

    def __init__(self) -> None:
        if os.name != "nt":
            raise RuntimeError("the supervisor runtime is Windows-only")
        self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self.advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
        self._declare_signatures()

    def _declare_signatures(self) -> None:
        self.advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.POINTER(wintypes.DWORD),
        ]
        self.advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = (
            wintypes.BOOL
        )
        self.kernel32.CreateMutexExW.argtypes = [
            ctypes.POINTER(_SecurityAttributes),
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
        ]
        self.kernel32.CreateMutexExW.restype = wintypes.HANDLE
        self.kernel32.CreateJobObjectW.argtypes = [
            ctypes.c_void_p,
            wintypes.LPCWSTR,
        ]
        self.kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        self.kernel32.SetInformationJobObject.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
        ]
        self.kernel32.SetInformationJobObject.restype = wintypes.BOOL
        self.kernel32.AssignProcessToJobObject.argtypes = [
            wintypes.HANDLE,
            wintypes.HANDLE,
        ]
        self.kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
        self.advapi32.LogonUserW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.LPCWSTR,
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.HANDLE),
        ]
        self.advapi32.LogonUserW.restype = wintypes.BOOL
        self.advapi32.CreateProcessAsUserW.argtypes = [
            wintypes.HANDLE,
            wintypes.LPCWSTR,
            wintypes.LPWSTR,
            ctypes.c_void_p,
            ctypes.c_void_p,
            wintypes.BOOL,
            wintypes.DWORD,
            ctypes.c_void_p,
            wintypes.LPCWSTR,
            ctypes.POINTER(_StartupInfo),
            ctypes.POINTER(_ProcessInformation),
        ]
        self.advapi32.CreateProcessAsUserW.restype = wintypes.BOOL
        self.kernel32.ResumeThread.argtypes = [wintypes.HANDLE]
        self.kernel32.ResumeThread.restype = wintypes.DWORD
        self.kernel32.GetExitCodeProcess.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(wintypes.DWORD),
        ]
        self.kernel32.GetExitCodeProcess.restype = wintypes.BOOL
        self.kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
        self.kernel32.TerminateProcess.restype = wintypes.BOOL
        self.kernel32.WaitForSingleObject.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
        ]
        self.kernel32.WaitForSingleObject.restype = wintypes.DWORD
        self.kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        self.kernel32.CloseHandle.restype = wintypes.BOOL
        self.kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        self.kernel32.LocalFree.restype = ctypes.c_void_p

    def create_mutex(self, name: str, sddl: str) -> tuple[int, bool]:
        descriptor = ctypes.c_void_p()
        if not self.advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW(
            sddl,
            self.SDDL_REVISION_1,
            ctypes.byref(descriptor),
            None,
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        attributes = _SecurityAttributes(
            ctypes.sizeof(_SecurityAttributes), descriptor, False
        )
        try:
            ctypes.set_last_error(0)
            handle = self.kernel32.CreateMutexExW(
                ctypes.byref(attributes), name, 0, self.MUTEX_ALL_ACCESS
            )
            last_error = ctypes.get_last_error()
            if not handle:
                raise ctypes.WinError(last_error)
            return int(handle), last_error == self.ERROR_ALREADY_EXISTS
        finally:
            self.kernel32.LocalFree(descriptor)

    def create_job(self, name: str, sddl: str) -> int:
        descriptor = ctypes.c_void_p()
        if not self.advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW(
            sddl,
            self.SDDL_REVISION_1,
            ctypes.byref(descriptor),
            None,
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        attributes = _SecurityAttributes(
            ctypes.sizeof(_SecurityAttributes), descriptor, False
        )
        try:
            handle = self.kernel32.CreateJobObjectW(ctypes.byref(attributes), name)
            if not handle:
                raise ctypes.WinError(ctypes.get_last_error())
        finally:
            self.kernel32.LocalFree(descriptor)
        limits = _ExtendedLimitInformation()
        limits.BasicLimitInformation.LimitFlags = (
            self.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        )
        if not self.kernel32.SetInformationJobObject(
            handle,
            self.JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
            ctypes.byref(limits),
            ctypes.sizeof(limits),
        ):
            error = ctypes.get_last_error()
            self.close_handle(int(handle))
            raise ctypes.WinError(error)
        return int(handle)

    def assign_to_job(self, job_handle: int, process_handle: int) -> None:
        if not self.kernel32.AssignProcessToJobObject(job_handle, process_handle):
            raise ctypes.WinError(ctypes.get_last_error())

    def logon_service(self, identity: str, password: str) -> int:
        if not identity.startswith(".\\"):
            raise RuntimeError("service identity must be a dedicated local account")
        token = wintypes.HANDLE()
        if not self.advapi32.LogonUserW(
            identity[2:],
            ".",
            password,
            self.LOGON32_LOGON_SERVICE,
            self.LOGON32_PROVIDER_DEFAULT,
            ctypes.byref(token),
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        return int(token.value)

    def create_suspended(
        self,
        command: list[str],
        *,
        cwd: str,
        environment: dict[str, str],
        identity_token: int,
    ) -> ManagedProcess:
        command_line = ctypes.create_unicode_buffer(subprocess.list2cmdline(command))
        environment_block = ctypes.create_unicode_buffer(
            "\0".join(
                f"{key}={value}"
                for key, value in sorted(environment.items(), key=lambda item: item[0])
            )
            + "\0\0"
        )
        startup = _StartupInfo()
        startup.cb = ctypes.sizeof(startup)
        process_info = _ProcessInformation()
        flags = (
            self.CREATE_SUSPENDED
            | self.CREATE_UNICODE_ENVIRONMENT
            | self.CREATE_NO_WINDOW
        )
        if not self.advapi32.CreateProcessAsUserW(
            identity_token,
            None,
            command_line,
            None,
            None,
            False,
            flags,
            environment_block,
            cwd,
            ctypes.byref(startup),
            ctypes.byref(process_info),
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        return ManagedProcess(
            int(process_info.hProcess),
            int(process_info.hThread),
            int(process_info.dwProcessId),
        )

    def resume(self, process: ManagedProcess) -> None:
        if self.kernel32.ResumeThread(process.thread_handle) == 0xFFFFFFFF:
            raise ctypes.WinError(ctypes.get_last_error())
        self.close_handle(process.thread_handle)
        process.thread_handle = 0

    def poll(self, process: ManagedProcess) -> int | None:
        exit_code = wintypes.DWORD()
        if not self.kernel32.GetExitCodeProcess(
            process.process_handle, ctypes.byref(exit_code)
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        return None if exit_code.value == self.STILL_ACTIVE else int(exit_code.value)

    def terminate(self, process: ManagedProcess) -> None:
        if process.process_handle and not self.kernel32.TerminateProcess(
            process.process_handle, 1
        ):
            raise ctypes.WinError(ctypes.get_last_error())

    def wait(self, process: ManagedProcess, timeout_milliseconds: int) -> bool:
        result = self.kernel32.WaitForSingleObject(
            process.process_handle, timeout_milliseconds
        )
        if result == self.WAIT_OBJECT_0:
            return True
        if result == self.WAIT_TIMEOUT:
            return False
        raise ctypes.WinError(ctypes.get_last_error())

    def close_process(self, process: ManagedProcess) -> None:
        if process.thread_handle:
            self.close_handle(process.thread_handle)
            process.thread_handle = 0
        if process.process_handle:
            self.close_handle(process.process_handle)
            process.process_handle = 0

    def close_handle(self, handle: int) -> None:
        if handle and not self.kernel32.CloseHandle(handle):
            raise ctypes.WinError(ctypes.get_last_error())


class Supervisor:
    """Foreground child runtime hosted by the RagSupervisor SCM service."""

    def __init__(
        self,
        services: tuple[Service, ...],
        policy: RestartPolicy,
        *,
        adapter: Win32AdapterProtocol | None = None,
        sleeper: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        acl_validator: EnvironmentFileValidator = validate_windows_secret_acl,
        readiness_checker: Callable[
            [Service, ManagedProcess, dict[str, str], Win32AdapterProtocol], bool
        ]
        | None = None,
        listener_owner_checker: Callable[[str, int, int], bool] | None = None,
        startup_diagnostic_path: Path | None = None,
    ) -> None:
        self._services = services
        self._policy = policy
        self._adapter = adapter
        self._sleeper = sleeper
        self._clock = clock
        self._acl_validator = acl_validator
        self._readiness_checker = readiness_checker or self._check_readiness
        self._listener_owner_checker = (
            listener_owner_checker or windows_tcp_listener_owned_by
        )
        self._startup_diagnostic_path = startup_diagnostic_path
        self._retained_processes: list[ManagedProcess] = []

    def run(self, deployment_id: str) -> int:
        adapter = self._adapter or Win32Adapter()
        remove_startup_diagnostic(self._startup_diagnostic_path)
        processes: dict[str, ManagedProcess] = {}
        budgets = {
            service.name: RestartBudget(self._policy) for service in self._services
        }
        try:
            with (
                SingleInstance(deployment_id, adapter),
                JobObject(deployment_id, adapter) as job,
            ):
                ordered = self._ordered_services()
                while True:
                    startup_failure: tuple[Service, BaseException] | None = None
                    for service in ordered:
                        try:
                            processes[service.name] = self._start(service, adapter, job)
                        except BaseException as exc:
                            startup_failure = (service, exc)
                            break
                    if startup_failure is not None:
                        service, startup_error = startup_failure
                        write_startup_diagnostic(
                            self._startup_diagnostic_path,
                            service.name,
                            startup_error,
                        )
                        cascade_error = self._stop_cascade(processes, adapter)
                        if cascade_error is not None:
                            raise ProcessCleanupError(
                                ManagedProcess(0, 0, 0),
                                start_error=startup_error,
                                cleanup_error=cascade_error,
                            ) from cascade_error
                        decision = budgets[service.name].record_failure(self._clock())
                        if not decision.allowed:
                            raise RuntimeError(
                                f"{service.name} exhausted its startup restart budget"
                            ) from startup_error
                        self._sleeper(decision.delay_seconds or 0)
                        continue
                    remove_startup_diagnostic(self._startup_diagnostic_path)
                    failed: tuple[Service, int] | None = None
                    while failed is None:
                        for service in ordered:
                            process = processes[service.name]
                            code = adapter.poll(process)
                            if code is not None:
                                failed = (service, code)
                                break
                        if failed is None:
                            self._sleeper(0.25)
                    service, code = failed
                    cascade_error = self._stop_cascade(processes, adapter)
                    if cascade_error is not None:
                        raise cascade_error
                    decision = budgets[service.name].record_failure(self._clock())
                    if not decision.allowed:
                        return code or 1
                    self._sleeper(decision.delay_seconds or 0)
        finally:
            try:
                cascade_error = self._stop_cascade(processes, adapter)
                if cascade_error is not None:
                    raise cascade_error
            finally:
                self.reap_retained(adapter)
        return 0

    def _ordered_services(self) -> tuple[Service, ...]:
        pending = {service.name: service for service in self._services}
        ordered: list[Service] = []
        while pending:
            ready = [
                service
                for service in pending.values()
                if all(dependency not in pending for dependency in service.dependencies)
            ]
            if not ready:
                raise RuntimeError("service dependencies contain a cycle")
            for service in sorted(ready, key=lambda item: item.name):
                ordered.append(service)
                pending.pop(service.name)
        return tuple(ordered)

    def _start(
        self,
        service: Service,
        adapter: Win32AdapterProtocol,
        job: JobObject,
    ) -> ManagedProcess:
        environment, _ = load_service_environment(
            service,
            acl_validator=self._acl_validator,
        )
        password = load_identity_password(
            service,
            acl_validator=self._acl_validator,
        )
        identity_token = adapter.logon_service(service.identity, password)
        try:
            if service.name == "caddy":
                self._validate_caddy(service, environment, identity_token, adapter, job)
            process = adapter.create_suspended(
                [service.executable, *service.arguments],
                cwd=service.working_directory,
                environment=environment,
                identity_token=identity_token,
            )
            try:
                job.assign(process)
                adapter.resume(process)
                if not self._readiness_checker(service, process, environment, adapter):
                    raise ChildReadinessError(service.name, adapter.poll(process))
                return process
            except BaseException as start_error:
                cleanup_error = self._terminate_and_close(process, adapter)
                if cleanup_error is not None:
                    self._retained_processes.append(process)
                    raise ProcessCleanupError(
                        process,
                        start_error=start_error,
                        cleanup_error=cleanup_error,
                    ) from cleanup_error
                raise
        finally:
            password = ""
            adapter.close_handle(identity_token)

    def _validate_caddy(
        self,
        service: Service,
        environment: dict[str, str],
        identity_token: int,
        adapter: Win32AdapterProtocol,
        job: JobObject,
    ) -> None:
        try:
            config_index = service.arguments.index("--config")
            config_path = service.arguments[config_index + 1]
        except (ValueError, IndexError) as exc:
            raise RuntimeError("Caddy service must pin one --config path") from exc
        validation = adapter.create_suspended(
            [service.executable, "validate", "--config", config_path],
            cwd=service.working_directory,
            environment=environment,
            identity_token=identity_token,
        )
        try:
            job.assign(validation)
            adapter.resume(validation)
            if not adapter.wait(validation, 30000):
                raise TimeoutError("Caddy validation exceeded 30 seconds")
            code = adapter.poll(validation)
            if code != 0:
                raise RuntimeError(f"Caddy validation failed with exit code {code}")
            adapter.close_process(validation)
        except BaseException as validation_error:
            cleanup_error = self._terminate_and_close(validation, adapter)
            if cleanup_error is not None:
                self._retained_processes.append(validation)
                raise ProcessCleanupError(
                    validation,
                    start_error=validation_error,
                    cleanup_error=cleanup_error,
                ) from cleanup_error
            raise

    def _check_readiness(
        self,
        service: Service,
        process: ManagedProcess,
        environment: dict[str, str],
        adapter: Win32AdapterProtocol,
    ) -> bool:
        deadline = self._clock() + service.readiness_timeout_seconds
        if service.readiness_url is None:
            if service.listen_host is None or service.listen_port is None:
                self._sleeper(min(1.0, service.readiness_timeout_seconds))
                return adapter.poll(process) is None
            addresses = self._listener_addresses(service, environment)
            while self._clock() < deadline:
                if adapter.poll(process) is not None:
                    return False
                if (
                    all(
                        self._listener_owner_checker(
                            address,
                            service.listen_port,
                            process.process_id,
                        )
                        for address in addresses
                    )
                    and adapter.poll(process) is None
                ):
                    return True
                self._sleeper(0.25)
            return False
        headers: dict[str, str] = {}
        if service.readiness_token_environment is not None:
            headers["Authorization"] = (
                "Bearer " + environment[service.readiness_token_environment]
            )
        if service.name == "api":
            canonical_host = environment.get("CANONICAL_HOST")
            if canonical_host is None:
                raise RuntimeError("api readiness requires the canonical Host header")
            headers["Host"] = canonical_host
        context: ssl.SSLContext | None = None
        if service.readiness_url.startswith("https://"):
            context = ssl.create_default_context(
                cafile=environment.get("RAG_API_CLIENT_CA_PATH")
            )
            context.load_cert_chain(
                environment["RAG_SUPERVISOR_API_CLIENT_CERT_PATH"],
                environment["RAG_SUPERVISOR_API_CLIENT_KEY_PATH"],
            )
        while self._clock() < deadline:
            if adapter.poll(process) is not None:
                return False
            try:
                request = urllib.request.Request(
                    service.readiness_url,
                    headers=headers,
                    method="GET",
                )
                with urllib.request.urlopen(
                    request,
                    timeout=2,
                    context=context,
                ) as response:
                    if 200 <= response.status < 300:
                        if (
                            service.listen_host is not None
                            and service.listen_port is not None
                            and self._listener_owner_checker(
                                service.listen_host,
                                service.listen_port,
                                process.process_id,
                            )
                            and adapter.poll(process) is None
                        ):
                            return True
            except (OSError, urllib.error.URLError):
                pass
            self._sleeper(0.25)
        return False

    @staticmethod
    def _listener_addresses(
        service: Service,
        environment: dict[str, str],
    ) -> tuple[str, ...]:
        if service.name == "caddy" and service.listen_host == "0.0.0.0":
            addresses = tuple(
                environment[key]
                for key in ("RAG_LAN_IPV4", "RAG_LAN_IPV6")
                if environment.get(key)
            )
            if len(addresses) != 2:
                raise RuntimeError("Caddy requires exact IPv4 and IPv6 listener values")
            if len(set(addresses)) != 2:
                raise RuntimeError("Caddy LAN listener addresses must be distinct")
            try:
                parsed = tuple(ipaddress.ip_address(address) for address in addresses)
            except ValueError as exc:
                raise RuntimeError(
                    "Caddy LAN listener values must be literal IP addresses"
                ) from exc
            if parsed[0].version != 4 or parsed[1].version != 6:
                raise RuntimeError("Caddy requires IPv4 then IPv6 LAN listener values")
            if any(
                not address.is_private
                or address.is_unspecified
                or address.is_loopback
                or address.is_link_local
                or address.is_multicast
                for address in parsed
            ):
                raise RuntimeError(
                    "Caddy LAN listener values must be private non-wildcard "
                    "LAN addresses"
                )
            return addresses
        if service.listen_host is None:
            return ()
        return (service.listen_host,)

    def _stop_cascade(
        self,
        processes: dict[str, ManagedProcess],
        adapter: Win32AdapterProtocol,
    ) -> BaseException | None:
        last_error: BaseException | None = None
        for service in reversed(self._ordered_services()):
            process = processes.pop(service.name, None)
            if process is None:
                continue
            error = self._terminate_and_close(process, adapter)
            if error is not None:
                self._retained_processes.append(process)
                last_error = error
        return last_error

    def reap_retained(self, adapter: Win32AdapterProtocol | None = None) -> None:
        selected_adapter = adapter or self._adapter
        if selected_adapter is None:
            return
        still_retained: list[ManagedProcess] = []
        errors: list[BaseException] = []
        for process in self._retained_processes:
            error = self._terminate_and_close(process, selected_adapter)
            if error is not None:
                still_retained.append(process)
                errors.append(error)
        self._retained_processes = still_retained
        if errors:
            raise ProcessCleanupError(
                still_retained[0],
                start_error=errors[0],
                cleanup_error=errors[-1],
            )

    def _terminate_and_close(
        self,
        process: ManagedProcess,
        adapter: Win32AdapterProtocol,
    ) -> BaseException | None:
        last_error: BaseException | None = None
        terminated = False
        for attempt in range(3):
            try:
                if adapter.poll(process) is not None:
                    terminated = True
                    break
            except BaseException as exc:
                last_error = exc
            try:
                adapter.terminate(process)
                if not adapter.wait(process, 5000):
                    raise TimeoutError(
                        "process did not signal termination in 5 seconds"
                    )
                if adapter.poll(process) is None:
                    raise RuntimeError(
                        "process signaled termination but still reports active"
                    )
                terminated = True
                break
            except BaseException as exc:
                last_error = exc
                if attempt < 2:
                    self._sleeper(0.05)
        if not terminated:
            return last_error or RuntimeError(
                "suspended child termination could not be confirmed"
            )
        try:
            adapter.close_process(process)
        except BaseException as exc:
            return exc
        return None
