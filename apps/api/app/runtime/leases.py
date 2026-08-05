import time
from collections.abc import Callable
from dataclasses import dataclass


class LeaseConflictError(RuntimeError):
    pass


class StaleLeaseError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class Lease:
    resource_id: str
    owner_id: str
    fencing_token: int
    expires_at: float


class LeaseRegistry:
    """In-memory lease semantics for adapters backed by durable atomic storage."""

    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._leases: dict[str, Lease] = {}
        self._next_fence: dict[str, int] = {}

    def claim(self, resource_id: str, owner_id: str, ttl_seconds: float) -> Lease:
        if not resource_id or not owner_id:
            raise ValueError("resource_id and owner_id are required")
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        now = self._clock()
        current = self._leases.get(resource_id)
        if current is not None and current.expires_at > now:
            raise LeaseConflictError("resource has an active lease")
        token = self._next_fence.get(resource_id, 0) + 1
        self._next_fence[resource_id] = token
        lease = Lease(resource_id, owner_id, token, now + ttl_seconds)
        self._leases[resource_id] = lease
        return lease

    def heartbeat(self, lease: Lease, ttl_seconds: float) -> Lease:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        self.validate(lease)
        renewed = Lease(
            lease.resource_id,
            lease.owner_id,
            lease.fencing_token,
            self._clock() + ttl_seconds,
        )
        self._leases[lease.resource_id] = renewed
        return renewed

    def validate(self, lease: Lease) -> None:
        current = self._leases.get(lease.resource_id)
        if (
            current is None
            or current.owner_id != lease.owner_id
            or current.fencing_token != lease.fencing_token
            or current.expires_at <= self._clock()
        ):
            raise StaleLeaseError("lease is absent, expired, or fenced")

    def release(self, lease: Lease) -> None:
        self.validate(lease)
        del self._leases[lease.resource_id]
