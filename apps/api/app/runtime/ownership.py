import json
import os
import sys
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO


class OwnershipError(RuntimeError):
    pass


def _process_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        return _windows_process_alive(pid)
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _windows_process_alive(pid: int) -> bool:
    import ctypes
    from ctypes import wintypes

    process_query_limited_information = 0x1000
    synchronize = 0x00100000
    error_access_denied = 5
    error_invalid_parameter = 87
    still_active = 259
    wait_object_0 = 0
    wait_timeout = 258
    wait_failed = 0xFFFFFFFF

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.GetExitCodeProcess.argtypes = (
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.DWORD),
    )
    kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    kernel32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel32.CloseHandle.restype = wintypes.BOOL

    handle = kernel32.OpenProcess(
        process_query_limited_information | synchronize, False, pid
    )
    if not handle:
        error = ctypes.get_last_error()
        if error == error_invalid_parameter:
            return False
        if error == error_access_denied:
            return True
        raise ctypes.WinError(error)

    pending_error: BaseException | None = None
    try:
        exit_code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            raise ctypes.WinError(ctypes.get_last_error())
        wait_result = int(kernel32.WaitForSingleObject(handle, 0))
        if wait_result == wait_failed:
            raise ctypes.WinError(ctypes.get_last_error())
        if wait_result not in {wait_object_0, wait_timeout}:
            raise OSError(f"unexpected process wait result: {wait_result}")
        return wait_result == wait_timeout and exit_code.value == still_active
    except BaseException as exc:
        pending_error = exc
        raise
    finally:
        if not kernel32.CloseHandle(handle) and pending_error is None:
            raise ctypes.WinError(ctypes.get_last_error())


def _lock(handle: BinaryIO) -> None:
    if sys.platform == "win32":
        import msvcrt

        handle.seek(0)
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            raise OwnershipError("another process owns this instance") from exc
    else:
        import fcntl

        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise OwnershipError("another process owns this instance") from exc


def _unlock(handle: BinaryIO) -> None:
    if sys.platform == "win32":
        import msvcrt

        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@dataclass(slots=True)
class SingleInstanceOwnership:
    path: Path
    instance_id: str
    pid: int
    acquired_at: float
    previous_owner_alive: bool | None
    _handle: BinaryIO | None

    @classmethod
    def acquire(
        cls,
        path: Path,
        *,
        process_alive: Callable[[int], bool] = _process_alive,
        clock: Callable[[], float] = time.time,
    ) -> "SingleInstanceOwnership":
        path = path.expanduser()
        if not path.is_absolute():
            raise ValueError("ownership path must be absolute")
        path.parent.mkdir(parents=True, exist_ok=True)
        handle = path.open("a+b")
        try:
            if path.stat().st_size == 0:
                handle.write(b"\0")
                handle.flush()
            _lock(handle)
            previous_pid = _read_pid(handle)
            previous_alive = (
                process_alive(previous_pid) if previous_pid is not None else None
            )
            instance_id = uuid.uuid4().hex
            acquired_at = clock()
            payload = json.dumps(
                {
                    "instance_id": instance_id,
                    "pid": os.getpid(),
                    "acquired_at": acquired_at,
                },
                separators=(",", ":"),
            ).encode("utf-8")
            handle.seek(0)
            handle.truncate()
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
            return cls(
                path=path,
                instance_id=instance_id,
                pid=os.getpid(),
                acquired_at=acquired_at,
                previous_owner_alive=previous_alive,
                _handle=handle,
            )
        except BaseException:
            handle.close()
            raise

    def release(self) -> None:
        handle = self._handle
        if handle is None:
            return
        handle.seek(0)
        try:
            payload = json.loads(handle.read().decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise OwnershipError("ownership record was corrupted") from exc
        if payload.get("instance_id") != self.instance_id:
            raise OwnershipError("ownership record no longer belongs to this instance")
        _unlock(handle)
        handle.close()
        self._handle = None

    def __enter__(self) -> "SingleInstanceOwnership":
        return self

    def __exit__(self, *_args: object) -> None:
        self.release()


def _read_pid(handle: BinaryIO) -> int | None:
    handle.seek(0)
    raw = handle.read()
    if raw in {b"", b"\0"}:
        return None
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OwnershipError("ownership record is invalid") from exc
    pid = payload.get("pid")
    if type(pid) is not int or pid <= 0:
        raise OwnershipError("ownership record has an invalid pid")
    return pid
