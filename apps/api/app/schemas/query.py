import unicodedata
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


class QueryRequest(BaseModel):
    question: str = Field(min_length=1)
    document_ids: list[UUID] | None = Field(default=None, min_length=1)
    retrieve_k: int = Field(default=20, ge=1, le=20)
    context_k: int = Field(default=6, ge=5, le=8)

    @field_validator("question")
    @classmethod
    def strip_nonempty_question(cls, value: str) -> str:
        question = unicodedata.normalize("NFC", value.strip())
        if not question:
            raise ValueError("question must not be empty")
        if len(question) > 2000:
            raise ValueError("question must contain at most 2000 characters")
        if any(
            unicodedata.category(char) in {"Cf", "Cs"}
            or (
                unicodedata.category(char) == "Cc"
                and char not in {"\n", "\r", "\t"}
            )
            for char in question
        ):
            raise ValueError("question contains a forbidden Unicode character")
        return question

    @field_validator("document_ids")
    @classmethod
    def require_unique_document_ids(cls, value: list[UUID] | None) -> list[UUID] | None:
        if value is not None and len(value) != len(set(value)):
            raise ValueError("document_ids must be unique")
        return value


class Citation(BaseModel):
    label: str
    chunk_id: UUID
    filename: str
    document_id: UUID
    display_name: str
    logical_path: str
    page_start: int = Field(ge=1)
    page_end: int = Field(ge=1)
