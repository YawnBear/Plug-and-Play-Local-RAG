import { normalizeUuid } from "@/lib/uuid";

const FORWARDED_REQUEST_HEADERS = [
  "accept",
  "content-type",
  "cookie",
  "origin",
  "x-csrf-token",
] as const;

const HOP_BY_HOP_RESPONSE_HEADERS = [
  "connection",
  "keep-alive",
  "proxy-authenticate",
  "proxy-authorization",
  "te",
  "trailer",
  "transfer-encoding",
  "upgrade",
] as const;

function relayBody(
  upstream: ReadableStream<Uint8Array> | null,
): ReadableStream<Uint8Array> | null {
  if (upstream === null) return null;
  const reader = upstream.getReader();
  return new ReadableStream<Uint8Array>({
    async pull(controller) {
      try {
        const { done, value } = await reader.read();
        if (done) {
          controller.close();
          return;
        }
        controller.enqueue(value);
      } catch (error) {
        controller.error(error);
      }
    },
    async cancel(reason) {
      await reader.cancel(reason);
    },
  });
}

function internalApiUrl(): string {
  const configured = process.env.INTERNAL_API_URL?.trim().replace(/\/+$/, "");
  if (!configured) {
    throw new Error(
      "INTERNAL_API_URL is required when Next.js handles a chat stream route.",
    );
  }
  return configured;
}

function invalidIdentifier(label: string): Response {
  return Response.json(
    {
      detail: [
        {
          type: "uuid_parsing",
          loc: ["path", label],
          msg: "Input should be a valid UUID.",
        },
      ],
    },
    { status: 422 },
  );
}

export async function proxyChatStream(
  request: Request,
  path: string,
): Promise<Response> {
  const headers = new Headers();
  for (const name of FORWARDED_REQUEST_HEADERS) {
    const value = request.headers.get(name);
    if (value !== null) headers.set(name, value);
  }

  const body = request.body === null ? undefined : await request.arrayBuffer();
  const upstream = await fetch(`${internalApiUrl()}${path}`, {
    method: "POST",
    headers,
    body,
    cache: "no-store",
    signal: request.signal,
  });
  const responseHeaders = new Headers(upstream.headers);
  const setCookies =
    (
      upstream.headers as Headers & {
        getSetCookie?: () => string[];
      }
    ).getSetCookie?.() ?? [];
  if (setCookies.length > 0) {
    responseHeaders.delete("set-cookie");
    for (const cookie of setCookies) responseHeaders.append("set-cookie", cookie);
  }
  for (const name of HOP_BY_HOP_RESPONSE_HEADERS) {
    responseHeaders.delete(name);
  }
  responseHeaders.delete("content-encoding");
  responseHeaders.delete("content-length");
  const cacheControl = responseHeaders.get("cache-control");
  if (cacheControl && !/\bno-transform\b/i.test(cacheControl)) {
    responseHeaders.set("cache-control", `${cacheControl}, no-transform`);
  }

  return new Response(relayBody(upstream.body), {
    status: upstream.status,
    statusText: upstream.statusText,
    headers: responseHeaders,
  });
}

export function chatStreamIdentifier(
  value: string,
  label: string,
): string | Response {
  return normalizeUuid(value) ?? invalidIdentifier(label);
}
