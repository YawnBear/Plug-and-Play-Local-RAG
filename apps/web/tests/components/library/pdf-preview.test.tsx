import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@/lib/http";

vi.mock("@/features/library/api", () => ({
  documentContentUrl: vi.fn(
    (id: string) => `http://127.0.0.1:8000/api/documents/${id}/content`,
  ),
  headDocumentContent: vi.fn(),
}));

import { headDocumentContent } from "@/features/library/api";
import { PdfPreview } from "@/features/library/pdf-preview";

import { documentSummary } from "../../library-fixtures";

describe("PDF preview", () => {
  beforeEach(() => vi.clearAllMocks());

  it("HEAD-checks before rendering a keyed page iframe and fallback link", async () => {
    vi.mocked(headDocumentContent).mockResolvedValue(new Response(null));
    const user = userEvent.setup();
    const onPage = vi.fn();
    render(
      <PdfPreview
        document={documentSummary()}
        page={3}
        onPage={onPage}
        onMissing={vi.fn()}
      />,
    );
    const frame = await screen.findByLabelText("Evidence.pdf, page 3");
    expect(headDocumentContent).toHaveBeenCalledWith(
      documentSummary().document_id,
      expect.any(AbortSignal),
    );
    expect(frame).toHaveAttribute("src", expect.stringMatching(/#page=3$/));
    expect(screen.getByRole("link", { name: "Open in new tab" })).toHaveAttribute(
      "href",
      expect.stringMatching(/#page=3$/),
    );
    await user.click(screen.getByRole("button", { name: "Next page" }));
    expect(onPage).toHaveBeenCalledWith(4);
  });

  it("clamps page controls to live page_count", async () => {
    vi.mocked(headDocumentContent).mockResolvedValue(new Response(null));
    render(
      <PdfPreview
        document={documentSummary({ page_count: 5 })}
        page={99}
        onPage={vi.fn()}
        onMissing={vi.fn()}
      />,
    );
    expect(
      await screen.findByLabelText("Evidence.pdf, page 5"),
    ).toBeVisible();
    expect(screen.getByRole("button", { name: "Next page" })).toBeDisabled();
  });

  it.each([
    [410, /retained PDF bytes are unavailable/i, false],
    [503, /object storage is unavailable/i, true],
  ])("renders actionable status %i without an iframe", async (status, text, retry) => {
    vi.mocked(headDocumentContent).mockRejectedValue(
      new ApiError("preview failure", status),
    );
    render(
      <PdfPreview
        document={documentSummary()}
        page={1}
        onPage={vi.fn()}
        onMissing={vi.fn()}
      />,
    );
    expect(await screen.findByText(text)).toBeVisible();
    expect(screen.queryByTitle(/Evidence\.pdf/)).not.toBeInTheDocument();
    const retryButton = screen.queryByRole("button", {
      name: "Retry preview",
    });
    if (retry) expect(retryButton).toBeInTheDocument();
    else expect(retryButton).not.toBeInTheDocument();
  });

  it("reconciles a content 404 as a missing document", async () => {
    vi.mocked(headDocumentContent).mockRejectedValue(
      new ApiError("not found", 404),
    );
    const onMissing = vi.fn();
    render(
      <PdfPreview
        document={documentSummary()}
        page={1}
        onPage={vi.fn()}
        onMissing={onMissing}
      />,
    );
    await waitFor(() => expect(onMissing).toHaveBeenCalledOnce());
    expect(screen.queryByTitle(/Evidence\.pdf/)).not.toBeInTheDocument();
  });

  it("aborts a manual retry when the preview unmounts", async () => {
    const observed: { signal: AbortSignal | null } = { signal: null };
    vi.mocked(headDocumentContent)
      .mockRejectedValueOnce(new ApiError("storage offline", 503))
      .mockImplementationOnce(
        (_documentId, signal) =>
          new Promise((_resolve, reject) => {
            observed.signal = signal ?? null;
            signal?.addEventListener("abort", () => reject(signal.reason));
          }),
      );
    const user = userEvent.setup();
    const view = render(
      <PdfPreview
        document={documentSummary()}
        page={1}
        onPage={vi.fn()}
        onMissing={vi.fn()}
      />,
    );
    await user.click(
      await screen.findByRole("button", { name: "Retry preview" }),
    );
    expect(observed.signal).not.toBeNull();
    view.unmount();
    expect(observed.signal?.aborted).toBe(true);
  });
});
