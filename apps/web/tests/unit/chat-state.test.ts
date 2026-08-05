import { describe, expect, it } from "vitest";

import {
  chatWorkspaceReducer,
  codePointLength,
  initialChatWorkspaceState,
  terminalPhase,
  validateQuestion,
} from "@/features/chats/state";
import { chatDetailSchema } from "@/features/chats/contracts";

import {
  CHAT_A,
  chatDetail,
  completedTurn,
  streamSource,
  TURN_A,
} from "../chat-fixtures";

describe("chat workspace reducer", () => {
  it("keeps streamed content explicitly draft until an authoritative refetch", () => {
    let state = initialChatWorkspaceState(CHAT_A, false);
    state = chatWorkspaceReducer(state, {
      type: "detail-loaded",
      detail: chatDetail(),
    });
    state = chatWorkspaceReducer(state, {
      type: "start",
      question: "Question",
      retry: false,
    });
    state = chatWorkspaceReducer(state, {
      type: "sources",
      turnId: TURN_A,
      sources: [streamSource],
    });
    state = chatWorkspaceReducer(state, {
      type: "status",
      phase: "reasoning",
    });
    state = chatWorkspaceReducer(state, { type: "reasoning-start" });
    state = chatWorkspaceReducer(state, {
      type: "reasoning-delta",
      text: "raw thought",
    });
    state = chatWorkspaceReducer(state, {
      type: "reasoning-end",
      truncated: true,
    });
    state = chatWorkspaceReducer(state, { type: "token", text: "draft" });
    state = chatWorkspaceReducer(state, {
      type: "stream-final",
      final: {
        chat_id: CHAT_A,
        turn_id: TURN_A,
        seq: 1,
        answer: "final",
        insufficient_context: false,
        citations: [streamSource],
      },
    });

    expect(state.phase).toBe("recovering");
    expect(state.draft?.text).toBe("draft");
    expect(state.draft?.activity).toEqual([
      { kind: "progress", phase: "reasoning" },
      { kind: "thinking", text: "raw thought" },
    ]);
    expect(state.draft?.reasoningTruncated).toBe(true);
    expect(state.detail?.turns).toHaveLength(0);
  });

  it("keeps completed Activity only for the current session reconciliation", () => {
    let state = initialChatWorkspaceState(CHAT_A, false);
    state = chatWorkspaceReducer(state, {
      type: "detail-loaded",
      detail: chatDetail(),
    });
    state = chatWorkspaceReducer(state, {
      type: "start",
      question: "Question",
      retry: false,
    });
    state = chatWorkspaceReducer(state, { type: "reasoning-start" });
    state = chatWorkspaceReducer(state, {
      type: "reasoning-delta",
      text: "ephemeral thought",
    });
    state = chatWorkspaceReducer(state, {
      type: "reasoning-end",
      truncated: false,
    });
    state = chatWorkspaceReducer(state, {
      type: "stream-final",
      final: {
        chat_id: CHAT_A,
        turn_id: TURN_A,
        seq: 1,
        answer: "final",
        insufficient_context: false,
        citations: [],
      },
    });
    state = chatWorkspaceReducer(state, {
      type: "detail-reconciled",
      detail: chatDetail(CHAT_A, [completedTurn()]),
    });

    expect(state.phase).toBe("verified");
    expect(state.draft?.activity).toContainEqual({
      kind: "thinking",
      text: "ephemeral thought",
    });

    state = chatWorkspaceReducer(state, {
      type: "route",
      chatId: null,
      invalid: false,
    });
    expect(state.draft).toBeNull();
  });

  it("replaces rather than appends the draft during citation repair", () => {
    let state = initialChatWorkspaceState(CHAT_A, false);
    state = chatWorkspaceReducer(state, {
      type: "start",
      question: "Question",
      retry: false,
    });
    state = chatWorkspaceReducer(state, { type: "token", text: "Uncited draft" });
    state = chatWorkspaceReducer(state, { type: "answer-reset" });
    state = chatWorkspaceReducer(state, {
      type: "token",
      text: "Repaired draft [S1]",
    });

    expect(state.draft?.text).toBe("Repaired draft [S1]");
  });

  it("recovers invalid routes to the chooser without creating a chat", () => {
    const state = initialChatWorkspaceState(null, true);
    expect(state.phase).toBe("chooser");
    expect(state.activeChatId).toBeNull();
    expect(state.routeNotice).toMatch(/invalid/i);
  });

  it("keeps access revocation distinct from generic recovery", () => {
    expect(
      terminalPhase(
        chatDetail(CHAT_A, [
          completedTurn({
            status: "access_revoked",
            final_answer: null,
            citations: [],
            citation_ranks: [],
            error: "Access revoked.",
          }),
        ]),
      ),
    ).toBe("access-revoked");
  });

  it("keeps length-limited turns distinct and unverified", () => {
    expect(
      terminalPhase(
        chatDetail(CHAT_A, [
          completedTurn({
            status: "length_limited",
            final_answer: null,
            partial_answer: "Partial [S1]",
            citations: [],
            citation_ranks: [],
            completed_at: null,
            error: "response reached generation limit",
          }),
        ]),
      ),
    ).toBe("length-limited");
  });

  it("keeps citation-failed turns distinct and unverified", () => {
    expect(
      terminalPhase(
        chatDetail(CHAT_A, [
          completedTurn({
            status: "citation_failed",
            final_answer: null,
            partial_answer: "Unverified table",
            citations: [],
            citation_ranks: [],
            completed_at: null,
            error: "citation validation failed",
          }),
        ]),
      ),
    ).toBe("citation-failed");
  });

  it("validates trimmed Unicode code points rather than UTF-16 units", () => {
    expect(codePointLength("😀")).toBe(1);
    expect(validateQuestion("  grounded?  ")).toEqual({
      valid: true,
      question: "grounded?",
    });
    expect(validateQuestion("😀".repeat(2_001))).toEqual(
      expect.objectContaining({ valid: false }),
    );
  });

  it("parses backend questions by Unicode code point count", () => {
    const maximum = "😀".repeat(2_000);
    const accepted = chatDetailSchema.parse(
      chatDetail(CHAT_A, [completedTurn({ question: maximum })]),
    );
    expect(accepted.turns[0].question).toBe(maximum);
    expect(() =>
      chatDetailSchema.parse(
        chatDetail(CHAT_A, [
          completedTurn({ question: "😀".repeat(2_001) }),
        ]),
      ),
    ).toThrow();
  });
});
