from fastapi import APIRouter, HTTPException, Query, Request, status

from app.schemas.search import LibrarySearchItem, LibrarySearchResponse
from app.security.request_auth import authenticated_request
from app.services.search import SearchValidation

router = APIRouter(prefix="/api/search", tags=["search"])


@router.get("", response_model=LibrarySearchResponse)
async def search_library(
    request: Request,
    query: str = Query(min_length=1, max_length=200),
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=20, ge=1, le=100),
) -> LibrarySearchResponse:
    try:
        async with authenticated_request(request) as authenticated:
            result = await request.app.state.container.search.search(
                authenticated.actor,
                authenticated.session,
                query,
                page=page,
                limit=limit,
            )
    except SearchValidation as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    return LibrarySearchResponse(
        query=result.query,
        page=result.page,
        limit=result.limit,
        total=result.total,
        items=[
            LibrarySearchItem(
                document_id=item.document_id,
                node_id=item.node_id,
                filename=item.filename,
                display_name=item.display_name,
                logical_path=item.logical_path,
                page_start=item.page_start,
                page_end=item.page_end,
                match_kinds=list(item.match_kinds),
                rank=item.rank,
            )
            for item in result.items
        ],
        correlation_id=result.correlation_id,
        stage_timings_ms=result.stage_timings_ms,
    )
