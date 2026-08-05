import asyncio
from collections.abc import AsyncIterator
from uuid import UUID, uuid4

from fastapi import (
    APIRouter,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
    status,
)
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from starlette.background import BackgroundTask
from starlette.responses import StreamingResponse

from app.db.repositories import DocumentRepository
from app.schemas.documents import (
    DocumentReingestAccepted,
    DocumentSummary,
    DocumentUploadAccepted,
)
from app.security.request_auth import authenticated_request
from app.services.document_content import (
    DocumentContentGone,
    DocumentContentNotFound,
    DocumentContentUnavailable,
    InvalidDocumentRange,
)
from app.services.document_reingest import (
    DocumentNotRetryable,
    DocumentReingestNotFound,
)
from app.services.documents import (
    DeletionPendingError,
    DocumentDeletionError,
    DocumentUploadParentNotFound,
    DuplicateUploadRequiresAccess,
    UploadReservationActive,
    UploadTooLargeError,
    UploadValidationError,
)
from app.services.library import (
    InvalidLibraryName,
    acquire_library_lock,
    locations_for_documents_in_session,
)
from app.services.object_lifecycle import ObjectIntegrityError
from app.services.object_storage import ObjectStoreError

router = APIRouter(prefix="/api/documents", tags=["documents"])

_DOCUMENT_NOT_RETRYABLE_DETAIL = {
    "code": "document_not_retryable",
    "message": "Document cannot be reingested in its current state.",
}
_DOCUMENT_ORIGINAL_INVALID_DETAIL = {
    "code": "document_original_invalid",
    "message": "The original document is missing or invalid.",
}
_OBJECT_STORAGE_UNAVAILABLE_DETAIL = {
    "code": "object_storage_unavailable",
    "message": "Object storage is temporarily unavailable.",
}


@router.post(
    "", response_model=DocumentUploadAccepted, status_code=status.HTTP_202_ACCEPTED
)
async def upload_document(
    request: Request,
    response: Response,
    file: UploadFile = File(...),
    folder_id: UUID | None = Form(default=None),
    team_ids: list[UUID] | None = Form(default=None),
) -> DocumentUploadAccepted:
    async with authenticated_request(request, mutation=True) as auth:
        actor = auth.actor
    submitted_team_ids = team_ids or []
    if len(submitted_team_ids) != len(set(submitted_team_ids)):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "team_ids must be distinct",
        )
    selected_team_ids = tuple(sorted(submitted_team_ids, key=str))
    staged = None
    try:
        staged = await request.app.state.container.documents.stage(file)
        async with authenticated_request(request, mutation=True) as auth:
            if auth.actor != actor:
                raise HTTPException(
                    status.HTTP_401_UNAUTHORIZED, "authentication required"
                )
            preflight = await request.app.state.container.documents.preflight(
                auth.actor, auth.session, staged, folder_id, selected_team_ids
            )
        if preflight.upload_required:
            await request.app.state.container.documents.put(staged)
            if preflight.reservation_id is None:
                raise RuntimeError("upload reservation is missing")
            async with authenticated_request(request, mutation=True) as auth:
                if auth.actor != actor:
                    raise HTTPException(
                        status.HTTP_401_UNAUTHORIZED, "authentication required"
                    )
                result = await request.app.state.container.documents.commit(
                    auth.actor,
                    auth.session,
                    staged,
                    folder_id,
                    preflight.reservation_id,
                    selected_team_ids,
                    preflight=preflight,
                )
        else:
            result = request.app.state.container.documents.duplicate_result(preflight)
    except UploadTooLargeError as exc:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, str(exc)) from exc
    except UploadValidationError as exc:
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, str(exc)) from exc
    except DeletionPendingError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except UploadReservationActive as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except DuplicateUploadRequiresAccess as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail={
                "code": "upload_unavailable",
                "message": (
                    "This content is already stored. Ask an administrator for access."
                ),
            },
        ) from exc
    except DocumentUploadParentNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except InvalidLibraryName as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    except (ObjectStoreError, ObjectIntegrityError) as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            f"object storage unavailable: {exc}",
        ) from exc
    except DBAPIError as exc:
        sqlstate = getattr(exc.orig, "sqlstate", None)
        if sqlstate == "22023":
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                "invalid upload request",
            ) from exc
        if sqlstate == "42501":
            raise HTTPException(status.HTTP_404_NOT_FOUND, "folder not found") from exc
        raise
    finally:
        if staged is not None:
            await request.app.state.container.documents.cleanup(staged)
    if result.status == "duplicate":
        response.status_code = status.HTTP_200_OK
    return DocumentUploadAccepted(
        document_id=result.document_id,
        job_id=result.job_id,
        status=result.status,
        duplicate_of=result.duplicate_of,
        node_id=result.node_id,
        parent_id=result.parent_id,
        display_name=result.display_name,
        logical_path=result.logical_path,
        location_reused=result.location_reused,
    )


@router.get("", response_model=list[DocumentSummary])
async def list_documents(
    request: Request,
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=100, ge=1, le=100),
) -> list[DocumentSummary]:
    async with authenticated_request(request) as auth:
        await acquire_library_lock(auth.session)
        documents = await DocumentRepository(auth.session).list(
            auth.actor, page=page, limit=limit
        )
        locations = await locations_for_documents_in_session(
            auth.session, auth.actor, [document.id for document in documents]
        )
        team_rows = (
            await auth.session.execute(
                text(
                    "SELECT document_id, team_ids "
                    "FROM v4_document_team_recipients(:document_ids)"
                ),
                {"document_ids": [document.id for document in documents]},
            )
        ).all()
        teams_by_document = {row.document_id: list(row.team_ids) for row in team_rows}
        actor = auth.actor
    return [
        DocumentSummary(
            document_id=document.id,
            filename=document.original_filename,
            sha256=document.sha256,
            state=document.state,
            page_count=document.page_count,
            chunk_count=document.chunk_count,
            created_at=document.created_at,
            updated_at=document.updated_at,
            error=document.error,
            node_id=locations[document.id].node_id,
            parent_id=locations[document.id].parent_id,
            display_name=locations[document.id].display_name,
            logical_path=locations[document.id].logical_path,
            uploader_user_id=locations[document.id].uploader_user_id,
            can_manage=(
                actor.role.value == "admin"
                or locations[document.id].uploader_user_id == actor.user_id
            ),
            team_ids=teams_by_document.get(document.id, []),
        )
        for document in documents
    ]


@router.post(
    "/{document_id}/reingest",
    response_model=DocumentReingestAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
async def reingest_document(
    request: Request,
    document_id: UUID,
) -> DocumentReingestAccepted:
    try:
        async with authenticated_request(request, mutation=True) as auth:
            actor = auth.actor
            preparation = await request.app.state.container.document_reingest.prepare(
                auth.session, document_id
            )
        await request.app.state.container.document_reingest.verify_original(preparation)
        job_id = uuid4()
        async with authenticated_request(request, mutation=True) as auth:
            if auth.actor != actor:
                raise HTTPException(
                    status.HTTP_401_UNAUTHORIZED, "authentication required"
                )
            result = await request.app.state.container.document_reingest.commit(
                auth.session, preparation, job_id
            )
    except DocumentReingestNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "document not found") from exc
    except DocumentNotRetryable as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=_DOCUMENT_NOT_RETRYABLE_DETAIL,
        ) from exc
    except ObjectIntegrityError as exc:
        raise HTTPException(
            status.HTTP_410_GONE,
            detail=_DOCUMENT_ORIGINAL_INVALID_DETAIL,
        ) from exc
    except ObjectStoreError as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=_OBJECT_STORAGE_UNAVAILABLE_DETAIL,
        ) from exc
    return DocumentReingestAccepted(
        document_id=result.document_id,
        job_id=result.job_id,
        status="queued",
    )


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(request: Request, document_id: UUID) -> None:
    try:
        async with authenticated_request(request, mutation=True) as auth:
            deleted = await request.app.state.container.documents.delete(
                auth.actor, auth.session, document_id
            )
    except DBAPIError as exc:
        sqlstate = getattr(exc.orig, "sqlstate", None)
        if sqlstate in {"02000", "P0002"}:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND, "document not found"
            ) from exc
        if sqlstate == "42501":
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                "capability denied",
            ) from exc
        raise
    except DocumentDeletionError as exc:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(exc)) from exc
    if not deleted:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "document not found")


@router.api_route(
    "/{document_id}/content",
    methods=["GET", "HEAD"],
    response_class=Response,
)
async def document_content(request: Request, document_id: UUID) -> Response:
    include_body = request.method == "GET"
    try:
        async with authenticated_request(request) as auth:
            actor = auth.actor
            authorized = await request.app.state.container.document_content.authorize(
                auth.actor, auth.session, document_id
            )
        descriptor = await request.app.state.container.document_content.resolve(
            authorized,
            range_header=request.headers.get("range"),
            include_body=include_body,
        )
    except DocumentContentNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except DocumentContentGone as exc:
        raise HTTPException(status.HTTP_410_GONE, str(exc)) from exc
    except DocumentContentUnavailable as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    except InvalidDocumentRange as exc:
        return Response(
            status_code=status.HTTP_416_RANGE_NOT_SATISFIABLE,
            headers={
                "Content-Range": f"bytes */{exc.size}",
                "Accept-Ranges": "bytes",
                "Cache-Control": "private, no-store",
            },
        )
    if descriptor.body is None:
        async with authenticated_request(request) as auth:
            remains_authorized = (
                await request.app.state.container.document_content.remains_authorized(
                    auth.actor, auth.session, authorized
                )
            )
            if auth.actor != actor or not remains_authorized:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "document not found")
        return Response(status_code=descriptor.status_code, headers=descriptor.headers)
    body = descriptor.body
    try:
        async with authenticated_request(request) as auth:
            remains_authorized = (
                await request.app.state.container.document_content.remains_authorized(
                    auth.actor, auth.session, authorized
                )
            )
            if auth.actor != actor or not remains_authorized:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "document not found")
    except BaseException:
        await body.close()
        raise

    async def stream() -> AsyncIterator[bytes]:
        finished = asyncio.Event()
        revoked = asyncio.Event()

        async def monitor() -> None:
            while not finished.is_set():
                try:
                    await asyncio.wait_for(finished.wait(), timeout=0.5)
                    return
                except TimeoutError:
                    pass
                try:
                    content_service = request.app.state.container.document_content
                    async with authenticated_request(request) as current:
                        allowed = (
                            current.actor == actor
                            and await content_service.remains_authorized(
                                current.actor, current.session, authorized
                            )
                        )
                except asyncio.CancelledError:
                    raise
                except BaseException:
                    allowed = False
                if not allowed:
                    revoked.set()
                    await body.close()
                    return

        monitor_task = asyncio.create_task(
            monitor(), name=f"document-content-auth-{document_id}"
        )
        try:
            while True:
                read_task = asyncio.create_task(body.read(1024 * 1024))
                revoked_task = asyncio.create_task(revoked.wait())
                done, _ = await asyncio.wait(
                    {read_task, revoked_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if revoked_task in done and revoked.is_set():
                    read_task.cancel()
                    await asyncio.gather(read_task, return_exceptions=True)
                    return
                revoked_task.cancel()
                await asyncio.gather(revoked_task, return_exceptions=True)
                chunk = await read_task
                if not chunk:
                    return
                yield chunk
        finally:
            finished.set()
            monitor_task.cancel()
            await asyncio.gather(monitor_task, return_exceptions=True)
            await body.close()

    return StreamingResponse(
        stream(),
        status_code=descriptor.status_code,
        headers=descriptor.headers,
        background=BackgroundTask(body.close),
    )
