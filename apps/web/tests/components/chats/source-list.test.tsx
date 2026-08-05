import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { SourceList } from "@/features/chats/source-list";

import { historicalSource, streamSource } from "../../chat-fixtures";

vi.mock("@/features/chats/citation-pdf-viewer", () => ({
  CitationPdfViewer: () => <div data-testid="citation-pdf-viewer" />,
}));

describe("source list", () => {
  it("labels live streamed sources as draft and previews live documents in a drawer", async () => {
    const user = userEvent.setup();
    render(<SourceList sources={[streamSource]} draft />);
    expect(screen.getByText("Draft · Unverified")).toBeVisible();
    await user.click(
      screen.getByRole("button", {
        name: "Preview Research.pdf, Page 2",
      }),
    );
    expect(
      screen.getByRole("dialog", { name: "S1 · Research.pdf" }),
    ).toHaveAttribute("open");
    expect(
      screen.getByRole("link", { name: "Open in Knowledge Base" }),
    ).toHaveAttribute(
      "href",
      expect.stringContaining("document="),
    );
    expect(
      screen.getByLabelText("Research.pdf, page 2"),
    ).toBeVisible();
  });

  it("retains deleted source snapshots without creating a dead citation link", () => {
    render(
      <SourceList
        sources={[
          {
            ...historicalSource,
            document_id: null,
            chunk_id: null,
            source_available: false,
          },
        ]}
        title="Citations"
      />,
    );
    expect(
      screen.getByRole("button", {
        name: "Research.pdf, source unavailable",
      }),
    ).toBeDisabled();
    expect(screen.getByText("Unavailable")).toBeVisible();
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
    expect(screen.getByText(/\/Research\.pdf/)).toBeVisible();
  });

  it("loads verified evidence on demand and highlights the complete chunk", async () => {
    const user = userEvent.setup();
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({
          label: "S1",
          rank: 1,
          document_id: historicalSource.document_id,
          display_name: historicalSource.display_name,
          logical_path: historicalSource.logical_path,
          page_start: 2,
          page_end: 2,
          section: historicalSource.section,
          parse_method: "direct",
          snapshot_text: "The complete cited chunk.",
          highlight_anchor: {
            version: 1,
            normalization: "citation-highlight-v1",
            pages: [
              {
                page: 2,
                kind: "text_quote",
                selector: {
                  exact: "The complete cited chunk.",
                  prefix: "",
                  suffix: "",
                  sha256: "a".repeat(64),
                },
              },
            ],
          },
          source_sha256: "b".repeat(64),
          text_sha256: "c".repeat(64),
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );
    render(
      <SourceList
        sources={[historicalSource]}
        title="Citations"
        chatId="11111111-1111-4111-8111-111111111111"
        turnId="33333333-3333-4333-8333-333333333333"
      />,
    );

    await user.click(
      screen.getByRole("button", {
        name: "Preview Research.pdf, Page 2",
      }),
    );
    expect(await screen.findByText("The complete cited chunk.")).toHaveProperty(
      "tagName",
      "MARK",
    );
    expect(screen.getByTestId("citation-pdf-viewer")).toBeVisible();
    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        expect.stringContaining("/citations/S1/evidence"),
        expect.objectContaining({ cache: "no-store" }),
      ),
    );
  });
});
