from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request, status

from app.schemas.library import (
    AccountTeamListResponse,
    FolderCreateRequest,
    LibraryBrowseResponse,
    LibraryNodeResponse,
    LibraryTreeNodeResponse,
    NodeMovePreviewRequest,
    NodeMovePreviewResponse,
    NodePatchRequest,
)
from app.security.request_auth import authenticated_request
from app.services.authorization import AuthorizationUnavailable
from app.services.library import (
    InvalidLibraryName,
    LibraryConflict,
    LibraryCorruption,
    LibraryNotFound,
)

router = APIRouter(prefix="/api/library", tags=["library"])
account_router = APIRouter(prefix="/api/account", tags=["account"])


@account_router.get("/teams", response_model=AccountTeamListResponse)
async def account_teams(request: Request) -> AccountTeamListResponse:
    try:
        async with authenticated_request(request) as authenticated:
            teams = await request.app.state.container.authorization.account_teams(
                authenticated.actor, authenticated.session_token
            )
    except AuthorizationUnavailable as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "account teams are unavailable",
        ) from exc
    return AccountTeamListResponse(
        teams=teams,
        requires_team_selection=bool(teams),
    )


def _response(node: object, actor: object) -> LibraryNodeResponse:
    return LibraryNodeResponse(
        node_id=node.node_id,
        parent_id=node.parent_id,
        kind=node.kind,
        name=node.name,
        logical_path=node.logical_path,
        document_id=node.document_id,
        uploader_user_id=node.uploader_user_id,
        can_manage=node.can_manage(actor),
        can_create_children=node.can_create_children,
        readable_document_count=node.readable_document_count,
    )


def _raise_library_error(exc: Exception) -> None:
    if isinstance(exc, InvalidLibraryName):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    if isinstance(exc, LibraryNotFound):
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    if isinstance(exc, LibraryConflict):
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    if isinstance(exc, LibraryCorruption):
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(exc)) from exc
    raise exc


@router.get("/browse", response_model=LibraryBrowseResponse)
async def browse_library(
    request: Request,
    parent_id: UUID | None = None,
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=100, ge=1, le=100),
) -> LibraryBrowseResponse:
    try:
        async with authenticated_request(request) as authenticated:
            result = await request.app.state.container.library.browse(
                authenticated.actor,
                authenticated.session,
                parent_id,
                page=page,
                limit=limit,
            )
    except Exception as exc:
        _raise_library_error(exc)
        raise
    return LibraryBrowseResponse(
        parent_id=result.parent_id,
        breadcrumbs=[
            _response(node, authenticated.actor) for node in result.breadcrumbs
        ],
        children=[_response(node, authenticated.actor) for node in result.children],
        page=result.page,
        limit=result.limit,
        total=result.total,
    )


def _tree_response(node: object) -> LibraryTreeNodeResponse:
    return LibraryTreeNodeResponse(
        node_id=node.node_id,
        parent_id=node.parent_id,
        name=node.name,
        logical_path=node.logical_path,
        children=[_tree_response(child) for child in node.children],
    )


@router.get("/tree", response_model=list[LibraryTreeNodeResponse])
async def library_tree(request: Request) -> list[LibraryTreeNodeResponse]:
    try:
        async with authenticated_request(request) as authenticated:
            nodes = await request.app.state.container.library.tree(
                authenticated.actor, authenticated.session
            )
    except Exception as exc:
        _raise_library_error(exc)
        raise
    return [_tree_response(node) for node in nodes]


@router.post(
    "/folders",
    response_model=LibraryNodeResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_folder(
    request: Request, payload: FolderCreateRequest
) -> LibraryNodeResponse:
    try:
        async with authenticated_request(request, mutation=True) as authenticated:
            node = await request.app.state.container.library.create_folder(
                authenticated.actor,
                authenticated.session,
                payload.name,
                payload.parent_id,
            )
    except Exception as exc:
        _raise_library_error(exc)
        raise
    return _response(node, authenticated.actor)


@router.patch("/nodes/{node_id}", response_model=LibraryNodeResponse)
async def patch_node(
    request: Request, node_id: UUID, payload: NodePatchRequest
) -> LibraryNodeResponse:
    try:
        async with authenticated_request(request, mutation=True) as authenticated:
            node = await request.app.state.container.library.update_node(
                authenticated.actor,
                authenticated.session,
                node_id,
                name=payload.name,
                parent_id=payload.parent_id,
                update_name="name" in payload.model_fields_set,
                update_parent="parent_id" in payload.model_fields_set,
                preview_id=payload.preview_id,
                impact_digest=payload.impact_digest,
            )
    except Exception as exc:
        _raise_library_error(exc)
        raise
    return _response(node, authenticated.actor)


@router.post(
    "/nodes/{node_id}/move-preview",
    response_model=NodeMovePreviewResponse,
)
async def preview_node_move(
    request: Request,
    node_id: UUID,
    payload: NodeMovePreviewRequest,
) -> NodeMovePreviewResponse:
    try:
        async with authenticated_request(request, mutation=True) as authenticated:
            preview = await request.app.state.container.library.preview_move(
                authenticated.actor,
                authenticated.session,
                node_id,
                payload.parent_id,
            )
    except Exception as exc:
        _raise_library_error(exc)
        raise
    return NodeMovePreviewResponse(
        preview_id=preview.preview_id,
        impact_digest=preview.impact_digest,
        impact=preview.impact,
    )


@router.delete("/folders/{folder_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_folder(request: Request, folder_id: UUID) -> None:
    try:
        async with authenticated_request(request, mutation=True) as authenticated:
            await request.app.state.container.library.delete_folder(
                authenticated.actor, authenticated.session, folder_id
            )
    except Exception as exc:
        _raise_library_error(exc)
