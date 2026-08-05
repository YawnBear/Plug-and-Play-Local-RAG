import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

import { API_ROUTE, fulfillJson } from "./mock-api";

async function mockSetupApi(page: Page) {
  let ownerRequest: unknown;
  await page.route(API_ROUTE, async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (request.method() === "GET" && path === "/api/setup/status") {
      await fulfillJson(route, {
        state: "setup_required",
        code_issued: true,
        code_expires_at: "2026-08-02T05:15:00Z",
        attempts_remaining: 5,
      });
      return;
    }
    if (request.method() === "POST" && path === "/api/setup/challenge") {
      await fulfillJson(route, {
        state: "owner_details_required",
        expires_at: "2026-08-02T05:10:00Z",
      });
      return;
    }
    if (request.method() === "POST" && path === "/api/setup/owner") {
      ownerRequest = request.postDataJSON();
      await fulfillJson(route, {
        state: "setup_complete",
        login_path: "/login",
        first_document_path: "/knowledge-base",
      });
      return;
    }
    await fulfillJson(route, { detail: "Unexpected setup request." }, 500);
  });
  return () => ownerRequest;
}

async function expectNoHorizontalOverflow(page: Page) {
  expect(
    await page.evaluate(
      () =>
        document.documentElement.scrollWidth <=
        document.documentElement.clientWidth,
    ),
  ).toBe(true);
}

test("Personal owner setup is accessible and usable at 375px", async ({ page }) => {
  const ownerRequest = await mockSetupApi(page);
  await page.setViewportSize({ width: 375, height: 812 });
  await page.goto("/setup");

  const code = page.getByRole("textbox", { name: "One-time setup code" });
  await expect(code).toHaveAttribute("autocomplete", "one-time-code");
  await expectNoHorizontalOverflow(page);
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);

  await code.fill("private-setup-code");
  await page.getByRole("button", { name: "Continue" }).click();

  await expect(
    page.getByRole("heading", { name: "Create your owner account" }),
  ).toBeFocused();
  await expect(page.getByLabel("Username")).toHaveAttribute(
    "autocomplete",
    "username",
  );
  await expect(page.getByLabel("Display name")).toHaveAttribute(
    "autocomplete",
    "name",
  );
  await expect(page.getByLabel("Password", { exact: true })).toHaveAttribute(
    "autocomplete",
    "new-password",
  );
  await expect(page.getByLabel("Confirm password")).toHaveAttribute(
    "autocomplete",
    "new-password",
  );

  await page.getByLabel("Username").fill("Owner.One");
  await page.getByLabel("Display name").fill("Owner One");
  await page.getByLabel("Password", { exact: true }).fill("fourteen-chars!");
  await page.getByLabel("Confirm password").fill("fourteen-chars!");
  await expectNoHorizontalOverflow(page);
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  await page.getByRole("button", { name: "Create owner account" }).click();

  await expect(
    page.getByRole("link", { name: "Continue to sign in" }),
  ).toHaveAttribute("href", "/login?next=%2Fknowledge-base&setup=complete");
  expect(ownerRequest()).toEqual({
    username: "owner.one",
    display_name: "Owner One",
    password: "fourteen-chars!",
  });
});
