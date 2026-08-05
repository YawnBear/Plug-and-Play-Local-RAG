from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.auth import AuthUser, UserRole


class _StrictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AdminUserCreateRequest(_StrictRequest):
    username: str = Field(min_length=1)
    display_name: str = Field(min_length=1)
    role: UserRole


class AdminUserMutationRequest(_StrictRequest):
    role: UserRole
    status: Literal["active", "disabled", "deleted"]


class AdminActivationResponse(BaseModel):
    user_id: UUID
    activation_code: str = Field(min_length=1)


class AdminTeamCreateRequest(_StrictRequest):
    name: str = Field(min_length=1)


class AdminTeamCreateResponse(BaseModel):
    team_id: UUID


class AdminTeamMemberRequest(_StrictRequest):
    user_id: UUID


class AdminSetGrantOperation(_StrictRequest):
    kind: Literal["set_grant"]
    node_id: UUID
    user_id: UUID | None = None
    team_id: UUID | None = None
    present: bool

    @model_validator(mode="after")
    def require_exactly_one_principal(self) -> "AdminSetGrantOperation":
        if (self.user_id is None) == (self.team_id is None):
            raise ValueError("exactly one of user_id or team_id is required")
        return self


class AdminSetMembershipOperation(_StrictRequest):
    kind: Literal["set_membership"]
    team_id: UUID
    user_id: UUID
    present: bool


class AdminSetCreateChildrenGrantOperation(_StrictRequest):
    kind: Literal["set_create_children_grant"]
    folder_id: UUID
    user_id: UUID | None = None
    team_id: UUID | None = None
    present: bool

    @model_validator(mode="after")
    def require_exactly_one_principal(
        self,
    ) -> "AdminSetCreateChildrenGrantOperation":
        if (self.user_id is None) == (self.team_id is None):
            raise ValueError("exactly one of user_id or team_id is required")
        return self


class AdminSetBoundaryOperation(_StrictRequest):
    kind: Literal["set_boundary"]
    node_id: UUID
    enabled: bool


class AdminSetTeamActiveOperation(_StrictRequest):
    kind: Literal["set_team_active"]
    team_id: UUID
    active: bool


class AdminMoveNodeOperation(_StrictRequest):
    kind: Literal["move_node"]
    node_id: UUID
    parent_id: UUID | None


AdminAclOperation = Annotated[
    AdminSetGrantOperation
    | AdminSetCreateChildrenGrantOperation
    | AdminSetMembershipOperation
    | AdminSetBoundaryOperation
    | AdminSetTeamActiveOperation
    | AdminMoveNodeOperation,
    Field(discriminator="kind"),
]


class AdminAclPreviewRequest(_StrictRequest):
    operation: AdminAclOperation


class AdminAclImpact(BaseModel):
    user_ids: list[UUID]
    node_ids: list[UUID]
    document_ids: list[UUID]
    user_count: int = Field(ge=0)
    node_count: int = Field(ge=0)
    document_count: int = Field(ge=0)


class AdminAclPreviewResponse(BaseModel):
    preview_id: UUID
    impact_digest: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    impact: AdminAclImpact


class AdminAclApplyRequest(_StrictRequest):
    preview_id: UUID
    impact_digest: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")


class AdminAclApplyResponse(BaseModel):
    authorization_version: int = Field(ge=1)


class AdminUserListResponse(BaseModel):
    users: list[AuthUser]


class AdminTeam(BaseModel):
    id: UUID
    name: str
    is_active: bool
    member_ids: list[UUID] = Field(default_factory=list)
    member_count: int = Field(ge=0)


class AdminTeamListResponse(BaseModel):
    teams: list[AdminTeam]


class AdminGrant(BaseModel):
    id: UUID
    node_id: UUID
    user_id: UUID | None
    team_id: UUID | None


class AdminGrantListResponse(BaseModel):
    grants: list[AdminGrant]


class AdminInheritedGrant(BaseModel):
    source_node_id: UUID
    user_id: UUID | None
    team_id: UUID | None


class AdminAccessContextResponse(BaseModel):
    node_id: UUID
    nearest_boundary_node_id: UUID | None
    direct_grants: list[AdminGrant]
    inherited_grants: list[AdminInheritedGrant]
    direct_create_grants: list[AdminGrant]
    inherited_create_grants: list[AdminInheritedGrant]


class AdminAuditEvent(BaseModel):
    id: UUID
    actor_user_id: UUID | None
    event_type: str
    target_type: str | None
    target_id: UUID | None
    details: dict[str, object]
    correlation_id: UUID | None
    created_at: datetime


class AdminAuditListResponse(BaseModel):
    events: list[AdminAuditEvent]
