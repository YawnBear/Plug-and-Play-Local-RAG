import { expect, test } from "@playwright/test";

import {
  API_ROUTE,
  FOLDER_ALPHA,
  fulfillAuthMeIfRequested,
  fulfillJson,
  libraryTree,
} from "./mock-api";

const USER = "99999999-9999-4999-8999-999999999999";
const TEAM = "88888888-8888-4888-8888-888888888888";
const CREATED_USER = "66666666-6666-4666-8666-666666666666";
const CREATED_TEAM = "55555555-5555-4555-8555-555555555555";
const AUDIT = "77777777-7777-4777-8777-777777777777";

test("administrator can navigate users, teams, contextual access, and audit", async ({
  page,
}) => {
  const users = [
    {
      id: USER,
      username: "testadmin",
      display_name: "Test Admin",
      role: "admin",
      status: "active",
    },
  ];
  const teams = [
    {
      id: TEAM,
      name: "Research",
      is_active: true,
      member_ids: [USER],
      member_count: 1,
    },
  ];

  await page.route(API_ROUTE, async (route) => {
    if (await fulfillAuthMeIfRequested(route)) return;
    const url = new URL(route.request().url());
    if (url.pathname === "/api/admin/users") {
      if (route.request().method() === "POST") {
        const input = route.request().postDataJSON();
        users.push({
          id: CREATED_USER,
          username: input.username,
          display_name: input.display_name,
          role: input.role,
          status: "active",
        });
        await fulfillJson(route, {
          user_id: CREATED_USER,
          activation_code: "one-time-code",
        });
        return;
      }
      await fulfillJson(route, { users });
      return;
    }
    if (url.pathname === "/api/admin/teams") {
      if (route.request().method() === "POST") {
        const input = route.request().postDataJSON();
        teams.push({
          id: CREATED_TEAM,
          name: input.name,
          is_active: true,
          member_ids: [],
          member_count: 0,
        });
        await fulfillJson(route, { team_id: CREATED_TEAM });
        return;
      }
      await fulfillJson(route, { teams });
      return;
    }
    if (url.pathname === "/api/admin/grants") {
      await fulfillJson(route, { grants: [] });
      return;
    }
    if (url.pathname === "/api/admin/audit") {
      await fulfillJson(route, {
        events: [
          {
            id: AUDIT,
            actor_user_id: USER,
            event_type: "team_created",
            target_type: "team",
            target_id: TEAM,
            details: { name: "Research" },
            correlation_id: null,
            created_at: "2026-07-26T00:00:00Z",
          },
        ],
      });
      return;
    }
    if (url.pathname === "/api/library/tree") {
      await fulfillJson(route, libraryTree());
      return;
    }
    if (url.pathname === "/api/documents") {
      await fulfillJson(route, []);
      return;
    }
    if (url.pathname === "/api/admin/access") {
      await fulfillJson(route, {
        node_id: url.searchParams.get("node_id"),
        nearest_boundary_node_id: null,
        direct_grants: [],
        inherited_grants: [],
        direct_create_grants: [],
        inherited_create_grants: [],
      });
      return;
    }
    await fulfillJson(route, { detail: "Unexpected admin request." }, 500);
  });

  await page.goto("/admin/users");
  await expect(page.getByRole("heading", { name: "Users" })).toBeVisible();
  expect(
    await page.locator(".admin-workspace > .workspace-header").evaluate(
      (header) => getComputedStyle(header).borderBottomStyle,
    ),
  ).toBe("none");
  const adminTabs = page.getByRole("navigation", {
    name: "Administration sections",
  });
  await expect(adminTabs).toBeVisible();
  expect(
    await adminTabs.evaluate((element) => {
      const style = getComputedStyle(element);
      return {
        overflowX: style.overflowX,
        overflowY: style.overflowY,
        scrollableX: element.scrollWidth > element.clientWidth,
        scrollableY: element.scrollHeight > element.clientHeight,
      };
    }),
  ).toEqual({
    overflowX: "visible",
    overflowY: "visible",
    scrollableX: false,
    scrollableY: false,
  });
  await expect(page.getByText("@testadmin")).toBeVisible();
  await page.getByLabel("Username").first().fill("newmember");
  await page.getByLabel("Display name").first().fill("New Member");
  await page.getByRole("button", { name: "Create user" }).click();
  await expect(page.getByText("@newmember")).toBeVisible();
  await expect(page.getByText("one-time-code")).toBeVisible();
  await expect(page.getByLabel("Username").first()).toHaveValue("");

  await page.getByRole("link", { name: "Teams" }).last().click();
  await expect(page.getByRole("heading", { name: "Teams" })).toBeVisible();
  const teamsTabsTop = await adminTabs.evaluate((element) =>
    Math.round(element.getBoundingClientRect().top),
  );
  await expect(page.getByText("Research")).toBeVisible();
  await page.getByLabel("Team name").fill("Applied Research");
  await page.getByRole("button", { name: "Create team" }).click();
  await expect(page.getByRole("heading", { name: "Applied Research" })).toBeVisible();
  await expect(page.getByLabel("Team name")).toHaveValue("");

  await page.goto(`/admin/access?node=${FOLDER_ALPHA}`);
  await expect(page.getByRole("heading", { name: "Access" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Alpha" })).toBeVisible();
  await expect(
    page.getByText("Access inherited from parent folders continues here."),
  ).toBeVisible();
  await expect(page.getByRole("button", { name: "Set boundary" })).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Remove boundary" }),
  ).toHaveCount(0);

  const readAccess = page.getByRole("region", { name: "Read access" });
  await expect(readAccess).toBeVisible();
  await expect(
    readAccess.getByRole("heading", { level: 4, name: "Users" }),
  ).toBeVisible();
  await expect(
    readAccess.getByRole("heading", { level: 4, name: "Teams" }),
  ).toBeVisible();

  const createSubfolders = page.getByRole("region", {
    name: "Create subfolders",
  });
  await expect(createSubfolders).toBeVisible();
  await expect(
    createSubfolders.getByRole("heading", { level: 4, name: "Users" }),
  ).toBeVisible();
  await expect(
    createSubfolders.getByRole("heading", { level: 4, name: "Teams" }),
  ).toBeVisible();
  expect(
    await createSubfolders.evaluate(
      (element) => getComputedStyle(element).borderTopWidth,
    ),
  ).toBe("0px");

  const desktopLayout = await page
    .locator(".access-workspace__panes")
    .evaluate((panes) => {
      const nodes = panes.querySelector<HTMLElement>(".access-workspace__nodes");
      const nodeList = panes.querySelector<HTMLElement>(
        ".access-workspace__node-list",
      );
      const detail = panes.querySelector<HTMLElement>(".access-workspace__detail");
      const detailHeader = detail?.querySelector<HTMLElement>("header");
      const detailScroll = panes.querySelector<HTMLElement>(
        ".access-workspace__detail-scroll",
      );
      return {
        detailHeight: detail?.getBoundingClientRect().height,
        detailHeaderTop: detailHeader?.getBoundingClientRect().top,
        detailOverflow: detailScroll
          ? getComputedStyle(detailScroll).overflowY
          : null,
        detailScrollHeight: detailScroll?.scrollHeight,
        detailVisibleHeight: detailScroll?.clientHeight,
        nodeHeight: nodes?.getBoundingClientRect().height,
        nodeOverflow: nodeList ? getComputedStyle(nodeList).overflowY : null,
        pageHeight: document.documentElement.clientHeight,
        pageScrollHeight: document.documentElement.scrollHeight,
      };
    });
  expect(desktopLayout.nodeHeight).toBe(desktopLayout.detailHeight);
  expect(desktopLayout.nodeOverflow).toBe("auto");
  expect(desktopLayout.detailOverflow).toBe("auto");
  expect(desktopLayout.detailScrollHeight ?? 0).toBeGreaterThan(
    desktopLayout.detailVisibleHeight ?? 0,
  );
  expect(desktopLayout.pageScrollHeight).toBe(desktopLayout.pageHeight);

  const detailScroll = page.locator(".access-workspace__detail-scroll");
  await detailScroll.evaluate((element) => {
    element.scrollTop = element.scrollHeight;
  });
  expect(
    await page.locator(".access-workspace__detail header").evaluate(
      (header) => header.getBoundingClientRect().top,
    ),
  ).toBe(desktopLayout.detailHeaderTop);
  expect(await page.evaluate(() => window.scrollY)).toBe(0);

  await page.getByRole("link", { name: "Audit" }).last().click();
  await expect(page.getByRole("heading", { name: "Audit" })).toBeVisible();
  expect(
    await adminTabs.evaluate((element) =>
      Math.round(element.getBoundingClientRect().top),
    ),
  ).toBe(teamsTabsTop);
  await expect(page.getByText("team_created")).toBeVisible();

  await page.setViewportSize({ width: 375, height: 812 });
  await page.goto(`/admin/access?node=${FOLDER_ALPHA}`);
  const mobileLayout = await page
    .locator(".access-workspace__panes")
    .evaluate((panes) => {
      const nodeList = panes.querySelector<HTMLElement>(
        ".access-workspace__node-list",
      );
      const detailScroll = panes.querySelector<HTMLElement>(
        ".access-workspace__detail-scroll",
      );
      return {
        detailOverflow: detailScroll
          ? getComputedStyle(detailScroll).overflowY
          : null,
        nodeOverflow: nodeList ? getComputedStyle(nodeList).overflowY : null,
        pageHeight: document.documentElement.clientHeight,
        pageScrollHeight: document.documentElement.scrollHeight,
      };
    });
  expect(mobileLayout.nodeOverflow).toBe("visible");
  expect(mobileLayout.detailOverflow).toBe("visible");
  expect(mobileLayout.pageScrollHeight).toBeGreaterThan(mobileLayout.pageHeight);
});
