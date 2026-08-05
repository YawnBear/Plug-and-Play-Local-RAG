import { expect, test, type Page } from "@playwright/test";

import {
  API_ROUTE,
  DOCUMENT,
  DOCUMENT_NODE,
  FOLDER_ALPHA,
  FOLDER_BETA,
  TIMESTAMP,
  fulfillAuthMeIfRequested,
  fulfillEmpty,
  fulfillJson,
} from "./mock-api";

const FOLDER_NESTED = "cccccccc-cccc-4ccc-8ccc-cccccccccccc";
const JOB = "ffffffff-ffff-4fff-8fff-ffffffffffff";

interface FolderRecord {
  id: string;
  parentId: string | null;
  name: string;
}

interface BrowseChild {
  node_id: string;
  parent_id: string | null;
  kind: "folder" | "file";
  name: string;
  logical_path: string;
  document_id: string | null;
  uploader_user_id: string | null;
  can_manage: boolean;
  can_create_children: boolean;
  readable_document_count: number;
}

async function mockMutableKnowledgeBase(page: Page) {
  const folders = new Map<string, FolderRecord>([
    [
      FOLDER_ALPHA,
      { id: FOLDER_ALPHA, parentId: null, name: "Alpha" },
    ],
    [
      FOLDER_BETA,
      { id: FOLDER_BETA, parentId: null, name: "Beta" },
    ],
  ]);
  let document:
    | {
        document_id: string;
        filename: string;
        sha256: string;
        state: string;
        page_count: number | null;
        chunk_count: number;
        created_at: string;
        updated_at: string;
        error: string | null;
        node_id: string;
        parent_id: string | null;
        display_name: string;
        logical_path: string;
        uploader_user_id: string;
        can_manage: boolean;
        team_ids: string[];
      }
    | undefined;
  let jobCalls = 0;

  function logicalPath(folder: FolderRecord): string {
    const parent = folder.parentId ? folders.get(folder.parentId) : undefined;
    return `${parent ? logicalPath(parent) : ""}/${folder.name}`;
  }

  function folderNode(folder: FolderRecord) {
    return {
      node_id: folder.id,
      parent_id: folder.parentId,
      kind: "folder" as const,
      name: folder.name,
      logical_path: logicalPath(folder),
      document_id: null,
      uploader_user_id: null,
      can_manage: true,
      can_create_children: true,
      readable_document_count: 0,
    };
  }

  function treeNodes(parentId: string | null): unknown[] {
    return [...folders.values()]
      .filter((folder) => folder.parentId === parentId)
      .map((folder) => ({
        node_id: folder.id,
        parent_id: folder.parentId,
        name: folder.name,
        logical_path: logicalPath(folder),
        children: treeNodes(folder.id),
      }));
  }

  function breadcrumbs(folderId: string | null) {
    if (!folderId) return [];
    const chain: FolderRecord[] = [];
    let current = folders.get(folderId);
    while (current) {
      chain.unshift(current);
      current = current.parentId ? folders.get(current.parentId) : undefined;
    }
    return chain.map(folderNode);
  }

  function browse(folderId: string | null) {
    const children: BrowseChild[] = [...folders.values()]
      .filter((folder) => folder.parentId === folderId)
      .map(folderNode);
    if (document && document.parent_id === folderId) {
      children.push({
        node_id: document.node_id,
        parent_id: document.parent_id,
        kind: "file",
        name: document.display_name,
        logical_path: document.logical_path,
        document_id: document.document_id,
        uploader_user_id: document.uploader_user_id,
        can_manage: document.can_manage,
        can_create_children: false,
        readable_document_count: 1,
      });
    }
    return {
      parent_id: folderId,
      breadcrumbs: breadcrumbs(folderId),
      children,
      page: 1,
      limit: 100,
      total: children.length,
    };
  }

  function moveDocument(parentId: string | null) {
    if (!document) return;
    document.parent_id = parentId;
    const parent = parentId ? folders.get(parentId) : undefined;
    document.logical_path = `${parent ? logicalPath(parent) : ""}/${document.display_name}`;
    document.updated_at = "2026-07-23T00:01:00Z";
  }

  await page.route(API_ROUTE, async (route) => {
    if (await fulfillAuthMeIfRequested(route)) return;
    const request = route.request();
    const url = new URL(request.url());
    const method = request.method();

    if (method === "GET" && url.pathname === "/api/account/teams") {
      await fulfillJson(route, { teams: [], requires_team_selection: false });
      return;
    }
    if (method === "GET" && url.pathname === "/api/library/tree") {
      await fulfillJson(route, treeNodes(null));
      return;
    }
    if (method === "GET" && url.pathname === "/api/library/browse") {
      await fulfillJson(route, browse(url.searchParams.get("parent_id")));
      return;
    }
    if (method === "GET" && url.pathname === "/api/documents") {
      await fulfillJson(route, document ? [document] : []);
      return;
    }
    if (method === "POST" && url.pathname === "/api/library/folders") {
      const body = request.postDataJSON() as {
        name: string;
        parent_id: string | null;
      };
      const folder = {
        id: FOLDER_NESTED,
        parentId: body.parent_id,
        name: body.name,
      };
      folders.set(folder.id, folder);
      await fulfillJson(route, folderNode(folder));
      return;
    }
    const nodeMatch = url.pathname.match(
      /^\/api\/library\/nodes\/([0-9a-f-]+)$/,
    );
    const movePreviewMatch = url.pathname.match(
      /^\/api\/library\/nodes\/([0-9a-f-]+)\/move-preview$/,
    );
    if (method === "POST" && movePreviewMatch) {
      await fulfillJson(route, {
        preview_id: "77777777-7777-4777-8777-777777777777",
        impact_digest: "a".repeat(64),
        impact: {
          user_ids: [],
          node_ids: [movePreviewMatch[1]],
          document_ids: [],
          user_count: 0,
          node_count: 1,
          document_count: 0,
        },
      });
      return;
    }
    if (method === "PATCH" && nodeMatch) {
      const body = request.postDataJSON() as {
        name?: string;
        parent_id?: string | null;
      };
      const folder = folders.get(nodeMatch[1]);
      if (folder) {
        if (body.name !== undefined) folder.name = body.name;
        if ("parent_id" in body) folder.parentId = body.parent_id ?? null;
        await fulfillJson(route, folderNode(folder));
        return;
      }
      if (document?.node_id === nodeMatch[1]) {
        if (body.name !== undefined) document.display_name = body.name;
        if ("parent_id" in body) moveDocument(body.parent_id ?? null);
        await fulfillJson(route, {
          node_id: document.node_id,
          parent_id: document.parent_id,
          kind: "file",
          name: document.display_name,
          logical_path: document.logical_path,
          document_id: document.document_id,
          uploader_user_id: document.uploader_user_id,
          can_manage: document.can_manage,
          can_create_children: false,
          readable_document_count: 1,
        });
        return;
      }
    }
    if (method === "POST" && url.pathname === "/api/documents") {
      const parent = folders.get(FOLDER_NESTED)!;
      document = {
        document_id: DOCUMENT,
        filename: "Evidence.pdf",
        sha256: "a".repeat(64),
        state: "processing",
        page_count: null,
        chunk_count: 0,
        created_at: TIMESTAMP,
        updated_at: TIMESTAMP,
        error: null,
        node_id: DOCUMENT_NODE,
        parent_id: FOLDER_NESTED,
        display_name: "Evidence.pdf",
        logical_path: `${logicalPath(parent)}/Evidence.pdf`,
        uploader_user_id: "99999999-9999-4999-8999-999999999999",
        can_manage: true,
        team_ids: [],
      };
      await fulfillJson(route, {
        document_id: DOCUMENT,
        job_id: JOB,
        status: "queued",
        duplicate_of: DOCUMENT,
        node_id: DOCUMENT_NODE,
        parent_id: FOLDER_NESTED,
        display_name: document.display_name,
        logical_path: document.logical_path,
        location_reused: true,
      });
      return;
    }
    if (method === "GET" && url.pathname === `/api/jobs/${JOB}`) {
      jobCalls += 1;
      const completed = jobCalls > 1;
      if (completed && document) {
        document.state = "ready";
        document.page_count = 4;
        document.chunk_count = 8;
      }
      await fulfillJson(route, {
        job_id: JOB,
        document_id: DOCUMENT,
        status: completed ? "completed" : "running",
        stage: completed ? "complete" : "embedding",
        completed_units: completed ? 5 : 1,
        total_units: 5,
        error: null,
      });
      return;
    }
    if (
      method === "HEAD" &&
      url.pathname === `/api/documents/${DOCUMENT}/content`
    ) {
      await fulfillEmpty(route, 200);
      return;
    }
    if (
      method === "DELETE" &&
      url.pathname === `/api/documents/${DOCUMENT}`
    ) {
      document = undefined;
      await fulfillEmpty(route);
      return;
    }
    if (
      method === "DELETE" &&
      url.pathname === `/api/library/folders/${FOLDER_NESTED}`
    ) {
      folders.delete(FOLDER_NESTED);
      await fulfillEmpty(route);
      return;
    }
    await fulfillJson(route, { detail: "Unexpected deterministic request." }, 500);
  });

  return {
    get document() {
      return document;
    },
    folders,
    get jobCalls() {
      return jobCalls;
    },
  };
}

test("creates and moves a folder, polls a duplicate upload, then moves and deletes it", async ({
  page,
}) => {
  const mock = await mockMutableKnowledgeBase(page);
  await page.goto("/knowledge-base");

  await page.getByRole("button", { name: "New folder" }).click();
  const createFolder = page.getByRole("dialog", { name: "Create folder" });
  const createFolderOverflow = await createFolder.evaluate((element) => ({
    clientWidth: element.clientWidth,
    scrollWidth: element.scrollWidth,
  }));
  expect(createFolderOverflow.scrollWidth).toBeLessThanOrEqual(
    createFolderOverflow.clientWidth,
  );
  await page.getByLabel("Folder name").fill("Nested");
  await page.getByRole("button", { name: "Save" }).click();
  await page.getByRole("button", { name: "Nested", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Nested" })).toBeVisible();

  await page.getByRole("button", { name: "Actions for Nested" }).click();
  await page.getByRole("menuitem", { name: "Rename" }).click();
  await page.getByLabel("Folder name").fill("Renamed");
  await page.getByRole("button", { name: "Save" }).click();
  await expect(page.getByRole("heading", { name: "Renamed" })).toBeVisible();

  await page.getByRole("button", { name: "Actions for Renamed" }).click();
  await page.getByRole("menuitem", { name: "Move" }).click();
  const folderMove = page.getByRole("dialog", { name: "Move folder" });
  await folderMove.getByLabel("Destination").selectOption(FOLDER_BETA);
  await folderMove.getByRole("button", { name: "Review move" }).click();
  await folderMove.getByLabel(/Type Renamed to confirm/).fill("Renamed");
  await folderMove.getByRole("button", { name: "Save" }).click();
  await expect(
    page
      .getByRole("complementary", { name: "Knowledge Base sidebar" })
      .getByRole("button", { name: "Beta", exact: true }),
  ).toBeVisible();
  await expect(page.getByRole("heading", { name: "Renamed" })).toBeVisible();

  await page
    .getByRole("complementary", { name: "Knowledge Base sidebar" })
    .getByRole("button", { name: "Upload PDF" })
    .click();
  await page.getByLabel("PDF file").setInputFiles({
    name: "Evidence.pdf",
    mimeType: "application/pdf",
    buffer: Buffer.from("%PDF-1.4\n%%EOF"),
  });
  await page.getByRole("button", { name: "Upload here" }).click();
  await expect(
    page
      .getByRole("region", { name: "Knowledge Base editor" })
      .getByText(/existing authorized file was reused/i),
  ).toBeVisible();
  await expect
    .poll(() => mock.jobCalls, { timeout: 5_000 })
    .toBeGreaterThanOrEqual(1);
  await expect(page.getByText("Document processing completed.")).toBeVisible({
    timeout: 10_000,
  });
  expect(mock.jobCalls).toBeGreaterThanOrEqual(2);

  const details = page.getByRole("complementary", { name: "Evidence.pdf" });
  await details.getByRole("button", { name: "Move" }).click();
  const documentMove = page.getByRole("dialog", { name: "Move document" });
  await documentMove.getByLabel("Destination").selectOption(FOLDER_ALPHA);
  await documentMove.getByRole("button", { name: "Review move" }).click();
  await documentMove.getByLabel(/Type Evidence\.pdf to confirm/).fill("Evidence.pdf");
  await documentMove.getByRole("button", { name: "Save" }).click();
  await expect(page).toHaveURL(new RegExp(`folder=${FOLDER_ALPHA}`));
  expect(mock.document?.parent_id).toBe(FOLDER_ALPHA);

  await details.getByRole("button", { name: "Delete" }).click();
  const documentDelete = page.getByRole("dialog", { name: "Delete document?" });
  await documentDelete.getByLabel(/Type Evidence\.pdf to confirm/).fill("Evidence.pdf");
  await page
    .getByRole("button", { name: "Delete document", exact: true })
    .click();
  await expect(details).toBeHidden();
  expect(mock.document).toBeUndefined();

  await page.goto(`/knowledge-base?folder=${FOLDER_NESTED}`);
  await page.getByRole("button", { name: "Actions for Renamed" }).click();
  await page.getByRole("menuitem", { name: "Delete" }).click();
  const folderDelete = page.getByRole("dialog", { name: "Delete empty folder?" });
  await folderDelete.getByLabel(/Type Renamed to confirm/).fill("Renamed");
  await page
    .getByRole("button", { name: "Delete folder", exact: true })
    .click();
  await expect(page).toHaveURL(new RegExp(`folder=${FOLDER_BETA}`));
  expect(mock.folders.has(FOLDER_NESTED)).toBe(false);
});
