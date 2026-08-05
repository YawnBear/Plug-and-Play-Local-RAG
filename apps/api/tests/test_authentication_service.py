import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from argon2 import PasswordHasher
from argon2.low_level import Type

from app.security.tokens import hash_opaque_token
from app.services.authentication import (
    AuthenticationService,
    BoundedPasswordVerifier,
    DatabaseAuthenticationGateway,
    InvalidCredentials,
    InvalidSession,
    PreAuthCsrf,
    UnavailableAuthenticationGateway,
)


class _Result:
    def __init__(self, row: object | None = None) -> None:
        self.row = row

    def one_or_none(self) -> object | None:
        return self.row

    def one(self) -> object:
        if self.row is None:
            raise AssertionError("expected one row")
        return self.row


class _Context:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None


class _Session(_Context):
    def __init__(self, rows: list[object | None]) -> None:
        self.rows = rows
        self.calls: list[tuple[str, dict[str, object]]] = []

    def begin(self) -> _Context:
        return _Context()

    async def execute(self, statement, parameters=None) -> _Result:
        self.calls.append((str(statement), parameters or {}))
        row = self.rows.pop(0) if self.rows else None
        return _Result(row)

    async def scalar(self, statement, parameters=None) -> object | None:
        self.calls.append((str(statement), parameters or {}))
        return self.rows.pop(0) if self.rows else None


class _Verifier:
    def __init__(self, result: bool) -> None:
        self.result = result
        self.calls: list[tuple[str, str | None]] = []

    async def verify(self, password: str, encoded_hash: str | None) -> bool:
        self.calls.append((password, encoded_hash))
        return self.result


def _user_row() -> SimpleNamespace:
    return SimpleNamespace(
        user_id=uuid.uuid4(),
        username="member.one",
        display_name="Member One",
        actor_role="member",
        account_status="active",
    )


def test_preauth_csrf_is_bound_and_tamper_evident() -> None:
    csrf = PreAuthCsrf("x" * 32)
    binding, token = csrf.issue()

    assert csrf.valid(binding, token)
    assert not csrf.valid(binding + "x", token)
    assert not csrf.valid(binding, token + "x")
    assert not csrf.valid(None, token)


def test_bounded_verifier_always_supports_dummy_unknown_user_path() -> None:
    hasher = PasswordHasher(
        time_cost=1,
        memory_cost=8192,
        parallelism=1,
        hash_len=16,
        salt_len=16,
        type=Type.ID,
    )
    verifier = BoundedPasswordVerifier(hasher=hasher, maximum_concurrency=1)
    encoded = hasher.hash("correct-password")

    assert asyncio.run(verifier.verify("correct-password", encoded)) is True
    assert asyncio.run(verifier.verify("incorrect", encoded)) is False
    assert asyncio.run(verifier.verify("incorrect", None)) is False


def test_password_verifier_defers_calibration_until_password_work(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.services.authentication.calibrate_password_hasher",
        lambda: (_ for _ in ()).throw(AssertionError("unexpected calibration")),
    )

    BoundedPasswordVerifier(maximum_concurrency=1)
    AuthenticationService(
        UnavailableAuthenticationGateway(),
        maximum_hash_concurrency=1,
    )


def test_password_hash_concurrency_must_be_bounded() -> None:
    with pytest.raises(ValueError, match="maximum_hash_concurrency"):
        AuthenticationService(
            UnavailableAuthenticationGateway(),
            maximum_hash_concurrency=0,
        )


def test_database_session_resolution_uses_only_controlled_session_view() -> None:
    user = _user_row()
    csrf = "csrf-token"
    row = SimpleNamespace(
        **vars(user),
        authentication_version=3,
        authorization_version=7,
        session_id=uuid.uuid4(),
        csrf_token_hash=hash_opaque_token(csrf),
    )
    session = _Session([row])
    gateway = DatabaseAuthenticationGateway(lambda: session)

    view = asyncio.run(gateway.resolve_session("a" * 64, csrf))

    assert view.user.username == "member.one"
    assert view.actor.authentication_version == 3
    assert "v4_session_view" in session.calls[0][0]
    assert " FROM users" not in session.calls[0][0]
    assert " JOIN sessions" not in session.calls[0][0]


def test_database_login_verifies_then_issues_rotated_session() -> None:
    user = _user_row()
    lookup = SimpleNamespace(
        **vars(user),
        password_hash="$argon2id$encoded",
        authentication_version=5,
        blocked_until=None,
    )
    verifier = _Verifier(True)
    session = _Session([None, lookup, None, user])
    gateway = DatabaseAuthenticationGateway(
        lambda: session,
        verifier=verifier,
        session_idle_seconds=1800,
    )

    result = asyncio.run(
        gateway.login(
            username="member.one",
            password="permanent-password",
            client_key="b" * 64,
            session_token_hash="c" * 64,
            csrf_token_hash="d" * 64,
        )
    )

    assert result.username == "member.one"
    assert verifier.calls == [("permanent-password", "$argon2id$encoded")]
    assert "v4_login_blocked_until" in session.calls[0][0]
    assert "v4_auth_lookup" in session.calls[1][0]
    assert "v4_clear_login_failures" in session.calls[2][0]
    assert "v4_issue_login_session" in session.calls[3][0]
    parameters = session.calls[3][1]
    assert parameters["idle_expires_at"] == parameters["absolute_expires_at"]


def test_database_refresh_uses_atomic_function_and_preserves_throttle_result() -> None:
    csrf = "csrf-token"
    row = SimpleNamespace(
        **vars(_user_row()),
        authentication_version=3,
        authorization_version=7,
        session_id=uuid.uuid4(),
        csrf_token_hash=hash_opaque_token(csrf),
        refreshed=False,
    )
    session = _Session([row])
    gateway = DatabaseAuthenticationGateway(lambda: session)

    result = asyncio.run(gateway.refresh("a" * 64, csrf, hash_opaque_token(csrf)))

    assert result.refreshed is False
    assert result.view.user.username == "member.one"
    assert "v4_refresh_session" in session.calls[0][0]
    parameters = session.calls[0][1]
    assert parameters["csrf_token_hash"] == hash_opaque_token(csrf)


def test_database_refresh_rejects_a_missing_atomic_result() -> None:
    gateway = DatabaseAuthenticationGateway(lambda: _Session([None]))

    with pytest.raises(InvalidSession):
        asyncio.run(gateway.refresh("a" * 64, "csrf", hash_opaque_token("csrf")))


def test_database_login_keeps_unknown_user_on_dummy_verification_path() -> None:
    verifier = _Verifier(False)
    lookup = SimpleNamespace(
        user_id=None,
        username=None,
        display_name=None,
        actor_role=None,
        account_status=None,
        password_hash=None,
        authentication_version=None,
        blocked_until=None,
    )
    session = _Session([None, lookup, None])
    gateway = DatabaseAuthenticationGateway(lambda: session, verifier=verifier)

    with pytest.raises(InvalidCredentials):
        asyncio.run(
            gateway.login(
                username="unknown.user",
                password="incorrect-password",
                client_key="e" * 64,
                session_token_hash="f" * 64,
                csrf_token_hash="0" * 64,
            )
        )

    assert verifier.calls == [("incorrect-password", None)]
    assert "v4_login_blocked_until" in session.calls[0][0]
    assert "v4_record_login_failure" in session.calls[2][0]


def test_database_login_short_circuits_before_argon_when_client_is_blocked() -> None:
    verifier = _Verifier(True)
    session = _Session([datetime.now(UTC) + timedelta(minutes=5)])
    gateway = DatabaseAuthenticationGateway(lambda: session, verifier=verifier)

    with pytest.raises(InvalidCredentials):
        asyncio.run(
            gateway.login(
                username="member.one",
                password="permanent-password",
                client_key="e" * 64,
                session_token_hash="f" * 64,
                csrf_token_hash="0" * 64,
            )
        )

    assert verifier.calls == []
    assert len(session.calls) == 1
    assert "v4_login_blocked_until" in session.calls[0][0]


def test_concurrent_login_hash_waiters_do_not_hold_database_sessions() -> None:
    async def exercise() -> None:
        lookup = SimpleNamespace(
            **vars(_user_row()),
            password_hash="$argon2id$encoded",
            authentication_version=5,
            blocked_until=None,
        )

        class TrackingSession(_Context):
            def __init__(self, factory: "TrackingFactory") -> None:
                self.factory = factory

            async def __aenter__(self) -> "TrackingSession":
                self.factory.active += 1
                self.factory.maximum_active = max(
                    self.factory.maximum_active, self.factory.active
                )
                return self

            async def __aexit__(self, *_args: object) -> None:
                self.factory.active -= 1

            def begin(self) -> _Context:
                return _Context()

            async def scalar(self, statement, parameters=None) -> None:
                assert "v4_login_blocked_until" in str(statement)
                return None

            async def execute(self, statement, parameters=None) -> _Result:
                sql = str(statement)
                if "v4_auth_lookup" in sql:
                    return _Result(lookup)
                if "v4_record_login_failure" in sql:
                    return _Result()
                raise AssertionError(f"unexpected SQL: {sql}")

        class TrackingFactory:
            def __init__(self) -> None:
                self.active = 0
                self.maximum_active = 0
                self.created = 0

            def __call__(self) -> TrackingSession:
                self.created += 1
                return TrackingSession(self)

        class WaitingVerifier:
            def __init__(self) -> None:
                self.started = 0
                self.all_started = asyncio.Event()
                self.release = asyncio.Event()
                self.semaphore = asyncio.Semaphore(1)

            async def verify(self, password: str, encoded_hash: str | None) -> bool:
                self.started += 1
                if self.started == 2:
                    self.all_started.set()
                async with self.semaphore:
                    await self.release.wait()
                return False

        factory = TrackingFactory()
        verifier = WaitingVerifier()
        gateway = DatabaseAuthenticationGateway(factory, verifier=verifier)
        calls = [
            asyncio.create_task(
                gateway.login(
                    username=f"member.{index}",
                    password="wrong-password",
                    client_key=str(index) * 64,
                    session_token_hash="f" * 64,
                    csrf_token_hash="0" * 64,
                )
            )
            for index in (1, 2)
        ]

        await asyncio.wait_for(verifier.all_started.wait(), timeout=1)
        assert factory.active == 0
        assert factory.created == 2

        verifier.release.set()
        results = await asyncio.gather(*calls, return_exceptions=True)
        assert all(isinstance(result, InvalidCredentials) for result in results)
        assert factory.active == 0
        assert factory.created == 4

    asyncio.run(exercise())


def test_database_activation_password_change_and_logout_use_functions() -> None:
    activated = _user_row()
    changed = _user_row()
    current = SimpleNamespace(
        user_id=changed.user_id,
        password_hash="$argon2id$current",
        authentication_version=8,
    )
    verifier = _Verifier(True)
    session = _Session([None, activated, current, None, changed, None])
    gateway = DatabaseAuthenticationGateway(lambda: session, verifier=verifier)

    assert (
        asyncio.run(
            gateway.activate(
                activation_token_hash="1" * 64,
                password_hash="$argon2id$new",
                session_token_hash="2" * 64,
                csrf_token_hash="3" * 64,
            )
        ).id
        == activated.user_id
    )
    assert (
        asyncio.run(
            gateway.change_password(
                current_session_token_hash="4" * 64,
                current_password="current-password",
                new_password_hash="$argon2id$replacement",
                replacement_session_token_hash="5" * 64,
                csrf_token_hash="6" * 64,
            )
        ).id
        == changed.user_id
    )
    asyncio.run(gateway.logout("7" * 64))

    statements = "\n".join(call[0] for call in session.calls)
    assert "v4_consume_activation" in statements
    assert "v4_password_change_lookup" in statements
    assert "v4_change_password" in statements
    assert "v4_logout" in statements
