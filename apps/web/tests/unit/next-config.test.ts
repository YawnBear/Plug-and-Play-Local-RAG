import { describe, expect, it } from "vitest";

import nextConfig, { apiRewrites } from "../../next.config";

describe("Next API rewrites", () => {
  it("allows bounded long-running local System operations", () => {
    expect(nextConfig.experimental?.proxyTimeout).toBe(15 * 60 * 1_000);
  });

  it("keeps the catch-all API proxy in fallback so explicit routes win", () => {
    expect(apiRewrites("http://127.0.0.1:8000/")).toEqual({
      fallback: [
        {
          source: "/api/:path*",
          destination: "http://127.0.0.1:8000/api/:path*",
        },
      ],
    });
  });

  it("does not create an API fallback without an internal development URL", () => {
    expect(apiRewrites(undefined)).toEqual({ fallback: [] });
  });
});
