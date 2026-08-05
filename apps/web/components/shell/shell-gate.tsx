"use client";

import { useEffect, type ReactNode } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";

import { useAuth } from "@/features/auth/auth-provider";
import { SessionActivityController } from "@/features/auth/session-activity-controller";
import { safeNextPath } from "@/lib/http";

import { ProtectedShell } from "./protected-shell";

function isPublicPath(pathname: string): boolean {
  return (
    pathname === "/login" ||
    pathname === "/setup" ||
    pathname.startsWith("/activate")
  );
}

export function ShellGate({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const router = useRouter();
  const { error, loading, refresh, sessionExpired, user } = useAuth();
  const isPublic = isPublicPath(pathname);

  useEffect(() => {
    if (loading || error) return;
    if (!isPublic && !user) {
      const requested = safeNextPath(
        `${pathname}${searchParams.size ? `?${searchParams.toString()}` : ""}`,
      );
      const reason = sessionExpired ? "&reason=expired" : "";
      router.replace(`/login?next=${encodeURIComponent(requested)}${reason}`);
      return;
    }
    if (pathname === "/login" && user) {
      router.replace(safeNextPath(searchParams.get("next")));
    }
  }, [
    error,
    isPublic,
    loading,
    pathname,
    router,
    searchParams,
    sessionExpired,
    user,
  ]);

  if (isPublic) {
    if (pathname === "/login" && (loading || user)) {
      return <FullPageStatus label="Checking your session…" />;
    }
    return (
      <div className="public-shell">
        <a className="skip-link" href="#main-content">Skip to main content</a>
        <main id="main-content" tabIndex={-1}>{children}</main>
      </div>
    );
  }

  if (loading || (!user && !error)) {
    return <FullPageStatus label="Checking your session…" />;
  }

  if (error) {
    return (
      <main
        className="session-error"
        id="main-content"
        tabIndex={-1}
        aria-labelledby="session-error-title"
      >
        <p className="route-state__eyebrow">Service unavailable</p>
        <h1 id="session-error-title">We could not verify your session</h1>
        <p>{error}</p>
        <button className="button-primary" onClick={() => void refresh()} type="button">
          Try again
        </button>
      </main>
    );
  }

  return (
    <>
      <SessionActivityController
        route={`${pathname}${searchParams.size ? `?${searchParams.toString()}` : ""}`}
      />
      <ProtectedShell>{children}</ProtectedShell>
    </>
  );
}

function FullPageStatus({ label }: { label: string }) {
  return (
    <main className="session-loading" id="main-content" tabIndex={-1}>
      <span className="session-loading__mark" aria-hidden="true">LR</span>
      <span role="status" aria-live="polite">{label}</span>
    </main>
  );
}
