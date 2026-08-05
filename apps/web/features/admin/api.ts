import {
  jsonRequest,
  requestJson,
  requestVoid,
} from "@/lib/http";
import { requireUuid } from "@/lib/uuid";

import {
  aclApplySchema,
  aclOperationSchema,
  aclPreviewSchema,
  adminAccessContextSchema,
  adminActivationSchema,
  adminAuditListSchema,
  adminGrantListSchema,
  adminTeamCreatedSchema,
  adminTeamListSchema,
  adminUserListSchema,
  type AclOperation,
  type AclPreview,
  type AdminAuditEvent,
  type AdminAccessContext,
  type AdminGrant,
  type AdminTeam,
  type AdminUser,
} from "./contracts";

export async function listAdminUsers(
  signal?: AbortSignal,
): Promise<AdminUser[]> {
  const response = await requestJson("/api/admin/users", adminUserListSchema, {
    cache: "no-store",
    signal,
  });
  return response.users;
}

export async function createAdminUser(
  input: { username: string; display_name: string; role: "admin" | "member" },
  signal?: AbortSignal,
) {
  return requestJson(
    "/api/admin/users",
    adminActivationSchema,
    jsonRequest(input, { method: "POST", signal }),
  );
}

export async function updateAdminUser(
  userId: string,
  input: {
    role: "admin" | "member";
    status: "active" | "disabled" | "deleted";
  },
  signal?: AbortSignal,
): Promise<void> {
  return requestVoid(
    `/api/admin/users/${requireUuid(userId, "user")}`,
    jsonRequest(input, { method: "PATCH", signal }),
  );
}

export async function resetAdminUser(
  userId: string,
  signal?: AbortSignal,
) {
  return requestJson(
    `/api/admin/users/${requireUuid(userId, "user")}/reset`,
    adminActivationSchema,
    { method: "POST", signal },
  );
}

export async function listAdminTeams(
  signal?: AbortSignal,
): Promise<AdminTeam[]> {
  const response = await requestJson("/api/admin/teams", adminTeamListSchema, {
    cache: "no-store",
    signal,
  });
  return response.teams;
}

export async function createAdminTeam(
  name: string,
  signal?: AbortSignal,
): Promise<string> {
  const response = await requestJson(
    "/api/admin/teams",
    adminTeamCreatedSchema,
    jsonRequest({ name }, { method: "POST", signal }),
  );
  return response.team_id;
}

export async function listAdminGrants(
  signal?: AbortSignal,
): Promise<AdminGrant[]> {
  const response = await requestJson("/api/admin/grants", adminGrantListSchema, {
    cache: "no-store",
    signal,
  });
  return response.grants;
}

export async function getAdminAccessContext(
  nodeId: string,
  signal?: AbortSignal,
): Promise<AdminAccessContext> {
  return requestJson(
    `/api/admin/access?node_id=${requireUuid(nodeId, "library node")}`,
    adminAccessContextSchema,
    { cache: "no-store", signal },
  );
}

export async function listAdminAudit(
  limit = 100,
  signal?: AbortSignal,
): Promise<AdminAuditEvent[]> {
  const response = await requestJson(
    `/api/admin/audit?limit=${limit}`,
    adminAuditListSchema,
    { cache: "no-store", signal },
  );
  return response.events;
}

export async function previewAcl(
  operation: AclOperation,
  signal?: AbortSignal,
): Promise<AclPreview> {
  const validOperation = aclOperationSchema.parse(operation);
  return requestJson(
    "/api/admin/acl/preview",
    aclPreviewSchema,
    jsonRequest({ operation: validOperation }, { method: "POST", signal }),
  );
}

export async function applyAcl(
  preview: AclPreview,
  signal?: AbortSignal,
): Promise<number> {
  const response = await requestJson(
    "/api/admin/acl/apply",
    aclApplySchema,
    jsonRequest(
      {
        preview_id: preview.preview_id,
        impact_digest: preview.impact_digest,
      },
      { method: "POST", signal },
    ),
  );
  return response.authorization_version;
}
