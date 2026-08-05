import math
from enum import StrEnum


class DocumentState(StrEnum):
    UPLOADED = "uploaded"
    PARSING = "parsing"
    CHUNKING = "chunking"
    EMBEDDING = "embedding"
    INDEXING = "indexing"
    READY = "ready"
    FAILED = "failed"


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    INTERRUPTED = "interrupted"


class ParseMethod(StrEnum):
    DIRECT = "direct"
    OCR = "ocr"


def validate_embedding(values: list[float], *, dimension: int = 1024) -> None:
    if len(values) != dimension:
        raise ValueError(f"embedding must contain exactly {dimension} values")
    if not all(math.isfinite(value) for value in values):
        raise ValueError("embedding values must all be finite")
    if not any(value != 0 for value in values):
        raise ValueError("embedding must have a non-zero norm")
