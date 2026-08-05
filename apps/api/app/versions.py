PARSER_VERSION = "pypdf+paddleocr-vl-v1.6"
ADAPTIVE_PARSER_VERSION = "pypdf+paddleocr-vl-v1.6-adaptive-v2"
CHUNKING_VERSION = "paragraph-sentence-v1"
FRAGMENT_CHUNKING_VERSION = "fragment-paragraph-sentence-v2"
EMBEDDING_VERSION = "qwen3-embedding-0.6b-1024"
CHUNK_SCHEMA_VERSION = "chunk-v1"


def active_parser_version(*, adaptive_page_routing: bool) -> str:
    return ADAPTIVE_PARSER_VERSION if adaptive_page_routing else PARSER_VERSION


def active_chunking_version(*, visual_supplement_ocr: bool) -> str:
    return (
        FRAGMENT_CHUNKING_VERSION
        if visual_supplement_ocr
        else CHUNKING_VERSION
    )
