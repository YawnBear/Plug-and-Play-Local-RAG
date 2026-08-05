import { apiFetch, jsonRequest, requestJson } from "@/lib/http";

import {
  capabilitiesSchema,
  configurationApplySchema,
  configurationChangesSchema,
  configurationPreviewSchema,
  configurationSchema,
  reauthenticationSchema,
  diagnosticsPreviewSchema,
  operationResponseSchema,
  operationsSchema,
  overviewSchema,
  personalBackupResponseSchema,
  personalBackupHistorySchema,
  personalBackupStatusSchema,
  generationActionSchema,
  ingestionProfileSelectionResponseSchema,
  reprocessingOperationResponseSchema,
  reprocessingOperationsSchema,
  reprocessingPreviewSchema,
  versionInventorySchema,
} from "./contracts";
import type { ConfigurationSelection } from "./contracts";

export const getSystemOverview = (signal?: AbortSignal) =>
  requestJson("/api/admin/system/overview", overviewSchema, { signal });

export const getSystemCapabilities = (signal?: AbortSignal) =>
  requestJson("/api/admin/system/capabilities", capabilitiesSchema, { signal });

export const getSystemConfiguration = (signal?: AbortSignal) =>
  requestJson("/api/admin/system/configuration", configurationSchema, { signal });

export const getSystemOperations = (signal?: AbortSignal) =>
  requestJson("/api/admin/system/operations", operationsSchema, { signal });

export const getConfigurationChanges = (signal?: AbortSignal) =>
  requestJson("/api/admin/system/configuration/changes", configurationChangesSchema, { signal });

export const getPersonalBackupStatus = (signal?: AbortSignal) =>
  requestJson("/api/admin/system/backups/latest", personalBackupStatusSchema, { signal });

export const getPersonalBackupHistory = (signal?: AbortSignal) =>
  requestJson("/api/admin/system/backups?limit=25", personalBackupHistorySchema, { signal });

export const startPersonalBackup = () =>
  requestJson("/api/admin/system/backups", personalBackupResponseSchema, { method: "POST" });

export const getVersionInventory = (signal?: AbortSignal) =>
  requestJson("/api/admin/system/versions", versionInventorySchema, { signal });

export const selectIngestionProfile = (baseRevision: string, parserProfileId: string) =>
  requestJson(
    "/api/admin/system/versions/ingestion",
    ingestionProfileSelectionResponseSchema,
    jsonRequest(
      {
        base_revision: baseRevision,
        parser_profile_id: parserProfileId,
      },
      { method: "PUT" },
    ),
  );

export const getReprocessingOperations = (signal?: AbortSignal) =>
  requestJson("/api/admin/system/reprocessing", reprocessingOperationsSchema, { signal });

export const previewReprocessing = (
  operationType: "reindex" | "reingestion",
  targetProfileId: string,
  sourceParserVersion: string | null,
) => requestJson(
  "/api/admin/system/reprocessing/preview",
  reprocessingPreviewSchema,
  jsonRequest(
    {
      operation_type: operationType,
      target_profile_id: targetProfileId,
      source_parser_version: sourceParserVersion,
    },
    { method: "POST" },
  ),
);

export const reauthenticateReprocessing = (
  previewId: string,
  impactDigest: string,
  password: string,
) => requestJson(
  "/api/admin/system/reprocessing/reauthenticate",
  reauthenticationSchema,
  jsonRequest(
    {
      preview_id: previewId,
      impact_digest: impactDigest,
      password,
    },
    { method: "POST" },
  ),
);

export const startReprocessing = (
  previewId: string,
  impactDigest: string,
  reauthenticationGrant: string,
) => requestJson(
  "/api/admin/system/reprocessing",
  reprocessingOperationResponseSchema,
  jsonRequest(
    {
      preview_id: previewId,
      impact_digest: impactDigest,
      reauthentication_grant: reauthenticationGrant,
    },
    { method: "POST" },
  ),
);

export const controlReprocessing = (
  operationId: string,
  action: "pause" | "resume" | "cancel" | "retry",
) => requestJson(
  `/api/admin/system/reprocessing/${encodeURIComponent(operationId)}/${action}`,
  reprocessingOperationResponseSchema,
  { method: "POST" },
);

export const rollbackEmbeddingGeneration = (generationId: string) =>
  requestJson(
    `/api/admin/system/generations/embedding/${encodeURIComponent(generationId)}/rollback`,
    generationActionSchema,
    { method: "POST" },
  );

export const cleanupGeneration = (generationType: "embedding" | "document", generationId: string) =>
  requestJson(
    `/api/admin/system/generations/${generationType}/${encodeURIComponent(generationId)}`,
    generationActionSchema,
    { method: "DELETE" },
  );

export const previewConfiguration = (selection: ConfigurationSelection) =>
  requestJson(
    "/api/admin/system/configuration/preview",
    configurationPreviewSchema,
    jsonRequest(selection, { method: "POST" }),
  );

export const reauthenticateConfiguration = (
  previewId: string,
  impactDigest: string,
  password: string,
) => requestJson(
  "/api/admin/system/configuration/reauthenticate",
  reauthenticationSchema,
  jsonRequest(
    { preview_id: previewId, impact_digest: impactDigest, password },
    { method: "POST" },
  ),
);

export const applyConfiguration = (
  previewId: string,
  impactDigest: string,
  reauthenticationGrant: string,
) => requestJson(
  "/api/admin/system/configuration/apply",
  configurationApplySchema,
  jsonRequest(
    {
      preview_id: previewId,
      impact_digest: impactDigest,
      reauthentication_grant: reauthenticationGrant,
    },
    { method: "POST" },
  ),
);

export const getDiagnosticsPreview = (signal?: AbortSignal) =>
  requestJson("/api/admin/system/diagnostics/preview", diagnosticsPreviewSchema, { signal });

export const runProfileValidation = (profileId: string, benchmark = false) =>
  requestJson(
    `/api/admin/system/profiles/${encodeURIComponent(profileId)}/${benchmark ? "benchmark" : "validate"}`,
    operationResponseSchema,
    { method: "POST" },
  );

export async function downloadDiagnostics(): Promise<void> {
  const response = await apiFetch("/api/admin/system/diagnostics/export", { method: "POST" });
  const url = URL.createObjectURL(await response.blob());
  const link = document.createElement("a");
  link.href = url;
  link.download = "rag-system-diagnostics.zip";
  link.click();
  URL.revokeObjectURL(url);
}
