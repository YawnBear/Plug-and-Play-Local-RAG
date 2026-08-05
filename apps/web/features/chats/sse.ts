import { ApiError } from "@/lib/http";
import { normalizeUuid } from "@/lib/uuid";

import {
  answerResetEventSchema,
  errorEventSchema,
  finalEventSchema,
  reasoningDeltaEventSchema,
  reasoningEndEventSchema,
  reasoningStartEventSchema,
  sourcesEventSchema,
  statusEventSchema,
  tokenEventSchema,
  type AnswerResetEvent,
  type FinalEvent,
  type ReasoningDeltaEvent,
  type ReasoningEndEvent,
  type ReasoningStartEvent,
  type SourcesEvent,
  type StatusEvent,
  type StreamSource,
  type TokenEvent,
} from "./contracts";

export interface ChatStreamHandlers {
  onStatus: (event: StatusEvent) => void;
  onSources: (event: SourcesEvent) => void;
  onReasoningStart: (event: ReasoningStartEvent) => void;
  onReasoningDelta: (event: ReasoningDeltaEvent) => void;
  onReasoningEnd: (event: ReasoningEndEvent) => void;
  onAnswerReset: (event: AnswerResetEvent) => void;
  onToken: (event: TokenEvent) => void;
  onFinal: (event: FinalEvent) => void;
}

export interface ExpectedChatStream {
  chatId: string;
  turnId?: string;
}

interface SseFrame {
  event: string;
  data: string;
}

function invalidStream(message: string, details: readonly string[] = [message]): ApiError {
  return new ApiError(message, undefined, details);
}

const MAX_REASONING_CODE_POINTS = 20_000;

function parseFrame(rawFrame: string): SseFrame | null {
  if (!rawFrame.trim()) return null;
  let event: string | null = null;
  const data: string[] = [];
  let meaningful = false;

  for (const line of rawFrame.split(/\r\n|\r|\n/)) {
    if (line === "" || line.startsWith(":")) continue;
    meaningful = true;
    const colon = line.indexOf(":");
    const field = colon === -1 ? line : line.slice(0, colon);
    let value = colon === -1 ? "" : line.slice(colon + 1);
    if (value.startsWith(" ")) value = value.slice(1);
    if (field === "event") {
      if (event !== null) {
        throw invalidStream("The server returned a malformed stream event.");
      }
      event = value;
    } else if (field === "data") {
      data.push(value);
    } else {
      throw invalidStream(`The server returned an unsupported SSE field: ${field}.`);
    }
  }

  if (!meaningful) return null;
  if (data.length === 0) {
    throw invalidStream("The server returned a stream event without data.");
  }
  return { event: event ?? "message", data: data.join("\n") };
}

function nextBoundary(buffer: string): { index: number; length: number } | null {
  const match = /\r\n\r\n|\n\n|\r\r/.exec(buffer);
  return match ? { index: match.index, length: match[0].length } : null;
}

function parsePayload(data: string): unknown {
  try {
    return JSON.parse(data);
  } catch {
    throw invalidStream("The server returned invalid JSON in a stream event.");
  }
}

function abortReason(signal: AbortSignal): unknown {
  return (
    signal.reason ??
    new DOMException("The operation was aborted.", "AbortError")
  );
}

function validateStreamSources(sources: readonly StreamSource[]): void {
  for (const [index, source] of sources.entries()) {
    const expectedRank = index + 1;
    if (source.rank !== expectedRank || source.label !== `S${expectedRank}`) {
      throw invalidStream(
        "The sources event returned duplicate, out-of-order, or non-contiguous sources.",
      );
    }
    if (source.source_available !== (source.document_id !== null)) {
      throw invalidStream("A streamed source returned inconsistent availability.");
    }
  }
}

const immutableCitationFields = [
  "label",
  "rank",
  "document_id_snapshot",
  "chunk_id_snapshot",
  "filename",
  "display_name",
  "logical_path",
  "page_start",
  "page_end",
  "section",
] as const satisfies readonly (keyof StreamSource)[];

function validateFinalCitations(
  streamedSources: readonly StreamSource[],
  citations: readonly StreamSource[],
): void {
  const sourcesByLabel = new Map(
    streamedSources.map((source) => [source.label, source]),
  );
  const sourcesByRank = new Map(
    streamedSources.map((source) => [source.rank, source]),
  );
  const citedLabels = new Set<string>();
  const citedRanks = new Set<number>();

  for (const citation of citations) {
    if (
      citedLabels.has(citation.label) ||
      citedRanks.has(citation.rank)
    ) {
      throw invalidStream("The final event returned duplicate citations.");
    }
    citedLabels.add(citation.label);
    citedRanks.add(citation.rank);

    const streamed = sourcesByLabel.get(citation.label);
    if (!streamed || sourcesByRank.get(citation.rank) !== streamed) {
      throw invalidStream("The final event cited a source that was not streamed.");
    }
    if (
      immutableCitationFields.some(
        (field) => citation[field] !== streamed[field],
      ) ||
      (citation.document_id !== streamed.document_id &&
        citation.document_id !== null) ||
      (citation.chunk_id !== streamed.chunk_id && citation.chunk_id !== null) ||
      citation.source_available !== (citation.document_id !== null)
    ) {
      throw invalidStream(
        "The final event citation did not match the streamed source snapshot.",
      );
    }
  }
}

export async function consumeChatSse(
  response: Response,
  expected: ExpectedChatStream,
  handlers: ChatStreamHandlers,
  signal: AbortSignal,
): Promise<FinalEvent> {
  if (!response.body) {
    throw invalidStream("The server returned an empty answer stream.");
  }
  const expectedChatId = normalizeUuid(expected.chatId);
  const requestedTurnId =
    expected.turnId === undefined ? undefined : normalizeUuid(expected.turnId);
  if (!expectedChatId || (expected.turnId !== undefined && !requestedTurnId)) {
    throw new TypeError("Expected stream identifiers must be valid UUIDs.");
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let turnId = requestedTurnId;
  let sources: SourcesEvent | null = null;
  let final: FinalEvent | null = null;
  let expectedSequence = 1;
  let protocolState:
    | "expect_retrieving"
    | "expect_reranking"
    | "expect_preparing_answer"
    | "expect_sources"
    | "expect_reasoning_status"
    | "expect_reasoning_start"
    | "reasoning_active"
    | "expect_answer_status"
    | "answer"
    | "expect_validation_status"
    | "validating"
    | "repairing"
    | "repair_answer" = "expect_retrieving";
  let reasoningCodePoints = 0;

  const assertEnvelope = (event: {
    chat_id: string;
    turn_id: string;
    seq: number;
  }): void => {
    if (event.chat_id !== expectedChatId) {
      throw invalidStream("The answer stream returned a different chat identifier.");
    }
    if (turnId === undefined) turnId = event.turn_id;
    if (event.turn_id !== turnId) {
      throw invalidStream("The answer stream returned a different turn identifier.");
    }
    if (event.seq !== expectedSequence) {
      throw invalidStream(
        `The answer stream returned sequence ${event.seq}; expected ${expectedSequence}.`,
      );
    }
    expectedSequence += 1;
  };

  const consume = (rawFrame: string): void => {
    const frame = parseFrame(rawFrame);
    if (!frame) return;
    if (final) {
      throw invalidStream("The answer stream continued after its final event.");
    }
    const payload = parsePayload(frame.data);

    if (frame.event === "error") {
      const parsed = errorEventSchema.safeParse(payload);
      if (!parsed.success) {
        throw invalidStream(
          "The server returned an invalid error event.",
          parsed.error.issues.map((issue) => issue.message),
        );
      }
      assertEnvelope(parsed.data);
      throw new ApiError(
        parsed.data.message,
        undefined,
        [parsed.data.message],
        parsed.data.code,
      );
    }

    if (frame.event === "status") {
      const parsed = statusEventSchema.safeParse(payload);
      if (!parsed.success) {
        throw invalidStream(
          "The server returned an invalid status event.",
          parsed.error.issues.map((issue) => issue.message),
        );
      }
      assertEnvelope(parsed.data);
      let expectedPhase: StatusEvent["phase"] | null = null;
      if (protocolState === "expect_retrieving") expectedPhase = "retrieving";
      else if (protocolState === "expect_reranking") expectedPhase = "reranking";
      else if (protocolState === "expect_preparing_answer") {
        expectedPhase = "preparing_answer";
      } else if (protocolState === "expect_reasoning_status") {
        expectedPhase = "reasoning";
      } else if (protocolState === "expect_answer_status") {
        expectedPhase = "streaming_answer";
      } else if (
        protocolState === "answer" &&
        parsed.data.phase === "continuing_answer"
      ) {
        expectedPhase = "continuing_answer";
      } else if (
        protocolState === "expect_validation_status" ||
        protocolState === "answer" ||
        protocolState === "repair_answer"
      ) {
        expectedPhase = "validating_citations";
      } else if (protocolState === "validating") {
        expectedPhase = "repairing_citations";
      }
      if (parsed.data.phase !== expectedPhase) {
        throw invalidStream(
          `The answer stream returned status ${parsed.data.phase}; expected ${expectedPhase}.`,
        );
      }
      if (protocolState === "expect_retrieving") {
        protocolState = "expect_reranking";
      } else if (protocolState === "expect_reranking") {
        protocolState = "expect_preparing_answer";
      } else if (protocolState === "expect_preparing_answer") {
        protocolState = "expect_sources";
      } else if (protocolState === "expect_reasoning_status") {
        protocolState = "expect_reasoning_start";
      } else if (protocolState === "expect_answer_status") {
        protocolState = "answer";
      } else if (
        protocolState === "answer" &&
        parsed.data.phase === "continuing_answer"
      ) {
        protocolState = "answer";
      } else if (
        protocolState === "expect_validation_status" ||
        protocolState === "answer" ||
        protocolState === "repair_answer"
      ) {
        protocolState = "validating";
      } else if (protocolState === "validating") {
        protocolState = "repairing";
      } else {
        throw invalidStream("The answer stream returned a status in an invalid phase.");
      }
      handlers.onStatus(parsed.data);
      return;
    }

    if (
      ![
        "sources",
        "reasoning_start",
        "reasoning_delta",
        "reasoning_end",
        "answer_reset",
        "token",
        "final",
      ].includes(frame.event)
    ) {
      throw invalidStream(
        `The server returned an unknown stream event: ${frame.event}.`,
      );
    }

    if (frame.event === "sources") {
      const parsed = sourcesEventSchema.safeParse(payload);
      if (!parsed.success) {
        throw invalidStream(
          "The server returned an invalid sources event.",
          parsed.error.issues.map((issue) => issue.message),
        );
      }
      if (sources) {
        throw invalidStream("The answer stream returned sources more than once.");
      }
      if (protocolState !== "expect_sources") {
        throw invalidStream("The answer stream returned sources out of order.");
      }
      assertEnvelope(parsed.data);
      validateStreamSources(parsed.data.sources);
      sources = parsed.data;
      protocolState =
        parsed.data.sources.length === 0
          ? "expect_validation_status"
          : "expect_reasoning_status";
      handlers.onSources(parsed.data);
      return;
    }

    if (!sources) {
      throw invalidStream("The answer stream returned content before its sources.");
    }
    if (frame.event === "reasoning_start") {
      const parsed = reasoningStartEventSchema.safeParse(payload);
      if (!parsed.success) {
        throw invalidStream(
          "The server returned an invalid reasoning start event.",
          parsed.error.issues.map((issue) => issue.message),
        );
      }
      if (protocolState !== "expect_reasoning_start") {
        throw invalidStream("The answer stream started reasoning out of order.");
      }
      assertEnvelope(parsed.data);
      protocolState = "reasoning_active";
      handlers.onReasoningStart(parsed.data);
      return;
    }
    if (frame.event === "reasoning_delta") {
      const parsed = reasoningDeltaEventSchema.safeParse(payload);
      if (!parsed.success) {
        throw invalidStream(
          "The server returned an invalid reasoning delta event.",
          parsed.error.issues.map((issue) => issue.message),
        );
      }
      if (protocolState !== "reasoning_active") {
        throw invalidStream(
          "The answer stream returned reasoning outside its start/end envelope.",
        );
      }
      assertEnvelope(parsed.data);
      reasoningCodePoints += Array.from(parsed.data.text).length;
      if (reasoningCodePoints > MAX_REASONING_CODE_POINTS) {
        throw invalidStream("The answer stream exceeded the visible reasoning limit.");
      }
      handlers.onReasoningDelta(parsed.data);
      return;
    }
    if (frame.event === "reasoning_end") {
      const parsed = reasoningEndEventSchema.safeParse(payload);
      if (!parsed.success) {
        throw invalidStream(
          "The server returned an invalid reasoning end event.",
          parsed.error.issues.map((issue) => issue.message),
        );
      }
      if (protocolState !== "reasoning_active") {
        throw invalidStream("The answer stream ended reasoning out of order.");
      }
      assertEnvelope(parsed.data);
      protocolState = "expect_answer_status";
      handlers.onReasoningEnd(parsed.data);
      return;
    }
    if (frame.event === "answer_reset") {
      const parsed = answerResetEventSchema.safeParse(payload);
      if (!parsed.success) {
        throw invalidStream(
          "The server returned an invalid answer reset event.",
          parsed.error.issues.map((issue) => issue.message),
        );
      }
      if (protocolState !== "repairing") {
        throw invalidStream("The answer stream reset text out of order.");
      }
      assertEnvelope(parsed.data);
      protocolState = "repair_answer";
      handlers.onAnswerReset(parsed.data);
      return;
    }
    if (frame.event === "token") {
      const parsed = tokenEventSchema.safeParse(payload);
      if (!parsed.success) {
        throw invalidStream(
          "The server returned an invalid token event.",
          parsed.error.issues.map((issue) => issue.message),
        );
      }
      if (protocolState !== "answer" && protocolState !== "repair_answer") {
        throw invalidStream("The answer stream returned answer text out of order.");
      }
      assertEnvelope(parsed.data);
      handlers.onToken(parsed.data);
      return;
    }
    if (frame.event === "final") {
      const parsed = finalEventSchema.safeParse(payload);
      if (!parsed.success) {
        throw invalidStream(
          "The server returned an invalid final event.",
          parsed.error.issues.map((issue) => issue.message),
        );
      }
      if (protocolState !== "validating") {
        throw invalidStream("The answer stream returned its final result out of order.");
      }
      assertEnvelope(parsed.data);
      validateFinalCitations(sources.sources, parsed.data.citations);
      final = parsed.data;
      handlers.onFinal(parsed.data);
      return;
    }
    throw invalidStream(`The server returned an unknown stream event: ${frame.event}.`);
  };

  let cancellation: Promise<void> | null = null;
  const cancelReader = (reason: unknown): Promise<void> => {
    if (cancellation === null) {
      try {
        cancellation = reader.cancel(reason);
      } catch (error) {
        cancellation = Promise.reject(error);
      }
    }
    return cancellation;
  };
  const onAbort = (): void => {
    void cancelReader(abortReason(signal)).catch(() => undefined);
  };
  signal.addEventListener("abort", onAbort, { once: true });
  try {
    if (signal.aborted) throw abortReason(signal);
    while (true) {
      const { value, done } = await reader.read();
      if (signal.aborted) throw abortReason(signal);
      buffer += decoder.decode(value, { stream: !done });
      let boundary = nextBoundary(buffer);
      while (boundary) {
        consume(buffer.slice(0, boundary.index));
        buffer = buffer.slice(boundary.index + boundary.length);
        boundary = nextBoundary(buffer);
      }
      if (done) break;
    }
    buffer += decoder.decode();
    if (buffer.trim()) consume(buffer);
    if (!final) {
      throw invalidStream(
        "The answer stream ended before a verified final result arrived.",
      );
    }
    return final;
  } catch (error) {
    try {
      await cancelReader(error);
    } catch {
      // Preserve the protocol, handler, network, or abort error that ended the stream.
    }
    throw error;
  } finally {
    signal.removeEventListener("abort", onAbort);
    reader.releaseLock();
  }
}
