import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.schemas.admin import AdminAccessContextResponse, AdminAclImpact, AdminGrant
from app.schemas.auth import AuthUser
from app.security.actor import ActorContext, ActorRole
from app.services.authentication import SessionView
from app.services.authorization import (
    AclPreview,
    AuthorizationConflict,
    CapabilityDenied,
    InaccessibleResource,
    InvalidAuthorizationRequest,
    IssuedActivation,
)

_MUTATION_FLAGS: list[bool] = []


class _Authentication:
    async def current(
        self, session_token: str | None, csrf_token: str | None = None
    ) -> SessionView | None:
        if session_token is None:
            return None
        user_id = uuid.UUID(int=1)
        user = AuthUser(
            id=user_id,
            username="admin.one",
            display_name="Admin One",
            role="admin",
            status="active",
        )
        actor = ActorContext(
            user_id=user_id,
            role=ActorRole.ADMIN,
            authentication_version=1,
            authorization_version=1,
            session_id=uuid.UUID(int=2),
        )
        return SessionView(user, actor, csrf_token or "csrf")


class _Authorization:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []
        self.reset_error: Exception | None = None
        self.user_id = uuid.uuid4()
        self.team_id = uuid.uuid4()
        self.preview_id = uuid.uuid4()

    async def users(self, actor: ActorContext, session_token: str) -> list[AuthUser]:
        assert actor.role is ActorRole.ADMIN
        assert session_token == "opaque"
        return []

    async def teams(self, actor: ActorContext, session_token: str) -> list[object]:
        raise CapabilityDenied

    async def grants(self, actor: ActorContext, session_token: str) -> list[object]:
        return []

    async def access_context(
        self, actor: ActorContext, session_token: str, *, node_id: uuid.UUID
    ) -> AdminAccessContextResponse:
        return AdminAccessContextResponse(
            node_id=node_id,
            nearest_boundary_node_id=None,
            direct_grants=[
                AdminGrant(
                    id=uuid.UUID(int=30),
                    node_id=node_id,
                    user_id=uuid.UUID(int=31),
                    team_id=None,
                )
            ],
            inherited_grants=[],
            direct_create_grants=[],
            inherited_create_grants=[],
        )

    async def audit(
        self, actor: ActorContext, session_token: str, *, limit: int
    ) -> list[object]:
        assert limit == 25
        return []

    async def create_user(self, actor, session_token: str, **kwargs: object):
        assert session_token == "opaque"
        self.calls.append(("create_user", kwargs))
        return IssuedActivation(self.user_id, "single-use-code")

    async def reset_user(self, actor, session_token: str, **kwargs: object):
        if self.reset_error is not None:
            raise self.reset_error
        self.calls.append(("reset_user", kwargs))
        return IssuedActivation(kwargs["user_id"], "reset-code")

    async def set_user(self, actor, session_token: str, **kwargs: object) -> None:
        self.calls.append(("set_user", kwargs))

    async def create_team(self, actor, session_token: str, **kwargs: object):
        self.calls.append(("create_team", kwargs))
        return self.team_id

    async def preview_acl(self, actor, session_token: str, **kwargs: object):
        self.calls.append(("preview_acl", kwargs))
        return AclPreview(
            self.preview_id,
            "a" * 64,
            AdminAclImpact(
                user_ids=[],
                node_ids=[],
                document_ids=[],
                user_count=0,
                node_count=0,
                document_count=0,
            ),
        )

    async def apply_acl(self, actor, session_token: str, **kwargs: object) -> int:
        self.calls.append(("apply_acl", kwargs))
        return 12


def test_admin_routes_require_database_derived_actor_and_map_capability(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_id = uuid.UUID(int=1)
    actor = ActorContext(
        user_id=user_id,
        role=ActorRole.ADMIN,
        authentication_version=1,
        authorization_version=1,
        session_id=uuid.UUID(int=2),
    )

    @asynccontextmanager
    async def request_auth(request: object, *, mutation: bool = False):
        if request.cookies.get("rag_session") is None:
            raise HTTPException(401, "authentication required")
        _MUTATION_FLAGS.append(mutation)
        yield SimpleNamespace(actor=actor, session_token="opaque", session=object())

    monkeypatch.setattr("app.routes.admin.authenticated_request", request_auth)
    app = create_app(
        Settings(environment="test"),
        container=SimpleNamespace(
            authentication=_Authentication(),
            authorization=_Authorization(),
        ),
    )
    client = TestClient(app, base_url="https://rag.home.arpa")

    assert client.get("/api/admin/users").status_code == 401
    client.cookies.set("rag_session", "opaque")
    client.cookies.set("csrf_token", "csrf")
    assert client.get("/api/admin/users").json() == {"users": []}
    assert client.get("/api/admin/teams").status_code == 403
    assert client.get("/api/admin/grants").json() == {"grants": []}
    node_id = uuid.UUID(int=40)
    assert client.get("/api/admin/access", params={"node_id": str(node_id)}).json()[
        "direct_grants"
    ][0]["node_id"] == str(node_id)
    assert client.get("/api/admin/audit?limit=25").json() == {"events": []}


def test_admin_mutations_use_session_bound_csrf_path_and_acl_confirmation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    actor = ActorContext(
        user_id=uuid.UUID(int=1),
        role=ActorRole.ADMIN,
        authentication_version=1,
        authorization_version=1,
        session_id=uuid.UUID(int=2),
    )

    @asynccontextmanager
    async def request_auth(request: object, *, mutation: bool = False):
        assert request.headers["origin"] == "https://rag.home.arpa"
        assert request.headers["x-csrf-token"] == "csrf"
        assert request.cookies["rag_session"] == "opaque"
        assert request.cookies["csrf_token"] == "csrf"
        _MUTATION_FLAGS.append(mutation)
        yield SimpleNamespace(actor=actor, session_token="opaque", session=object())

    monkeypatch.setattr("app.routes.admin.authenticated_request", request_auth)
    authorization = _Authorization()
    app = create_app(
        Settings(environment="test"),
        container=SimpleNamespace(
            authentication=_Authentication(),
            authorization=authorization,
        ),
    )
    client = TestClient(app, base_url="https://rag.home.arpa")
    client.cookies.set("rag_session", "opaque")
    client.cookies.set("csrf_token", "csrf")
    headers = {"Origin": "https://rag.home.arpa", "X-CSRF-Token": "csrf"}

    created = client.post(
        "/api/admin/users",
        headers=headers,
        json={
            "username": "member.one",
            "display_name": "Member One",
            "role": "member",
        },
    )
    assert created.status_code == 201
    assert created.json()["activation_code"] == "single-use-code"
    assert (
        client.post(
            f"/api/admin/users/{authorization.user_id}/reset", headers=headers
        ).json()["activation_code"]
        == "reset-code"
    )
    assert (
        client.patch(
            f"/api/admin/users/{authorization.user_id}",
            headers=headers,
            json={"role": "member", "status": "disabled"},
        ).status_code
        == 204
    )
    assert client.post(
        "/api/admin/teams",
        headers=headers,
        json={"name": "Research"},
    ).json()["team_id"] == str(authorization.team_id)
    assert client.delete(
        f"/api/admin/teams/{authorization.team_id}", headers=headers
    ).json()["preview_id"] == str(authorization.preview_id)
    assert (
        client.post(
            f"/api/admin/teams/{authorization.team_id}/members",
            headers=headers,
            json={"user_id": str(authorization.user_id)},
        ).status_code
        == 200
    )
    assert (
        client.delete(
            f"/api/admin/teams/{authorization.team_id}/members/{authorization.user_id}",
            headers=headers,
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/api/admin/acl/preview",
            headers=headers,
            json={
                "operation": {
                    "kind": "set_grant",
                    "node_id": str(uuid.uuid4()),
                    "user_id": str(authorization.user_id),
                    "present": True,
                }
            },
        ).status_code
        == 200
    )
    folder_id = uuid.uuid4()
    assert (
        client.post(
            "/api/admin/acl/preview",
            headers=headers,
            json={
                "operation": {
                    "kind": "set_create_children_grant",
                    "folder_id": str(folder_id),
                    "team_id": str(authorization.team_id),
                    "present": True,
                }
            },
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/api/admin/acl/apply",
            headers=headers,
            json={
                "preview_id": str(authorization.preview_id),
                "impact_digest": "a" * 64,
            },
        ).json()["authorization_version"]
        == 12
    )

    assert _MUTATION_FLAGS[-10:] == [True] * 10
    preview_operations = [
        call[1]["operation"] for call in authorization.calls if call[0] == "preview_acl"
    ]
    assert preview_operations[0] == {
        "kind": "set_team_active",
        "team_id": str(authorization.team_id),
        "active": False,
    }
    assert preview_operations[2]["present"] is False
    assert preview_operations[-1] == {
        "kind": "set_create_children_grant",
        "folder_id": str(folder_id),
        "team_id": str(authorization.team_id),
        "present": True,
    }


def test_admin_identifier_mutation_is_nonenumerable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_id = uuid.uuid4()
    actor = ActorContext(user_id, ActorRole.ADMIN, 1, 1, uuid.uuid4())

    @asynccontextmanager
    async def request_auth(request: object, *, mutation: bool = False):
        yield SimpleNamespace(actor=actor, session_token="opaque", session=object())

    monkeypatch.setattr("app.routes.admin.authenticated_request", request_auth)
    authorization = _Authorization()
    authorization.reset_error = InaccessibleResource()
    app = create_app(
        Settings(environment="test"),
        container=SimpleNamespace(
            authentication=_Authentication(),
            authorization=authorization,
        ),
    )
    response = TestClient(app).post(f"/api/admin/users/{uuid.uuid4()}/reset")
    assert response.status_code == 404
    assert response.json() == {"detail": "resource not found"}


@pytest.mark.parametrize(
    ("error", "status_code", "detail"),
    [
        (
            InvalidAuthorizationRequest("invalid ACL operation"),
            422,
            "invalid ACL operation",
        ),
        (
            AuthorizationConflict("administration state is stale"),
            409,
            "administration state is stale",
        ),
        (InaccessibleResource(), 404, "resource not found"),
    ],
)
def test_admin_mutation_domain_errors_are_not_reported_as_unavailable(
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
    status_code: int,
    detail: str,
) -> None:
    actor = ActorContext(uuid.uuid4(), ActorRole.ADMIN, 1, 1, uuid.uuid4())

    @asynccontextmanager
    async def request_auth(request: object, *, mutation: bool = False):
        yield SimpleNamespace(actor=actor, session_token="opaque", session=object())

    monkeypatch.setattr("app.routes.admin.authenticated_request", request_auth)
    authorization = _Authorization()
    authorization.reset_error = error
    app = create_app(
        Settings(environment="test"),
        container=SimpleNamespace(
            authentication=_Authentication(),
            authorization=authorization,
        ),
    )
    response = TestClient(app).post(f"/api/admin/users/{uuid.uuid4()}/reset")
    assert response.status_code == status_code
    assert response.json() == {"detail": detail}
