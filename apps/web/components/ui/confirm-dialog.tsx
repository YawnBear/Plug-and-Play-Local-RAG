"use client";

import type { ReactNode } from "react";

import { NativeDialog } from "./native-dialog";

const targetStyle = { minHeight: 44 } as const;

export interface ConfirmDialogProps {
  open: boolean;
  onClose: () => void;
  onConfirm: () => void;
  title: string;
  children: ReactNode;
  confirmLabel?: string;
  cancelLabel?: string;
  busy?: boolean;
}

export function ConfirmDialog({
  open,
  onClose,
  onConfirm,
  title,
  children,
  confirmLabel = "Confirm",
  cancelLabel = "Cancel",
  busy = false,
}: ConfirmDialogProps) {
  return (
    <NativeDialog
      open={open}
      onClose={onClose}
      title={title}
      closeDisabled={busy}
    >
      <div className="confirm-dialog__body">{children}</div>
      <div className="dialog-actions">
        <button
          type="button"
          onClick={onClose}
          disabled={busy}
          style={targetStyle}
        >
          {cancelLabel}
        </button>
        <button
          type="button"
          onClick={onConfirm}
          disabled={busy}
          aria-busy={busy || undefined}
          className="button-danger"
          style={targetStyle}
        >
          {busy ? "Working…" : confirmLabel}
        </button>
      </div>
    </NativeDialog>
  );
}
