import { Suspense, type ReactNode } from "react";

import { AuthProvider } from "@/features/auth/auth-provider";

import { ShellGate } from "./shell-gate";
import { SidebarContentProvider } from "./protected-shell";

export function AppShell({ children }: { children: ReactNode }) {
  return (
    <AuthProvider>
      <SidebarContentProvider>
        <Suspense
        fallback={
          <main className="session-loading" id="main-content" tabIndex={-1}>
            <span className="session-loading__mark" aria-hidden="true">LR</span>
            <span role="status">Checking your session…</span>
          </main>
        }
        >
          <ShellGate>{children}</ShellGate>
        </Suspense>
      </SidebarContentProvider>
    </AuthProvider>
  );
}
