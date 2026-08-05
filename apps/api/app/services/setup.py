import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from argon2 import PasswordHasher
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.security.identity import (
    normalize_display_name,
    normalize_username,
    validate_permanent_password,
)
from app.security.passwords import calibrate_password_hasher
from app.security.tokens import hash_opaque_token, issue_opaque_token


class SetupError(Exception):
    pass


class SetupUnavailable(SetupError):
    pass


class SetupCodeRejected(SetupError):
    pass


class SetupCodeExpired(SetupError):
    pass


class SetupCodeLocked(SetupError):
    pass


class SetupState(StrEnum):
    REQUIRED = "setup_required"
    COMPLETE = "setup_complete"


@dataclass(frozen=True, slots=True)
class SetupStatus:
    state: SetupState
    code_expires_at: datetime | None
    attempts_remaining: int


@dataclass(frozen=True, slots=True)
class SetupChallenge:
    token: str
    expires_at: datetime


class SetupGateway(Protocol):
    async def status(self) -> SetupStatus: ...

    async def verify_code(
        self,
        *,
        code_hash: str,
        challenge_hash: str,
        challenge_expires_at: datetime,
    ) -> str: ...

    async def complete(
        self,
        *,
        challenge_hash: str,
        username: str,
        display_name: str,
        password_hash: str,
    ) -> UUID: ...


class DatabaseSetupGateway:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def status(self) -> SetupStatus:
        try:
            async with self._session_factory() as session, session.begin():
                row = (
                    await session.execute(
                        text(
                            "SELECT setup_state, code_expires_at, "
                            "attempts_remaining FROM v8_setup_status()"
                        )
                    )
                ).one()
            return SetupStatus(
                state=SetupState(row.setup_state),
                code_expires_at=row.code_expires_at,
                attempts_remaining=row.attempts_remaining,
            )
        except (SQLAlchemyError, ValueError) as exc:
            raise SetupUnavailable("owner setup is unavailable") from exc

    async def verify_code(
        self,
        *,
        code_hash: str,
        challenge_hash: str,
        challenge_expires_at: datetime,
    ) -> str:
        try:
            async with self._session_factory() as session, session.begin():
                outcome = await session.scalar(
                    text(
                        "SELECT v8_verify_setup_code("
                        ":code_hash, :challenge_hash, :challenge_expires_at)"
                    ),
                    {
                        "code_hash": code_hash,
                        "challenge_hash": challenge_hash,
                        "challenge_expires_at": challenge_expires_at,
                    },
                )
            allowed = {"accepted", "rejected", "expired", "locked", "unavailable"}
            if outcome not in allowed:
                raise SetupUnavailable("owner setup returned an invalid outcome")
            return str(outcome)
        except SQLAlchemyError as exc:
            raise SetupUnavailable("owner setup is unavailable") from exc

    async def complete(
        self,
        *,
        challenge_hash: str,
        username: str,
        display_name: str,
        password_hash: str,
    ) -> UUID:
        try:
            async with self._session_factory() as session, session.begin():
                owner_id = await session.scalar(
                    text(
                        "SELECT v8_complete_owner_setup("
                        ":challenge_hash, :username, :display_name, :password_hash)"
                    ),
                    {
                        "challenge_hash": challenge_hash,
                        "username": username,
                        "display_name": display_name,
                        "password_hash": password_hash,
                    },
                )
            if not isinstance(owner_id, UUID):
                raise SetupUnavailable("owner setup returned an invalid identity")
            return owner_id
        except SetupUnavailable:
            raise
        except SQLAlchemyError as exc:
            raise SetupUnavailable("owner setup is unavailable") from exc


class SetupService:
    def __init__(
        self,
        gateway: SetupGateway,
        *,
        challenge_ttl_seconds: int = 10 * 60,
        maximum_hash_concurrency: int = 2,
        password_hasher: PasswordHasher | None = None,
    ) -> None:
        if challenge_ttl_seconds != 10 * 60:
            raise ValueError("setup challenge lifetime must be 10 minutes")
        if maximum_hash_concurrency < 1:
            raise ValueError("maximum hash concurrency must be positive")
        self._gateway = gateway
        self._challenge_ttl = timedelta(seconds=challenge_ttl_seconds)
        self._hash_semaphore = asyncio.Semaphore(maximum_hash_concurrency)
        self._hasher_lock = asyncio.Lock()
        self._password_hasher = password_hasher

    async def status(self) -> SetupStatus:
        return await self._gateway.status()

    async def challenge(self, code: str) -> SetupChallenge:
        if not 8 <= len(code) <= 256:
            raise SetupCodeRejected
        challenge = issue_opaque_token()
        expires_at = datetime.now(UTC) + self._challenge_ttl
        outcome = await self._gateway.verify_code(
            code_hash=hash_opaque_token(code),
            challenge_hash=challenge.digest,
            challenge_expires_at=expires_at,
        )
        if outcome == "accepted":
            return SetupChallenge(token=challenge.plaintext, expires_at=expires_at)
        if outcome == "expired":
            raise SetupCodeExpired
        if outcome == "locked":
            raise SetupCodeLocked
        if outcome == "unavailable":
            raise SetupUnavailable
        raise SetupCodeRejected

    async def complete_owner(
        self,
        *,
        challenge_token: str,
        username: str,
        display_name: str,
        password: str,
    ) -> UUID:
        if not 8 <= len(challenge_token) <= 256:
            raise SetupUnavailable
        canonical_username = normalize_username(username)
        canonical_display_name = normalize_display_name(display_name)
        permanent_password = validate_permanent_password(password)
        async with self._hash_semaphore:
            if self._password_hasher is None:
                async with self._hasher_lock:
                    if self._password_hasher is None:
                        self._password_hasher = await asyncio.to_thread(
                            calibrate_password_hasher
                        )
            password_hash = await asyncio.to_thread(
                self._password_hasher.hash, permanent_password
            )
        return await self._gateway.complete(
            challenge_hash=hash_opaque_token(challenge_token),
            username=canonical_username,
            display_name=canonical_display_name,
            password_hash=password_hash,
        )
