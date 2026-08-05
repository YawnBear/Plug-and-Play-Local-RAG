from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel


class DocumentSummary(BaseModel):
    document_id: UUID
    filename: str
    sha256: str
    state: str
    page_count: int | None
    chunk_count: int
    created_at: datetime
    updated_at: datetime
    error: str | None
    node_id: UUID
    parent_id: UUID | None
    display_name: str
    logical_path: str
    uploader_user_id: UUID
    can_manage: bool
    team_ids: list[UUID]


class DocumentUploadAccepted(BaseModel):
    document_id: UUID
    job_id: UUID
    status: str = "queued"
    duplicate_of: UUID | None = None
    node_id: UUID
    parent_id: UUID | None
    display_name: str
    logical_path: str
    location_reused: bool


class DocumentReingestAccepted(BaseModel):
    document_id: UUID
    job_id: UUID
    status: Literal["queued"]
