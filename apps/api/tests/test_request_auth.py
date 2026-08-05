import asyncio
import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import app.security.request_auth as request_auth
from app.schemas.auth import AuthUser
from app.security.actor import ActorContext, ActorRole
from app.services.authentication import InvalidSession


def _actor() -> ActorContext:
    return ActorContext(
        uuid.uuid4(),
        ActorRole.MEMBER,
        1,
        1,
        uuid.uuid4(),
    )


def _request(
    authentication: object,
    *,
    origin: str | None = "https://rag.home.arpa",
    header_csrf: str | None = "csrf",
    cookie_csrf: str | None = "csrf",
    session_cookie: str | None = "session",
) -> SimpleNamespace:
    headers: dict[str, str] = {}
    if origin is not None:
        headers["origin"] = origin
    if header_csrf is not None:
        headers[request_auth.CSRF_HEADER] = header_csrf
    cookies = {}
    if session_cookie is not None:
        cookies[request_auth.SESSION_COOKIE] = session_cookie
    if cookie_csrf is not None:
        cookies[request_auth.CSRF_COOKIE] = cookie_csrf
    return SimpleNamespace(
        headers=headers,
        cookies=cookies,
        app=SimpleNamespace(
            state=SimpleNamespace(
                settings=SimpleNamespace(
                    allowed_request_origins=frozenset({"https://rag.home.arpa"})
                ),
                container=SimpleNamespace(
                    authentication=authentication,
                    database=SimpleNamespace(session_factory=object()),
                ),
            )
        ),
    )


class _Authentication:
    def __init__(self, actor: ActorContext | None) -> None:
        self.actor = actor

    async def current(self, token: str | None, csrf: str | None) -> object | None:
        if self.actor is None:
            return None
        return SimpleNamespace(
            actor=self.actor,
            user=AuthUser(
                id=self.actor.user_id,
                username="member",
                display_name="Member",
                role="member",
                status="active",
            ),
        )


class _ExpiredAuthentication:
    async def current(self, token: str | None, csrf: str | None) -> object | None:
        raise InvalidSession


class _Unit:
    actor: ActorContext | None = None
    session = object()

    def __init__(self, factory: object, token_hash: str) -> None:
        return None

    async def __aenter__(self) -> "_Unit":
        return self

    async def __aexit__(self, *args: object) -> None:
        return None


def test_mutation_requires_exact_origin_and_bound_csrf() -> None:
    async def exercise() -> None:
        with pytest.raises(HTTPException) as missing_origin:
            async with request_auth.authenticated_request(
                _request(_Authentication(None), origin=None),
                mutation=True,
            ):
                pass
        assert missing_origin.value.status_code == 403

        with pytest.raises(HTTPException) as mismatched_csrf:
            async with request_auth.authenticated_request(
                _request(_Authentication(None), header_csrf="different"),
                mutation=True,
            ):
                pass
        assert mismatched_csrf.value.status_code == 403

    asyncio.run(exercise())


def test_missing_or_changed_session_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    actor = _actor()
    monkeypatch.setattr(request_auth, "AuthenticatedUnitOfWork", _Unit)

    async def exercise() -> None:
        with pytest.raises(HTTPException) as missing:
            async with request_auth.authenticated_request(
                _request(_Authentication(None))
            ):
                pass
        assert missing.value.status_code == 401

        _Unit.actor = _actor()
        with pytest.raises(HTTPException) as changed:
            async with request_auth.authenticated_request(
                _request(_Authentication(actor))
            ):
                pass
        assert changed.value.status_code == 401

    asyncio.run(exercise())


def test_missing_and_expired_sessions_have_stable_distinct_401_codes() -> None:
    async def exercise() -> None:
        with pytest.raises(HTTPException) as missing:
            async with request_auth.authenticated_request(
                _request(_Authentication(None), session_cookie=None)
            ):
                pass
        assert missing.value.status_code == 401
        assert missing.value.detail["code"] == "authentication_required"

        with pytest.raises(HTTPException) as expired:
            async with request_auth.authenticated_request(
                _request(_ExpiredAuthentication())
            ):
                pass
        assert expired.value.status_code == 401
        assert expired.value.detail["code"] == "session_expired"

    asyncio.run(exercise())


def test_authenticated_request_yields_database_derived_actor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    actor = _actor()
    _Unit.actor = actor
    monkeypatch.setattr(request_auth, "AuthenticatedUnitOfWork", _Unit)

    async def exercise() -> None:
        async with request_auth.authenticated_request(
            _request(_Authentication(actor)), mutation=True
        ) as authenticated:
            assert authenticated.actor == actor
            assert authenticated.session is _Unit.session
            assert authenticated.session_token == "session"

    asyncio.run(exercise())
