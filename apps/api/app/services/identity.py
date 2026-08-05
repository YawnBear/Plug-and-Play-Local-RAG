import hashlib
import uuid

DOCUMENT_NAMESPACE = uuid.UUID("35762b65-b72b-4d67-b24c-7d43c654c750")
CHUNK_NAMESPACE = uuid.UUID("699aedee-ec6b-4a8f-a775-70584ac78eed")


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def document_uuid(sha256: str) -> uuid.UUID:
    if len(sha256) != 64:
        raise ValueError("document checksum must be a SHA-256 hex digest")
    try:
        int(sha256, 16)
    except ValueError as exc:
        raise ValueError("document checksum must be a SHA-256 hex digest") from exc
    return uuid.uuid5(DOCUMENT_NAMESPACE, sha256.lower())


def chunk_uuid(
    document_id: uuid.UUID,
    *,
    ordinal: int,
    page_number: int,
    text_sha256: str,
    schema_version: str,
) -> uuid.UUID:
    identity = ":".join(
        (
            str(document_id),
            str(ordinal),
            str(page_number),
            text_sha256,
            schema_version,
        )
    )
    return uuid.uuid5(CHUNK_NAMESPACE, identity)
