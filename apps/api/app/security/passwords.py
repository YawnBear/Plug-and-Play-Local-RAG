import secrets
import time

from argon2 import PasswordHasher
from argon2.low_level import Type

_TARGET_SECONDS = 0.250
_MEMORY_COST_KIB = 64 * 1024
_PARALLELISM = 2


def calibrate_password_hasher(
    *,
    target_seconds: float = _TARGET_SECONDS,
    maximum_time_cost: int = 20,
) -> PasswordHasher:
    if target_seconds <= 0:
        raise ValueError("target_seconds must be positive")
    if maximum_time_cost < 1:
        raise ValueError("maximum_time_cost must be positive")

    sample = secrets.token_urlsafe(32)
    selected = PasswordHasher(
        time_cost=1,
        memory_cost=_MEMORY_COST_KIB,
        parallelism=_PARALLELISM,
        hash_len=32,
        salt_len=16,
        type=Type.ID,
    )
    for time_cost in range(1, maximum_time_cost + 1):
        candidate = PasswordHasher(
            time_cost=time_cost,
            memory_cost=_MEMORY_COST_KIB,
            parallelism=_PARALLELISM,
            hash_len=32,
            salt_len=16,
            type=Type.ID,
        )
        started = time.perf_counter()
        candidate.hash(sample)
        selected = candidate
        if time.perf_counter() - started >= target_seconds:
            break
    return selected
