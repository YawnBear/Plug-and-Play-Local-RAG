import io
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse

from app.schemas.system import (
    GenerationActionResponse,
    IngestionProfileSelection,
    IngestionProfileSelectionResponse,
    PersonalBackupHistoryResponse,
    PersonalBackupResponse,
    PersonalBackupStatusResponse,
    ReprocessingOperationResponse,
    ReprocessingOperationsResponse,
    ReprocessingPreviewRequest,
    ReprocessingPreviewResponse,
    ReprocessingStartRequest,
    RuntimeConfigurationApplyRequest,
    RuntimeConfigurationApplyResponse,
    RuntimeConfigurationChangesResponse,
    RuntimeConfigurationPreviewResponse,
    RuntimeConfigurationSelection,
    SystemCapabilitiesResponse,
    SystemConfigurationResponse,
    SystemDiagnosticsPreviewResponse,
    SystemOperationResponse,
    SystemOperationsResponse,
    SystemOverviewResponse,
    SystemReauthenticationRequest,
    SystemReauthenticationResponse,
    VersionInventoryResponse,
)
from app.security.request_auth import authenticated_request
from app.services.authentication import InvalidCredentials
from app.services.system import (
    SystemCapabilityDenied,
    SystemConflict,
    SystemUnavailable,
)

router = APIRouter(prefix="/api/admin/system", tags=["system"])


@router.get("/versions", response_model=VersionInventoryResponse)
async def version_inventory(request: Request) -> VersionInventoryResponse:
    try:
        async with authenticated_request(request) as auth:
            return await request.app.state.container.system.version_inventory(
                auth.actor, auth.session_token
            )
    except Exception as exc:
        _raise_system_error(exc)
        raise


@router.put("/versions/ingestion", response_model=IngestionProfileSelectionResponse)
async def select_ingestion_profile(
    request: Request, body: IngestionProfileSelection
) -> IngestionProfileSelectionResponse:
    try:
        async with authenticated_request(request, mutation=True) as auth:
            return await request.app.state.container.system.select_ingestion_profile(
                auth.actor, auth.session_token, body
            )
    except Exception as exc:
        _raise_system_error(exc)
        raise


@router.post("/reprocessing/preview", response_model=ReprocessingPreviewResponse)
async def preview_reprocessing(
    request: Request, body: ReprocessingPreviewRequest
) -> ReprocessingPreviewResponse:
    try:
        async with authenticated_request(request, mutation=True) as auth:
            return await request.app.state.container.system.preview_reprocessing(
                auth.actor, auth.session_token, body
            )
    except Exception as exc:
        _raise_system_error(exc)
        raise


@router.post(
    "/reprocessing/reauthenticate", response_model=SystemReauthenticationResponse
)
async def reauthenticate_reprocessing(
    request: Request, body: SystemReauthenticationRequest
) -> SystemReauthenticationResponse:
    try:
        async with authenticated_request(request, mutation=True) as auth:
            return await request.app.state.container.system.reauthenticate_reprocessing(
                auth.actor,
                auth.session_token,
                preview_id=body.preview_id,
                password=body.password,
                impact_digest=body.impact_digest,
            )
    except Exception as exc:
        _raise_system_error(exc)
        raise


@router.post(
    "/reprocessing",
    response_model=ReprocessingOperationResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def start_reprocessing(
    request: Request, body: ReprocessingStartRequest
) -> ReprocessingOperationResponse:
    try:
        async with authenticated_request(request, mutation=True) as auth:
            return await request.app.state.container.system.start_reprocessing(
                auth.actor,
                auth.session_token,
                preview_id=body.preview_id,
                impact_digest=body.impact_digest,
                grant_token=body.reauthentication_grant,
            )
    except Exception as exc:
        _raise_system_error(exc)
        raise


@router.get("/reprocessing", response_model=ReprocessingOperationsResponse)
async def reprocessing_operations(
    request: Request, limit: int = Query(default=50, ge=1, le=100)
) -> ReprocessingOperationsResponse:
    try:
        async with authenticated_request(request) as auth:
            return await request.app.state.container.system.reprocessing_operations(
                auth.actor, auth.session_token, limit=limit
            )
    except Exception as exc:
        _raise_system_error(exc)
        raise


@router.post(
    "/reprocessing/{operation_id}/{action}",
    response_model=ReprocessingOperationResponse,
)
async def control_reprocessing(
    request: Request, operation_id: UUID, action: str
) -> ReprocessingOperationResponse:
    try:
        async with authenticated_request(request, mutation=True) as auth:
            return await request.app.state.container.system.control_reprocessing(
                auth.actor,
                auth.session_token,
                operation_id=operation_id,
                action=action,
            )
    except Exception as exc:
        _raise_system_error(exc)
        raise


@router.post(
    "/generations/embedding/{generation_id}/rollback",
    response_model=GenerationActionResponse,
)
async def rollback_embedding_generation(
    request: Request, generation_id: UUID
) -> GenerationActionResponse:
    try:
        async with authenticated_request(request, mutation=True) as auth:
            return await (
                request.app.state.container.system.rollback_embedding_generation(
                    auth.actor, auth.session_token, generation_id
                )
            )
    except Exception as exc:
        _raise_system_error(exc)
        raise


@router.delete(
    "/generations/{generation_type}/{generation_id}",
    response_model=GenerationActionResponse,
)
async def cleanup_generation(
    request: Request, generation_type: str, generation_id: UUID
) -> GenerationActionResponse:
    try:
        async with authenticated_request(request, mutation=True) as auth:
            return await request.app.state.container.system.cleanup_generation(
                auth.actor,
                auth.session_token,
                generation_type=generation_type,
                generation_id=generation_id,
            )
    except Exception as exc:
        _raise_system_error(exc)
        raise


def _raise_system_error(exc: Exception) -> None:
    if isinstance(exc, SystemCapabilityDenied):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "capability denied") from exc
    if isinstance(exc, InvalidCredentials):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "password is incorrect") from exc
    if isinstance(exc, SystemConflict):
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    if isinstance(exc, ValueError):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    if isinstance(exc, SystemUnavailable):
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "System information is unavailable"
        ) from exc
    raise exc


@router.get("/overview", response_model=SystemOverviewResponse)
async def overview(request: Request) -> SystemOverviewResponse:
    try:
        async with authenticated_request(request) as auth:
            return await request.app.state.container.system.overview(
                auth.actor, auth.session_token
            )
    except Exception as exc:
        _raise_system_error(exc)
        raise


@router.get("/capabilities", response_model=SystemCapabilitiesResponse)
async def capabilities(request: Request) -> SystemCapabilitiesResponse:
    try:
        async with authenticated_request(request) as auth:
            return await request.app.state.container.system.capabilities(
                auth.actor, auth.session_token
            )
    except Exception as exc:
        _raise_system_error(exc)
        raise


@router.get("/configuration", response_model=SystemConfigurationResponse)
async def configuration(request: Request) -> SystemConfigurationResponse:
    try:
        async with authenticated_request(request) as auth:
            return await request.app.state.container.system.configuration(
                auth.actor, auth.session_token
            )
    except Exception as exc:
        _raise_system_error(exc)
        raise


@router.post(
    "/backups",
    response_model=PersonalBackupResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def start_personal_backup(request: Request) -> PersonalBackupResponse:
    try:
        async with authenticated_request(request, mutation=True) as auth:
            return await request.app.state.container.system.start_personal_backup(
                auth.actor, auth.session_token
            )
    except Exception as exc:
        _raise_system_error(exc)
        raise


@router.get("/backups/latest", response_model=PersonalBackupStatusResponse)
async def personal_backup_status(request: Request) -> PersonalBackupStatusResponse:
    try:
        async with authenticated_request(request) as auth:
            return await request.app.state.container.system.personal_backup_status(
                auth.actor, auth.session_token
            )
    except Exception as exc:
        _raise_system_error(exc)
        raise


@router.get("/backups", response_model=PersonalBackupHistoryResponse)
async def personal_backup_history(
    request: Request, limit: int = Query(default=25, ge=1, le=100)
) -> PersonalBackupHistoryResponse:
    try:
        async with authenticated_request(request) as auth:
            return await request.app.state.container.system.personal_backup_history(
                auth.actor, auth.session_token, limit=limit
            )
    except Exception as exc:
        _raise_system_error(exc)
        raise


@router.post(
    "/configuration/preview", response_model=RuntimeConfigurationPreviewResponse
)
async def preview_configuration(
    request: Request, selection: RuntimeConfigurationSelection
) -> RuntimeConfigurationPreviewResponse:
    try:
        async with authenticated_request(request, mutation=True) as auth:
            return await request.app.state.container.system.preview_configuration(
                auth.actor, auth.session_token, selection
            )
    except Exception as exc:
        _raise_system_error(exc)
        raise


@router.post(
    "/configuration/reauthenticate", response_model=SystemReauthenticationResponse
)
async def reauthenticate_configuration(
    request: Request, body: SystemReauthenticationRequest
) -> SystemReauthenticationResponse:
    try:
        async with authenticated_request(request, mutation=True) as auth:
            return await (
                request.app.state.container.system.reauthenticate_configuration(
                    auth.actor,
                    auth.session_token,
                    preview_id=body.preview_id,
                    password=body.password,
                    impact_digest=body.impact_digest,
                )
            )
    except Exception as exc:
        _raise_system_error(exc)
        raise


@router.post(
    "/configuration/apply",
    response_model=RuntimeConfigurationApplyResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def apply_configuration(
    request: Request, body: RuntimeConfigurationApplyRequest
) -> RuntimeConfigurationApplyResponse:
    try:
        async with authenticated_request(request, mutation=True) as auth:
            return await request.app.state.container.system.apply_configuration(
                auth.actor,
                auth.session_token,
                preview_id=body.preview_id,
                impact_digest=body.impact_digest,
                grant_token=body.reauthentication_grant,
            )
    except Exception as exc:
        _raise_system_error(exc)
        raise


@router.get(
    "/configuration/changes", response_model=RuntimeConfigurationChangesResponse
)
async def configuration_changes(
    request: Request, limit: int = Query(default=50, ge=1, le=100)
) -> RuntimeConfigurationChangesResponse:
    try:
        async with authenticated_request(request) as auth:
            return await request.app.state.container.system.configuration_changes(
                auth.actor, auth.session_token, limit=limit
            )
    except Exception as exc:
        _raise_system_error(exc)
        raise


@router.get("/operations", response_model=SystemOperationsResponse)
async def operations(
    request: Request, limit: int = Query(default=50, ge=1, le=100)
) -> SystemOperationsResponse:
    try:
        async with authenticated_request(request) as auth:
            return await request.app.state.container.system.operations(
                auth.actor, auth.session_token, limit=limit
            )
    except Exception as exc:
        _raise_system_error(exc)
        raise


@router.get("/operations/{operation_id}", response_model=SystemOperationResponse)
async def operation(request: Request, operation_id: UUID) -> SystemOperationResponse:
    try:
        async with authenticated_request(request) as auth:
            current = await request.app.state.container.system.operations(
                auth.actor, auth.session_token, limit=100
            )
        result = next(
            (item for item in current.operations if item.operation_id == operation_id),
            None,
        )
        if result is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "operation not found")
        return SystemOperationResponse(operation=result)
    except HTTPException:
        raise
    except Exception as exc:
        _raise_system_error(exc)
        raise


async def _run_profile(
    request: Request, profile_id: str, *, benchmark: bool
) -> SystemOperationResponse:
    try:
        async with authenticated_request(request, mutation=True) as auth:
            actor = auth.actor
            session_token = auth.session_token
        result = await request.app.state.container.system.run_profile_operation(
            actor,
            session_token,
            profile_id=profile_id,
            benchmark=benchmark,
        )
        return SystemOperationResponse(operation=result)
    except Exception as exc:
        _raise_system_error(exc)
        raise


@router.post("/profiles/{profile_id}/validate", response_model=SystemOperationResponse)
async def validate_profile(
    request: Request, profile_id: str
) -> SystemOperationResponse:
    return await _run_profile(request, profile_id, benchmark=False)


@router.post("/profiles/{profile_id}/benchmark", response_model=SystemOperationResponse)
async def benchmark_profile(
    request: Request, profile_id: str
) -> SystemOperationResponse:
    return await _run_profile(request, profile_id, benchmark=True)


@router.get("/diagnostics/preview", response_model=SystemDiagnosticsPreviewResponse)
async def diagnostics_preview(request: Request) -> SystemDiagnosticsPreviewResponse:
    try:
        async with authenticated_request(request) as auth:
            return request.app.state.container.system.diagnostics_preview(auth.actor)
    except Exception as exc:
        _raise_system_error(exc)
        raise


@router.post("/diagnostics/export")
async def diagnostics_export(request: Request) -> StreamingResponse:
    try:
        async with authenticated_request(request, mutation=True) as auth:
            actor = auth.actor
            session_token = auth.session_token
        archive = await request.app.state.container.system.diagnostics_archive(
            actor, session_token
        )
    except Exception as exc:
        _raise_system_error(exc)
        raise
    return StreamingResponse(
        io.BytesIO(archive),
        media_type="application/zip",
        headers={
            "Content-Disposition": 'attachment; filename="rag-system-diagnostics.zip"',
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )
