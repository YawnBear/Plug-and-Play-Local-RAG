"use client";

import { useEffect, useState } from "react";

export const STANDARD_MOTION_MS = 180;

export type MotionPhase = "closed" | "closing" | "opening" | "open";

export function prefersReducedMotion(): boolean {
  const configured =
    typeof document !== "undefined"
      ? document.documentElement.dataset.motionMode
      : undefined;
  if (configured === "reduced") return true;
  if (configured === "full") return false;
  return (
    typeof window !== "undefined" &&
    typeof window.matchMedia === "function" &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches
  );
}

export function useMotionPresence(
  visible: boolean,
  duration = STANDARD_MOTION_MS,
): MotionPhase {
  const [phase, setPhase] = useState<MotionPhase>(visible ? "open" : "closed");

  useEffect(() => {
    if (prefersReducedMotion()) {
      const reducedTimer = window.setTimeout(
        () => setPhase(visible ? "open" : "closed"),
        0,
      );
      return () => window.clearTimeout(reducedTimer);
    }

    const startTimer = window.setTimeout(() => {
      setPhase((current) => {
        if (visible) return current === "open" ? "open" : "opening";
        return current === "closed" ? "closed" : "closing";
      });
    }, 0);
    const endTimer = window.setTimeout(
      () => setPhase(visible ? "open" : "closed"),
      duration,
    );
    return () => {
      window.clearTimeout(startTimer);
      window.clearTimeout(endTimer);
    };
  }, [duration, visible]);

  return phase;
}
