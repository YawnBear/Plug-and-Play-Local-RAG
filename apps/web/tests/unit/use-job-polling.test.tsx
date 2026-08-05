import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@/lib/http";

vi.mock("@/features/library/api", () => ({
  getJob: vi.fn(),
}));

import { getJob } from "@/features/library/api";
import {
  pollingDelay,
  useJobPolling,
} from "@/features/library/use-job-polling";

import {
  JOB,
  jobStatus,
  uploadAccepted,
} from "../library-fixtures";

describe("job polling", () => {
  let visibility: DocumentVisibilityState;

  beforeEach(() => {
    vi.useFakeTimers();
    visibility = "visible";
    Object.defineProperty(document, "visibilityState", {
      configurable: true,
      get: () => visibility,
    });
    window.sessionStorage.clear();
    vi.clearAllMocks();
  });

  afterEach(() => {
    window.sessionStorage.clear();
    vi.useRealTimers();
  });

  it("uses normal two-second polling and stops at terminal status", async () => {
    const onJob = vi.fn();
    const onTerminal = vi.fn();
    vi.mocked(getJob)
      .mockResolvedValueOnce(jobStatus({ status: "running" }))
      .mockResolvedValueOnce(jobStatus({ status: "completed" }));
    const hook = renderHook(() => useJobPolling({ onJob, onTerminal }));

    act(() => hook.result.current.track(uploadAccepted({ status: "queued" })));
    await act(async () => {
      await vi.runAllTicks();
      await Promise.resolve();
    });
    expect(getJob).toHaveBeenCalledTimes(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2_000);
    });
    expect(getJob).toHaveBeenCalledTimes(2);
    expect(onTerminal).toHaveBeenCalledWith(
      expect.objectContaining({ status: "completed" }),
    );
    expect(hook.result.current.tracked).toEqual({});
    await act(async () => {
      await vi.advanceTimersByTimeAsync(30_000);
    });
    expect(getJob).toHaveBeenCalledTimes(2);
  });

  it("backs failures off at 2/4/8/16 seconds and caps at 30 seconds", async () => {
    expect([1, 2, 3, 4, 5, 6].map(pollingDelay)).toEqual([
      2_000, 4_000, 8_000, 16_000, 30_000, 30_000,
    ]);
    vi.mocked(getJob).mockRejectedValue(new Error("API offline"));
    const hook = renderHook(() =>
      useJobPolling({ onJob: vi.fn(), onTerminal: vi.fn() }),
    );
    act(() => hook.result.current.track(uploadAccepted({ status: "queued" })));
    await act(async () => {
      await vi.runAllTicks();
      await Promise.resolve();
    });
    expect(getJob).toHaveBeenCalledTimes(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2_000);
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(getJob).toHaveBeenCalledTimes(2);
    expect(hook.result.current.tracked[JOB].failures).toBe(2);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3_999);
    });
    expect(getJob).toHaveBeenCalledTimes(2);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1);
    });
    expect(getJob).toHaveBeenCalledTimes(3);
    expect(hook.result.current.pollError).toBe("API offline");
  });

  it("pauses while hidden, resumes immediately, and aborts on cleanup", async () => {
    const observed = { signal: null as AbortSignal | null };
    vi.mocked(getJob).mockImplementation(
      (_jobId, nextSignal) =>
        new Promise((_resolve, reject) => {
          observed.signal = nextSignal ?? null;
          nextSignal?.addEventListener("abort", () =>
            reject(nextSignal.reason),
          );
        }),
    );
    visibility = "hidden";
    const hook = renderHook(() =>
      useJobPolling({ onJob: vi.fn(), onTerminal: vi.fn() }),
    );
    act(() => hook.result.current.track(uploadAccepted({ status: "queued" })));
    await act(async () => {
      await vi.runAllTicks();
    });
    expect(getJob).not.toHaveBeenCalled();

    visibility = "visible";
    act(() => document.dispatchEvent(new Event("visibilitychange")));
    expect(getJob).toHaveBeenCalledWith(JOB, expect.any(AbortSignal));
    expect(observed.signal?.aborted).toBe(false);
    hook.unmount();
    expect(observed.signal?.aborted).toBe(true);
    await act(async () => {
      await Promise.resolve();
      await vi.runAllTicks();
      await vi.advanceTimersByTimeAsync(30_000);
    });
    expect(getJob).toHaveBeenCalledOnce();
  });

  it("treats a deleted job 404 as terminal and stops retrying", async () => {
    vi.mocked(getJob).mockRejectedValue(new ApiError("job not found", 404));
    const hook = renderHook(() =>
      useJobPolling({ onJob: vi.fn(), onTerminal: vi.fn() }),
    );

    act(() => hook.result.current.track(uploadAccepted({ status: "queued" })));
    await act(async () => {
      await vi.runAllTicks();
      await Promise.resolve();
    });

    expect(getJob).toHaveBeenCalledOnce();
    expect(hook.result.current.tracked).toEqual({});
    expect(hook.result.current.pollError).toBeNull();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(30_000);
    });
    expect(getJob).toHaveBeenCalledOnce();
  });

  it("does not restore an in-flight job after its document is untracked", async () => {
    let resolveJob: ((value: ReturnType<typeof jobStatus>) => void) | undefined;
    vi.mocked(getJob).mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveJob = resolve;
        }),
    );
    const onJob = vi.fn();
    const hook = renderHook(() =>
      useJobPolling({ onJob, onTerminal: vi.fn() }),
    );

    act(() => hook.result.current.track(uploadAccepted({ status: "queued" })));
    await act(async () => {
      await vi.runAllTicks();
    });
    act(() => hook.result.current.untrackDocument(uploadAccepted().document_id));
    await act(async () => {
      resolveJob?.(jobStatus({ status: "running" }));
      await Promise.resolve();
      await Promise.resolve();
      await vi.runAllTicks();
    });

    expect(hook.result.current.tracked).toEqual({});
    expect(onJob).not.toHaveBeenCalled();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(30_000);
    });
    expect(getJob).toHaveBeenCalledOnce();
  });

  it("restores a pending job after navigation remounts the workspace", async () => {
    visibility = "hidden";
    const first = renderHook(() =>
      useJobPolling({ onJob: vi.fn(), onTerminal: vi.fn() }),
    );
    act(() => first.result.current.track(uploadAccepted({ status: "queued" })));
    await act(async () => {
      await vi.runAllTicks();
    });
    expect(getJob).not.toHaveBeenCalled();
    first.unmount();

    visibility = "visible";
    const onTerminal = vi.fn();
    vi.mocked(getJob).mockResolvedValue(jobStatus({ status: "completed" }));
    renderHook(() => useJobPolling({ onJob: vi.fn(), onTerminal }));
    await act(async () => {
      await vi.runAllTicks();
      await Promise.resolve();
    });

    expect(getJob).toHaveBeenCalledWith(JOB, expect.any(AbortSignal));
    expect(onTerminal).toHaveBeenCalledWith(
      expect.objectContaining({ status: "completed" }),
    );
    expect(window.sessionStorage.length).toBe(0);
  });
});
