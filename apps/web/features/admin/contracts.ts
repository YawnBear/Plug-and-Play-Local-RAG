import { z } from "zod";

import { authUserSchema } from "@/features/auth/contracts";
import { uuidSchema } from "@/features/chats/contracts";

const timestampSchema = z.iso.datetime({ offset: true });

export const adminUserListSchema = z
  .object({ users: z.array(authUserSchema) })
  .strict();

export const adminActivationSchema = z
  .object({
    user_id: uuidSchema,
    activation_code: z.string().min(1),
  })
  .strict();

export const adminTeamSchema = z
  .object({
    id: uuidSchema,
    name: z.string().min(1),
    is_active: z.boolean(),
    member_ids: z.array(uuidSchema),
    member_count: z.number().int().nonnegative(),
  })
  .strict();

export const adminTeamListSchema = z
  .object({ teams: z.array(adminTeamSchema) })
  .strict();

export const adminTeamCreatedSchema = z
  .object({ team_id: uuidSchema })
  .strict();

export const adminGrantSchema = z
  .object({
    id: uuidSchema,
    node_id: uuidSchema,
    user_id: uuidSchema.nullable(),
    team_id: uuidSchema.nullable(),
  })
  .strict()
  .refine((value) => (value.user_id === null) !== (value.team_id === null), {
    message: "A grant must identify exactly one principal.",
  });

export const adminGrantListSchema = z
  .object({ grants: z.array(adminGrantSchema) })
  .strict();

export const adminAuditEventSchema = z
  .object({
    id: uuidSchema,
    actor_user_id: uuidSchema.nullable(),
    event_type: z.string().min(1),
    target_type: z.string().nullable(),
    target_id: uuidSchema.nullable(),
    details: z.record(z.string(), z.unknown()),
    correlation_id: uuidSchema.nullable(),
    created_at: timestampSchema,
  })
  .strict();

export const adminAuditListSchema = z
  .object({ events: z.array(adminAuditEventSchema) })
  .strict();

export const aclOperationSchema = z.discriminatedUnion("kind", [
  z
    .object({
      kind: z.literal("set_grant"),
      node_id: uuidSchema,
      user_id: uuidSchema.optional(),
      team_id: uuidSchema.optional(),
      present: z.boolean(),
    })
    .strict()
    .refine(
      (value) =>
        (value.user_id === undefined) !== (value.team_id === undefined),
      { message: "Choose exactly one user or team." },
    ),
  z
    .object({
      kind: z.literal("set_create_children_grant"),
      folder_id: uuidSchema,
      user_id: uuidSchema.optional(),
      team_id: uuidSchema.optional(),
      present: z.boolean(),
    })
    .strict()
    .refine(
      (value) =>
        (value.user_id === undefined) !== (value.team_id === undefined),
      { message: "Choose exactly one user or team." },
    ),
  z
    .object({
      kind: z.literal("set_membership"),
      team_id: uuidSchema,
      user_id: uuidSchema,
      present: z.boolean(),
    })
    .strict(),
  z
    .object({
      kind: z.literal("set_boundary"),
      node_id: uuidSchema,
      enabled: z.boolean(),
    })
    .strict(),
  z
    .object({
      kind: z.literal("set_team_active"),
      team_id: uuidSchema,
      active: z.boolean(),
    })
    .strict(),
]);

export const aclPreviewSchema = z
  .object({
    preview_id: uuidSchema,
    impact_digest: z.string().regex(/^[0-9a-f]{64}$/),
    impact: z
      .object({
        user_ids: z.array(uuidSchema),
        node_ids: z.array(uuidSchema),
        document_ids: z.array(uuidSchema),
        user_count: z.number().int().nonnegative(),
        node_count: z.number().int().nonnegative(),
        document_count: z.number().int().nonnegative(),
      })
      .strict(),
  })
  .strict();

export const adminInheritedGrantSchema = z
  .object({
    source_node_id: uuidSchema,
    user_id: uuidSchema.nullable(),
    team_id: uuidSchema.nullable(),
  })
  .strict();

export const adminAccessContextSchema = z
  .object({
    node_id: uuidSchema,
    nearest_boundary_node_id: uuidSchema.nullable(),
    direct_grants: z.array(adminGrantSchema),
    inherited_grants: z.array(adminInheritedGrantSchema),
    direct_create_grants: z.array(adminGrantSchema),
    inherited_create_grants: z.array(adminInheritedGrantSchema),
  })
  .strict();

export const aclApplySchema = z
  .object({
    authorization_version: z.number().int().positive(),
  })
  .strict();

export type AdminUser = z.infer<typeof authUserSchema>;
export type AdminTeam = z.infer<typeof adminTeamSchema>;
export type AdminGrant = z.infer<typeof adminGrantSchema>;
export type AdminAuditEvent = z.infer<typeof adminAuditEventSchema>;
export type AclOperation = z.infer<typeof aclOperationSchema>;
export type AclPreview = z.infer<typeof aclPreviewSchema>;
export type AdminAccessContext = z.infer<typeof adminAccessContextSchema>;
