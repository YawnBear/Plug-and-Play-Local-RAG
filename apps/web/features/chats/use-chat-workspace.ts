"use client";

import { useRouter } from "next/navigation";
import {
  useCallback,
  useEffect,
  useReducer,
  useRef,
  useState,
} from "react";

import { ApiError } from "@/lib/http";
import { homeRoute } from "@/lib/route-state";

import {
  createChat,
  deleteChat,
  getChat,
  listChats,
  renameChat,
  saveChatScope,
  streamMessage,
  streamRetry,
} from "./api";
import type {
  ChatDetail,
  ChatSummary,
  FinalEvent,
  ReasoningDeltaEvent,
  ReasoningEndEvent,
  SourcesEvent,
  StatusEvent,
  TokenEvent,
} from "./contracts";
import {
  chatWorkspaceReducer,
  initialChatWorkspaceState,
  terminalPhase,
  validateQuestion,
} from "./state";

const STOP_DELAYS = [0, 250, 500, 1_000, 2_000, 4_000] as const;

function messageOf(error: unknown): string {
  return error instanceof Error ? error.message : "An unexpected error occurred.";
}

function isAbort(error: unknown): boolean {
  return (
    (error instanceof DOMException && error.name === "AbortError") ||
    (typeof error === "object" &&
      error !== null &&
      "name" in error &&
      error.name === "AbortError")
  );
}

function delay(milliseconds: number): Promise<void> {
  return new Promise((resolve) => window.setTimeout(resolve, milliseconds));
}

export interface UseChatWorkspaceOptions {
  initialChatId: string | null;
  invalidChatRoute: boolean;
}

export function useChatWorkspace({
  initialChatId,
  invalidChatRoute,
}: UseChatWorkspaceOptions) {
  const router = useRouter();
  const [state, dispatch] = useReducer(
    chatWorkspaceReducer,
    undefined,
    () => initialChatWorkspaceState(initialChatId, invalidChatRoute),
  );
  const [actionError, setActionError] = useState<string | null>(null);
  const stateRef = useRef(state);
  const chatsRequest = useRef(0);
  const detailRequest = useRef(0);
  const detailRefreshing = useRef(false);
  const streamRequest = useRef(0);
  const detailController = useRef<AbortController | null>(null);
  const streamController = useRef<AbortController | null>(null);
  const firstSendInFlight = useRef(false);
  const previousRoute = useRef({ initialChatId, invalidChatRoute });

  useEffect(() => {
    stateRef.current = state;
  }, [state]);

  const refreshChats = useCallback(async (signal?: AbortSignal) => {
    const request = ++chatsRequest.current;
    dispatch({ type: "chats-loading" });
    try {
      const chats = await listChats(signal);
      if (request !== chatsRequest.current) return null;
      dispatch({ type: "chats-loaded", chats });
      return chats;
    } catch (error) {
      if (isAbort(error) || request !== chatsRequest.current) return null;
      dispatch({ type: "chats-failed", message: messageOf(error) });
      return null;
    }
  }, []);

  const loadDetail = useCallback(
    async (chatId: string, phase?: "ready" | "verified") => {
      const preserving =
        stateRef.current.activeChatId === chatId &&
        stateRef.current.detail !== null;
      if (preserving) detailRefreshing.current = true;
      detailController.current?.abort();
      const controller = new AbortController();
      detailController.current = controller;
      const request = ++detailRequest.current;
      dispatch({ type: "detail-loading", chatId });
      try {
        const detail = await getChat(chatId, controller.signal);
        if (
          request !== detailRequest.current ||
          stateRef.current.activeChatId !== chatId
        ) {
          return null;
        }
        dispatch({
          type: "detail-loaded",
          detail,
          phase: phase ?? terminalPhase(detail),
        });
        detailRefreshing.current = false;
        return detail;
      } catch (error) {
        if (isAbort(error) || request !== detailRequest.current) return null;
        detailRefreshing.current = false;
        if (error instanceof ApiError && error.status === 404) {
          dispatch({
            type: "missing-chat",
            message:
              "That conversation no longer exists. Choose another conversation or start a new one.",
          });
          router.replace(homeRoute());
          return null;
        }
        dispatch({ type: "detail-failed", message: messageOf(error) });
        return null;
      }
    },
    [router],
  );

  useEffect(() => {
    const controller = new AbortController();
    void refreshChats(controller.signal);
    return () => controller.abort();
  }, [refreshChats]);

  useEffect(() => {
    const previous = previousRoute.current;
    if (
      previous.initialChatId === initialChatId &&
      previous.invalidChatRoute === invalidChatRoute
    ) {
      if (initialChatId && !stateRef.current.detail) {
        void loadDetail(initialChatId);
      }
      return;
    }
    previousRoute.current = { initialChatId, invalidChatRoute };
    streamRequest.current += 1;
    detailRefreshing.current = false;
    streamController.current?.abort();
    detailController.current?.abort();
    dispatch({
      type: "route",
      chatId: initialChatId,
      invalid: invalidChatRoute,
    });
    if (initialChatId) void loadDetail(initialChatId);
  }, [initialChatId, invalidChatRoute, loadDetail]);

  useEffect(
    () => () => {
      chatsRequest.current += 1;
      detailRequest.current += 1;
      streamRequest.current += 1;
      detailController.current?.abort();
      streamController.current?.abort();
    },
    [],
  );

  const openChat = useCallback(
    (chatId: string, replace = false) => {
      if (detailRefreshing.current) return;
      streamRequest.current += 1;
      streamController.current?.abort();
      detailController.current?.abort();
      dispatch({ type: "detail-loading", chatId });
      const href = homeRoute(chatId);
      if (replace) router.replace(href);
      else router.push(href);
      void loadDetail(chatId);
    },
    [loadDetail, router],
  );

  const chooseHome = useCallback(() => {
    streamRequest.current += 1;
    streamController.current?.abort();
    detailController.current?.abort();
    dispatch({ type: "route", chatId: null, invalid: false });
    router.replace(homeRoute());
  }, [router]);

  const activateCreatedChat = useCallback(
    (created: ChatSummary) => {
      const detail: ChatDetail = {
        ...created,
        scope_node_ids: [],
        turns: [],
        page: 1,
        limit: 50,
        total: 0,
      };
      const action = { type: "chat-created" as const, detail };
      stateRef.current = chatWorkspaceReducer(stateRef.current, action);
      dispatch(action);
      previousRoute.current = {
        initialChatId: created.chat_id,
        invalidChatRoute: false,
      };
      router.replace(homeRoute(created.chat_id));
      return detail;
    },
    [router],
  );

  const rename = useCallback(async (chatId: string, title: string) => {
    if (detailRefreshing.current) return false;
    setActionError(null);
    try {
      await renameChat(chatId, title.trim());
      await Promise.all([
        refreshChats(),
        stateRef.current.activeChatId === chatId
          ? loadDetail(chatId)
          : Promise.resolve(null),
      ]);
      return true;
    } catch (error) {
      setActionError(messageOf(error));
      if (error instanceof ApiError && error.status === 409) {
        const activeChatId = stateRef.current.activeChatId;
        await Promise.all([
          refreshChats(),
          activeChatId ? loadDetail(activeChatId) : Promise.resolve(null),
        ]);
      }
      return false;
    }
  }, [loadDetail, refreshChats]);

  const remove = useCallback(
    async (chatId: string) => {
      if (detailRefreshing.current) return false;
      setActionError(null);
      try {
        await deleteChat(chatId);
        const active = stateRef.current.activeChatId === chatId;
        await refreshChats();
        if (active) chooseHome();
        return true;
      } catch (error) {
        setActionError(messageOf(error));
        if (error instanceof ApiError && error.status === 409) {
          const activeChatId = stateRef.current.activeChatId;
          await Promise.all([
            refreshChats(),
            activeChatId ? loadDetail(activeChatId) : Promise.resolve(null),
          ]);
        }
        return false;
      }
    },
    [chooseHome, loadDetail, refreshChats],
  );

  const reconcile = useCallback(
    async (chatId: string, operation: number, preferVerified: boolean) => {
      dispatch({ type: "recovering" });
      const chatsPromise = refreshChats();
      try {
        const detail = await getChat(chatId);
        if (
          operation !== streamRequest.current ||
          stateRef.current.activeChatId !== chatId
        ) {
          return null;
        }
        const phase =
          preferVerified && detail.turns.at(-1)?.status === "complete"
            ? "verified"
            : terminalPhase(detail);
        if (
          phase === "verified" &&
          stateRef.current.draft?.final !== null
        ) {
          dispatch({ type: "detail-reconciled", detail });
        } else {
          dispatch({ type: "detail-loaded", detail, phase });
        }
        await chatsPromise;
        return detail;
      } catch (error) {
        await chatsPromise;
        if (operation === streamRequest.current && !isAbort(error)) {
          dispatch({ type: "stream-failed", message: messageOf(error) });
        }
        return null;
      }
    },
    [refreshChats],
  );

  const runStream = useCallback(
    async (
      chatId: string,
      options:
        | { kind: "message"; question: string }
        | { kind: "retry"; turnId: string; question: string },
    ) => {
      const operation = ++streamRequest.current;
      detailRequest.current += 1;
      detailRefreshing.current = false;
      detailController.current?.abort();
      detailController.current = null;
      streamController.current?.abort();
      const controller = new AbortController();
      streamController.current = controller;
      dispatch({
        type: "start",
        question: options.question,
        retry: options.kind === "retry",
      });

      const current = () =>
        operation === streamRequest.current &&
        stateRef.current.activeChatId === chatId;
      const handlers = {
        onStatus: (event: StatusEvent) => {
          if (current()) dispatch({ type: "status", phase: event.phase });
        },
        onSources: (event: SourcesEvent) => {
          if (current()) {
            dispatch({
              type: "sources",
              turnId: event.turn_id,
              sources: event.sources,
            });
            void refreshChats();
          }
        },
        onReasoningStart: () => {
          if (current()) dispatch({ type: "reasoning-start" });
        },
        onReasoningDelta: (event: ReasoningDeltaEvent) => {
          if (current()) dispatch({ type: "reasoning-delta", text: event.text });
        },
        onReasoningEnd: (event: ReasoningEndEvent) => {
          if (current()) {
            dispatch({
              type: "reasoning-end",
              truncated: event.truncated,
            });
          }
        },
        onAnswerReset: () => {
          if (current()) dispatch({ type: "answer-reset" });
        },
        onToken: (event: TokenEvent) => {
          if (current()) dispatch({ type: "token", text: event.text });
        },
        onFinal: (event: FinalEvent) => {
          if (current()) dispatch({ type: "stream-final", final: event });
        },
      };

      try {
        if (options.kind === "message") {
          await streamMessage(
            chatId,
            options.question,
            handlers,
            controller.signal,
          );
        } else {
          await streamRetry(
            chatId,
            options.turnId,
            handlers,
            controller.signal,
          );
        }
        if (current()) await reconcile(chatId, operation, true);
      } catch (error) {
        if (!current()) return;
        if (isAbort(error) || controller.signal.aborted) return;
        const message = messageOf(error);
        dispatch({ type: "stream-failed", message });
        await reconcile(chatId, operation, false);
        if (current()) {
          dispatch({ type: "stream-failed", message });
        }
      } finally {
        if (streamController.current === controller) {
          streamController.current = null;
        }
      }
    },
    [reconcile, refreshChats],
  );

  const send = useCallback(
    async (value: string) => {
      if (detailRefreshing.current) return false;
      const validated = validateQuestion(value);
      if (!validated.valid) {
        dispatch({ type: "stream-failed", message: validated.message });
        return false;
      }
      const chatId = stateRef.current.activeChatId;
      if (!chatId) {
        if (firstSendInFlight.current) return false;
        firstSendInFlight.current = true;
        setActionError(null);
        try {
          const created = await createChat();
          activateCreatedChat(created);
          void refreshChats();
          void runStream(created.chat_id, {
            kind: "message",
            question: validated.question,
          });
          return true;
        } catch (error) {
          setActionError(messageOf(error));
          return false;
        } finally {
          firstSendInFlight.current = false;
        }
      }
      void runStream(chatId, {
        kind: "message",
        question: validated.question,
      });
      return true;
    },
    [activateCreatedChat, refreshChats, runStream],
  );

  const retry = useCallback(async () => {
    if (detailRefreshing.current) return false;
    const detail = stateRef.current.detail;
    const latest = detail?.turns.at(-1);
    if (
      !detail ||
      !latest ||
      ![
        "failed",
        "interrupted",
        "length_limited",
        "citation_failed",
      ].includes(latest.status)
    ) {
      return false;
    }
    void runStream(detail.chat_id, {
      kind: "retry",
      turnId: latest.turn_id,
      question: latest.question,
    });
    return true;
  }, [runStream]);

  const stop = useCallback(async () => {
    if (detailRefreshing.current) return;
    const chatId = stateRef.current.activeChatId;
    if (!chatId) return;
    const priorLatest = stateRef.current.detail?.turns.at(-1) ?? null;
    const stoppingRetry = stateRef.current.draft?.isRetry ?? false;
    const operation = ++streamRequest.current;
    dispatch({ type: "stopping" });
    streamController.current?.abort(
      new DOMException("Generation stopped by the user.", "AbortError"),
    );
    streamController.current = null;

    for (const wait of STOP_DELAYS) {
      if (wait > 0) await delay(wait);
      if (
        operation !== streamRequest.current ||
        stateRef.current.activeChatId !== chatId
      ) {
        return;
      }
      try {
        const detail = await getChat(chatId);
        const latest = detail.turns.at(-1);
        const observedStoppedTurn =
          latest !== undefined &&
          (stoppingRetry
            ? latest.turn_id === priorLatest?.turn_id &&
              latest.attempt > (priorLatest?.attempt ?? 0)
            : latest.turn_id !== priorLatest?.turn_id);
        if (observedStoppedTurn && latest.status !== "generating") {
          if (
            operation === streamRequest.current &&
            stateRef.current.activeChatId === chatId
          ) {
            dispatch({
              type: "detail-loaded",
              detail,
              phase: terminalPhase(detail),
            });
          }
          await refreshChats();
          return;
        }
      } catch (error) {
        if (operation === streamRequest.current && !isAbort(error)) {
          dispatch({ type: "stream-failed", message: messageOf(error) });
        }
        return;
      }
    }
    if (operation === streamRequest.current) {
      dispatch({
        type: "stop-timeout",
        message:
          "The server is still stopping this answer. Refresh the conversation before retrying.",
      });
    }
  }, [refreshChats]);

  const saveScope = useCallback(
    async (
      mode: "all_ready" | "selected",
      nodeIds: string[],
    ): Promise<ChatDetail> => {
      if (detailRefreshing.current) {
        throw new Error("Wait for the conversation refresh to finish.");
      }
      const detail = stateRef.current.detail;
      if (!detail) throw new Error("No conversation is open.");
      try {
        const scope = await saveChatScope(detail.chat_id, {
          mode,
          node_ids: nodeIds,
        });
        const updated: ChatDetail = {
          ...detail,
          ...scope,
          scope_node_ids: scope.scope_node_ids,
        };
        dispatch({
          type: "detail-loaded",
          detail: updated,
          phase: stateRef.current.phase,
        });
        await refreshChats();
        return updated;
      } catch (error) {
        if (error instanceof ApiError && error.status === 409) {
          try {
            const authoritative = await getChat(detail.chat_id);
            if (stateRef.current.activeChatId === detail.chat_id) {
              dispatch({
                type: "detail-loaded",
                detail: authoritative,
                phase: terminalPhase(authoritative),
              });
            }
          } catch {
            // Keep the original conflict as the actionable mutation failure.
          }
        }
        throw error;
      }
    },
    [refreshChats],
  );

  const patchDetail = useCallback((detail: ChatDetail) => {
    dispatch({ type: "detail-loaded", detail });
  }, []);

  const generating = [
    "starting",
    "streaming-draft",
    "stopping",
    "recovering",
  ].includes(state.phase);

  return {
    state,
    actionError,
    generating,
    openChat,
    rename,
    remove,
    refreshChats,
    loadDetail,
    send,
    retry,
    stop,
    saveScope,
    patchDetail,
    clearActionError: () => setActionError(null),
  };
}

export type ChatWorkspaceController = ReturnType<typeof useChatWorkspace>;
