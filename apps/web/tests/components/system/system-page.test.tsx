import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  SidebarContentOutlet,
  SidebarContentProvider,
} from "@/components/shell/protected-shell";

import {
  cleanupGeneration,
  downloadDiagnostics,
  getDiagnosticsPreview,
  getConfigurationChanges,
  getPersonalBackupStatus,
  getPersonalBackupHistory,
  getSystemCapabilities,
  getSystemConfiguration,
  getSystemOperations,
  getReprocessingOperations,
  getVersionInventory,
  applyConfiguration,
  previewConfiguration,
  previewReprocessing,
  reauthenticateConfiguration,
  reauthenticateReprocessing,
  runProfileValidation,
  startPersonalBackup,
  startReprocessing,
} from "@/features/system/api";
import { SystemPage } from "@/features/system/system-page";

vi.mock("next/navigation", () => ({ usePathname: () => "/system/models" }));
vi.mock("@/features/system/api", () => ({
  downloadDiagnostics: vi.fn(),
  getDiagnosticsPreview: vi.fn(),
  getConfigurationChanges: vi.fn(),
  getPersonalBackupStatus: vi.fn(),
  getPersonalBackupHistory: vi.fn(),
  previewConfiguration: vi.fn(),
  reauthenticateConfiguration: vi.fn(),
  applyConfiguration: vi.fn(),
  getSystemCapabilities: vi.fn(),
  getSystemConfiguration: vi.fn(),
  getSystemOperations: vi.fn(),
  getSystemOverview: vi.fn(),
  runProfileValidation: vi.fn(),
  startPersonalBackup: vi.fn(),
  getVersionInventory: vi.fn(),
  getReprocessingOperations: vi.fn(),
  selectIngestionProfile: vi.fn(),
  previewReprocessing: vi.fn(),
  reauthenticateReprocessing: vi.fn(),
  startReprocessing: vi.fn(),
  controlReprocessing: vi.fn(),
  rollbackEmbeddingGeneration: vi.fn(),
  cleanupGeneration: vi.fn(),
}));

const configuration = {
  effective_revision: "abcd",
  desired_revision: "abcd",
  state: "effective" as const,
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
  ocr_mode: "auto" as const,
  ocr_preset_id: "balanced",
  impact_digest: null,
  operation_class: null,
  prior_revision: null,
  proposed_by: null,
  proposed_at: null,
  reason_code: null,
  backup_verified: false,
  backup_verified_at: null,
};

const profiles = [
  {
    profile_id: configuration.generation_profile_id,
    profile_revision: 1,
    function: "generation" as const,
    release_support_class: "release_qualified" as const,
    local_validation_state: "installed" as const,
    engine: "ollama",
    model_identity: "qwen3:8b",
    accelerator_vendor: "auto",
    minimum_ram_gib: 16,
    minimum_vram_gib: 0,
    impact_class: "medium",
    effective: true,
    selectable: false,
    reason: "Installed, but not qualified here. Run the fixed local validation.",
    evidence: null,
  },
  {
    profile_id: configuration.ocr_profile_id,
    profile_revision: 1,
    function: "ocr" as const,
    release_support_class: "release_qualified" as const,
    local_validation_state: "failed" as const,
    engine: "paddleocr-vl-1.6",
    model_identity: "PaddleOCR-VL-1.6",
    accelerator_vendor: "cpu",
    minimum_ram_gib: 16,
    minimum_vram_gib: 0,
    impact_class: "medium",
    effective: true,
    selectable: false,
    reason: "The last fixed local validation failed. Check services, then retry.",
    evidence: {
      state: "failed" as const,
      reason_code: "ocr_fixture_mismatch",
      fixture_id: "ocr.system-scan-v1",
      evidence_at: "2026-08-02T00:00:00Z",
      metrics: {},
    },
  },
];

const versionInventory = {
  ingestion: {
    revision_id: "v8e-ingestion-initial",
    parser_profile_id: "parser.paddleocr-vl-1.6.adaptive-v2",
    parser_version: "pypdf+paddleocr-vl-v1.6-adaptive-v2",
    chunking_version: "fragment-paragraph-sentence-v2",
    document_versions: [],
    generations: [],
  },
  embedding: {
    active_generation_id: "5153f71b-a633-4a4a-9b9c-7e47c80e24fc",
    profile_id: configuration.embedding_profile_id,
    embedding_version: "qwen3-embedding-0.6b-1024",
    dimension: 1024,
    generations: [
      {
        generation_id: "5153f71b-a633-4a4a-9b9c-7e47c80e24fc",
        profile_id: configuration.embedding_profile_id,
        embedding_version: "qwen3-embedding-0.6b-1024",
        dimension: 1024,
        state: "active" as const,
        chunk_count: 0,
        created_at: "2026-08-02T00:00:00Z",
        activated_at: "2026-08-02T00:00:00Z",
        retired_at: null,
        cleanup_available: false,
      },
    ],
  },
};

describe("System workspace", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(getSystemConfiguration).mockResolvedValue(configuration);
    vi.mocked(getSystemCapabilities).mockResolvedValue({
      catalog_id: "local-rag-v8a-baseline",
      catalog_revision: 1,
      profiles,
      observed_processor: "Ollama reports accelerator memory in use",
      logical_cpu_count: 16,
      system_memory_bytes: 32 * 1024 ** 3,
      maximum_ocr_processes: 2,
    });
    vi.mocked(getSystemOperations).mockResolvedValue({ operations: [] });
    vi.mocked(getConfigurationChanges).mockResolvedValue({ changes: [] });
    vi.mocked(getPersonalBackupStatus).mockResolvedValue({ operation: null });
    vi.mocked(getPersonalBackupHistory).mockResolvedValue({
      retention_mode: "keep_all",
      automatic_deletion: false,
      destination_mode: "attended_folder_picker",
      operations: [],
    });
    vi.mocked(getVersionInventory).mockResolvedValue(versionInventory);
    vi.mocked(getReprocessingOperations).mockResolvedValue({ operations: [] });
    vi.mocked(startPersonalBackup).mockResolvedValue({
      operation: {
        backup_run_id: "b53c3cb4-d765-454c-a46f-b733ec69f452",
        state: "pending",
        stage: "queued",
        reason_code: null,
        created_at: "2026-08-02T00:00:00Z",
        finished_at: null,
        restore_verified: false,
        manifest_sha256: null,
      },
    });
    vi.mocked(runProfileValidation).mockResolvedValue({
      operation: {
        operation_id: "b53c3cb4-d765-454c-a46f-b733ec69f452",
        operation_type: "profile_validation",
        profile_id: configuration.generation_profile_id,
        state: "effective",
        stage: "effective",
        reason_code: "generation_fixture_passed",
        metrics: {},
        created_at: "2026-08-02T00:00:00Z",
        finished_at: "2026-08-02T00:00:01Z",
      },
    });
    vi.mocked(previewConfiguration).mockResolvedValue({
      preview_id: "5153f71b-a633-4a4a-9b9c-7e47c80e24fc",
      impact_digest: "a".repeat(64),
      expires_at: "2026-08-02T00:05:00Z",
      operation_class: "restart_scoped",
      affected_services: ["ocr"],
      waits_for: ["active_ocr_boundary"],
      expected_interruption: "OCR restarts after active work reaches a safe boundary.",
      backup_required: false,
    });
  });

  it("shows System sections in the workspace sidebar", async () => {
    render(
      <SidebarContentProvider>
        <SystemPage section="models" />
        <SidebarContentOutlet />
      </SidebarContentProvider>,
    );

    const navigation = await screen.findByRole("navigation", {
      name: "System workspace navigation",
    });
    const links = within(navigation).getAllByRole("link");

    expect(links.map((link) => link.getAttribute("href"))).toEqual([
      "/system/overview",
      "/system/models",
      "/system/ocr",
      "/system/maintenance",
    ]);
    expect(within(navigation).getByRole("link", { name: "AI Models" }))
      .toHaveAttribute("aria-current", "page");
  });

  it.each([
    [
      "models" as const,
      /models that generate answers, create embeddings, and rerank search results/i,
    ],
    ["ocr" as const, /reads scanned and image-based PDF pages/i],
    [
      "maintenance" as const,
      /create restore-verified backups, choose processing for future uploads/i,
    ],
  ])("explains the purpose of the %s tab", (section, description) => {
    render(<SystemPage section={section} />);

    expect(screen.getByText(description)).toBeInTheDocument();
  });

  it("keeps release support separate from validation on this computer", async () => {
    const user = userEvent.setup();
    render(<SystemPage section="models" />);

    expect(
      await screen.findByRole("heading", { name: "qwen3:8b" }),
    ).toBeInTheDocument();
    expect(screen.getByText("Release qualified")).toBeInTheDocument();
    expect(screen.getByText("Installed")).toBeInTheDocument();
    const effective = screen.getByText("Effective").closest(".trust-status");
    expect(effective).toHaveClass("trust-status", "trust-status--verified");
    expect(effective?.querySelector("svg")).not.toBeNull();
    await user.click(screen.getByRole("button", { name: "Run local validation" }));
    await waitFor(() =>
      expect(runProfileValidation).toHaveBeenCalledWith(
        configuration.generation_profile_id,
        false,
      ),
    );
  });

  it("keeps the current reranker profile visible even when it is not selectable", async () => {
    vi.mocked(getSystemCapabilities).mockResolvedValue({
      catalog_id: "local-rag-v8a-baseline",
      catalog_revision: 1,
      profiles: [
        ...profiles,
        {
          profile_id: configuration.reranker_profile_id,
          profile_revision: 1,
          function: "reranking" as const,
          release_support_class: "release_qualified" as const,
          local_validation_state: "installed" as const,
          engine: "sentence-transformers",
          model_identity: configuration.reranker_model,
          accelerator_vendor: "none",
          minimum_ram_gib: 8,
          minimum_vram_gib: 0,
          impact_class: "low",
          effective: false,
          selectable: false,
          reason: "Installed, but not qualified here. Run the fixed local validation.",
          evidence: null,
        },
      ],
      observed_processor: "Ollama reports accelerator memory in use",
      logical_cpu_count: 16,
      system_memory_bytes: 32 * 1024 ** 3,
      maximum_ocr_processes: 2,
    });

    render(<SystemPage section="models" />);

    const reranking = await screen.findByLabelText("Result reranking");
    expect(reranking).toHaveValue(configuration.reranker_profile_id);
    expect(within(reranking).getByRole("option", { name: configuration.reranker_model }))
      .toBeInTheDocument();
  });

  it("shows a disabled diagnostic option when the current profile is unavailable", async () => {
    render(<SystemPage section="models" />);

    const reranking = await screen.findByLabelText("Result reranking");
    const unavailable = within(reranking).getByRole("option", {
      name: `Current profile unavailable (${configuration.reranker_profile_id})`,
    });

    expect(unavailable).toBeDisabled();
    expect(reranking).toHaveValue(configuration.reranker_profile_id);
  });

  it("excludes unvalidated non-current alternatives from model selectors", async () => {
    const alternativeGenerationId = "generation.qwen3-14b.ollama.windows-x64";
    vi.mocked(getSystemCapabilities).mockResolvedValue({
      catalog_id: "local-rag-v8a-baseline",
      catalog_revision: 1,
      profiles: [
        ...profiles,
        {
          profile_id: alternativeGenerationId,
          profile_revision: 1,
          function: "generation" as const,
          release_support_class: "release_qualified" as const,
          local_validation_state: "installed" as const,
          engine: "ollama",
          model_identity: "qwen3:14b",
          accelerator_vendor: "auto",
          minimum_ram_gib: 16,
          minimum_vram_gib: 0,
          impact_class: "medium",
          effective: false,
          selectable: false,
          reason: "Installed, but not qualified here. Run the fixed local validation.",
          evidence: null,
        },
      ],
      observed_processor: "Ollama reports accelerator memory in use",
      logical_cpu_count: 16,
      system_memory_bytes: 32 * 1024 ** 3,
      maximum_ocr_processes: 2,
    });

    render(<SystemPage section="models" />);

    const generation = await screen.findByLabelText("Answer generation");
    expect(within(generation).getByRole("option", { name: "qwen3:8b" })).toBeInTheDocument();
    expect(within(generation).queryByRole("option", { name: "qwen3:14b" })).toBeNull();
  });

  it("shows exact CPU OCR settings, failure recovery, and fixed checks", async () => {
    render(<SystemPage section="ocr" />);
    expect(await screen.findByText("PaddleOCR-VL-1.6")).toBeInTheDocument();
    expect(screen.getByText("CPU")).toBeInTheDocument();
    expect(screen.getByText("10")).toBeInTheDocument();
    expect(screen.getByText("Parallel OCR processes")).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: /GPU inference/i })).toBeDisabled();
    expect(screen.getByText(/last fixed local validation failed/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Run OCR benchmark" })).toBeEnabled();
  });

  it("applies a restart-only OCR change without requiring a backup", async () => {
    const user = userEvent.setup();
    vi.mocked(reauthenticateConfiguration).mockResolvedValue({
      grant_token: "g".repeat(43),
      expires_at: "2026-08-02T00:05:00Z",
    });
    vi.mocked(applyConfiguration).mockResolvedValue({
      change: {
        change_id: "5feb75a5-49c0-4f02-ac49-e30a8a6fa756",
        actor_user_id: "7c6bd190-246f-4c13-854e-2c37465a029d",
        prior_revision: "abcd",
        desired_revision: "efgh",
        impact_digest: "a".repeat(64),
        operation_class: "restart_scoped",
        state: "pending",
        stage: "queued",
        reason_code: null,
        created_at: "2026-08-02T00:00:00Z",
        finished_at: null,
      },
    });
    render(<SystemPage section="ocr" />);
    await user.click(await screen.findByRole("radio", { name: /CPU inference/i }));
    const threads = screen.getByRole("spinbutton", { name: /CPU threads/i });
    const processes = screen.getByRole("spinbutton", { name: /Parallel OCR processes/i });
    expect(threads).toHaveValue(10);
    expect(processes).toHaveValue(1);
    await user.clear(threads);
    await user.type(threads, "8");
    await user.clear(processes);
    await user.type(processes, "2");
    await user.click(screen.getByRole("button", { name: "Review change" }));
    expect(previewConfiguration).toHaveBeenCalledWith(expect.objectContaining({
      ocr_cpu_threads: 8,
      ocr_process_count: 2,
    }));
    expect(await screen.findByText(/does not rewrite your documents or search index/i)).toBeInTheDocument();
    expect(screen.getByText("Active ocr boundary")).toBeInTheDocument();
    const apply = screen.getByRole("button", { name: "Apply change" });
    expect(apply).toBeDisabled();
    await user.type(
      screen.getByLabelText("Confirm with your admin password"),
      "correct horse",
    );
    expect(apply).toBeEnabled();
    await user.click(apply);
    expect(reauthenticateConfiguration).toHaveBeenCalledWith(
      "5153f71b-a633-4a4a-9b9c-7e47c80e24fc",
      "a".repeat(64),
      "correct horse",
    );
    expect(applyConfiguration).toHaveBeenCalledWith(
      "5153f71b-a633-4a4a-9b9c-7e47c80e24fc",
      "a".repeat(64),
      "g".repeat(43),
    );
  });

  it("previews exclusions before exporting sanitized diagnostics", async () => {
    const user = userEvent.setup();
    vi.mocked(getDiagnosticsPreview).mockResolvedValue({
      privacy_mode: true,
      files: ["versions.json", "manifest.json"],
      exclusions: ["document text and filenames", "setup codes and session tokens"],
    });
    render(<SystemPage section="maintenance" />);
    expect(await screen.findByText("document text and filenames")).toBeInTheDocument();
    await user.click(
      screen.getByRole("button", { name: "Download sanitized diagnostics" }),
    );
    expect(downloadDiagnostics).toHaveBeenCalledOnce();
  });

  it("starts an attended coordinated backup without accepting a browser path", async () => {
    const user = userEvent.setup();
    vi.mocked(getDiagnosticsPreview).mockResolvedValue({
      privacy_mode: true,
      files: ["versions.json", "manifest.json"],
      exclusions: ["document text and filenames"],
    });
    render(<SystemPage section="maintenance" />);
    const attention = (await screen.findByText("Attention")).closest(".trust-status");
    expect(attention).toHaveClass("trust-status", "trust-status--warning");
    expect(attention?.querySelector("svg")).not.toBeNull();
    const backupButton = screen.getByRole("button", { name: "Create and verify backup" });
    expect(backupButton.parentElement).toHaveClass("system-actions");
    expect(screen.getByText(/A folder picker opens on this computer/i))
      .toHaveClass("system-gate-note");
    await user.click(backupButton);
    expect(startPersonalBackup).toHaveBeenCalledWith();
    expect(await screen.findByText("Queued")).toBeInTheDocument();
    expect(screen.getByText(/folder picker opens on this computer/i)).toBeInTheDocument();
  });

  it("keeps future-upload selection separate from rebuilding existing documents", async () => {
    vi.mocked(getDiagnosticsPreview).mockResolvedValue({
      privacy_mode: true,
      files: ["versions.json"],
      exclusions: ["document text and filenames"],
    });
    render(<SystemPage section="maintenance" />);

    expect(
      await screen.findByRole("heading", {
        name: "Document processing for new uploads",
      }),
    ).toBeInTheDocument();
    expect(screen.getByText(/applies only to documents uploaded after/i)).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Rebuild existing documents" })).toBeInTheDocument();
    expect(screen.getByText(/current search remains available/i)).toBeInTheDocument();
  });

  it("shows and deletes only the exact removable document-processing copy", async () => {
    const user = userEvent.setup();
    const failedGenerationId = "2de730bf-4930-4238-a515-0449ef255f62";
    vi.mocked(getVersionInventory).mockResolvedValue({
      ...versionInventory,
      ingestion: {
        ...versionInventory.ingestion,
        generations: [{
          generation_id: failedGenerationId,
          document_id: "8151552e-5bf6-42a1-9b3d-f6feef4785dd",
          filename: "employee-handbook.pdf",
          parser_version: "pypdf+paddleocr-vl-v1.6-legacy-v1",
          chunking_version: "fragment-paragraph-v1",
          state: "failed" as const,
          chunk_count: 0,
          created_at: "2026-08-02T00:00:00Z",
          retired_at: null,
          cleanup_available: true,
        }],
      },
    });
    vi.mocked(cleanupGeneration).mockResolvedValue({ succeeded: true });
    render(<SystemPage section="maintenance" />);

    expect(await screen.findByText("employee-handbook.pdf")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Delete this copy" }));

    await waitFor(() => {
      expect(cleanupGeneration).toHaveBeenCalledWith("document", failedGenerationId);
    });
  });

  it("previews and password-confirms a shadow search-index rebuild", async () => {
    const user = userEvent.setup();
    vi.mocked(getSystemConfiguration).mockResolvedValue({
      ...configuration,
      backup_verified: true,
      backup_verified_at: "2026-08-02T00:00:00Z",
    });
    vi.mocked(getDiagnosticsPreview).mockResolvedValue({
      privacy_mode: true,
      files: ["versions.json"],
      exclusions: ["document text and filenames"],
    });
    vi.mocked(previewReprocessing).mockResolvedValue({
      preview_id: "79535367-35f1-44f9-a4b2-8053f01c4c8e",
      impact_digest: "f".repeat(64),
      expires_at: "2026-08-02T00:05:00Z",
      document_count: 3,
      chunk_count: 24,
      estimated_bytes: 98_304,
      backup_verified: true,
      backup_required: true,
    });
    vi.mocked(reauthenticateReprocessing).mockResolvedValue({
      grant_token: "g".repeat(43),
      expires_at: "2026-08-02T00:05:00Z",
    });
    vi.mocked(startReprocessing).mockResolvedValue({
      operation: {
        operation_id: "5feb75a5-49c0-4f02-ac49-e30a8a6fa756",
        operation_type: "reindex",
        state: "running",
        stage: "processing",
        target_profile_id: configuration.embedding_profile_id,
        source_parser_version: null,
        target_parser_version: null,
        target_embedding_version: "qwen3-embedding-0.6b-1024",
        target_dimension: 1024,
        impact_digest: "f".repeat(64),
        total_documents: 3,
        completed_documents: 0,
        failed_documents: 0,
        total_chunks: 24,
        completed_chunks: 0,
        reason_code: null,
        qualification: {},
        operation_generation_id: "7c6bd190-246f-4c13-854e-2c37465a029d",
        created_at: "2026-08-02T00:00:00Z",
        finished_at: null,
      },
    });
    render(<SystemPage section="maintenance" />);

    await user.click(await screen.findByRole("button", { name: "Review rebuild" }));
    expect(await screen.findByText(/3 documents and 24 chunks match/i)).toBeInTheDocument();
    await user.type(screen.getByLabelText("Confirm with your admin password"), "correct horse");
    await user.click(screen.getByRole("button", { name: "Start rebuild" }));

    await waitFor(() => expect(startReprocessing).toHaveBeenCalledOnce());
    expect(reauthenticateReprocessing).toHaveBeenCalledWith(
      "79535367-35f1-44f9-a4b2-8053f01c4c8e",
      "f".repeat(64),
      "correct horse",
    );
  });

  it("uses the shared icon-and-label treatment for failed System states", async () => {
    vi.mocked(getDiagnosticsPreview).mockResolvedValue({
      privacy_mode: true,
      files: ["versions.json"],
      exclusions: ["document text and filenames"],
    });
    vi.mocked(getSystemOperations).mockResolvedValue({
      operations: [{
        operation_id: "b53c3cb4-d765-454c-a46f-b733ec69f452",
        operation_type: "profile_validation",
        profile_id: configuration.generation_profile_id,
        state: "failed",
        stage: "failed",
        reason_code: "generation_fixture_failed",
        metrics: {},
        created_at: "2026-08-02T00:00:00Z",
        finished_at: "2026-08-02T00:00:01Z",
      }],
    });

    const { container } = render(<SystemPage section="maintenance" />);
    const failed = (await screen.findByText("Failed", { selector: ".trust-status span" }))
      .closest(".trust-status");

    expect(failed).toHaveClass("trust-status", "trust-status--danger");
    expect(failed?.querySelector("svg")).not.toBeNull();
    expect(container.querySelector(".system-status")).toBeNull();
  });
});
