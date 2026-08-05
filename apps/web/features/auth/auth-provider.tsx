"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";

import {
  ApiError,
  SESSION_EXPIRED_EVENT,
  SESSION_UNAUTHENTICATED_EVENT,
  setCsrfToken,
} from "@/lib/http";

import { getCurrentUser, refreshSession } from "./api";
import type { AuthUser } from "./contracts";

interface AuthContextValue {
  user: AuthUser | null;
  loading: boolean;
  error: string | null;
  sessionExpired: boolean;
  refresh: () => Promise<AuthUser | null>;
  renew: () => Promise<AuthUser | null>;
  setUser: (user: AuthUser | null) => void;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUserState] = useState<AuthUser | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [sessionExpired, setSessionExpired] = useState(false);
  const endedRef = useRef(false);

  const setUser = useCallback((nextUser: AuthUser | null) => {
    setUserState(nextUser);
    if (nextUser) {
      endedRef.current = false;
      setSessionExpired(false);
      window.localStorage.removeItem("rag-session-last-refresh");
    } else {
      setCsrfToken(null);
      window.localStorage.setItem("rag-auth-status", `logout:${Date.now()}`);
    }
  }, []);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await getCurrentUser();
      setUserState(result.user);
      endedRef.current = result.user === null;
      return result.user;
    } catch (caught) {
      if (caught instanceof ApiError && caught.status === 401) {
        setUserState(null);
        setCsrfToken(null);
        return null;
      }
      const message =
        caught instanceof Error ? caught.message : "Unable to load session.";
      setError(message);
      return null;
    } finally {
      setLoading(false);
    }
  }, []);

  const renew = useCallback(async () => {
    try {
      const result = await refreshSession();
      setUserState(result.user);
      endedRef.current = false;
      return result.user;
    } catch (caught) {
      if (caught instanceof ApiError && caught.status === 401) return null;
      throw caught;
    }
  }, []);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      void refresh();
    }, 0);
    return () => window.clearTimeout(timer);
  }, [refresh]);

  useEffect(() => {
    const endSession = (expired: boolean, broadcast: boolean) => {
      if (endedRef.current) return;
      endedRef.current = true;
      setUserState(null);
      setCsrfToken(null);
      setError(null);
      setLoading(false);
      setSessionExpired(expired);
      if (broadcast) {
        window.localStorage.setItem(
          "rag-auth-status",
          `${expired ? "expired" : "unauthenticated"}:${Date.now()}`,
        );
      }
    };
    const expireSession = () => endSession(true, true);
    const unauthenticate = () => endSession(false, true);
    const synchronize = (event: StorageEvent) => {
      if (event.key !== "rag-auth-status" || !event.newValue) return;
      endSession(event.newValue.startsWith("expired:"), false);
    };
    window.addEventListener(SESSION_EXPIRED_EVENT, expireSession);
    window.addEventListener(SESSION_UNAUTHENTICATED_EVENT, unauthenticate);
    window.addEventListener("storage", synchronize);
    return () => {
      window.removeEventListener(SESSION_EXPIRED_EVENT, expireSession);
      window.removeEventListener(SESSION_UNAUTHENTICATED_EVENT, unauthenticate);
      window.removeEventListener("storage", synchronize);
    };
  }, []);

  const value = useMemo(
    () => ({
      user,
      loading,
      error,
      sessionExpired,
      refresh,
      renew,
      setUser,
    }),
    [error, loading, refresh, renew, sessionExpired, setUser, user],
  );
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const value = useContext(AuthContext);
  if (!value) throw new Error("useAuth must be used within AuthProvider");
  return value;
}
