import type { NextConfig } from "next";

export function apiRewrites(configuredUrl: string | undefined) {
  const internalApiUrl = configuredUrl?.replace(/\/+$/, "");
  if (!internalApiUrl) return { fallback: [] };
  return {
    fallback: [
      {
        source: "/api/:path*",
        destination: `${internalApiUrl}/api/:path*`,
      },
    ],
  };
}

const nextConfig: NextConfig = {
  output: "standalone",
  experimental: {
    // Local CPU validation and backup operations can legitimately take several
    // minutes. Keep the same-origin API rewrite open for their bounded runtime.
    proxyTimeout: 15 * 60 * 1_000,
  },
  async rewrites() {
    return apiRewrites(process.env.INTERNAL_API_URL);
  },
};

export default nextConfig;
