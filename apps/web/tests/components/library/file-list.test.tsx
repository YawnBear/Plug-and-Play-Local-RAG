import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { FileList } from "@/features/library/file-list";

import {
  DOCUMENT,
  FOLDER_B,
  browse,
  documentSummary,
  jobStatus,
} from "../../library-fixtures";

function renderFileList(status: string) {
  return render(
    <FileList
      browse={browse(FOLDER_B)}
      documents={[documentSummary()]}
      jobs={{ [DOCUMENT]: jobStatus({ status }) }}
      selectedDocumentId={null}
      onFolder={vi.fn()}
      onDocument={vi.fn()}
    />,
  );
}

describe("file list status", () => {
  it("shows a completed ingestion job as a ready document", () => {
    const view = renderFileList("completed");

    expect(screen.getByText("ready", { exact: true })).toBeVisible();
    expect(screen.queryByText("completed", { exact: true })).not.toBeInTheDocument();

    view.rerender(
      <FileList
        browse={browse(FOLDER_B)}
        documents={[documentSummary()]}
        jobs={{ [DOCUMENT]: jobStatus({ status: "failed" }) }}
        selectedDocumentId={null}
        onFolder={vi.fn()}
        onDocument={vi.fn()}
      />,
    );

    expect(screen.getByText("failed", { exact: true })).toBeVisible();
  });
});
