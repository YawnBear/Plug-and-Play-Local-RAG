import type { ReactNode } from "react";

const targetStyle = { minHeight: 44 } as const;

export function LoadingState({ label = "Loading…" }: { label?: string }) {
  return (
    <p role="status" aria-live="polite" className="async-state async-state--loading">
      {label}
    </p>
  );
}

export function EmptyState({
  title,
  children,
  action,
}: {
  title: string;
  children?: ReactNode;
  action?: ReactNode;
}) {
  return (
    <section className="async-state async-state--empty" aria-labelledby="empty-title">
      <h2 id="empty-title">{title}</h2>
      {children}
      {action}
    </section>
  );
}

export function ErrorState({
  message,
  onRetry,
  retryLabel = "Try again",
}: {
  message: string;
  onRetry?: () => void;
  retryLabel?: string;
}) {
  return (
    <div className="async-state async-state--error">
      <p role="alert">{message}</p>
      {onRetry ? (
        <button type="button" onClick={onRetry} style={targetStyle}>
          {retryLabel}
        </button>
      ) : null}
    </div>
  );
}
