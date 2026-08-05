"use client";

import {
  useEffect,
  useId,
  useRef,
  type CSSProperties,
  type PointerEvent,
  type ReactNode,
  type RefObject,
} from "react";

import { Icon } from "@/components/shell/icons";
import { Tooltip } from "./tooltip";

const targetStyle: CSSProperties = { minHeight: 44, minWidth: 44 };

const FOCUSABLE =
  '[data-dialog-autofocus], button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

export interface NativeDialogProps {
  open: boolean;
  onClose: () => void;
  title: string;
  children: ReactNode;
  description?: string;
  className?: string;
  initialFocusRef?: RefObject<HTMLElement | null>;
  closeDisabled?: boolean;
  closeOnBackdrop?: boolean;
}

export function NativeDialog({
  open,
  onClose,
  title,
  children,
  description,
  className,
  initialFocusRef,
  closeDisabled = false,
  closeOnBackdrop = false,
}: NativeDialogProps) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const headingRef = useRef<HTMLHeadingElement>(null);
  const returnFocusRef = useRef<HTMLElement | null>(null);
  const titleId = useId();
  const descriptionId = useId();

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    if (!open) {
      if (dialog.open) {
        if (typeof dialog.close === "function") dialog.close();
        else dialog.removeAttribute("open");
      }
      return;
    }

    returnFocusRef.current =
      document.activeElement instanceof HTMLElement
        ? document.activeElement
        : null;
    if (!dialog.open) {
      if (typeof dialog.showModal === "function") dialog.showModal();
      else dialog.setAttribute("open", "");
    }
    queueMicrotask(() => {
      const requested = initialFocusRef?.current;
      const first = dialog.querySelector<HTMLElement>(FOCUSABLE);
      (requested ?? first ?? headingRef.current)?.focus();
    });

    return () => {
      const returnTarget = returnFocusRef.current;
      queueMicrotask(() => {
        if (returnTarget?.isConnected) returnTarget.focus();
      });
    };
  }, [initialFocusRef, open]);

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    const handleCancel = (event: Event): void => {
      event.preventDefault();
      if (!closeDisabled) onClose();
    };
    dialog.addEventListener("cancel", handleCancel);
    return () => dialog.removeEventListener("cancel", handleCancel);
  }, [closeDisabled, onClose]);

  return (
    <dialog
      ref={dialogRef}
      aria-hidden={open ? undefined : true}
      inert={open ? undefined : true}
      aria-labelledby={titleId}
      aria-describedby={description ? descriptionId : undefined}
      className={className}
      onPointerDown={(event: PointerEvent<HTMLDialogElement>) => {
        if (!closeOnBackdrop || closeDisabled) return;
        const bounds = event.currentTarget.getBoundingClientRect();
        const outside =
          event.clientX < bounds.left ||
          event.clientX > bounds.right ||
          event.clientY < bounds.top ||
          event.clientY > bounds.bottom;
        if (outside) onClose();
      }}
    >
      <header className="dialog-header">
        <h2 id={titleId} ref={headingRef} tabIndex={-1}>
          {title}
        </h2>
        <Tooltip content="Close"><button
          type="button"
          onClick={onClose}
          disabled={closeDisabled}
          aria-label={`Close ${title}`}
          style={targetStyle}
        >
          <Icon name="close" />
        </button></Tooltip>
      </header>
      {description ? <p id={descriptionId}>{description}</p> : null}
      {children}
    </dialog>
  );
}
