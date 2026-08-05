import { z } from "zod";

const evidenceSchema = z.object({
  state: z.enum(["locally_validated", "failed"]),
  reason_code: z.string(),
  fixture_id: z.string(),
  evidence_at: z.string(),
  metrics: z.record(z.string(), z.union([z.string(), z.number(), z.null()])),
}).strict();

export const capabilitySchema = z.object({
  profile_id: z.string(),
  profile_revision: z.number().int(),
  function: z.enum(["generation", "embedding", "reranking", "ocr"]),
  release_support_class: z.enum(["release_qualified", "experimental", "unavailable"]),
  local_validation_state: z.enum([
    "not_detected", "detected", "package_available", "installed",
    "smoke_tested", "locally_validated", "failed",
  ]),
  engine: z.string(),
  model_identity: z.string(),
  accelerator_vendor: z.string(),
  minimum_ram_gib: z.number().int(),
  minimum_vram_gib: z.number().int(),
  impact_class: z.string(),
  effective: z.boolean(),
  selectable: z.boolean(),
  reason: z.string(),
  evidence: evidenceSchema.nullable(),
}).strict();

export const capabilitiesSchema = z.object({
  catalog_id: z.string(),
  catalog_revision: z.number().int(),
  profiles: z.array(capabilitySchema),
  observed_processor: z.string(),
  logical_cpu_count: z.number().int(),
  system_memory_bytes: z.number(),
  maximum_ocr_processes: z.number().int(),
}).strict();

const serviceSchema = z.object({
  service_id: z.string(),
  label: z.string(),
  state: z.enum(["ready", "degraded", "unavailable", "unknown"]),
  reason_code: z.string(),
  message: z.string(),
}).strict();

export const overviewSchema = z.object({
  product_profile: z.enum(["personal", "team_lan", "contributor"]),
  overall_state: z.enum(["ready", "attention", "unavailable"]),
  recommended_action: z.string(),
  services: z.array(serviceSchema),
  documents: z.object({ ready: z.number(), processing: z.number(), failed: z.number() }).strict(),
  jobs: z.object({ active: z.number(), queued: z.number() }).strict(),
  disk: z.object({ total_bytes: z.number(), free_bytes: z.number() }).strict(),
  operation_count: z.number(),
}).strict();

export const configurationSchema = z.object({
  effective_revision: z.string(),
  desired_revision: z.string(),
  state: z.enum(["effective", "pending", "applying"]),
  generation_profile_id: z.string(),
  generation_model: z.string(),
  embedding_profile_id: z.string(),
  embedding_model: z.string(),
  reranker_profile_id: z.string(),
  reranker_model: z.string(),
  parser_identity: z.string(),
  ocr_profile_id: z.string(),
  ocr_device: z.string(),
  ocr_engine: z.string(),
  ocr_cpu_threads: z.number(),
  ocr_process_count: z.number(),
  ocr_page_batch_size: z.number(),
  maximum_generation_context: z.number(),
  maximum_generation_output: z.number(),
  ocr_mode: z.enum(["auto", "explicit"]),
  ocr_preset_id: z.string(),
  impact_digest: z.string().nullable(),
  operation_class: z.literal("restart_scoped").nullable(),
  prior_revision: z.string().nullable(),
  proposed_by: z.string().uuid().nullable(),
  proposed_at: z.string().nullable(),
  reason_code: z.string().nullable(),
  backup_verified: z.boolean(),
  backup_verified_at: z.string().nullable(),
}).strict();

export const configurationSelectionSchema = z.object({
  base_revision: z.string(),
  generation_profile_id: z.string(),
  reranker_profile_id: z.string(),
  ocr_mode: z.enum(["auto", "explicit"]),
  ocr_profile_id: z.string(),
  ocr_cpu_threads: z.number().int().min(1).max(256),
  ocr_process_count: z.number().int().min(1).max(16),
}).strict();

export const configurationPreviewSchema = z.object({
  preview_id: z.string().uuid(),
  impact_digest: z.string().regex(/^[0-9a-f]{64}$/),
  expires_at: z.string(),
  operation_class: z.literal("restart_scoped"),
  affected_services: z.array(z.enum(["coordinator", "ocr"])),
  waits_for: z.array(z.enum(["active_answer_boundary", "active_ocr_boundary"])),
  expected_interruption: z.string(),
  backup_required: z.literal(false),
}).strict();

export const reauthenticationSchema = z.object({
  grant_token: z.string(),
  expires_at: z.string(),
}).strict();

export const configurationChangeSchema = z.object({
  change_id: z.string().uuid(),
  actor_user_id: z.string().uuid(),
  prior_revision: z.string(),
  desired_revision: z.string(),
  impact_digest: z.string(),
  operation_class: z.literal("restart_scoped"),
  state: z.enum(["pending", "applying", "effective", "failed", "rolled_back", "cancelled"]),
  stage: z.enum([
    "queued", "preflight", "backing_up", "draining", "applying", "restarting",
    "validating", "effective", "failed", "rolling_back", "rolled_back", "cancelled",
  ]),
  reason_code: z.string().nullable(),
  created_at: z.string(),
  finished_at: z.string().nullable(),
}).strict();

export const configurationApplySchema = z.object({ change: configurationChangeSchema }).strict();
export const configurationChangesSchema = z.object({ changes: z.array(configurationChangeSchema) }).strict();

export const personalBackupOperationSchema = z.object({
  backup_run_id: z.string().uuid(),
  state: z.enum(["pending", "running", "succeeded", "failed"]),
  stage: z.enum(["queued", "draining", "exporting", "verifying", "succeeded", "failed"]),
  reason_code: z.string().nullable(),
  created_at: z.string(),
  finished_at: z.string().nullable(),
  restore_verified: z.boolean(),
  manifest_sha256: z.string().nullable(),
}).strict();
export const personalBackupResponseSchema = z.object({ operation: personalBackupOperationSchema }).strict();
export const personalBackupStatusSchema = z.object({ operation: personalBackupOperationSchema.nullable() }).strict();
export const personalBackupHistorySchema = z.object({
  retention_mode: z.literal("keep_all"),
  automatic_deletion: z.literal(false),
  destination_mode: z.literal("attended_folder_picker"),
  operations: z.array(personalBackupOperationSchema),
}).strict();

export const operationSchema = z.object({
  operation_id: z.string().uuid(),
  operation_type: z.enum(["profile_validation", "profile_benchmark"]),
  profile_id: z.string(),
  state: z.enum(["running", "effective", "failed"]),
  stage: z.enum(["preflight", "validating", "benchmarking", "effective", "failed"]),
  reason_code: z.string().nullable(),
  metrics: z.record(z.string(), z.union([z.string(), z.number(), z.null()])),
  created_at: z.string(),
  finished_at: z.string().nullable(),
}).strict();

export const operationsSchema = z.object({ operations: z.array(operationSchema) }).strict();
export const operationResponseSchema = z.object({ operation: operationSchema }).strict();
export const diagnosticsPreviewSchema = z.object({
  privacy_mode: z.literal(true),
  files: z.array(z.string()),
  exclusions: z.array(z.string()),
}).strict();

const ingestionVersionCountSchema = z.object({
  parser_version: z.string(),
  document_count: z.number().int().nonnegative(),
}).strict();

const documentGenerationSchema = z.object({
  generation_id: z.string().uuid(),
  document_id: z.string().uuid(),
  filename: z.string(),
  parser_version: z.string(),
  chunking_version: z.string(),
  state: z.enum(["building", "ready", "failed", "retained", "abandoned"]),
  chunk_count: z.number().int().nonnegative(),
  created_at: z.string(),
  retired_at: z.string().nullable(),
  cleanup_available: z.boolean(),
}).strict();

const embeddingGenerationSchema = z.object({
  generation_id: z.string().uuid(),
  profile_id: z.string(),
  embedding_version: z.string(),
  dimension: z.number().int().positive(),
  state: z.enum(["building", "qualified", "active", "retained", "abandoned"]),
  chunk_count: z.number().int().nonnegative(),
  created_at: z.string(),
  activated_at: z.string().nullable(),
  retired_at: z.string().nullable(),
  cleanup_available: z.boolean(),
}).strict();

export const versionInventorySchema = z.object({
  ingestion: z.object({
    revision_id: z.string(),
    parser_profile_id: z.string(),
    parser_version: z.string(),
    chunking_version: z.string(),
    document_versions: z.array(ingestionVersionCountSchema),
    generations: z.array(documentGenerationSchema),
  }).strict(),
  embedding: z.object({
    active_generation_id: z.string().uuid(),
    profile_id: z.string(),
    embedding_version: z.string(),
    dimension: z.number().int().positive(),
    generations: z.array(embeddingGenerationSchema),
  }).strict(),
}).strict();

export const ingestionProfileSelectionResponseSchema = z.object({
  revision_id: z.string(),
}).strict();

export const reprocessingPreviewSchema = z.object({
  preview_id: z.string().uuid(),
  impact_digest: z.string().regex(/^[0-9a-f]{64}$/),
  expires_at: z.string(),
  document_count: z.number().int().nonnegative(),
  chunk_count: z.number().int().nonnegative(),
  estimated_bytes: z.number().nonnegative(),
  backup_verified: z.boolean(),
  backup_required: z.literal(true),
}).strict();

export const reprocessingOperationSchema = z.object({
  operation_id: z.string().uuid(),
  operation_type: z.enum(["reindex", "reingestion"]),
  state: z.enum(["running", "paused", "qualifying", "succeeded", "failed", "cancelled"]),
  stage: z.enum(["queued", "processing", "paused", "qualifying", "cutover", "succeeded", "failed", "cancelled"]),
  target_profile_id: z.string(),
  source_parser_version: z.string().nullable(),
  target_parser_version: z.string().nullable(),
  target_embedding_version: z.string().nullable(),
  target_dimension: z.number().int().positive().nullable(),
  impact_digest: z.string(),
  total_documents: z.number().int().nonnegative(),
  completed_documents: z.number().int().nonnegative(),
  failed_documents: z.number().int().nonnegative(),
  total_chunks: z.number().int().nonnegative(),
  completed_chunks: z.number().int().nonnegative(),
  reason_code: z.string().nullable(),
  qualification: z.record(z.string(), z.unknown()),
  operation_generation_id: z.string().uuid().nullable(),
  created_at: z.string(),
  finished_at: z.string().nullable(),
}).strict();

export const reprocessingOperationResponseSchema = z.object({
  operation: reprocessingOperationSchema,
}).strict();
export const reprocessingOperationsSchema = z.object({
  operations: z.array(reprocessingOperationSchema),
}).strict();
export const generationActionSchema = z.object({ succeeded: z.boolean() }).strict();

export type Capability = z.infer<typeof capabilitySchema>;
export type Capabilities = z.infer<typeof capabilitiesSchema>;
export type Configuration = z.infer<typeof configurationSchema>;
export type ConfigurationChange = z.infer<typeof configurationChangeSchema>;
export type ConfigurationPreview = z.infer<typeof configurationPreviewSchema>;
export type ConfigurationSelection = z.infer<typeof configurationSelectionSchema>;
export type DiagnosticsPreview = z.infer<typeof diagnosticsPreviewSchema>;
export type Operation = z.infer<typeof operationSchema>;
export type Overview = z.infer<typeof overviewSchema>;
export type PersonalBackupOperation = z.infer<typeof personalBackupOperationSchema>;
export type ReprocessingOperation = z.infer<typeof reprocessingOperationSchema>;
export type ReprocessingPreview = z.infer<typeof reprocessingPreviewSchema>;
export type VersionInventory = z.infer<typeof versionInventorySchema>;
