import { describe, expect, it } from "vitest";

import { isUuid, normalizeUuid, requireUuid } from "@/lib/uuid";

const UUID = "123E4567-E89B-12D3-A456-426614174000";

describe("UUID helpers", () => {
  it("accept and normalize canonical UUIDs", () => {
    expect(normalizeUuid(UUID)).toBe(UUID.toLowerCase());
    expect(isUuid(UUID)).toBe(true);
  });

  it.each([
    "",
    "123e4567e89b12d3a456426614174000",
    "{123e4567-e89b-12d3-a456-426614174000}",
    "123e4567-e89b-12d3-a456-42661417400z",
  ])("rejects non-canonical input %j", (value) => {
    expect(normalizeUuid(value)).toBeNull();
    expect(isUuid(value)).toBe(false);
  });

  it("throws a labelled error when a UUID is required", () => {
    expect(() => requireUuid("bad", "chat")).toThrow(
      "chat must be a valid UUID.",
    );
  });
});
