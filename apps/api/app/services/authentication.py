import asyncio
import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol
from uuid import UUID

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from sqlalchemy import text
from sqlalchemy.exc import NoResultFound, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.schemas.auth import AuthUser
from app.security.actor import ActorContext, ActorRole
from app.security.identity import normalize_username, validate_permanent_password
from app.security.passwords import calibrate_password_hasher
from app.security.tokens import hash_opaque_token, issue_opaque_token


class AuthenticationError(Exception):
    pass


class InvalidCredentials(AuthenticationError):
    pass


class InvalidSession(AuthenticationError):
    pass


class InvalidActivation(AuthenticationError):
    pass


class AuthenticationUnavailable(AuthenticationError):
    pass


@dataclass(frozen=True, slots=True)
class SessionView:
    user: AuthUser
    actor: ActorContext
    csrf_token: str


@dataclass(frozen=True, slots=True)
class IssuedSession:
    user: AuthUser
    session_token: str
    csrf_token: str


@dataclass(frozen=True, slots=True)
class RefreshedSession:
    view: SessionView
    refreshed: bool


class AuthenticationGateway(Protocol):
    """Authoritative database-function boundary for authentication mutations."""

    async def resolve_session(
        self, token_hash: str, csrf_token: str | None
    ) -> SessionView: ...

    async def login(
        self,
        *,
        username: str,
        password: str,
        client_key: str,
        session_token_hash: str,
        csrf_token_hash: str,
    ) -> AuthUser: ...

    async def refresh(
        self, token_hash: str, csrf_token: str, csrf_token_hash: str
    ) -> RefreshedSession: ...

    async def activate(
        self,
        *,
        activation_token_hash: str,
        password_hash: str,
        session_token_hash: str,
        csrf_token_hash: str,
    ) -> AuthUser: ...

    async def change_password(
        self,
        *,
        current_session_token_hash: str,
        current_password: str,
        new_password_hash: str,
        replacement_session_token_hash: str,
        csrf_token_hash: str,
    ) -> AuthUser: ...

    async def logout(self, token_hash: str) -> None: ...

    async def verify_current_password(
        self, session_token_hash: str, password: str
    ) -> None: ...


class UnavailableAuthenticationGateway:
    """Fail closed until the baseline supplies the required controlled functions."""

    async def resolve_session(
        self, token_hash: str, csrf_token: str | None
    ) -> SessionView:
        raise InvalidSession

    async def login(self, **_kwargs: str) -> AuthUser:
        raise AuthenticationUnavailable("authentication functions are unavailable")

    async def activate(self, **_kwargs: str) -> AuthUser:
        raise AuthenticationUnavailable("authentication functions are unavailable")

    async def refresh(self, **_kwargs: str) -> RefreshedSession:
        raise InvalidSession

    async def change_password(self, **_kwargs: str) -> AuthUser:
        raise AuthenticationUnavailable("authentication functions are unavailable")

    async def logout(self, token_hash: str) -> None:
        raise AuthenticationUnavailable("authentication functions are unavailable")

    async def verify_current_password(
        self, session_token_hash: str, password: str
    ) -> None:
        raise AuthenticationUnavailable("authentication functions are unavailable")


class BoundedPasswordVerifier:
    """Runs Argon2 work off-loop with a hard concurrency bound."""

    def __init__(
        self,
        *,
        hasher: PasswordHasher | None = None,
        maximum_concurrency: int = 2,
    ) -> None:
        if maximum_concurrency < 1:
            raise ValueError("maximum_concurrency must be positive")
        self.hasher = hasher
        self._semaphore = asyncio.Semaphore(maximum_concurrency)
        self._initialization_lock = asyncio.Lock()
        self._dummy_hash = (
            hasher.hash(secrets.token_urlsafe(32)) if hasher is not None else None
        )

    async def verify(self, password: str, encoded_hash: str | None) -> bool:
        async with self._semaphore:
            if self.hasher is None or self._dummy_hash is None:
                async with self._initialization_lock:
                    if self.hasher is None:
                        self.hasher = await asyncio.to_thread(calibrate_password_hasher)
                    if self._dummy_hash is None:
                        self._dummy_hash = await asyncio.to_thread(
                            self.hasher.hash, secrets.token_urlsafe(32)
                        )
            candidate = encoded_hash or self._dummy_hash
            try:
                return await asyncio.to_thread(self.hasher.verify, candidate, password)
            except (InvalidHashError, VerificationError, VerifyMismatchError):
                return False


class PreAuthCsrf:
    """Signed synchronizer token for the anonymous login/activation exchange."""

    def __init__(self, secret: str) -> None:
        if len(secret) < 32:
            raise ValueError("CSRF signing secret must contain at least 32 characters")
        self._secret = secret.encode("utf-8")

    def issue(self) -> tuple[str, str]:
        binding = secrets.token_urlsafe(32)
        digest = hmac.new(self._secret, binding.encode(), hashlib.sha256).hexdigest()
        return binding, digest

    def valid(self, binding: str | None, token: str | None) -> bool:
        if not binding or not token:
            return False
        expected = hmac.new(
            self._secret, binding.encode("utf-8"), hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(expected, token)


class DatabaseAuthenticationGateway(UnavailableAuthenticationGateway):
    """Authentication gateway restricted to the V4 controlled SQL functions."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        verifier: BoundedPasswordVerifier | None = None,
        session_idle_seconds: int = 30 * 60,
    ) -> None:
        if session_idle_seconds < 60:
            raise ValueError("session_idle_seconds must be at least 60")
        self._session_factory = session_factory
        self._verifier = verifier or BoundedPasswordVerifier()
        self._session_idle = timedelta(seconds=session_idle_seconds)

    async def resolve_session(
        self, token_hash: str, csrf_token: str | None
    ) -> SessionView:
        if not csrf_token:
            raise InvalidSession
        try:
            async with self._session_factory() as session, session.begin():
                row = (
                    await session.execute(
                        text(
                            "SELECT user_id, username, display_name, actor_role, "
                            "account_status, authentication_version, "
                            "authorization_version, session_id, csrf_token_hash "
                            "FROM v4_session_view(:token_hash)"
                        ),
                        {"token_hash": token_hash},
                    )
                ).one_or_none()
                if row is None or not hmac.compare_digest(
                    row.csrf_token_hash, hash_opaque_token(csrf_token)
                ):
                    raise InvalidSession
                return SessionView(
                    user=AuthUser(
                        id=row.user_id,
                        username=row.username,
                        display_name=row.display_name,
                        role=row.actor_role,
                        status=row.account_status,
                    ),
                    actor=ActorContext(
                        user_id=row.user_id,
                        role=ActorRole(row.actor_role),
                        authentication_version=row.authentication_version,
                        authorization_version=row.authorization_version,
                        session_id=row.session_id,
                    ),
                    csrf_token=csrf_token,
                )
        except InvalidSession:
            raise
        except SQLAlchemyError as exc:
            raise AuthenticationUnavailable(
                "authentication database functions are unavailable"
            ) from exc

    async def login(
        self,
        *,
        username: str,
        password: str,
        client_key: str,
        session_token_hash: str,
        csrf_token_hash: str,
    ) -> AuthUser:
        now = datetime.now(UTC)
        try:
            async with self._session_factory() as session, session.begin():
                blocked_until = await session.scalar(
                    text("SELECT v4_login_blocked_until(:client_key)"),
                    {"client_key": client_key},
                )
                if blocked_until is not None and blocked_until > now:
                    raise InvalidCredentials
                row = (
                    await session.execute(
                        text(
                            "SELECT user_id, username, display_name, actor_role, "
                            "account_status, password_hash, "
                            "authentication_version, blocked_until "
                            "FROM v4_auth_lookup(:username, :client_key)"
                        ),
                        {"username": username, "client_key": client_key},
                    )
                ).one_or_none()
        except SQLAlchemyError as exc:
            raise AuthenticationUnavailable(
                "authentication database functions are unavailable"
            ) from exc

        password_matches = await self._verifier.verify(
            password, None if row is None else row.password_hash
        )
        invalid = (
            row is None
            or row.user_id is None
            or not password_matches
            or row.account_status != "active"
        )
        try:
            async with self._session_factory() as session, session.begin():
                if invalid:
                    await session.execute(
                        text("SELECT v4_record_login_failure(:client_key)"),
                        {"client_key": client_key},
                    )
                else:
                    await session.execute(
                        text("SELECT v4_clear_login_failures(:client_key)"),
                        {"client_key": client_key},
                    )
                    idle_expires_at, absolute_expires_at = self._session_expiries(now)
                    issued = (
                        await session.execute(
                            text(
                                "SELECT user_id, username, display_name, actor_role, "
                                "account_status FROM v4_issue_login_session("
                                ":user_id, :authentication_version, "
                                ":session_token_hash, :csrf_token_hash, "
                                ":idle_expires_at, :absolute_expires_at)"
                            ),
                            {
                                "user_id": row.user_id,
                                "authentication_version": (row.authentication_version),
                                "session_token_hash": session_token_hash,
                                "csrf_token_hash": csrf_token_hash,
                                "idle_expires_at": idle_expires_at,
                                "absolute_expires_at": absolute_expires_at,
                            },
                        )
                    ).one()
            if invalid:
                raise InvalidCredentials
            return self._auth_user(issued)
        except SQLAlchemyError as exc:
            if _sqlstate(exc) in {"02000", "P0002"}:
                raise InvalidCredentials from exc
            raise AuthenticationUnavailable(
                "authentication database functions are unavailable"
            ) from exc

    async def refresh(
        self, token_hash: str, csrf_token: str, csrf_token_hash: str
    ) -> RefreshedSession:
        expires_at = datetime.now(UTC) + self._session_idle
        try:
            async with self._session_factory() as session, session.begin():
                row = (
                    await session.execute(
                        text(
                            "SELECT user_id, username, display_name, actor_role, "
                            "account_status, authentication_version, "
                            "authorization_version, session_id, csrf_token_hash, "
                            "refreshed FROM v4_refresh_session("
                            ":token_hash, :csrf_token_hash, :expires_at)"
                        ),
                        {
                            "token_hash": token_hash,
                            "csrf_token_hash": csrf_token_hash,
                            "expires_at": expires_at,
                        },
                    )
                ).one_or_none()
                if row is None or not hmac.compare_digest(
                    row.csrf_token_hash, hash_opaque_token(csrf_token)
                ):
                    raise InvalidSession
                view = SessionView(
                    user=self._auth_user(row),
                    actor=ActorContext(
                        user_id=row.user_id,
                        role=ActorRole(row.actor_role),
                        authentication_version=row.authentication_version,
                        authorization_version=row.authorization_version,
                        session_id=row.session_id,
                    ),
                    csrf_token=csrf_token,
                )
                return RefreshedSession(view=view, refreshed=row.refreshed)
        except InvalidSession:
            raise
        except SQLAlchemyError as exc:
            raise AuthenticationUnavailable(
                "authentication database functions are unavailable"
            ) from exc

    async def activate(
        self,
        *,
        activation_token_hash: str,
        password_hash: str,
        session_token_hash: str,
        csrf_token_hash: str,
    ) -> AuthUser:
        now = datetime.now(UTC)
        idle_expires_at, absolute_expires_at = self._session_expiries(now)
        try:
            async with self._session_factory() as session, session.begin():
                await session.execute(
                    text(
                        "SELECT v4_consume_activation("
                        ":activation_token_hash, :password_hash, "
                        ":session_token_hash, :csrf_token_hash, "
                        ":idle_expires_at, :absolute_expires_at)"
                    ),
                    {
                        "activation_token_hash": activation_token_hash,
                        "password_hash": password_hash,
                        "session_token_hash": session_token_hash,
                        "csrf_token_hash": csrf_token_hash,
                        "idle_expires_at": idle_expires_at,
                        "absolute_expires_at": absolute_expires_at,
                    },
                )
                row = (
                    await session.execute(
                        text(
                            "SELECT user_id, username, display_name, actor_role, "
                            "account_status FROM "
                            "v4_session_view(:session_token_hash)"
                        ),
                        {"session_token_hash": session_token_hash},
                    )
                ).one()
                return self._auth_user(row)
        except NoResultFound as exc:
            raise InvalidActivation from exc
        except SQLAlchemyError as exc:
            if _sqlstate(exc) in {"02000", "22023", "P0002"}:
                raise InvalidActivation from exc
            raise AuthenticationUnavailable(
                "authentication database functions are unavailable"
            ) from exc

    async def change_password(
        self,
        *,
        current_session_token_hash: str,
        current_password: str,
        new_password_hash: str,
        replacement_session_token_hash: str,
        csrf_token_hash: str,
    ) -> AuthUser:
        now = datetime.now(UTC)
        try:
            async with self._session_factory() as session, session.begin():
                current = (
                    await session.execute(
                        text(
                            "SELECT user_id, password_hash, "
                            "authentication_version FROM "
                            "v4_password_change_lookup(:session_token_hash)"
                        ),
                        {"session_token_hash": current_session_token_hash},
                    )
                ).one_or_none()
                if current is None or not await self._verifier.verify(
                    current_password, current.password_hash
                ):
                    raise InvalidCredentials
                idle_expires_at, absolute_expires_at = self._session_expiries(now)
                await session.execute(
                    text(
                        "SELECT v4_change_password("
                        ":current_session_token_hash, :authentication_version, "
                        ":new_password_hash, :replacement_session_token_hash, "
                        ":csrf_token_hash, :idle_expires_at, "
                        ":absolute_expires_at)"
                    ),
                    {
                        "current_session_token_hash": current_session_token_hash,
                        "authentication_version": current.authentication_version,
                        "new_password_hash": new_password_hash,
                        "replacement_session_token_hash": (
                            replacement_session_token_hash
                        ),
                        "csrf_token_hash": csrf_token_hash,
                        "idle_expires_at": idle_expires_at,
                        "absolute_expires_at": absolute_expires_at,
                    },
                )
                row = (
                    await session.execute(
                        text(
                            "SELECT user_id, username, display_name, actor_role, "
                            "account_status FROM "
                            "v4_session_view(:replacement_session_token_hash)"
                        ),
                        {
                            "replacement_session_token_hash": (
                                replacement_session_token_hash
                            )
                        },
                    )
                ).one()
                return self._auth_user(row)
        except InvalidCredentials:
            raise
        except NoResultFound as exc:
            raise InvalidSession from exc
        except SQLAlchemyError as exc:
            if _sqlstate(exc) in {"02000", "P0002"}:
                raise InvalidSession from exc
            raise AuthenticationUnavailable(
                "authentication database functions are unavailable"
            ) from exc

    async def logout(self, token_hash: str) -> None:
        try:
            async with self._session_factory() as session, session.begin():
                await session.execute(
                    text("SELECT v4_logout(:token_hash)"),
                    {"token_hash": token_hash},
                )
        except SQLAlchemyError as exc:
            raise AuthenticationUnavailable(
                "authentication database functions are unavailable"
            ) from exc

    async def verify_current_password(
        self, session_token_hash: str, password: str
    ) -> None:
        try:
            async with self._session_factory() as session, session.begin():
                current = (
                    await session.execute(
                        text(
                            "SELECT user_id, password_hash FROM "
                            "v4_password_change_lookup(:session_token_hash)"
                        ),
                        {"session_token_hash": session_token_hash},
                    )
                ).one_or_none()
        except SQLAlchemyError as exc:
            raise AuthenticationUnavailable(
                "authentication database functions are unavailable"
            ) from exc
        if current is None or not await self._verifier.verify(
            password, current.password_hash
        ):
            raise InvalidCredentials

    def _session_expiries(self, now: datetime) -> tuple[datetime, datetime]:
        expires_at = now + self._session_idle
        return expires_at, expires_at

    @staticmethod
    def _auth_user(row: Any) -> AuthUser:
        return AuthUser(
            id=UUID(str(row.user_id)),
            username=row.username,
            display_name=row.display_name,
            role=row.actor_role,
            status=row.account_status,
        )


def _sqlstate(exc: SQLAlchemyError) -> str | None:
    return getattr(getattr(exc, "orig", None), "sqlstate", None)


class AuthenticationService:
    def __init__(
        self,
        gateway: AuthenticationGateway,
        *,
        password_hasher: PasswordHasher | None = None,
        maximum_hash_concurrency: int = 2,
    ) -> None:
        if maximum_hash_concurrency < 1:
            raise ValueError("maximum_hash_concurrency must be positive")
        self.gateway = gateway
        self.password_hasher = password_hasher
        self._hash_semaphore = asyncio.Semaphore(maximum_hash_concurrency)
        self._hasher_lock = asyncio.Lock()

    async def _hash_password(self, password: str) -> str:
        async with self._hash_semaphore:
            if self.password_hasher is None:
                async with self._hasher_lock:
                    if self.password_hasher is None:
                        self.password_hasher = await asyncio.to_thread(
                            calibrate_password_hasher
                        )
            return await asyncio.to_thread(self.password_hasher.hash, password)

    async def current(
        self, session_token: str | None, csrf_token: str | None = None
    ) -> SessionView | None:
        if not session_token:
            return None
        try:
            return await self.gateway.resolve_session(
                hash_opaque_token(session_token), csrf_token
            )
        except InvalidSession:
            raise

    async def refresh(self, session_token: str, csrf_token: str) -> RefreshedSession:
        return await self.gateway.refresh(
            hash_opaque_token(session_token),
            csrf_token,
            hash_opaque_token(csrf_token),
        )

    async def login(
        self, username: str, password: str, client_key: str
    ) -> IssuedSession:
        try:
            canonical = normalize_username(username)
        except ValueError:
            canonical = ""
        session = issue_opaque_token()
        csrf = issue_opaque_token()
        user = await self.gateway.login(
            username=canonical,
            password=password,
            client_key=client_key,
            session_token_hash=session.digest,
            csrf_token_hash=csrf.digest,
        )
        return IssuedSession(user, session.plaintext, csrf.plaintext)

    async def activate(self, code: str, password: str) -> IssuedSession:
        validated = validate_permanent_password(password)
        password_hash = await self._hash_password(validated)
        session = issue_opaque_token()
        csrf = issue_opaque_token()
        user = await self.gateway.activate(
            activation_token_hash=hash_opaque_token(code),
            password_hash=password_hash,
            session_token_hash=session.digest,
            csrf_token_hash=csrf.digest,
        )
        return IssuedSession(user, session.plaintext, csrf.plaintext)

    async def change_password(
        self,
        session_token: str,
        current_password: str,
        new_password: str,
    ) -> IssuedSession:
        validated = validate_permanent_password(new_password)
        password_hash = await self._hash_password(validated)
        replacement = issue_opaque_token()
        csrf = issue_opaque_token()
        user = await self.gateway.change_password(
            current_session_token_hash=hash_opaque_token(session_token),
            current_password=current_password,
            new_password_hash=password_hash,
            replacement_session_token_hash=replacement.digest,
            csrf_token_hash=csrf.digest,
        )
        return IssuedSession(user, replacement.plaintext, csrf.plaintext)

    async def logout(self, session_token: str) -> None:
        await self.gateway.logout(hash_opaque_token(session_token))

    async def verify_current_password(self, session_token: str, password: str) -> None:
        await self.gateway.verify_current_password(
            hash_opaque_token(session_token), password
        )
