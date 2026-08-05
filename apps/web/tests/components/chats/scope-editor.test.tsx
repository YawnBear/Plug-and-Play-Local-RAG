import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("@/features/library/api", () => ({
  getLibraryTree: vi.fn(),
  browseLibrary: vi.fn(),
}));

import {
  browseLibrary,
  getLibraryTree,
} from "@/features/library/api";
import { ScopeEditor } from "@/features/chats/scope-editor";
import { ApiError } from "@/lib/http";

import {
  NODE_A,
  chatDetail,
  timestamp,
} from "../../chat-fixtures";

afterEach(() => vi.clearAllMocks());

describe("scope editor", () => {
  it("saves the server-canonical node IDs and disables changes during generation", async () => {
    const user = userEvent.setup();
    vi.mocked(getLibraryTree).mockResolvedValue([]);
    vi.mocked(browseLibrary).mockResolvedValue({
      parent_id: null,
      breadcrumbs: [],
      children: [
        {
          node_id: NODE_A,
          parent_id: null,
          kind: "file",
          name: "Research.pdf",
          logical_path: "/Research.pdf",
          document_id: "44444444-4444-4444-8444-444444444444",
          uploader_user_id: "88888888-8888-4888-8888-888888888888",
          can_manage: true,
          can_create_children: false,
          readable_document_count: 1,
        },
      ],
      page: 1,
      limit: 100,
      total: 1,
    });
    const onSave = vi.fn().mockResolvedValue({
      ...chatDetail(),
      scope_mode: "selected",
      scope_version: 2,
      scope_node_ids: [NODE_A],
      updated_at: timestamp,
    });
    const { rerender } = render(
      <ScopeEditor
        detail={chatDetail()}
        generating={false}
        onSave={onSave}
      />,
    );

    await user.click(screen.getByRole("button", { name: /scope:/i }));
    await user.click(
      screen.getByRole("radio", { name: "Selected folders and files" }),
    );
    await waitFor(() =>
      expect(screen.getByLabelText("Research.pdf")).toBeVisible(),
    );
    await user.click(screen.getByLabelText("Research.pdf"));
    await user.click(screen.getByRole("button", { name: "Save scope" }));
    expect(onSave).toHaveBeenCalledWith("selected", [NODE_A]);

    rerender(
      <ScopeEditor
        detail={chatDetail()}
        generating
        onSave={onSave}
      />,
    );
    expect(screen.getByRole("button", { name: /scope:/i })).toBeDisabled();
  });

  it("retains the open editor and selection after a 409 conflict", async () => {
    const user = userEvent.setup();
    vi.mocked(getLibraryTree).mockResolvedValue([]);
    vi.mocked(browseLibrary).mockResolvedValue({
      parent_id: null,
      breadcrumbs: [],
      children: [
        {
          node_id: NODE_A,
          parent_id: null,
          kind: "file",
          name: "Research.pdf",
          logical_path: "/Research.pdf",
          document_id: "44444444-4444-4444-8444-444444444444",
          uploader_user_id: "88888888-8888-4888-8888-888888888888",
          can_manage: true,
          can_create_children: false,
          readable_document_count: 1,
        },
      ],
      page: 1,
      limit: 100,
      total: 1,
    });
    const onSave = vi
      .fn()
      .mockRejectedValue(new ApiError("Generation is active.", 409));
    render(
      <ScopeEditor
        detail={chatDetail()}
        generating={false}
        onSave={onSave}
      />,
    );

    await user.click(screen.getByRole("button", { name: /scope:/i }));
    await user.click(
      screen.getByRole("radio", { name: "Selected folders and files" }),
    );
    const file = await screen.findByLabelText("Research.pdf");
    await user.click(file);
    await user.click(screen.getByRole("button", { name: "Save scope" }));

    expect(
      screen.getByRole("dialog", { name: "Conversation scope" }),
    ).toHaveAttribute("open");
    expect(file).toBeChecked();
    expect(screen.getByRole("alert")).toHaveTextContent(
      "Generation is active.",
    );
  });

  it("visually subsumes loaded descendants and saves only the ancestor folder", async () => {
    const user = userEvent.setup();
    const fileNode = "77777777-7777-4777-8777-777777777777";
    vi.mocked(getLibraryTree).mockResolvedValue([
      {
        node_id: NODE_A,
        parent_id: null,
        name: "Evidence",
        logical_path: "/Evidence",
        children: [],
      },
    ]);
    vi.mocked(browseLibrary).mockImplementation(async (parentId) =>
      parentId === NODE_A
        ? {
            parent_id: NODE_A,
            breadcrumbs: [],
            children: [
              {
                node_id: fileNode,
                parent_id: NODE_A,
                kind: "file",
                name: "Research.pdf",
                logical_path: "/Evidence/Research.pdf",
                document_id: "44444444-4444-4444-8444-444444444444",
                uploader_user_id: "88888888-8888-4888-8888-888888888888",
                can_manage: true,
                can_create_children: false,
                readable_document_count: 1,
              },
            ],
            page: 1,
            limit: 100,
            total: 1,
          }
        : {
            parent_id: null,
            breadcrumbs: [],
            children: [],
            page: 1,
            limit: 100,
            total: 0,
          },
    );
    const onSave = vi.fn().mockResolvedValue({
      ...chatDetail(),
      scope_mode: "selected",
      scope_version: 2,
      scope_node_ids: [NODE_A],
    });
    render(
      <ScopeEditor
        detail={chatDetail()}
        generating={false}
        onSave={onSave}
      />,
    );
    await user.click(screen.getByRole("button", { name: /scope:/i }));
    await user.click(
      screen.getByRole("radio", { name: "Selected folders and files" }),
    );
    await user.click(screen.getByRole("button", { name: "Expand Evidence" }));
    const file = await screen.findByLabelText("Research.pdf");
    await user.click(file);
    await user.click(screen.getByLabelText("Include folder Evidence"));

    expect(file).toBeChecked();
    expect(file).toBeDisabled();
    expect(screen.getByText("Included by selected folder")).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Save scope" }));
    expect(onSave).toHaveBeenCalledWith("selected", [NODE_A]);
  });
});
