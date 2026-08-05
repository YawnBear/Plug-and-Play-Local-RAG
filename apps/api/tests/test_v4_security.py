import asyncio
import uuid
from typing import cast

import pytest
from argon2 import PasswordHasher
from argon2.low_level import Type
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.maintenance_cli import _parser
from app.security.actor import ActorContext, ActorRole
from app.security.bootstrap import bootstrap_first_admin
from app.security.identity import (
    normalize_display_name,
    normalize_username,
    validate_permanent_password,
)
from app.security.passwords import calibrate_password_hasher
from app.security.tokens import hash_opaque_token, issue_opaque_token


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("ExampleUser", "exampleuser"), ("a.b", "a.b"), ("a_b-c", "a_b-c")],
)
def test_username_normalization_is_the_frozen_contract(raw: str, expected: str) -> None:
    assert normalize_username(raw) == expected


@pytest.mark.parametrize(
    "invalid",
    ["ab", "a" * 33, "-abc", "abc-", "has space", "ümlaut", "a/B"],
)
def test_username_normalization_rejects_out_of_contract_values(invalid: str) -> None:
    with pytest.raises(ValueError):
        normalize_username(invalid)


def test_display_name_is_trimmed_nfkc_and_rejects_controls() -> None:
    assert normalize_display_name("  Ａｌｉｃｅ　Ｓｍｉｔｈ  ") == "Alice Smith"
    assert normalize_display_name("\ue000") == "\ue000"
    with pytest.raises(ValueError):
        normalize_display_name("Yu\u200bXian")


def test_password_policy_has_only_the_frozen_length_rule() -> None:
    assert validate_permanent_password("correct horse!") == "correct horse!"
    assert validate_permanent_password("密" * 14) == "密" * 14
    with pytest.raises(ValueError):
        validate_permanent_password("short")


def test_opaque_tokens_are_random_and_store_only_sha256_digests() -> None:
    first = issue_opaque_token()
    second = issue_opaque_token()
    assert first.plaintext != second.plaintext
    assert first.digest == hash_opaque_token(first.plaintext)
    assert len(first.digest) == 64
    assert first.plaintext not in first.digest


def test_actor_context_is_immutable_and_versions_are_positive() -> None:
    actor = ActorContext(
        user_id=uuid.uuid4(),
        role=ActorRole.ADMIN,
        authentication_version=1,
        authorization_version=1,
        session_id=uuid.uuid4(),
    )
    with pytest.raises(AttributeError):
        setattr(actor, "role", ActorRole.MEMBER)
    with pytest.raises(ValueError):
        ActorContext(
            user_id=uuid.uuid4(),
            role=ActorRole.MEMBER,
            authentication_version=0,
            authorization_version=1,
            session_id=uuid.uuid4(),
        )


def test_argon2id_hash_and_verify_path() -> None:
    hasher = PasswordHasher(
        time_cost=1,
        memory_cost=8192,
        parallelism=1,
        hash_len=16,
        salt_len=16,
        type=Type.ID,
    )
    encoded = hasher.hash("fourteen-chars!")
    assert encoded.startswith("$argon2id$")
    assert hasher.verify(encoded, "fourteen-chars!")


def test_hasher_calibration_rejects_invalid_bounds() -> None:
    with pytest.raises(ValueError):
        calibrate_password_hasher(target_seconds=0)
    with pytest.raises(ValueError):
        calibrate_password_hasher(maximum_time_cost=0)


class _ScalarSession:
    def __init__(self, admin_id: uuid.UUID) -> None:
        self.admin_id = admin_id
        self.parameters: dict[str, str] | None = None

    async def __aenter__(self) -> "_ScalarSession":
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    def begin(self) -> "_ScalarSession":
        return self

    async def scalar(self, _statement: object, parameters: dict[str, str]) -> uuid.UUID:
        self.parameters = parameters
        return self.admin_id


class _ScalarFactory:
    def __init__(self, session: _ScalarSession) -> None:
        self.session = session

    def __call__(self) -> _ScalarSession:
        return self.session


def test_interactive_bootstrap_hashes_password_before_database_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.security.bootstrap.sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("app.security.bootstrap.sys.stderr.isatty", lambda: True)
    admin_id = uuid.uuid4()
    session = _ScalarSession(admin_id)
    answers = iter(["fourteen-chars!", "fourteen-chars!"])
    identity_answers = iter(["owner.one", "Owner One"])

    def prompt(_label: str) -> str:
        return next(answers)

    hasher = PasswordHasher(
        time_cost=1,
        memory_cost=8192,
        parallelism=1,
        hash_len=16,
        salt_len=16,
        type=Type.ID,
    )

    result = asyncio.run(
        bootstrap_first_admin(
            cast(
                async_sessionmaker[AsyncSession],
                _ScalarFactory(session),
            ),
            password_hasher=hasher,
            password_prompt=prompt,
            identity_prompt=lambda _label: next(identity_answers),
        )
    )

    assert result == admin_id
    assert session.parameters is not None
    assert session.parameters["username"] == "owner.one"
    assert session.parameters["display_name"] == "Owner One"
    encoded = session.parameters["password_hash"]
    assert "fourteen-chars!" not in encoded
    assert hasher.verify(encoded, "fourteen-chars!")


def test_interactive_bootstrap_rejects_mismatched_passwords_before_database(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.security.bootstrap.sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("app.security.bootstrap.sys.stderr.isatty", lambda: True)
    session = _ScalarSession(uuid.uuid4())
    answers = iter(["fourteen-chars!", "different-value"])
    identity_answers = iter(["owner.two", "Owner Two"])

    with pytest.raises(ValueError, match="do not match"):
        asyncio.run(
            bootstrap_first_admin(
                cast(
                    async_sessionmaker[AsyncSession],
                    _ScalarFactory(session),
                ),
                password_prompt=lambda _label: next(answers),
                identity_prompt=lambda _label: next(identity_answers),
            )
        )
    assert session.parameters is None


def test_bootstrap_refuses_noninteractive_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.security.bootstrap.sys.stdin.isatty", lambda: False)
    monkeypatch.setattr("app.security.bootstrap.sys.stderr.isatty", lambda: True)
    with pytest.raises(RuntimeError, match="interactive local TTY"):
        asyncio.run(
            bootstrap_first_admin(
                cast(
                    async_sessionmaker[AsyncSession],
                    _ScalarFactory(_ScalarSession(uuid.uuid4())),
                ),
                password_prompt=lambda _label: "fourteen-chars!",
            )
        )


def test_bootstrap_cli_has_no_password_argument() -> None:
    arguments = _parser().parse_args(["--confirm-stopped", "bootstrap-admin"])
    assert arguments.command == "bootstrap-admin"
    assert arguments.confirm_stopped is True
    assert not hasattr(arguments, "password")

    setup_arguments = _parser().parse_args(["--confirm-stopped", "setup-code-issue"])
    assert setup_arguments.command == "setup-code-issue"
    assert not hasattr(setup_arguments, "code")
