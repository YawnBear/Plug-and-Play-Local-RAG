from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, Field


class UserRole(StrEnum):
    ADMIN = "admin"
    MEMBER = "member"


class UserStatus(StrEnum):
    PENDING_ACTIVATION = "pending_activation"
    ACTIVE = "active"
    DISABLED = "disabled"
    DELETED = "deleted"


class AuthUser(BaseModel):
    id: UUID
    username: str
    display_name: str
    role: UserRole
    status: UserStatus


class AuthSessionResponse(BaseModel):
    user: AuthUser
    csrf_token: str = Field(min_length=1)


class AuthMeResponse(BaseModel):
    user: AuthUser | None
    csrf_token: str = Field(min_length=1)


class LoginRequest(BaseModel):
    username: str = Field(min_length=1)
    password: str = Field(min_length=1)


class ActivationRequest(BaseModel):
    code: str = Field(min_length=1)
    password: str


class PasswordChangeRequest(BaseModel):
    current_password: str = Field(min_length=1)
    new_password: str
