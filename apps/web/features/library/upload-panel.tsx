"use client";

import { useRef, useState, type FormEvent } from "react";

import type { AccountTeams, DocumentUploadAccepted } from "./contracts";

const MAX_UPLOAD_BYTES = 100 * 1024 * 1024;

function message(error: unknown): string {
  return error instanceof Error ? error.message : "Upload failed.";
}

export function UploadPanel({
  folderName,
  notice,
  pollingError,
  accountTeams,
  teamsError,
  teamsLoading,
  canUpload,
  requiresFolder,
  disabled = false,
  onRetryTeams,
  onRetryPolling,
  onUpload,
}: {
  folderName: string;
  notice: string | null;
  pollingError: string | null;
  accountTeams: AccountTeams | null;
  teamsError: string | null;
  teamsLoading: boolean;
  canUpload: boolean;
  requiresFolder: boolean;
  disabled?: boolean;
  onRetryTeams: () => void;
  onRetryPolling: () => void;
  onUpload: (
    file: File,
    teamIds: readonly string[],
  ) => Promise<DocumentUploadAccepted>;
}) {
  const [file, setFile] = useState<File | null>(null);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selectedTeamIds, setSelectedTeamIds] = useState<Set<string>>(
    () => new Set(),
  );
  const [confirmedSingleTeam, setConfirmedSingleTeam] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const teams = accountTeams?.teams ?? [];
  const singleTeam = teams.length === 1 ? teams[0] : null;
  const selected =
    selectedTeamIds.size === 0 && singleTeam
      ? new Set([singleTeam.id])
      : selectedTeamIds;
  const needsTeam = accountTeams?.requires_team_selection === true;
  const teamSelectionValid =
    !needsTeam ||
    (selected.size > 0 && (singleTeam === null || confirmedSingleTeam));

  function choose(selected: File | undefined) {
    setError(null);
    if (!selected) {
      setFile(null);
      return;
    }
    if (
      selected.type !== "application/pdf" &&
      !selected.name.toLocaleLowerCase().endsWith(".pdf")
    ) {
      setFile(null);
      setError("Choose a PDF file.");
      return;
    }
    if (selected.size > MAX_UPLOAD_BYTES) {
      setFile(null);
      setError("The PDF must be 100 MB or smaller.");
      return;
    }
    setFile(selected);
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!file || !teamSelectionValid || !canUpload) return;
    setUploading(true);
    setError(null);
    try {
      await onUpload(file, [...selected]);
      setFile(null);
      if (inputRef.current) inputRef.current.value = "";
    } catch (reason) {
      setError(message(reason));
    } finally {
      setUploading(false);
    }
  }

  if (!canUpload && requiresFolder) {
    return (
      <section
        aria-labelledby="upload-title"
        className="rounded-xl border border-[var(--border)] bg-[var(--surface)] p-4"
      >
        <h2 id="upload-title">Upload PDF</h2>
        <p className="mt-2 text-sm text-[var(--muted-foreground)]">
          Choose a folder you can read before uploading. Member uploads cannot
          be placed at the library root.
        </p>
      </section>
    );
  }

  return (
    <section aria-labelledby="upload-title" className="rounded-xl border border-[var(--border)] bg-[var(--surface)] p-4">
      <h2 id="upload-title">Upload PDF</h2>
      <p className="mt-1 text-sm text-[var(--mute)]">
        Destination: {folderName}
      </p>
      <form onSubmit={submit} className="mt-3">
        <label htmlFor="kb-upload" className="block text-sm font-medium">
          PDF file
        </label>
        <input
          ref={inputRef}
          id="kb-upload"
          type="file"
          accept="application/pdf,.pdf"
          disabled={disabled || uploading}
          onChange={(event) => choose(event.target.files?.[0])}
          className="mt-1 min-h-12 w-full rounded-[4px] border border-[var(--hairline)] bg-[var(--surface-soft)] p-2"
        />
        {teamsLoading ? (
          <p role="status" className="mt-3 text-sm text-[var(--muted-foreground)]">
            Loading team access…
          </p>
        ) : null}
        {teamsError ? (
          <div className="mt-3 rounded-lg border border-[var(--danger)] p-3">
            <p role="alert" className="m-0 text-sm text-[var(--danger)]">
              {teamsError}
            </p>
            <button type="button" onClick={onRetryTeams} className="mt-2 min-h-11 rounded-lg border border-[var(--border)] px-4">
              Retry teams
            </button>
          </div>
        ) : null}
        {!teamsLoading && !teamsError && teams.length === 0 ? (
          <p className="mt-3 rounded-lg bg-[var(--surface-subtle)] p-3 text-sm">
            This upload will be private to you. An administrator can grant
            access later.
          </p>
        ) : null}
        {!teamsLoading && !teamsError && teams.length > 0 ? (
          <fieldset className="mt-4 rounded-lg border border-[var(--border)] p-3">
            <legend className="px-1">Share with teams</legend>
            <p className="mt-1 text-sm text-[var(--muted-foreground)]">
              At least one active team must receive this new file. Duplicate
              uploads never change existing access.
            </p>
            <div className="mt-2 grid gap-1">
              {teams.map((team) => {
                const checked = selected.has(team.id);
                return (
                  <label
                    key={team.id}
                    className="flex min-h-11 items-center gap-3 rounded-lg px-2 hover:bg-[var(--surface-hover)]"
                  >
                    <input
                      type="checkbox"
                      checked={checked}
                      onChange={(event) => {
                        setSelectedTeamIds((current) => {
                          const next =
                            current.size === 0 && singleTeam
                              ? new Set([singleTeam.id])
                              : new Set(current);
                          if (event.target.checked) next.add(team.id);
                          else next.delete(team.id);
                          return next;
                        });
                        if (singleTeam) setConfirmedSingleTeam(false);
                      }}
                    />
                    <span>{team.name}</span>
                  </label>
                );
              })}
            </div>
            {singleTeam ? (
              <label className="mt-2 flex min-h-11 items-center gap-3 rounded-lg border-t border-[var(--border)] px-2 pt-2">
                <input
                  type="checkbox"
                  checked={confirmedSingleTeam}
                  onChange={(event) =>
                    setConfirmedSingleTeam(event.target.checked)
                  }
                />
                <span>I confirm this team will receive access.</span>
              </label>
            ) : null}
          </fieldset>
        ) : null}
        <button
          type="submit"
          disabled={
            disabled ||
            !file ||
            uploading ||
            teamsLoading ||
            teamsError !== null ||
            !teamSelectionValid
          }
          className="mt-3 min-h-11 rounded-lg border border-[var(--foreground)] bg-[var(--foreground)] px-4 font-medium text-[var(--background)] disabled:border-[var(--border)] disabled:bg-[var(--surface-hover)] disabled:text-[var(--muted-foreground)]"
        >
          {uploading ? "Uploading…" : "Upload here"}
        </button>
      </form>
      {notice ? (
        <p role="status" className="mt-3 text-sm leading-7">
          {notice}
        </p>
      ) : null}
      {error ? (
        <p role="alert" className="mt-3 text-sm text-[var(--danger)]">
          {error}
        </p>
      ) : null}
      {pollingError ? (
        <div className="mt-3 border border-[var(--danger)] p-3">
          <p role="alert" className="m-0 text-sm text-[var(--danger)]">
            {pollingError}
          </p>
          <button
            type="button"
            onClick={onRetryPolling}
            className="mt-2 min-h-11 rounded-[4px] border border-[var(--hairline-strong)] px-4"
          >
            Retry status
          </button>
        </div>
      ) : null}
    </section>
  );
}
