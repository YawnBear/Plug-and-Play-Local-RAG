import { afterEach, describe, expect, it, vi } from "vitest";

import { previewConfiguration } from "@/features/system/api";

afterEach(() => vi.unstubAllGlobals());

describe("System API", () => {
  it("sends runtime configuration previews as JSON objects", async () => {
    const selection = {
      base_revision: "v8d-baseline-0001",
      generation_profile_id: "generation.qwen3-8b.ollama.windows-x64",
      reranker_profile_id: "reranking.bge-v2-m3.cpu.windows-x64",
      ocr_mode: "explicit" as const,
      ocr_profile_id: "ocr.paddleocr-vl-1.6.cpu.windows-x64",
      ocr_cpu_threads: 10,
      ocr_process_count: 2,
    };
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          preview_id: "5153f71b-a633-4a4a-9b9c-7e47c80e24fc",
          impact_digest: "a".repeat(64),
          expires_at: "2026-08-04T04:00:00Z",
          operation_class: "restart_scoped",
          affected_services: ["ocr"],
          waits_for: ["active_ocr_boundary"],
          expected_interruption: "OCR restarts after active work reaches a safe boundary.",
          backup_required: false,
        }),
        { status: 200 },
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    await expect(previewConfiguration(selection)).resolves.toMatchObject({
      affected_services: ["ocr"],
    });

    const init = fetchMock.mock.calls[0][1] as RequestInit;
    expect(new Headers(init.headers).get("Content-Type")).toBe("application/json");
    expect(init.body).toBe(JSON.stringify(selection));
  });
});
