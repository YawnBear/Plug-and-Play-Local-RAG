import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import LoginPage from "@/app/login/page";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ refresh: vi.fn(), replace: vi.fn() }),
}));

vi.mock("@/features/auth/auth-provider", () => ({
  useAuth: () => ({ setUser: vi.fn() }),
}));

describe("LoginPage", () => {
  it("shows the inactivity explanation only for the exact expiry reason", async () => {
    const expired = await LoginPage({
      searchParams: Promise.resolve({
        next: "/knowledge-base?document=abc&page=3",
        reason: "expired",
      }),
    });
    const { unmount } = render(expired);
    expect(
      screen.getByText(
        "Your session expired after 30 minutes of inactivity. Sign in to continue.",
      ),
    ).toBeInTheDocument();
    unmount();

    const ordinary = await LoginPage({
      searchParams: Promise.resolve({ reason: "other" }),
    });
    render(ordinary);
    expect(
      screen.queryByText(/expired after 30 minutes of inactivity/i),
    ).not.toBeInTheDocument();
  });
});
