"use client";

import { useCallback, useEffect, useState } from "react";

import { getAccountTeams } from "./api";
import type { AccountTeams } from "./contracts";

export function useAccountTeams() {
  const [data, setData] = useState<AccountTeams | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async (signal?: AbortSignal) => {
    setLoading(true);
    setError(null);
    try {
      setData(await getAccountTeams(signal));
    } catch (reason) {
      if (signal?.aborted) return;
      setError(
        reason instanceof Error
          ? reason.message
          : "Unable to load your active teams.",
      );
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

  return { data, error, loading, retry: load };
}
