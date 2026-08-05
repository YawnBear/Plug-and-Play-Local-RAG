import {
  ApiError,
  jsonRequest,
  requestJson,
  requestVoid,
  setCsrfToken,
} from "@/lib/http";

import {
  activationRequestSchema,
  authMeSchema,
  authSessionSchema,
  loginRequestSchema,
  passwordChangeRequestSchema,
  type AuthMe,
  type AuthSession,
} from "./contracts";

function rememberSession(session: AuthSession): AuthSession {
  setCsrfToken(session.csrf_token);
  return session;
}

export async function getCurrentUser(signal?: AbortSignal): Promise<AuthMe> {
  const result = await requestJson("/api/auth/me", authMeSchema, {
    cache: "no-store",
    signal,
  });
  setCsrfToken(result.csrf_token);
  return result;
}

async function preparePreAuthCsrf(signal?: AbortSignal): Promise<void> {
  try {
    await getCurrentUser(signal);
  } catch (error) {
    if (
      !(error instanceof ApiError) ||
      error.status !== 401 ||
      error.code !== "session_expired"
    ) {
      throw error;
    }
    await getCurrentUser(signal);
  }
}

export async function login(
  username: string,
  password: string,
  signal?: AbortSignal,
): Promise<AuthSession> {
  const body = loginRequestSchema.parse({ username, password });
  await preparePreAuthCsrf(signal);
  return rememberSession(
    await requestJson(
      "/api/auth/login",
      authSessionSchema,
      jsonRequest(body, { method: "POST", signal }),
    ),
  );
}

export async function logout(signal?: AbortSignal): Promise<void> {
  try {
    await requestVoid("/api/auth/logout", { method: "POST", signal });
  } finally {
    setCsrfToken(null);
  }
}

export async function refreshSession(signal?: AbortSignal): Promise<AuthSession> {
  return rememberSession(
    await requestJson("/api/auth/refresh", authSessionSchema, {
      method: "POST",
      signal,
    }),
  );
}

export async function changePassword(
  currentPassword: string,
  newPassword: string,
  signal?: AbortSignal,
): Promise<AuthSession> {
  const body = passwordChangeRequestSchema.parse({
    current_password: currentPassword,
    new_password: newPassword,
  });
  return rememberSession(
    await requestJson(
      "/api/auth/password",
      authSessionSchema,
      jsonRequest(body, { method: "POST", signal }),
    ),
  );
}

export async function activate(
  code: string,
  password: string,
  signal?: AbortSignal,
): Promise<AuthSession> {
  const body = activationRequestSchema.parse({ code, password });
  await preparePreAuthCsrf(signal);
  return rememberSession(
    await requestJson(
      "/api/auth/activate",
      authSessionSchema,
      jsonRequest(body, { method: "POST", signal }),
    ),
  );
}
