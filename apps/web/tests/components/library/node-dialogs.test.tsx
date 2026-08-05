import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { ApiError } from "@/lib/http";
import { NodeDialogs } from "@/features/library/node-dialogs";

import {
  FOLDER_A,
  FOLDER_B,
  documentSummary,
  tree,
} from "../../library-fixtures";

const movePreview = {
  preview_id: "11111111-1111-4111-8111-111111111111",
  impact_digest: "a".repeat(64),
  impact: {
    user_ids: [],
    node_ids: [],
    document_ids: [],
    user_count: 0,
    node_count: 0,
    document_count: 0,
  },
};

describe("document dialogs", () => {
  it("retains the rename dialog and input after an authoritative conflict", async () => {
    const user = userEvent.setup();
    const patch = vi
      .fn()
      .mockRejectedValue(new ApiError("Name already exists.", 409));
    render(
      <NodeDialogs
        tree={tree}
        document={documentSummary()}
        busy={false}
        onPatch={patch}
        onPreviewMove={vi.fn()}
        onDelete={vi.fn()}
      />,
    );

    await user.click(screen.getByRole("button", { name: "Rename" }));
    const input = screen.getByLabelText("Document name");
    await user.clear(input);
    await user.type(input, "Renamed.pdf");
    await user.click(screen.getByRole("button", { name: "Save" }));

    expect(patch).toHaveBeenCalledWith(documentSummary().node_id, {
      name: "Renamed.pdf",
    });
    expect(screen.getByRole("dialog", { name: "Rename document" })).toHaveAttribute(
      "open",
    );
    expect(input).toHaveValue("Renamed.pdf");
    expect(screen.getByRole("alert")).toHaveTextContent("Name already exists.");
  });

  it("moves to the selected live folder and confirms destructive deletion", async () => {
    const user = userEvent.setup();
    const patch = vi.fn().mockResolvedValue(undefined);
    const preview = vi.fn().mockResolvedValue(movePreview);
    const remove = vi.fn().mockResolvedValue(undefined);
    render(
      <NodeDialogs
        tree={tree}
        document={documentSummary({ parent_id: FOLDER_B })}
        busy={false}
        onPatch={patch}
        onPreviewMove={preview}
        onDelete={remove}
      />,
    );

    await user.click(screen.getByRole("button", { name: "Move" }));
    await user.selectOptions(screen.getByLabelText("Destination"), FOLDER_A);
    await user.click(screen.getByRole("button", { name: "Review move" }));
    expect(preview).toHaveBeenCalledWith(documentSummary().node_id, FOLDER_A);
    const moveDialog = screen.getByRole("dialog", { name: "Move document" });
    await user.type(
      within(moveDialog).getByLabelText(/Type Evidence\.pdf to confirm/),
      "Evidence.pdf",
    );
    await user.click(screen.getByRole("button", { name: "Save" }));
    expect(patch).toHaveBeenCalledWith(documentSummary().node_id, {
      parent_id: FOLDER_A,
      preview_id: movePreview.preview_id,
      impact_digest: movePreview.impact_digest,
    });

    await user.click(screen.getByRole("button", { name: "Delete" }));
    expect(screen.getByText(/Historical citation snapshots remain/i)).toBeVisible();
    const deleteDialog = screen.getByRole("dialog", { name: "Delete document?" });
    await user.type(
      within(deleteDialog).getByLabelText(/Type Evidence\.pdf to confirm/),
      "Evidence.pdf",
    );
    await user.click(
      screen.getByRole("button", { name: "Delete document" }),
    );
    expect(remove).toHaveBeenCalledWith(
      expect.objectContaining({ document_id: documentSummary().document_id }),
    );
  });
});
