import { afterEach, describe, expect, it, vi } from "vitest";

import {
  activate,
  getCurrentUser,
  login,
  logout,
  refreshSession,
} from "@/features/auth/api";
import { activationRequestSchema } from "@/features/auth/contracts";
import { setCsrfToken } from "@/lib/http";

const user = {
  id: "11111111-1111-4111-8111-111111111111",
  username: "reader",
  display_name: "Reader",
  role: "member" as const,
  status: "active" as const,
};

afterEach(() => {
  vi.unstubAllGlobals();
  setCsrfToken(null);
});

describe("auth API", () => {
  it("uses the typed login contract and records the returned CSRF token", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({ user: null, csrf_token: "preauth-csrf" }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        ),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ user, csrf_token: "login-csrf" }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      );
    vi.stubGlobal("fetch", fetchMock);

    await expect(login("reader", "a sufficiently long password")).resolves.toMatchObject({ user });
    expect(fetchMock.mock.calls[0][0]).toBe("/api/auth/me");
    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      "/api/auth/login",
      expect.objectContaining({ credentials: "same-origin" }),
    );
    const loginInit = fetchMock.mock.calls[1][1] as RequestInit;
    expect(loginInit.body).toBe(
      JSON.stringify({ username: "reader", password: "a sufficiently long password" }),
    );
    expect(new Headers(loginInit.headers).get("X-CSRF-Token")).toBe(
      "preauth-csrf",
    );
  });

  it("recovers an expired cookie before posting login credentials", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            detail: {
              code: "session_expired",
              message: "Your session expired after 30 minutes of inactivity.",
            },
          }),
          { status: 401 },
        ),
      )
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({ user: null, csrf_token: "replacement-csrf" }),
          { status: 200 },
        ),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ user, csrf_token: "login-csrf" }), {
          status: 200,
        }),
      );
    vi.stubGlobal("fetch", fetchMock);

    await expect(login("reader", "a sufficiently long password")).resolves.toMatchObject({ user });
    expect(fetchMock.mock.calls.map(([path]) => path)).toEqual([
      "/api/auth/me",
      "/api/auth/me",
      "/api/auth/login",
    ]);
    expect(
      new Headers((fetchMock.mock.calls[2][1] as RequestInit).headers).get(
        "X-CSRF-Token",
      ),
    ).toBe("replacement-csrf");
  });

  it("loads the no-cache session contract", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ user: null, csrf_token: "anonymous-csrf" }), {
          status: 200,
        }),
      ),
    );
    await expect(getCurrentUser()).resolves.toMatchObject({ user: null });
    expect((fetch as unknown as ReturnType<typeof vi.fn>).mock.calls[0][1]).toEqual(
      expect.objectContaining({ cache: "no-store", credentials: "same-origin" }),
    );
  });

  it("posts one-time activation and clears the session on logout", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({ user: null, csrf_token: "preauth-csrf" }),
          { status: 200 },
        ),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ user, csrf_token: "activation-csrf" }), {
          status: 200,
        }),
      )
      .mockResolvedValueOnce(new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetchMock);

    await activate("single-use-code", "a permanent password of fourteen");
    await logout();
    expect(fetchMock.mock.calls[0][0]).toBe("/api/auth/me");
    expect(fetchMock.mock.calls[1][0]).toBe("/api/auth/activate");
    expect(fetchMock.mock.calls[2][0]).toBe("/api/auth/logout");
    expect((fetchMock.mock.calls[1][1] as RequestInit).body).toBe(
      JSON.stringify({ code: "single-use-code", password: "a permanent password of fourteen" }),
    );
  });

  it("refreshes the opaque session through the CSRF-protected client", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ user, csrf_token: "refreshed-csrf" }), {
        status: 200,
      }),
    );
    vi.stubGlobal("fetch", fetchMock);
    setCsrfToken("refreshed-csrf");

    await expect(refreshSession()).resolves.toMatchObject({ user });
    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(fetchMock.mock.calls[0][0]).toBe("/api/auth/refresh");
    expect(init.method).toBe("POST");
    expect(new Headers(init.headers).get("X-CSRF-Token")).toBe("refreshed-csrf");
  });

  it("counts permanent password length as Unicode characters", () => {
    const fourteenCodePoints = "\u{1F600}".repeat(14);
    expect(
      activationRequestSchema.safeParse({
        code: "single-use-code",
        password: fourteenCodePoints,
      }).success,
    ).toBe(true);
  });
});
