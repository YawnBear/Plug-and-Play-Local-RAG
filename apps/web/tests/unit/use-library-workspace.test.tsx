import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@/lib/http";
import type { KnowledgeBaseRouteState } from "@/lib/route-state";

const navigation = vi.hoisted(() => ({
  push: vi.fn(),
  replace: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => navigation,
}));

vi.mock("@/features/library/api", () => ({
  getLibraryTree: vi.fn(),
  browseLibrary: vi.fn(),
  listDocuments: vi.fn(),
  createFolder: vi.fn(),
  updateNode: vi.fn(),
  previewNodeMove: vi.fn(),
  deleteFolder: vi.fn(),
  deleteDocument: vi.fn(),
  uploadDocument: vi.fn(),
  reingestDocument: vi.fn(),
  getJob: vi.fn(),
  documentContentUrl: vi.fn(),
  headDocumentContent: vi.fn(),
}));

import {
  browseLibrary,
  createFolder,
  deleteDocument,
  deleteFolder,
  getJob,
  getLibraryTree,
  listDocuments,
  updateNode,
  uploadDocument,
  reingestDocument,
} from "@/features/library/api";
import { useLibraryWorkspace } from "@/features/library/use-library-workspace";

import {
  DOCUMENT,
  FOLDER_A,
  FOLDER_B,
  FOLDER_C,
  JOB,
  NODE,
  browse,
  documentSummary,
  folderNode,
  tree,
  jobStatus,
  uploadAccepted,
} from "../library-fixtures";

function route(
  overrides: Partial<KnowledgeBaseRouteState> = {},
): KnowledgeBaseRouteState {
  return {
    folderId: FOLDER_A,
    documentId: null,
    page: null,
    invalidFolder: false,
    invalidDocument: false,
    invalidPage: false,
    ...overrides,
  };
}

describe("useLibraryWorkspace", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    window.history.replaceState(
      null,
      "",
      `/knowledge-base?folder=${FOLDER_A}`,
    );
    vi.mocked(getLibraryTree).mockResolvedValue(tree);
    vi.mocked(listDocuments).mockResolvedValue([documentSummary()]);
    vi.mocked(browseLibrary).mockImplementation(async (parentId) =>
      browse(parentId ?? null),
    );
  });

  it("reconciles a moved document to its live parent and clamps its page", async () => {
    window.history.replaceState(
      null,
      "",
      `/knowledge-base?folder=${FOLDER_A}&document=${DOCUMENT}&page=99`,
    );
    const hook = renderHook(() =>
      useLibraryWorkspace(
        route({ documentId: DOCUMENT, page: 99 }),
      ),
    );
    await waitFor(() =>
      expect(hook.result.current.state.selection).toEqual({
        folderId: FOLDER_B,
        documentId: DOCUMENT,
        page: 5,
      }),
    );
    expect(browseLibrary).toHaveBeenCalledWith(
      FOLDER_B,
      expect.any(AbortSignal),
    );
    expect(navigation.replace).toHaveBeenCalledWith(
      `/knowledge-base?folder=${FOLDER_B}&document=${DOCUMENT}&page=5`,
    );
  });

  it("recovers missing folders to root and missing documents within a valid folder", async () => {
    vi.mocked(listDocuments).mockResolvedValue([]);
    vi.mocked(browseLibrary).mockImplementation(async (parentId) => {
      if (parentId === FOLDER_C) throw new ApiError("folder not found", 404);
      return browse(parentId ?? null);
    });
    window.history.replaceState(
      null,
      "",
      `/knowledge-base?folder=${FOLDER_C}`,
    );
    const missingFolder = renderHook(() =>
      useLibraryWorkspace(route({ folderId: FOLDER_C })),
    );
    await waitFor(() =>
      expect(missingFolder.result.current.state.selection.folderId).toBeNull(),
    );
    expect(missingFolder.result.current.state.notice).toMatch(/no longer exists/i);
    expect(navigation.replace).toHaveBeenCalledWith("/knowledge-base");
    missingFolder.unmount();

    vi.clearAllMocks();
    vi.mocked(getLibraryTree).mockResolvedValue(tree);
    vi.mocked(listDocuments).mockResolvedValue([]);
    vi.mocked(browseLibrary).mockResolvedValue(browse(FOLDER_A));
    window.history.replaceState(
      null,
      "",
      `/knowledge-base?folder=${FOLDER_A}&document=${DOCUMENT}&page=3`,
    );
    const missingDocument = renderHook(() =>
      useLibraryWorkspace(route({ documentId: DOCUMENT, page: 3 })),
    );
    await waitFor(() =>
      expect(missingDocument.result.current.state.selection).toEqual({
        folderId: FOLDER_A,
        documentId: null,
        page: null,
      }),
    );
    expect(missingDocument.result.current.state.notice).toMatch(
      /document no longer exists/i,
    );
    expect(navigation.replace).toHaveBeenCalledWith(
      `/knowledge-base?folder=${FOLDER_A}`,
    );
  });

  it("does not replace an already canonical full URL and restores back-route props", async () => {
    const hook = renderHook(
      ({ value }: { value: KnowledgeBaseRouteState }) =>
        useLibraryWorkspace(value),
      { initialProps: { value: route() } },
    );
    await waitFor(() => expect(hook.result.current.state.loading).toBe(false));
    expect(navigation.replace).not.toHaveBeenCalled();

    window.history.replaceState(
      null,
      "",
      `/knowledge-base?folder=${FOLDER_B}`,
    );
    hook.rerender({ value: route({ folderId: FOLDER_B }) });
    await waitFor(() =>
      expect(hook.result.current.state.selection.folderId).toBe(FOLDER_B),
    );
    expect(hook.result.current.state.browse?.parent_id).toBe(FOLDER_B);
  });

  it("retains stale tree, browse, and documents during an outage with retry errors", async () => {
    const hook = renderHook(() => useLibraryWorkspace(route()));
    await waitFor(() => expect(hook.result.current.state.loading).toBe(false));
    vi.mocked(getLibraryTree).mockRejectedValue(new ApiError("tree offline"));
    vi.mocked(listDocuments).mockRejectedValue(
      new ApiError("documents offline"),
    );
    vi.mocked(browseLibrary).mockRejectedValue(new ApiError("browse offline"));
    await act(() => hook.result.current.refreshCurrent());

    expect(hook.result.current.state.tree).toEqual(tree);
    expect(hook.result.current.state.browse).toEqual(browse(FOLDER_A));
    expect(hook.result.current.state.documents).toEqual([documentSummary()]);
    expect(hook.result.current.state.browseError).toBe("browse offline");
  });

  it("never exposes stale folder actions after a cross-folder browse failure", async () => {
    const hook = renderHook(
      ({ value }: { value: KnowledgeBaseRouteState }) =>
        useLibraryWorkspace(value),
      { initialProps: { value: route() } },
    );
    await waitFor(() => expect(hook.result.current.state.loading).toBe(false));

    vi.mocked(browseLibrary).mockImplementation(async (parentId) => {
      if (parentId === FOLDER_B) throw new ApiError("browse offline");
      return browse(parentId ?? null);
    });
    window.history.replaceState(
      null,
      "",
      `/knowledge-base?folder=${FOLDER_B}`,
    );
    hook.rerender({ value: route({ folderId: FOLDER_B }) });

    await waitFor(() =>
      expect(hook.result.current.state.browseError).toBe("browse offline"),
    );
    expect(hook.result.current.state.selection.folderId).toBe(FOLDER_B);
    expect(hook.result.current.state.browse).toBeNull();
    expect(hook.result.current.currentFolder).toBeNull();
  });

  it("creates in the current folder and refetches after a 409 without optimistic structure", async () => {
    vi.mocked(createFolder).mockResolvedValue(
      folderNode(FOLDER_C, FOLDER_A, "Created"),
    );
    const hook = renderHook(() => useLibraryWorkspace(route()));
    await waitFor(() => expect(hook.result.current.state.loading).toBe(false));
    await act(() => hook.result.current.addFolder("Created"));
    expect(createFolder).toHaveBeenCalledWith(
      "Created",
      FOLDER_A,
      expect.any(AbortSignal),
    );

    vi.mocked(updateNode).mockRejectedValue(
      new ApiError("Name already exists.", 409),
    );
    const treeCalls = vi.mocked(getLibraryTree).mock.calls.length;
    await expect(
      hook.result.current.patchNode(FOLDER_A, { name: "Conflict" }),
    ).rejects.toMatchObject({ status: 409 });
    expect(getLibraryTree).toHaveBeenCalledTimes(treeCalls + 1);
    expect(hook.result.current.state.tree).toEqual(tree);
  });

  it("clears a deleted selected document while retaining its folder", async () => {
    window.history.replaceState(
      null,
      "",
      `/knowledge-base?folder=${FOLDER_B}&document=${DOCUMENT}&page=2`,
    );
    vi.mocked(deleteDocument).mockResolvedValue(undefined);
    vi.mocked(listDocuments)
      .mockResolvedValueOnce([documentSummary()])
      .mockResolvedValue([]);
    const hook = renderHook(() =>
      useLibraryWorkspace(
        route({ folderId: FOLDER_B, documentId: DOCUMENT, page: 2 }),
      ),
    );
    await waitFor(() =>
      expect(hook.result.current.selectedDocument?.document_id).toBe(DOCUMENT),
    );
    await act(() =>
      hook.result.current.removeDocument(documentSummary()),
    );
    expect(hook.result.current.state.selection).toEqual({
      folderId: FOLDER_B,
      documentId: null,
      page: null,
    });
    expect(navigation.replace).toHaveBeenCalledWith(
      `/knowledge-base?folder=${FOLDER_B}`,
    );
  });

  it("deletes the active folder and replaces its route with the former parent", async () => {
    vi.mocked(deleteFolder).mockResolvedValue(undefined);
    const hook = renderHook(() => useLibraryWorkspace(route()));
    await waitFor(() =>
      expect(hook.result.current.currentFolder?.node_id).toBe(FOLDER_A),
    );

    await act(() =>
      hook.result.current.removeFolder(folderNode(FOLDER_A)),
    );

    expect(deleteFolder).toHaveBeenCalledWith(
      FOLDER_A,
      expect.any(AbortSignal),
    );
    expect(hook.result.current.state.selection).toEqual({
      folderId: null,
      documentId: null,
      page: null,
    });
    expect(navigation.replace).toHaveBeenCalledWith("/knowledge-base");
  });

  it("reconciles a moved selected document from the live listing", async () => {
    window.history.replaceState(
      null,
      "",
      `/knowledge-base?folder=${FOLDER_B}&document=${DOCUMENT}&page=2`,
    );
    vi.mocked(updateNode).mockResolvedValue({
      node_id: NODE,
      parent_id: FOLDER_A,
      kind: "file",
      name: "Evidence.pdf",
      logical_path: "/Alpha/Evidence.pdf",
      document_id: DOCUMENT,
      uploader_user_id: "99999999-9999-4999-8999-999999999999",
      can_manage: true,
      can_create_children: false,
      readable_document_count: 1,
    });
    vi.mocked(listDocuments)
      .mockResolvedValueOnce([documentSummary()])
      .mockResolvedValue([
        documentSummary({
          parent_id: FOLDER_A,
          logical_path: "/Alpha/Evidence.pdf",
        }),
      ]);
    const hook = renderHook(() =>
      useLibraryWorkspace(
        route({ folderId: FOLDER_B, documentId: DOCUMENT, page: 2 }),
      ),
    );
    await waitFor(() =>
      expect(hook.result.current.selectedDocument?.parent_id).toBe(FOLDER_B),
    );

    await act(() =>
      hook.result.current.patchNode(NODE, { parent_id: FOLDER_A }),
    );

    expect(hook.result.current.state.selection).toEqual({
      folderId: FOLDER_A,
      documentId: DOCUMENT,
      page: 2,
    });
    expect(navigation.replace).toHaveBeenCalledWith(
      `/knowledge-base?folder=${FOLDER_A}&document=${DOCUMENT}&page=2`,
    );
  });

  it("replaces page controls with a live page-count-clamped URL", async () => {
    window.history.replaceState(
      null,
      "",
      `/knowledge-base?folder=${FOLDER_B}&document=${DOCUMENT}&page=1`,
    );
    const hook = renderHook(() =>
      useLibraryWorkspace(
        route({ folderId: FOLDER_B, documentId: DOCUMENT, page: 1 }),
      ),
    );
    await waitFor(() =>
      expect(hook.result.current.selectedDocument).not.toBeNull(),
    );
    act(() => hook.result.current.selectPage(99));
    expect(hook.result.current.state.selection.page).toBe(5);
    expect(navigation.replace).toHaveBeenCalledWith(
      `/knowledge-base?folder=${FOLDER_B}&document=${DOCUMENT}&page=5`,
    );
  });

  it("reports duplicate canonical location and pushes the returned document route", async () => {
    vi.mocked(uploadDocument).mockResolvedValue(
      uploadAccepted({
        duplicate_of: DOCUMENT,
        location_reused: true,
      }),
    );
    const hook = renderHook(() => useLibraryWorkspace(route()));
    await waitFor(() => expect(hook.result.current.state.loading).toBe(false));
    const file = new File(["%PDF-content"], "Evidence.pdf", {
      type: "application/pdf",
    });
    await act(() => hook.result.current.upload(file, []));

    expect(uploadDocument).toHaveBeenCalledWith(
      file,
      FOLDER_A,
      [],
      expect.any(AbortSignal),
    );
    expect(hook.result.current.state.uploadNotice).toMatch(
      /already exists.*\/Beta\/Evidence\.pdf/i,
    );
    expect(navigation.push).toHaveBeenCalledWith(
      `/knowledge-base?folder=${FOLDER_B}&document=${DOCUMENT}&page=1`,
    );
  });

  it("aborts a late upload and never navigates after unmount", async () => {
    const observed: { signal: AbortSignal | null } = { signal: null };
    vi.mocked(uploadDocument).mockImplementation(
      (_file, _folderId, _teamIds, signal) =>
        new Promise((_resolve, reject) => {
          observed.signal = signal ?? null;
          signal?.addEventListener("abort", () => reject(signal.reason));
        }),
    );
    const hook = renderHook(() => useLibraryWorkspace(route()));
    await waitFor(() => expect(hook.result.current.state.loading).toBe(false));
    const pending = hook.result.current.upload(
      new File(["%PDF"], "late.pdf", { type: "application/pdf" }),
      [],
    );

    hook.unmount();
    await expect(pending).rejects.toMatchObject({ name: "AbortError" });
    expect(observed.signal?.aborted).toBe(true);
    expect(navigation.push).not.toHaveBeenCalled();
  });

  it("commits the upload route before an immediate terminal poll refresh", async () => {
    Object.defineProperty(document, "visibilityState", {
      configurable: true,
      value: "visible",
    });
    vi.mocked(uploadDocument).mockResolvedValue(
      uploadAccepted({ status: "queued" }),
    );
    vi.mocked(getJob).mockResolvedValue(jobStatus({ status: "completed" }));
    const hook = renderHook(() => useLibraryWorkspace(route()));
    await waitFor(() => expect(hook.result.current.state.loading).toBe(false));

    await act(() =>
      hook.result.current.upload(
        new File(["%PDF"], "Evidence.pdf", { type: "application/pdf" }),
        [],
      ),
    );
    await waitFor(() => expect(getJob).toHaveBeenCalled());
    await waitFor(() =>
      expect(hook.result.current.polling.tracked).not.toHaveProperty(
        uploadAccepted().job_id,
      ),
    );

    expect(hook.result.current.state.selection).toEqual({
      folderId: FOLDER_B,
      documentId: DOCUMENT,
      page: 1,
    });
    expect(navigation.push).toHaveBeenCalledWith(
      `/knowledge-base?folder=${FOLDER_B}&document=${DOCUMENT}&page=1`,
    );
    expect(browseLibrary).toHaveBeenLastCalledWith(
      FOLDER_B,
      expect.any(AbortSignal),
    );
  });

  it("refreshes and tracks a failed document after reingest is queued", async () => {
    vi.mocked(reingestDocument).mockResolvedValue({
      document_id: DOCUMENT,
      job_id: JOB,
      status: "queued",
    });
    vi.mocked(getJob).mockResolvedValue(jobStatus({ status: "running" }));
    vi.mocked(listDocuments)
      .mockResolvedValueOnce([documentSummary({ state: "failed" })])
      .mockResolvedValue([documentSummary({ state: "queued" })]);
    const hook = renderHook(() =>
      useLibraryWorkspace(
        route({ folderId: FOLDER_B, documentId: DOCUMENT, page: 1 }),
      ),
    );
    await waitFor(() =>
      expect(hook.result.current.selectedDocument?.state).toBe("failed"),
    );

    await act(() => hook.result.current.reingest(documentSummary({ state: "failed" })));

    expect(reingestDocument).toHaveBeenCalledWith(
      DOCUMENT,
      expect.any(AbortSignal),
    );
    expect(hook.result.current.state.notice).toBe("Document reingest queued.");
    expect(hook.result.current.polling.tracked[JOB]).toEqual(
      expect.objectContaining({ documentId: DOCUMENT, status: "running" }),
    );
  });
});
