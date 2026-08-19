from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.services.setup import (
    SetupChallenge,
    SetupCodeExpired,
    SetupCodeLocked,
    SetupCodeRejected,
    SetupState,
    SetupStatus,
    SetupUnavailable,
)


class _Setup:
    def __init__(self) -> None:
        self.state = SetupState.REQUIRED
        self.challenge_error: Exception | None = None
        self.owner_error: Exception | None = None
        self.owner_values: dict[str, str] | None = None

    async def status(self) -> SetupStatus:
        return SetupStatus(
            self.state,
            datetime.now(UTC) + timedelta(minutes=15),
            5,
        )

    async def challenge(self, code: str) -> SetupChallenge:
        assert code == "private-setup-code"
        if self.challenge_error:
            raise self.challenge_error
        return SetupChallenge(
            token="browser-challenge-token",
            expires_at=datetime.now(UTC) + timedelta(minutes=10),
        )

    async def complete_owner(self, **values: str):
        if self.owner_error:
            raise self.owner_error
        self.owner_values = values
        self.state = SetupState.COMPLETE
        return uuid4()


def _settings(profile: str = "personal") -> Settings:
    values: dict[str, object] = {"environment": "test"}
    if profile == "personal":
        values.update(
            product_profile="personal",
            canonical_origin="http://127.0.0.1:3000",
            canonical_host="127.0.0.1",
            cors_origins=[],
        )
    elif profile == "team_lan_preview_unsigned":
        values.update(
            product_profile="team_lan_preview_unsigned",
            rag_lan_ipv4="192.168.40.10",
            cors_origins=[],
        )
    return Settings(**values)


def _client(
    *, profile: str = "personal", address: str = "127.0.0.1"
) -> tuple[TestClient, _Setup]:
    setup = _Setup()
    container = SimpleNamespace(setup=setup)
    app = create_app(_settings(profile), container=container)
    base_url = (
        "http://127.0.0.1:8000"
        if profile == "personal"
        else "https://rag.home.arpa"
    )
    client = TestClient(
        app,
        base_url=base_url,
        client=(address, 50000),
    )
    return client, setup


def _status_csrf(client: TestClient) -> str:
    response = client.get("/api/setup/status")
    assert response.status_code == 200
    return response.headers["X-CSRF-Token"]


def test_personal_setup_status_is_bounded_and_issues_http_loopback_csrf() -> None:
    client, _setup = _client()
    response = client.get("/api/setup/status")

    assert response.status_code == 200
    assert response.json()["state"] == "setup_required"
    assert response.json()["code_issued"] is True
    assert set(response.json()) == {
        "state",
        "code_issued",
        "code_expires_at",
        "attempts_remaining",
    }
    cookie = response.headers["set-cookie"]
    assert "rag_preauth=" in cookie
    assert "HttpOnly" in cookie
    assert "Path=/api" in cookie
    assert "Secure" not in cookie


def test_setup_challenge_requires_origin_and_csrf_then_sets_http_only_cookie() -> None:
    client, _setup = _client()
    csrf = _status_csrf(client)
    payload = {"code": "private-setup-code"}

    assert client.post("/api/setup/challenge", json=payload).status_code == 403
    assert (
        client.post(
            "/api/setup/challenge",
            json=payload,
            headers={
                "Origin": "http://evil.example",
                "X-CSRF-Token": csrf,
            },
        ).status_code
        == 403
    )
    response = client.post(
        "/api/setup/challenge",
        json=payload,
        headers={
            "Origin": "http://127.0.0.1:3000",
            "X-CSRF-Token": csrf,
        },
    )

    assert response.status_code == 200
    assert response.json()["state"] == "owner_details_required"
    cookie = response.headers["set-cookie"]
    assert "rag_setup_challenge=browser-challenge-token" in cookie
    assert "HttpOnly" in cookie
    assert "SameSite=strict" in cookie
    assert "Secure" not in cookie


def test_owner_creation_has_clear_login_handoff_and_terminal_lockout() -> None:
    client, setup = _client()
    csrf = _status_csrf(client)
    headers = {
        "Origin": "http://127.0.0.1:3000",
        "X-CSRF-Token": csrf,
    }
    challenged = client.post(
        "/api/setup/challenge",
        json={"code": "private-setup-code"},
        headers=headers,
    )
    assert challenged.status_code == 200
    response = client.post(
        "/api/setup/owner",
        json={
            "username": "owner.one",
            "display_name": "Owner One",
            "password": "fourteen-chars!",
        },
        headers=headers,
    )

    assert response.status_code == 200
    assert response.json() == {
        "state": "setup_complete",
        "login_path": "/login",
        "first_document_path": "/knowledge-base",
    }
    assert setup.owner_values == {
        "challenge_token": "browser-challenge-token",
        "username": "owner.one",
        "display_name": "Owner One",
        "password": "fourteen-chars!",
    }
    setup.owner_error = SetupUnavailable()
    replay = client.post(
        "/api/setup/owner",
        json={
            "username": "other.one",
            "display_name": "Other One",
            "password": "fourteen-chars!",
        },
        headers=headers,
    )
    assert replay.status_code in {403, 410}
    complete = client.get("/api/setup/status")
    assert complete.json() == {
        "state": "setup_complete",
        "code_issued": False,
        "code_expires_at": None,
        "attempts_remaining": 0,
    }
    assert "X-CSRF-Token" not in complete.headers


@pytest.mark.parametrize(
    ("error", "expected_status"),
    [
        (SetupCodeRejected(), 401),
        (SetupCodeExpired(), 410),
        (SetupCodeLocked(), 429),
        (SetupUnavailable(), 410),
    ],
)
def test_setup_challenge_returns_bounded_recovery_errors(
    error: Exception, expected_status: int
) -> None:
    client, setup = _client()
    csrf = _status_csrf(client)
    setup.challenge_error = error
    response = client.post(
        "/api/setup/challenge",
        json={"code": "private-setup-code"},
        headers={
            "Origin": "http://127.0.0.1:3000",
            "X-CSRF-Token": csrf,
        },
    )
    assert response.status_code == expected_status
    assert "private-setup-code" not in response.text


def test_setup_is_hidden_from_remote_and_team_clients() -> None:
    remote, _setup = _client(address="192.168.1.50")
    assert remote.get("/api/setup/status").status_code == 404

    team, _setup = _client(profile="team_lan")
    assert team.get("/api/setup/status").status_code == 404


def test_preview_setup_is_visible_only_from_the_configured_host_address() -> None:
    host, _setup = _client(
        profile="team_lan_preview_unsigned", address="192.168.40.10"
    )
    response = host.get("/api/setup/status")
    assert response.status_code == 200
    assert "Secure" in response.headers["set-cookie"]

    other_client, _setup = _client(
        profile="team_lan_preview_unsigned", address="192.168.40.11"
    )
    assert other_client.get("/api/setup/status").status_code == 404
