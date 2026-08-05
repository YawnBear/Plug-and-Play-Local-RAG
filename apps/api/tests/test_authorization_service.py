import asyncio
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from sqlalchemy.exc import DBAPIError

from app.schemas.admin import AdminAclImpact
from app.schemas.auth import AuthUser
from app.schemas.library import AccountTeamResponse
from app.security.actor import ActorContext, ActorRole
from app.security.tokens import hash_opaque_token
from app.services import authorization
from app.services.authorization import (
    AclPreview,
    AuthorizationConflict,
    AuthorizationService,
    CapabilityDenied,
    DatabaseAdminGateway,
    InaccessibleResource,
    InvalidAuthorizationRequest,
    _authorization_error,
)


def _actor(role: ActorRole = ActorRole.ADMIN) -> ActorContext:
    return ActorContext(
        user_id=uuid.uuid4(),
        role=role,
        authentication_version=1,
        authorization_version=1,
        session_id=uuid.uuid4(),
    )


class _Gateway:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, object]]] = []
        self.user_id = uuid.uuid4()
        self.preview_id = uuid.uuid4()

    async def list_users(self, token_hash: str) -> list[AuthUser]:
        self.calls.append(("list_users", token_hash, {}))
        return []

    async def list_teams(self, token_hash: str) -> list[object]:
        self.calls.append(("list_teams", token_hash, {}))
        return []

    async def list_account_teams(self, token_hash: str) -> list[AccountTeamResponse]:
        self.calls.append(("list_account_teams", token_hash, {}))
        return [
            AccountTeamResponse(
                id=self.user_id,
                name="Research",
                is_active=True,
            )
        ]

    async def list_grants(self, token_hash: str) -> list[object]:
        self.calls.append(("list_grants", token_hash, {}))
        return []

    async def list_audit_events(self, token_hash: str, *, limit: int) -> list[object]:
        self.calls.append(("list_audit_events", token_hash, {"limit": limit}))
        return []

    async def create_user(self, token_hash: str, **kwargs: object) -> uuid.UUID:
        self.calls.append(("create_user", token_hash, kwargs))
        return self.user_id

    async def reset_user(self, token_hash: str, **kwargs: object) -> None:
        self.calls.append(("reset_user", token_hash, kwargs))

    async def set_user(self, token_hash: str, **kwargs: object) -> None:
        self.calls.append(("set_user", token_hash, kwargs))

    async def create_team(self, token_hash: str, **kwargs: object) -> uuid.UUID:
        self.calls.append(("create_team", token_hash, kwargs))
        return self.user_id

    async def preview_acl(self, token_hash: str, **kwargs: object) -> AclPreview:
        self.calls.append(("preview_acl", token_hash, kwargs))
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

    async def apply_acl(self, token_hash: str, **kwargs: object) -> int:
        self.calls.append(("apply_acl", token_hash, kwargs))
        return 9


def test_admin_reads_require_admin_and_hash_session_token() -> None:
    gateway = _Gateway()
    service = AuthorizationService(gateway)

    asyncio.run(service.users(_actor(), "opaque-session"))
    asyncio.run(service.teams(_actor(), "opaque-session"))
    asyncio.run(service.grants(_actor(), "opaque-session"))
    asyncio.run(service.audit(_actor(), "opaque-session", limit=25))

    assert [call[0] for call in gateway.calls] == [
        "list_users",
        "list_teams",
        "list_grants",
        "list_audit_events",
    ]
    assert all(call[1] == hash_opaque_token("opaque-session") for call in gateway.calls)
    with pytest.raises(CapabilityDenied):
        asyncio.run(service.users(_actor(ActorRole.MEMBER), "opaque-session"))


def test_account_teams_are_available_to_members_and_hash_session_token() -> None:
    gateway = _Gateway()
    service = AuthorizationService(gateway)

    teams = asyncio.run(
        service.account_teams(_actor(ActorRole.MEMBER), "opaque-session")
    )

    assert teams == [
        AccountTeamResponse(
            id=gateway.user_id,
            name="Research",
            is_active=True,
        )
    ]
    assert gateway.calls == [
        (
            "list_account_teams",
            hash_opaque_token("opaque-session"),
            {},
        )
    ]


def test_admin_user_mutations_normalize_and_return_single_use_activation() -> None:
    gateway = _Gateway()
    service = AuthorizationService(gateway)
    actor = _actor()

    created = asyncio.run(
        service.create_user(
            actor,
            "opaque-session",
            username="Member.One",
            display_name="  Member One  ",
            role="member",
        )
    )
    reset = asyncio.run(
        service.reset_user(
            actor,
            "opaque-session",
            user_id=gateway.user_id,
        )
    )
    asyncio.run(
        service.set_user(
            actor,
            "opaque-session",
            user_id=gateway.user_id,
            role="member",
            status="disabled",
        )
    )

    create_call = gateway.calls[0]
    assert created.user_id == gateway.user_id
    assert created.activation_code
    assert create_call[2]["username"] == "member.one"
    assert create_call[2]["display_name"] == "Member One"
    assert create_call[2]["challenge_token_hash"] == hash_opaque_token(
        created.activation_code
    )
    assert create_call[2]["expires_at"] > datetime.now(UTC)
    assert reset.activation_code != created.activation_code
    assert gateway.calls[1][2]["challenge_token_hash"] == hash_opaque_token(
        reset.activation_code
    )


def test_admin_team_and_acl_mutations_preserve_strict_operation() -> None:
    gateway = _Gateway()
    service = AuthorizationService(gateway)
    actor = _actor()
    operation = {
        "kind": "set_membership",
        "team_id": str(uuid.uuid4()),
        "user_id": str(uuid.uuid4()),
        "present": True,
    }

    team_id = asyncio.run(
        service.create_team(actor, "opaque-session", name="  Research Team  ")
    )
    preview = asyncio.run(
        service.preview_acl(
            actor,
            "opaque-session",
            operation=operation,
        )
    )
    version = asyncio.run(
        service.apply_acl(
            actor,
            "opaque-session",
            preview_id=preview.preview_id,
            impact_digest=preview.impact_digest,
        )
    )

    assert team_id == gateway.user_id
    assert gateway.calls[0][2] == {
        "name": "Research Team",
        "name_key": "research team",
    }
    assert gateway.calls[1][2]["operation"] is operation
    assert version == 9


class _Result:
    def __init__(self, *, rows: list[object] | None = None, row: object = None) -> None:
        self.rows = rows or []
        self.row = row

    def all(self) -> list[object]:
        return self.rows

    def one(self) -> object:
        return self.row


class _Session:
    def __init__(self) -> None:
        now = datetime.now(UTC)
        self.execute_results = [
            _Result(
                rows=[
                    SimpleNamespace(
                        id=uuid.uuid4(),
                        username="admin.one",
                        display_name="Admin One",
                        role="admin",
                        status="active",
                    )
                ]
            ),
            _Result(
                rows=[
                    SimpleNamespace(
                        id=uuid.uuid4(),
                        actor_user_id=None,
                        event_type="user_created",
                        target_type="user",
                        target_id=uuid.uuid4(),
                        details={"role": "member"},
                        correlation_id=None,
                        created_at=now,
                    )
                ]
            ),
            _Result(
                row=SimpleNamespace(
                    preview_id=uuid.uuid4(),
                    impact_digest="b" * 64,
                    impact={
                        "user_ids": [],
                        "node_ids": [],
                        "document_ids": [],
                        "user_count": 0,
                        "node_count": 0,
                        "document_count": 0,
                    },
                )
            ),
        ]
        self.scalar_results: list[object] = [uuid.uuid4(), 11]
        self.calls: list[tuple[str, dict[str, object]]] = []

    async def execute(self, statement, parameters=None) -> _Result:
        self.calls.append((str(statement), parameters or {}))
        return self.execute_results.pop(0)

    async def scalar(self, statement, parameters=None) -> object:
        self.calls.append((str(statement), parameters or {}))
        return self.scalar_results.pop(0)


class _Unit:
    session: _Session

    def __init__(self, _factory: object, token_hash: str) -> None:
        assert token_hash == "a" * 64

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None


def test_database_admin_gateway_uses_activated_views_and_functions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _Session()
    _Unit.session = session
    monkeypatch.setattr(authorization, "AuthenticatedUnitOfWork", _Unit)
    gateway = DatabaseAdminGateway(lambda: session)

    users = asyncio.run(gateway.list_users("a" * 64))
    events = asyncio.run(gateway.list_audit_events("a" * 64, limit=25))
    created = asyncio.run(
        gateway.create_team("a" * 64, name="Research", name_key="research")
    )
    preview = asyncio.run(
        gateway.preview_acl(
            "a" * 64,
            operation={"kind": "set_team_active", "active": True},
        )
    )
    version = asyncio.run(
        gateway.apply_acl(
            "a" * 64,
            preview_id=preview.preview_id,
            impact_digest=preview.impact_digest,
        )
    )

    assert users[0].username == "admin.one"
    assert events[0].event_type == "user_created"
    assert isinstance(created, uuid.UUID)
    assert version == 11
    statements = "\n".join(call[0] for call in session.calls)
    assert "v4_admin_users" in statements
    assert "v4_admin_audit" in statements
    assert "v4_admin_create_team" in statements
    assert "v4_admin_preview_acl" in statements
    assert "v4_admin_apply_acl" in statements
    assert " FROM users" not in statements
    assert " FROM teams" not in statements
    assert " FROM audit_events" not in statements


@pytest.mark.parametrize(
    ("sqlstate", "error_type", "message"),
    [
        ("22023", InvalidAuthorizationRequest, "invalid ACL operation"),
        ("23505", AuthorizationConflict, "resource already exists"),
        ("40001", AuthorizationConflict, "administration state is stale"),
        ("RAG02", AuthorizationConflict, "administration state is stale"),
        (
            "RAG03",
            AuthorizationConflict,
            "at least one active administrator is required",
        ),
        (
            "RAG04",
            AuthorizationConflict,
            "deleted user state is irreversible",
        ),
        ("P0002", InaccessibleResource, ""),
    ],
)
def test_database_authorization_sqlstates_map_to_precise_domain_errors(
    sqlstate: str,
    error_type: type[Exception],
    message: str,
) -> None:
    class OriginalError(Exception):
        def __init__(self) -> None:
            super().__init__("invalid ACL operation")
            self.sqlstate = sqlstate

    translated = _authorization_error(
        DBAPIError("statement", {}, OriginalError(), False)
    )
    assert isinstance(translated, error_type)
    if message:
        assert str(translated) == message
