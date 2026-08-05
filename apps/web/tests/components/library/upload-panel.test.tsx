import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { UploadPanel } from "@/features/library/upload-panel";

import { uploadAccepted } from "../../library-fixtures";

describe("upload panel", () => {
  it("validates a PDF locally and submits the selected file", async () => {
    const user = userEvent.setup({ applyAccept: false });
    const upload = vi.fn().mockResolvedValue(uploadAccepted());
    render(
      <UploadPanel
        folderName="Alpha"
        notice={null}
        pollingError={null}
        accountTeams={{ teams: [], requires_team_selection: false }}
        teamsError={null}
        teamsLoading={false}
        canUpload
        requiresFolder={false}
        onRetryTeams={vi.fn()}
        onRetryPolling={vi.fn()}
        onUpload={upload}
      />,
    );
    const input = screen.getByLabelText("PDF file");

    await user.upload(
      input,
      new File(["plain"], "notes.txt", { type: "text/plain" }),
    );
    expect(screen.getByRole("alert")).toHaveTextContent("Choose a PDF file.");
    expect(screen.getByRole("button", { name: "Upload here" })).toBeDisabled();

    const pdf = new File(["%PDF"], "evidence.pdf", {
      type: "application/pdf",
    });
    await user.upload(input, pdf);
    await user.click(screen.getByRole("button", { name: "Upload here" }));
    expect(upload).toHaveBeenCalledWith(pdf, []);
    expect(input).toHaveValue("");
  });

  it("preselects a single team but requires explicit confirmation", async () => {
    const user = userEvent.setup();
    const upload = vi.fn().mockResolvedValue(uploadAccepted());
    render(
      <UploadPanel
        folderName="Alpha"
        notice={null}
        pollingError={null}
        accountTeams={{
          teams: [
            {
              id: "11111111-1111-4111-8111-111111111111",
              name: "Research",
              is_active: true,
            },
          ],
          requires_team_selection: true,
        }}
        teamsError={null}
        teamsLoading={false}
        canUpload
        requiresFolder
        onRetryTeams={vi.fn()}
        onRetryPolling={vi.fn()}
        onUpload={upload}
      />,
    );

    const pdf = new File(["%PDF"], "evidence.pdf", {
      type: "application/pdf",
    });
    await user.upload(screen.getByLabelText("PDF file"), pdf);
    expect(screen.getByLabelText("Research")).toBeChecked();
    expect(screen.getByRole("button", { name: "Upload here" })).toBeDisabled();

    await user.click(
      screen.getByLabelText("I confirm this team will receive access."),
    );
    await user.click(screen.getByRole("button", { name: "Upload here" }));
    expect(upload).toHaveBeenCalledWith(pdf, [
      "11111111-1111-4111-8111-111111111111",
    ]);
  });

  it("requires a member to choose at least one of multiple active teams", async () => {
    const user = userEvent.setup();
    const upload = vi.fn().mockResolvedValue(uploadAccepted());
    render(
      <UploadPanel
        folderName="Alpha"
        notice={null}
        pollingError={null}
        accountTeams={{
          teams: [
            { id: "11111111-1111-4111-8111-111111111111", name: "Alpha", is_active: true },
            { id: "22222222-2222-4222-8222-222222222222", name: "Beta", is_active: true },
          ],
          requires_team_selection: true,
        }}
        teamsError={null}
        teamsLoading={false}
        canUpload
        requiresFolder
        onRetryTeams={vi.fn()}
        onRetryPolling={vi.fn()}
        onUpload={upload}
      />,
    );
    const pdf = new File(["%PDF"], "evidence.pdf", {
      type: "application/pdf",
    });
    await user.upload(screen.getByLabelText("PDF file"), pdf);
    expect(screen.getByRole("button", { name: "Upload here" })).toBeDisabled();
    await user.click(screen.getByLabelText("Beta"));
    await user.click(screen.getByRole("button", { name: "Upload here" }));
    expect(upload).toHaveBeenCalledWith(pdf, [
      "22222222-2222-4222-8222-222222222222",
    ]);
  });

  it("explains why a member cannot upload at the synthetic root", () => {
    render(
      <UploadPanel
        folderName="Root"
        notice={null}
        pollingError={null}
        accountTeams={{ teams: [], requires_team_selection: false }}
        teamsError={null}
        teamsLoading={false}
        canUpload={false}
        requiresFolder
        onRetryTeams={vi.fn()}
        onRetryPolling={vi.fn()}
        onUpload={vi.fn()}
      />,
    );
    expect(screen.getByText(/choose a folder you can read/i)).toBeVisible();
    expect(screen.queryByLabelText("PDF file")).not.toBeInTheDocument();
  });

  it("shows duplicate-location and actionable polling feedback", async () => {
    const user = userEvent.setup();
    const retry = vi.fn();
    render(
      <UploadPanel
        folderName="Beta"
        notice="This PDF already exists at /Beta/Evidence.pdf. Its canonical location was reused."
        pollingError="Status service offline."
        accountTeams={{ teams: [], requires_team_selection: false }}
        teamsError={null}
        teamsLoading={false}
        canUpload
        requiresFolder={false}
        onRetryTeams={vi.fn()}
        onRetryPolling={retry}
        onUpload={vi.fn()}
      />,
    );
    expect(screen.getByText(/already exists.*canonical location/i)).toBeVisible();
    expect(screen.getByRole("alert")).toHaveTextContent(
      "Status service offline.",
    );
    await user.click(screen.getByRole("button", { name: "Retry status" }));
    expect(retry).toHaveBeenCalledOnce();
  });
});
