"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import {
  createContext,
  useContext,
  useEffect,
  useRef,
  useState,
  type CSSProperties,
  type ReactNode,
} from "react";

import { logout } from "@/features/auth/api";
import { useAuth } from "@/features/auth/auth-provider";
import { Tooltip } from "@/components/ui/tooltip";
import { useMotionPresence } from "@/lib/motion";

import { Icon } from "./icons";
import { MobileDrawer } from "./mobile-drawer";
import { PrimaryNav } from "./primary-nav";
import {
  MotionControl,
  MotionPreferenceProvider,
  ThemeControl,
} from "./theme-control";

const SIDEBAR_KEY = "rag-sidebar-collapsed";
const SIDEBAR_WIDTH_KEY = "local-rag.primary-sidebar-width.v1";

function storedSidebarWidth(): number {
  if (typeof window === "undefined") return 260;
  const parsed = Number(window.localStorage.getItem(SIDEBAR_WIDTH_KEY));
  return Number.isFinite(parsed) ? Math.min(380, Math.max(220, parsed)) : 260;
}

export interface SidebarContentSlot {
  render: (onNavigate?: () => void) => ReactNode;
}

const SidebarContentContext = createContext<SidebarContentSlot | null>(null);
const SidebarContentSetterContext =
  createContext<((content: SidebarContentSlot | null) => void) | null>(null);

export function SidebarContentProvider({ children }: { children: ReactNode }) {
  const [content, setContent] = useState<SidebarContentSlot | null>(null);
  return (
    <SidebarContentSetterContext.Provider value={setContent}>
      <SidebarContentContext.Provider value={content}>
        {children}
      </SidebarContentContext.Provider>
    </SidebarContentSetterContext.Provider>
  );
}

export function useSidebarContent(content: SidebarContentSlot | null) {
  const setContent = useContext(SidebarContentSetterContext);

  useEffect(() => {
    if (!setContent) return;
    setContent(content);
    return () => setContent(null);
  }, [content, setContent]);
}

export function SidebarContentOutlet() {
  const content = useContext(SidebarContentContext);
  return <>{content?.render()}</>;
}

function routeTitle(pathname: string): string {
  if (pathname.startsWith("/knowledge-base")) return "Knowledge Base";
  if (pathname.startsWith("/admin")) return "Administration";
  if (pathname.startsWith("/system")) return "System";
  return "Chat";
}

function AccountPanel({
  compact = false,
  displayName,
  onSignOut,
}: {
  compact?: boolean;
  displayName: string;
  onSignOut: () => void;
}) {
  const detailsRef = useRef<HTMLDetailsElement>(null);

  useEffect(() => {
    function dismiss(event: PointerEvent) {
      const details = detailsRef.current;
      if (details?.open && !details.contains(event.target as Node)) {
        details.open = false;
      }
    }
    function onKeyDown(event: globalThis.KeyboardEvent) {
      const details = detailsRef.current;
      if (event.key === "Escape" && details?.open) {
        details.open = false;
        details.querySelector("summary")?.focus();
      }
    }
    document.addEventListener("pointerdown", dismiss);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("pointerdown", dismiss);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, []);

  const menu = (
    <details className="account-menu" ref={detailsRef}>
      <summary aria-label={`Account menu for ${displayName}`}>
        <span className="account-avatar" aria-hidden="true">
          {displayName.slice(0, 1).toUpperCase()}
        </span>
        {compact ? null : (
          <>
            <span className="account-menu__name">{displayName}</span>
            <Icon className="disclosure-chevron" name="chevron" />
          </>
        )}
      </summary>
      <div className="account-menu__panel">
        <ThemeControl />
        <MotionControl />
        <button className="account-menu__sign-out" onClick={onSignOut} type="button">
          <Icon name="sign-out" />
          <span>Sign out</span>
        </button>
      </div>
    </details>
  );

  return compact ? <Tooltip content={displayName}>{menu}</Tooltip> : menu;
}

function ActivityBar({
  displayName,
  isAdmin,
  onSignOut,
}: {
  displayName: string;
  isAdmin: boolean;
  onSignOut: () => void;
}) {
  const pathname = usePathname();
  const links = [
    {
      active: pathname === "/",
      href: "/",
      icon: "chat" as const,
      label: "Chat",
    },
    {
      active: pathname.startsWith("/knowledge-base"),
      href: "/knowledge-base",
      icon: "knowledge" as const,
      label: "Knowledge Base",
    },
    ...(isAdmin
      ? [
          {
            active: pathname.startsWith("/admin"),
            href: "/admin/users",
            icon: "admin" as const,
            label: "Administration",
          },
          {
            active: pathname.startsWith("/system"),
            href: "/system/overview",
            icon: "system" as const,
            label: "System",
          },
        ]
      : []),
  ];

  return (
    <aside className="activity-bar" aria-label="Application activity">
      <Tooltip content="Local RAG">
        <Link className="activity-bar__brand" href="/" aria-label="Local RAG home">
          LR
        </Link>
      </Tooltip>
      <nav aria-label="Workspace">
        {links.map((item) => (
          <Tooltip content={item.label} key={item.href}>
            <Link
              aria-current={item.active ? "page" : undefined}
              aria-label={item.label}
              className="activity-bar__item"
              href={item.href}
            >
              <Icon name={item.icon} />
            </Link>
          </Tooltip>
        ))}
      </nav>
      <div className="activity-bar__spacer" />
      <AccountPanel
        compact
        displayName={displayName}
        onSignOut={onSignOut}
      />
    </aside>
  );
}

function MobileSidebarContents({
  displayName,
  isAdmin,
  onNavigate,
  onSignOut,
  sidebarContent,
}: {
  displayName: string;
  isAdmin: boolean;
  onNavigate?: () => void;
  onSignOut: () => void;
  sidebarContent: ReactNode;
}) {
  return (
    <>
      <Link className="sidebar__new-chat" href="/" onClick={onNavigate}>
        <Icon name="new" />
        <span>New chat</span>
      </Link>
      <PrimaryNav
        collapsed={false}
        isAdmin={isAdmin}
        onNavigate={onNavigate}
      />
      {sidebarContent ? (
        <div className="min-h-0 flex-1 overflow-y-auto">{sidebarContent}</div>
      ) : (
        <div className="sidebar__spacer" />
      )}
      <AccountPanel displayName={displayName} onSignOut={onSignOut} />
    </>
  );
}

export function ProtectedShell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const { user, setUser } = useAuth();
  const sidebarContent = useContext(SidebarContentContext);
  const [sidebarHidden, setSidebarHidden] = useState(false);
  const [sidebarWidth, setSidebarWidth] = useState(storedSidebarWidth);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const previousPath = useRef(pathname);
  const hideSidebarRef = useRef<HTMLButtonElement>(null);
  const showSidebarRef = useRef<HTMLButtonElement>(null);
  const sidebarFocusTarget = useRef<"hide" | "show" | null>(null);
  const sidebarMotion = useMotionPresence(!sidebarHidden);
  const sidebarLayoutHidden = sidebarMotion === "closed";

  useEffect(() => {
    const frame = window.requestAnimationFrame(() => {
      setSidebarHidden(window.localStorage.getItem(SIDEBAR_KEY) === "true");
    });
    return () => window.cancelAnimationFrame(frame);
  }, []);

  useEffect(() => {
    if (previousPath.current !== pathname) {
      document.getElementById("main-content")?.focus();
      setDrawerOpen(false);
      previousPath.current = pathname;
    }
  }, [pathname]);

  function toggleSidebar(restoreFocus: boolean) {
    setSidebarHidden((current) => {
      const next = !current;
      sidebarFocusTarget.current = restoreFocus ? (next ? "show" : "hide") : null;
      window.localStorage.setItem(SIDEBAR_KEY, String(next));
      return next;
    });
  }

  useEffect(() => {
    if (sidebarFocusTarget.current === "show" && sidebarLayoutHidden) {
      showSidebarRef.current?.focus();
      sidebarFocusTarget.current = null;
    }
    if (sidebarFocusTarget.current === "hide" && sidebarMotion === "open") {
      hideSidebarRef.current?.focus();
      sidebarFocusTarget.current = null;
    }
  }, [sidebarLayoutHidden, sidebarMotion]);

  function beginSidebarResize(event: React.PointerEvent<HTMLDivElement>) {
    event.preventDefault();
    const handle = event.currentTarget;
    const pointerId = event.pointerId;
    const startX = event.clientX;
    const startWidth = sidebarWidth;
    let latestWidth = startWidth;
    handle.setPointerCapture?.(pointerId);
    document.documentElement.classList.add("is-resizing-sidebar");

    const move = (next: PointerEvent) => {
      latestWidth = Math.min(
        380,
        Math.max(220, startWidth + next.clientX - startX),
      );
      setSidebarWidth(latestWidth);
    };

    const cleanup = () => {
      document.documentElement.classList.remove("is-resizing-sidebar");
      if (handle.hasPointerCapture?.(pointerId)) {
        handle.releasePointerCapture(pointerId);
      }
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", finish);
      window.removeEventListener("pointercancel", cancel);
      window.removeEventListener("blur", cancel);
    };

    const persist = () => {
      setSidebarWidth(latestWidth);
      window.localStorage.setItem(SIDEBAR_WIDTH_KEY, String(latestWidth));
    };

    const finish = (next: PointerEvent) => {
      latestWidth = Math.min(
        380,
        Math.max(220, startWidth + next.clientX - startX),
      );
      persist();
      cleanup();
    };

    const cancel = () => {
      persist();
      cleanup();
    };

    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", finish);
    window.addEventListener("pointercancel", cancel);
    window.addEventListener("blur", cancel);
  }

  async function signOut() {
    try {
      await logout();
    } finally {
      setUser(null);
      router.replace("/login");
      router.refresh();
    }
  }

  const forbidden =
    (pathname.startsWith("/admin") || pathname.startsWith("/system")) &&
    user?.role !== "admin";
  const chatRoute = pathname === "/";

  return (
    <MotionPreferenceProvider>
    <div
      className={`app-shell ${sidebarLayoutHidden ? "app-shell--sidebar-hidden" : ""}`}
      style={
        {
          "--primary-sidebar-width": `${sidebarWidth}px`,
        } as CSSProperties
      }
    >
      <a className="skip-link" href="#main-content">Skip to main content</a>
      <ActivityBar
        displayName={user?.display_name ?? ""}
        isAdmin={user?.role === "admin"}
        onSignOut={() => void signOut()}
      />
      <aside
        className="primary-sidebar"
        aria-label={`${routeTitle(pathname)} sidebar`}
        data-motion={sidebarMotion}
      >
        <div className="primary-sidebar__header">
          <strong>{routeTitle(pathname)}</strong>
          <Tooltip content="Hide sidebar">
            <button
              aria-label="Hide sidebar"
              className="primary-sidebar__toggle"
              onClick={(event) => toggleSidebar(event.detail === 0)}
              ref={hideSidebarRef}
              type="button"
            >
              <Icon name="collapse" />
            </button>
          </Tooltip>
        </div>
        {chatRoute ? (
          <Link className="sidebar__new-chat" href="/">
            <Icon name="new" />
            <span>New chat</span>
          </Link>
        ) : null}
        <div className="primary-sidebar__content">
          {sidebarContent?.render() ?? (
            <p className="sidebar__hint">
              {chatRoute
                ? "Your conversations will appear here."
                : "Workspace navigation"}
            </p>
          )}
        </div>
        <div
          aria-label="Resize sidebar"
          className="primary-sidebar__resize"
          onPointerDown={beginSidebarResize}
          role="separator"
        />
      </aside>
      {sidebarHidden || sidebarMotion !== "open" ? (
        <div className="sidebar-show-control" data-motion={sidebarMotion}>
          <Tooltip content="Show sidebar">
            <button
              aria-label="Show sidebar"
              className="primary-sidebar__toggle primary-sidebar__toggle--show"
              onClick={(event) => toggleSidebar(event.detail === 0)}
              ref={showSidebarRef}
              type="button"
            >
              <Icon name="collapse" />
            </button>
          </Tooltip>
        </div>
      ) : null}
      <header className="mobile-context-bar">
        <button
          aria-expanded={drawerOpen}
          aria-label="Open navigation"
          className="icon-button"
          onClick={() => setDrawerOpen(true)}
          type="button"
        >
          <Icon name="menu" />
        </button>
        <div className="mobile-context-bar__identity">
          <span className="sidebar__mark" aria-hidden="true">LR</span>
          <strong>{routeTitle(pathname)}</strong>
        </div>
      </header>
      <MobileDrawer onClose={() => setDrawerOpen(false)} open={drawerOpen}>
        <div className="mobile-drawer__header">
          <div className="mobile-drawer__identity">
            <span className="sidebar__mark" aria-hidden="true">LR</span>
            <span className="sidebar__wordmark">Local RAG</span>
          </div>
          <button
            aria-label="Close navigation"
            className="mobile-drawer__close"
            data-drawer-autofocus
            onClick={() => setDrawerOpen(false)}
            type="button"
          >
            <Icon name="collapse" />
          </button>
        </div>
        <MobileSidebarContents
          displayName={user?.display_name ?? ""}
          isAdmin={user?.role === "admin"}
          onNavigate={() => setDrawerOpen(false)}
          onSignOut={() => void signOut()}
          sidebarContent={sidebarContent?.render(() => setDrawerOpen(false))}
        />
      </MobileDrawer>
      <main id="main-content" tabIndex={-1} className="app-main">
        {forbidden ? (
          <section className="route-state" aria-labelledby="forbidden-title">
            <p className="route-state__eyebrow">Access denied</p>
            <h1 id="forbidden-title">
              {pathname.startsWith("/system")
                ? "System is restricted"
                : "Administration is restricted"}
            </h1>
            <p>Your account does not have administrator access.</p>
            <Link className="button-primary" href="/">Return to Chat</Link>
          </section>
        ) : children}
      </main>
    </div>
    </MotionPreferenceProvider>
  );
}
