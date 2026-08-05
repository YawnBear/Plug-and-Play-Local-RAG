from uuid import UUID

from pydantic import BaseModel, Field


class LibrarySearchItem(BaseModel):
    document_id: UUID
    node_id: UUID
    filename: str
    display_name: str
    logical_path: str
    page_start: int | None = Field(default=None, ge=1)
    page_end: int | None = Field(default=None, ge=1)
    match_kinds: list[str]
    rank: float


class LibrarySearchResponse(BaseModel):
    query: str
    page: int
    limit: int
    total: int
    items: list[LibrarySearchItem]
    correlation_id: UUID
    stage_timings_ms: dict[str, float]
