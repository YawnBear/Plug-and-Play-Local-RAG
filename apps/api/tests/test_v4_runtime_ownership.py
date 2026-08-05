from pathlib import Path

import pytest

from app.runtime import ownership
from app.runtime.leases import LeaseConflictError, LeaseRegistry, StaleLeaseError
from app.runtime.ownership import OwnershipError, SingleInstanceOwnership


class Clock:
    value = 100.0

    def __call__(self) -> float:
        return self.value


def test_lease_expiry_increments_fence_and_rejects_stale_owner() -> None:
    clock = Clock()
    registry = LeaseRegistry(clock=clock)
    first = registry.claim("job-1", "worker-a", 10)

    with pytest.raises(LeaseConflictError):
        registry.claim("job-1", "worker-b", 10)

    clock.value = 111
    second = registry.claim("job-1", "worker-b", 10)
    assert second.fencing_token == first.fencing_token + 1

    with pytest.raises(StaleLeaseError):
        registry.validate(first)
    with pytest.raises(StaleLeaseError):
        registry.heartbeat(first, 10)
    with pytest.raises(StaleLeaseError):
        registry.release(first)

    renewed = registry.heartbeat(second, 20)
    assert renewed.fencing_token == second.fencing_token
    registry.validate(renewed)


def test_single_instance_uses_process_lock_and_releases_after_exit(
    tmp_path: Path,
) -> None:
    lock_path = (tmp_path / "coordinator.lock").resolve()
    first = SingleInstanceOwnership.acquire(lock_path)

    with pytest.raises(OwnershipError, match="another process"):
        SingleInstanceOwnership.acquire(lock_path)

    first.release()
    second = SingleInstanceOwnership.acquire(
        lock_path,
        process_alive=lambda _pid: False,
        clock=lambda: 123.0,
    )
    assert second.acquired_at == 123.0
    assert second.previous_owner_alive is False
    second.release()


def test_single_instance_rejects_relative_path() -> None:
    with pytest.raises(ValueError, match="absolute"):
        SingleInstanceOwnership.acquire(Path("relative.lock"))


def test_windows_liveness_uses_non_mutating_process_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import ctypes

    class Function:
        def __init__(self, operation):
            self.operation = operation
            self.argtypes = None
            self.restype = None

        def __call__(self, *args):
            return self.operation(*args)

    calls: list[tuple[str, object]] = []

    class Kernel32:
        def __init__(self) -> None:
            self.OpenProcess = Function(self.open_process)
            self.GetExitCodeProcess = Function(self.get_exit_code)
            self.WaitForSingleObject = Function(self.wait)
            self.CloseHandle = Function(self.close)

        @staticmethod
        def open_process(access: int, inherit: bool, pid: int) -> int:
            calls.append(("open", (access, inherit, pid)))
            return 123

        @staticmethod
        def get_exit_code(handle: int, output: object) -> bool:
            calls.append(("exit", handle))
            output._obj.value = 259
            return True

        @staticmethod
        def wait(handle: int, timeout: int) -> int:
            calls.append(("wait", (handle, timeout)))
            return 258

        @staticmethod
        def close(handle: int) -> bool:
            calls.append(("close", handle))
            return True

    monkeypatch.setattr(ownership.sys, "platform", "win32")
    monkeypatch.setattr(ctypes, "WinDLL", lambda *_args, **_kwargs: Kernel32())
    monkeypatch.setattr(
        ownership.os,
        "kill",
        lambda *_args: pytest.fail("Windows liveness must not call os.kill"),
    )

    assert ownership._process_alive(42) is True
    assert calls == [
        ("open", (0x101000, False, 42)),
        ("exit", 123),
        ("wait", (123, 0)),
        ("close", 123),
    ]
