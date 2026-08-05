"use client";

import { useEffect, useMemo, useRef, useState } from "react";

import { Icon } from "@/components/shell/icons";
import { Tooltip } from "@/components/ui/tooltip";
import { useMotionPresence } from "@/lib/motion";

import type { DocumentSummary, JobStatus } from "./contracts";
import type { TrackedJob } from "./use-job-polling";

const PANEL_OPEN_KEY = "local-rag.kb-panel-open.v1";
const PANEL_HEIGHT_KEY = "local-rag.kb-panel-height.v1";
const PANEL_TAB_KEY = "local-rag.kb-panel-tab.v1";

type PanelTab = "ingestion" | "errors";

function storedHeight(): number {
  if (typeof window === "undefined") return 220;
  const parsed = Number(window.localStorage.getItem(PANEL_HEIGHT_KEY));
  return Number.isFinite(parsed) ? Math.min(480, Math.max(140, parsed)) : 220;
}

export function BottomPanel({
  jobs,
  tracked,
  documents,
  errors,
}: {
  jobs: Readonly<Record<string, JobStatus>>;
  tracked: Readonly<Record<string, TrackedJob>>;
  documents: readonly DocumentSummary[];
  errors: readonly string[];
}) {
  const [open, setOpen] = useState(
    () =>
      typeof window !== "undefined" &&
      window.localStorage.getItem(PANEL_OPEN_KEY) === "true",
  );
  const [height, setHeight] = useState(storedHeight);
  const [active, setActive] = useState<PanelTab>(() => {
    if (typeof window === "undefined") return "ingestion";
    return window.localStorage.getItem(PANEL_TAB_KEY) === "errors"
      ? "errors"
      : "ingestion";
  });
  const previousAttention = useRef(0);
  const activeJobs = Object.keys(tracked).length;
  const attention = activeJobs + errors.length;
  const motion = useMotionPresence(open);

  useEffect(() => {
    const toggle = () => {
      setOpen((current) => {
        const next = !current;
        window.localStorage.setItem(PANEL_OPEN_KEY, String(next));
        return next;
      });
    };
    window.addEventListener("rag:toggle-ingestion-panel", toggle);
    return () => window.removeEventListener("rag:toggle-ingestion-panel", toggle);
  }, []);

  useEffect(() => {
    if (attention > previousAttention.current) {
      window.localStorage.setItem(PANEL_OPEN_KEY, "true");
      queueMicrotask(() => {
        setOpen(true);
        if (errors.length > 0) {
          setActive("errors");
          window.localStorage.setItem(PANEL_TAB_KEY, "errors");
        }
      });
    }
    previousAttention.current = attention;
  }, [attention, errors.length]);

  const rows = useMemo(() => {
    const byDocument = new Map(
      documents.map((document) => [document.document_id, document]),
    );
    const live = new Map(
      Object.values(jobs).map((job) => [job.document_id, job]),
    );
    return Object.values(tracked).map((item) => {
      const job = live.get(item.documentId);
      return {
        id: item.jobId,
        name:
          byDocument.get(item.documentId)?.display_name ?? "Uploaded document",
        status: job?.status ?? item.status,
        stage: job?.stage ?? "Waiting",
        completed: job?.completed_units ?? 0,
        total: job?.total_units ?? null,
      };
    });
  }, [documents, jobs, tracked]);

  function beginResize(event: React.PointerEvent<HTMLDivElement>) {
    event.preventDefault();
    const startY = event.clientY;
    const startHeight = height;
    const move = (next: PointerEvent) => {
      setHeight(Math.min(480, Math.max(140, startHeight + startY - next.clientY)));
    };
    const finish = (next: PointerEvent) => {
      const value = Math.min(
        480,
        Math.max(140, startHeight + startY - next.clientY),
      );
      setHeight(value);
      window.localStorage.setItem(PANEL_HEIGHT_KEY, String(value));
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", finish);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", finish);
  }

  if (motion === "closed") return null;

  return (
    <section
      aria-label="Knowledge Base activity panel"
      className="kb-bottom-panel"
      data-motion={motion}
      style={{ height }}
    >
      <div
        aria-label="Resize activity panel"
        className="kb-bottom-panel__resize"
        onPointerDown={beginResize}
        role="separator"
      />
      <header>
        <div role="tablist" aria-label="Activity panel sections">
          <button
            aria-selected={active === "ingestion"}
            onClick={() => {
              setActive("ingestion");
              window.localStorage.setItem(PANEL_TAB_KEY, "ingestion");
            }}
            role="tab"
            type="button"
          >
            Ingestion
            {activeJobs > 0 ? <span>{activeJobs}</span> : null}
          </button>
          <button
            aria-selected={active === "errors"}
            onClick={() => {
              setActive("errors");
              window.localStorage.setItem(PANEL_TAB_KEY, "errors");
            }}
            role="tab"
            type="button"
          >
            Errors
            {errors.length > 0 ? <span>{errors.length}</span> : null}
          </button>
        </div>
        <Tooltip content="Close activity panel">
          <button
            aria-label="Close activity panel"
            onClick={() => {
              setOpen(false);
              window.localStorage.setItem(PANEL_OPEN_KEY, "false");
            }}
            type="button"
          >
            <Icon name="close" />
          </button>
        </Tooltip>
      </header>
      <div className="kb-bottom-panel__body">
        {active === "ingestion" ? (
          rows.length === 0 ? (
            <p>No active ingestion jobs in this browser session.</p>
          ) : (
            <table>
              <thead>
                <tr>
                  <th>Document</th>
                  <th>Stage</th>
                  <th>Status</th>
                  <th>Progress</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => (
                  <tr key={row.id}>
                    <td>{row.name}</td>
                    <td>{row.stage}</td>
                    <td>{row.status}</td>
                    <td>
                      {row.total === null
                        ? row.completed
                        : `${row.completed}/${row.total}`}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )
        ) : errors.length === 0 ? (
          <p>No current Knowledge Base errors.</p>
        ) : (
          <ul className="kb-bottom-panel__errors">
            {errors.map((error) => (
              <li key={error}>{error}</li>
            ))}
          </ul>
        )}
      </div>
    </section>
  );
}
