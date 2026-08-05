import { z } from "zod";

import { uuidSchema } from "@/features/chats/contracts";

const timestampSchema = z.iso.datetime({ offset: true });

export const libraryNodeSchema = z
  .object({
    node_id: uuidSchema,
    parent_id: uuidSchema.nullable(),
    kind: z.enum(["folder", "file"]),
    name: z.string().min(1),
    logical_path: z.string().min(1),
    document_id: uuidSchema.nullable(),
    uploader_user_id: uuidSchema.nullable(),
    can_manage: z.boolean(),
    can_create_children: z.boolean(),
    readable_document_count: z.number().int().nonnegative(),
  })
  .strict();

export const libraryBrowseSchema = z
  .object({
    parent_id: uuidSchema.nullable(),
    breadcrumbs: z.array(libraryNodeSchema),
    children: z.array(libraryNodeSchema),
    page: z.number().int().positive(),
    limit: z.number().int().min(1).max(100),
    total: z.number().int().nonnegative(),
  })
  .strict();

export type LibraryTreeNode = {
  node_id: string;
  parent_id: string | null;
  name: string;
  logical_path: string;
  children: LibraryTreeNode[];
};

export const libraryTreeNodeSchema: z.ZodType<LibraryTreeNode> = z.lazy(() =>
  z
    .object({
      node_id: uuidSchema,
      parent_id: uuidSchema.nullable(),
      name: z.string().min(1),
      logical_path: z.string().min(1),
      children: z.array(libraryTreeNodeSchema),
    })
    .strict(),
);

export const libraryTreeSchema = z.array(libraryTreeNodeSchema);

export const documentSummarySchema = z
  .object({
    document_id: uuidSchema,
    filename: z.string().min(1),
    sha256: z.string().regex(/^[0-9a-f]{64}$/),
    state: z.string().min(1),
    page_count: z.number().int().positive().nullable(),
    chunk_count: z.number().int().nonnegative(),
    created_at: timestampSchema,
    updated_at: timestampSchema,
    error: z.string().nullable(),
    node_id: uuidSchema,
    parent_id: uuidSchema.nullable(),
    display_name: z.string().min(1),
    logical_path: z.string().min(1),
    uploader_user_id: uuidSchema,
    can_manage: z.boolean(),
    team_ids: z.array(uuidSchema),
  })
  .strict();

export const documentListSchema = z.array(documentSummarySchema);

export const documentUploadAcceptedSchema = z
  .object({
    document_id: uuidSchema,
    job_id: uuidSchema,
    status: z.string().min(1),
    duplicate_of: uuidSchema.nullable(),
    node_id: uuidSchema,
    parent_id: uuidSchema.nullable(),
    display_name: z.string().min(1),
    logical_path: z.string().min(1),
    location_reused: z.boolean(),
  })
  .strict();

export const documentReingestAcceptedSchema = z
  .object({
    document_id: uuidSchema,
    job_id: uuidSchema,
    status: z.literal("queued"),
  })
  .strict();

export const accountTeamSchema = z
  .object({
    id: uuidSchema,
    name: z.string().min(1),
    is_active: z.literal(true),
  })
  .strict();

export const accountTeamsSchema = z
  .object({
    teams: z.array(accountTeamSchema),
    requires_team_selection: z.boolean(),
  })
  .strict();

export const aclImpactSchema = z
  .object({
    user_ids: z.array(uuidSchema),
    node_ids: z.array(uuidSchema),
    document_ids: z.array(uuidSchema),
    user_count: z.number().int().nonnegative(),
    node_count: z.number().int().nonnegative(),
    document_count: z.number().int().nonnegative(),
  })
  .strict();

export const nodeMovePreviewSchema = z
  .object({
    preview_id: uuidSchema,
    impact_digest: z.string().regex(/^[0-9a-f]{64}$/),
    impact: aclImpactSchema,
  })
  .strict();

export const jobStatusSchema = z
  .object({
    job_id: uuidSchema,
    document_id: uuidSchema,
    status: z.string().min(1),
    stage: z.string().min(1),
    completed_units: z.number().int().nonnegative(),
    total_units: z.number().int().nonnegative().nullable(),
    error: z.string().nullable(),
  })
  .strict();

export type LibraryNode = z.infer<typeof libraryNodeSchema>;
export type LibraryBrowse = z.infer<typeof libraryBrowseSchema>;
export type DocumentSummary = z.infer<typeof documentSummarySchema>;
export type DocumentUploadAccepted = z.infer<
  typeof documentUploadAcceptedSchema
>;
export type DocumentReingestAccepted = z.infer<
  typeof documentReingestAcceptedSchema
>;
export type JobStatus = z.infer<typeof jobStatusSchema>;
export type AccountTeam = z.infer<typeof accountTeamSchema>;
export type AccountTeams = z.infer<typeof accountTeamsSchema>;
export type NodeMovePreview = z.infer<typeof nodeMovePreviewSchema>;
