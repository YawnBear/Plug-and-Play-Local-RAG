"use client";

import { useCallback, useEffect, useRef } from "react";

import { useAuth } from "./auth-provider";

const REFRESH_THROTTLE_MS = 5 * 60 * 1000;
const LAST_REFRESH_KEY = "rag-session-last-refresh";
const REFRESH_LOCK_NAME = "rag-session-refresh";

export function SessionActivityController({
  route,
}: {
  route: string;
}) {
  const { renew, user } = useAuth();
  const inFlight = useRef<Promise<unknown> | null>(null);
  const previousRoute = useRef(route);

  const requestRefresh = useCallback(() => {
    if (!user || document.visibilityState !== "visible" || inFlight.current) return;
    const refreshIfEligible = async () => {
      if (document.visibilityState !== "visible") return;
      const now = Date.now();
      const recorded = Number(
        window.localStorage.getItem(LAST_REFRESH_KEY) ?? "0",
      );
      if (Number.isFinite(recorded) && now - recorded < REFRESH_THROTTLE_MS) {
        return;
      }
      window.localStorage.setItem(LAST_REFRESH_KEY, String(now));
      await renew().catch(() => null);
    };
    const pending = (async () => {
      try {
        if (navigator.locks) {
          await navigator.locks.request(
            REFRESH_LOCK_NAME,
            { mode: "exclusive", ifAvailable: true },
            async (lock) => {
              if (lock) await refreshIfEligible();
            },
          );
        } else {
          await refreshIfEligible();
        }
      } finally {
        inFlight.current = null;
      }
    })();
    inFlight.current = pending;
  }, [renew, user]);

  useEffect(() => {
    if (!user) return;
    const activity = () => requestRefresh();
    document.addEventListener("click", activity);
    document.addEventListener("pointerdown", activity);
    document.addEventListener("touchstart", activity, { passive: true });
    document.addEventListener("keydown", activity);
    document.addEventListener("wheel", activity, { passive: true, capture: true });
    return () => {
      document.removeEventListener("click", activity);
      document.removeEventListener("pointerdown", activity);
      document.removeEventListener("touchstart", activity);
      document.removeEventListener("keydown", activity);
      document.removeEventListener("wheel", activity, true);
    };
  }, [requestRefresh, user]);

  useEffect(() => {
    if (previousRoute.current !== route) {
      previousRoute.current = route;
      requestRefresh();
    }
  }, [requestRefresh, route]);

  return null;
}
