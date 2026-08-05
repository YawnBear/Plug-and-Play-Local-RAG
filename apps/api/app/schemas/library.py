from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.admin import AdminAclImpact


class LibraryNodeResponse(BaseModel):
    node_id: UUID
    parent_id: UUID | None
    kind: str
    name: str
    logical_path: str
    document_id: UUID | None
    uploader_user_id: UUID | None
    can_manage: bool
    can_create_children: bool
    readable_document_count: int = Field(ge=0)


class AccountTeamResponse(BaseModel):
    id: UUID
    name: str
    is_active: bool


class AccountTeamListResponse(BaseModel):
    teams: list[AccountTeamResponse]
    requires_team_selection: bool


class LibraryBrowseResponse(BaseModel):
    parent_id: UUID | None
    breadcrumbs: list[LibraryNodeResponse]
    children: list[LibraryNodeResponse]
    page: int = Field(ge=1)
    limit: int = Field(ge=1, le=100)
    total: int = Field(ge=0)


class LibraryTreeNodeResponse(BaseModel):
    node_id: UUID
    parent_id: UUID | None
    name: str
    logical_path: str
    children: list["LibraryTreeNodeResponse"]


class FolderCreateRequest(BaseModel):
    name: str = Field(min_length=1)
    parent_id: UUID | None = None


class NodePatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    parent_id: UUID | None = None
    preview_id: UUID | None = None
    impact_digest: str | None = Field(
        default=None,
        min_length=64,
        max_length=64,
        pattern=r"^[0-9a-f]{64}$",
    )

    @model_validator(mode="after")
    def require_bound_move_confirmation(self) -> "NodePatchRequest":
        fields = self.model_fields_set
        moving = "parent_id" in fields
        renaming = "name" in fields
        if moving and renaming:
            raise ValueError("rename and move must be separate operations")
        if not moving and not renaming:
            raise ValueError("at least one of name or parent_id is required")
        if moving and (self.preview_id is None or self.impact_digest is None):
            raise ValueError("move requires preview_id and impact_digest")
        if not moving and (
            self.preview_id is not None or self.impact_digest is not None
        ):
            raise ValueError("ACL confirmation is only valid for a move")
        return self


class NodeMovePreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    parent_id: UUID | None


class NodeMovePreviewResponse(BaseModel):
    preview_id: UUID
    impact_digest: str = Field(
        min_length=64,
        max_length=64,
        pattern=r"^[0-9a-f]{64}$",
    )
    impact: AdminAclImpact
