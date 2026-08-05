import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page, type Route } from "@playwright/test";

import {
  API_ROUTE,
  CHAT_ALPHA,
  CHAT_BETA,
  CHAT_DOCUMENT,
  TURN,
  betaFolder,
  chatDetail,
  chatSummary,
  citationEvidence,
  completedTurn,
  digitalCitationPdf,
  fulfillAuthMeIfRequested,
  fulfillJson,
  libraryTree,
  sseBody,
} from "./mock-api";

interface ChatMockOptions {
  delayedFirstStream?: boolean;
}

async function fulfillSse(route: Route, body: string): Promise<void> {
  await route.fulfill({
    status: 200,
    contentType: "text/event-stream",
    headers: {
      "access-control-allow-origin": "*",
      "cache-control": "no-cache",
    },
    body,
  });
}

async function mockChatApi(
  page: Page,
  { delayedFirstStream = false }: ChatMockOptions = {},
) {
  const summaries = [
    chatSummary(CHAT_ALPHA, "Alpha chat"),
    chatSummary(CHAT_BETA, "Beta chat"),
  ];
  const details = new Map([
    [
      CHAT_ALPHA,
      chatDetail(CHAT_ALPHA, "Alpha chat", [
        completedTurn({ sourceAvailable: false }),
      ]),
    ],
    [CHAT_BETA, chatDetail(CHAT_BETA, "Beta chat")],
  ]);

  await page.route(API_ROUTE, async (route) => {
    if (await fulfillAuthMeIfRequested(route)) return;
    const request = route.request();
    const url = new URL(request.url());
    const method = request.method();

    if (method === "GET" && url.pathname === "/api/chats") {
      await fulfillJson(route, summaries);
      return;
    }
    const chatMatch = url.pathname.match(
      /^\/api\/chats\/([0-9a-f-]+)$/,
    );
    if (method === "GET" && chatMatch) {
      await fulfillJson(route, details.get(chatMatch[1]));
      return;
    }
    const evidenceMatch = url.pathname.match(
      /^\/api\/chats\/[0-9a-f-]+\/turns\/[0-9a-f-]+\/citations\/S1\/evidence$/,
    );
    if (method === "GET" && evidenceMatch) {
      await fulfillJson(route, citationEvidence());
      return;
    }
    if (
      method === "GET" &&
      url.pathname === `/api/documents/${CHAT_DOCUMENT}/content`
    ) {
      await route.fulfill({
        status: 200,
        contentType: "application/pdf",
        headers: { "accept-ranges": "bytes" },
        body: digitalCitationPdf(),
      });
      return;
    }
    const scopeMatch = url.pathname.match(
      /^\/api\/chats\/([0-9a-f-]+)\/scope$/,
    );
    if (method === "PUT" && scopeMatch) {
      const chatId = scopeMatch[1];
      const body = request.postDataJSON() as {
        mode: "all_ready" | "selected";
        node_ids: string[];
      };
      const current = details.get(chatId)!;
      const next = {
        ...current,
        scope_mode: body.mode,
        scope_version: current.scope_version + 1,
        scope_node_ids: body.node_ids,
      };
      details.set(chatId, next);
      const index = summaries.findIndex((chat) => chat.chat_id === chatId);
      summaries[index] = {
        ...summaries[index],
        scope_mode: body.mode,
        scope_version: next.scope_version,
      };
      await fulfillJson(route, {
        ...summaries[index],
        scope_node_ids: next.scope_node_ids,
      });
      return;
    }
    if (method === "GET" && url.pathname === "/api/library/tree") {
      await fulfillJson(route, libraryTree());
      return;
    }
    if (method === "GET" && url.pathname === "/api/library/browse") {
      await fulfillJson(route, {
        parent_id: url.searchParams.get("parent_id"),
        breadcrumbs: url.searchParams.has("parent_id") ? [betaFolder()] : [],
        children: [],
        page: Number(url.searchParams.get("page") ?? 1),
        limit: Number(url.searchParams.get("limit") ?? 100),
        total: 0,
      });
      return;
    }
    const messageMatch = url.pathname.match(
      /^\/api\/chats\/([0-9a-f-]+)\/messages\/stream$/,
    );
    if (method === "POST" && messageMatch) {
      const chatId = messageMatch[1];
      if (delayedFirstStream) {
        details.set(chatId, chatDetail(chatId, "Beta chat", [
          completedTurn({ status: "interrupted" }),
        ]));
        await new Promise((resolve) => setTimeout(resolve, 750));
      } else {
        details.set(chatId, chatDetail(chatId, "Beta chat", [completedTurn()]));
      }
      await fulfillSse(route, sseBody(chatId)).catch(() => undefined);
      return;
    }
    const retryMatch = url.pathname.match(
      /^\/api\/chats\/([0-9a-f-]+)\/turns\/([0-9a-f-]+)\/retry\/stream$/,
    );
    if (method === "POST" && retryMatch) {
      const chatId = retryMatch[1];
      details.set(
        chatId,
        chatDetail(chatId, "Beta chat", [
          completedTurn({ status: "complete", attempt: 2 }),
        ]),
      );
      await fulfillSse(route, sseBody(chatId));
      return;
    }
    await fulfillJson(route, { detail: "Unexpected deterministic request." }, 500);
  });

  return { details, summaries };
}

test("restores, switches, scopes, and preserves deleted-source history", async ({
  page,
}) => {
  await mockChatApi(page);
  await page.goto(`/?chat=${CHAT_ALPHA}`);

  await expect(page.getByRole("heading", { name: "Alpha chat" })).toBeVisible();
  await expect(page.getByText("Unavailable")).toBeVisible();
  await page.reload();
  await expect(page.getByRole("heading", { name: "Alpha chat" })).toBeVisible();

  await page.getByRole("button", { name: "Beta chat", exact: true }).click();
  await expect(page).toHaveURL(new RegExp(`chat=${CHAT_BETA}`));
  await expect(page.getByRole("heading", { name: "Beta chat" })).toBeVisible();

  await page.getByRole("button", { name: /Conversation scope:/ }).click();
  await page.getByRole("radio", { name: "Selected folders and files" }).check();
  await page.getByLabel("Include folder Beta").check();
  await page.getByRole("button", { name: "Save scope" }).click();
  await expect(
    page.getByRole("button", { name: "Conversation scope: 1 selected" }),
  ).toBeVisible();

  await page.reload();
  await expect(page.getByRole("heading", { name: "Beta chat" })).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Conversation scope: 1 selected" }),
  ).toBeVisible();
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
});

test("switches recent conversations from the unified mobile drawer", async ({
  page,
}) => {
  await mockChatApi(page);
  await page.setViewportSize({ width: 375, height: 812 });
  await page.goto(`/?chat=${CHAT_ALPHA}`);

  await page.getByRole("button", { name: "Open navigation" }).click();
  const drawer = page.getByRole("dialog", { name: "Application navigation" });
  await expect(drawer).toBeVisible();
  await drawer
    .getByRole("button", { name: "Beta chat", exact: true })
    .click();

  await expect(drawer).not.toBeVisible();
  await expect(page).toHaveURL(new RegExp(`chat=${CHAT_BETA}`));
  await expect(page.getByRole("heading", { name: "Beta chat" })).toBeVisible();
});

test("stops an in-flight answer and retries the same interrupted turn", async ({
  page,
}) => {
  await mockChatApi(page, { delayedFirstStream: true });
  await page.goto(`/?chat=${CHAT_BETA}`);

  await page.getByLabel("Question").fill("What was found?");
  await page.getByRole("button", { name: "Send" }).click();
  await expect(page.getByText("Draft · Unverified")).toBeVisible();
  await page.getByRole("button", { name: "Stop" }).click();

  await expect(page.getByText("Interrupted", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Retry this turn" }).click();
  await expect(page.getByText("Verified", { exact: true })).toBeVisible();
  await expect(page.getByText("The verified finding [S1].")).toBeVisible();
  await page
    .getByRole("button", { name: "S1, Research.pdf, page 2" })
    .click();
  const sourceDrawer = page.locator("dialog.drawer--right");
  await expect(sourceDrawer).toBeVisible();
  await expect(sourceDrawer.locator("mark")).toHaveText(
    "The complete cited chunk.",
  );
  await expect(sourceDrawer.locator(".citation-pdf__overlay rect").first()).toBeVisible();
  await expect(
    page.getByRole("link", { name: "Open in Knowledge Base" }),
  ).toHaveAttribute("href", /knowledge-base.*page=2/);

  await page
    .getByRole("button", { name: "Close S1 · Research.pdf" })
    .click();
  await expect(sourceDrawer).not.toBeVisible();
  await expect(sourceDrawer).not.toHaveAttribute("open");
  expect(
    await sourceDrawer.evaluate((dialog) => getComputedStyle(dialog).display),
  ).toBe("none");

  await page
    .getByRole("button", { name: "Preview Research.pdf, Page 2" })
    .click();
  await expect(sourceDrawer).toBeVisible();
  await page.mouse.click(20, 100);
  await expect(sourceDrawer).not.toBeVisible();
  await expect(sourceDrawer).not.toHaveAttribute("open");
  expect(TURN).toMatch(/^[0-9a-f-]{36}$/);
});
