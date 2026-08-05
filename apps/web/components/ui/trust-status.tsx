import type { ReactNode } from "react";

import { Icon } from "@/components/shell/icons";

export type TrustStatusTone =
  | "verified"
  | "pending"
  | "warning"
  | "danger"
  | "neutral";

export function TrustStatus({
  children,
  tone,
  live = false,
  className,
}: {
  children: ReactNode;
  tone: TrustStatusTone;
  live?: boolean;
  className?: string;
}) {
  const classes = [
    "trust-status",
    `trust-status--${tone}`,
    className,
  ].filter(Boolean);

  return (
    <span
      className={classes.join(" ")}
      aria-live={live ? "polite" : undefined}
      aria-atomic={live ? "true" : undefined}
    >
      {tone === "verified" ? <Icon name="check" /> : null}
      {tone === "warning" ? <Icon name="warning" /> : null}
      {tone === "danger" ? <Icon name="error" /> : null}
      {tone === "pending" || tone === "neutral" ? (
        <span className="trust-status__marker" aria-hidden="true" />
      ) : null}
      <span>{children}</span>
    </span>
  );
}
