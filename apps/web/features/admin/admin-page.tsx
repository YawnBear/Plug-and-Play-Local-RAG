"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import type { ReactNode } from "react";

import { useWorkspaceSectionNavigation } from "@/components/shell/workspace-section-nav";

const routes = [
  ["/admin/users", "Users"],
  ["/admin/teams", "Teams"],
  ["/admin/access", "Access"],
  ["/admin/audit", "Audit"],
] as const;

export function AdminPage({
  title,
  description,
  children,
}: {
  title: string;
  description: string;
  children: ReactNode;
}) {
  const pathname = usePathname();
  useWorkspaceSectionNavigation("Administration workspace navigation", routes);

  return (
    <div className="admin-workspace">
      <header className="workspace-header">
        <p className="workspace-header__eyebrow">
          Administration
        </p>
        <h1>{title}</h1>
        <p>
          {description}
        </p>
      </header>
      <nav
        aria-label="Administration sections"
        className="admin-tabs"
      >
        {routes.map(([href, label]) => (
          <Link
            key={href}
            href={href}
            aria-current={pathname === href ? "page" : undefined}
            className="admin-tabs__link"
          >
            {label}
          </Link>
        ))}
      </nav>
      {children}
    </div>
  );
}

export function AdminError({
  message,
  onRetry,
}: {
  message: string;
  onRetry: () => void;
}) {
  return (
    <div className="rounded-xl border border-[var(--danger)] bg-[var(--surface)] p-4">
      <p role="alert" className="m-0 text-[var(--danger)]">{message}</p>
      <button
        type="button"
        onClick={onRetry}
        className="mt-3 min-h-11 rounded-lg border border-[var(--border)] px-4"
      >
        Try again
      </button>
    </div>
  );
}
