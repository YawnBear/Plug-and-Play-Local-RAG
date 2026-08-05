import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@/lib/http";

const navigation = vi.hoisted(() => ({
  push: vi.fn(),
  replace: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => navigation,
}));

vi.mock("@/features/chats/api", () => ({
  listChats: vi.fn(),
  createChat: vi.fn(),
  getChat: vi.fn(),
  renameChat: vi.fn(),
  deleteChat: vi.fn(),
  saveChatScope: vi.fn(),
  streamMessage: vi.fn(),
  streamRetry: vi.fn(),
}));

import {
  createChat,
  deleteChat,
  getChat,
  listChats,
  renameChat,
  saveChatScope,
  streamMessage,
  streamRetry,
} from "@/features/chats/api";
import type { ChatStreamHandlers } from "@/features/chats/sse";
import { useChatWorkspace } from "@/features/chats/use-chat-workspace";

import {
  CHAT_A,
  CHAT_B,
  TURN_A,
  chatDetail,
  chatSummary,
  completedTurn,
  streamSource,
} from "../chat-fixtures";

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((nextResolve, nextReject) => {
    resolve = nextResolve;
    reject = nextReject;
  });
  return { promise, resolve, reject };
}

describe("useChatWorkspace", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(listChats).mockResolvedValue([]);
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("recovers a missing chat to the chooser but retains a network-failed route", async () => {
    vi.mocked(getChat).mockRejectedValueOnce(new ApiError("Missing", 404));
    const missing = renderHook(() =>
      useChatWorkspace({ initialChatId: CHAT_A, invalidChatRoute: false }),
    );
    await waitFor(() => expect(missing.result.current.state.phase).toBe("chooser"));
    expect(navigation.replace).toHaveBeenCalledWith("/");
    missing.unmount();

    vi.clearAllMocks();
    vi.mocked(listChats).mockResolvedValue([]);
    vi.mocked(getChat).mockRejectedValueOnce(new ApiError("API offline"));
    const offline = renderHook(() =>
      useChatWorkspace({ initialChatId: CHAT_A, invalidChatRoute: false }),
    );
    await waitFor(() => expect(offline.result.current.state.phase).toBe("failed"));
    expect(offline.result.current.state.activeChatId).toBe(CHAT_A);
    expect(navigation.replace).not.toHaveBeenCalled();
  });

  it("creates on the first valid send, activates synchronously, and keeps early SSE events", async () => {
    vi.mocked(createChat).mockResolvedValue(chatSummary());
    vi.mocked(streamMessage).mockImplementation(
      async (chatId, question, handlers) => {
        expect(chatId).toBe(CHAT_A);
        expect(question).toBe("First question");
        handlers.onStatus({
          chat_id: CHAT_A,
          turn_id: TURN_A,
          seq: 1,
          phase: "streaming_answer",
        });
        handlers.onToken({
          chat_id: CHAT_A,
          turn_id: TURN_A,
          seq: 2,
          text: "early",
        });
        return {
          chat_id: CHAT_A,
          turn_id: TURN_A,
          seq: 3,
          answer: "early",
          insufficient_context: false,
          citations: [],
        };
      },
    );
    vi.mocked(getChat).mockResolvedValue(chatDetail(CHAT_A, [completedTurn()]));
    const hook = renderHook(() =>
      useChatWorkspace({ initialChatId: null, invalidChatRoute: false }),
    );

    await expect(
      act(() => hook.result.current.send("  First question  ")),
    ).resolves.toBe(true);
    expect(createChat).toHaveBeenCalledOnce();
    expect(navigation.replace).toHaveBeenCalledWith(`/?chat=${CHAT_A}`);
    expect(navigation.push).not.toHaveBeenCalledWith(`/?chat=${CHAT_A}`);
    await waitFor(() => expect(streamMessage).toHaveBeenCalledOnce());
    expect(hook.result.current.state.activeChatId).toBe(CHAT_A);
  });

  it("does not create a chat for an invalid empty-home submission", async () => {
    const hook = renderHook(() =>
      useChatWorkspace({ initialChatId: null, invalidChatRoute: false }),
    );
    await expect(act(() => hook.result.current.send("   "))).resolves.toBe(false);
    expect(createChat).not.toHaveBeenCalled();
    expect(streamMessage).not.toHaveBeenCalled();
  });

  it("coalesces same-tick first sends while chat creation is in flight", async () => {
    const pending = deferred<ReturnType<typeof chatSummary>>();
    vi.mocked(createChat).mockReturnValue(pending.promise);
    const hook = renderHook(() =>
      useChatWorkspace({ initialChatId: null, invalidChatRoute: false }),
    );

    let first!: Promise<boolean>;
    let second!: Promise<boolean>;
    act(() => {
      first = hook.result.current.send("First question");
      second = hook.result.current.send("Second question");
    });
    await expect(second).resolves.toBe(false);
    expect(createChat).toHaveBeenCalledOnce();

    pending.resolve(chatSummary());
    await expect(first).resolves.toBe(true);
  });

  it("keeps tokens unverified, then accepts only the authoritative refetch", async () => {
    let handlers: ChatStreamHandlers | null = null;
    let finish!: () => void;
    const streamDone = new Promise<void>((resolve) => {
      finish = resolve;
    });
    vi.mocked(getChat)
      .mockResolvedValueOnce(chatDetail())
      .mockResolvedValueOnce(chatDetail(CHAT_A, [completedTurn()]));
    vi.mocked(listChats)
      .mockResolvedValueOnce([chatSummary()])
      .mockResolvedValueOnce([chatSummary(CHAT_A, "Generated title")])
      .mockRejectedValueOnce(new ApiError("Conversation list unavailable"));
    vi.mocked(streamMessage).mockImplementation(
      async (_chatId, _question, nextHandlers) => {
        handlers = nextHandlers;
        nextHandlers.onStatus({
          chat_id: CHAT_A,
          turn_id: TURN_A,
          seq: 1,
          phase: "retrieving",
        });
        nextHandlers.onSources({
          chat_id: CHAT_A,
          turn_id: TURN_A,
          seq: 2,
          sources: [streamSource],
        });
        nextHandlers.onStatus({
          chat_id: CHAT_A,
          turn_id: TURN_A,
          seq: 3,
          phase: "reasoning",
        });
        nextHandlers.onReasoningStart({
          chat_id: CHAT_A,
          turn_id: TURN_A,
          seq: 4,
        });
        nextHandlers.onReasoningDelta({
          chat_id: CHAT_A,
          turn_id: TURN_A,
          seq: 5,
          text: "raw model thought",
        });
        nextHandlers.onReasoningEnd({
          chat_id: CHAT_A,
          turn_id: TURN_A,
          seq: 6,
          truncated: false,
        });
        nextHandlers.onToken({
          chat_id: CHAT_A,
          turn_id: TURN_A,
          seq: 7,
          text: "unverified token",
        });
        await streamDone;
        const final = {
          chat_id: CHAT_A,
          turn_id: TURN_A,
          seq: 8,
          answer: "The verified finding [S1].",
          insufficient_context: false,
          citations: [streamSource],
        };
        nextHandlers.onFinal(final);
        return final;
      },
    );
    const hook = renderHook(() =>
      useChatWorkspace({ initialChatId: CHAT_A, invalidChatRoute: false }),
    );
    await waitFor(() => expect(hook.result.current.state.detail).not.toBeNull());

    act(() => {
      void hook.result.current.send("What was found?");
    });
    await waitFor(() =>
      expect(hook.result.current.state.draft?.text).toBe("unverified token"),
    );
    expect(hook.result.current.state.phase).toBe("streaming-draft");
    expect(hook.result.current.state.draft?.activity).toEqual([
      { kind: "progress", phase: "retrieving" },
      { kind: "progress", phase: "reasoning" },
      { kind: "thinking", text: "raw model thought" },
    ]);
    expect(hook.result.current.state.draft?.reasoningComplete).toBe(true);
    expect(hook.result.current.state.detail?.turns).toHaveLength(0);
    expect(handlers).not.toBeNull();

    finish();
    await waitFor(() => expect(hook.result.current.state.phase).toBe("verified"));
    expect(hook.result.current.state.detail?.turns[0].final_answer).toMatch(
      /verified finding/,
    );
    expect(hook.result.current.state.chatsError).toBe(
      "Conversation list unavailable",
    );
  });

  it("ignores stale stream events after switching chats", async () => {
    let oldHandlers!: ChatStreamHandlers;
    vi.mocked(getChat)
      .mockResolvedValueOnce(chatDetail())
      .mockResolvedValueOnce(chatDetail(CHAT_B));
    vi.mocked(streamMessage).mockImplementation(
      (_chatId, _question, handlers, signal) =>
        new Promise((_resolve, reject) => {
          oldHandlers = handlers;
          signal.addEventListener("abort", () => reject(signal.reason));
        }),
    );
    const hook = renderHook(() =>
      useChatWorkspace({ initialChatId: CHAT_A, invalidChatRoute: false }),
    );
    await waitFor(() => expect(hook.result.current.state.detail).not.toBeNull());
    act(() => {
      void hook.result.current.send("Old question");
    });
    await waitFor(() => expect(streamMessage).toHaveBeenCalled());

    act(() => hook.result.current.openChat(CHAT_B));
    await waitFor(() =>
      expect(hook.result.current.state.activeChatId).toBe(CHAT_B),
    );
    act(() => {
      oldHandlers.onToken({
        chat_id: CHAT_A,
        turn_id: TURN_A,
        seq: 1,
        text: "stale",
      });
    });
    expect(hook.result.current.state.draft).toBeNull();
  });

  it("retries the same failed turn without creating a second user question", async () => {
    const failed = completedTurn({
      status: "interrupted",
      final_answer: null,
      citations: [],
      citation_ranks: [],
      error: "Stopped.",
      completed_at: null,
    });
    vi.mocked(getChat).mockResolvedValue(chatDetail(CHAT_A, [failed]));
    vi.mocked(streamRetry).mockImplementation(
      (_chatId, _turnId, _handlers, signal) =>
        new Promise((_resolve, reject) => {
          signal.addEventListener("abort", () => reject(signal.reason));
        }),
    );
    const hook = renderHook(() =>
      useChatWorkspace({ initialChatId: CHAT_A, invalidChatRoute: false }),
    );
    await waitFor(() => expect(hook.result.current.state.phase).toBe("interrupted"));
    act(() => {
      void hook.result.current.retry();
    });
    await waitFor(() => expect(streamRetry).toHaveBeenCalled());
    expect(streamRetry).toHaveBeenCalledWith(
      CHAT_A,
      TURN_A,
      expect.anything(),
      expect.any(AbortSignal),
    );
    expect(hook.result.current.state.draft).toEqual(
      expect.objectContaining({ isRetry: true, question: failed.question }),
    );
  });

  it("applies rename and active deletion only after each server mutation succeeds", async () => {
    let finishRename!: (value: ReturnType<typeof chatSummary>) => void;
    vi.mocked(getChat)
      .mockResolvedValueOnce(chatDetail())
      .mockResolvedValueOnce({ ...chatDetail(), title: "Renamed" });
    vi.mocked(listChats)
      .mockResolvedValueOnce([chatSummary()])
      .mockResolvedValueOnce([chatSummary(CHAT_A, "Renamed")])
      .mockResolvedValueOnce([]);
    vi.mocked(renameChat).mockReturnValue(
      new Promise((resolve) => {
        finishRename = resolve;
      }),
    );
    vi.mocked(deleteChat).mockResolvedValue(undefined);
    const hook = renderHook(() =>
      useChatWorkspace({ initialChatId: CHAT_A, invalidChatRoute: false }),
    );
    await waitFor(() => expect(hook.result.current.state.detail).not.toBeNull());
    let renamePromise!: Promise<boolean>;
    act(() => {
      renamePromise = hook.result.current.rename(CHAT_A, "Renamed");
    });
    expect(hook.result.current.state.detail?.title).toBe("New chat");
    finishRename(chatSummary(CHAT_A, "Renamed"));
    await act(() => renamePromise);
    expect(hook.result.current.state.detail?.title).toBe("Renamed");

    await act(() => hook.result.current.remove(CHAT_A));
    expect(hook.result.current.state.activeChatId).toBeNull();
    expect(navigation.replace).toHaveBeenCalledWith("/");
    expect(navigation.push).not.toHaveBeenCalledWith(
      expect.stringMatching(/chat=/),
    );
  });

  it("fences late list responses behind newer authoritative refreshes", async () => {
    const initial = deferred<ReturnType<typeof chatSummary>[]>();
    vi.mocked(listChats)
      .mockReturnValueOnce(initial.promise)
      .mockResolvedValueOnce([chatSummary(CHAT_A, "Renamed")]);
    vi.mocked(getChat)
      .mockResolvedValueOnce(chatDetail())
      .mockResolvedValueOnce({ ...chatDetail(), title: "Renamed" });
    vi.mocked(renameChat).mockResolvedValue(chatSummary(CHAT_A, "Renamed"));
    const hook = renderHook(() =>
      useChatWorkspace({ initialChatId: CHAT_A, invalidChatRoute: false }),
    );
    await waitFor(() => expect(hook.result.current.state.detail).not.toBeNull());

    await act(() => hook.result.current.rename(CHAT_A, "Renamed"));
    expect(hook.result.current.state.chats[0].title).toBe("Renamed");

    await act(async () => {
      initial.resolve([chatSummary(CHAT_A, "Stale")]);
      await initial.promise;
    });
    expect(hook.result.current.state.chats[0].title).toBe("Renamed");
  });

  it("keeps stale detail on same-chat refresh failure", async () => {
    vi.mocked(getChat)
      .mockResolvedValueOnce(chatDetail())
      .mockRejectedValueOnce(new ApiError("Detail refresh failed"));
    const hook = renderHook(() =>
      useChatWorkspace({ initialChatId: CHAT_A, invalidChatRoute: false }),
    );
    await waitFor(() => expect(hook.result.current.state.detail).not.toBeNull());

    await act(() => hook.result.current.loadDetail(CHAT_A));
    expect(hook.result.current.state.detail?.chat_id).toBe(CHAT_A);
    expect(hook.result.current.state.error).toBe("Detail refresh failed");
  });

  it("preserves ready detail and blocks sending during a same-chat refresh", async () => {
    const refresh = deferred<ReturnType<typeof chatDetail>>();
    vi.mocked(getChat)
      .mockResolvedValueOnce(chatDetail())
      .mockReturnValueOnce(refresh.promise);
    const hook = renderHook(() =>
      useChatWorkspace({ initialChatId: CHAT_A, invalidChatRoute: false }),
    );
    await waitFor(() => expect(hook.result.current.state.phase).toBe("ready"));

    let refreshing!: Promise<ReturnType<typeof chatDetail> | null>;
    act(() => {
      refreshing = hook.result.current.loadDetail(CHAT_A);
    });
    expect(hook.result.current.state.detailRefreshing).toBe(true);
    expect(hook.result.current.state.phase).toBe("ready");
    expect(hook.result.current.state.detail?.chat_id).toBe(CHAT_A);
    await expect(hook.result.current.send("Blocked question")).resolves.toBe(
      false,
    );
    expect(streamMessage).not.toHaveBeenCalled();

    await act(async () => {
      refresh.resolve({ ...chatDetail(), title: "Refreshed" });
      await refreshing;
    });
    expect(hook.result.current.state.detailRefreshing).toBe(false);
    expect(hook.result.current.state.detail?.title).toBe("Refreshed");
  });

  it("refreshes the authoritative list on sources and fences the stale list", async () => {
    const staleList = deferred<ReturnType<typeof chatSummary>[]>();
    let handlers!: ChatStreamHandlers;
    vi.mocked(listChats)
      .mockReturnValueOnce(staleList.promise)
      .mockResolvedValueOnce([chatSummary(CHAT_A, "Generated title")]);
    vi.mocked(getChat).mockResolvedValue(chatDetail());
    vi.mocked(streamMessage).mockImplementation(
      (_chatId, _question, nextHandlers, signal) =>
        new Promise((_resolve, reject) => {
          handlers = nextHandlers;
          signal.addEventListener("abort", () => reject(signal.reason));
        }),
    );
    const hook = renderHook(() =>
      useChatWorkspace({ initialChatId: CHAT_A, invalidChatRoute: false }),
    );
    await waitFor(() => expect(hook.result.current.state.detail).not.toBeNull());
    act(() => {
      void hook.result.current.send("Title this conversation");
    });
    await waitFor(() => expect(streamMessage).toHaveBeenCalledOnce());

    act(() => {
      handlers.onSources({
        chat_id: CHAT_A,
        turn_id: TURN_A,
        seq: 1,
        sources: [streamSource],
      });
    });
    await waitFor(() =>
      expect(hook.result.current.state.chats[0]?.title).toBe("Generated title"),
    );
    await act(async () => {
      staleList.resolve([chatSummary(CHAT_A, "Stale title")]);
      await staleList.promise;
    });
    expect(hook.result.current.state.chats[0]?.title).toBe("Generated title");
  });

  it("refetches list and active detail after rename and delete conflicts", async () => {
    vi.mocked(getChat)
      .mockResolvedValueOnce(chatDetail())
      .mockResolvedValueOnce({ ...chatDetail(), scope_version: 2 })
      .mockResolvedValueOnce({ ...chatDetail(), scope_version: 3 });
    vi.mocked(listChats).mockResolvedValue([chatSummary()]);
    vi.mocked(renameChat).mockRejectedValue(
      new ApiError("Generation is active.", 409),
    );
    vi.mocked(deleteChat).mockRejectedValue(
      new ApiError("Generation is active.", 409),
    );
    const hook = renderHook(() =>
      useChatWorkspace({ initialChatId: CHAT_A, invalidChatRoute: false }),
    );
    await waitFor(() => expect(hook.result.current.state.detail).not.toBeNull());

    await expect(
      act(() => hook.result.current.rename(CHAT_A, "Blocked")),
    ).resolves.toBe(false);
    expect(hook.result.current.state.detail?.scope_version).toBe(2);
    await expect(
      act(() => hook.result.current.remove(CHAT_A)),
    ).resolves.toBe(false);
    expect(hook.result.current.state.detail?.scope_version).toBe(3);
    expect(hook.result.current.state.activeChatId).toBe(CHAT_A);
    expect(listChats).toHaveBeenCalledTimes(3);
    expect(getChat).toHaveBeenCalledTimes(3);
  });

  it("preserves a completion race when Stop observes an already-complete turn", async () => {
    vi.mocked(getChat)
      .mockResolvedValueOnce(chatDetail())
      .mockResolvedValueOnce(chatDetail(CHAT_A, [completedTurn()]));
    vi.mocked(listChats)
      .mockResolvedValueOnce([chatSummary()])
      .mockRejectedValueOnce(new ApiError("List refresh failed"));
    vi.mocked(streamMessage).mockImplementation(
      (_chatId, _question, _handlers, signal) =>
        new Promise((_resolve, reject) => {
          signal.addEventListener("abort", () => reject(signal.reason));
        }),
    );
    const hook = renderHook(() =>
      useChatWorkspace({ initialChatId: CHAT_A, invalidChatRoute: false }),
    );
    await waitFor(() => expect(hook.result.current.state.detail).not.toBeNull());
    act(() => {
      void hook.result.current.send("Question");
    });
    await waitFor(() => expect(streamMessage).toHaveBeenCalled());
    await act(() => hook.result.current.stop());
    expect(hook.result.current.state.phase).toBe("verified");
    expect(hook.result.current.state.detail?.turns[0].status).toBe("complete");
    expect(hook.result.current.state.chatsError).toBe("List refresh failed");
  });

  it("polls the full 0/250/500/1000/2000/4000 stop schedule before timing out", async () => {
    const generatingTurn = completedTurn({
      status: "generating",
      final_answer: null,
      citations: [],
      citation_ranks: [],
      completed_at: null,
    });
    vi.mocked(getChat)
      .mockResolvedValueOnce(chatDetail())
      .mockResolvedValue(chatDetail(CHAT_A, [generatingTurn]));
    vi.mocked(streamMessage).mockImplementation(
      (_chatId, _question, _handlers, signal) =>
        new Promise((_resolve, reject) => {
          signal.addEventListener("abort", () => reject(signal.reason));
        }),
    );
    const hook = renderHook(() =>
      useChatWorkspace({ initialChatId: CHAT_A, invalidChatRoute: false }),
    );
    await waitFor(() => expect(hook.result.current.state.detail).not.toBeNull());
    act(() => {
      void hook.result.current.send("Question");
    });
    await waitFor(() => expect(streamMessage).toHaveBeenCalled());

    vi.useFakeTimers();
    let stopped!: Promise<void>;
    act(() => {
      stopped = hook.result.current.stop();
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(7_750);
      await stopped;
    });
    expect(getChat).toHaveBeenCalledTimes(7);
    expect(hook.result.current.state.phase).toBe("stopping");
    expect(hook.result.current.state.error).toMatch(/still stopping/i);

    const refresh = deferred<ReturnType<typeof chatDetail>>();
    vi.mocked(getChat).mockReturnValueOnce(refresh.promise);
    let refreshing!: Promise<ReturnType<typeof chatDetail> | null>;
    act(() => {
      refreshing = hook.result.current.loadDetail(CHAT_A);
    });
    expect(hook.result.current.state.detailRefreshing).toBe(true);
    expect(hook.result.current.state.phase).toBe("stopping");
    expect(hook.result.current.state.draft).not.toBeNull();
    await expect(hook.result.current.send("Too soon")).resolves.toBe(false);
    await act(async () => {
      refresh.resolve(
        chatDetail(CHAT_A, [
          completedTurn({
            status: "interrupted",
            final_answer: null,
            citations: [],
            citation_ranks: [],
            completed_at: null,
          }),
        ]),
      );
      await refreshing;
    });
    expect(hook.result.current.state.detailRefreshing).toBe(false);
  });

  it("retains a conflicting scope editor workflow while refetching authoritative scope", async () => {
    const refreshed = {
      ...chatDetail(),
      scope_mode: "selected" as const,
      scope_version: 2,
      scope_node_ids: ["66666666-6666-4666-8666-666666666666"],
    };
    vi.mocked(getChat)
      .mockResolvedValueOnce(chatDetail())
      .mockResolvedValueOnce(refreshed);
    vi.mocked(saveChatScope).mockRejectedValue(
      new ApiError("Generation is active.", 409),
    );
    const hook = renderHook(() =>
      useChatWorkspace({ initialChatId: CHAT_A, invalidChatRoute: false }),
    );
    await waitFor(() => expect(hook.result.current.state.detail).not.toBeNull());

    await expect(
      hook.result.current.saveScope("selected", refreshed.scope_node_ids),
    ).rejects.toMatchObject({ status: 409 });
    await waitFor(() =>
      expect(hook.result.current.state.detail?.scope_version).toBe(2),
    );
    expect(getChat).toHaveBeenCalledTimes(2);
  });

  it("restores a deep-linked chat when the server route prop changes", async () => {
    vi.mocked(getChat).mockResolvedValue(chatDetail(CHAT_B));
    const initialProps: { chatId: string | null } = { chatId: null };
    const hook = renderHook(
      ({ chatId }: { chatId: string | null }) =>
        useChatWorkspace({
          initialChatId: chatId,
          invalidChatRoute: false,
        }),
      { initialProps },
    );
    expect(hook.result.current.state.phase).toBe("chooser");

    hook.rerender({ chatId: CHAT_B });
    await waitFor(() =>
      expect(hook.result.current.state.detail?.chat_id).toBe(CHAT_B),
    );
    expect(getChat).toHaveBeenCalledWith(CHAT_B, expect.any(AbortSignal));
  });

  it("refetches after a pre-stream failure and exposes the persisted failed turn", async () => {
    const failed = completedTurn({
      status: "failed",
      final_answer: null,
      citations: [],
      citation_ranks: [],
      error: "Model unavailable.",
      completed_at: null,
    });
    vi.mocked(getChat)
      .mockResolvedValueOnce(chatDetail())
      .mockResolvedValueOnce(chatDetail(CHAT_A, [failed]));
    vi.mocked(streamMessage).mockRejectedValue(
      new ApiError("Model unavailable.", 503),
    );
    const hook = renderHook(() =>
      useChatWorkspace({ initialChatId: CHAT_A, invalidChatRoute: false }),
    );
    await waitFor(() => expect(hook.result.current.state.detail).not.toBeNull());
    act(() => {
      void hook.result.current.send("Question");
    });
    await waitFor(() =>
      expect(hook.result.current.state.detail?.turns[0]?.status).toBe("failed"),
    );
    expect(hook.result.current.state.phase).toBe("failed");
    expect(hook.result.current.state.error).toBe("Model unavailable.");
  });
});
