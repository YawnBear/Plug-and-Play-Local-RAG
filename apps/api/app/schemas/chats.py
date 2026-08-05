import hashlib
from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator


class ChatCreateRequest(BaseModel):
    title: str | None = None


class ChatRenameRequest(BaseModel):
    title: str


class ChatScopeRequest(BaseModel):
    mode: str
    node_ids: list[UUID] = Field(default_factory=list, max_length=256)


class ChatMessageRequest(BaseModel):
    question: str


class ChatSummaryResponse(BaseModel):
    chat_id: UUID
    title: str
    title_is_manual: bool
    scope_mode: str
    scope_version: int
    created_at: datetime
    updated_at: datetime


class HistoricalSourceResponse(BaseModel):
    label: str
    rank: int
    document_id: UUID | None
    chunk_id: UUID | None
    document_id_snapshot: UUID
    chunk_id_snapshot: UUID
    filename: str
    display_name: str
    logical_path: str
    page_start: int
    page_end: int
    section: str | None
    source_sha256: str
    text_sha256: str
    retrieval_distance: float
    rerank_score: float
    source_available: bool


class ChatTurnResponse(BaseModel):
    turn_id: UUID
    ordinal: int
    question: str
    status: str
    attempt: int
    scope_version: int
    final_answer: str | None
    partial_answer: str | None
    insufficient_context: bool
    error: str | None
    sources: list[HistoricalSourceResponse]
    citations: list[HistoricalSourceResponse]
    citation_ranks: list[int]
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None


class ChatDetailResponse(ChatSummaryResponse):
    scope_node_ids: list[UUID]
    turns: list[ChatTurnResponse]
    page: int = Field(ge=1)
    limit: int = Field(ge=1, le=100)
    total: int = Field(ge=0)


class ChatScopeResponse(ChatSummaryResponse):
    scope_node_ids: list[UUID]


class TextQuoteSelectorResponse(BaseModel):
    exact: str = Field(min_length=1, max_length=16_000)
    prefix: str = Field(max_length=256)
    suffix: str = Field(max_length=256)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_digest(self) -> "TextQuoteSelectorResponse":
        digest = hashlib.sha256(self.exact.encode("utf-8")).hexdigest()
        if digest != self.sha256:
            raise ValueError("text quote digest does not match exact text")
        return self


class TextQuotePageResponse(BaseModel):
    page: int = Field(ge=1)
    kind: Literal["text_quote"]
    selector: TextQuoteSelectorResponse


class OcrRegionResponse(BaseModel):
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    width: float = Field(gt=0, le=1)
    height: float = Field(gt=0, le=1)

    @model_validator(mode="after")
    def validate_bounds(self) -> "OcrRegionResponse":
        if self.x + self.width > 1 or self.y + self.height > 1:
            raise ValueError("OCR highlight region exceeds page bounds")
        return self


class OcrRegionsPageResponse(BaseModel):
    page: int = Field(ge=1)
    kind: Literal["ocr_regions"]
    regions: list[OcrRegionResponse] = Field(min_length=1, max_length=256)


HighlightPageResponse = Annotated[
    TextQuotePageResponse | OcrRegionsPageResponse,
    Field(discriminator="kind"),
]


class HighlightAnchorResponse(BaseModel):
    version: Literal[1]
    normalization: Literal["citation-highlight-v1"]
    pages: list[HighlightPageResponse] = Field(min_length=1, max_length=32)

    @model_validator(mode="after")
    def validate_pages(self) -> "HighlightAnchorResponse":
        page_numbers = [page.page for page in self.pages]
        if len(page_numbers) != len(set(page_numbers)):
            raise ValueError("highlight pages must be unique")
        return self


class CitationEvidenceResponse(BaseModel):
    label: str = Field(pattern=r"^S[1-8]$")
    rank: int = Field(ge=1, le=8)
    document_id: UUID
    display_name: str = Field(min_length=1)
    logical_path: str = Field(min_length=1)
    page_start: int = Field(ge=1)
    page_end: int = Field(ge=1)
    section: str | None
    parse_method: Literal["direct", "ocr"]
    snapshot_text: str = Field(min_length=1, max_length=100_000)
    highlight_anchor: HighlightAnchorResponse
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    text_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_evidence(self) -> "CitationEvidenceResponse":
        if self.label != f"S{self.rank}" or self.page_end < self.page_start:
            raise ValueError("citation evidence identity or page range is invalid")
        if any(
            page.page < self.page_start or page.page > self.page_end
            for page in self.highlight_anchor.pages
        ):
            raise ValueError("highlight page is outside the citation page range")
        expected_kind = "text_quote" if self.parse_method == "direct" else "ocr_regions"
        if any(page.kind != expected_kind for page in self.highlight_anchor.pages):
            raise ValueError("highlight selector does not match parse method")
        return self
