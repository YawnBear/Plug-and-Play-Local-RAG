"use client";

import {
  createContext,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from "react";

import { Icon } from "./icons";

export type ThemeMode = "light" | "dark" | "system";
export type MotionMode = "system" | "reduced" | "full";

const THEME_KEY = "rag-theme";
const MOTION_KEY = "rag-motion";

function applyTheme(mode: ThemeMode) {
  const resolved =
    mode === "system"
      ? window.matchMedia("(prefers-color-scheme: dark)").matches
        ? "dark"
        : "light"
      : mode;
  document.documentElement.dataset.theme = resolved;
  document.documentElement.dataset.themeMode = mode;
}

function applyMotion(mode: MotionMode) {
  const reduced =
    mode === "reduced" ||
    (mode === "system" &&
      window.matchMedia("(prefers-reduced-motion: reduce)").matches);
  document.documentElement.dataset.motionMode = mode;
  document.documentElement.dataset.reduceMotion = String(reduced);
}

interface MotionPreferenceValue {
  mode: MotionMode;
  choose: (mode: MotionMode) => void;
}

const MotionPreferenceContext = createContext<MotionPreferenceValue | null>(null);

function storedMotionMode(): MotionMode {
  const stored = window.localStorage.getItem(MOTION_KEY);
  return stored === "reduced" || stored === "full" || stored === "system"
    ? stored
    : "system";
}

export function MotionPreferenceProvider({ children }: { children: ReactNode }) {
  const [mode, setMode] = useState<MotionMode>("system");

  useEffect(() => {
    const frame = window.requestAnimationFrame(() => {
      const initial = storedMotionMode();
      setMode(initial);
      applyMotion(initial);
    });
    return () => window.cancelAnimationFrame(frame);
  }, []);

  useEffect(() => {
    const media = window.matchMedia("(prefers-reduced-motion: reduce)");
    const update = () => {
      if (mode === "system") applyMotion(mode);
    };
    media.addEventListener("change", update);
    return () => media.removeEventListener("change", update);
  }, [mode]);

  useEffect(() => {
    const updateFromStorage = (event: StorageEvent) => {
      if (event.key !== MOTION_KEY) return;
      const next = storedMotionMode();
      setMode(next);
      applyMotion(next);
    };
    window.addEventListener("storage", updateFromStorage);
    return () => window.removeEventListener("storage", updateFromStorage);
  }, []);

  function choose(nextMode: MotionMode) {
    setMode(nextMode);
    window.localStorage.setItem(MOTION_KEY, nextMode);
    applyMotion(nextMode);
  }

  return (
    <MotionPreferenceContext.Provider value={{ mode, choose }}>
      {children}
    </MotionPreferenceContext.Provider>
  );
}

export function ThemeControl() {
  const [mode, setMode] = useState<ThemeMode>("system");

  useEffect(() => {
    const frame = window.requestAnimationFrame(() => {
      const stored = window.localStorage.getItem(THEME_KEY);
      const initial =
        stored === "light" || stored === "dark" || stored === "system"
          ? stored
          : "system";
      setMode(initial);
      applyTheme(initial);
    });
    return () => window.cancelAnimationFrame(frame);
  }, []);

  useEffect(() => {
    const media = window.matchMedia("(prefers-color-scheme: dark)");
    const update = () => {
      if (mode === "system") applyTheme(mode);
    };
    media.addEventListener("change", update);
    return () => media.removeEventListener("change", update);
  }, [mode]);

  function choose(nextMode: ThemeMode) {
    setMode(nextMode);
    window.localStorage.setItem(THEME_KEY, nextMode);
    applyTheme(nextMode);
  }

  return (
    <fieldset className="theme-control">
      <legend>Theme</legend>
      {(["light", "dark", "system"] as const).map((option) => (
        <button
          aria-pressed={mode === option}
          className="theme-control__option"
          key={option}
          onClick={() => choose(option)}
          type="button"
        >
          <Icon name={option === "dark" ? "moon" : option === "light" ? "sun" : "system"} />
          <span>{option[0].toUpperCase() + option.slice(1)}</span>
        </button>
      ))}
    </fieldset>
  );
}

export function MotionControl() {
  const preference = useContext(MotionPreferenceContext);
  if (!preference) {
    throw new Error("MotionControl must be used within MotionPreferenceProvider");
  }
  const { choose, mode } = preference;

  return (
    <fieldset className="theme-control motion-control">
      <legend>Motion</legend>
      {(["reduced", "full", "system"] as const).map((option) => (
        <button
          aria-pressed={mode === option}
          className="theme-control__option"
          key={option}
          onClick={() => choose(option)}
          type="button"
        >
          <Icon
            name={
              option === "reduced"
                ? "stop"
                : option === "full"
                  ? "activity"
                  : "system"
            }
          />
          <span>{option[0].toUpperCase() + option.slice(1)}</span>
        </button>
      ))}
    </fieldset>
  );
}
