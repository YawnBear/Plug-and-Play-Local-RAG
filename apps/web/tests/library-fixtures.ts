import type {
  DocumentSummary,
  DocumentUploadAccepted,
  JobStatus,
  LibraryBrowse,
  LibraryNode,
  LibraryTreeNode,
} from "@/features/library/contracts";

export const FOLDER_A = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
export const FOLDER_B = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";
export const FOLDER_C = "cccccccc-cccc-4ccc-8ccc-cccccccccccc";
export const DOCUMENT = "dddddddd-dddd-4ddd-8ddd-dddddddddddd";
export const NODE = "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee";
export const JOB = "ffffffff-ffff-4fff-8fff-ffffffffffff";
export const UPLOADER = "99999999-9999-4999-8999-999999999999";
export const timestamp = "2026-07-23T00:00:00Z";

export const tree: LibraryTreeNode[] = [
  {
    node_id: FOLDER_A,
    parent_id: null,
    name: "Alpha",
    logical_path: "/Alpha",
    children: [
      {
        node_id: FOLDER_C,
        parent_id: FOLDER_A,
        name: "Child",
        logical_path: "/Alpha/Child",
        children: [],
      },
    ],
  },
  {
    node_id: FOLDER_B,
    parent_id: null,
    name: "Beta",
    logical_path: "/Beta",
    children: [],
  },
];

export function folderNode(
  id = FOLDER_A,
  parentId: string | null = null,
  name = "Alpha",
): LibraryNode {
  return {
    node_id: id,
    parent_id: parentId,
    kind: "folder",
    name,
    logical_path: parentId ? `/Alpha/${name}` : `/${name}`,
    document_id: null,
    uploader_user_id: null,
    can_manage: true,
    can_create_children: true,
    readable_document_count: 0,
  };
}

export function browse(
  parentId: string | null = FOLDER_A,
): LibraryBrowse {
  if (parentId === null) {
    return {
      parent_id: null,
      breadcrumbs: [],
      children: [folderNode(FOLDER_A), folderNode(FOLDER_B, null, "Beta")],
      page: 1,
      limit: 100,
      total: 2,
    };
  }
  const folder =
    parentId === FOLDER_B
      ? folderNode(FOLDER_B, null, "Beta")
      : folderNode(FOLDER_A);
  return {
    parent_id: parentId,
    breadcrumbs: [folder],
    children:
      parentId === FOLDER_B
        ? [
            {
              node_id: NODE,
              parent_id: FOLDER_B,
              kind: "file",
              name: "Evidence.pdf",
              logical_path: "/Beta/Evidence.pdf",
              document_id: DOCUMENT,
              uploader_user_id: UPLOADER,
              can_manage: true,
              can_create_children: false,
              readable_document_count: 1,
            },
          ]
        : [],
    page: 1,
    limit: 100,
    total: parentId === FOLDER_B ? 1 : 0,
  };
}

export function documentSummary(
  overrides: Partial<DocumentSummary> = {},
): DocumentSummary {
  return {
    document_id: DOCUMENT,
    filename: "original.pdf",
    sha256: "a".repeat(64),
    state: "ready",
    page_count: 5,
    chunk_count: 12,
    created_at: timestamp,
    updated_at: timestamp,
    error: null,
    node_id: NODE,
    parent_id: FOLDER_B,
    display_name: "Evidence.pdf",
    logical_path: "/Beta/Evidence.pdf",
    uploader_user_id: UPLOADER,
    can_manage: true,
    team_ids: [],
    ...overrides,
  };
}

export function uploadAccepted(
  overrides: Partial<DocumentUploadAccepted> = {},
): DocumentUploadAccepted {
  return {
    document_id: DOCUMENT,
    job_id: JOB,
    status: "completed",
    duplicate_of: null,
    node_id: NODE,
    parent_id: FOLDER_B,
    display_name: "Evidence.pdf",
    logical_path: "/Beta/Evidence.pdf",
    location_reused: false,
    ...overrides,
  };
}

export function jobStatus(
  overrides: Partial<JobStatus> = {},
): JobStatus {
  return {
    job_id: JOB,
    document_id: DOCUMENT,
    status: "running",
    stage: "embedding",
    completed_units: 1,
    total_units: 5,
    error: null,
    ...overrides,
  };
}
