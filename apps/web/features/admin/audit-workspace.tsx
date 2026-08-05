"use client";

import { useCallback, useEffect, useState } from "react";

import { listAdminAudit } from "./api";
import { AdminError, AdminPage } from "./admin-page";
import type { AdminAuditEvent } from "./contracts";

export function AuditWorkspace() {
  const [events, setEvents] = useState<AdminAuditEvent[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async (signal?: AbortSignal) => {
    setLoading(true);
    setError(null);
    try {
      setEvents(await listAdminAudit(100, signal));
    } catch (reason) {
      if (!signal?.aborted) {
        setError(reason instanceof Error ? reason.message : "Audit failed to load.");
      }
    } finally {
      if (!signal?.aborted) setLoading(false);
    }
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    const timer = window.setTimeout(() => void load(controller.signal), 0);
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [load]);

  return (
    <AdminPage
      title="Audit"
      description="Review the latest bounded administration and authorization events."
    >
      {error ? <AdminError message={error} onRetry={() => void load()} /> : null}
      {loading ? <p role="status">Loading audit events…</p> : null}
      {!loading && events.length === 0 ? (
        <p className="rounded-xl border border-[var(--border)] p-6 text-[var(--muted-foreground)]">No audit events are available.</p>
      ) : null}
      <ol className="m-0 grid list-none gap-2 p-0">
        {events.map((event) => (
          <li key={event.id} className="grid gap-2 rounded-xl border border-[var(--border)] bg-[var(--surface)] p-4 md:grid-cols-[190px_minmax(180px,1fr)_minmax(160px,1fr)]">
            <time className="font-mono text-sm" dateTime={event.created_at}>
              {new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "medium" }).format(new Date(event.created_at))}
            </time>
            <div>
              <strong>{event.event_type}</strong>
              <span className="block text-sm text-[var(--muted-foreground)]">
                {event.target_type ?? "system"} {event.target_id ? <span className="font-mono">{event.target_id}</span> : null}
              </span>
            </div>
            <span className="font-mono text-sm text-[var(--muted-foreground)]">
              Actor: {event.actor_user_id ?? "system"}
              <span className="block">
                Correlation: {event.correlation_id ?? "none"}
              </span>
            </span>
            {Object.keys(event.details).length > 0 ? (
              <details className="md:col-span-3">
                <summary>Event details</summary>
                <pre className="max-h-56 overflow-auto whitespace-pre-wrap rounded-lg bg-[var(--surface-subtle)] p-3 font-mono text-xs">
                  {JSON.stringify(event.details, null, 2)}
                </pre>
              </details>
            ) : null}
          </li>
        ))}
      </ol>
    </AdminPage>
  );
}
