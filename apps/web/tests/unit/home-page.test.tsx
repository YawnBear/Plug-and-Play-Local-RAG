import { describe, expect, it } from "vitest";

import HomePage from "@/app/page";
import { ChatWorkspace } from "@/features/chats/chat-workspace";

import { CHAT_A } from "../chat-fixtures";

describe("Home server route", () => {
  it("awaits promised search params and passes canonical chat state to the workspace", async () => {
    const element = await HomePage({
      searchParams: Promise.resolve({ chat: CHAT_A }),
    });
    expect(element.type).toBe(ChatWorkspace);
    expect(element.props).toEqual({
      initialChatId: CHAT_A,
      invalidChatRoute: false,
    });
  });

  it("passes an invalid link to chooser recovery without rendering another main", async () => {
    const element = await HomePage({
      searchParams: Promise.resolve({ chat: "not-a-uuid" }),
    });
    expect(element.type).toBe(ChatWorkspace);
    expect(element.props).toEqual({
      initialChatId: null,
      invalidChatRoute: true,
    });
  });
});
