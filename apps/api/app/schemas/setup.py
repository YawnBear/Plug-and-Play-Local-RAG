from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class SetupStatusResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    state: Literal["setup_required", "setup_complete"]
    code_issued: bool
    code_expires_at: datetime | None = None
    attempts_remaining: int = Field(ge=0, le=5)


class SetupChallengeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=8, max_length=256)


class SetupChallengeResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    state: Literal["owner_details_required"] = "owner_details_required"
    expires_at: datetime


class SetupOwnerRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str = Field(min_length=3, max_length=32)
    display_name: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=14, max_length=128)


class SetupOwnerResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    state: Literal["setup_complete"] = "setup_complete"
    login_path: Literal["/login"] = "/login"
    first_document_path: Literal["/knowledge-base"] = "/knowledge-base"
