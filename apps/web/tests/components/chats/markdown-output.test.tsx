import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { MarkdownOutput } from "@/features/chats/markdown-output";
import { historicalSource } from "../../chat-fixtures";

describe("MarkdownOutput", () => {
  it("renders GitHub Flavored Markdown as semantic HTML", () => {
    const { container } = render(
      <MarkdownOutput>
        {"## Result\n\n**Verified** answer.\n\n| File | Page |\n| --- | --- |\n| Guide | 3 |"}
      </MarkdownOutput>,
    );

    expect(
      screen.getByRole("heading", { level: 2, name: "Result" }),
    ).toBeVisible();
    expect(screen.getByText("Verified").tagName).toBe("STRONG");
    expect(screen.getByRole("table")).toBeVisible();
    expect(container.querySelector("p")).toHaveTextContent("Verified answer.");
  });

  it("sanitizes model-supplied HTML and unsafe link protocols", () => {
    const { container } = render(
      <MarkdownOutput>
        {'<script>alert("unsafe")</script>\n\n[unsafe](javascript:alert("x"))'}
      </MarkdownOutput>,
    );

    expect(container.querySelector("script")).toBeNull();
    expect(screen.getByText("unsafe").closest("a")).not.toHaveAttribute("href");
  });

  it("activates only verified prose citation labels", async () => {
    const user = userEvent.setup();
    const onCitationSelect = vi.fn();
    render(
      <MarkdownOutput
        citations={[historicalSource]}
        onCitationSelect={onCitationSelect}
      >
        {"Finding [S1]. `code [S1]` and [linked [S1]](https://example.test)."}
      </MarkdownOutput>,
    );

    const citation = screen.getByRole("button", {
      name: "S1, Research.pdf, page 2",
    });
    await user.click(citation);
    expect(onCitationSelect).toHaveBeenCalledWith("S1");
    expect(screen.getAllByRole("button")).toHaveLength(1);
    expect(screen.getByText("code [S1]").closest("code")).toBeVisible();
  });

  it("keeps draft citation-like text non-interactive", () => {
    render(<MarkdownOutput>{"Draft [S1]."}</MarkdownOutput>);

    expect(screen.queryByRole("button")).not.toBeInTheDocument();
    expect(screen.getByText("Draft [S1].")).toBeVisible();
  });
});
