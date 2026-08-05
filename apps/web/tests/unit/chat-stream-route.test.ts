import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { POST as proxyMessageStream } from "@/app/api/chats/[chatId]/messages/stream/route";
import { POST as proxyRetryStream } from "@/app/api/chats/[chatId]/turns/[turnId]/retry/stream/route";

const CHAT = "11111111-1111-4111-8111-111111111111";
const TURN = "22222222-2222-4222-8222-222222222222";
const originalInternalApiUrl = process.env.INTERNAL_API_URL;

describe("chat stream route handlers", () => {
  beforeEach(() => {
    process.env.INTERNAL_API_URL = "http://127.0.0.1:8000/";
  });

  afterEach(() => {
    if (originalInternalApiUrl === undefined) {
      delete process.env.INTERNAL_API_URL;
    } else {
      process.env.INTERNAL_API_URL = originalInternalApiUrl;
    }
    vi.unstubAllGlobals();
  });

  it("relays the message response body incrementally with auth and mutation proof", async () => {
    const encoder = new TextEncoder();
    let upstreamController!: ReadableStreamDefaultController<Uint8Array>;
    const upstreamBody = new ReadableStream<Uint8Array>({
      start(controller) {
        upstreamController = controller;
      },
    });
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(upstreamBody, {
        status: 200,
        headers: {
          "Cache-Control": "no-cache",
          Connection: "keep-alive",
          "Content-Type": "text/event-stream; charset=utf-8",
          "X-Accel-Buffering": "no",
        },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const controller = new AbortController();
    const request = new Request(
      `http://localhost:3000/api/chats/${CHAT}/messages/stream`,
      {
        method: "POST",
        headers: {
          Accept: "text/event-stream",
          "Content-Type": "application/json",
          Cookie: "rag_session=session; csrf_token=proof",
          Origin: "http://localhost:3000",
          "X-CSRF-Token": "proof",
        },
        body: '{"question":"What was found?"}',
        signal: controller.signal,
      },
    );

    const response = await proxyMessageStream(request, {
      params: Promise.resolve({ chatId: CHAT.toUpperCase() }),
    });
    const [target, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(target).toBe(
      `http://127.0.0.1:8000/api/chats/${CHAT}/messages/stream`,
    );
    expect(init.signal).toBe(request.signal);
    expect(init.headers).toEqual(
      expect.objectContaining({
        get: expect.any(Function),
      }),
    );
    const forwarded = init.headers as Headers;
    expect(forwarded.get("accept")).toBe("text/event-stream");
    expect(forwarded.get("content-type")).toBe("application/json");
    expect(forwarded.get("cookie")).toContain("rag_session=session");
    expect(forwarded.get("origin")).toBe("http://localhost:3000");
    expect(forwarded.get("x-csrf-token")).toBe("proof");
    expect(new TextDecoder().decode(init.body as ArrayBuffer)).toBe(
      '{"question":"What was found?"}',
    );
    expect(response.headers.get("content-type")).toContain("text/event-stream");
    expect(response.headers.get("x-accel-buffering")).toBe("no");
    expect(response.headers.get("cache-control")).toBe(
      "no-cache, no-transform",
    );
    expect(response.headers.has("connection")).toBe(false);

    const reader = response.body!.getReader();
    const firstRead = reader.read();
    upstreamController.enqueue(encoder.encode("event: reasoning_delta\n\n"));
    await expect(firstRead).resolves.toEqual({
      done: false,
      value: encoder.encode("event: reasoning_delta\n\n"),
    });

    const secondRead = reader.read();
    upstreamController.enqueue(encoder.encode("event: token\n\n"));
    upstreamController.close();
    await expect(secondRead).resolves.toEqual({
      done: false,
      value: encoder.encode("event: token\n\n"),
    });
    await expect(reader.read()).resolves.toEqual({
      done: true,
      value: undefined,
    });
  });

  it("uses the exact retry path and preserves upstream status and errors", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      Response.json(
        { detail: "invalid CSRF token" },
        {
          status: 403,
          headers: { "X-CSRF-Token": "replacement" },
        },
      ),
    );
    vi.stubGlobal("fetch", fetchMock);
    const request = new Request(
      `http://localhost:3000/api/chats/${CHAT}/turns/${TURN}/retry/stream`,
      {
        method: "POST",
        headers: {
          Accept: "text/event-stream",
          Cookie: "rag_session=session; csrf_token=proof",
          Origin: "http://localhost:3000",
          "X-CSRF-Token": "proof",
        },
      },
    );

    const response = await proxyRetryStream(request, {
      params: Promise.resolve({ chatId: CHAT, turnId: TURN }),
    });

    expect(fetchMock.mock.calls[0][0]).toBe(
      `http://127.0.0.1:8000/api/chats/${CHAT}/turns/${TURN}/retry/stream`,
    );
    expect(response.status).toBe(403);
    expect(response.headers.get("x-csrf-token")).toBe("replacement");
    await expect(response.json()).resolves.toEqual({
      detail: "invalid CSRF token",
    });
  });

  it("preserves both session-clearing Set-Cookie headers on pre-stream expiry", async () => {
    const headers = new Headers({ "Content-Type": "application/json" });
    headers.append("Set-Cookie", "rag_session=; Max-Age=0; Path=/");
    headers.append("Set-Cookie", "csrf_token=; Max-Age=0; Path=/");
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            detail: {
              code: "session_expired",
              message: "Your session expired after 30 minutes of inactivity.",
            },
          }),
          { status: 401, headers },
        ),
      ),
    );
    const request = new Request(
      `http://localhost:3000/api/chats/${CHAT}/messages/stream`,
      { method: "POST" },
    );

    const response = await proxyMessageStream(request, {
      params: Promise.resolve({ chatId: CHAT }),
    });

    expect(response.status).toBe(401);
    const cookies = (
      response.headers as Headers & { getSetCookie?: () => string[] }
    ).getSetCookie?.();
    expect(cookies).toEqual([
      "rag_session=; Max-Age=0; Path=/",
      "csrf_token=; Max-Age=0; Path=/",
    ]);
  });
});
