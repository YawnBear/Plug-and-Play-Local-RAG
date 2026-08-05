import { z } from "zod";

/**
 * Browser requests deliberately stay on the current origin.  The reverse
 * proxy owns the private API origin; exposing it through a public env var
 * would bypass the same-origin and cookie boundary.
 */
export const API_URL = "";
export const SESSION_EXPIRED_EVENT = "rag:session-expired";
export const SESSION_UNAUTHENTICATED_EVENT = "rag:session-unauthenticated";

let csrfToken: string | null = null;

function readCookie(name: string): string | null {
  if (typeof document === "undefined") return null;
  const prefix = `${name}=`;
  const cookie = document.cookie
    .split(";")
    .map((part) => part.trim())
    .find((part) => part.startsWith(prefix));
  return cookie ? decodeURIComponent(cookie.slice(prefix.length)) : null;
}

export function setCsrfToken(token: string | null): void {
  csrfToken = token?.trim() || null;
}

export function getCsrfToken(): string | null {
  return csrfToken ?? readCookie("csrf_token");
}

const validationIssueSchema = z
  .object({
    loc: z.array(z.union([z.string(), z.number()])),
    msg: z.string(),
    type: z.string(),
  })
  .passthrough();

const errorPayloadSchema = z
  .object({
    detail: z.union([
      z.string(),
      z.array(validationIssueSchema),
      z
        .object({
          code: z.string().min(1),
          message: z.string().min(1),
        })
        .strict(),
    ]),
  })
  .passthrough();

export class ApiError extends Error {
  constructor(
    message: string,
    public readonly status?: number,
    public readonly details: readonly string[] = [message],
    public readonly code?: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

function issueMessage(issue: z.infer<typeof validationIssueSchema>): string {
  const path = issue.loc
    .filter((part) => part !== "body")
    .map(String)
    .join(".");
  return path ? `${path}: ${issue.msg}` : issue.msg;
}

export async function apiErrorFromResponse(response: Response): Promise<ApiError> {
  let payload: unknown;
  try {
    payload = await response.clone().json();
  } catch {
    payload = null;
  }

  const parsed = errorPayloadSchema.safeParse(payload);
  if (parsed.success) {
    if (typeof parsed.data.detail === "string") {
      return new ApiError(parsed.data.detail, response.status);
    }
    if (!Array.isArray(parsed.data.detail)) {
      return new ApiError(
        parsed.data.detail.message,
        response.status,
        [parsed.data.detail.message],
        parsed.data.detail.code,
      );
    }
    const details = parsed.data.detail.map(issueMessage);
    const message =
      details.length > 0
        ? details.join("; ")
        : `${response.status} ${response.statusText}`.trim();
    return new ApiError(message, response.status, details);
  }

  let text = "";
  try {
    text = (await response.text()).trim();
  } catch {
    // A network intermediary can make even an error body unreadable.
  }
  const fallback =
    text || `${response.status} ${response.statusText}`.trim() || "Request failed.";
  return new ApiError(fallback, response.status);
}

export function apiUrl(path: string): string {
  if (!path.startsWith("/")) {
    throw new TypeError("API paths must start with '/'.");
  }
  return path;
}

export function safeNextPath(value: string | null | undefined): string {
  if (!value || !value.startsWith("/") || value.startsWith("//")) return "/";
  if (/[\u0000-\u001f\u007f\\]/u.test(value)) return "/";

  let decoded: string;
  try {
    decoded = decodeURIComponent(value);
  } catch {
    return "/";
  }
  if (
    !decoded.startsWith("/") ||
    decoded.startsWith("//") ||
    /[\u0000-\u001f\u007f\\]/u.test(decoded)
  ) {
    return "/";
  }

  try {
    const decodedUrl = new URL(decoded, "http://local-rag.invalid");
    const url = new URL(value, "http://local-rag.invalid");
    if (
      url.origin !== "http://local-rag.invalid" ||
      decodedUrl.origin !== "http://local-rag.invalid"
    ) {
      return "/";
    }
    if (
      decodedUrl.pathname === "/login" ||
      decodedUrl.pathname === "/setup" ||
      decodedUrl.pathname.startsWith("/activate")
    ) {
      return "/";
    }
    return `${url.pathname}${url.search}${url.hash}`;
  } catch {
    return "/";
  }
}

function isAbortError(error: unknown): boolean {
  return (
    typeof error === "object" &&
    error !== null &&
    "name" in error &&
    error.name === "AbortError"
  );
}

export async function apiFetch(
  path: string,
  init: RequestInit = {},
): Promise<Response> {
  const method = (init.method ?? "GET").toUpperCase();
  const headers = new Headers(init.headers);
  if (!["GET", "HEAD", "OPTIONS"].includes(method)) {
    const token = getCsrfToken();
    if (token && !headers.has("X-CSRF-Token")) {
      headers.set("X-CSRF-Token", token);
    }
  }
  const requestInit: RequestInit = {
    ...init,
    headers,
    credentials: "same-origin",
    cache: init.cache ?? "no-store",
  };
  let response: Response;
  try {
    response = await fetch(apiUrl(path), requestInit);
  } catch (error) {
    if (init.signal?.aborted) throw init.signal.reason ?? error;
    if (isAbortError(error)) throw error;
    const detail = error instanceof Error ? error.message : String(error);
    throw new ApiError(
      "Unable to reach the API. Check that the local API service is running and try again.",
      undefined,
      [detail || "Network request failed."],
    );
  }
  const responseCsrf = response.headers.get("X-CSRF-Token");
  if (responseCsrf) setCsrfToken(responseCsrf);
  if (!response.ok) {
    const error = await apiErrorFromResponse(response);
    if (
      response.status === 401 &&
      typeof window !== "undefined" &&
      path !== "/api/auth/login" &&
      path !== "/api/auth/activate" &&
      !path.startsWith("/api/setup/")
    ) {
      window.dispatchEvent(
        new Event(
          error.code === "session_expired"
            ? SESSION_EXPIRED_EVENT
            : SESSION_UNAUTHENTICATED_EVENT,
        ),
      );
    }
    throw error;
  }
  return response;
}

export async function requestJson<T>(
  path: string,
  schema: z.ZodType<T>,
  init: RequestInit = {},
): Promise<T> {
  const response = await apiFetch(path, init);
  let payload: unknown;
  try {
    payload = await response.json();
  } catch (error) {
    throw new ApiError("The server returned invalid JSON.", response.status, [
      error instanceof Error ? error.message : "Invalid JSON response.",
    ]);
  }
  const parsed = schema.safeParse(payload);
  if (!parsed.success) {
    throw new ApiError("The server returned an unexpected response.", response.status, [
      ...parsed.error.issues.map((issue) => issue.message),
    ]);
  }
  return parsed.data;
}

export async function requestVoid(
  path: string,
  init: RequestInit = {},
): Promise<void> {
  await apiFetch(path, init);
}

export function jsonRequest(body: unknown, init: RequestInit = {}): RequestInit {
  const headers = new Headers(init.headers);
  headers.set("Content-Type", "application/json");
  return { ...init, headers, body: JSON.stringify(body) };
}
