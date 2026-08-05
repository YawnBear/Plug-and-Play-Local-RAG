import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

import { API_ROUTE, fulfillAuthMeIfRequested, fulfillJson } from "./mock-api";

async function mockSystemApi(page: Page) {
  await page.route(API_ROUTE, async (route) => {
    if (await fulfillAuthMeIfRequested(route)) return;
    const path = new URL(route.request().url()).pathname;
    if (path === "/api/admin/system/overview") {
      await fulfillJson(route, {
        product_profile: "personal",
        overall_state: "attention",
        recommended_action: "Start RustFS and verify the originals bucket.",
        services: [
          { service_id: "api", label: "API", state: "ready", reason_code: "api_ready", message: "Ready" },
          { service_id: "storage", label: "Document storage", state: "unavailable", reason_code: "storage_unavailable", message: "Start RustFS and verify the originals bucket." },
        ],
        documents: { ready: 2, processing: 1, failed: 0 },
        jobs: { active: 1, queued: 0 },
        disk: { total_bytes: 1000000000, free_bytes: 500000000 },
        operation_count: 0,
      });
      return;
    }
    if (path === "/api/admin/system/configuration") {
      await fulfillJson(route, {
        effective_revision: "abcd",
        desired_revision: "abcd",
        state: "effective",
        generation_profile_id: "generation.qwen3-8b.ollama.windows-x64",
        generation_model: "qwen3:8b",
        embedding_profile_id: "embedding.qwen3-0.6b-1024.ollama.windows-x64",
        embedding_model: "qwen3-embedding:0.6b",
        reranker_profile_id: "reranking.bge-v2-m3.cpu.windows-x64",
        reranker_model: "BAAI/bge-reranker-v2-m3",
        parser_identity: "paddleocr-vl-v1.6-adaptive-v2",
        ocr_profile_id: "ocr.paddleocr-vl-1.6.cpu.windows-x64",
        ocr_device: "cpu",
        ocr_engine: "paddleocr-vl-1.6",
        ocr_cpu_threads: 10,
        ocr_process_count: 1,
        ocr_page_batch_size: 8,
        maximum_generation_context: 16384,
        maximum_generation_output: 3072,
        ocr_mode: "auto",
        ocr_preset_id: "balanced",
        impact_digest: null,
        operation_class: null,
        prior_revision: null,
        proposed_by: null,
        proposed_at: null,
        reason_code: null,
        backup_verified: false,
        backup_verified_at: null,
      });
      return;
    }
    if (path === "/api/admin/system/configuration/changes") {
      await fulfillJson(route, { changes: [] });
      return;
    }
    if (path === "/api/admin/system/capabilities") {
      await fulfillJson(route, {
        catalog_id: "local-rag-v8a-baseline",
        catalog_revision: 1,
        profiles: [{
          profile_id: "ocr.paddleocr-vl-1.6.cpu.windows-x64",
          profile_revision: 1,
          function: "ocr",
          release_support_class: "release_qualified",
          local_validation_state: "locally_validated",
          engine: "paddleocr-vl-1.6",
          model_identity: "PaddleOCR-VL-1.6",
          accelerator_vendor: "cpu",
          minimum_ram_gib: 16,
          minimum_vram_gib: 0,
          impact_class: "medium",
          effective: true,
          selectable: true,
          reason: "Qualified and validated on this computer.",
          evidence: {
            state: "locally_validated",
            reason_code: "ocr_fixture_passed",
            fixture_id: "ocr.system-scan-v1",
            evidence_at: "2026-08-02T00:00:00Z",
            metrics: {},
          },
        }],
        observed_processor: "CPU",
        logical_cpu_count: 16,
        system_memory_bytes: 34359738368,
        maximum_ocr_processes: 2,
      });
      return;
    }
    if (path === "/api/admin/system/operations") {
      await fulfillJson(route, { operations: [] });
      return;
    }
    if (path === "/api/admin/system/configuration/preview") {
      await fulfillJson(route, {
        preview_id: "5153f71b-a633-4a4a-9b9c-7e47c80e24fc",
        impact_digest: "a".repeat(64),
        expires_at: "2026-08-02T00:05:00Z",
        operation_class: "restart_scoped",
        affected_services: ["ocr"],
        waits_for: ["active_ocr_boundary"],
        expected_interruption: "OCR restarts after active work reaches a safe boundary.",
        backup_required: false,
      });
      return;
    }
    await fulfillJson(route, { detail: "Unexpected System request" }, 500);
  });
}

test("System overview stays actionable and accessible at 375px", async ({ page }) => {
  await mockSystemApi(page);
  await page.setViewportSize({ width: 375, height: 812 });
  await page.goto("/system/overview");

  await expect(page.getByRole("heading", { name: "System overview" })).toBeVisible();
  await expect(page.getByText("Start RustFS and verify the originals bucket.").first()).toBeVisible();
  await expect(page.getByText("qwen3:8b")).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= document.documentElement.clientWidth,
    ),
  ).toBe(true);
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
});

test("OCR restart review explains rollback accessibly at 375px", async ({ page }) => {
  await mockSystemApi(page);
  await page.setViewportSize({ width: 375, height: 812 });
  await page.goto("/system/ocr");

  await page.getByRole("radio", { name: /CPU inference/i }).check();
  await page.getByRole("button", { name: "Review change" }).click();
  await expect(page.getByText(/does not rewrite your documents or search index/i)).toBeVisible();
  await expect(page.getByLabel("Confirm with your admin password")).toBeVisible();
  await expect(page.getByRole("button", { name: "Apply change" })).toBeDisabled();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= document.documentElement.clientWidth,
    ),
  ).toBe(true);
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
});
