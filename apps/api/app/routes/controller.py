from __future__ import annotations

import hashlib
import hmac
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.schemas.controller import (
    ControllerBackupExportRequest,
    ControllerBackupFailureRequest,
    ControllerBackupStageRequest,
    ControllerConfigurationResponse,
    ControllerFinishRequest,
    ControllerNonce,
    ControllerRestoreVerificationRequest,
    ControllerSmokeResponse,
    ControllerStageRequest,
)

router = APIRouter(prefix="/internal/controller", include_in_schema=False)


async def require_controller(
    request: Request, authorization: str | None = Header(default=None)
) -> None:
    expected = request.app.state.settings.controller_service_token.get_secret_value()
    supplied = authorization.removeprefix("Bearer ") if authorization else ""
    if (
        not expected
        or request.client is None
        or request.client.host not in {"127.0.0.1", "::1"}
        or not hmac.compare_digest(supplied, expected)
    ):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not found")


def _nonce_hash(nonce: str) -> str:
    return hashlib.sha256(nonce.encode("ascii")).hexdigest()


@router.post(
    "/backups/{backup_run_id}/claim",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_controller)],
)
async def claim_backup(
    request: Request, backup_run_id: UUID, body: ControllerNonce
) -> None:
    await _backup_command(
        request,
        "SELECT v9_controller_claim_personal_backup(:backup_run_id, :nonce_hash)",
        backup_run_id,
        body.nonce,
    )


@router.post(
    "/backups/{backup_run_id}/stage",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_controller)],
)
async def advance_backup(
    request: Request, backup_run_id: UUID, body: ControllerBackupStageRequest
) -> None:
    await _backup_command(
        request,
        "SELECT v9_controller_advance_personal_backup("
        ":backup_run_id, :nonce_hash, :stage)",
        backup_run_id,
        body.nonce,
        {"stage": body.stage},
    )


@router.post(
    "/backups/{backup_run_id}/exported",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_controller)],
)
async def finish_backup_export(
    request: Request, backup_run_id: UUID, body: ControllerBackupExportRequest
) -> None:
    await _backup_command(
        request,
        "SELECT v9_controller_finish_personal_backup_export("
        ":backup_run_id, :nonce_hash, :database_sha256, :manifest_sha256, "
        ":database_bytes, :storage_bytes)",
        backup_run_id,
        body.nonce,
        body.model_dump(exclude={"nonce"}),
    )


@router.post(
    "/backups/{backup_run_id}/failed",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_controller)],
)
async def fail_backup(
    request: Request, backup_run_id: UUID, body: ControllerBackupFailureRequest
) -> None:
    await _backup_command(
        request,
        "SELECT v9_controller_fail_personal_backup("
        ":backup_run_id, :nonce_hash, :reason_code)",
        backup_run_id,
        body.nonce,
        {"reason_code": body.reason_code},
    )


@router.post(
    "/backups/{backup_run_id}/verified",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_controller)],
)
async def verify_backup(
    request: Request, backup_run_id: UUID, body: ControllerRestoreVerificationRequest
) -> None:
    await _backup_command(
        request,
        "SELECT v9_controller_record_restore_verification("
        ":backup_run_id, :nonce_hash, :manifest_sha256, :verification_profile)",
        backup_run_id,
        body.nonce,
        body.model_dump(exclude={"nonce"}),
    )


async def _backup_command(
    request: Request,
    statement: str,
    backup_run_id: UUID,
    nonce: str,
    values: dict[str, object] | None = None,
) -> None:
    parameters = {
        "backup_run_id": backup_run_id,
        "nonce_hash": _nonce_hash(nonce),
        **(values or {}),
    }
    try:
        async with request.app.state.container.database.engine.begin() as connection:
            await connection.execute(text(statement), parameters)
    except SQLAlchemyError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, "backup is unavailable") from exc


@router.post(
    "/configuration/{change_id}/claim",
    response_model=ControllerConfigurationResponse,
    dependencies=[Depends(require_controller)],
)
async def claim_configuration(
    request: Request, change_id: UUID, body: ControllerNonce
) -> ControllerConfigurationResponse:
    try:
        async with request.app.state.container.database.engine.begin() as connection:
            row = (
                await connection.execute(
                    text(
                        "SELECT * FROM v9_controller_runtime_configuration_change("
                        ":change_id, :nonce_hash)"
                    ),
                    {"change_id": change_id, "nonce_hash": _nonce_hash(body.nonce)},
                )
            ).one()
    except SQLAlchemyError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, "change is unavailable") from exc
    return ControllerConfigurationResponse(
        change_id=change_id,
        prior_configuration=dict(row.prior_configuration),
        desired_configuration=dict(row.desired_configuration),
    )


@router.post(
    "/configuration/{change_id}/stage",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_controller)],
)
async def advance_configuration(
    request: Request, change_id: UUID, body: ControllerStageRequest
) -> None:
    try:
        async with request.app.state.container.database.engine.begin() as connection:
            await connection.execute(
                text(
                    "SELECT v9_controller_advance_runtime_configuration("
                    ":change_id, :nonce_hash, :stage)"
                ),
                {
                    "change_id": change_id,
                    "nonce_hash": _nonce_hash(body.nonce),
                    "stage": body.stage,
                },
            )
    except SQLAlchemyError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, "change is unavailable") from exc


@router.post(
    "/configuration/{change_id}/finish",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_controller)],
)
async def finish_configuration(
    request: Request, change_id: UUID, body: ControllerFinishRequest
) -> None:
    try:
        async with request.app.state.container.database.engine.begin() as connection:
            await connection.execute(
                text(
                    "SELECT v9_controller_finish_runtime_configuration("
                    ":change_id, :nonce_hash, :result, :reason_code)"
                ),
                {
                    "change_id": change_id,
                    "nonce_hash": _nonce_hash(body.nonce),
                    "result": body.result,
                    "reason_code": body.reason_code,
                },
            )
    except SQLAlchemyError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, "change is unavailable") from exc


@router.post(
    "/profiles/{profile_id}/smoke",
    response_model=ControllerSmokeResponse,
    dependencies=[Depends(require_controller)],
)
async def smoke_profile(request: Request, profile_id: str) -> ControllerSmokeResponse:
    try:
        succeeded, reason_code, fixture_id = await (
            request.app.state.container.system.controller_smoke_profile(profile_id)
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "profile smoke test failed"
        ) from exc
    return ControllerSmokeResponse(
        succeeded=succeeded,
        reason_code=reason_code,
        fixture_id=fixture_id,
    )


@router.post(
    "/maintenance/drain",
    dependencies=[Depends(require_controller)],
)
async def drain_mutations(request: Request) -> dict[str, object]:
    try:
        await request.app.state.maintenance_gate.drain()
    except TimeoutError as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "API mutation drain timed out"
        ) from exc
    return request.app.state.maintenance_gate.status()


@router.post(
    "/maintenance/resume",
    dependencies=[Depends(require_controller)],
)
async def resume_mutations(request: Request) -> dict[str, object]:
    await request.app.state.maintenance_gate.resume()
    return request.app.state.maintenance_gate.status()
