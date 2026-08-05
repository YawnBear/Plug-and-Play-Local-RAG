import { expect, test, type Locator, type Page } from "@playwright/test";
import { basename } from "node:path";

const runId = process.env.PLAYWRIGHT_LIVE_RUN_ID!;
const pdfPath = process.env.PLAYWRIGHT_LIVE_PDF_PATH!;
const question = process.env.PLAYWRIGHT_LIVE_QUESTION!;
const apiUrl = process.env.PLAYWRIGHT_LIVE_API_URL!;
const deploymentId = process.env.PLAYWRIGHT_LIVE_DEPLOYMENT_ID!;
const pdfName = basename(pdfPath);
const sourceFolder = `pw-${runId}-source`;
const destinationFolder = `pw-${runId}-destination`;
const initialChild = `pw-${runId}-child`;
const movedChild = `pw-${runId}-evidence`;
let createdChatId: string | null = null;
let createdDocumentId: string | null = null;
let createdFolderIds: string[] = [];

function dialog(page: Page, name: string): Locator {
  return page.getByRole("dialog", { name });
}

async function createFolder(page: Page, name: string): Promise<void> {
  await page.getByRole("button", { name: "New folder" }).click();
  const create = dialog(page, "Create folder");
  await create.getByLabel("Folder name").fill(name);
  await create.getByRole("button", { name: "Save" }).click();
  await expect(page.getByRole("treeitem", { name, exact: true })).toBeVisible();
}

async function selectFolder(page: Page, name: string): Promise<void> {
  await page.getByRole("treeitem", { name, exact: true }).click();
  await expect(page.getByText(`Current folder: ${name}`)).toBeVisible();
}

async function deleteCurrentFolder(page: Page): Promise<void> {
  await page.getByRole("button", { name: "Delete", exact: true }).click();
  await dialog(page, "Delete empty folder?")
    .getByRole("button", { name: "Delete folder", exact: true })
    .click();
}

test.describe.serial("guarded Phase 5 live flow", () => {
  test.afterEach(async ({ page, request }) => {
    await page.close();
    if (createdDocumentId) {
      const deleted = await request.delete(
        `${apiUrl}/api/documents/${createdDocumentId}`,
      );
      expect([204, 404]).toContain(deleted.status());
    }
    if (createdChatId) {
      let chatDeleted = false;
      for (let attempt = 0; attempt < 72; attempt += 1) {
        const deleted = await request.delete(
          `${apiUrl}/api/chats/${createdChatId}`,
        );
        if ([204, 404].includes(deleted.status())) {
          chatDeleted = true;
          break;
        }
        expect(deleted.status()).toBe(409);
        await new Promise((resolve) => setTimeout(resolve, 5_000));
      }
      expect(chatDeleted).toBe(true);
    }
    for (const folderId of createdFolderIds) {
      const deleted = await request.delete(
        `${apiUrl}/api/library/folders/${folderId}`,
      );
      expect([204, 404]).toContain(deleted.status());
    }
  });

  test("persists a nested PDF, scoped chat, citation, and deleted-source state", async ({
    page,
    request,
  }) => {
    const readiness = await request.get(`${apiUrl}/ready`);
    expect(readiness.status()).toBe(200);
    expect(await readiness.json()).toMatchObject({
      ready: true,
      deployment_id: deploymentId,
      database: true,
      object_storage_endpoint: true,
      object_storage_bucket: true,
    });
    await page.route("**/api/**", async (route) => {
      const requestOrigin = new URL(route.request().url()).origin;
      if (requestOrigin !== new URL(apiUrl).origin) {
        await route.abort("blockedbyclient");
        throw new Error(
          `Refusing browser API request to unexpected origin ${requestOrigin}`,
        );
      }
      await route.continue();
    });
    await page.goto("/knowledge-base");

    await createFolder(page, sourceFolder);
    await createFolder(page, destinationFolder);
    await selectFolder(page, sourceFolder);
    await createFolder(page, initialChild);
    await selectFolder(page, initialChild);

    await page.getByRole("button", { name: "Rename", exact: true }).click();
    const rename = dialog(page, "Rename folder");
    await rename.getByLabel("Folder name").fill(movedChild);
    await rename.getByRole("button", { name: "Save" }).click();
    await expect(page.getByText(`Current folder: ${movedChild}`)).toBeVisible();

    await page.getByRole("button", { name: "Move", exact: true }).click();
    const move = dialog(page, "Move folder");
    await move.getByLabel("Destination").selectOption({
      label: destinationFolder,
    });
    await move.getByRole("button", { name: "Save" }).click();
    await expect(
      page.getByRole("button", { name: destinationFolder, exact: true }),
    ).toBeVisible();
    const tree = await (await request.get(`${apiUrl}/api/library/tree`)).json();
    const destination = tree.find(
      (node: { name: string }) => node.name === destinationFolder,
    );
    const source = tree.find(
      (node: { name: string }) => node.name === sourceFolder,
    );
    expect(destination?.children[0]?.name).toBe(movedChild);
    expect(source?.node_id).toBeTruthy();
    createdFolderIds = [
      destination.children[0].node_id,
      source.node_id,
      destination.node_id,
    ];

    await page.getByLabel("PDF file").setInputFiles(pdfPath);
    await page.getByRole("button", { name: "Upload here" }).click();
    await expect(page.getByText(/Upload accepted at/i)).toBeVisible();
    const details = page.getByRole("region", { name: pdfName });
    await expect(details.getByText("ready", { exact: true })).toBeVisible({
      timeout: 15 * 60 * 1_000,
    });

    await page.getByLabel("PDF file").setInputFiles(pdfPath);
    await page.getByRole("button", { name: "Upload here" }).click();
    await expect(page.getByText(/canonical location was reused/i)).toBeVisible();

    const previewLink = details.getByRole("link", {
      name: "Open in new tab",
    });
    await expect(previewLink).toHaveAttribute("href", /#page=1$/);
    const documentUrl = page.url();
    createdDocumentId = new URL(documentUrl).searchParams.get("document");
    expect(createdDocumentId).not.toBeNull();
    await page.reload();
    await expect(page.getByRole("region", { name: pdfName })).toBeVisible();
    await expect(page).toHaveURL(documentUrl);

    await page.getByRole("link", { name: "Chat", exact: true }).click();
    await page.getByRole("link", { name: "New chat", exact: true }).click();
    await page.getByLabel("Question").fill("Start a conversation");
    await page.getByRole("button", { name: "Send" }).click();
    await expect(page).toHaveURL(/[?&]chat=[0-9a-f-]{36}/);
    const chatUrl = page.url();
    createdChatId = new URL(chatUrl).searchParams.get("chat");
    expect(createdChatId).not.toBeNull();
    await expect(page.getByText("Verified").first()).toBeVisible({
      timeout: 10 * 60 * 1_000,
    });

    await page.getByRole("button", { name: /Scope:/ }).click();
    const scope = dialog(page, "Conversation scope");
    await scope
      .getByRole("radio", { name: "Selected folders and files" })
      .check();
    await scope
      .getByRole("button", { name: `Expand ${destinationFolder}` })
      .click();
    await scope.getByLabel(`Include folder ${movedChild}`).check();
    await scope.getByRole("button", { name: "Save scope" }).click();
    await expect(
      page.getByRole("button", { name: "Scope: 1 selected" }),
    ).toBeVisible();

    await page.getByLabel("Question").fill(question);
    await page.getByRole("button", { name: "Send" }).click();
    const verified = page.getByText("Verified").last();
    await expect(verified).toBeVisible({
      timeout: 10 * 60 * 1_000,
    });

    const citation = page.getByRole("link", {
      name: new RegExp(`^Open ${pdfName.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}, page`),
    });
    await expect(citation).toBeVisible();
    await page.reload();
    await expect(page.getByText("Verified").last()).toBeVisible();
    await expect(citation).toBeVisible();

    await citation.click();
    await expect(page).toHaveURL(/\/knowledge-base\?.*document=.*page=/);
    await expect(page.getByRole("region", { name: pdfName })).toBeVisible();

    await page.goto(documentUrl);
    const liveDetails = page.getByRole("region", { name: pdfName });
    await liveDetails.getByRole("button", { name: "Delete" }).click();
    await dialog(page, "Delete document?")
      .getByRole("button", { name: "Delete document", exact: true })
      .click();
    await expect(
      page.getByRole("region", { name: "Document details" }),
    ).toContainText("Select a PDF");

    await page.goto(chatUrl);
    await page.reload();
    await expect(page.getByText("[ SOURCE UNAVAILABLE ]")).toBeVisible();

    await page.goto(documentUrl.split("&document=")[0]);
    await deleteCurrentFolder(page);
    await page.goto("/knowledge-base");
    await selectFolder(page, sourceFolder);
    await deleteCurrentFolder(page);
    await page.goto("/knowledge-base");
    await selectFolder(page, destinationFolder);
    await deleteCurrentFolder(page);
  });
});
