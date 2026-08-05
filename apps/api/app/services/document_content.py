import re
import uuid
from dataclasses import dataclass
from urllib.parse import quote

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.repositories import DocumentRepository
from app.security.actor import ActorContext
from app.services.object_lifecycle import (
    ObjectIntegrityError,
    canonical_object_key,
    validate_remote_metadata,
)
from app.services.object_storage import AsyncObjectBody, ObjectStore, ObjectStoreError

_RANGE_PATTERN = re.compile(r"bytes=(\d*)-(\d*)")


class DocumentContentNotFound(RuntimeError):
    pass


class DocumentContentGone(RuntimeError):
    pass


class DocumentContentUnavailable(RuntimeError):
    pass


class InvalidDocumentRange(ValueError):
    def __init__(self, size: int) -> None:
        super().__init__("requested byte range is not satisfiable")
        self.size = size


@dataclass(frozen=True, slots=True)
class ResolvedRange:
    start: int
    end: int
    size: int

    @property
    def length(self) -> int:
        return self.end - self.start + 1

    @property
    def request_header(self) -> str:
        return f"bytes={self.start}-{self.end}"

    @property
    def content_range(self) -> str:
        return f"bytes {self.start}-{self.end}/{self.size}"


def parse_single_range(value: str, size: int) -> ResolvedRange:
    if size <= 0 or "," in value:
        raise InvalidDocumentRange(size)
    match = _RANGE_PATTERN.fullmatch(value)
    if match is None:
        raise InvalidDocumentRange(size)
    start_text, end_text = match.groups()
    if not start_text and not end_text:
        raise InvalidDocumentRange(size)
    try:
        start_number = int(start_text) if start_text else None
        end_number = int(end_text) if end_text else None
    except ValueError as exc:
        raise InvalidDocumentRange(size) from exc
    if start_number is None:
        assert end_number is not None
        suffix = end_number
        if suffix <= 0:
            raise InvalidDocumentRange(size)
        start = max(size - suffix, 0)
        return ResolvedRange(start, size - 1, size)
    start = start_number
    if start >= size:
        raise InvalidDocumentRange(size)
    if not end_text:
        return ResolvedRange(start, size - 1, size)
    assert end_number is not None
    end = end_number
    if end < start:
        raise InvalidDocumentRange(size)
    return ResolvedRange(start, min(end, size - 1), size)


@dataclass(frozen=True, slots=True)
class ContentDescriptor:
    status_code: int
    headers: dict[str, str]
    body: AsyncObjectBody | None


@dataclass(frozen=True, slots=True)
class AuthorizedDocumentContent:
    document_id: uuid.UUID
    sha256: str
    object_key: str
    byte_size: int
    original_filename: str


class DocumentContentService:
    def __init__(self, object_store: ObjectStore) -> None:
        self._object_store = object_store

    async def authorize(
        self,
        actor: ActorContext,
        session: AsyncSession,
        document_id: uuid.UUID,
    ) -> AuthorizedDocumentContent:
        document = await DocumentRepository(session).get(actor, document_id)
        if document is None:
            raise DocumentContentNotFound("document not found")
        try:
            expected_key = canonical_object_key(document.sha256)
        except (TypeError, ValueError) as exc:
            raise DocumentContentGone("document original metadata is invalid") from exc
        if document.object_key != expected_key or document.byte_size <= 0:
            raise DocumentContentGone("document original metadata is invalid")
        return AuthorizedDocumentContent(
            document_id=document.id,
            sha256=document.sha256,
            object_key=document.object_key,
            byte_size=document.byte_size,
            original_filename=document.original_filename,
        )

    async def remains_authorized(
        self,
        actor: ActorContext,
        session: AsyncSession,
        authorized: AuthorizedDocumentContent,
    ) -> bool:
        try:
            current = await self.authorize(actor, session, authorized.document_id)
        except (DocumentContentNotFound, DocumentContentGone):
            return False
        return current == authorized

    async def resolve(
        self,
        authorized: AuthorizedDocumentContent,
        *,
        range_header: str | None,
        include_body: bool,
    ) -> ContentDescriptor:
        try:
            metadata = await self._object_store.head(authorized.object_key)
            validate_remote_metadata(
                metadata,
                key=authorized.object_key,
                sha256=authorized.sha256,
                byte_size=authorized.byte_size,
            )
        except ObjectIntegrityError as exc:
            raise DocumentContentGone(str(exc)) from exc
        except ObjectStoreError as exc:
            if exc.not_found:
                raise DocumentContentGone("document original is missing") from exc
            raise DocumentContentUnavailable(str(exc)) from exc

        selected = (
            parse_single_range(range_header, metadata.size)
            if range_header is not None
            else None
        )
        status_code = 206 if selected is not None else 200
        content_length = selected.length if selected is not None else metadata.size
        headers = {
            "Content-Type": "application/pdf",
            "Accept-Ranges": "bytes",
            "Content-Length": str(content_length),
            "Content-Disposition": _content_disposition(authorized.original_filename),
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, no-store",
        }
        if selected is not None:
            headers["Content-Range"] = selected.content_range
        if not include_body:
            return ContentDescriptor(status_code, headers, None)
        try:
            result = await self._object_store.get(
                authorized.object_key,
                byte_range=(selected.request_header if selected else None),
            )
        except ObjectStoreError as exc:
            if exc.not_found:
                raise DocumentContentGone("document original is missing") from exc
            raise DocumentContentUnavailable(str(exc)) from exc
        expected_range = selected.content_range if selected is not None else None
        if result.size != content_length or result.content_range != expected_range:
            await result.body.close()
            raise DocumentContentGone("object response range or size is inconsistent")
        return ContentDescriptor(status_code, headers, result.body)


def _content_disposition(filename: str) -> str:
    safe_ascii = "".join(
        character
        if 32 <= ord(character) < 127 and character not in {'"', "\\"}
        else "_"
        for character in filename
    )
    encoded = quote(filename, safe="")
    return f"inline; filename=\"{safe_ascii}\"; filename*=UTF-8''{encoded}"
