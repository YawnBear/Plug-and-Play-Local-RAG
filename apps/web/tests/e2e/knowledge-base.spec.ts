import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

import {
  DOCUMENT,
  FOLDER_BETA,
  API_ROUTE,
  betaFile,
  betaFolder,
  documentSummary,
  fulfillAuthMeIfRequested,
  fulfillEmpty,
  fulfillJson,
  libraryTree,
} from "./mock-api";

async function mockKnowledgeBaseApi(page: Page): Promise<void> {
  await page.route(API_ROUTE, async (route) => {
    if (await fulfillAuthMeIfRequested(route)) return;
    const request = route.request();
    const url = new URL(request.url());
    if (request.method() === "GET" && url.pathname === "/api/account/teams") {
      await fulfillJson(route, { teams: [], requires_team_selection: false });
      return;
    }
    if (request.method() === "GET" && url.pathname === "/api/library/tree") {
      await fulfillJson(route, libraryTree());
      return;
    }
    if (request.method() === "GET" && url.pathname === "/api/library/browse") {
      const parentId = url.searchParams.get("parent_id");
      await fulfillJson(
        route,
        parentId === FOLDER_BETA
          ? {
              parent_id: FOLDER_BETA,
              breadcrumbs: [betaFolder()],
              children: [betaFile()],
              page: Number(url.searchParams.get("page") ?? 1),
              limit: Number(url.searchParams.get("limit") ?? 100),
              total: 1,
            }
          : {
              parent_id: null,
              breadcrumbs: [],
              children: libraryTree().map(
                ({ children: omittedChildren, ...node }) => {
                  void omittedChildren;
                  return {
                    ...node,
                    kind: "folder",
                    document_id: null,
                    uploader_user_id: null,
                    can_manage: true,
                    can_create_children: true,
                    readable_document_count: 0,
                  };
                },
              ),
              page: Number(url.searchParams.get("page") ?? 1),
              limit: Number(url.searchParams.get("limit") ?? 100),
              total: libraryTree().length,
            },
      );
      return;
    }
    if (request.method() === "GET" && url.pathname === "/api/documents") {
      await fulfillJson(route, [documentSummary()]);
      return;
    }
    if (
      request.method() === "HEAD" &&
      url.pathname === `/api/documents/${DOCUMENT}/content`
    ) {
      await fulfillEmpty(route, 200);
      return;
    }
    if (
      request.method() === "GET" &&
      url.pathname === `/api/documents/${DOCUMENT}/content`
    ) {
      await route.fulfill({
        status: 200,
        contentType: "application/pdf",
        headers: { "access-control-allow-origin": "*" },
        body: "%PDF-1.4\n%%EOF",
      });
      return;
    }
    await fulfillJson(route, { detail: "Unexpected deterministic request." }, 500);
  });
}

test("restores a PDF deep link and preserves its cited page", async ({ page }) => {
  await mockKnowledgeBaseApi(page);
  await page.goto(
    `/knowledge-base?folder=${FOLDER_BETA}&document=${DOCUMENT}&page=3`,
  );

  await expect(
    page.getByRole("region", { name: "Knowledge Base editor" }),
  ).toBeVisible();
  const documentRow = page.getByRole("treeitem", {
    name: "Evidence.pdf ready Actions for Evidence.pdf",
  });
  await expect(documentRow).toHaveAttribute("aria-selected", "true");
  await expect(page.getByLabel("Evidence.pdf, page 3")).toHaveAttribute(
    "src",
    /#page=3$/,
  );
  await expect(page.getByRole("link", { name: "Open in new tab" })).toHaveAttribute(
    "href",
    /#page=3$/,
  );

  await page.reload();
  await expect(page.getByLabel("Evidence.pdf, page 3")).toBeVisible();
  await expect(page).toHaveURL(new RegExp(`folder=${FOLDER_BETA}.*document=${DOCUMENT}.*page=3`));

  const explorer = page.getByRole("region", { name: "Local PDF Library" });
  const editorToolbar = page.locator(".kb-breadcrumb-bar");
  const ingestionActivity = editorToolbar.getByRole("button", {
    name: "Toggle ingestion activity",
  });
  const documentDetails = editorToolbar.getByRole("button", {
    name: "Hide document details",
  });
  await expect(
    ingestionActivity,
  ).toBeVisible();
  await expect(
    explorer.getByRole("button", { name: "Toggle ingestion activity" }),
  ).toHaveCount(0);
  await expect(
    explorer.getByRole("button", { name: "Explorer actions" }),
  ).toHaveCount(0);
  await expect(
    page
      .locator(".activity-bar")
      .getByRole("button", { name: "Toggle ingestion activity" }),
  ).toHaveCount(0);
  const [ingestionBox, detailsBox] = await Promise.all([
    ingestionActivity.boundingBox(),
    documentDetails.boundingBox(),
  ]);
  expect(ingestionBox).not.toBeNull();
  expect(detailsBox).not.toBeNull();
  expect(ingestionBox!.x + ingestionBox!.width).toBeLessThanOrEqual(
    detailsBox!.x,
  );

  const rootChevron = explorer.getByRole("button", { name: "Collapse Root" });
  const rootLabel = explorer.getByRole("button", {
    name: "Root",
    exact: true,
  });
  const [chevronBox, labelBox] = await Promise.all([
    rootChevron.boundingBox(),
    rootLabel.boundingBox(),
  ]);
  expect(chevronBox).not.toBeNull();
  expect(labelBox).not.toBeNull();
  expect(labelBox!.x).toBeGreaterThanOrEqual(
    chevronBox!.x + chevronBox!.width,
  );

  const documentChevronPlaceholder = documentRow.locator(
    "span.kb-explorer__chevron",
  );
  const documentChevronBox = await documentChevronPlaceholder.boundingBox();
  expect(documentChevronBox).not.toBeNull();
  await page.mouse.move(
    documentChevronBox!.x + documentChevronBox!.width / 2,
    documentChevronBox!.y + documentChevronBox!.height / 2,
  );
  await expect(documentChevronPlaceholder).toHaveCSS(
    "background-color",
    "rgba(0, 0, 0, 0)",
  );

  const detailsPane = page.locator(".kb-details-wrap");
  await documentRow
    .getByRole("button", { name: "Actions for Evidence.pdf" })
    .click();
  await page.getByRole("menuitem", { name: "View details" }).click();
  await expect(detailsPane).toBeVisible();
  await expect(documentDetails).toHaveAttribute("aria-pressed", "true");

  await documentDetails.click();
  await expect(detailsPane).toBeHidden();
  const showDocumentDetails = editorToolbar.getByRole("button", {
    name: "Show document details",
  });
  await expect(showDocumentDetails).toHaveAttribute("aria-pressed", "false");

  await documentRow.getByRole("button", {
    name: "Actions for Evidence.pdf",
  }).click();
  await page.getByRole("menuitem", { name: "View details" }).click();
  await expect(detailsPane).toBeVisible();
  await expect(documentDetails).toHaveAttribute("aria-pressed", "true");

  await documentRow.getByRole("button", {
    name: "Actions for Evidence.pdf",
  }).click();
  await page.getByRole("menuitem", { name: "View details" }).click();
  await expect(detailsPane).toBeVisible();
  await expect(documentDetails).toHaveAttribute("aria-pressed", "true");

  const sidebar = page.locator(".primary-sidebar");
  const sidebarResize = page.getByRole("separator", {
    name: "Resize sidebar",
  });
  const [sidebarBefore, resizeBox] = await Promise.all([
    sidebar.boundingBox(),
    sidebarResize.boundingBox(),
  ]);
  expect(sidebarBefore).not.toBeNull();
  expect(resizeBox).not.toBeNull();
  await page.mouse.move(
    resizeBox!.x + resizeBox!.width / 2,
    resizeBox!.y + 300,
  );
  await page.mouse.down();
  await page.mouse.move(
    resizeBox!.x + resizeBox!.width / 2 + 60,
    resizeBox!.y + 300,
    { steps: 6 },
  );
  await page.mouse.up();
  const sidebarAfter = await sidebar.boundingBox();
  expect(sidebarAfter).not.toBeNull();
  expect(sidebarAfter!.width).toBeGreaterThan(sidebarBefore!.width + 40);
  await expect(page.locator("html")).not.toHaveClass(/is-resizing-sidebar/);
  expect(
    await page.getByLabel("Evidence.pdf, page 3").evaluate(
      (frame) => getComputedStyle(frame).pointerEvents,
    ),
  ).toBe("auto");

  const accessibility = await new AxeBuilder({ page })
    .exclude("iframe")
    .analyze();
  expect(accessibility.violations).toEqual([]);
});

test("mobile navigation returns focus and the Explorer supports arrow navigation", async ({
  page,
}) => {
  await mockKnowledgeBaseApi(page);
  await page.setViewportSize({ width: 375, height: 900 });
  await page.goto("/knowledge-base");

  const trigger = page.getByRole("button", { name: "Open navigation" });
  await trigger.click();
  const drawer = page.getByRole("dialog", { name: "Application navigation" });
  await expect(drawer).toBeVisible();
  await drawer.getByRole("button", { name: "Close navigation" }).click();
  await expect(trigger).toBeFocused();

  await page.setViewportSize({ width: 1024, height: 900 });
  const beta = page.getByRole("treeitem", { name: "Beta" });
  await beta.focus();
  await page.keyboard.press("ArrowUp");
  await expect(page.getByRole("treeitem", { name: "Alpha" })).toBeFocused();
});
