import asyncio
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID, uuid4

import pytest
from argon2 import PasswordHasher, Type
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.security.bootstrap import issue_owner_setup_code
from app.security.tokens import hash_opaque_token
from app.services.setup import (
    SetupCodeExpired,
    SetupCodeLocked,
    SetupCodeRejected,
    SetupService,
    SetupState,
    SetupStatus,
    SetupUnavailable,
)


class _Gateway:
    def __init__(self, outcome: str = "accepted") -> None:
        self.outcome = outcome
        self.verified: dict[str, object] | None = None
        self.completed: dict[str, str] | None = None
        self.owner_id = uuid4()

    async def status(self) -> SetupStatus:
        return SetupStatus(SetupState.REQUIRED, datetime.now(UTC), 5)

    async def verify_code(self, **values: object) -> str:
        self.verified = values
        return self.outcome

    async def complete(self, **values: str) -> UUID:
        self.completed = values
        return self.owner_id


def _service(gateway: _Gateway) -> SetupService:
    return SetupService(
        gateway,
        password_hasher=PasswordHasher(
            time_cost=1,
            memory_cost=8192,
            parallelism=1,
            hash_len=16,
            salt_len=16,
            type=Type.ID,
        ),
    )


def test_setup_challenge_stores_only_hashes_and_has_fixed_expiry() -> None:
    gateway = _Gateway()
    before = datetime.now(UTC)
    challenge = asyncio.run(_service(gateway).challenge("private-setup-code"))

    assert gateway.verified is not None
    assert gateway.verified["code_hash"] == hash_opaque_token("private-setup-code")
    assert gateway.verified["challenge_hash"] == hash_opaque_token(challenge.token)
    assert "private-setup-code" not in repr(gateway.verified)
    assert before + timedelta(minutes=9, seconds=55) < challenge.expires_at
    assert challenge.expires_at <= before + timedelta(minutes=10, seconds=5)


@pytest.mark.parametrize(
    ("outcome", "expected"),
    [
        ("rejected", SetupCodeRejected),
        ("expired", SetupCodeExpired),
        ("locked", SetupCodeLocked),
        ("unavailable", SetupUnavailable),
    ],
)
def test_setup_challenge_maps_bounded_database_outcomes(
    outcome: str, expected: type[Exception]
) -> None:
    with pytest.raises(expected):
        asyncio.run(_service(_Gateway(outcome)).challenge("private-setup-code"))


def test_setup_owner_accepts_arbitrary_identity_and_hashes_password() -> None:
    gateway = _Gateway()
    service = _service(gateway)

    owner_id = asyncio.run(
        service.complete_owner(
            challenge_token="browser-challenge-token",
            username="Owner.One",
            display_name="  Owner One  ",
            password="fourteen-chars!",
        )
    )

    assert owner_id == gateway.owner_id
    assert gateway.completed is not None
    assert gateway.completed["username"] == "owner.one"
    assert gateway.completed["display_name"] == "Owner One"
    assert gateway.completed["challenge_hash"] == hash_opaque_token(
        "browser-challenge-token"
    )
    password_hash = gateway.completed["password_hash"]
    assert "fourteen-chars!" not in password_hash
    assert password_hash.startswith("$argon2id$")


def test_setup_rejects_invalid_identity_before_database_mutation() -> None:
    gateway = _Gateway()
    with pytest.raises(ValueError, match="username"):
        asyncio.run(
            _service(gateway).complete_owner(
                challenge_token="browser-challenge-token",
                username="invalid owner",
                display_name="Owner",
                password="fourteen-chars!",
            )
        )
    assert gateway.completed is None


class _Transaction:
    async def __aenter__(self):
        return None

    async def __aexit__(self, *_args: object):
        return None


class _IssueSession:
    def __init__(self) -> None:
        self.parameters: dict[str, object] | None = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args: object):
        return None

    def begin(self) -> _Transaction:
        return _Transaction()

    async def execute(self, _statement: object, parameters: dict[str, object]):
        self.parameters = parameters


def test_setup_code_issuer_persists_only_digest() -> None:
    session = _IssueSession()
    factory = cast(async_sessionmaker[AsyncSession], lambda: session)
    issued = asyncio.run(issue_owner_setup_code(factory))

    assert session.parameters is not None
    assert session.parameters["code_hash"] == hash_opaque_token(issued.code)
    assert issued.code not in repr(session.parameters)
    assert datetime.now(UTC) + timedelta(minutes=14) < issued.expires_at
