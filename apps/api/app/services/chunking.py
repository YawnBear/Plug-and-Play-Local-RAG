import re
import uuid
from dataclasses import dataclass

from app.domain import ParseMethod
from app.services.highlight_anchors import (
    HighlightAnchor,
    normalize_highlight_text,
    ocr_regions_anchor,
    text_quote_anchor,
)
from app.services.identity import chunk_uuid, sha256_text
from app.services.parsing.types import ParsedPage
from app.versions import (
    CHUNK_SCHEMA_VERSION,
    CHUNKING_VERSION,
    EMBEDDING_VERSION,
    PARSER_VERSION,
)

TOKEN_PATTERN = re.compile(r"\w+|[^\w\s]", re.UNICODE)
SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?\u3002\uff01\uff1f])\s+")


def count_tokens(text: str) -> int:
    """Count tokens with a stable, dependency-free Unicode lexical strategy."""
    return sum(1 for _ in TOKEN_PATTERN.finditer(text))


@dataclass(frozen=True, slots=True)
class ChunkDraft:
    id: uuid.UUID
    document_id: uuid.UUID
    ordinal: int
    filename: str
    page_start: int
    page_end: int
    section: str | None
    text: str
    token_count: int
    text_sha256: str
    source_sha256: str
    parse_method: ParseMethod
    parser_version: str
    chunking_version: str
    embedding_version: str
    schema_version: str
    citation_label: str
    highlight_anchor: HighlightAnchor


@dataclass(frozen=True, slots=True)
class _Unit:
    text: str
    section: str | None
    regions: tuple[tuple[float, float, float, float], ...] = ()
    start: int | None = None
    end: int | None = None


class DocumentChunker:
    def __init__(
        self,
        *,
        target_tokens: int = 750,
        max_tokens: int = 900,
        overlap_tokens: int = 125,
        parser_version: str = PARSER_VERSION,
        chunking_version: str = CHUNKING_VERSION,
        embedding_version: str = EMBEDDING_VERSION,
        schema_version: str = CHUNK_SCHEMA_VERSION,
    ) -> None:
        if not 0 <= overlap_tokens < target_tokens <= max_tokens:
            raise ValueError("require 0 <= overlap < target <= max")
        self.target_tokens = target_tokens
        self.max_tokens = max_tokens
        self.overlap_tokens = overlap_tokens
        self.parser_version = parser_version
        self.chunking_version = chunking_version
        self.embedding_version = embedding_version
        self.schema_version = schema_version

    def chunk(
        self,
        pages: list[ParsedPage],
        *,
        document_id: uuid.UUID,
        filename: str,
        source_sha256: str,
    ) -> list[ChunkDraft]:
        drafts: list[ChunkDraft] = []
        ordinal = 0
        for page in pages:
            for source_page in self._fragment_pages(page):
                units = self._units(source_page)
                for text, section, selected_units in self._pack(units):
                    checksum = sha256_text(text)
                    anchor = (
                        ocr_regions_anchor(
                            page=source_page.page_number,
                            regions=[
                                region
                                for unit in selected_units
                                for region in unit.regions
                            ],
                        )
                        if source_page.parse_method is ParseMethod.OCR
                        else text_quote_anchor(
                            page=source_page.page_number,
                            page_text=source_page.text,
                            chunk_text=text,
                            start_hint=selected_units[0].start,
                        )
                    )
                    drafts.append(
                        ChunkDraft(
                            id=chunk_uuid(
                                document_id,
                                ordinal=ordinal,
                                page_number=source_page.page_number,
                                text_sha256=checksum,
                                schema_version=self.schema_version,
                            ),
                            document_id=document_id,
                            ordinal=ordinal,
                            filename=filename,
                            page_start=source_page.page_number,
                            page_end=source_page.page_number,
                            section=section,
                            text=text,
                            token_count=count_tokens(text),
                            text_sha256=checksum,
                            source_sha256=source_sha256,
                            parse_method=source_page.parse_method,
                            parser_version=self.parser_version,
                            chunking_version=self.chunking_version,
                            embedding_version=self.embedding_version,
                            schema_version=self.schema_version,
                            citation_label=(
                                f"p{source_page.page_number}:c{ordinal}"
                            ),
                            highlight_anchor=anchor,
                        )
                    )
                    ordinal += 1
        return drafts

    @staticmethod
    def _fragment_pages(page: ParsedPage) -> list[ParsedPage]:
        if not page.fragments:
            return [page]
        return [
            ParsedPage(
                page.page_number,
                fragment.text,
                fragment.parse_method,
                fragment.blocks,
                page.routing_mode,
                (),
                page.assessment,
            )
            for fragment in page.fragments
            if fragment.text.strip()
        ]

    def _units(self, page: ParsedPage) -> list[_Unit]:
        units: list[_Unit] = []
        section: str | None = None
        normalized_page = normalize_highlight_text(page.text)
        cursor = 0
        paragraphs = (
            [(block.text, (block.region,)) for block in page.blocks]
            if page.blocks
            else [(paragraph, ()) for paragraph in re.split(
                r"\n\s*\n", page.text.strip()
            )]
        )
        for paragraph, regions in paragraphs:
            paragraph = paragraph.strip()
            if not paragraph:
                continue
            if paragraph.startswith("# "):
                section = paragraph[2:].strip() or section
            sentences = SENTENCE_BOUNDARY.split(paragraph)
            for sentence in sentences:
                sentence = sentence.strip()
                if not sentence:
                    continue
                for part in self._split_oversized(sentence):
                    if page.blocks:
                        units.append(_Unit(part, section, regions))
                        continue
                    normalized = normalize_highlight_text(part)
                    start = normalized_page.find(normalized, cursor)
                    if start < 0:
                        raise ValueError("chunk unit does not map to source page")
                    end = start + len(normalized)
                    units.append(_Unit(part, section, regions, start, end))
                    cursor = end
        return units

    def _split_oversized(self, text: str) -> list[str]:
        matches = list(TOKEN_PATTERN.finditer(text))
        if len(matches) <= self.max_tokens:
            return [text]
        parts: list[str] = []
        for start in range(0, len(matches), self.max_tokens):
            group = matches[start : start + self.max_tokens]
            parts.append(text[group[0].start() : group[-1].end()].strip())
        return parts

    def _pack(
        self, units: list[_Unit]
    ) -> list[tuple[str, str | None, tuple[_Unit, ...]]]:
        chunks: list[tuple[str, str | None, tuple[_Unit, ...]]] = []
        current: list[_Unit] = []
        current_tokens = 0
        for unit in units:
            unit_tokens = count_tokens(unit.text)
            if current and current_tokens + unit_tokens > self.target_tokens:
                text, section = self._render(current)
                chunks.append((text, section, tuple(current)))
                current = self._overlap(current)
                current_tokens = count_tokens(
                    "\n\n".join(item.text for item in current)
                )
                if current and current_tokens + unit_tokens > self.max_tokens:
                    current = []
                    current_tokens = 0
            current.append(unit)
            current_tokens += unit_tokens
        if current:
            rendered = self._render(current)
            if not chunks or rendered[0] != chunks[-1][0]:
                chunks.append((*rendered, tuple(current)))
        return chunks

    def _overlap(self, units: list[_Unit]) -> list[_Unit]:
        if self.overlap_tokens == 0:
            return []
        selected: list[_Unit] = []
        total = 0
        for unit in reversed(units):
            tokens = count_tokens(unit.text)
            if selected and total + tokens > self.overlap_tokens:
                break
            if tokens > self.overlap_tokens:
                matches = list(TOKEN_PATTERN.finditer(unit.text))
                suffix = matches[-self.overlap_tokens :]
                text = unit.text[suffix[0].start() :].strip()
                normalized = normalize_highlight_text(text)
                return [
                    _Unit(
                        text,
                        unit.section,
                        unit.regions,
                        None if unit.end is None else unit.end - len(normalized),
                        unit.end,
                    )
                ]
            selected.append(unit)
            total += tokens
        return list(reversed(selected))

    @staticmethod
    def _render(units: list[_Unit]) -> tuple[str, str | None]:
        text = "\n\n".join(unit.text for unit in units).strip()
        section = next((unit.section for unit in reversed(units) if unit.section), None)
        return text, section
