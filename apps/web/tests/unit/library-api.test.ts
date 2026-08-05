import { afterEach, describe, expect, it, vi } from "vitest";

import {
  browseLibrary,
  createFolder,
  documentContentUrl,
  headDocumentContent,
  previewNodeMove,
  reingestDocument,
  updateNode,
  uploadDocument,
} from "@/features/library/api";

const FOLDER = "11111111-1111-4111-8111-111111111111";
const NODE = "22222222-2222-4222-8222-222222222222";
const DOCUMENT = "33333333-3333-4333-8333-333333333333";
const JOB = "44444444-4444-4444-8444-444444444444";
const TEAM = "55555555-5555-4555-8555-555555555555";
const libraryNode = {
  node_id: NODE,
  parent_id: FOLDER,
  kind: "file",
  name: "Paper.pdf",
  logical_path: "/Folder/Paper.pdf",
  document_id: DOCUMENT,
  uploader_user_id: "99999999-9999-4999-8999-999999999999",
  can_manage: true,
  can_create_children: false,
  readable_document_count: 1,
};

afterEach(() => vi.unstubAllGlobals());

describe("library API", () => {
  it("browses roots and folders using the authoritative parent query", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          parent_id: FOLDER,
          breadcrumbs: [],
          children: [libraryNode],
          page: 1,
          limit: 100,
          total: 1,
        }),
        { status: 200 },
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    await browseLibrary(FOLDER);
    expect(fetchMock.mock.calls[0][0]).toBe(
      `/api/library/browse?page=1&limit=100&parent_id=${FOLDER}`,
    );
  });

  it("preserves explicit null moves and creates folders", async () => {
    const folder = {
      ...libraryNode,
      document_id: null,
      uploader_user_id: null,
      kind: "folder",
      name: "Folder",
    };
    const fetchMock = vi
      .fn()
      .mockImplementation(async () =>
        new Response(JSON.stringify(folder), { status: 200 }),
      );
    vi.stubGlobal("fetch", fetchMock);

    await updateNode(NODE, { parent_id: null });
    expect((fetchMock.mock.calls[0][1] as RequestInit).body).toBe(
      '{"parent_id":null}',
    );
    await createFolder("Folder", null);
    expect((fetchMock.mock.calls[1][1] as RequestInit).body).toBe(
      '{"name":"Folder","parent_id":null}',
    );
  });

  it("uploads folder-aware multipart data and parses duplicate-location fields", async () => {
    const accepted = {
      document_id: DOCUMENT,
      job_id: JOB,
      status: "queued",
      duplicate_of: DOCUMENT,
      node_id: NODE,
      parent_id: FOLDER,
      display_name: "Paper.pdf",
      logical_path: "/Folder/Paper.pdf",
      location_reused: true,
    };
    const fetchMock = vi
      .fn()
      .mockResolvedValue(
        new Response(JSON.stringify(accepted), { status: 202 }),
      );
    vi.stubGlobal("fetch", fetchMock);

    await expect(
      uploadDocument(new File(["pdf"], "Paper.pdf"), FOLDER, [TEAM]),
    ).resolves.toEqual(accepted);
    const body = (fetchMock.mock.calls[0][1] as RequestInit).body as FormData;
    expect(body.get("folder_id")).toBe(FOLDER);
    expect(body.getAll("team_ids")).toEqual([TEAM]);
    expect(body.get("file")).toBeInstanceOf(File);
  });

  it("reingests a document with an empty POST body and strict queued response", async () => {
    const accepted = {
      document_id: DOCUMENT,
      job_id: JOB,
      status: "queued",
    };
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify(accepted), { status: 202 }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await expect(reingestDocument(DOCUMENT)).resolves.toEqual(accepted);
    expect(fetchMock.mock.calls[0][0]).toBe(
      `/api/documents/${DOCUMENT}/reingest`,
    );
    expect(fetchMock.mock.calls[0][1]).toEqual(
      expect.objectContaining({ method: "POST" }),
    );
    expect(fetchMock.mock.calls[0][1]).not.toHaveProperty("body");
  });

  it("requests an authoritative move preview before binding an apply", async () => {
    const preview = {
      preview_id: "66666666-6666-4666-8666-666666666666",
      impact_digest: "b".repeat(64),
      impact: {
        user_ids: [],
        node_ids: [NODE],
        document_ids: [DOCUMENT],
        user_count: 0,
        node_count: 1,
        document_count: 1,
      },
    };
    const fetchMock = vi
      .fn()
      .mockResolvedValue(new Response(JSON.stringify(preview), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(previewNodeMove(NODE, FOLDER)).resolves.toEqual(preview);
    expect(fetchMock.mock.calls[0][0]).toBe(
      `/api/library/nodes/${NODE}/move-preview`,
    );
    expect((fetchMock.mock.calls[0][1] as RequestInit).body).toBe(
      JSON.stringify({ parent_id: FOLDER }),
    );
  });

  it("builds content URLs and supports HEAD metadata checks", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(
        new Response(null, {
          status: 200,
          headers: { "accept-ranges": "bytes" },
        }),
      );
    vi.stubGlobal("fetch", fetchMock);

    expect(documentContentUrl(DOCUMENT)).toBe(
      `/api/documents/${DOCUMENT}/content`,
    );
    await expect(headDocumentContent(DOCUMENT)).resolves.toBeInstanceOf(
      Response,
    );
    expect(fetchMock.mock.calls[0][1]).toEqual(
      expect.objectContaining({ method: "HEAD", cache: "no-store" }),
    );
  });
});
