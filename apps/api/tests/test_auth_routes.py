import uuid
from types import SimpleNamespace

import pytest
from fastapi import Response
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.routes.auth import _set_session_cookie
from app.schemas.auth import AuthUser
from app.security.actor import ActorContext, ActorRole
from app.services.authentication import (
    InvalidSession,
    IssuedSession,
    RefreshedSession,
    SessionView,
)

USER = AuthUser(
    id=uuid.uuid4(),
    username="member.one",
    display_name="Member One",
    role="member",
    status="active",
)
ACTOR = ActorContext(
    user_id=USER.id,
    role=ActorRole.MEMBER,
    authentication_version=1,
    authorization_version=1,
    session_id=uuid.uuid4(),
)


def test_session_cookie_rejects_header_control_characters() -> None:
    with pytest.raises(RuntimeError, match="invalid cookie token"):
        _set_session_cookie(
            Response(),
            "opaque-token\r\nSet-Cookie: injected=value",
            maximum_age=1800,
            secure=True,
        )


class _Authentication:
    def __init__(self) -> None:
        self.logged_out = False
        self.refresh_changed = True

    async def current(
        self, session_token: str | None, csrf_token: str | None = None
    ) -> SessionView | None:
        if session_token == "session-token" and csrf_token == "session-csrf":
            return SessionView(USER, ACTOR, "session-csrf")
        if session_token == "expired-session":
            raise InvalidSession
        return None

    async def login(
        self, username: str, password: str, client_key: str
    ) -> IssuedSession:
        assert username == "member.one"
        assert password == "not-logged-password"
        assert len(client_key) == 64
        return IssuedSession(USER, "session-token", "session-csrf")

    async def activate(self, code: str, password: str) -> IssuedSession:
        assert code == "one-time-code"
        assert len(password) == 14
        return IssuedSession(USER, "session-token", "session-csrf")

    async def change_password(
        self, token: str, current_password: str, new_password: str
    ) -> IssuedSession:
        assert token == "session-token"
        assert current_password == "old-password-value"
        assert new_password == "new-password-value"
        return IssuedSession(USER, "rotated-session", "rotated-csrf")

    async def logout(self, token: str) -> None:
        assert token == "session-token"
        self.logged_out = True

    async def refresh(self, token: str, csrf_token: str) -> RefreshedSession:
        if token != "session-token" or csrf_token != "session-csrf":
            raise InvalidSession
        return RefreshedSession(
            SessionView(USER, ACTOR, "session-csrf"),
            refreshed=self.refresh_changed,
        )


def _client() -> tuple[TestClient, _Authentication]:
    authentication = _Authentication()
    container = SimpleNamespace(
        authentication=authentication,
        authorization=SimpleNamespace(),
    )
    app = create_app(
        Settings(environment="test"),
        container=container,
    )
    return TestClient(app, base_url="https://rag.home.arpa"), authentication


def _preauth(client: TestClient) -> str:
    response = client.get("/api/auth/me")
    assert response.status_code == 200
    assert response.json()["user"] is None
    return response.json()["csrf_token"]


def test_login_accepts_exact_configured_loopback_origin_and_requires_csrf() -> None:
    client, _authentication = _client()
    csrf = _preauth(client)
    payload = {"username": "member.one", "password": "not-logged-password"}

    assert client.post("/api/auth/login", json=payload).status_code == 403
    response = client.post(
        "/api/auth/login",
        json=payload,
        headers={"Origin": "http://127.0.0.1:3000", "X-CSRF-Token": csrf},
    )

    assert response.status_code == 200
    assert response.json() == {
        "user": USER.model_dump(mode="json"),
        "csrf_token": "session-csrf",
    }
    cookie = response.headers["set-cookie"]
    assert "rag_session=session-token" in cookie
    assert "HttpOnly" in cookie
    assert "Secure" in cookie
    assert "SameSite=lax" in cookie
    assert "Domain=" not in cookie
    assert "Max-Age=1800" in cookie


def test_authenticated_contract_rotates_password_session_and_logs_out() -> None:
    client, authentication = _client()
    client.cookies.clear()
    client.cookies.set("rag_session", "session-token")
    client.cookies.set("csrf_token", "session-csrf")
    headers = {
        "Origin": "https://rag.home.arpa",
        "X-CSRF-Token": "session-csrf",
    }

    current = client.get("/api/auth/me")
    changed = client.post(
        "/api/auth/password",
        json={
            "current_password": "old-password-value",
            "new_password": "new-password-value",
        },
        headers=headers,
    )
    client.cookies.clear()
    client.cookies.set("rag_session", "session-token")
    client.cookies.set("csrf_token", "session-csrf")
    logged_out = client.post("/api/auth/logout", headers=headers)

    assert current.json()["user"]["username"] == "member.one"
    assert current.json()["csrf_token"] == "session-csrf"
    assert changed.json()["csrf_token"] == "rotated-csrf"
    assert "rag_session=rotated-session" in changed.headers["set-cookie"]
    assert logged_out.status_code == 204
    assert authentication.logged_out is True


def test_activation_uses_same_preauth_boundary() -> None:
    client, _authentication = _client()
    csrf = _preauth(client)

    response = client.post(
        "/api/auth/activate",
        json={"code": "one-time-code", "password": "a" * 14},
        headers={"Origin": "https://rag.home.arpa", "X-CSRF-Token": csrf},
    )

    assert response.status_code == 200
    assert response.json()["user"]["status"] == "active"


def test_refresh_requires_origin_csrf_and_renews_only_after_database_refresh() -> None:
    client, authentication = _client()
    client.cookies.set("rag_session", "session-token")
    client.cookies.set("csrf_token", "session-csrf")
    headers = {
        "Origin": "https://rag.home.arpa",
        "X-CSRF-Token": "session-csrf",
    }

    assert client.post("/api/auth/refresh").status_code == 403
    refreshed = client.post("/api/auth/refresh", headers=headers)
    assert refreshed.status_code == 200
    assert "rag_session=session-token" in refreshed.headers["set-cookie"]
    assert "Max-Age=1800" in refreshed.headers["set-cookie"]

    authentication.refresh_changed = False
    duplicate = client.post("/api/auth/refresh", headers=headers)
    assert duplicate.status_code == 200
    assert "set-cookie" not in duplicate.headers


def test_me_rejects_a_presented_expired_cookie_and_clears_session_cookies() -> None:
    client, _authentication = _client()
    client.cookies.set("rag_session", "expired-session")
    client.cookies.set("csrf_token", "session-csrf")

    response = client.get("/api/auth/me")

    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "session_expired"
    assert "rag_session=" in response.headers["set-cookie"]
    assert "Max-Age=0" in response.headers["set-cookie"]
