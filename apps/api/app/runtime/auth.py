import secrets
from dataclasses import dataclass

from fastapi import Header, HTTPException, status

MINIMUM_SERVICE_TOKEN_BYTES = 32


@dataclass(frozen=True, slots=True)
class LocalServiceAuthenticator:
    """Constant-time bearer authentication for loopback service protocols."""

    token: str

    def __post_init__(self) -> None:
        if len(self.token.encode("utf-8")) < MINIMUM_SERVICE_TOKEN_BYTES:
            raise ValueError("service token must contain at least 32 UTF-8 bytes")
        if any(character.isspace() for character in self.token):
            raise ValueError("service token must not contain whitespace")

    async def __call__(
        self,
        authorization: str | None = Header(default=None),
    ) -> None:
        scheme, separator, candidate = (authorization or "").partition(" ")
        authenticated = (
            bool(separator)
            and scheme == "Bearer"
            and bool(candidate)
            and secrets.compare_digest(
                candidate.encode("utf-8"),
                self.token.encode("utf-8"),
            )
        )
        if not authenticated:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="service authentication required",
                headers={"WWW-Authenticate": "Bearer"},
            )
