import {
  apiFetch,
  apiUrl,
  jsonRequest,
  requestJson,
  requestVoid,
} from "@/lib/http";
import { requireUuid } from "@/lib/uuid";

import {
  documentListSchema,
  documentUploadAcceptedSchema,
  documentReingestAcceptedSchema,
  accountTeamsSchema,
  jobStatusSchema,
  libraryBrowseSchema,
  libraryNodeSchema,
  libraryTreeSchema,
  nodeMovePreviewSchema,
  type DocumentSummary,
  type DocumentUploadAccepted,
  type DocumentReingestAccepted,
  type JobStatus,
  type LibraryBrowse,
  type LibraryNode,
  type LibraryTreeNode,
  type AccountTeams,
  type NodeMovePreview,
} from "./contracts";

export async function getAccountTeams(
  signal?: AbortSignal,
): Promise<AccountTeams> {
  return requestJson("/api/account/teams", accountTeamsSchema, {
    cache: "no-store",
    signal,
  });
}

export async function getLibraryTree(
  signal?: AbortSignal,
): Promise<LibraryTreeNode[]> {
  return requestJson("/api/library/tree", libraryTreeSchema, {
    cache: "no-store",
    signal,
  });
}

export async function browseLibrary(
  parentId?: string | null,
  signal?: AbortSignal,
  page = 1,
  limit = 100,
): Promise<LibraryBrowse> {
  const query = new URLSearchParams({
    page: String(page),
    limit: String(limit),
  });
  if (parentId) {
    query.set("parent_id", requireUuid(parentId, "parent folder"));
  }
  return requestJson(`/api/library/browse?${query}`, libraryBrowseSchema, {
    cache: "no-store",
    signal,
  });
}

export async function createFolder(
  name: string,
  parentId?: string | null,
  signal?: AbortSignal,
): Promise<LibraryNode> {
  return requestJson(
    "/api/library/folders",
    libraryNodeSchema,
    jsonRequest(
      { name, parent_id: parentId ? requireUuid(parentId, "parent folder") : null },
      { method: "POST", signal },
    ),
  );
}

export async function updateNode(
  nodeId: string,
  patch: {
    name?: string;
    parent_id?: string | null;
    preview_id?: string;
    impact_digest?: string;
  },
  signal?: AbortSignal,
): Promise<LibraryNode> {
  const body: {
    name?: string;
    parent_id?: string | null;
    preview_id?: string;
    impact_digest?: string;
  } = {};
  if ("name" in patch) body.name = patch.name;
  if ("parent_id" in patch) {
    body.parent_id = patch.parent_id
      ? requireUuid(patch.parent_id, "parent folder")
      : null;
  }
  if (patch.preview_id) {
    body.preview_id = requireUuid(patch.preview_id, "move preview");
  }
  if (patch.impact_digest) body.impact_digest = patch.impact_digest;
  return requestJson(
    `/api/library/nodes/${requireUuid(nodeId, "library node")}`,
    libraryNodeSchema,
    jsonRequest(body, { method: "PATCH", signal }),
  );
}

export async function previewNodeMove(
  nodeId: string,
  parentId: string | null,
  signal?: AbortSignal,
): Promise<NodeMovePreview> {
  return requestJson(
    `/api/library/nodes/${requireUuid(nodeId, "library node")}/move-preview`,
    nodeMovePreviewSchema,
    jsonRequest(
      {
        parent_id: parentId ? requireUuid(parentId, "parent folder") : null,
      },
      { method: "POST", signal },
    ),
  );
}

export async function deleteFolder(
  folderId: string,
  signal?: AbortSignal,
): Promise<void> {
  return requestVoid(
    `/api/library/folders/${requireUuid(folderId, "folder")}`,
    { method: "DELETE", signal },
  );
}

export async function listDocuments(
  signal?: AbortSignal,
): Promise<DocumentSummary[]> {
  return requestJson("/api/documents", documentListSchema, {
    cache: "no-store",
    signal,
  });
}

export async function uploadDocument(
  file: File,
  folderId?: string | null,
  teamIds: readonly string[] = [],
  signal?: AbortSignal,
): Promise<DocumentUploadAccepted> {
  const body = new FormData();
  body.append("file", file);
  if (folderId) body.append("folder_id", requireUuid(folderId, "folder"));
  for (const teamId of teamIds) {
    body.append("team_ids", requireUuid(teamId, "team"));
  }
  return requestJson("/api/documents", documentUploadAcceptedSchema, {
    method: "POST",
    body,
    signal,
  });
}

export async function reingestDocument(
  documentId: string,
  signal?: AbortSignal,
): Promise<DocumentReingestAccepted> {
  return requestJson(
    `/api/documents/${requireUuid(documentId, "document")}/reingest`,
    documentReingestAcceptedSchema,
    { method: "POST", signal },
  );
}

export async function getJob(
  jobId: string,
  signal?: AbortSignal,
): Promise<JobStatus> {
  return requestJson(
    `/api/jobs/${requireUuid(jobId, "job")}`,
    jobStatusSchema,
    { cache: "no-store", signal },
  );
}

export async function deleteDocument(
  documentId: string,
  signal?: AbortSignal,
): Promise<void> {
  return requestVoid(`/api/documents/${requireUuid(documentId, "document")}`, {
    method: "DELETE",
    signal,
  });
}

export function documentContentUrl(documentId: string): string {
  return apiUrl(
    `/api/documents/${requireUuid(documentId, "document")}/content`,
  );
}

export async function headDocumentContent(
  documentId: string,
  signal?: AbortSignal,
): Promise<Response> {
  return apiFetch(
    `/api/documents/${requireUuid(documentId, "document")}/content`,
    { method: "HEAD", cache: "no-store", signal },
  );
}
