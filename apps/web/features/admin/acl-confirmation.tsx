"use client";

import { useEffect, useState } from "react";

import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { ApiError } from "@/lib/http";

import { applyAcl, previewAcl } from "./api";
import type { AclOperation, AclPreview } from "./contracts";

export function AclConfirmation({
  operation,
  subjectName,
  title,
  onClose,
  onApplied,
}: {
  operation: AclOperation | null;
  subjectName: string;
  title: string;
  onClose: () => void;
  onApplied: (authorizationVersion: number) => void;
}) {
  const [preview, setPreview] = useState<AclPreview | null>(null);
  const [confirmation, setConfirmation] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      if (!operation) {
        setPreview(null);
        setConfirmation("");
        setError(null);
        return;
      }
      setBusy(true);
      setError(null);
      void previewAcl(operation, controller.signal)
        .then(setPreview)
        .catch((reason: unknown) => {
          if (!controller.signal.aborted) {
            setError(
              reason instanceof Error ? reason.message : "Preview failed.",
            );
          }
        })
        .finally(() => {
          if (!controller.signal.aborted) setBusy(false);
        });
    }, 0);
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [operation]);

  async function confirm() {
    if (!preview) return;
    if (confirmation !== subjectName) {
      setError(`Type ${subjectName} exactly to confirm.`);
      return;
    }
    setBusy(true);
    setError(null);
    try {
      onApplied(await applyAcl(preview));
    } catch (reason) {
      if (reason instanceof ApiError && reason.status === 409) {
        setPreview(null);
        setError("The administration state changed. Review changes again.");
      } else {
        setError(reason instanceof Error ? reason.message : "Apply failed.");
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <ConfirmDialog
      open={operation !== null}
      onClose={onClose}
      onConfirm={() => void confirm()}
      title={title}
      confirmLabel="Apply change"
      busy={busy}
    >
      {preview ? (
        <>
          {operation?.kind === "set_create_children_grant" ? (
            <p>Read access is unchanged by this capability change.</p>
          ) : null}
          <p>
            This affects <strong>{preview.impact.user_count}</strong>{" "}
            account{preview.impact.user_count === 1 ? "" : "s"},{" "}
            <strong>{preview.impact.node_count}</strong> library node
            {preview.impact.node_count === 1 ? "" : "s"}, and{" "}
            <strong>{preview.impact.document_count}</strong> document
            {preview.impact.document_count === 1 ? "" : "s"}.
          </p>
          {preview.impact.user_ids.length > 0 ? (
            <p className="font-mono text-xs text-[var(--muted-foreground)]">
              Accounts: {preview.impact.user_ids.join(", ")}
            </p>
          ) : null}
          <label htmlFor="acl-confirm-name" className="mt-4 block">
            Type <strong>{subjectName}</strong> to confirm
          </label>
          <input
            id="acl-confirm-name"
            value={confirmation}
            onChange={(event) => setConfirmation(event.target.value)}
            autoComplete="off"
            className="mt-2 w-full rounded-lg border border-[var(--border)] bg-[var(--background)] px-3"
          />
        </>
      ) : busy ? (
        <p role="status">Calculating authoritative impact…</p>
      ) : null}
      {error ? <p role="alert" className="text-[var(--danger)]">{error}</p> : null}
    </ConfirmDialog>
  );
}
