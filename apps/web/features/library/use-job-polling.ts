"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError } from "@/lib/http";

import { getJob } from "./api";
import type { JobStatus } from "./contracts";

const TERMINAL = new Set(["completed", "failed", "interrupted"]);
const NORMAL_DELAY = 2_000;
const MAX_FAILURE_DELAY = 30_000;
const STORAGE_KEY = "local-rag.pending-ingestion-jobs.v1";

export function pollingDelay(failures: number): number {
  if (failures <= 0) return NORMAL_DELAY;
  return Math.min(
    MAX_FAILURE_DELAY,
    NORMAL_DELAY * 2 ** Math.min(failures - 1, 10),
  );
}

export interface TrackedJob {
  jobId: string;
  documentId: string;
  status: string;
  failures: number;
}

type TrackableJob = {
  document_id: string;
  job_id: string;
  status: string;
};

function isTrackedJob(value: unknown): value is TrackedJob {
  if (typeof value !== "object" || value === null) return false;
  return (
    "jobId" in value &&
    typeof value.jobId === "string" &&
    "documentId" in value &&
    typeof value.documentId === "string" &&
    "status" in value &&
    typeof value.status === "string" &&
    "failures" in value &&
    typeof value.failures === "number" &&
    Number.isInteger(value.failures) &&
    value.failures >= 0 &&
    !TERMINAL.has(value.status)
  );
}

function restoreTracked(): Record<string, TrackedJob> {
  if (typeof window === "undefined") return {};
  try {
    const value: unknown = JSON.parse(
      window.sessionStorage.getItem(STORAGE_KEY) ?? "{}",
    );
    if (typeof value !== "object" || value === null) return {};
    return Object.fromEntries(
      Object.entries(value).filter(
        (entry): entry is [string, TrackedJob] =>
          isTrackedJob(entry[1]) && entry[0] === entry[1].jobId,
      ),
    );
  } catch {
    return {};
  }
}

function persistTracked(tracked: Record<string, TrackedJob>): void {
  if (typeof window === "undefined") return;
  try {
    if (Object.keys(tracked).length === 0) {
      window.sessionStorage.removeItem(STORAGE_KEY);
    } else {
      window.sessionStorage.setItem(STORAGE_KEY, JSON.stringify(tracked));
    }
  } catch {
    // Polling still works in-memory when storage is unavailable.
  }
}

export function useJobPolling({
  onJob,
  onTerminal,
}: {
  onJob: (job: JobStatus) => void;
  onTerminal: (job: JobStatus) => void;
}) {
  const [tracked, setTracked] = useState<Record<string, TrackedJob>>({});
  const [pollError, setPollError] = useState<string | null>(null);
  const trackedRef = useRef(tracked);
  const callbacks = useRef({ onJob, onTerminal });
  const timer = useRef<number | null>(null);
  const controller = useRef<AbortController | null>(null);
  const inFlight = useRef(false);
  const disposed = useRef(false);

  useEffect(() => {
    trackedRef.current = tracked;
  }, [tracked]);
  useEffect(() => {
    callbacks.current = { onJob, onTerminal };
  }, [onJob, onTerminal]);

  const clearTimer = useCallback(() => {
    if (timer.current !== null) {
      window.clearTimeout(timer.current);
      timer.current = null;
    }
  }, []);

  const schedule = useCallback(
    (delay: number, poll: () => void) => {
      clearTimer();
      if (
        disposed.current ||
        document.visibilityState === "hidden" ||
        Object.keys(trackedRef.current).length === 0
      ) {
        return;
      }
      timer.current = window.setTimeout(poll, delay);
    },
    [clearTimer],
  );

  const pollRef = useRef<() => void>(() => undefined);
  const poll = useCallback(() => {
    if (
      disposed.current ||
      inFlight.current ||
      document.visibilityState === "hidden"
    ) {
      return;
    }
    const active = Object.values(trackedRef.current).filter(
      (item) => !TERMINAL.has(item.status),
    );
    if (active.length === 0) return;
    inFlight.current = true;
    const nextController = new AbortController();
    controller.current = nextController;
    void Promise.allSettled(
      active.map(async (item) => ({
        item,
        job: await getJob(item.jobId, nextController.signal),
      })),
    ).then((results) => {
      if (disposed.current || nextController.signal.aborted) return;
      const next = { ...trackedRef.current };
      let maximumFailures = 0;
      let failureReason: unknown = null;
      for (const [index, result] of results.entries()) {
        const item =
          result.status === "fulfilled" ? result.value.item : active[index];
        if (!item || !next[item.jobId]) continue;
        if (result.status === "fulfilled") {
          const { job } = result.value;
          callbacks.current.onJob(job);
          next[item.jobId] = {
            ...item,
            status: job.status,
            failures: 0,
          };
          if (TERMINAL.has(job.status)) {
            delete next[item.jobId];
            callbacks.current.onTerminal(job);
          }
        } else {
          if (
            result.reason instanceof ApiError &&
            result.reason.status === 404
          ) {
            delete next[item.jobId];
            continue;
          }
          const failures = (next[item.jobId]?.failures ?? 0) + 1;
          maximumFailures = Math.max(maximumFailures, failures);
          next[item.jobId] = { ...item, failures };
          failureReason ??= result.reason;
        }
      }
      trackedRef.current = next;
      persistTracked(next);
      setTracked(next);
      setPollError(
        failureReason
          ? failureReason instanceof Error
            ? failureReason.message
            : "Unable to refresh ingestion status."
          : null,
      );
      schedule(
        pollingDelay(maximumFailures),
        () => pollRef.current(),
      );
    }).finally(() => {
      if (controller.current === nextController) {
        controller.current = null;
        inFlight.current = false;
      }
    });
  }, [schedule]);

  useEffect(() => {
    pollRef.current = poll;
  }, [poll]);

  useEffect(() => {
    const restored = restoreTracked();
    if (Object.keys(restored).length === 0) return;
    const next = { ...restored, ...trackedRef.current };
    trackedRef.current = next;
    setTracked(next);
    queueMicrotask(() => pollRef.current());
  }, []);

  const track = useCallback((job: TrackableJob) => {
    if (TERMINAL.has(job.status)) return;
    const item: TrackedJob = {
      jobId: job.job_id,
      documentId: job.document_id,
      status: job.status,
      failures: 0,
    };
    const next = { ...trackedRef.current, [item.jobId]: item };
    trackedRef.current = next;
    persistTracked(next);
    setTracked(next);
    clearTimer();
    queueMicrotask(() => pollRef.current());
  }, [clearTimer]);

  const retryNow = useCallback(() => {
    setPollError(null);
    clearTimer();
    pollRef.current();
  }, [clearTimer]);

  const untrackDocument = useCallback(
    (documentId: string) => {
      const next = Object.fromEntries(
        Object.entries(trackedRef.current).filter(
          ([, item]) => item.documentId !== documentId,
        ),
      );
      trackedRef.current = next;
      persistTracked(next);
      setTracked(next);
      if (Object.keys(next).length === 0) {
        clearTimer();
        setPollError(null);
      }
    },
    [clearTimer],
  );

  useEffect(() => {
    disposed.current = false;
    const visibility = () => {
      if (document.visibilityState === "hidden") {
        clearTimer();
        controller.current?.abort();
        controller.current = null;
        inFlight.current = false;
      } else {
        retryNow();
      }
    };
    document.addEventListener("visibilitychange", visibility);
    return () => {
      disposed.current = true;
      document.removeEventListener("visibilitychange", visibility);
      clearTimer();
      controller.current?.abort();
    };
  }, [clearTimer, retryNow]);

  return { tracked, pollError, track, untrackDocument, retryNow };
}
