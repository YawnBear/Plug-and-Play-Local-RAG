import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { ConversationList } from "@/features/chats/conversation-list";

import { CHAT_A, CHAT_B, chatSummary } from "../../chat-fixtures";

function props(overrides: Record<string, unknown> = {}) {
  return {
    chats: [chatSummary()],
    activeChatId: CHAT_A,
    loading: false,
    busy: false,
    error: null,
    actionError: null,
    onRetry: vi.fn().mockResolvedValue([]),
    onOpen: vi.fn(),
    onRename: vi.fn().mockResolvedValue(true),
    onDelete: vi.fn().mockResolvedValue(true),
    ...overrides,
  };
}

describe("conversation list", () => {
  it("keeps rename neutral and marks only delete as destructive", () => {
    render(<ConversationList {...props()} />);

    expect(
      screen.getByRole("button", { name: "Rename New chat" }),
    ).toHaveClass("conversation-item__action--rename");
    expect(
      screen.getByRole("button", { name: "Rename New chat" }),
    ).not.toHaveAttribute("aria-describedby");
    expect(
      screen.getByRole("button", { name: "Delete New chat" }),
    ).toHaveClass("conversation-item__action--delete");
    expect(
      screen.getByRole("button", { name: "Delete New chat" }),
    ).not.toHaveAttribute("aria-describedby");
  });

  it("uses unique label associations for simultaneous drawer and aside instances", async () => {
    const user = userEvent.setup();
    render(
      <>
        <ConversationList {...props()} />
        <ConversationList {...props()} />
      </>,
    );
    const sections = screen.getAllByRole("region", { name: "Conversations" });
    expect(sections).toHaveLength(2);
    const labelledBy = sections.map((section) =>
      section.getAttribute("aria-labelledby"),
    );
    expect(new Set(labelledBy).size).toBe(2);

    const renameButtons = screen.getAllByRole("button", {
      name: "Rename New chat",
    });
    await user.click(renameButtons[0]);
    await user.click(renameButtons[1]);
    const inputs = screen.getAllByLabelText("Conversation title");
    expect(new Set(inputs.map((input) => input.id)).size).toBe(2);
  });

  it("retries list failures and disables even the active conversation while busy", async () => {
    const retry = vi.fn().mockResolvedValue([]);
    const user = userEvent.setup();
    render(
      <ConversationList
        {...props({ busy: true, error: "List unavailable", onRetry: retry })}
      />,
    );
    expect(
      screen.getByRole("button", { name: "New chat", current: "page" }),
    ).toBeDisabled();
    await user.click(
      screen.getByRole("button", { name: "Retry conversations" }),
    );
    expect(retry).toHaveBeenCalledOnce();
  });

  it("retains rename input and delete dialog when server mutations conflict", async () => {
    const user = userEvent.setup();
    const onRename = vi.fn().mockResolvedValue(false);
    const onDelete = vi.fn().mockResolvedValue(false);
    render(<ConversationList {...props({ onRename, onDelete })} />);

    await user.click(screen.getByRole("button", { name: "Rename New chat" }));
    const renameDialog = screen.getByRole("dialog", {
      name: "Rename conversation",
    });
    const input = within(renameDialog).getByLabelText("Conversation title");
    await user.clear(input);
    await user.type(input, "Blocked rename");
    await user.click(within(renameDialog).getByRole("button", { name: "Save title" }));
    expect(onRename).toHaveBeenCalledWith(CHAT_A, "Blocked rename");
    expect(screen.getByRole("dialog", { name: "Rename conversation" })).toBeVisible();
    expect(screen.getByDisplayValue("Blocked rename")).toBeVisible();

    await user.click(within(renameDialog).getByRole("button", { name: "Cancel" }));
    await user.click(screen.getByRole("button", { name: "Delete New chat" }));
    const deleteDialog = screen.getByRole("dialog", {
      name: "Delete conversation?",
    });
    await user.click(
      within(deleteDialog).getByRole("button", {
        name: "Delete conversation",
      }),
    );
    expect(onDelete).toHaveBeenCalledWith(CHAT_A);
    expect(screen.getByRole("dialog", { name: "Delete conversation?" })).toBeVisible();
  });

  it("filters conversation titles without searching message content", async () => {
    const user = userEvent.setup();
    render(
      <ConversationList
        {...props({
          chats: [
            chatSummary(CHAT_A, "Project alpha"),
            chatSummary(CHAT_B, "Release beta"),
          ],
        })}
      />,
    );

    await user.type(
      screen.getByRole("searchbox", {
        name: "Search conversations by title",
      }),
      "beta",
    );

    expect(
      screen.getByRole("button", { name: "Release beta" }),
    ).toBeVisible();
    expect(
      screen.queryByRole("button", { name: "Project alpha" }),
    ).not.toBeInTheDocument();
  });
});
