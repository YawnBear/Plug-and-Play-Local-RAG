import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@/lib/http";
import type { KnowledgeBaseRouteState } from "@/lib/route-state";
import {
  SidebarContentOutlet,
  SidebarContentProvider,
} from "@/components/shell/protected-shell";

const navigation = vi.hoisted(() => ({
  push: vi.fn(),
  replace: vi.fn(),
}));
const authState = vi.hoisted(() => ({
  user: {
    id: "99999999-9999-4999-8999-999999999999",
    username: "testadmin",
    display_name: "Test Admin",
    role: "admin",
    status: "active",
  },
}));

vi.mock("next/navigation", () => ({
  useRouter: () => navigation,
}));

vi.mock("@/features/auth/auth-provider", () => ({
  useAuth: () => ({
    user: authState.user,
  }),
}));

vi.mock("@/features/library/api", () => ({
  getAccountTeams: vi.fn().mockResolvedValue({
    teams: [],
    requires_team_selection: false,
  }),
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
  documentContentUrl: vi.fn(
    (id: string) => `http://127.0.0.1:8000/api/documents/${id}/content`,
  ),
  headDocumentContent: vi.fn(),
}));

import {
  browseLibrary,
  getLibraryTree,
  getJob,
  headDocumentContent,
  listDocuments,
  reingestDocument,
} from "@/features/library/api";
import { KnowledgeBaseWorkspace } from "@/features/library/knowledge-base-workspace";

import {
  DOCUMENT,
  FOLDER_A,
  FOLDER_B,
  browse,
  documentSummary,
  folderNode,
  jobStatus,
  tree,
} from "../../library-fixtures";

const rootRoute: KnowledgeBaseRouteState = {
  folderId: null,
  documentId: null,
  page: null,
  invalidFolder: false,
  invalidDocument: false,
  invalidPage: false,
};

describe("knowledge base workspace", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    authState.user.role = "admin";
    authState.user.username = "testadmin";
    authState.user.display_name = "Test Admin";
    window.localStorage.removeItem("local-rag.kb-details-hidden.v1");
    window.localStorage.removeItem("local-rag.kb-explorer-expanded.v1");
    window.history.replaceState(null, "", "/knowledge-base");
    vi.mocked(getLibraryTree).mockResolvedValue(tree);
    vi.mocked(listDocuments).mockResolvedValue([]);
    vi.mocked(headDocumentContent).mockResolvedValue(new Response(null));
    vi.mocked(browseLibrary).mockImplementation(async (parentId) =>
      browse(parentId ?? null),
    );
  });

  function renderWorkspace(route = rootRoute) {
    return render(
      <SidebarContentProvider>
        <aside aria-label="Knowledge Base sidebar">
          <SidebarContentOutlet />
        </aside>
        <KnowledgeBaseWorkspace initialRoute={route} />
      </SidebarContentProvider>,
    );
  }

  it("renders one valid ID namespace and navigates from the unified Explorer", async () => {
    const user = userEvent.setup();
    const { container } = renderWorkspace();
    expect(await screen.findByRole("tree", { name: "Knowledge base files" }))
      .toBeVisible();

    const ids = [...container.querySelectorAll<HTMLElement>("[id]")].map(
      (element) => element.id,
    );
    expect(new Set(ids).size).toBe(ids.length);

    const sidebar = screen.getByRole("complementary", {
      name: "Knowledge Base sidebar",
    });
    const alpha = within(sidebar).getByRole("treeitem", { name: /Alpha/ });
    await user.click(within(alpha).getByRole("button", { name: "Alpha" }));
    expect(navigation.push).toHaveBeenCalledWith(
      `/knowledge-base?folder=${FOLDER_A}`,
    );
    expect(screen.getByRole("tab", { name: /Alpha/ })).toHaveAttribute(
      "aria-selected",
      "true",
    );
  });

  it("shows vertical ancestry guides beneath expanded folders", async () => {
    const user = userEvent.setup();
    renderWorkspace();

    const sidebar = screen.getByRole("complementary", {
      name: "Knowledge Base sidebar",
    });
    const root = await within(sidebar).findByRole("treeitem", {
      name: /Root/,
    });
    const alpha = within(sidebar).getByRole("treeitem", { name: /Alpha/ });

    expect(root).toHaveAttribute("data-expanded-children", "true");
    expect(alpha.querySelectorAll("[data-tree-guide]")).toHaveLength(1);

    await user.click(
      within(alpha).getByRole("button", { name: "Expand Alpha" }),
    );
    const child = within(sidebar).getByRole("treeitem", { name: /Child/ });

    expect(alpha).toHaveAttribute("data-expanded-children", "true");
    expect(child.querySelectorAll("[data-tree-guide]")).toHaveLength(2);

    await user.click(
      within(alpha).getByRole("button", { name: "Collapse Alpha" }),
    );
    expect(alpha).not.toHaveAttribute("data-expanded-children");
    expect(
      within(sidebar).queryByRole("treeitem", { name: /Child/ }),
    ).not.toBeInTheDocument();
  });

  it("retains the workspace and exposes an actionable retry after an outage", async () => {
    const user = userEvent.setup();
    vi.mocked(browseLibrary).mockRejectedValueOnce(
      new ApiError("browse offline"),
    );
    renderWorkspace();

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "browse offline",
    );
    const retry = screen.getByRole("button", { name: "Retry current folder" });
    await user.click(retry);
    await waitFor(() =>
      expect(screen.queryByText("browse offline")).not.toBeInTheDocument(),
    );
    expect(screen.getByRole("heading", { name: "Knowledge Base" })).toBeVisible();
  });

  it("gates folder-scoped controls when a different folder cannot load", async () => {
    const view = renderWorkspace({ ...rootRoute, folderId: FOLDER_A });
    expect(await screen.findByRole("heading", { name: "Alpha" })).toBeVisible();

    vi.mocked(browseLibrary).mockImplementation(async (parentId) => {
      if (parentId === FOLDER_B) throw new ApiError("browse offline");
      return browse(parentId ?? null);
    });
    window.history.replaceState(
      null,
      "",
      `/knowledge-base?folder=${FOLDER_B}`,
    );
    view.rerender(
      <SidebarContentProvider>
        <aside aria-label="Knowledge Base sidebar">
          <SidebarContentOutlet />
        </aside>
        <KnowledgeBaseWorkspace
          initialRoute={{ ...rootRoute, folderId: FOLDER_B }}
        />
      </SidebarContentProvider>,
    );

    expect(await screen.findByRole("alert")).toHaveTextContent("browse offline");
    expect(
      screen.queryByRole("heading", { name: "Beta" }),
    ).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Upload PDF" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "New folder" })).toBeDisabled();
  });

  it("enables member subfolder creation only with the folder capability", async () => {
    authState.user.role = "member";
    authState.user.username = "testmember";
    authState.user.display_name = "Test Member";
    vi.mocked(browseLibrary).mockResolvedValue({
      ...browse(FOLDER_A),
      breadcrumbs: [
        {
          ...folderNode(FOLDER_A),
          can_manage: false,
          can_create_children: true,
        },
      ],
    });

    renderWorkspace({ ...rootRoute, folderId: FOLDER_A });

    expect(await screen.findByRole("heading", { name: "Alpha" })).toBeVisible();
    expect(screen.getByRole("button", { name: "New folder" })).toBeEnabled();
  });

  it("keeps member subfolder creation disabled for a read-only folder", async () => {
    authState.user.role = "member";
    authState.user.username = "testmember";
    authState.user.display_name = "Test Member";
    vi.mocked(browseLibrary).mockResolvedValue({
      ...browse(FOLDER_A),
      breadcrumbs: [
        {
          ...folderNode(FOLDER_A),
          can_manage: false,
          can_create_children: false,
        },
      ],
    });

    renderWorkspace({ ...rootRoute, folderId: FOLDER_A });

    expect(await screen.findByRole("heading", { name: "Alpha" })).toBeVisible();
    expect(screen.getByRole("button", { name: "New folder" })).toBeDisabled();
  });

  it("retains page-control focus while the cited page route updates", async () => {
    vi.mocked(listDocuments).mockResolvedValue([documentSummary()]);
    vi.mocked(browseLibrary).mockResolvedValue(browse(FOLDER_B));
    const initialRoute = {
      ...rootRoute,
      folderId: FOLDER_B,
      documentId: DOCUMENT,
      page: 1,
    };
    const view = renderWorkspace(initialRoute);
    const next = await screen.findByRole("button", { name: "Next page" });
    next.focus();

    view.rerender(
      <SidebarContentProvider>
        <aside aria-label="Knowledge Base sidebar">
          <SidebarContentOutlet />
        </aside>
        <KnowledgeBaseWorkspace
          initialRoute={{ ...initialRoute, page: 2 }}
        />
      </SidebarContentProvider>,
    );
    await waitFor(() => expect(screen.getByText("Page 2 / 5")).toBeVisible());
    expect(screen.getByRole("button", { name: "Next page" })).toBe(next);
    expect(next).toHaveFocus();
  });

  it("shows document details from the Explorer row action without closing them", async () => {
    const user = userEvent.setup();
    vi.mocked(listDocuments).mockResolvedValue([documentSummary()]);
    vi.mocked(browseLibrary).mockResolvedValue(browse(FOLDER_B));
    renderWorkspace({
      ...rootRoute,
      folderId: FOLDER_B,
      documentId: DOCUMENT,
      page: 1,
    });

    expect(
      await screen.findByRole("button", { name: "Hide document details" }),
    ).toHaveAttribute("aria-pressed", "true");

    const actions = await screen.findByRole("button", {
      name: "Actions for Evidence.pdf",
    });
    await user.click(actions);
    await user.click(screen.getByRole("menuitem", { name: "View details" }));
    expect(
      screen.getByRole("button", { name: "Hide document details" }),
    ).toHaveAttribute("aria-pressed", "true");

    await user.click(
      screen.getByRole("button", { name: "Hide document details" }),
    );
    expect(
      screen.getByRole("button", { name: "Show document details" }),
    ).toHaveAttribute("aria-pressed", "false");

    await user.click(actions);
    await user.click(screen.getByRole("menuitem", { name: "View details" }));
    expect(
      screen.getByRole("button", { name: "Hide document details" }),
    ).toHaveAttribute("aria-pressed", "true");

    await user.click(actions);
    await user.click(screen.getByRole("menuitem", { name: "View details" }));
    expect(
      screen.getByRole("button", { name: "Hide document details" }),
    ).toHaveAttribute("aria-pressed", "true");
  });

  it("shows reingest only for manageable failed documents and tracks one click", async () => {
    const user = userEvent.setup();
    vi.mocked(listDocuments).mockResolvedValue([
      documentSummary({ state: "failed", can_manage: true }),
    ]);
    vi.mocked(browseLibrary).mockResolvedValue(browse(FOLDER_B));
    let resolveReingest: (() => void) | undefined;
    vi.mocked(reingestDocument).mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveReingest = () =>
            resolve({ document_id: DOCUMENT, job_id: "ffffffff-ffff-4fff-8fff-ffffffffffff", status: "queued" });
        }),
    );
    vi.mocked(getJob).mockResolvedValue(jobStatus({ status: "running" }));
    renderWorkspace({
      ...rootRoute,
      folderId: FOLDER_B,
      documentId: DOCUMENT,
      page: 1,
    });

    const button = await screen.findByRole("button", { name: "Reingest" });
    await user.click(button);
    await user.click(button);
    expect(reingestDocument).toHaveBeenCalledOnce();
    expect(button).toBeDisabled();
    await act(async () => {
      resolveReingest?.();
      await Promise.resolve();
    });
  });

  it("shows an inline reingest error and hides the action when read-only or ready", async () => {
    const user = userEvent.setup();
    vi.mocked(browseLibrary).mockResolvedValue(browse(FOLDER_B));
    vi.mocked(listDocuments).mockResolvedValue([
      documentSummary({ state: "failed", can_manage: true }),
    ]);
    vi.mocked(reingestDocument).mockRejectedValue(new ApiError("reingest unavailable"));
    const view = renderWorkspace({
      ...rootRoute,
      folderId: FOLDER_B,
      documentId: DOCUMENT,
      page: 1,
    });
    const button = await screen.findByRole("button", { name: "Reingest" });
    await user.click(button);
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "reingest unavailable",
    );

    view.unmount();
    vi.mocked(listDocuments).mockResolvedValue([
      documentSummary({ state: "failed", can_manage: false }),
    ]);
    const readOnlyView = renderWorkspace({
      ...rootRoute,
      folderId: FOLDER_B,
      documentId: DOCUMENT,
      page: 1,
    });
    await screen.findByRole("heading", { name: "Evidence.pdf" });
    expect(screen.queryByRole("button", { name: "Reingest" })).not.toBeInTheDocument();

    readOnlyView.unmount();
    vi.mocked(listDocuments).mockResolvedValue([
      documentSummary({ state: "ready", can_manage: true }),
    ]);
    renderWorkspace({
      ...rootRoute,
      folderId: FOLDER_B,
      documentId: DOCUMENT,
      page: 1,
    });
    await waitFor(() =>
      expect(screen.queryByRole("button", { name: "Reingest" })).not.toBeInTheDocument(),
    );
  });

  it("does not carry a pending document reingest state into a new selection", async () => {
    const user = userEvent.setup();
    const documentB = "abababab-abab-4aba-8bab-abababababab";
    const documentA = documentSummary({
      state: "failed",
      display_name: "A.pdf",
    });
    const documentBDetails = documentSummary({
      document_id: documentB,
      state: "failed",
      display_name: "B.pdf",
      filename: "b-original.pdf",
      logical_path: "/Beta/B.pdf",
    });
    vi.mocked(browseLibrary).mockResolvedValue(browse(FOLDER_B));
    vi.mocked(listDocuments).mockResolvedValue([documentA, documentBDetails]);
    let rejectReingest: ((error: Error) => void) | undefined;
    vi.mocked(reingestDocument).mockImplementation(
      () =>
        new Promise((_resolve, reject) => {
          rejectReingest = reject;
        }),
    );
    const view = renderWorkspace({
      ...rootRoute,
      folderId: FOLDER_B,
      documentId: DOCUMENT,
      page: 1,
    });
    const documentAButton = await screen.findByRole("button", {
      name: "Reingest",
    });
    await user.click(documentAButton);
    expect(documentAButton).toBeDisabled();

    view.rerender(
      <SidebarContentProvider>
        <aside aria-label="Knowledge Base sidebar">
          <SidebarContentOutlet />
        </aside>
        <KnowledgeBaseWorkspace
          initialRoute={{
            ...rootRoute,
            folderId: FOLDER_B,
            documentId: documentB,
            page: 1,
          }}
        />
      </SidebarContentProvider>,
    );

    await screen.findByRole("heading", { name: "B.pdf", level: 2 });
    const documentBButton = screen.getByRole("button", { name: "Reingest" });
    expect(documentBButton).toBeEnabled();
    await act(async () => {
      rejectReingest?.(new Error("document A failed late"));
      await Promise.resolve();
    });
    expect(screen.queryByText("document A failed late")).toBeNull();
    expect(documentBButton).toBeEnabled();
  });
});
