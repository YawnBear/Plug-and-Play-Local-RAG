import uuid

from app.domain import ParseMethod
from app.services.chunking import DocumentChunker, count_tokens
from app.services.identity import document_uuid, sha256_text
from app.services.parsing.types import ParsedBlock, ParsedPage


def _ocr_page(page: int, text: str) -> ParsedPage:
    return ParsedPage(
        page,
        text,
        ParseMethod.OCR,
        (ParsedBlock(1, 1, text, (0.1, 0.1, 0.8, 0.2)),),
    )


def test_identity_and_chunks_are_repeatable() -> None:
    checksum = sha256_text("same document bytes")
    document_id = document_uuid(checksum)
    pages = [
        ParsedPage(1, "# Policy\n\nRepeated text. Repeated text.", ParseMethod.DIRECT),
        _ocr_page(2, "Repeated text. Repeated text."),
    ]
    chunker = DocumentChunker(target_tokens=8, max_tokens=10, overlap_tokens=2)

    first = chunker.chunk(
        pages, document_id=document_id, filename="policy.pdf", source_sha256=checksum
    )
    second = chunker.chunk(
        pages, document_id=document_id, filename="policy.pdf", source_sha256=checksum
    )

    assert first == second
    assert len({chunk.id for chunk in first}) == len(first)
    assert all(chunk.page_start == chunk.page_end for chunk in first)
    assert first[0].section == "Policy"
    assert {chunk.parse_method for chunk in first} == {
        ParseMethod.DIRECT,
        ParseMethod.OCR,
    }


def test_long_paragraph_honors_hard_maximum_and_overlap() -> None:
    words = [f"word{number}" for number in range(1_050)]
    page = ParsedPage(1, " ".join(words), ParseMethod.DIRECT)
    chunker = DocumentChunker(target_tokens=750, max_tokens=900, overlap_tokens=125)

    chunks = chunker.chunk(
        [page],
        document_id=uuid.uuid4(),
        filename="long.pdf",
        source_sha256="a" * 64,
    )

    assert len(chunks) == 2
    assert max(chunk.token_count for chunk in chunks) <= 900
    first_tokens = chunks[0].text.split()
    second_tokens = chunks[1].text.split()
    assert first_tokens[-125:] == second_tokens[:125]


def test_table_like_lines_and_unicode_counting_are_preserved() -> None:
    unicode_sentence = "\u7ed3\u675f\u3002"
    text = f"Item | Count\nApple | 2\nPear | 3\n\n{unicode_sentence}"
    chunker = DocumentChunker(target_tokens=20, max_tokens=25, overlap_tokens=3)

    chunks = chunker.chunk(
        [_ocr_page(1, text)],
        document_id=uuid.uuid4(),
        filename="table.pdf",
        source_sha256="b" * 64,
    )

    assert "Apple | 2" in chunks[0].text
    assert count_tokens(unicode_sentence) == 2
    assert chunks[0].token_count == count_tokens(chunks[0].text)


def test_unicode_sentence_boundaries_are_deterministic() -> None:
    text = "\u7b2c\u4e00\u53e5\u3002 \u7b2c\u4e8c\u53e5\uff01 \u7b2c\u4e09\u53e5\uff1f"
    chunker = DocumentChunker(target_tokens=4, max_tokens=5, overlap_tokens=1)

    chunks = chunker.chunk(
        [ParsedPage(1, text, ParseMethod.DIRECT)],
        document_id=uuid.uuid4(),
        filename="unicode.pdf",
        source_sha256="c" * 64,
    )

    assert len(chunks) >= 2
    assert all(chunk.token_count <= 5 for chunk in chunks)
