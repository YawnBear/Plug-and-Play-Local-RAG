import { describe, expect, it, vi } from "vitest";

import { consumeChatSse, type ChatStreamHandlers } from "@/features/chats/sse";

const CHAT = "11111111-1111-4111-8111-111111111111";
const TURN = "22222222-2222-4222-8222-222222222222";
const DOCUMENT = "33333333-3333-4333-8333-333333333333";
const CHUNK = "44444444-4444-4444-8444-444444444444";
const OTHER = "55555555-5555-4555-8555-555555555555";

const source = {
  label: "S1",
  rank: 1,
  document_id: DOCUMENT,
  chunk_id: CHUNK,
  document_id_snapshot: DOCUMENT,
  chunk_id_snapshot: CHUNK,
  filename: "original.pdf",
  display_name: "Current.pdf",
  logical_path: "/Current.pdf",
  page_start: 2,
  page_end: 2,
  section: null,
  source_available: true,
};

interface TestEvent {
  name: string;
  payload: Record<string, unknown>;
}

function normalEvents(): TestEvent[] {
  return [
    { name: "status", payload: { phase: "retrieving" } },
    { name: "status", payload: { phase: "reranking" } },
    { name: "status", payload: { phase: "preparing_answer" } },
    { name: "sources", payload: { sources: [source] } },
    { name: "status", payload: { phase: "reasoning" } },
    { name: "reasoning_start", payload: {} },
    { name: "reasoning_delta", payload: { text: "Inspecting evidence" } },
    { name: "reasoning_end", payload: { truncated: false } },
    { name: "status", payload: { phase: "streaming_answer" } },
    { name: "token", payload: { text: "Hello [S1]" } },
    { name: "status", payload: { phase: "validating_citations" } },
    {
      name: "final",
      payload: {
        answer: "Hello [S1]",
        insufficient_context: false,
        citations: [source],
      },
    },
  ];
}

function serialize(
  events: readonly TestEvent[],
  ending = "\n\n",
  startSequence = 1,
): string {
  return events
    .map(
      ({ name, payload }, index) =>
        `event: ${name}\ndata: ${JSON.stringify({
          chat_id: CHAT,
          turn_id: TURN,
          seq: startSequence + index,
          ...payload,
        })}${ending}`,
    )
    .join("");
}

function responseFrom(chunks: string[]): Response {
  const encoder = new TextEncoder();
  return new Response(
    new ReadableStream({
      start(controller) {
        for (const chunk of chunks) controller.enqueue(encoder.encode(chunk));
        controller.close();
      },
    }),
    { headers: { "content-type": "text/event-stream" } },
  );
}

function trackedResponse(chunks: string[], cancelError?: Error) {
  const encoded = chunks.map((chunk) => new TextEncoder().encode(chunk));
  let index = 0;
  const cancel = vi.fn(async () => {
    if (cancelError) throw cancelError;
  });
  const releaseLock = vi.fn();
  const reader = {
    read: vi.fn(async () =>
      index < encoded.length
        ? { value: encoded[index++], done: false }
        : { value: undefined, done: true },
    ),
    cancel,
    releaseLock,
  };
  return {
    response: { body: { getReader: () => reader } } as unknown as Response,
    cancel,
    releaseLock,
  };
}

function handlers(): {
  callbacks: ChatStreamHandlers;
  spies: Record<keyof ChatStreamHandlers, ReturnType<typeof vi.fn>>;
} {
  const spies = {
    onStatus: vi.fn(),
    onSources: vi.fn(),
    onReasoningStart: vi.fn(),
    onReasoningDelta: vi.fn(),
    onReasoningEnd: vi.fn(),
    onAnswerReset: vi.fn(),
    onToken: vi.fn(),
    onFinal: vi.fn(),
  };
  return { callbacks: spies, spies };
}

async function consume(stream: string, callbacks = handlers().callbacks) {
  return consumeChatSse(
    responseFrom([stream]),
    { chatId: CHAT },
    callbacks,
    new AbortController().signal,
  );
}

describe("chat SSE consumer", () => {
  it("dispatches reasoning and answer callbacks before the response closes", async () => {
    const encoder = new TextEncoder();
    let streamController!: ReadableStreamDefaultController<Uint8Array>;
    const response = new Response(
      new ReadableStream<Uint8Array>({
        start(controller) {
          streamController = controller;
        },
      }),
      { headers: { "content-type": "text/event-stream" } },
    );
    const { callbacks, spies } = handlers();
    const result = consumeChatSse(
      response,
      { chatId: CHAT },
      callbacks,
      new AbortController().signal,
    );
    const events = normalEvents();

    streamController.enqueue(encoder.encode(serialize(events.slice(0, 7))));
    await vi.waitFor(() => {
      expect(spies.onReasoningDelta).toHaveBeenCalledWith(
        expect.objectContaining({ text: "Inspecting evidence" }),
      );
    });
    expect(spies.onToken).not.toHaveBeenCalled();
    expect(spies.onFinal).not.toHaveBeenCalled();

    streamController.enqueue(
      encoder.encode(serialize(events.slice(7, 10), "\n\n", 8)),
    );
    await vi.waitFor(() => {
      expect(spies.onToken).toHaveBeenCalledWith(
        expect.objectContaining({ text: "Hello [S1]" }),
      );
    });
    expect(spies.onFinal).not.toHaveBeenCalled();

    streamController.enqueue(
      encoder.encode(serialize(events.slice(10), "\n\n", 11)),
    );
    streamController.close();
    await expect(result).resolves.toMatchObject({ answer: "Hello [S1]" });
  });

  it("handles chunk boundaries and dispatches separate progress, reasoning, answer, and final channels", async () => {
    const completeStream = serialize(normalEvents());
    const stream = completeStream.slice(0, -2);
    const { callbacks, spies } = handlers();

    const result = await consumeChatSse(
      responseFrom([
        stream.slice(0, 7),
        stream.slice(7, 81),
        stream.slice(81, 503),
        stream.slice(503),
      ]),
      { chatId: CHAT },
      callbacks,
      new AbortController().signal,
    );

    expect(result.answer).toBe("Hello [S1]");
    expect(spies.onStatus).toHaveBeenCalledTimes(6);
    expect(spies.onReasoningDelta).toHaveBeenCalledWith(
      expect.objectContaining({ text: "Inspecting evidence" }),
    );
    expect(spies.onToken).toHaveBeenCalledWith(
      expect.objectContaining({ text: "Hello [S1]" }),
    );
    expect(spies.onFinal).toHaveBeenCalledOnce();
  });

  it("accepts the deterministic no-context path without reasoning or answer tokens", async () => {
    const events: TestEvent[] = [
      { name: "status", payload: { phase: "retrieving" } },
      { name: "status", payload: { phase: "reranking" } },
      { name: "status", payload: { phase: "preparing_answer" } },
      { name: "sources", payload: { sources: [] } },
      { name: "status", payload: { phase: "validating_citations" } },
      {
        name: "final",
        payload: {
          answer: "The available documents do not contain enough information.",
          insufficient_context: true,
          citations: [],
        },
      },
    ];

    await expect(consume(serialize(events))).resolves.toMatchObject({
      insufficient_context: true,
      citations: [],
    });
  });

  it("accepts one continuing-answer phase before final validation", async () => {
    const events = normalEvents();
    events.splice(10, 0, {
      name: "status",
      payload: { phase: "continuing_answer" },
    });
    events.splice(11, 0, {
      name: "token",
      payload: { text: " continued" },
    });
    const { callbacks, spies } = handlers();

    await expect(
      consumeChatSse(
        responseFrom([serialize(events)]),
        { chatId: CHAT },
        callbacks,
        new AbortController().signal,
      ),
    ).resolves.toMatchObject({ answer: "Hello [S1]" });

    expect(spies.onStatus).toHaveBeenCalledWith(
      expect.objectContaining({ phase: "continuing_answer" }),
    );
    expect(spies.onToken).toHaveBeenCalledWith(
      expect.objectContaining({ text: " continued" }),
    );
  });

  it("replaces the draft while a citation repair streams", async () => {
    const events = normalEvents();
    events.splice(
      11,
      0,
      { name: "status", payload: { phase: "repairing_citations" } },
      { name: "answer_reset", payload: {} },
      { name: "token", payload: { text: "Repaired [S1]" } },
      { name: "status", payload: { phase: "validating_citations" } },
    );
    const { callbacks, spies } = handlers();

    await expect(
      consumeChatSse(
        responseFrom([serialize(events)]),
        { chatId: CHAT },
        callbacks,
        new AbortController().signal,
      ),
    ).resolves.toMatchObject({ answer: "Hello [S1]" });

    expect(spies.onAnswerReset).toHaveBeenCalledOnce();
    expect(spies.onToken).toHaveBeenCalledWith(
      expect.objectContaining({ text: "Repaired [S1]" }),
    );
  });

  it("preserves the generation-limit error code for recovery UI", async () => {
    const events = normalEvents().slice(0, 10);
    events.push({
      name: "status",
      payload: { phase: "continuing_answer" },
    });
    events.push({
      name: "error",
      payload: {
        code: "generation_limit",
        message: "Continue the unverified partial answer.",
      },
    });

    await expect(consume(serialize(events))).rejects.toMatchObject({
      code: "generation_limit",
    });
  });

  it.each([
    {
      label: "a skipped sequence number",
      mutate: (events: TestEvent[]) =>
        serialize(events.slice(0, 1)) + serialize(events.slice(1), "\n\n", 3),
      message: "expected 2",
    },
    {
      label: "an invalid phase transition",
      mutate: (events: TestEvent[]) =>
        serialize([
          events[0],
          { name: "status", payload: { phase: "preparing_answer" } },
        ] as TestEvent[]),
      message: "expected reranking",
    },
    {
      label: "reasoning before its start envelope",
      mutate: (events: TestEvent[]) =>
        serialize(events.filter((event) => event.name !== "reasoning_start")),
      message: "outside its start/end envelope",
    },
    {
      label: "answer text before reasoning ends",
      mutate: (events: TestEvent[]) => {
        const next = [...events];
        const end = next.findIndex((event) => event.name === "reasoning_end");
        next.splice(end, 0, { name: "token", payload: { text: "early" } });
        return serialize(next);
      },
      message: "answer text out of order",
    },
  ])("rejects $label", async ({ mutate, message }) => {
    await expect(consume(mutate(normalEvents()))).rejects.toThrow(message);
  });

  it("rejects malformed, unknown, cross-chat, and post-final events", async () => {
    await expect(
      consume('event: status\ndata: {\n\n'),
    ).rejects.toThrow("invalid JSON");

    await expect(
      consume(
        serialize([
          {
            name: "mystery",
            payload: {},
          },
        ]),
      ),
    ).rejects.toThrow("unknown stream event");

    const crossChat = serialize(normalEvents()).replace(
      `"chat_id":"${CHAT}"`,
      `"chat_id":"${OTHER}"`,
    );
    await expect(consume(crossChat)).rejects.toThrow(
      "different chat identifier",
    );

    await expect(
      consume(
        serialize([
          ...normalEvents(),
          {
            name: "token",
            payload: { text: "late" },
          },
        ]),
      ),
    ).rejects.toThrow("continued after its final event");
  });

  it("rejects missing finals and reasoning beyond the 20,000-code-point display limit", async () => {
    await expect(
      consume(serialize(normalEvents().slice(0, -1))),
    ).rejects.toThrow("before a verified final");

    const oversized = normalEvents().map((event) =>
      event.name === "reasoning_delta"
        ? { ...event, payload: { text: "r".repeat(20_001) } }
        : event,
    );
    await expect(consume(serialize(oversized))).rejects.toThrow(
      "visible reasoning limit",
    );
  });

  it("allows citations to become unavailable but rejects changed source identity", async () => {
    const unavailable = {
      ...source,
      document_id: null,
      chunk_id: null,
      source_available: false,
    };
    const valid = normalEvents().map((event) =>
      event.name === "final"
        ? {
            ...event,
            payload: {
              ...event.payload,
              citations: [unavailable],
            },
          }
        : event,
    );
    await expect(consume(serialize(valid))).resolves.toMatchObject({
      citations: [unavailable],
    });

    const invalid = normalEvents().map((event) =>
      event.name === "final"
        ? {
            ...event,
            payload: {
              ...event.payload,
              citations: [{ ...source, logical_path: "/Hidden.pdf" }],
            },
          }
        : event,
    );
    await expect(consume(serialize(invalid))).rejects.toThrow(
      "did not match the streamed source snapshot",
    );
  });

  it("accepts an early terminal error while still enforcing its envelope and sequence", async () => {
    const error = serialize([
      { name: "status", payload: { phase: "retrieving" } },
      {
        name: "error",
        payload: { code: "generation_failed", message: "model failed" },
      },
    ]);
    await expect(consume(error)).rejects.toThrow("model failed");

    const wrongSequence = error.replace('"seq":2', '"seq":4');
    await expect(consume(wrongSequence)).rejects.toThrow("expected 2");
  });

  it("awaits reader cancellation before releasing its lock on failure", async () => {
    const tracked = trackedResponse(['event: status\ndata: {\n\n']);
    const caught = await consumeChatSse(
      tracked.response,
      { chatId: CHAT },
      handlers().callbacks,
      new AbortController().signal,
    ).catch((error: unknown) => error);

    expect(caught).toBeInstanceOf(Error);
    expect(tracked.cancel).toHaveBeenCalledWith(caught);
    expect(tracked.cancel.mock.invocationCallOrder[0]).toBeLessThan(
      tracked.releaseLock.mock.invocationCallOrder[0],
    );
  });

  it("cancels with the exact abort reason and preserves the original failure", async () => {
    const controller = new AbortController();
    const reason = new DOMException("user stopped", "AbortError");
    controller.abort(reason);
    const aborted = trackedResponse([]);

    const caughtAbort = await consumeChatSse(
      aborted.response,
      { chatId: CHAT },
      handlers().callbacks,
      controller.signal,
    ).catch((error: unknown) => error);
    expect(caughtAbort).toBe(reason);
    expect(aborted.cancel).toHaveBeenCalledWith(reason);

    const cancellationFailure = new Error("cancel failed");
    const malformed = trackedResponse(
      ['event: status\ndata: {\n\n'],
      cancellationFailure,
    );
    const original = await consumeChatSse(
      malformed.response,
      { chatId: CHAT },
      handlers().callbacks,
      new AbortController().signal,
    ).catch((error: unknown) => error);
    expect(original).not.toBe(cancellationFailure);
    expect(original).toMatchObject({
      message: expect.stringContaining("invalid JSON"),
    });
    expect(malformed.releaseLock).toHaveBeenCalledOnce();
  });
});
