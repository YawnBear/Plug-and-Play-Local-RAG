import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { ActivityPanel } from "@/features/chats/activity-panel";
import type { DraftTurn } from "@/features/chats/state";

function draft(overrides: Partial<DraftTurn> = {}): DraftTurn {
  return {
    question: "What does the evidence say?",
    isRetry: false,
    turnId: "22222222-2222-4222-8222-222222222222",
    sources: [],
    activity: [
      { kind: "progress", phase: "retrieving" },
      { kind: "progress", phase: "reasoning" },
      { kind: "thinking", text: "Raw **unverified** model text." },
      { kind: "progress", phase: "streaming_answer" },
    ],
    reasoningActive: false,
    reasoningComplete: true,
    reasoningTruncated: false,
    text: "Draft answer",
    final: null,
    ...overrides,
  };
}

describe("ActivityPanel", () => {
  it("keeps trusted progress separate from selectable verbatim model thinking", () => {
    const { container } = render(
      <ActivityPanel draft={draft()} phase="streaming-draft" />,
    );

    expect(screen.getAllByText("Progress")).toHaveLength(3);
    expect(screen.getByText("Unverified model thinking")).toBeVisible();
    expect(screen.getByText("Raw **unverified** model text.")).toBeVisible();
    expect(screen.getAllByText("Streaming the answer")).toHaveLength(3);
    expect(container.querySelector("details")).toHaveAttribute("open");
  });

  it("keeps the raw-thinking viewport pinned to its newest content", () => {
    const value = draft({
      activity: [{ kind: "thinking", text: "First line" }],
      reasoningActive: true,
      reasoningComplete: false,
    });
    const { rerender } = render(
      <ActivityPanel draft={value} phase="streaming-draft" />,
    );
    const thinking = screen.getByText("First line");
    Object.defineProperties(thinking, {
      scrollHeight: { configurable: true, value: 640 },
      scrollTop: { configurable: true, writable: true, value: 120 },
    });

    rerender(
      <ActivityPanel
        draft={{
          ...value,
          activity: [{ kind: "thinking", text: "First line\nNewest line" }],
        }}
        phase="streaming-draft"
      />,
    );

    expect(thinking.scrollTop).toBe(640);
  });

  it("collapses completed activity and exposes server truncation", () => {
    const { container } = render(
      <ActivityPanel
        draft={draft({ reasoningTruncated: true })}
        phase="recovering"
      />,
    );

    expect(container.querySelector("details")).not.toHaveAttribute("open");
    expect(
      screen.getByText("Display limited to 20,000 characters."),
    ).toBeInTheDocument();
  });

  it("collapses on completion and can be reopened for copying", async () => {
    const user = userEvent.setup();
    const value = draft();
    const { container, rerender } = render(
      <ActivityPanel draft={value} phase="streaming-draft" />,
    );

    expect(container.querySelector("details")).toHaveAttribute("open");

    rerender(
      <ActivityPanel
        draft={{
          ...value,
          final: {
            chat_id: "11111111-1111-4111-8111-111111111111",
            turn_id: value.turnId!,
            seq: 9,
            answer: "Verified answer",
            insufficient_context: false,
            citations: [],
          },
        }}
        phase="verified"
      />,
    );

    const details = container.querySelector("details");
    expect(details).not.toHaveAttribute("open");

    await user.click(screen.getByText("Activity"));

    expect(details).toHaveAttribute("open");
    expect(screen.getByText("Raw **unverified** model text.")).toBeVisible();
  });
});
