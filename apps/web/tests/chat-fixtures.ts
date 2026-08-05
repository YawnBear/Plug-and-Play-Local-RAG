import type {
  ChatDetail,
  ChatSummary,
  ChatTurn,
  HistoricalSource,
  StreamSource,
} from "@/features/chats/contracts";

export const CHAT_A = "11111111-1111-4111-8111-111111111111";
export const CHAT_B = "22222222-2222-4222-8222-222222222222";
export const TURN_A = "33333333-3333-4333-8333-333333333333";
export const DOC_A = "44444444-4444-4444-8444-444444444444";
export const CHUNK_A = "55555555-5555-4555-8555-555555555555";
export const NODE_A = "66666666-6666-4666-8666-666666666666";
export const timestamp = "2026-07-23T00:00:00Z";

export function chatSummary(
  chatId = CHAT_A,
  title = "New chat",
): ChatSummary {
  return {
    chat_id: chatId,
    title,
    title_is_manual: false,
    scope_mode: "all_ready",
    scope_version: 1,
    created_at: timestamp,
    updated_at: timestamp,
  };
}

export const streamSource: StreamSource = {
  label: "S1",
  rank: 1,
  document_id: DOC_A,
  chunk_id: CHUNK_A,
  document_id_snapshot: DOC_A,
  chunk_id_snapshot: CHUNK_A,
  filename: "original.pdf",
  display_name: "Research.pdf",
  logical_path: "/Research.pdf",
  page_start: 2,
  page_end: 2,
  section: "Findings",
  source_available: true,
};

export const historicalSource: HistoricalSource = {
  ...streamSource,
  source_sha256: "a".repeat(64),
  text_sha256: "b".repeat(64),
  retrieval_distance: 0.1,
  rerank_score: 2.5,
};

export function completedTurn(
  overrides: Partial<ChatTurn> = {},
): ChatTurn {
  return {
    turn_id: TURN_A,
    ordinal: 1,
    question: "What was found?",
    status: "complete",
    attempt: 1,
    scope_version: 1,
    final_answer: "The verified finding [S1].",
    partial_answer: null,
    insufficient_context: false,
    error: null,
    sources: [historicalSource],
    citations: [historicalSource],
    citation_ranks: [1],
    created_at: timestamp,
    updated_at: timestamp,
    completed_at: timestamp,
    ...overrides,
  };
}

export function chatDetail(
  chatId = CHAT_A,
  turns: ChatTurn[] = [],
): ChatDetail {
  return {
    ...chatSummary(chatId),
    scope_node_ids: [],
    turns,
    page: 1,
    limit: 50,
    total: turns.length,
  };
}
