import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import SetupPage from "@/app/setup/page";
import {
  createSetupOwner,
  getSetupStatus,
  verifySetupCode,
} from "@/features/setup/api";

vi.mock("@/features/setup/api", () => ({
  createSetupOwner: vi.fn(),
  getSetupStatus: vi.fn(),
  verifySetupCode: vi.fn(),
}));

const mockedStatus = vi.mocked(getSetupStatus);
const mockedVerify = vi.mocked(verifySetupCode);
const mockedCreate = vi.mocked(createSetupOwner);

describe("Personal owner setup", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockedStatus.mockResolvedValue({
      state: "setup_required",
      code_issued: true,
      code_expires_at: "2026-08-02T04:15:00Z",
      attempts_remaining: 5,
    });
    mockedVerify.mockResolvedValue();
    mockedCreate.mockResolvedValue({
      state: "setup_complete",
      login_path: "/login",
      first_document_path: "/knowledge-base",
    });
  });

  it("uses a two-step password-manager-compatible flow with no default identity", async () => {
    const user = userEvent.setup();
    render(<SetupPage />);

    expect(
      await screen.findByRole("heading", { name: "Enter the one-time setup code" }),
    ).toBeInTheDocument();
    const code = screen.getByLabelText("One-time setup code");
    expect(code).toHaveAttribute("autocomplete", "one-time-code");
    await user.type(code, "private-setup-code");
    await user.click(screen.getByRole("button", { name: "Continue" }));

    expect(mockedVerify).toHaveBeenCalledWith("private-setup-code");
    const ownerHeading = await screen.findByRole("heading", {
      name: "Create your owner account",
    });
    expect(ownerHeading).toHaveFocus();
    const username = screen.getByLabelText("Username");
    const displayName = screen.getByLabelText("Display name");
    const password = screen.getByLabelText("Password");
    expect(username).toHaveValue("");
    expect(displayName).toHaveValue("");
    expect(password).toHaveAttribute("autocomplete", "new-password");
    expect(screen.getByLabelText("Confirm password")).toHaveAttribute(
      "autocomplete",
      "new-password",
    );
  });

  it("focuses the error summary and completes with a guided login handoff", async () => {
    const user = userEvent.setup();
    render(<SetupPage />);
    await user.type(await screen.findByLabelText("One-time setup code"), "private-code");
    await user.click(screen.getByRole("button", { name: "Continue" }));

    await user.type(await screen.findByLabelText("Username"), "Owner.One");
    await user.type(screen.getByLabelText("Display name"), "Owner One");
    await user.type(screen.getByLabelText("Password"), "fourteen-chars!");
    await user.type(screen.getByLabelText("Confirm password"), "different-value");
    await user.click(screen.getByRole("button", { name: "Create owner account" }));

    const alert = screen.getByRole("alert");
    expect(alert).toHaveFocus();
    expect(alert).toHaveTextContent("password entries do not match");
    await user.clear(screen.getByLabelText("Confirm password"));
    await user.type(screen.getByLabelText("Confirm password"), "fourteen-chars!");
    await user.click(screen.getByRole("button", { name: "Create owner account" }));

    await waitFor(() =>
      expect(mockedCreate).toHaveBeenCalledWith({
        username: "owner.one",
        display_name: "Owner One",
        password: "fourteen-chars!",
      }),
    );
    const handoff = await screen.findByRole("link", { name: "Continue to sign in" });
    expect(handoff).toHaveAttribute(
      "href",
      "/login?next=%2Fknowledge-base&setup=complete",
    );
  });

  it("gives local recovery guidance when no usable code is active", async () => {
    mockedStatus.mockResolvedValue({
      state: "setup_required",
      code_issued: false,
      code_expires_at: null,
      attempts_remaining: 0,
    });
    render(<SetupPage />);

    expect(await screen.findByText(/Issue new setup code/u)).toBeInTheDocument();
    expect(screen.getByLabelText("One-time setup code")).toBeDisabled();
    expect(screen.getByRole("button", { name: "Continue" })).toBeDisabled();
  });
});
