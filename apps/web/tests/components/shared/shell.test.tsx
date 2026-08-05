import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AppShell } from "@/components/shell/app-shell";
import { AdminPage } from "@/features/admin/admin-page";

const MEMBER = {
  id: "11111111-1111-4111-8111-111111111111",
  username: "reader",
  display_name: "Reader",
  role: "member",
  status: "active",
};

const ADMIN = {
  ...MEMBER,
  display_name: "Administrator",
  role: "admin",
};

const navigation = vi.hoisted(() => ({
  pathname: "/",
  refresh: vi.fn(),
  replace: vi.fn(),
  searchParams: new URLSearchParams(),
}));

vi.mock("next/navigation", () => ({
  usePathname: () => navigation.pathname,
  useRouter: () => ({
    refresh: navigation.refresh,
    replace: navigation.replace,
  }),
  useSearchParams: () => navigation.searchParams,
}));

function sessionResponse(user: typeof MEMBER | null = MEMBER) {
  return new Response(JSON.stringify({ user, csrf_token: "csrf-test" }), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

beforeEach(() => {
  navigation.pathname = "/";
  navigation.searchParams = new URLSearchParams();
  navigation.refresh.mockReset();
  navigation.replace.mockReset();
  window.localStorage.clear();
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(sessionResponse()));
});

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
  Reflect.deleteProperty(navigator, "locks");
  vi.unstubAllGlobals();
});

describe("AppShell", () => {
  it("waits for the session before mounting protected content", async () => {
    let resolveSession!: (response: Response) => void;
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation(
        () => new Promise<Response>((resolve) => {
          resolveSession = resolve;
        }),
      ),
    );

    render(<AppShell><h1>Protected workspace</h1></AppShell>);

    expect(screen.queryByText("Protected workspace")).not.toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("Checking your session");

    await waitFor(() => expect(resolveSession).toBeTypeOf("function"));
    resolveSession(sessionResponse());
    expect(await screen.findByText("Protected workspace")).toBeInTheDocument();
  });

  it("renders the activity navigation with active state and persists the sidebar", async () => {
    navigation.pathname = "/knowledge-base";
    render(<AppShell><h1>Library</h1></AppShell>);

    const nav = await screen.findByRole("navigation", { name: "Workspace" });
    const links = within(nav).getAllByRole("link");
    expect(links.map((link) => link.getAttribute("href"))).toEqual([
      "/",
      "/knowledge-base",
    ]);
    expect(screen.getByRole("link", { name: "Knowledge Base" })).toHaveAttribute(
      "aria-current",
      "page",
    );
    expect(screen.getByRole("link", { name: "Chat" })).not.toHaveAttribute(
      "aria-current",
    );
    expect(screen.getByRole("complementary", { name: "Knowledge Base sidebar" }))
      .toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Hide sidebar" }));
    expect(window.localStorage.getItem("rag-sidebar-collapsed")).toBe("true");
    const showSidebar = await screen.findByRole("button", {
      name: "Show sidebar",
    });
    expect(showSidebar).toBeInTheDocument();
    await waitFor(() => expect(showSidebar).toHaveFocus());
    expect(showSidebar.closest(".sidebar-show-control")).not.toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Show sidebar" }));
    expect(window.localStorage.getItem("rag-sidebar-collapsed")).toBe("false");
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Hide sidebar" })).toHaveFocus(),
    );
    expect(document.querySelector("[title]")).toBeNull();
  });

  it("keeps the mobile product identity and drawer controls aligned", async () => {
    render(<AppShell><h1>Chat workspace</h1></AppShell>);

    const banner = await screen.findByRole("banner");
    expect(within(banner).getByText("LR")).toBeInTheDocument();
    expect(within(banner).getByText("Chat")).toBeInTheDocument();

    const drawerHeader = document.querySelector(".mobile-drawer__header");
    expect(drawerHeader).not.toBeNull();
    expect(drawerHeader).toHaveTextContent("Local RAG");
    expect(
      drawerHeader?.querySelector('button[aria-label="Close navigation"]'),
    ).toBeInTheDocument();
  });

  it("dismisses the account menu on outside pointer input and Escape", async () => {
    render(<AppShell><button type="button">Outside</button></AppShell>);
    const summary = (await screen.findAllByLabelText("Account menu for Reader"))[0];
    const details = summary.closest("details");
    fireEvent.click(summary);
    expect(details).toHaveAttribute("open");
    fireEvent.pointerDown(screen.getByRole("button", { name: "Outside" }));
    expect(details).not.toHaveAttribute("open");
    fireEvent.click(summary);
    fireEvent.keyDown(document, { key: "Escape" });
    expect(details).not.toHaveAttribute("open");
    expect(summary).toHaveFocus();
  });

  it("shows the logged-in display name in the profile tooltip", async () => {
    render(<AppShell><h1>Chat workspace</h1></AppShell>);
    const summary = (await screen.findAllByLabelText("Account menu for Reader"))[0];
    const details = summary.closest("details");

    expect(details).not.toBeNull();
    fireEvent.pointerEnter(details!);
    expect(screen.getByRole("tooltip", { name: "Reader" })).toHaveAttribute(
      "data-visible",
      "true",
    );
    expect(document.querySelector("[title]")).toBeNull();
  });

  it("shows Administration sections in the workspace sidebar", async () => {
    navigation.pathname = "/admin/teams";
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(sessionResponse(ADMIN)));

    render(
      <AppShell>
        <AdminPage description="Manage workspace teams." title="Teams">
          <p>Team settings</p>
        </AdminPage>
      </AppShell>,
    );

    const sidebar = await screen.findByRole("complementary", {
      name: "Administration sidebar",
    });
    const sectionNavigation = await screen.findByRole("navigation", {
      name: "Administration workspace navigation",
    });
    expect(sidebar).toContainElement(sectionNavigation);
    const navigationLinks = within(sectionNavigation).getAllByRole("link");

    expect(navigationLinks.map((link) => link.getAttribute("href"))).toEqual([
      "/admin/users",
      "/admin/teams",
      "/admin/access",
      "/admin/audit",
    ]);
    expect(
      within(sidebar).getByRole("link", { name: "Teams" }),
    ).toHaveAttribute("aria-current", "page");
  });

  it("persists an explicit motion preference from the account menu", async () => {
    const user = userEvent.setup();
    render(<AppShell><h1>Chat workspace</h1></AppShell>);

    const activityBar = await screen.findByRole("complementary", {
      name: "Application activity",
    });
    const account = within(activityBar).getByLabelText("Account menu for Reader");
    await user.click(account);
    const accountMenu = account.closest("details");
    expect(accountMenu).not.toBeNull();

    await user.click(
      within(accountMenu!).getByRole("button", { name: "Reduced" }),
    );
    expect(window.localStorage.getItem("rag-motion")).toBe("reduced");
    expect(document.documentElement).toHaveAttribute(
      "data-reduce-motion",
      "true",
    );

    await user.click(within(accountMenu!).getByRole("button", { name: "Full" }));
    expect(window.localStorage.getItem("rag-motion")).toBe("full");
    expect(document.documentElement).toHaveAttribute(
      "data-reduce-motion",
      "false",
    );
    const motionControls = document.querySelectorAll(".motion-control");
    expect(motionControls).toHaveLength(2);
    for (const control of motionControls) {
      expect(
        within(control as HTMLElement)
          .getAllByRole("button", { hidden: true })
          .map((button) => button.textContent),
      ).toEqual(["Reduced", "Full", "System"]);
      expect(
        within(control as HTMLElement).getByRole("button", {
          name: "Full",
          hidden: true,
        }),
      ).toHaveAttribute("aria-pressed", "true");
    }
  });

  it("keeps public login free of application navigation", async () => {
    navigation.pathname = "/login";
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(sessionResponse(null)));

    render(<AppShell><h1>Sign in</h1></AppShell>);

    expect(await screen.findByText("Sign in")).toBeInTheDocument();
    expect(screen.queryByRole("navigation", { name: "Workspace" })).not.toBeInTheDocument();
    expect(screen.getByRole("main")).toBeInTheDocument();
  });

  it("redirects an anonymous protected visit with its internal return path", async () => {
    navigation.pathname = "/knowledge-base";
    navigation.searchParams = new URLSearchParams("folder=abc");
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(sessionResponse(null)));

    render(<AppShell><h1>Protected library</h1></AppShell>);

    await waitFor(() => {
      expect(navigation.replace).toHaveBeenCalledWith(
        "/login?next=%2Fknowledge-base%3Ffolder%3Dabc",
      );
    });
    expect(screen.queryByText("Protected library")).not.toBeInTheDocument();
  });

  it("refreshes only for visible meaningful activity", async () => {
    vi.spyOn(document, "visibilityState", "get").mockReturnValue("visible");
    Object.defineProperty(navigator, "locks", {
      configurable: true,
      value: {
        request: async (
          _name: string,
          _options: LockOptions,
          callback: (lock: Lock | null) => Promise<void>,
        ) => callback({ name: "rag-session-refresh", mode: "exclusive" }),
      },
    });
    const fetchMock = vi.fn().mockImplementation(() =>
      Promise.resolve(sessionResponse()),
    );
    vi.stubGlobal("fetch", fetchMock);
    render(<AppShell><h1>Protected workspace</h1></AppShell>);
    await screen.findByText("Protected workspace");
    await new Promise((resolve) => window.setTimeout(resolve, 0));
    window.localStorage.removeItem("rag-session-last-refresh");
    fetchMock.mockClear();

    fireEvent.mouseMove(document);
    expect(fetchMock).not.toHaveBeenCalled();
    fireEvent.click(document);
    await waitFor(() => expect(fetchMock).toHaveBeenCalledOnce());
    expect(fetchMock.mock.calls[0][0]).toBe("/api/auth/refresh");
    await new Promise((resolve) => window.setTimeout(resolve, 0));
    fireEvent.keyDown(document, { key: "a" });
    fireEvent.scroll(document);
    expect(fetchMock).toHaveBeenCalledOnce();
  });

  it("honors the shared five-minute refresh timestamp", async () => {
    vi.spyOn(document, "visibilityState", "get").mockReturnValue("visible");
    let now = new Date("2026-07-27T06:00:00Z").getTime();
    vi.spyOn(Date, "now").mockImplementation(() => now);
    Object.defineProperty(navigator, "locks", {
      configurable: true,
      value: {
        request: async (
          _name: string,
          _options: LockOptions,
          callback: (lock: Lock | null) => Promise<void>,
        ) => callback({ name: "rag-session-refresh", mode: "exclusive" }),
      },
    });
    window.localStorage.setItem("rag-session-last-refresh", String(now));
    const fetchMock = vi.fn().mockImplementation(() =>
      Promise.resolve(sessionResponse()),
    );
    vi.stubGlobal("fetch", fetchMock);
    render(<AppShell><h1>Protected workspace</h1></AppShell>);
    await screen.findByText("Protected workspace");
    await new Promise((resolve) => window.setTimeout(resolve, 0));
    fetchMock.mockClear();

    fireEvent.wheel(document);
    await new Promise((resolve) => window.setTimeout(resolve, 0));
    expect(fetchMock).not.toHaveBeenCalled();
    now += 5 * 60 * 1000;
    fireEvent.wheel(document);
    await waitFor(() => expect(fetchMock).toHaveBeenCalledOnce());
  });

  it("uses an origin-wide exclusive lock for simultaneous refresh attempts", async () => {
    vi.spyOn(document, "visibilityState", "get").mockReturnValue("visible");
    let occupied = false;
    const request = vi.fn(
      async (
        _name: string,
        _options: LockOptions,
        callback: (lock: Lock | null) => Promise<void>,
      ) => {
        if (occupied) return callback(null);
        occupied = true;
        try {
          await callback({ name: "rag-session-refresh", mode: "exclusive" });
        } finally {
          occupied = false;
        }
      },
    );
    Object.defineProperty(navigator, "locks", {
      configurable: true,
      value: { request },
    });
    const fetchMock = vi.fn().mockImplementation(() =>
      Promise.resolve(sessionResponse()),
    );
    vi.stubGlobal("fetch", fetchMock);
    render(
      <>
        <AppShell><h1>First tab</h1></AppShell>
        <AppShell><h1>Second tab</h1></AppShell>
      </>,
    );
    await screen.findByText("First tab");
    await screen.findByText("Second tab");
    fetchMock.mockClear();

    fireEvent.wheel(document);

    await waitFor(() => {
      const refreshCalls = fetchMock.mock.calls.filter(
        ([path]) => path === "/api/auth/refresh",
      );
      expect(refreshCalls).toHaveLength(1);
    });
    expect(request).toHaveBeenCalledTimes(2);
    expect(request.mock.calls[0][0]).toBe("rag-session-refresh");
    expect(request.mock.calls[0][1]).toMatchObject({
      mode: "exclusive",
      ifAvailable: true,
    });
  });

  it("ignores activity while the document is hidden", async () => {
    const fetchMock = vi.fn().mockResolvedValue(sessionResponse());
    vi.stubGlobal("fetch", fetchMock);
    render(<AppShell><h1>Protected workspace</h1></AppShell>);
    await screen.findByText("Protected workspace");
    fetchMock.mockClear();
    const original = Object.getOwnPropertyDescriptor(document, "visibilityState");
    Object.defineProperty(document, "visibilityState", {
      configurable: true,
      value: "hidden",
    });

    fireEvent.click(document);
    expect(fetchMock).not.toHaveBeenCalled();
    Object.defineProperty(
      document,
      "visibilityState",
      original ?? { configurable: true, value: "visible" },
    );
  });

  it("synchronizes cross-tab expiry and appends the expiry reason", async () => {
    navigation.pathname = "/knowledge-base";
    navigation.searchParams = new URLSearchParams("document=abc&page=3");
    render(<AppShell><h1>Protected library</h1></AppShell>);
    await screen.findByText("Protected library");

    window.dispatchEvent(
      new StorageEvent("storage", {
        key: "rag-auth-status",
        newValue: "expired:123",
      }),
    );

    await waitFor(() =>
      expect(navigation.replace).toHaveBeenCalledWith(
        "/login?next=%2Fknowledge-base%3Fdocument%3Dabc%26page%3D3&reason=expired",
      ),
    );
  });

  it("shows a retryable session-service error instead of redirecting", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("offline")));

    render(<AppShell><h1>Protected workspace</h1></AppShell>);

    expect(
      await screen.findByRole("heading", { name: "We could not verify your session" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
    expect(navigation.replace).not.toHaveBeenCalled();
  });

  it("renders a member-safe forbidden state for admin routes", async () => {
    navigation.pathname = "/admin/users";
    render(<AppShell><h1>Admin users</h1></AppShell>);

    expect(
      await screen.findByRole("heading", { name: "Administration is restricted" }),
    ).toBeInTheDocument();
    expect(screen.queryByText("Admin users")).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Return to Chat" })).toHaveAttribute(
      "href",
      "/",
    );
  });

  it("keeps the System workspace administrator-only", async () => {
    navigation.pathname = "/system/overview";
    render(<AppShell><h1>Private System data</h1></AppShell>);

    expect(
      await screen.findByRole("heading", { name: "System is restricted" }),
    ).toBeInTheDocument();
    expect(screen.queryByText("Private System data")).not.toBeInTheDocument();
  });
});
