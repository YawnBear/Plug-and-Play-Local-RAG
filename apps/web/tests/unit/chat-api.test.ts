import { afterEach, describe, expect, it, vi } from "vitest";

import {
  createChat,
  deleteChat,
  getChat,
  listChats,
  saveChatScope,
} from "@/features/chats/api";

const CHAT = "11111111-1111-4111-8111-111111111111";
const NODE = "22222222-2222-4222-8222-222222222222";
const timestamp = "2026-07-23T00:00:00Z";
const summary = {
  chat_id: CHAT,
  title: "New chat",
  title_is_manual: false,
  scope_mode: "all_ready",
  scope_version: 1,
  created_at: timestamp,
  updated_at: timestamp,
};

afterEach(() => vi.unstubAllGlobals());

describe("chat API", () => {
  it("requests chat details with the default pagination window", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          ...summary,
          scope_node_ids: [],
          turns: [],
          page: 1,
          limit: 50,
          total: 0,
        }),
        { status: 200 },
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    await getChat(CHAT);
    expect(fetchMock.mock.calls[0][0]).toBe(
      `/api/chats/${CHAT}?page=1&limit=50`,
    );
  });

  it("lists chats without cache and strictly parses the response", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(new Response(JSON.stringify([summary]), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(listChats()).resolves.toEqual([summary]);
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/chats",
      expect.objectContaining({ cache: "no-store" }),
    );
  });

  it("creates a chat with the exact optional title body", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(new Response(JSON.stringify(summary), { status: 201 }));
    vi.stubGlobal("fetch", fetchMock);

    await createChat("Manual");
    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(init.method).toBe("POST");
    expect(init.body).toBe('{"title":"Manual"}');
  });

  it("saves selected scope and deletes through canonical UUID paths", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            ...summary,
            scope_mode: "selected",
            scope_version: 2,
            scope_node_ids: [NODE],
          }),
          { status: 200 },
        ),
      )
      .mockResolvedValueOnce(new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetchMock);

    await saveChatScope(CHAT, { mode: "selected", node_ids: [NODE] });
    await deleteChat(CHAT);

    expect(fetchMock.mock.calls[0][0]).toBe(
      `/api/chats/${CHAT}/scope`,
    );
    expect((fetchMock.mock.calls[0][1] as RequestInit).body).toBe(
      `{"mode":"selected","node_ids":["${NODE}"]}`,
    );
    expect(fetchMock.mock.calls[1][0]).toBe(
      `/api/chats/${CHAT}`,
    );
  });
});
