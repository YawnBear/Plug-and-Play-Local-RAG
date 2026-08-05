import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

const useWorkspace = vi.hoisted(() => vi.fn());

vi.mock("@/features/chats/use-chat-workspace", () => ({
  useChatWorkspace: useWorkspace,
}));

import { ChatWorkspace } from "@/features/chats/chat-workspace";
import {
  SidebarContentOutlet,
  SidebarContentProvider,
} from "@/components/shell/protected-shell";

import { CHAT_A, chatDetail, chatSummary } from "../../chat-fixtures";

function controller() {
  return {
    state: {
      phase: "stopping" as const,
      chats: [chatSummary()],
      chatsLoading: false,
      chatsError: "Conversation list unavailable",
      activeChatId: CHAT_A,
      detail: chatDetail(),
      detailRefreshing: false,
      draft: {
        question: "Question",
        isRetry: false,
        turnId: null,
        sources: [],
        activity: [],
        reasoningActive: false,
        reasoningComplete: false,
        reasoningTruncated: false,
        text: "partial draft",
        final: null,
      },
      error: "The server is still stopping this answer.",
      routeNotice: null,
    },
    actionError: null,
    generating: true,
    openChat: vi.fn(),
    rename: vi.fn().mockResolvedValue(false),
    remove: vi.fn().mockResolvedValue(false),
    refreshChats: vi.fn().mockResolvedValue([]),
    loadDetail: vi.fn().mockResolvedValue(null),
    send: vi.fn().mockResolvedValue(false),
    retry: vi.fn().mockResolvedValue(false),
    stop: vi.fn().mockResolvedValue(undefined),
    saveScope: vi.fn(),
    patchDetail: vi.fn(),
    clearActionError: vi.fn(),
  };
}

describe("chat workspace recovery actions", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) => {
      callback(0);
      return 1;
    });
    vi.stubGlobal("matchMedia", vi.fn().mockReturnValue({ matches: true }));
    Element.prototype.scrollTo = vi.fn();
  });

  it("shows detail Refresh with a draft and a separate list retry", async () => {
    const current = controller();
    useWorkspace.mockReturnValue(current);
    const user = userEvent.setup();
    render(
      <SidebarContentProvider>
        <SidebarContentOutlet />
        <ChatWorkspace initialChatId={CHAT_A} invalidChatRoute={false} />
      </SidebarContentProvider>,
    );

    expect(screen.getByRole("region", { name: "Conversations" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Conversations" })).not.toBeInTheDocument();
    expect(
      screen.getAllByRole("alert").filter((alert) =>
        alert.textContent?.includes("still stopping"),
      ),
    ).toHaveLength(1);
    await user.click(screen.getByRole("button", { name: "Refresh" }));
    expect(current.loadDetail).toHaveBeenCalledWith(CHAT_A);
    await user.click(
      screen.getByRole("button", { name: "Retry conversations" }),
    );
    expect(current.refreshChats).toHaveBeenCalledOnce();
  });
});
