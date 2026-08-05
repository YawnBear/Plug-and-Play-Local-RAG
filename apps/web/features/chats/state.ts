import type {
  ChatDetail,
  ChatSummary,
  FinalEvent,
  StreamStatusPhase,
  StreamSource,
} from "./contracts";

export type WorkspacePhase =
  | "chooser"
  | "loading"
  | "ready"
  | "starting"
  | "streaming-draft"
  | "stopping"
  | "verified"
  | "failed"
  | "interrupted"
  | "length-limited"
  | "citation-failed"
  | "access-revoked"
  | "recovering";

export interface DraftTurn {
  question: string;
  isRetry: boolean;
  turnId: string | null;
  sources: StreamSource[];
  activity: DraftActivityEntry[];
  reasoningActive: boolean;
  reasoningComplete: boolean;
  reasoningTruncated: boolean;
  text: string;
  final: FinalEvent | null;
}

export type DraftActivityEntry =
  | { kind: "progress"; phase: StreamStatusPhase }
  | { kind: "thinking"; text: string };

export interface ChatWorkspaceState {
  phase: WorkspacePhase;
  chats: ChatSummary[];
  chatsLoading: boolean;
  chatsError: string | null;
  detailRefreshing: boolean;
  activeChatId: string | null;
  detail: ChatDetail | null;
  draft: DraftTurn | null;
  error: string | null;
  routeNotice: string | null;
}

export function initialChatWorkspaceState(
  chatId: string | null,
  invalidChatRoute: boolean,
): ChatWorkspaceState {
  return {
    phase: chatId ? "loading" : "chooser",
    chats: [],
    chatsLoading: true,
    chatsError: null,
    detailRefreshing: false,
    activeChatId: chatId,
    detail: null,
    draft: null,
    error: null,
    routeNotice: invalidChatRoute
      ? "That conversation link is invalid. Choose a conversation or start a new one."
      : null,
  };
}

export type ChatWorkspaceAction =
  | { type: "route"; chatId: string | null; invalid: boolean }
  | { type: "chat-created"; detail: ChatDetail }
  | { type: "chats-loading" }
  | { type: "chats-loaded"; chats: ChatSummary[] }
  | { type: "chats-failed"; message: string }
  | { type: "detail-loading"; chatId: string }
  | { type: "detail-loaded"; detail: ChatDetail; phase?: WorkspacePhase }
  | { type: "detail-reconciled"; detail: ChatDetail }
  | { type: "detail-failed"; message: string }
  | { type: "missing-chat"; message: string }
  | { type: "start"; question: string; retry: boolean }
  | { type: "status"; phase: StreamStatusPhase }
  | { type: "sources"; turnId: string; sources: StreamSource[] }
  | { type: "reasoning-start" }
  | { type: "reasoning-delta"; text: string }
  | { type: "reasoning-end"; truncated: boolean }
  | { type: "answer-reset" }
  | { type: "token"; text: string }
  | { type: "stream-final"; final: FinalEvent }
  | { type: "stopping" }
  | { type: "stop-timeout"; message: string }
  | { type: "recovering" }
  | { type: "stream-failed"; message: string; interrupted?: boolean }
  | { type: "clear-error" };

export function chatWorkspaceReducer(
  state: ChatWorkspaceState,
  action: ChatWorkspaceAction,
): ChatWorkspaceState {
  switch (action.type) {
    case "chat-created":
      return {
        ...state,
        activeChatId: action.detail.chat_id,
        detail: action.detail,
        draft: null,
        error: null,
        routeNotice: null,
        phase: "ready",
        detailRefreshing: false,
      };
    case "route":
      return {
        ...state,
        activeChatId: action.chatId,
        detail: action.chatId === state.activeChatId ? state.detail : null,
        draft: null,
        error: null,
        routeNotice: action.invalid
          ? "That conversation link is invalid. Choose a conversation or start a new one."
          : null,
        phase: action.chatId ? "loading" : "chooser",
        detailRefreshing: false,
      };
    case "chats-loading":
      return { ...state, chatsLoading: true, chatsError: null };
    case "chats-loaded":
      return {
        ...state,
        chats: action.chats,
        chatsLoading: false,
        chatsError: null,
      };
    case "chats-failed":
      return {
        ...state,
        chatsLoading: false,
        chatsError: action.message,
      };
    case "detail-loading":
      if (action.chatId === state.activeChatId && state.detail !== null) {
        return { ...state, detailRefreshing: true };
      }
      return {
        ...state,
        activeChatId: action.chatId,
        detail:
          action.chatId === state.activeChatId ? state.detail : null,
        draft: null,
        error: null,
        phase: "loading",
        detailRefreshing: false,
      };
    case "detail-loaded":
      return {
        ...state,
        activeChatId: action.detail.chat_id,
        detail: action.detail,
        draft: null,
        error: null,
        routeNotice: null,
        phase: action.phase ?? "ready",
        detailRefreshing: false,
      };
    case "detail-reconciled":
      return {
        ...state,
        activeChatId: action.detail.chat_id,
        detail: action.detail,
        draft: state.draft?.final ? state.draft : null,
        error: null,
        routeNotice: null,
        phase: "verified",
        detailRefreshing: false,
      };
    case "detail-failed":
      return {
        ...state,
        phase: "failed",
        error: action.message,
        detailRefreshing: false,
      };
    case "missing-chat":
      return {
        ...state,
        activeChatId: null,
        detail: null,
        draft: null,
        phase: "chooser",
        error: null,
        routeNotice: action.message,
        detailRefreshing: false,
      };
    case "start":
      return {
        ...state,
        phase: "starting",
        error: null,
        draft: {
          question: action.question,
          isRetry: action.retry,
          turnId: null,
          sources: [],
          activity: [],
          reasoningActive: false,
          reasoningComplete: false,
          reasoningTruncated: false,
          text: "",
          final: null,
        },
        detailRefreshing: false,
      };
    case "status":
      if (!state.draft) return state;
      return {
        ...state,
        phase:
          action.phase === "streaming_answer"
            ? "streaming-draft"
            : state.phase,
        draft: {
          ...state.draft,
          activity: [
            ...state.draft.activity,
            { kind: "progress", phase: action.phase },
          ],
        },
      };
    case "sources":
      if (!state.draft) return state;
      return {
        ...state,
        phase: "streaming-draft",
        draft: {
          ...state.draft,
          turnId: action.turnId,
          sources: action.sources,
        },
      };
    case "reasoning-start":
      if (!state.draft || state.draft.reasoningActive) return state;
      return {
        ...state,
        draft: {
          ...state.draft,
          activity: [
            ...state.draft.activity,
            { kind: "thinking", text: "" },
          ],
          reasoningActive: true,
        },
      };
    case "reasoning-delta":
      if (!state.draft || !state.draft.reasoningActive) return state;
      return {
        ...state,
        draft: {
          ...state.draft,
          activity: state.draft.activity.map((entry, index, entries) =>
            index === entries.length - 1 && entry.kind === "thinking"
              ? { ...entry, text: entry.text + action.text }
              : entry,
          ),
        },
      };
    case "reasoning-end":
      if (!state.draft || !state.draft.reasoningActive) return state;
      return {
        ...state,
        draft: {
          ...state.draft,
          reasoningActive: false,
          reasoningComplete: true,
          reasoningTruncated: action.truncated,
        },
      };
    case "token":
      if (!state.draft) return state;
      return {
        ...state,
        phase: "streaming-draft",
        draft: { ...state.draft, text: state.draft.text + action.text },
      };
    case "answer-reset":
      if (!state.draft) return state;
      return {
        ...state,
        phase: "streaming-draft",
        draft: { ...state.draft, text: "" },
      };
    case "stream-final":
      if (!state.draft) return state;
      return {
        ...state,
        phase: "recovering",
        draft: { ...state.draft, final: action.final },
      };
    case "stopping":
      return { ...state, phase: "stopping", error: null };
    case "stop-timeout":
      return { ...state, phase: "stopping", error: action.message };
    case "recovering":
      return { ...state, phase: "recovering" };
    case "stream-failed":
      return {
        ...state,
        phase: action.interrupted ? "interrupted" : "failed",
        error: action.message,
      };
    case "clear-error":
      return { ...state, error: null, routeNotice: null };
  }
}

export function codePointLength(value: string): number {
  return Array.from(value).length;
}

export function validateQuestion(value: string):
  | { valid: true; question: string }
  | { valid: false; message: string } {
  const question = value.trim();
  const length = codePointLength(question);
  if (length === 0) {
    return { valid: false, message: "Enter a question before sending." };
  }
  if (length > 2_000) {
    return {
      valid: false,
      message: "Questions must be 2,000 characters or fewer.",
    };
  }
  return { valid: true, question };
}

export function terminalPhase(detail: ChatDetail): WorkspacePhase {
  const latest = detail.turns.at(-1);
  if (!latest) return "ready";
  if (latest.status === "complete") return "verified";
  if (latest.status === "interrupted") return "interrupted";
  if (latest.status === "length_limited") return "length-limited";
  if (latest.status === "citation_failed") return "citation-failed";
  if (latest.status === "failed") return "failed";
  if (latest.status === "access_revoked") return "access-revoked";
  return "recovering";
}
