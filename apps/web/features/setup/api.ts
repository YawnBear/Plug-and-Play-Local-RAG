import { jsonRequest, requestJson } from "@/lib/http";

import {
  setupChallengeSchema,
  setupOwnerRequestSchema,
  setupOwnerResponseSchema,
  setupStatusSchema,
  type SetupOwnerResponse,
  type SetupStatus,
} from "./contracts";

export async function getSetupStatus(signal?: AbortSignal): Promise<SetupStatus> {
  return requestJson("/api/setup/status", setupStatusSchema, {
    cache: "no-store",
    signal,
  });
}

export async function verifySetupCode(
  code: string,
  signal?: AbortSignal,
): Promise<void> {
  await requestJson(
    "/api/setup/challenge",
    setupChallengeSchema,
    jsonRequest({ code }, { method: "POST", signal }),
  );
}

export async function createSetupOwner(
  values: { username: string; display_name: string; password: string },
  signal?: AbortSignal,
): Promise<SetupOwnerResponse> {
  const body = setupOwnerRequestSchema.parse(values);
  return requestJson(
    "/api/setup/owner",
    setupOwnerResponseSchema,
    jsonRequest(body, { method: "POST", signal }),
  );
}
