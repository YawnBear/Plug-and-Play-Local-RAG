import { expect, test, type Page } from "@playwright/test";

import {
  API_ROUTE,
  DOCUMENT,
  FOLDER_BETA,
  betaFile,
  betaFolder,
  documentSummary,
  fulfillAuthMeIfRequested,
  fulfillEmpty,
  fulfillJson,
  libraryTree,
} from "./mock-api";

const user = {
  id: "99999999-9999-4999-8999-999999999999",
  username: "testadmin",
  display_name: "Test Admin",
  role: "admin",
  status: "active",
};

async function mockKnowledgeBase(page: Page): Promise<void> {
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
      await fulfillJson(route, {
        parent_id: FOLDER_BETA,
        breadcrumbs: [betaFolder()],
        children: [betaFile()],
        page: 1,
        limit: 100,
        total: 1,
      });
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
    await fulfillJson(route, { detail: "Unexpected deterministic request." }, 500);
  });
}

test("expires after activity refresh and restores the exact protected route", async ({
  page,
}) => {
  await mockKnowledgeBase(page);
  let expired = false;
  let refreshes = 0;
  let anonymousCsrfRequests = 0;
  await page.route(API_ROUTE, async (route) => {
    const request = route.request();
    const pathname = new URL(request.url()).pathname;
    if (request.method() === "GET" && pathname === "/api/auth/me") {
      if (expired) {
        anonymousCsrfRequests += 1;
        await fulfillJson(route, {
          user: null,
          csrf_token: "replacement-playwright-csrf",
        });
      } else {
        await fulfillJson(route, {
          user,
          csrf_token: "deterministic-playwright-csrf",
        });
      }
      return;
    }
    if (request.method() === "POST" && pathname === "/api/auth/refresh") {
      refreshes += 1;
      if (expired) {
        await fulfillJson(
          route,
          {
            detail: {
              code: "session_expired",
              message: "Your session expired after 30 minutes of inactivity.",
            },
          },
          401,
        );
      } else {
        await fulfillJson(route, {
          user,
          csrf_token: "deterministic-playwright-csrf",
        });
      }
      return;
    }
    if (request.method() === "POST" && pathname === "/api/auth/login") {
      if (
        request.headers()["x-csrf-token"] !== "replacement-playwright-csrf"
      ) {
        await fulfillJson(route, { detail: "invalid CSRF token" }, 403);
        return;
      }
      expired = false;
      await fulfillJson(route, {
        user,
        csrf_token: "deterministic-playwright-csrf",
      });
      return;
    }
    await route.fallback();
  });
  const deepLink =
    `/knowledge-base?folder=${FOLDER_BETA}&document=${DOCUMENT}&page=3`;
  await page.goto(deepLink);
  const documentHeading = page.getByRole("heading", {
    name: "Evidence.pdf",
    level: 1,
  });
  await expect(documentHeading).toBeVisible();
  const activityControl = page.getByRole("button", {
    name: "Toggle ingestion activity",
  });

  await page.evaluate(() => localStorage.removeItem("rag-session-last-refresh"));
  await activityControl.click();
  await expect.poll(() => refreshes).toBe(1);

  expired = true;
  await page.evaluate(() => localStorage.removeItem("rag-session-last-refresh"));
  await activityControl.click();
  await expect(page).toHaveURL(/\/login\?.*reason=expired/);
  await expect(
    page.getByText(
      "Your session expired after 30 minutes of inactivity. Sign in to continue.",
    ),
  ).toBeVisible();

  await page.getByLabel("Username").fill("testadmin");
  await page.getByLabel("Password").fill("not-a-real-password");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).toHaveURL(new RegExp(deepLink.replaceAll("?", "\\?")));
  expect(anonymousCsrfRequests).toBe(1);
});
