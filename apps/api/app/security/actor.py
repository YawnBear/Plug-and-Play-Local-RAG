import uuid
from dataclasses import dataclass
from enum import StrEnum


class ActorRole(StrEnum):
    ADMIN = "admin"
    MEMBER = "member"


@dataclass(frozen=True, slots=True)
class ActorContext:
    user_id: uuid.UUID
    role: ActorRole
    authentication_version: int
    authorization_version: int
    session_id: uuid.UUID

    def __post_init__(self) -> None:
        if self.authentication_version < 1:
            raise ValueError("authentication_version must be positive")
        if self.authorization_version < 1:
            raise ValueError("authorization_version must be positive")
