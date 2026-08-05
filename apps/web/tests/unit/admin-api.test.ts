import { afterEach, describe, expect, it, vi } from "vitest";

import { applyAcl, listAdminTeams, previewAcl } from "@/features/admin/api";

const NODE = "11111111-1111-4111-8111-111111111111";
const TEAM = "22222222-2222-4222-8222-222222222222";
const USER = "33333333-3333-4333-8333-333333333333";
const PREVIEW = "44444444-4444-4444-8444-444444444444";

afterEach(() => vi.unstubAllGlobals());

describe("administration API", () => {
  it("parses authoritative team membership", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            teams: [
              {
                id: TEAM,
                name: "Research",
                is_active: true,
                member_ids: [USER],
                member_count: 1,
              },
            ],
          }),
          { status: 200 },
        ),
      ),
    );
    await expect(listAdminTeams()).resolves.toEqual([
      {
        id: TEAM,
        name: "Research",
        is_active: true,
        member_ids: [USER],
        member_count: 1,
      },
    ]);
  });

  it("previews and applies the exact server-bound ACL result", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            preview_id: PREVIEW,
            impact_digest: "a".repeat(64),
            impact: {
              user_ids: [USER],
              node_ids: [NODE],
              document_ids: [],
              user_count: 2,
              node_count: 1,
              document_count: 7,
            },
          }),
          { status: 200 },
        ),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ authorization_version: 9 }), {
          status: 200,
        }),
      );
    vi.stubGlobal("fetch", fetchMock);

    const preview = await previewAcl({
      kind: "set_grant",
      node_id: NODE,
      team_id: TEAM,
      present: true,
    });
    await expect(applyAcl(preview)).resolves.toBe(9);

    expect((fetchMock.mock.calls[0][1] as RequestInit).body).toBe(
      JSON.stringify({
        operation: {
          kind: "set_grant",
          node_id: NODE,
          team_id: TEAM,
          present: true,
        },
      }),
    );
    expect((fetchMock.mock.calls[1][1] as RequestInit).body).toBe(
      JSON.stringify({
        preview_id: PREVIEW,
        impact_digest: "a".repeat(64),
      }),
    );
  });
});
