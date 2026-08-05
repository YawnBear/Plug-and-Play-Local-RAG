import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({
  usePathname: () => "/",
  useRouter: () => ({ refresh: vi.fn(), replace: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
}));

import { AppShell } from "@/components/shell/app-shell";

describe("Home shell landmarks", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("has exactly one main landmark and one skip link", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            user: {
              id: "11111111-1111-4111-8111-111111111111",
              username: "reader",
              display_name: "Reader",
              role: "member",
              status: "active",
            },
            csrf_token: "csrf-test",
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        ),
      ),
    );
    render(
      <AppShell>
        <section aria-label="Empty chat">Ask your documents</section>
      </AppShell>,
    );
    expect(
      await screen.findAllByRole("link", { name: "Skip to main content" }),
    ).toHaveLength(1);
    expect(screen.getAllByRole("main")).toHaveLength(1);
  });
});
