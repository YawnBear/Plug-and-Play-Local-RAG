from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request, status

from app.schemas.admin import (
    AdminAccessContextResponse,
    AdminAclApplyRequest,
    AdminAclApplyResponse,
    AdminAclPreviewRequest,
    AdminAclPreviewResponse,
    AdminActivationResponse,
    AdminAuditListResponse,
    AdminGrantListResponse,
    AdminTeamCreateRequest,
    AdminTeamCreateResponse,
    AdminTeamListResponse,
    AdminTeamMemberRequest,
    AdminUserCreateRequest,
    AdminUserListResponse,
    AdminUserMutationRequest,
)
from app.security.request_auth import authenticated_request
from app.services.authorization import (
    AuthorizationConflict,
    AuthorizationUnavailable,
    CapabilityDenied,
    InaccessibleResource,
    InvalidAuthorizationRequest,
)

router = APIRouter(prefix="/api/admin", tags=["administration"])


def _raise_authorization_error(exc: Exception) -> None:
    if isinstance(exc, CapabilityDenied):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "capability denied") from exc
    if isinstance(exc, InaccessibleResource):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "resource not found") from exc
    if isinstance(exc, InvalidAuthorizationRequest):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            str(exc) or "invalid administration request",
        ) from exc
    if isinstance(exc, AuthorizationConflict):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            str(exc) or "administration conflict",
        ) from exc
    if isinstance(exc, AuthorizationUnavailable):
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "administration is not available",
        ) from exc
    if isinstance(exc, ValueError):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            str(exc),
        ) from exc
    raise exc


@router.get("/users", response_model=AdminUserListResponse)
async def list_users(request: Request) -> AdminUserListResponse:
    try:
        async with authenticated_request(request) as auth:
            users = await request.app.state.container.authorization.users(
                auth.actor, auth.session_token
            )
    except Exception as exc:
        _raise_authorization_error(exc)
        raise
    return AdminUserListResponse(users=users)


@router.get("/teams", response_model=AdminTeamListResponse)
async def list_teams(request: Request) -> AdminTeamListResponse:
    try:
        async with authenticated_request(request) as auth:
            teams = await request.app.state.container.authorization.teams(
                auth.actor, auth.session_token
            )
    except Exception as exc:
        _raise_authorization_error(exc)
        raise
    return AdminTeamListResponse(teams=teams)


@router.get("/grants", response_model=AdminGrantListResponse)
async def list_grants(request: Request) -> AdminGrantListResponse:
    try:
        async with authenticated_request(request) as auth:
            grants = await request.app.state.container.authorization.grants(
                auth.actor, auth.session_token
            )
    except Exception as exc:
        _raise_authorization_error(exc)
        raise
    return AdminGrantListResponse(grants=grants)


@router.get("/access", response_model=AdminAccessContextResponse)
async def access_context(
    request: Request, node_id: UUID = Query(...)
) -> AdminAccessContextResponse:
    try:
        async with authenticated_request(request) as auth:
            return await request.app.state.container.authorization.access_context(
                auth.actor,
                auth.session_token,
                node_id=node_id,
            )
    except Exception as exc:
        _raise_authorization_error(exc)
        raise


@router.get("/audit", response_model=AdminAuditListResponse)
async def list_audit_events(
    request: Request, limit: int = Query(default=100, ge=1, le=500)
) -> AdminAuditListResponse:
    try:
        async with authenticated_request(request) as auth:
            events = await request.app.state.container.authorization.audit(
                auth.actor, auth.session_token, limit=limit
            )
    except Exception as exc:
        _raise_authorization_error(exc)
        raise
    return AdminAuditListResponse(events=events)


@router.post(
    "/users",
    response_model=AdminActivationResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_user(
    request: Request, payload: AdminUserCreateRequest
) -> AdminActivationResponse:
    try:
        async with authenticated_request(request, mutation=True) as auth:
            issued = await request.app.state.container.authorization.create_user(
                auth.actor,
                auth.session_token,
                username=payload.username,
                display_name=payload.display_name,
                role=payload.role.value,
            )
    except Exception as exc:
        _raise_authorization_error(exc)
        raise
    return AdminActivationResponse(
        user_id=issued.user_id,
        activation_code=issued.activation_code,
    )


@router.post(
    "/users/{user_id}/reset",
    response_model=AdminActivationResponse,
)
async def reset_user(request: Request, user_id: UUID) -> AdminActivationResponse:
    try:
        async with authenticated_request(request, mutation=True) as auth:
            issued = await request.app.state.container.authorization.reset_user(
                auth.actor,
                auth.session_token,
                user_id=user_id,
            )
    except Exception as exc:
        _raise_authorization_error(exc)
        raise
    return AdminActivationResponse(
        user_id=issued.user_id,
        activation_code=issued.activation_code,
    )


@router.patch("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def mutate_user(
    request: Request,
    user_id: UUID,
    payload: AdminUserMutationRequest,
) -> None:
    try:
        async with authenticated_request(request, mutation=True) as auth:
            await request.app.state.container.authorization.set_user(
                auth.actor,
                auth.session_token,
                user_id=user_id,
                role=payload.role.value,
                status=payload.status,
            )
    except Exception as exc:
        _raise_authorization_error(exc)


@router.post(
    "/teams",
    response_model=AdminTeamCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_team(
    request: Request, payload: AdminTeamCreateRequest
) -> AdminTeamCreateResponse:
    try:
        async with authenticated_request(request, mutation=True) as auth:
            team_id = await request.app.state.container.authorization.create_team(
                auth.actor,
                auth.session_token,
                name=payload.name,
            )
    except Exception as exc:
        _raise_authorization_error(exc)
        raise
    return AdminTeamCreateResponse(team_id=team_id)


async def _preview(
    request: Request, operation: dict[str, object]
) -> AdminAclPreviewResponse:
    try:
        async with authenticated_request(request, mutation=True) as auth:
            preview = await request.app.state.container.authorization.preview_acl(
                auth.actor,
                auth.session_token,
                operation=operation,
            )
    except Exception as exc:
        _raise_authorization_error(exc)
        raise
    return AdminAclPreviewResponse(
        preview_id=preview.preview_id,
        impact_digest=preview.impact_digest,
        impact=preview.impact,
    )


@router.delete(
    "/teams/{team_id}",
    response_model=AdminAclPreviewResponse,
)
async def delete_team(request: Request, team_id: UUID) -> AdminAclPreviewResponse:
    return await _preview(
        request,
        {"kind": "set_team_active", "team_id": str(team_id), "active": False},
    )


@router.post(
    "/teams/{team_id}/members",
    response_model=AdminAclPreviewResponse,
)
async def add_team_member(
    request: Request,
    team_id: UUID,
    payload: AdminTeamMemberRequest,
) -> AdminAclPreviewResponse:
    return await _preview(
        request,
        {
            "kind": "set_membership",
            "team_id": str(team_id),
            "user_id": str(payload.user_id),
            "present": True,
        },
    )


@router.delete(
    "/teams/{team_id}/members/{user_id}",
    response_model=AdminAclPreviewResponse,
)
async def remove_team_member(
    request: Request,
    team_id: UUID,
    user_id: UUID,
) -> AdminAclPreviewResponse:
    return await _preview(
        request,
        {
            "kind": "set_membership",
            "team_id": str(team_id),
            "user_id": str(user_id),
            "present": False,
        },
    )


@router.post("/acl/preview", response_model=AdminAclPreviewResponse)
async def preview_acl(
    request: Request, payload: AdminAclPreviewRequest
) -> AdminAclPreviewResponse:
    operation = payload.operation.model_dump(
        mode="json",
        exclude_none=payload.operation.kind == "set_create_children_grant",
    )
    return await _preview(
        request,
        operation,
    )


@router.post("/acl/apply", response_model=AdminAclApplyResponse)
async def apply_acl(
    request: Request, payload: AdminAclApplyRequest
) -> AdminAclApplyResponse:
    try:
        async with authenticated_request(request, mutation=True) as auth:
            authorization_version = (
                await request.app.state.container.authorization.apply_acl(
                    auth.actor,
                    auth.session_token,
                    preview_id=payload.preview_id,
                    impact_digest=payload.impact_digest,
                )
            )
    except Exception as exc:
        _raise_authorization_error(exc)
        raise
    return AdminAclApplyResponse(authorization_version=authorization_version)
