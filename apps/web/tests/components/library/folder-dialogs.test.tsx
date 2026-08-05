import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { ApiError } from "@/lib/http";
import { FolderDialogs } from "@/features/library/folder-dialogs";

import { FOLDER_A, folderNode, tree } from "../../library-fixtures";

describe("folder dialogs", () => {
  it("preserves entered names and the open dialog after a 409", async () => {
    const user = userEvent.setup();
    const create = vi
      .fn()
      .mockRejectedValue(new ApiError("Name already exists.", 409));
    render(
      <FolderDialogs
        tree={tree}
        currentFolder={null}
        busy={false}
        onCreate={create}
        onPatch={vi.fn()}
        onPreviewMove={vi.fn()}
        onDelete={vi.fn()}
      />,
    );
    await user.click(screen.getByRole("button", { name: "New folder" }));
    const input = screen.getByLabelText("Folder name");
    await user.type(input, "Evidence");
    await user.click(screen.getByRole("button", { name: "Save" }));
    expect(screen.getByRole("dialog", { name: "Create folder" })).toHaveAttribute(
      "open",
    );
    expect(input).toHaveValue("Evidence");
    expect(screen.getByRole("alert")).toHaveTextContent("Name already exists.");
  });

  it("disables a folder and all descendants in its move picker", async () => {
    const user = userEvent.setup();
    render(
      <FolderDialogs
        tree={tree}
        currentFolder={folderNode(FOLDER_A)}
        busy={false}
        onCreate={vi.fn()}
        onPatch={vi.fn()}
        onPreviewMove={vi.fn()}
        onDelete={vi.fn()}
      />,
    );
    await user.click(screen.getByRole("button", { name: "Move" }));
    expect(
      screen.getByRole("option", { name: "Alpha" }),
    ).toBeDisabled();
    expect(
      screen.getByRole("option", { name: "— Child" }),
    ).toBeDisabled();
    expect(screen.getByRole("option", { name: "Beta" })).toBeEnabled();
  });
});
