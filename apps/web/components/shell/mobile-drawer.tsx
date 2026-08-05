"use client";

import { useEffect, useRef, type ReactNode } from "react";

interface MobileDrawerProps {
  children: ReactNode;
  onClose: () => void;
  open: boolean;
}

export function MobileDrawer({ children, onClose, open }: MobileDrawerProps) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const returnFocusRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    if (open) {
      returnFocusRef.current =
        document.activeElement instanceof HTMLElement ? document.activeElement : null;
      if (!dialog.open) dialog.showModal();
      queueMicrotask(() => {
        dialog.querySelector<HTMLElement>("[data-drawer-autofocus]")?.focus();
      });
    } else if (dialog.open) {
      dialog.close();
    }
  }, [open]);

  useEffect(() => {
    if (open) return;
    const target = returnFocusRef.current;
    if (target?.isConnected) queueMicrotask(() => target.focus());
  }, [open]);

  return (
    <dialog
      aria-hidden={open ? undefined : true}
      aria-label="Application navigation"
      className="mobile-drawer"
      inert={open ? undefined : true}
      onCancel={(event) => {
        event.preventDefault();
        onClose();
      }}
      onClick={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
      ref={dialogRef}
    >
      <div className="mobile-drawer__panel">{children}</div>
    </dialog>
  );
}
