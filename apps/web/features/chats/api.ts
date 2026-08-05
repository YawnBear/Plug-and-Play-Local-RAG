import {
  apiFetch,
  jsonRequest,
  requestJson,
  requestVoid,
} from "@/lib/http";
import { requireUuid } from "@/lib/uuid";

import {
  chatDetailSchema,
  chatListSchema,
  chatScopeSchema,
  chatSummarySchema,
  citationEvidenceSchema,
  type ChatDetail,
  type ChatScope,
  type ChatSummary,
  type CitationEvidence,
  type FinalEvent,
} from "./contracts";
import {
  consumeChatSse,
  type ChatStreamHandlers,
} from "./sse";

export async function listChats(signal?: AbortSignal): Promise<ChatSummary[]> {
  return requestJson("/api/chats", chatListSchema, {
    cache: "no-store",
    signal,
  });
}

export async function createChat(
  title?: string,
  signal?: AbortSignal,
): Promise<ChatSummary> {
  return requestJson(
    "/api/chats",
    chatSummarySchema,
    jsonRequest(title === undefined ? {} : { title }, {
      method: "POST",
      signal,
    }),
  );
}

export async function getChat(
  chatId: string,
  signal?: AbortSignal,
  page = 1,
  limit = 50,
): Promise<ChatDetail> {
  return requestJson(
    `/api/chats/${requireUuid(chatId, "chat")}?page=${page}&limit=${limit}`,
    chatDetailSchema,
    { cache: "no-store", signal },
  );
}

export async function getCitationEvidence(
  chatId: string,
  turnId: string,
  label: string,
  signal?: AbortSignal,
): Promise<CitationEvidence> {
  if (!/^S[1-8]$/.test(label)) {
    throw new TypeError("Citation label must be S1 through S8.");
  }
  return requestJson(
    `/api/chats/${requireUuid(chatId, "chat")}/turns/${requireUuid(
      turnId,
      "turn",
    )}/citations/${label}/evidence`,
    citationEvidenceSchema,
    { cache: "no-store", signal },
  );
}

export async function renameChat(
  chatId: string,
  title: string,
  signal?: AbortSignal,
): Promise<ChatSummary> {
  return requestJson(
    `/api/chats/${requireUuid(chatId, "chat")}`,
    chatSummarySchema,
    jsonRequest({ title }, { method: "PATCH", signal }),
  );
}

export async function deleteChat(
  chatId: string,
  signal?: AbortSignal,
): Promise<void> {
  return requestVoid(`/api/chats/${requireUuid(chatId, "chat")}`, {
    method: "DELETE",
    signal,
  });
}

export async function saveChatScope(
  chatId: string,
  scope: { mode: "all_ready" | "selected"; node_ids: string[] },
  signal?: AbortSignal,
): Promise<ChatScope> {
  const nodeIds = scope.node_ids.map((nodeId) => requireUuid(nodeId, "scope node"));
  return requestJson(
    `/api/chats/${requireUuid(chatId, "chat")}/scope`,
    chatScopeSchema,
    jsonRequest(
      { mode: scope.mode, node_ids: nodeIds },
      { method: "PUT", signal },
    ),
  );
}

async function stream(
  path: string,
  expected: { chatId: string; turnId?: string },
  body: unknown | undefined,
  handlers: ChatStreamHandlers,
  signal: AbortSignal,
): Promise<FinalEvent> {
  const headers = new Headers({ Accept: "text/event-stream" });
  let init: RequestInit = { method: "POST", headers, signal };
  if (body !== undefined) init = jsonRequest(body, init);
  const response = await apiFetch(path, init);
  const contentType = response.headers.get("content-type")?.toLowerCase() ?? "";
  if (!contentType.includes("text/event-stream")) {
    throw new TypeError("The server did not return an event stream.");
  }
  return consumeChatSse(response, expected, handlers, signal);
}

export async function streamMessage(
  chatId: string,
  question: string,
  handlers: ChatStreamHandlers,
  signal: AbortSignal,
): Promise<FinalEvent> {
  const normalizedChatId = requireUuid(chatId, "chat");
  return stream(
    `/api/chats/${normalizedChatId}/messages/stream`,
    { chatId: normalizedChatId },
    { question },
    handlers,
    signal,
  );
}

export async function streamRetry(
  chatId: string,
  turnId: string,
  handlers: ChatStreamHandlers,
  signal: AbortSignal,
): Promise<FinalEvent> {
  const normalizedChatId = requireUuid(chatId, "chat");
  const normalizedTurnId = requireUuid(turnId, "turn");
  return stream(
    `/api/chats/${normalizedChatId}/turns/${normalizedTurnId}/retry/stream`,
    { chatId: normalizedChatId, turnId: normalizedTurnId },
    undefined,
    handlers,
    signal,
  );
}
