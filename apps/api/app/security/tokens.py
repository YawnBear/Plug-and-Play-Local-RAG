import hashlib
import secrets
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class OpaqueToken:
    plaintext: str
    digest: str


def hash_opaque_token(token: str) -> str:
    if not token:
        raise ValueError("opaque token must not be empty")
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def issue_opaque_token() -> OpaqueToken:
    plaintext = secrets.token_urlsafe(32)
    return OpaqueToken(plaintext=plaintext, digest=hash_opaque_token(plaintext))
