import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import {
  EmptyState,
  ErrorState,
  LoadingState,
} from "@/components/ui/async-state";

describe("async states", () => {
  it("announces loading and errors using semantic live regions", () => {
    const { rerender } = render(<LoadingState label="Loading chats…" />);
    expect(screen.getByRole("status")).toHaveTextContent("Loading chats…");
    rerender(<ErrorState message="Could not load chats." />);
    expect(screen.getByRole("alert")).toHaveTextContent(
      "Could not load chats.",
    );
  });

  it("offers a retry action and useful empty content", async () => {
    const retry = vi.fn();
    const user = userEvent.setup();
    const { rerender } = render(
      <ErrorState message="Request failed." onRetry={retry} />,
    );
    await user.click(screen.getByRole("button", { name: "Try again" }));
    expect(retry).toHaveBeenCalledOnce();

    rerender(
      <EmptyState title="No documents">
        <p>Upload a PDF to get started.</p>
      </EmptyState>,
    );
    expect(
      screen.getByRole("heading", { name: "No documents" }),
    ).toBeInTheDocument();
  });
});
