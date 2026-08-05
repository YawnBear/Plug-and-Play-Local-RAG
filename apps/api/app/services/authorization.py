import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.unit_of_work import AuthenticatedUnitOfWork
from app.schemas.admin import (
    AdminAccessContextResponse,
    AdminAclImpact,
    AdminAuditEvent,
    AdminGrant,
    AdminTeam,
)
from app.schemas.auth import AuthUser
from app.schemas.library import AccountTeamResponse
from app.security.actor import ActorContext, ActorRole
from app.security.identity import normalize_display_name, normalize_username
from app.security.tokens import hash_opaque_token, issue_opaque_token


class AuthorizationError(Exception):
    pass


class CapabilityDenied(AuthorizationError):
    pass


class InaccessibleResource(AuthorizationError):
    pass


class InvalidAuthorizationRequest(AuthorizationError):
    pass


class AuthorizationConflict(AuthorizationError):
    pass


class AuthorizationUnavailable(AuthorizationError):
    pass


@dataclass(frozen=True, slots=True)
class IssuedActivation:
    user_id: UUID
    activation_code: str


@dataclass(frozen=True, slots=True)
class AclPreview:
    preview_id: UUID
    impact_digest: str
    impact: AdminAclImpact


class AdminGateway(Protocol):
    """Controlled database-function boundary for administrative operations."""

    async def list_users(self, session_token_hash: str) -> list[AuthUser]: ...

    async def list_teams(self, session_token_hash: str) -> list[AdminTeam]: ...

    async def list_account_teams(
        self, session_token_hash: str
    ) -> list[AccountTeamResponse]: ...

    async def list_grants(self, session_token_hash: str) -> list[AdminGrant]: ...

    async def access_context(
        self, session_token_hash: str, *, node_id: UUID
    ) -> AdminAccessContextResponse: ...

    async def list_audit_events(
        self, session_token_hash: str, *, limit: int
    ) -> list[AdminAuditEvent]: ...

    async def create_user(
        self,
        session_token_hash: str,
        *,
        username: str,
        display_name: str,
        role: str,
        challenge_token_hash: str,
        expires_at: datetime,
    ) -> UUID: ...

    async def reset_user(
        self,
        session_token_hash: str,
        *,
        user_id: UUID,
        challenge_token_hash: str,
        expires_at: datetime,
    ) -> None: ...

    async def set_user(
        self,
        session_token_hash: str,
        *,
        user_id: UUID,
        role: str,
        status: str,
    ) -> None: ...

    async def create_team(
        self,
        session_token_hash: str,
        *,
        name: str,
        name_key: str,
    ) -> UUID: ...

    async def preview_acl(
        self, session_token_hash: str, *, operation: dict[str, object]
    ) -> AclPreview: ...

    async def apply_acl(
        self,
        session_token_hash: str,
        *,
        preview_id: UUID,
        impact_digest: str,
    ) -> int: ...


class UnavailableAdminGateway:
    async def list_users(self, session_token_hash: str) -> list[AuthUser]:
        raise AuthorizationUnavailable("admin database functions are unavailable")

    async def list_teams(self, session_token_hash: str) -> list[AdminTeam]:
        raise AuthorizationUnavailable("admin database functions are unavailable")

    async def list_account_teams(
        self, session_token_hash: str
    ) -> list[AccountTeamResponse]:
        raise AuthorizationUnavailable("account teams are unavailable")

    async def list_grants(self, session_token_hash: str) -> list[AdminGrant]:
        raise AuthorizationUnavailable("admin database functions are unavailable")

    async def access_context(
        self, session_token_hash: str, *, node_id: UUID
    ) -> AdminAccessContextResponse:
        raise AuthorizationUnavailable("admin database functions are unavailable")

    async def list_audit_events(
        self, session_token_hash: str, *, limit: int
    ) -> list[AdminAuditEvent]:
        raise AuthorizationUnavailable("admin database functions are unavailable")

    async def create_user(self, session_token_hash: str, **_kwargs: object) -> UUID:
        raise AuthorizationUnavailable("admin database functions are unavailable")

    async def reset_user(self, session_token_hash: str, **_kwargs: object) -> None:
        raise AuthorizationUnavailable("admin database functions are unavailable")

    async def set_user(self, session_token_hash: str, **_kwargs: object) -> None:
        raise AuthorizationUnavailable("admin database functions are unavailable")

    async def create_team(self, session_token_hash: str, **_kwargs: object) -> UUID:
        raise AuthorizationUnavailable("admin database functions are unavailable")

    async def preview_acl(
        self, session_token_hash: str, **_kwargs: object
    ) -> AclPreview:
        raise AuthorizationUnavailable("admin database functions are unavailable")

    async def apply_acl(self, session_token_hash: str, **_kwargs: object) -> int:
        raise AuthorizationUnavailable("admin database functions are unavailable")


class DatabaseAdminGateway(UnavailableAdminGateway):
    """Admin gateway that activates each transaction from its opaque session."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def list_users(self, session_token_hash: str) -> list[AuthUser]:
        async with self._activated_session(session_token_hash) as session:
            rows = (
                await session.execute(
                    text(
                        "SELECT id, username, display_name, role, status "
                        "FROM v4_admin_users ORDER BY username, id"
                    )
                )
            ).all()
            return [
                AuthUser(
                    id=row.id,
                    username=row.username,
                    display_name=row.display_name,
                    role=row.role,
                    status=row.status,
                )
                for row in rows
            ]

    async def list_teams(self, session_token_hash: str) -> list[AdminTeam]:
        async with self._activated_session(session_token_hash) as session:
            rows = (
                await session.execute(
                    text(
                        "SELECT id, name, is_active, member_ids, member_count "
                        "FROM v4_admin_teams "
                        "ORDER BY name, id"
                    )
                )
            ).all()
            return [
                AdminTeam(
                    id=row.id,
                    name=row.name,
                    is_active=row.is_active,
                    member_ids=list(row.member_ids),
                    member_count=row.member_count,
                )
                for row in rows
            ]

    async def list_account_teams(
        self, session_token_hash: str
    ) -> list[AccountTeamResponse]:
        async with self._activated_session(session_token_hash) as session:
            rows = (
                await session.execute(
                    text(
                        "SELECT team_id, team_name, is_active "
                        "FROM v4_account_active_teams()"
                    )
                )
            ).all()
            return [
                AccountTeamResponse(
                    id=row.team_id,
                    name=row.team_name,
                    is_active=row.is_active,
                )
                for row in rows
            ]

    async def list_grants(self, session_token_hash: str) -> list[AdminGrant]:
        async with self._activated_session(session_token_hash) as session:
            rows = (
                await session.execute(
                    text(
                        "SELECT id, node_id, user_id, team_id "
                        "FROM v4_admin_grants ORDER BY id"
                    )
                )
            ).all()
            return [
                AdminGrant(
                    id=row.id,
                    node_id=row.node_id,
                    user_id=row.user_id,
                    team_id=row.team_id,
                )
                for row in rows
            ]

    async def access_context(
        self, session_token_hash: str, *, node_id: UUID
    ) -> AdminAccessContextResponse:
        async with self._activated_session(session_token_hash) as session:
            payload = await session.scalar(
                text("SELECT v4_admin_access_context(:node_id)"),
                {"node_id": node_id},
            )
            return AdminAccessContextResponse.model_validate(payload)

    async def list_audit_events(
        self, session_token_hash: str, *, limit: int
    ) -> list[AdminAuditEvent]:
        async with self._activated_session(session_token_hash) as session:
            rows = (
                await session.execute(
                    text(
                        "SELECT id, actor_user_id, event_type, target_type, "
                        "target_id, details, correlation_id, created_at "
                        "FROM v4_admin_audit "
                        "ORDER BY created_at DESC, id DESC LIMIT :limit"
                    ),
                    {"limit": limit},
                )
            ).all()
            return [
                AdminAuditEvent(
                    id=row.id,
                    actor_user_id=row.actor_user_id,
                    event_type=row.event_type,
                    target_type=row.target_type,
                    target_id=row.target_id,
                    details=row.details,
                    correlation_id=row.correlation_id,
                    created_at=row.created_at,
                )
                for row in rows
            ]

    async def create_user(
        self,
        session_token_hash: str,
        *,
        username: str,
        display_name: str,
        role: str,
        challenge_token_hash: str,
        expires_at: datetime,
    ) -> UUID:
        result = await self._scalar_mutation(
            session_token_hash,
            "SELECT v4_admin_create_user("
            ":username, :display_name, :role, :challenge_token_hash, :expires_at)",
            {
                "username": username,
                "display_name": display_name,
                "role": role,
                "challenge_token_hash": challenge_token_hash,
                "expires_at": expires_at,
            },
        )
        if result is None:
            raise InaccessibleResource
        return UUID(str(result))

    async def reset_user(
        self,
        session_token_hash: str,
        *,
        user_id: UUID,
        challenge_token_hash: str,
        expires_at: datetime,
    ) -> None:
        await self._scalar_mutation(
            session_token_hash,
            "SELECT v4_admin_reset_user(:user_id, :challenge_token_hash, :expires_at)",
            {
                "user_id": user_id,
                "challenge_token_hash": challenge_token_hash,
                "expires_at": expires_at,
            },
        )

    async def set_user(
        self,
        session_token_hash: str,
        *,
        user_id: UUID,
        role: str,
        status: str,
    ) -> None:
        await self._scalar_mutation(
            session_token_hash,
            "SELECT v4_admin_set_user(:user_id, :role, :status)",
            {"user_id": user_id, "role": role, "status": status},
        )

    async def create_team(
        self,
        session_token_hash: str,
        *,
        name: str,
        name_key: str,
    ) -> UUID:
        result = await self._scalar_mutation(
            session_token_hash,
            "SELECT v4_admin_create_team(:name, :name_key)",
            {"name": name, "name_key": name_key},
        )
        if result is None:
            raise InaccessibleResource
        return UUID(str(result))

    async def preview_acl(
        self, session_token_hash: str, *, operation: dict[str, object]
    ) -> AclPreview:
        try:
            async with self._activated_session(session_token_hash) as session:
                row = (
                    await session.execute(
                        text(
                            "SELECT preview_id, impact_digest, "
                            "impact "
                            "FROM v4_admin_preview_acl(CAST(:operation AS jsonb))"
                        ),
                        {
                            "operation": json.dumps(
                                operation, ensure_ascii=False, separators=(",", ":")
                            )
                        },
                    )
                ).one()
                return AclPreview(
                    preview_id=row.preview_id,
                    impact_digest=row.impact_digest,
                    impact=AdminAclImpact.model_validate(row.impact),
                )
        except SQLAlchemyError as exc:
            raise _authorization_error(exc) from exc

    async def apply_acl(
        self,
        session_token_hash: str,
        *,
        preview_id: UUID,
        impact_digest: str,
    ) -> int:
        result = await self._scalar_mutation(
            session_token_hash,
            "SELECT v4_admin_apply_acl(:preview_id, :impact_digest)",
            {"preview_id": preview_id, "impact_digest": impact_digest},
        )
        if result is None:
            raise InaccessibleResource
        return int(result)

    async def _scalar_mutation(
        self,
        session_token_hash: str,
        statement: str,
        parameters: dict[str, object],
    ) -> object | None:
        try:
            async with self._activated_session(session_token_hash) as session:
                return await session.scalar(text(statement), parameters)
        except SQLAlchemyError as exc:
            raise _authorization_error(exc) from exc

    @asynccontextmanager
    async def _activated_session(
        self, session_token_hash: str
    ) -> AsyncIterator[AsyncSession]:
        try:
            async with AuthenticatedUnitOfWork(
                self._session_factory, session_token_hash
            ) as unit:
                if unit.session is None:
                    raise AuthorizationUnavailable(
                        "admin database functions are unavailable"
                    )
                yield unit.session
        except SQLAlchemyError as exc:
            raise _authorization_error(exc) from exc


def _authorization_error(exc: SQLAlchemyError) -> AuthorizationError:
    original = getattr(exc, "orig", None)
    sqlstate = getattr(original, "sqlstate", None)
    if sqlstate in {"28000", "42501"}:
        return CapabilityDenied()
    if sqlstate in {"02000", "P0002"}:
        return InaccessibleResource()
    if sqlstate == "22023":
        return InvalidAuthorizationRequest(_database_error_message(original))
    if sqlstate == "23505":
        return AuthorizationConflict("resource already exists")
    if sqlstate in {"40001", "RAG02"}:
        return AuthorizationConflict("administration state is stale")
    if sqlstate == "RAG03":
        return AuthorizationConflict("at least one active administrator is required")
    if sqlstate == "RAG04":
        return AuthorizationConflict("deleted user state is irreversible")
    return AuthorizationUnavailable("admin database functions are unavailable")


def _database_error_message(original: object) -> str:
    diagnostic = getattr(original, "diag", None)
    primary = getattr(diagnostic, "message_primary", None)
    if isinstance(primary, str) and primary.strip():
        return primary.strip()[:500]
    message = str(original).strip()
    return (message or "invalid administration request")[:500]


def require_admin(actor: ActorContext) -> None:
    if actor.role is not ActorRole.ADMIN:
        raise CapabilityDenied


class AuthorizationService:
    def __init__(self, gateway: AdminGateway) -> None:
        self.gateway = gateway

    async def users(self, actor: ActorContext, session_token: str) -> list[AuthUser]:
        require_admin(actor)
        return await self.gateway.list_users(hash_opaque_token(session_token))

    async def teams(self, actor: ActorContext, session_token: str) -> list[AdminTeam]:
        require_admin(actor)
        return await self.gateway.list_teams(hash_opaque_token(session_token))

    async def account_teams(
        self, actor: ActorContext, session_token: str
    ) -> list[AccountTeamResponse]:
        return await self.gateway.list_account_teams(hash_opaque_token(session_token))

    async def grants(self, actor: ActorContext, session_token: str) -> list[AdminGrant]:
        require_admin(actor)
        return await self.gateway.list_grants(hash_opaque_token(session_token))

    async def access_context(
        self, actor: ActorContext, session_token: str, *, node_id: UUID
    ) -> AdminAccessContextResponse:
        require_admin(actor)
        return await self.gateway.access_context(
            hash_opaque_token(session_token), node_id=node_id
        )

    async def audit(
        self, actor: ActorContext, session_token: str, *, limit: int
    ) -> list[AdminAuditEvent]:
        require_admin(actor)
        return await self.gateway.list_audit_events(
            hash_opaque_token(session_token), limit=limit
        )

    async def create_user(
        self,
        actor: ActorContext,
        session_token: str,
        *,
        username: str,
        display_name: str,
        role: str,
    ) -> IssuedActivation:
        require_admin(actor)
        canonical_username = normalize_username(username)
        canonical_display_name = normalize_display_name(display_name)
        if role not in {"admin", "member"}:
            raise ValueError("role must be admin or member")
        challenge = issue_opaque_token()
        user_id = await self.gateway.create_user(
            hash_opaque_token(session_token),
            username=canonical_username,
            display_name=canonical_display_name,
            role=role,
            challenge_token_hash=challenge.digest,
            expires_at=datetime.now(UTC) + timedelta(minutes=30),
        )
        return IssuedActivation(user_id=user_id, activation_code=challenge.plaintext)

    async def reset_user(
        self,
        actor: ActorContext,
        session_token: str,
        *,
        user_id: UUID,
    ) -> IssuedActivation:
        require_admin(actor)
        challenge = issue_opaque_token()
        await self.gateway.reset_user(
            hash_opaque_token(session_token),
            user_id=user_id,
            challenge_token_hash=challenge.digest,
            expires_at=datetime.now(UTC) + timedelta(minutes=30),
        )
        return IssuedActivation(user_id=user_id, activation_code=challenge.plaintext)

    async def set_user(
        self,
        actor: ActorContext,
        session_token: str,
        *,
        user_id: UUID,
        role: str,
        status: str,
    ) -> None:
        require_admin(actor)
        if role not in {"admin", "member"}:
            raise ValueError("role must be admin or member")
        if status not in {"active", "disabled", "deleted"}:
            raise ValueError("status must be active, disabled, or deleted")
        await self.gateway.set_user(
            hash_opaque_token(session_token),
            user_id=user_id,
            role=role,
            status=status,
        )

    async def create_team(
        self,
        actor: ActorContext,
        session_token: str,
        *,
        name: str,
    ) -> UUID:
        require_admin(actor)
        canonical_name = normalize_display_name(name)
        return await self.gateway.create_team(
            hash_opaque_token(session_token),
            name=canonical_name,
            name_key=canonical_name.casefold(),
        )

    async def preview_acl(
        self,
        actor: ActorContext,
        session_token: str,
        *,
        operation: dict[str, object],
    ) -> AclPreview:
        require_admin(actor)
        return await self.gateway.preview_acl(
            hash_opaque_token(session_token), operation=operation
        )

    async def apply_acl(
        self,
        actor: ActorContext,
        session_token: str,
        *,
        preview_id: UUID,
        impact_digest: str,
    ) -> int:
        require_admin(actor)
        return await self.gateway.apply_acl(
            hash_opaque_token(session_token),
            preview_id=preview_id,
            impact_digest=impact_digest,
        )
