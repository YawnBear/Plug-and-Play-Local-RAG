from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class SystemModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SystemServiceStatus(SystemModel):
    service_id: str
    label: str
    state: Literal["ready", "degraded", "unavailable", "unknown"]
    reason_code: str
    message: str


class SystemCounts(SystemModel):
    ready: int = Field(ge=0)
    processing: int = Field(ge=0)
    failed: int = Field(ge=0)


class SystemJobCounts(SystemModel):
    active: int = Field(ge=0)
    queued: int = Field(ge=0)


class SystemDisk(SystemModel):
    total_bytes: int = Field(ge=0)
    free_bytes: int = Field(ge=0)


class SystemOverviewResponse(SystemModel):
    product_profile: Literal["personal", "team_lan", "contributor"]
    overall_state: Literal["ready", "attention", "unavailable"]
    recommended_action: str
    services: list[SystemServiceStatus]
    documents: SystemCounts
    jobs: SystemJobCounts
    disk: SystemDisk
    operation_count: int = Field(ge=0)


class SystemValidationEvidence(SystemModel):
    state: Literal["locally_validated", "failed"]
    reason_code: str
    fixture_id: str
    evidence_at: datetime
    metrics: dict[str, str | int | float | None]


class SystemCapabilityProfile(SystemModel):
    profile_id: str
    profile_revision: int
    function: Literal["generation", "embedding", "reranking", "ocr"]
    release_support_class: Literal["release_qualified", "experimental", "unavailable"]
    local_validation_state: Literal[
        "not_detected",
        "detected",
        "package_available",
        "installed",
        "smoke_tested",
        "locally_validated",
        "failed",
    ]
    engine: str
    model_identity: str
    accelerator_vendor: str
    minimum_ram_gib: int
    minimum_vram_gib: int
    impact_class: str
    effective: bool
    selectable: bool
    reason: str
    evidence: SystemValidationEvidence | None = None


class SystemCapabilitiesResponse(SystemModel):
    catalog_id: str
    catalog_revision: int
    profiles: list[SystemCapabilityProfile]
    observed_processor: str
    logical_cpu_count: int = Field(ge=1)
    system_memory_bytes: int = Field(ge=0)
    maximum_ocr_processes: int = Field(ge=1, le=16)


class SystemConfigurationResponse(SystemModel):
    effective_revision: str
    desired_revision: str
    state: Literal["effective", "pending", "applying"]
    generation_profile_id: str
    generation_model: str
    embedding_profile_id: str
    embedding_model: str
    reranker_profile_id: str
    reranker_model: str
    parser_identity: str
    ocr_profile_id: str
    ocr_device: str
    ocr_engine: str
    ocr_cpu_threads: int
    ocr_process_count: int
    ocr_page_batch_size: int
    maximum_generation_context: int
    maximum_generation_output: int
    ocr_mode: Literal["auto", "explicit"]
    ocr_preset_id: str
    impact_digest: str | None = None
    operation_class: Literal["restart_scoped"] | None = None
    prior_revision: str | None = None
    proposed_by: UUID | None = None
    proposed_at: datetime | None = None
    reason_code: str | None = None
    backup_verified: bool
    backup_verified_at: datetime | None = None


class RuntimeConfigurationSelection(SystemModel):
    base_revision: str
    generation_profile_id: str
    reranker_profile_id: str
    ocr_mode: Literal["auto", "explicit"]
    ocr_profile_id: str
    ocr_cpu_threads: int = Field(ge=1, le=256)
    ocr_process_count: int = Field(ge=1, le=16)


class RuntimeConfigurationPreviewResponse(SystemModel):
    preview_id: UUID
    impact_digest: str
    expires_at: datetime
    operation_class: Literal["restart_scoped"] = "restart_scoped"
    affected_services: list[Literal["coordinator", "ocr"]]
    waits_for: list[Literal["active_answer_boundary", "active_ocr_boundary"]]
    expected_interruption: str
    backup_required: Literal[False] = False


class SystemReauthenticationRequest(SystemModel):
    preview_id: UUID
    impact_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    password: str = Field(min_length=1, max_length=1024)


class SystemReauthenticationResponse(SystemModel):
    grant_token: str
    expires_at: datetime


class RuntimeConfigurationApplyRequest(SystemModel):
    preview_id: UUID
    impact_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    reauthentication_grant: str = Field(min_length=43, max_length=256)


class RuntimeConfigurationChange(SystemModel):
    change_id: UUID
    actor_user_id: UUID
    prior_revision: str
    desired_revision: str
    impact_digest: str
    operation_class: Literal["restart_scoped"]
    state: Literal[
        "pending", "applying", "effective", "failed", "rolled_back", "cancelled"
    ]
    stage: Literal[
        "queued",
        "preflight",
        "backing_up",
        "draining",
        "applying",
        "restarting",
        "validating",
        "effective",
        "failed",
        "rolling_back",
        "rolled_back",
        "cancelled",
    ]
    reason_code: str | None
    created_at: datetime
    finished_at: datetime | None


class RuntimeConfigurationApplyResponse(SystemModel):
    change: RuntimeConfigurationChange


class RuntimeConfigurationChangesResponse(SystemModel):
    changes: list[RuntimeConfigurationChange]


class PersonalBackupOperation(SystemModel):
    backup_run_id: UUID
    state: Literal["pending", "running", "succeeded", "failed"]
    stage: Literal[
        "queued", "draining", "exporting", "verifying", "succeeded", "failed"
    ]
    reason_code: str | None
    created_at: datetime
    finished_at: datetime | None
    restore_verified: bool
    manifest_sha256: str | None


class PersonalBackupResponse(SystemModel):
    operation: PersonalBackupOperation


class PersonalBackupStatusResponse(SystemModel):
    operation: PersonalBackupOperation | None


class PersonalBackupHistoryResponse(SystemModel):
    retention_mode: Literal["keep_all"] = "keep_all"
    automatic_deletion: Literal[False] = False
    destination_mode: Literal["attended_folder_picker"] = "attended_folder_picker"
    operations: list[PersonalBackupOperation]


class SystemOperation(SystemModel):
    operation_id: UUID
    operation_type: Literal["profile_validation", "profile_benchmark"]
    profile_id: str
    state: Literal["running", "effective", "failed"]
    stage: Literal["preflight", "validating", "benchmarking", "effective", "failed"]
    reason_code: str | None
    metrics: dict[str, str | int | float | None]
    created_at: datetime
    finished_at: datetime | None


class SystemOperationsResponse(SystemModel):
    operations: list[SystemOperation]


class SystemOperationResponse(SystemModel):
    operation: SystemOperation


class SystemDiagnosticsPreviewResponse(SystemModel):
    privacy_mode: Literal[True] = True
    files: list[str]
    exclusions: list[str]


class IngestionVersionCount(SystemModel):
    parser_version: str
    document_count: int = Field(ge=0)


class DocumentGenerationSummary(SystemModel):
    generation_id: UUID
    document_id: UUID
    filename: str
    parser_version: str
    chunking_version: str
    state: Literal["building", "ready", "failed", "retained", "abandoned"]
    chunk_count: int = Field(ge=0)
    created_at: datetime
    retired_at: datetime | None = None
    cleanup_available: bool


class EmbeddingGenerationSummary(SystemModel):
    generation_id: UUID
    profile_id: str
    embedding_version: str
    dimension: int = Field(ge=1, le=4096)
    state: Literal["building", "qualified", "active", "retained", "abandoned"]
    chunk_count: int = Field(ge=0)
    created_at: datetime
    activated_at: datetime | None = None
    retired_at: datetime | None = None
    cleanup_available: bool


class IngestionVersionInventory(SystemModel):
    revision_id: str
    parser_profile_id: str
    parser_version: str
    chunking_version: str
    document_versions: list[IngestionVersionCount]
    generations: list[DocumentGenerationSummary]


class EmbeddingVersionInventory(SystemModel):
    active_generation_id: UUID
    profile_id: str
    embedding_version: str
    dimension: int = Field(ge=1, le=4096)
    generations: list[EmbeddingGenerationSummary]


class VersionInventoryResponse(SystemModel):
    ingestion: IngestionVersionInventory
    embedding: EmbeddingVersionInventory


class IngestionProfileSelection(SystemModel):
    base_revision: str
    parser_profile_id: str


class IngestionProfileSelectionResponse(SystemModel):
    revision_id: str


class ReprocessingPreviewRequest(SystemModel):
    operation_type: Literal["reindex", "reingestion"]
    target_profile_id: str
    source_parser_version: str | None = None


class ReprocessingPreviewResponse(SystemModel):
    preview_id: UUID
    impact_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    expires_at: datetime
    document_count: int = Field(ge=0)
    chunk_count: int = Field(ge=0)
    estimated_bytes: int = Field(ge=0)
    backup_verified: bool
    backup_required: Literal[True] = True


class ReprocessingStartRequest(SystemModel):
    preview_id: UUID
    impact_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    reauthentication_grant: str = Field(min_length=43, max_length=256)


class ReprocessingOperation(SystemModel):
    operation_id: UUID
    operation_type: Literal["reindex", "reingestion"]
    state: Literal[
        "running", "paused", "qualifying", "succeeded", "failed", "cancelled"
    ]
    stage: Literal[
        "queued",
        "processing",
        "paused",
        "qualifying",
        "cutover",
        "succeeded",
        "failed",
        "cancelled",
    ]
    target_profile_id: str
    source_parser_version: str | None = None
    target_parser_version: str | None = None
    target_embedding_version: str | None = None
    target_dimension: int | None = None
    impact_digest: str
    total_documents: int = Field(ge=0)
    completed_documents: int = Field(ge=0)
    failed_documents: int = Field(ge=0)
    total_chunks: int = Field(ge=0)
    completed_chunks: int = Field(ge=0)
    reason_code: str | None = None
    qualification: dict[str, object]
    operation_generation_id: UUID | None = None
    created_at: datetime
    finished_at: datetime | None = None


class ReprocessingOperationResponse(SystemModel):
    operation: ReprocessingOperation


class ReprocessingOperationsResponse(SystemModel):
    operations: list[ReprocessingOperation]


class GenerationActionResponse(SystemModel):
    succeeded: bool
