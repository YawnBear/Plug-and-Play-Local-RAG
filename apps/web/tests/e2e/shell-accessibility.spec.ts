import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

import {
  API_ROUTE,
  fulfillAuthMeIfRequested,
  fulfillJson,
  libraryTree,
} from "./mock-api";

async function mockEmptyChatApi(page: Page): Promise<void> {
  await page.route(API_ROUTE, async (route) => {
    if (await fulfillAuthMeIfRequested(route)) return;
    const url = new URL(route.request().url());
    if (route.request().method() === "GET" && url.pathname === "/api/chats") {
      await fulfillJson(route, []);
      return;
    }
    await fulfillJson(route, { detail: "Unexpected deterministic request." }, 500);
  });
}

async function mockEmptyKnowledgeBaseApi(page: Page): Promise<void> {
  await page.route(API_ROUTE, async (route) => {
    if (await fulfillAuthMeIfRequested(route)) return;
    const url = new URL(route.request().url());
    if (route.request().method() === "GET" && url.pathname === "/api/library/tree") {
      await fulfillJson(route, libraryTree());
      return;
    }
    if (
      route.request().method() === "GET" &&
      url.pathname === "/api/library/browse"
    ) {
      await fulfillJson(route, {
        parent_id: null,
        breadcrumbs: [],
        children: libraryTree().map(({ children, ...folder }) => {
          void children;
          return {
            ...folder,
            kind: "folder",
            document_id: null,
            uploader_user_id: null,
            can_manage: true,
            can_create_children: true,
            readable_document_count: 0,
          };
        }),
        page: Number(url.searchParams.get("page") ?? 1),
        limit: Number(url.searchParams.get("limit") ?? 100),
        total: libraryTree().length,
      });
      return;
    }
    if (route.request().method() === "GET" && url.pathname === "/api/documents") {
      await fulfillJson(route, []);
      return;
    }
    await fulfillJson(route, { detail: "Unexpected deterministic request." }, 500);
  });
}

async function expectNoHorizontalOverflow(page: Page, width: number) {
  const hasHorizontalOverflow = await page.evaluate(
    () =>
      document.documentElement.scrollWidth >
      document.documentElement.clientWidth,
  );
  expect(hasHorizontalOverflow, `horizontal overflow at ${width}px`).toBe(false);
}

async function expectMinimumTargets(page: Page, context: string, minimum: number) {
  const undersized = await page.locator("a[href], button, [role='treeitem']").evaluateAll(
    (elements, targetMinimum) =>
      elements
        .filter((element) => {
          const style = window.getComputedStyle(element);
          const rect = element.getBoundingClientRect();
          return (
            style.visibility !== "hidden" &&
            style.display !== "none" &&
            rect.width > 0 &&
            rect.height > 0
          );
        })
        .map((element) => {
          const rect = element.getBoundingClientRect();
          return {
            name:
              element.getAttribute("aria-label") ??
              element.textContent?.trim() ??
              element.tagName,
            width: rect.width,
            height: rect.height,
          };
        })
        .filter(
          ({ width, height }) =>
            width < targetMinimum || height < targetMinimum,
        ),
    minimum,
  );
  expect(undersized, `undersized targets in ${context}`).toEqual([]);
}

test("shell remains accessible and overflow-free at acceptance widths", async ({
  page,
}) => {
  await mockEmptyChatApi(page);

  for (const width of [375, 768, 1024, 1440]) {
    await page.setViewportSize({ width, height: 900 });
    await page.goto("/");

    if (width < 1024) {
      await page.getByRole("button", { name: "Open navigation" }).click();
    }
    await expect(
      page.getByRole("navigation", {
        name: width < 1024 ? "Primary" : "Workspace",
      }),
    ).toBeVisible();
    await expect(page.getByRole("link", { name: "Chat", exact: true })).toHaveAttribute(
      "aria-current",
      "page",
    );
    if (width < 1024) {
      await page.getByRole("button", { name: "Close navigation" }).click();
    }
    await expect(
      page.getByRole("textbox", { name: "Question" }),
    ).toBeVisible();

    await expectNoHorizontalOverflow(page, width);
    await expectMinimumTargets(page, `Home at ${width}px`, width < 1024 ? 44 : 30);

    const accessibility = await new AxeBuilder({ page }).analyze();
    expect(
      accessibility.violations,
      `axe violations at ${width}px`,
    ).toEqual([]);
  }
});

test("knowledge base remains overflow-free at acceptance widths", async ({
  page,
}) => {
  await mockEmptyKnowledgeBaseApi(page);

  for (const width of [375, 768, 1024, 1440]) {
    await page.setViewportSize({ width, height: 900 });
    await page.goto("/knowledge-base");

    await expect(
      page.getByRole("heading", { name: "Knowledge Base" }),
    ).toBeVisible();
    await expectNoHorizontalOverflow(page, width);
    await expectMinimumTargets(
      page,
      `Knowledge Base at ${width}px`,
      width < 1024 ? 44 : 30,
    );
    expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  }
});

test("focus is visible, skip works, and reduced motion is honored", async ({
  page,
}) => {
  await mockEmptyChatApi(page);
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.goto("/");
  await expect(page.getByRole("navigation", { name: "Workspace" })).toBeVisible();

  await page.keyboard.press("Tab");
  const skipLink = page.getByRole("link", { name: "Skip to main content" });
  await expect(skipLink).toBeFocused();
  await expect(skipLink).toBeVisible();
  const focusStyle = await skipLink.evaluate((element) => {
    const style = window.getComputedStyle(element);
    return {
      outlineStyle: style.outlineStyle,
      outlineWidth: style.outlineWidth,
    };
  });
  expect(focusStyle.outlineStyle).not.toBe("none");
  expect(Number.parseFloat(focusStyle.outlineWidth)).toBeGreaterThanOrEqual(2);
  expect(
    await page.evaluate(
      () => window.getComputedStyle(document.documentElement).scrollBehavior,
    ),
  ).toBe("auto");
  await page.keyboard.press("Enter");
  await expect(page.locator("#main-content")).toBeFocused();

  await page.locator(".activity-bar .account-menu summary").click();
  await page.getByRole("button", { name: "Full" }).click();
  await expect(page.locator("html")).toHaveAttribute("data-motion-mode", "full");
  await expect(page.locator("html")).toHaveAttribute("data-reduce-motion", "false");
  expect(
    await page.locator(".activity-bar__item").first().evaluate((element) =>
      window.getComputedStyle(element, "::before").transitionDuration,
    ),
  ).toContain("0.18s");
  await page
    .locator(".activity-bar .motion-control")
    .getByRole("button", { name: "System", exact: true })
    .click();
  await expect(page.locator("html")).toHaveAttribute("data-motion-mode", "system");
  await expect(page.locator("html")).toHaveAttribute("data-reduce-motion", "true");
  await page.emulateMedia({ reducedMotion: "no-preference" });
  await expect(page.locator("html")).toHaveAttribute("data-reduce-motion", "false");
});

test("desktop sidebar and local theme preferences persist", async ({ page }) => {
  await mockEmptyChatApi(page);
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/");

  await page.getByRole("button", { name: "Hide sidebar" }).click();
  await expect(page.getByRole("button", { name: "Show sidebar" })).toBeVisible();
  await expect(page.locator('[role="tooltip"][data-visible="true"]')).toHaveCount(0);
  const hiddenSidebarLayout = await page.locator("#main-content").evaluate((main) => {
    const rect = main.getBoundingClientRect();
    return { left: rect.left, top: rect.top, width: rect.width };
  });
  expect(hiddenSidebarLayout).toEqual({ left: 52, top: 0, width: 1388 });
  await page.reload();
  await expect(page.getByRole("button", { name: "Show sidebar" })).toBeVisible();

  await page.locator(".activity-bar .account-menu summary").click();
  await page.getByRole("button", { name: "Dark" }).click();
  await page.getByRole("button", { name: "Reduced" }).click();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await expect(page.locator("html")).toHaveAttribute("data-motion-mode", "reduced");
  await expect(page.locator("html")).toHaveAttribute("data-reduce-motion", "true");
  await page.reload();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await expect(page.locator("html")).toHaveAttribute("data-motion-mode", "reduced");
  await expect(page.locator("html")).toHaveAttribute("data-reduce-motion", "true");
});
