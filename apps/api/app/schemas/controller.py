from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ControllerModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ControllerNonce(ControllerModel):
    nonce: str = Field(pattern=r"^[A-Za-z0-9_-]{43,128}$")


class ControllerStageRequest(ControllerNonce):
    stage: Literal[
        "backing_up", "draining", "applying", "restarting", "validating", "rolling_back"
    ]


class ControllerFinishRequest(ControllerNonce):
    result: Literal["effective", "failed", "rolled_back"]
    reason_code: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{1,63}$")


class ControllerConfigurationResponse(ControllerModel):
    change_id: UUID
    prior_configuration: dict[str, str]
    desired_configuration: dict[str, str]


class ControllerSmokeResponse(ControllerModel):
    succeeded: bool
    reason_code: str
    fixture_id: str


class ControllerBackupStageRequest(ControllerNonce):
    stage: Literal["draining", "exporting", "verifying"]


class ControllerBackupExportRequest(ControllerNonce):
    database_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    database_bytes: int = Field(ge=0)
    storage_bytes: int = Field(ge=0)


class ControllerBackupFailureRequest(ControllerNonce):
    reason_code: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{1,63}$")


class ControllerRestoreVerificationRequest(ControllerNonce):
    manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    verification_profile: Literal["personal.isolated-restore.v1"]
