import { defineConfig, devices } from "@playwright/test";
import { existsSync } from "node:fs";
import { isAbsolute } from "node:path";

const liveBaseUrl = process.env.PLAYWRIGHT_LIVE_BASE_URL;
const liveApiUrl = process.env.PLAYWRIGHT_LIVE_API_URL;
const deploymentId = process.env.PLAYWRIGHT_LIVE_DEPLOYMENT_ID;
const confirmation = process.env.PLAYWRIGHT_LIVE_CONFIRMATION;
const runId = process.env.PLAYWRIGHT_LIVE_RUN_ID;
const pdfPath = process.env.PLAYWRIGHT_LIVE_PDF_PATH;
const question = process.env.PLAYWRIGHT_LIVE_QUESTION;
const REQUIRED_CONFIRMATION =
  "I_ACKNOWLEDGE_THIS_MUTATES_AND_DELETES_DEDICATED_TEST_DATA";

if (!liveBaseUrl) {
  throw new Error(
    "PLAYWRIGHT_LIVE_BASE_URL is required for the guarded live Playwright suite.",
  );
}
if (!liveApiUrl || !deploymentId) {
  throw new Error(
    "PLAYWRIGHT_LIVE_API_URL and PLAYWRIGHT_LIVE_DEPLOYMENT_ID are required.",
  );
}
if (confirmation !== REQUIRED_CONFIRMATION) {
  throw new Error(
    `PLAYWRIGHT_LIVE_CONFIRMATION must equal ${REQUIRED_CONFIRMATION}. The live suite creates and deletes library data.`,
  );
}
if (!runId || !/^[a-z0-9][a-z0-9-]{5,39}$/.test(runId)) {
  throw new Error(
    "PLAYWRIGHT_LIVE_RUN_ID must be a unique 6-40 character lowercase alphanumeric/hyphen identifier.",
  );
}
if (
  !pdfPath ||
  !isAbsolute(pdfPath) ||
  !pdfPath.toLocaleLowerCase().endsWith(".pdf") ||
  !existsSync(pdfPath)
) {
  throw new Error(
    "PLAYWRIGHT_LIVE_PDF_PATH must identify an existing absolute PDF path.",
  );
}
if (!question?.trim()) {
  throw new Error(
    "PLAYWRIGHT_LIVE_QUESTION must be an answerable question grounded in the supplied PDF.",
  );
}

const parsedBaseUrl = new URL(liveBaseUrl);
const parsedApiUrl = new URL(liveApiUrl);
if (!["localhost", "127.0.0.1", "::1"].includes(parsedBaseUrl.hostname)) {
  throw new Error(
    "The Phase 5 live suite is restricted to a dedicated loopback deployment.",
  );
}
if (!["localhost", "127.0.0.1", "::1"].includes(parsedApiUrl.hostname)) {
  throw new Error("The live API must be a dedicated loopback deployment.");
}

export default defineConfig({
  testDir: "./tests/e2e-live",
  fullyParallel: false,
  forbidOnly: true,
  retries: 0,
  reporter: "list",
  timeout: 20 * 60 * 1_000,
  use: {
    baseURL: parsedBaseUrl.toString(),
    trace: "retain-on-failure",
    actionTimeout: 30_000,
  },
  projects: [
    {
      name: "chromium-live",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
});
