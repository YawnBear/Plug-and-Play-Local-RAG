import { afterEach, describe, expect, it, vi } from "vitest";
import { z } from "zod";

import {
  ApiError,
  apiErrorFromResponse,
  apiFetch,
  apiUrl,
  jsonRequest,
  requestJson,
  safeNextPath,
  SESSION_EXPIRED_EVENT,
  SESSION_UNAUTHENTICATED_EVENT,
  setCsrfToken,
} from "@/lib/http";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("HTTP helpers", () => {
  it("accepts only safe local post-login paths", () => {
    expect(safeNextPath("/knowledge-base?folder=abc#page")).toBe(
      "/knowledge-base?folder=abc#page",
    );
    expect(safeNextPath("https://evil.example/path")).toBe("/");
    expect(safeNextPath("//evil.example/path")).toBe("/");
    expect(safeNextPath("/%2f%2fevil.example/path")).toBe("/");
    expect(safeNextPath("/\\evil.example/path")).toBe("/");
    expect(safeNextPath("/%5cevil.example/path")).toBe("/");
    expect(safeNextPath("/login?next=/admin")).toBe("/");
    expect(safeNextPath("/lo%67in?next=/admin")).toBe("/");
    expect(safeNextPath("/activate?code=secret")).toBe("/");
    expect(safeNextPath("/knowledge-base%0d%0aSet-Cookie:test")).toBe("/");
  });

  it("uses relative same-origin URLs and session credentials", async () => {
    expect(apiUrl("/api/example")).toBe("/api/example");
    const fetchMock = vi
      .fn()
      .mockResolvedValue(new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetchMock);
    setCsrfToken("csrf-test");

    await apiFetch("/api/example", { method: "POST" });

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/example",
      expect.objectContaining({
        credentials: "same-origin",
        cache: "no-store",
      }),
    );
    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(new Headers(init.headers).get("X-CSRF-Token")).toBe("csrf-test");
  });

  it("signals an expired session for authoritative API 401 responses", async () => {
    const expired = vi.fn();
    window.addEventListener(SESSION_EXPIRED_EVENT, expired);
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
          { status: 401 },
        ),
      ),
    );

    await expect(apiFetch("/api/chats")).rejects.toMatchObject({ status: 401 });
    expect(expired).toHaveBeenCalledOnce();

    window.removeEventListener(SESSION_EXPIRED_EVENT, expired);
  });

  it("signals other 401 responses separately from inactivity expiry", async () => {
    const expired = vi.fn();
    const unauthenticated = vi.fn();
    window.addEventListener(SESSION_EXPIRED_EVENT, expired);
    window.addEventListener(SESSION_UNAUTHENTICATED_EVENT, unauthenticated);
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            detail: {
              code: "authentication_required",
              message: "Authentication is required.",
            },
          }),
          { status: 401 },
        ),
      ),
    );

    await expect(apiFetch("/api/chats")).rejects.toMatchObject({ status: 401 });
    expect(unauthenticated).toHaveBeenCalledOnce();
    expect(expired).not.toHaveBeenCalled();

    window.removeEventListener(SESSION_EXPIRED_EVENT, expired);
    window.removeEventListener(SESSION_UNAUTHENTICATED_EVENT, unauthenticated);
  });

  it("normalizes FastAPI validation arrays", async () => {
    const error = await apiErrorFromResponse(
      new Response(
        JSON.stringify({
          detail: [
            {
              loc: ["body", "question"],
              msg: "Field required",
              type: "missing",
            },
            {
              loc: ["query", "page"],
              msg: "Input should be greater than 0",
              type: "greater_than",
            },
          ],
        }),
        { status: 422, statusText: "Unprocessable Content" },
      ),
    );

    expect(error).toBeInstanceOf(ApiError);
    expect(error.status).toBe(422);
    expect(error.details).toEqual([
      "question: Field required",
      "query.page: Input should be greater than 0",
    ]);
    expect(error.message).toContain("question: Field required");
  });

  it("uses string details and readable plain-text fallbacks", async () => {
    await expect(
      apiErrorFromResponse(
        new Response(JSON.stringify({ detail: "chat is busy" }), {
          status: 409,
        }),
      ),
    ).resolves.toMatchObject({ message: "chat is busy", status: 409 });
    await expect(
      apiErrorFromResponse(new Response("proxy unavailable", { status: 503 })),
    ).resolves.toMatchObject({ message: "proxy unavailable", status: 503 });
  });

  it("parses only the documented structured error detail shape", async () => {
    await expect(
      apiErrorFromResponse(
        new Response(
          JSON.stringify({
            detail: {
              code: "upload_unavailable",
              message:
                "This content is already stored. Ask an administrator for access.",
            },
          }),
          { status: 409 },
        ),
      ),
    ).resolves.toMatchObject({
      message:
        "This content is already stored. Ask an administrator for access.",
      status: 409,
      code: "upload_unavailable",
    });

    const undocumented = JSON.stringify({
      detail: { reason: "secret", message: "not an accepted shape" },
    });
    await expect(
      apiErrorFromResponse(new Response(undocumented, { status: 409 })),
    ).resolves.toMatchObject({ message: undocumented, code: undefined });
  });

  it("strictly validates successful JSON", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ value: 1, extra: true }), {
          status: 200,
        }),
      ),
    );
    const schema = z.object({ value: z.number() }).strict();

    await expect(requestJson("/api/example", schema)).rejects.toThrow(
      "unexpected response",
    );
  });

  it("preserves abort signals and JSON headers", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetchMock);
    const controller = new AbortController();

    await apiFetch(
      "/api/example",
      jsonRequest(
        { ok: true },
        { method: "POST", signal: controller.signal },
      ),
    );

    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(init.signal).toBe(controller.signal);
    expect(new Headers(init.headers).get("content-type")).toBe(
      "application/json",
    );
    expect(init.body).toBe('{"ok":true}');
  });

  it("normalizes fetch network failures to an actionable ApiError", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("fetch failed")));

    const caught = await apiFetch("/api/example").catch(
      (error: unknown) => error,
    );

    expect(caught).toBeInstanceOf(ApiError);
    expect(caught).toMatchObject({
      message: expect.stringContaining("local API service"),
      details: ["fetch failed"],
    });
  });

  it("preserves AbortError and an explicit signal reason by identity", async () => {
    const abortError = new DOMException("aborted", "AbortError");
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(abortError));
    await expect(apiFetch("/api/example")).rejects.toBe(abortError);

    const controller = new AbortController();
    const reason = { kind: "explicit abort" };
    controller.abort(reason);
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("ignored")));
    await expect(
      apiFetch("/api/example", { signal: controller.signal }),
    ).rejects.toBe(reason);
  });
});
