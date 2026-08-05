"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { useWorkspaceSectionNavigation } from "@/components/shell/workspace-section-nav";
import { TrustStatus, type TrustStatusTone } from "@/components/ui/trust-status";
import { ApiError } from "@/lib/http";

import {
  applyConfiguration,
  downloadDiagnostics,
  getConfigurationChanges,
  getDiagnosticsPreview,
  getSystemCapabilities,
  getSystemConfiguration,
  getSystemOperations,
  getSystemOverview,
  getPersonalBackupStatus,
  getPersonalBackupHistory,
  previewConfiguration,
  reauthenticateConfiguration,
  runProfileValidation,
  startPersonalBackup,
  cleanupGeneration,
  controlReprocessing,
  getReprocessingOperations,
  getVersionInventory,
  previewReprocessing,
  reauthenticateReprocessing,
  rollbackEmbeddingGeneration,
  selectIngestionProfile,
  startReprocessing,
} from "./api";
import type {
  Capabilities,
  Capability,
  Configuration,
  ConfigurationChange,
  ConfigurationPreview,
  ConfigurationSelection,
  DiagnosticsPreview,
  Operation,
  Overview,
  PersonalBackupOperation,
  ReprocessingOperation,
  ReprocessingPreview,
  VersionInventory,
} from "./contracts";

export type SystemSection = "overview" | "models" | "ocr" | "maintenance";

const routes = [
  ["/system/overview", "Overview"],
  ["/system/models", "AI Models"],
  ["/system/ocr", "OCR"],
  ["/system/maintenance", "Maintenance"],
] as const;

const statusTones: Record<string, TrustStatusTone> = {
  abandoned: "neutral",
  active: "verified",
  applying: "pending",
  attention: "warning",
  building: "pending",
  cancelled: "danger",
  degraded: "warning",
  effective: "verified",
  failed: "danger",
  paused: "warning",
  pending: "pending",
  qualified: "verified",
  qualifying: "pending",
  ready: "verified",
  retained: "neutral",
  rolled_back: "neutral",
  running: "pending",
  succeeded: "verified",
  unavailable: "danger",
  unknown: "warning",
};

const content: Record<SystemSection, [string, string]> = {
  overview: ["System overview", "Health and the next useful action, without exposing private data."],
  models: [
    "AI Models",
    "Review the models that generate answers, create embeddings, and rerank search results. Validate supported profiles on this computer and safely change the active generation or reranking profile.",
  ],
  ocr: [
    "OCR",
    "Review how Local RAG reads scanned and image-based PDF pages. Validate the installed runtime, benchmark it locally, or manually tune its validated CPU or GPU execution profile.",
  ],
  maintenance: [
    "Maintenance",
    "Protect and repair the local library: create restore-verified backups, choose processing for future uploads, rebuild existing documents or the search index, and download privacy-safe diagnostics.",
  ],
};

function errorMessage(error: unknown): string {
  return error instanceof ApiError || error instanceof Error
    ? error.message
    : "System information is unavailable.";
}

export function SystemPage({ section }: { section: SystemSection }) {
  const pathname = usePathname();
  useWorkspaceSectionNavigation("System workspace navigation", routes);

  const [overview, setOverview] = useState<Overview | null>(null);
  const [capabilities, setCapabilities] = useState<Capabilities | null>(null);
  const [configuration, setConfiguration] = useState<Configuration | null>(null);
  const [operations, setOperations] = useState<Operation[]>([]);
  const [changes, setChanges] = useState<ConfigurationChange[]>([]);
  const [preview, setPreview] = useState<DiagnosticsPreview | null>(null);
  const [backup, setBackup] = useState<PersonalBackupOperation | null>(null);
  const [backupHistory, setBackupHistory] = useState<PersonalBackupOperation[]>([]);
  const [versions, setVersions] = useState<VersionInventory | null>(null);
  const [reprocessing, setReprocessing] = useState<ReprocessingOperation[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const load = useCallback(async (signal?: AbortSignal) => {
    setError(null);
    try {
      if (section === "overview") {
        const [nextOverview, nextConfiguration, nextChanges] = await Promise.all([
          getSystemOverview(signal),
          getSystemConfiguration(signal),
          getConfigurationChanges(signal),
        ]);
        setOverview(nextOverview);
        setConfiguration(nextConfiguration);
        setChanges(nextChanges.changes);
      } else if (section === "models" || section === "ocr") {
        const [nextCapabilities, nextConfiguration, nextOperations, nextChanges] = await Promise.all([
          getSystemCapabilities(signal),
          getSystemConfiguration(signal),
          getSystemOperations(signal),
          getConfigurationChanges(signal),
        ]);
        setCapabilities(nextCapabilities);
        setConfiguration(nextConfiguration);
        setOperations(nextOperations.operations);
        setChanges(nextChanges.changes);
      } else {
        const [
          nextPreview,
          nextOperations,
          nextConfiguration,
          nextChanges,
          nextBackup,
          nextBackupHistory,
          nextVersions,
          nextReprocessing,
        ] = await Promise.all([
          getDiagnosticsPreview(signal),
          getSystemOperations(signal),
          getSystemConfiguration(signal),
          getConfigurationChanges(signal),
          getPersonalBackupStatus(signal),
          getPersonalBackupHistory(signal),
          getVersionInventory(signal),
          getReprocessingOperations(signal),
        ]);
        setPreview(nextPreview);
        setOperations(nextOperations.operations);
        setConfiguration(nextConfiguration);
        setChanges(nextChanges.changes);
        setBackup(nextBackup.operation);
        setBackupHistory(nextBackupHistory.operations);
        setVersions(nextVersions);
        setReprocessing(nextReprocessing.operations);
      }
    } catch (nextError) {
      if (!signal?.aborted) setError(errorMessage(nextError));
    }
  }, [section]);

  useEffect(() => {
    const controller = new AbortController();
    const timer = window.setTimeout(() => void load(controller.signal), 0);
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [load]);

  useEffect(() => {
    const reprocessingActive = reprocessing.some((item) =>
      ["running", "qualifying"].includes(item.state),
    );
    const backupActive = backup && ["pending", "running"].includes(backup.state);
    if (section !== "maintenance" || (!backupActive && !reprocessingActive)) return;
    const timer = window.setInterval(() => void load(), 2000);
    return () => window.clearInterval(timer);
  }, [backup, load, reprocessing, section]);

  async function run(profile: Capability, benchmark = false) {
    setBusy(profile.profile_id);
    setError(null);
    try {
      await runProfileValidation(profile.profile_id, benchmark);
      await load();
    } catch (nextError) {
      setError(errorMessage(nextError));
    } finally {
      setBusy(null);
    }
  }

  async function exportDiagnostics() {
    setBusy("diagnostics");
    setError(null);
    try {
      await downloadDiagnostics();
    } catch (nextError) {
      setError(errorMessage(nextError));
    } finally {
      setBusy(null);
    }
  }

  async function createBackup() {
    setBusy("backup");
    setError(null);
    try {
      const result = await startPersonalBackup();
      setBackup(result.operation);
    } catch (nextError) {
      setError(errorMessage(nextError));
    } finally {
      setBusy(null);
    }
  }

  const [title, description] = content[section];
  return (
    <div className="system-workspace">
      <header className="workspace-header">
        <p className="workspace-header__eyebrow">Administration · System</p>
        <h1>{title}</h1>
        <p>{description}</p>
      </header>
      <nav aria-label="System sections" className="admin-tabs">
        {routes.map(([href, label]) => (
          <Link
            aria-current={pathname === href ? "page" : undefined}
            className="admin-tabs__link"
            href={href}
            key={href}
          >
            {label}
          </Link>
        ))}
      </nav>
      {error ? (
        <div className="system-alert" role="alert">
          <strong>Could not complete that action</strong>
          <span>{error}</span>
          <button type="button" onClick={() => void load()}>Try again</button>
        </div>
      ) : null}
      {section === "overview" ? (
        <OverviewSection overview={overview} configuration={configuration} changes={changes} />
      ) : null}
      {section === "models" ? (
        <ModelsSection
          busy={busy}
          capabilities={capabilities}
          configuration={configuration}
          changes={changes}
          onChanged={load}
          onRun={run}
        />
      ) : null}
      {section === "ocr" ? (
        <OcrSection
          busy={busy}
          capabilities={capabilities}
          configuration={configuration}
          changes={changes}
          onChanged={load}
          onRun={run}
        />
      ) : null}
      {section === "maintenance" ? (
        <MaintenanceSection
          backup={backup}
          backupHistory={backupHistory}
          busy={busy}
          changes={changes}
          configuration={configuration}
          onExport={exportDiagnostics}
          onBackup={createBackup}
          onChanged={load}
          onError={setError}
          operations={operations}
          preview={preview}
          reprocessing={reprocessing}
          versions={versions}
        />
      ) : null}
    </div>
  );
}

function OverviewSection({
  overview,
  configuration,
  changes,
}: {
  overview: Overview | null;
  configuration: Configuration | null;
  changes: ConfigurationChange[];
}) {
  if (!overview || !configuration) return <Loading />;
  return (
    <div className="system-stack">
      <section className={`system-summary system-summary--${overview.overall_state}`}>
        <div>
          <div className="system-summary__meta">
            <p className="system-label">Current state</p>
            <Status value={overview.overall_state} />
          </div>
          <h2>{overview.overall_state === "ready" ? "Everything is ready" : "System needs attention"}</h2>
          <p className="system-summary__message">{overview.recommended_action}</p>
        </div>
      </section>
      <section className="system-section" aria-labelledby="services-title">
        <h2 id="services-title">Services</h2>
        <div className="system-rows">
          {overview.services.map((service) => (
            <div className="system-row" key={service.service_id}>
              <div><strong>{service.label}</strong><span>{service.message}</span></div>
              <Status value={service.state} />
            </div>
          ))}
        </div>
      </section>
      <section className="system-section" aria-labelledby="current-title">
        <h2 id="current-title">Current workload and configuration</h2>
        <dl className="system-facts">
          <Fact label="Ready documents" value={String(overview.documents.ready)} />
          <Fact label="Documents processing" value={String(overview.documents.processing)} />
          <Fact label="Queued jobs" value={String(overview.jobs.queued)} />
          <Fact label="Free disk" value={formatBytes(overview.disk.free_bytes)} />
          <Fact label="Generation model" value={configuration.generation_model} mono />
          <Fact label="Parser" value={configuration.parser_identity} mono />
        </dl>
      </section>
      <ConfigurationProgress configuration={configuration} changes={changes} />
    </div>
  );
}

function ModelsSection({
  busy,
  capabilities,
  configuration,
  changes,
  onChanged,
  onRun,
}: {
  busy: string | null;
  capabilities: Capabilities | null;
  configuration: Configuration | null;
  changes: ConfigurationChange[];
  onChanged: () => Promise<void>;
  onRun: (profile: Capability) => Promise<void>;
}) {
  if (!capabilities || !configuration) return <Loading />;
  const profiles = capabilities.profiles.filter((profile) => profile.function !== "ocr");
  return (
    <div className="system-stack">
      <section className="system-section">
        <h2>Active profiles</h2>
        <p className="system-intro">
          Release support describes what this release supports. Local validation proves what passed on this computer.
        </p>
        <div className="system-profile-list">
          {profiles.map((profile) => (
            <ProfileCard busy={busy === profile.profile_id} key={profile.profile_id} onRun={onRun} profile={profile} />
          ))}
        </div>
      </section>
      <RuntimeConfigurationEditor
        capabilities={capabilities}
        configuration={configuration}
        focus="models"
        key={configuration.effective_revision}
        onChanged={onChanged}
      />
      <ConfigurationProgress configuration={configuration} changes={changes} />
      <section className="system-section">
        <h2>Observed hardware</h2>
        <dl className="system-facts">
          <Fact label="Processor observation" value={capabilities.observed_processor} />
          <Fact label="Logical CPU count" value={String(capabilities.logical_cpu_count)} />
          <Fact label="System memory" value={formatBytes(capabilities.system_memory_bytes)} />
          <Fact label="Embedding model" value={configuration.embedding_model} mono />
          <Fact label="Reranker model" value={configuration.reranker_model} mono />
        </dl>
      </section>
    </div>
  );
}

function OcrSection({
  busy,
  capabilities,
  configuration,
  changes,
  onChanged,
  onRun,
}: {
  busy: string | null;
  capabilities: Capabilities | null;
  configuration: Configuration | null;
  changes: ConfigurationChange[];
  onChanged: () => Promise<void>;
  onRun: (profile: Capability, benchmark?: boolean) => Promise<void>;
}) {
  if (!capabilities || !configuration) return <Loading />;
  const profiles = capabilities.profiles.filter((item) => item.function === "ocr");
  const profile = profiles.find((item) => item.profile_id === configuration.ocr_profile_id);
  if (!profile) return <p role="status">The effective OCR profile is unavailable.</p>;
  const canRun = !["not_detected", "detected", "package_available"].includes(profile.local_validation_state);
  return (
    <div className="system-stack">
      <section className="system-section">
        <h2>Document reading profile</h2>
        {profiles.map((item) => (
          <ProfileCard busy={busy === item.profile_id} key={item.profile_id} onRun={onRun} profile={item} />
        ))}
        <div className="system-actions">
          <button
            className="button-secondary"
            disabled={!canRun || busy !== null}
            onClick={() => void onRun(profile, true)}
            type="button"
          >
            {busy === profile.profile_id ? "Running fixed check…" : "Run OCR benchmark"}
          </button>
          {!canRun ? <span>{profile.reason}</span> : <span>Uses one fixed synthetic page; no document content is sent.</span>}
        </div>
      </section>
      <section className="system-section">
        <h2>Effective OCR settings</h2>
        <dl className="system-facts">
          <Fact label="Engine" value={configuration.ocr_engine} mono />
          <Fact label="Device" value={configuration.ocr_device.toUpperCase()} />
          <Fact label="CPU threads" value={String(configuration.ocr_cpu_threads)} />
          <Fact label="Parallel OCR processes" value={String(configuration.ocr_process_count)} />
          <Fact label="Pages per batch" value={String(configuration.ocr_page_batch_size)} />
          <Fact label="Parser" value={configuration.parser_identity} mono />
        </dl>
      </section>
      <RuntimeConfigurationEditor
        capabilities={capabilities}
        configuration={configuration}
        focus="ocr"
        key={configuration.effective_revision}
        onChanged={onChanged}
      />
      <ConfigurationProgress configuration={configuration} changes={changes} />
    </div>
  );
}

function RuntimeConfigurationEditor({
  capabilities,
  configuration,
  focus,
  onChanged,
}: {
  capabilities: Capabilities;
  configuration: Configuration;
  focus: "models" | "ocr";
  onChanged: () => Promise<void>;
}) {
  const initial = selectionFrom(configuration);
  const [selection, setSelection] = useState<ConfigurationSelection>(initial);
  const [review, setReview] = useState<ConfigurationPreview | null>(null);
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [localError, setLocalError] = useState<string | null>(null);

  const available = capabilities.profiles.filter(
    (profile) => profile.release_support_class === "release_qualified"
      && (profile.selectable
        || (profile.function === "generation"
          && profile.profile_id === configuration.generation_profile_id)
        || (profile.function === "reranking"
          && profile.profile_id === configuration.reranker_profile_id)),
  );
  const generationOptions = available.filter((item) => item.function === "generation");
  const rerankerOptions = available.filter((item) => item.function === "reranking");
  const ocrOptions = capabilities.profiles.filter(
    (profile) => profile.function === "ocr"
      && profile.release_support_class === "release_qualified"
      && (profile.selectable || profile.profile_id === configuration.ocr_profile_id),
  );
  const cpuProfile = ocrOptions.find((profile) => profile.accelerator_vendor === "cpu");
  const gpuProfile = ocrOptions.find((profile) => profile.accelerator_vendor !== "cpu");
  const selectedOcrProfile = capabilities.profiles.find(
    (profile) => profile.profile_id === selection.ocr_profile_id,
  );
  const manualCpu = selection.ocr_mode === "explicit"
    && selectedOcrProfile?.accelerator_vendor === "cpu";
  const manualGpu = selection.ocr_mode === "explicit"
    && Boolean(selectedOcrProfile)
    && selectedOcrProfile?.accelerator_vendor !== "cpu";
  const changed = JSON.stringify(selection) !== JSON.stringify(initial);

  async function reviewChange() {
    setSubmitting(true);
    setLocalError(null);
    try {
      setReview(await previewConfiguration(selection));
    } catch (error) {
      setLocalError(errorMessage(error));
    } finally {
      setSubmitting(false);
    }
  }

  async function confirmChange() {
    if (!review) return;
    setSubmitting(true);
    setLocalError(null);
    try {
      const grant = await reauthenticateConfiguration(
        review.preview_id,
        review.impact_digest,
        password,
      );
      await applyConfiguration(review.preview_id, review.impact_digest, grant.grant_token);
      setPassword("");
      setReview(null);
      await onChanged();
    } catch (error) {
      setLocalError(errorMessage(error));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <section className="system-section" aria-labelledby={`${focus}-configuration-title`}>
      <h2 id={`${focus}-configuration-title`}>
        {focus === "models" ? "Choose active AI profiles" : "Choose OCR behavior"}
      </h2>
      <p className="system-intro">
        Only profiles qualified by this release and validated on this computer can be selected.
      </p>
      {focus === "models" ? (
        <div className="system-form-grid">
          <label>
            <span>Answer generation</span>
            <select
              onChange={(event) => setSelection({ ...selection, generation_profile_id: event.target.value })}
              value={selection.generation_profile_id}
            >
              {generationOptions.map((item) => (
                <option key={item.profile_id} value={item.profile_id}>{item.model_identity}</option>
              ))}
              {!generationOptions.some((item) => item.profile_id === selection.generation_profile_id) ? (
                <option disabled value={selection.generation_profile_id}>
                  Current profile unavailable ({selection.generation_profile_id})
                </option>
              ) : null}
            </select>
          </label>
          <label>
            <span>Result reranking</span>
            <select
              onChange={(event) => setSelection({ ...selection, reranker_profile_id: event.target.value })}
              value={selection.reranker_profile_id}
            >
              {rerankerOptions.map((item) => (
                <option key={item.profile_id} value={item.profile_id}>{item.model_identity}</option>
              ))}
              {!rerankerOptions.some((item) => item.profile_id === selection.reranker_profile_id) ? (
                <option disabled value={selection.reranker_profile_id}>
                  Current profile unavailable ({selection.reranker_profile_id})
                </option>
              ) : null}
            </select>
          </label>
        </div>
      ) : (
        <div className="system-stack">
          <fieldset className="system-choice-group">
            <legend>Execution device</legend>
            <label>
              <input
                checked={selection.ocr_mode === "auto"}
                name="ocr-mode"
                onChange={() => setSelection({ ...selection, ocr_mode: "auto" })}
                type="radio"
              />
              <span><strong>Auto</strong><small>Choose the best release-qualified profile that passed validation on this computer, with CPU fallback.</small></span>
            </label>
            <label>
              <input
                checked={manualCpu}
                disabled={!cpuProfile}
                name="ocr-mode"
                onChange={() => cpuProfile && setSelection({
                  ...selection,
                  ocr_mode: "explicit",
                  ocr_profile_id: cpuProfile.profile_id,
                })}
                type="radio"
              />
              <span><strong>CPU inference</strong><small>{cpuProfile ? "Pin OCR model inference to the CPU." : "No validated CPU OCR runtime is available."}</small></span>
            </label>
            <label>
              <input
                checked={manualGpu}
                disabled={!gpuProfile}
                name="ocr-mode"
                onChange={() => gpuProfile && setSelection({
                  ...selection,
                  ocr_mode: "explicit",
                  ocr_profile_id: gpuProfile.profile_id,
                })}
                type="radio"
              />
              <span><strong>GPU inference</strong><small>{gpuProfile ? `Pin model inference to ${gpuProfile.accelerator_vendor.toUpperCase()} GPU. PDF decoding and preprocessing still use CPU.` : "No release-qualified GPU OCR runtime has passed validation on this computer."}</small></span>
            </label>
          </fieldset>
          {selection.ocr_mode === "explicit" ? (
            <div className="system-form-grid">
              <label>
                <span>CPU threads</span>
                <input
                  disabled={!manualCpu}
                  inputMode="numeric"
                  max={capabilities.logical_cpu_count}
                  min={1}
                  onChange={(event) => setSelection({
                    ...selection,
                    ocr_cpu_threads: Number(event.target.value),
                  })}
                  type="number"
                  value={selection.ocr_cpu_threads}
                />
                <small>{manualCpu ? `1–${capabilities.logical_cpu_count} logical CPU threads.` : "Not used for GPU model inference."}</small>
              </label>
              <label>
                <span>Parallel OCR processes</span>
                <input
                  inputMode="numeric"
                  max={capabilities.maximum_ocr_processes}
                  min={1}
                  onChange={(event) => setSelection({
                    ...selection,
                    ocr_process_count: Number(event.target.value),
                  })}
                  type="number"
                  value={selection.ocr_process_count}
                />
                <small>1–{capabilities.maximum_ocr_processes}, based on this computer&apos;s CPU and memory. Each process loads its own model and uses substantially more RAM.</small>
              </label>
            </div>
          ) : null}
        </div>
      )}
      {localError ? <p className="system-inline-error" role="alert">{localError}</p> : null}
      {!review ? (
        <button
          className="button-primary"
          disabled={!changed || submitting || configuration.state !== "effective"}
          onClick={() => void reviewChange()}
          type="button"
        >
          {submitting ? "Preparing review…" : "Review change"}
        </button>
      ) : (
        <div className="system-change-review" role="region" aria-label="Configuration change review">
          <h3>Review before applying</h3>
          <p>{review.expected_interruption}</p>
          <dl className="system-facts">
            <Fact label="Affected services" value={review.affected_services.map(humanize).join(", ")} />
            <Fact label="Safe boundaries" value={review.waits_for.map(humanize).join(", ")} />
            <Fact label="Operation" value="Restart scoped" />
            <Fact label="Recovery" value="Automatic rollback if validation fails" />
          </dl>
          <p className="system-gate-note" role="status">
            This restart-only change does not rewrite your documents or search index. If the new runtime fails validation, Local RAG restores the previous configuration automatically.
          </p>
          <label className="system-password-field">
            <span>Confirm with your admin password</span>
            <input
              autoComplete="current-password"
              onChange={(event) => setPassword(event.target.value)}
              type="password"
              value={password}
            />
          </label>
          <div className="system-actions">
            <button className="button-secondary" disabled={submitting} onClick={() => setReview(null)} type="button">
              Cancel
            </button>
            <button
              className="button-primary"
              disabled={password.length === 0 || submitting}
              onClick={() => void confirmChange()}
              type="button"
            >
              {submitting ? "Starting safely…" : "Apply change"}
            </button>
          </div>
        </div>
      )}
    </section>
  );
}

function ConfigurationProgress({
  configuration,
  changes,
}: {
  configuration: Configuration;
  changes: ConfigurationChange[];
}) {
  const current = changes[0];
  if (!current && configuration.state === "effective") return null;
  return (
    <section className="system-section" aria-live="polite" aria-labelledby="configuration-progress-title">
      <h2 id="configuration-progress-title">Configuration progress</h2>
      <div className="system-row">
        <div>
          <strong>{current ? humanize(current.stage) : humanize(configuration.state)}</strong>
          <span>
            Effective {configuration.effective_revision} · Desired {configuration.desired_revision}
          </span>
        </div>
        <Status value={current?.state ?? configuration.state} />
      </div>
      {current?.reason_code ? <p>Result: {humanize(current.reason_code)}</p> : null}
    </section>
  );
}

function selectionFrom(configuration: Configuration): ConfigurationSelection {
  return {
    base_revision: configuration.effective_revision,
    generation_profile_id: configuration.generation_profile_id,
    reranker_profile_id: configuration.reranker_profile_id,
    ocr_mode: configuration.ocr_mode,
    ocr_profile_id: configuration.ocr_profile_id,
    ocr_cpu_threads: configuration.ocr_cpu_threads,
    ocr_process_count: configuration.ocr_process_count,
  };
}

function MaintenanceSection({
  backup,
  backupHistory,
  busy,
  changes,
  configuration,
  onExport,
  onBackup,
  onChanged,
  onError,
  operations,
  preview,
  reprocessing,
  versions,
}: {
  backup: PersonalBackupOperation | null;
  backupHistory: PersonalBackupOperation[];
  busy: string | null;
  changes: ConfigurationChange[];
  configuration: Configuration | null;
  onExport: () => Promise<void>;
  onBackup: () => Promise<void>;
  onChanged: (signal?: AbortSignal) => Promise<void>;
  onError: (message: string | null) => void;
  operations: Operation[];
  preview: DiagnosticsPreview | null;
  reprocessing: ReprocessingOperation[];
  versions: VersionInventory | null;
}) {
  if (!preview || !configuration || !versions) return <Loading />;
  return (
    <div className="system-stack">
      <section className="system-section">
        <h2>Safety prerequisite</h2>
        <p className="system-intro">
          Data-rewriting maintenance operations stay locked until a coordinated Personal backup passes an isolated restore check. Restart-only model and OCR settings use automatic rollback instead.
        </p>
        <div className="system-row">
          <div>
            <strong>Restore-verified backup</strong>
            <span>
              {configuration.backup_verified_at
                ? `Verified ${new Date(configuration.backup_verified_at).toLocaleString()}`
                : "No current verification evidence is available."}
            </span>
          </div>
          <Status value={configuration.backup_verified ? "ready" : "attention"} />
        </div>
        {backup ? (
          <div className="system-row" aria-live="polite">
            <div>
              <strong>{humanize(backup.stage)}</strong>
              <span>
                {backup.state === "succeeded"
                  ? "The coordinated bundle passed an isolated restore."
                  : backup.reason_code
                    ? humanize(backup.reason_code)
                    : "Keep Local RAG open while this operation finishes."}
              </span>
            </div>
            <Status value={backup.state} />
          </div>
        ) : null}
        <div className="system-actions">
          <button
            className="button-primary"
            disabled={busy !== null || backup?.state === "pending" || backup?.state === "running"}
            onClick={() => void onBackup()}
            type="button"
          >
            {busy === "backup" ? "Opening folder picker…" : "Create and verify backup"}
          </button>
        </div>
        <p className="system-gate-note">
          A folder picker opens on this computer. Local RAG then checksums the bundle and restores it into isolated temporary stores before marking it verified.
        </p>
        <div className="system-change-review" role="region" aria-label="Backup retention and destinations">
          <h3>Storage and retention</h3>
          <p>
            You choose the destination for every backup. Local RAG keeps all verified bundles
            and never deletes files in those folders automatically.
          </p>
          <dl className="system-facts">
            <Fact label="Destination" value="Chosen with a local folder picker" />
            <Fact label="Retention" value="Keep all (safe default)" />
            <Fact label="Automatic deletion" value="Off" />
          </dl>
        </div>
        <h3>Backup history</h3>
        {backupHistory.length === 0 ? (
          <p>No backup attempts have been recorded yet.</p>
        ) : (
          <div className="system-rows">
            {backupHistory.map((item) => (
              <div className="system-row" key={item.backup_run_id}>
                <div>
                  <strong>{item.restore_verified ? "Restore verified" : humanize(item.stage)}</strong>
                  <span>
                    {new Date(item.created_at).toLocaleString()}
                    {item.reason_code ? ` · ${humanize(item.reason_code)}` : ""}
                  </span>
                </div>
                <Status value={item.state} />
              </div>
            ))}
          </div>
        )}
      </section>
      <VersionMaintenance
        backupVerified={configuration.backup_verified}
        onChanged={onChanged}
        onError={onError}
        operations={reprocessing}
        versions={versions}
      />
      <ConfigurationProgress configuration={configuration} changes={changes} />
      <section className="system-section">
        <h2>Sanitized diagnostics</h2>
        <p className="system-intro">The support bundle includes only bounded system state. Review the exclusions before downloading.</p>
        <div className="system-diagnostics-grid">
          <div><strong>Included</strong><ul>{preview.files.map((file) => <li key={file}>{file}</li>)}</ul></div>
          <div><strong>Always excluded</strong><ul>{preview.exclusions.map((item) => <li key={item}>{item}</li>)}</ul></div>
        </div>
        <button className="button-primary" disabled={busy !== null} onClick={() => void onExport()} type="button">
          {busy === "diagnostics" ? "Preparing download…" : "Download sanitized diagnostics"}
        </button>
      </section>
      <section className="system-section">
        <h2>Recent validation activity</h2>
        {operations.length === 0 ? <p>No local validation has been run yet.</p> : (
          <div className="system-rows">
            {operations.slice(0, 10).map((operation) => (
              <div className="system-row" key={operation.operation_id}>
                <div><strong>{operation.operation_type === "profile_benchmark" ? "OCR benchmark" : "Profile validation"}</strong><span>{operation.profile_id}</span></div>
                <Status value={operation.state} />
              </div>
            ))}
          </div>
        )}
      </section>
    </div>
  );
}

const parserProfiles = [
  {
    id: "parser.paddleocr-vl-1.6.adaptive-v2",
    label: "Adaptive parser (recommended)",
  },
  {
    id: "parser.paddleocr-vl-1.6.legacy-v1",
    label: "Legacy parser",
  },
] as const;

function VersionMaintenance({
  backupVerified,
  onChanged,
  onError,
  operations,
  versions,
}: {
  backupVerified: boolean;
  onChanged: (signal?: AbortSignal) => Promise<void>;
  onError: (message: string | null) => void;
  operations: ReprocessingOperation[];
  versions: VersionInventory;
}) {
  const [parserProfile, setParserProfile] = useState(versions.ingestion.parser_profile_id);
  const [operationType, setOperationType] = useState<"reindex" | "reingestion">("reindex");
  const [targetParser, setTargetParser] = useState(parserProfiles[0].id as string);
  const [sourceParser, setSourceParser] = useState("");
  const [review, setReview] = useState<ReprocessingPreview | null>(null);
  const [password, setPassword] = useState("");
  const [working, setWorking] = useState<string | null>(null);
  const active = operations.find((item) =>
    ["running", "paused", "qualifying"].includes(item.state),
  );

  async function saveParserSelection() {
    setWorking("parser");
    onError(null);
    try {
      await selectIngestionProfile(versions.ingestion.revision_id, parserProfile);
      await onChanged();
    } catch (error) {
      onError(errorMessage(error));
    } finally {
      setWorking(null);
    }
  }

  async function createReview() {
    setWorking("preview");
    onError(null);
    try {
      const result = await previewReprocessing(
        operationType,
        operationType === "reindex" ? versions.embedding.profile_id : targetParser,
        operationType === "reingestion" && sourceParser ? sourceParser : null,
      );
      setReview(result);
      setPassword("");
    } catch (error) {
      onError(errorMessage(error));
    } finally {
      setWorking(null);
    }
  }

  async function confirmStart() {
    if (!review) return;
    setWorking("start");
    onError(null);
    try {
      const grant = await reauthenticateReprocessing(
        review.preview_id,
        review.impact_digest,
        password,
      );
      await startReprocessing(
        review.preview_id,
        review.impact_digest,
        grant.grant_token,
      );
      setReview(null);
      setPassword("");
      await onChanged();
    } catch (error) {
      onError(errorMessage(error));
    } finally {
      setWorking(null);
    }
  }

  async function control(
    operation: ReprocessingOperation,
    action: "pause" | "resume" | "cancel" | "retry",
  ) {
    setWorking(`${action}-${operation.operation_id}`);
    onError(null);
    try {
      await controlReprocessing(operation.operation_id, action);
      await onChanged();
    } catch (error) {
      onError(errorMessage(error));
    } finally {
      setWorking(null);
    }
  }

  async function generationAction(
    generationType: "embedding" | "document",
    generationId: string,
    action: "rollback" | "cleanup",
  ) {
    setWorking(`${action}-${generationId}`);
    onError(null);
    try {
      if (action === "rollback") {
        await rollbackEmbeddingGeneration(generationId);
      } else {
        await cleanupGeneration(generationType, generationId);
      }
      await onChanged();
    } catch (error) {
      onError(errorMessage(error));
    } finally {
      setWorking(null);
    }
  }

  return (
    <>
      <section className="system-section" aria-labelledby="new-upload-version-title">
        <h2 id="new-upload-version-title">Document processing for new uploads</h2>
        <p className="system-intro">
          This selection applies only to documents uploaded after you save it. Existing
          documents are never rewritten automatically.
        </p>
        <label className="system-password-field">
          <span>Parser and chunking profile</span>
          <select
            onChange={(event) => setParserProfile(event.target.value)}
            value={parserProfile}
          >
            {parserProfiles.map((profile) => (
              <option key={profile.id} value={profile.id}>{profile.label}</option>
            ))}
          </select>
        </label>
        <dl className="system-facts">
          <Fact label="Current parser" value={versions.ingestion.parser_version} mono />
          <Fact label="Current chunking" value={versions.ingestion.chunking_version} mono />
        </dl>
        <button
          className="button-primary"
          disabled={
            parserProfile === versions.ingestion.parser_profile_id || working !== null
          }
          onClick={() => void saveParserSelection()}
          type="button"
        >
          {working === "parser" ? "Saving…" : "Use for new uploads"}
        </button>
      </section>

      <section className="system-section" aria-labelledby="reprocessing-title">
        <h2 id="reprocessing-title">Rebuild existing documents</h2>
        <p className="system-intro">
          Local RAG builds a separate copy first. Current search remains available until
          the replacement passes its checks.
        </p>
        <fieldset className="system-choice-group" disabled={Boolean(active)}>
          <legend>What do you want to rebuild?</legend>
          <label>
            <input
              checked={operationType === "reindex"}
              name="reprocessing-type"
              onChange={() => { setOperationType("reindex"); setReview(null); }}
              type="radio"
            />
            <span>
              <strong>Search index</strong>
              <small>Recreate embeddings, test retrieval and citations, then switch once.</small>
            </span>
          </label>
          <label>
            <input
              checked={operationType === "reingestion"}
              name="reprocessing-type"
              onChange={() => { setOperationType("reingestion"); setReview(null); }}
              type="radio"
            />
            <span>
              <strong>Document parsing</strong>
              <small>Re-read only the matching ready documents with another parser.</small>
            </span>
          </label>
        </fieldset>
        {operationType === "reingestion" ? (
          <div className="system-form-grid">
            <label>
              <span>New parser profile</span>
              <select value={targetParser} onChange={(event) => setTargetParser(event.target.value)}>
                {parserProfiles.map((profile) => (
                  <option key={profile.id} value={profile.id}>{profile.label}</option>
                ))}
              </select>
            </label>
            <label>
              <span>Documents to include</span>
              <select value={sourceParser} onChange={(event) => setSourceParser(event.target.value)}>
                <option value="">All documents on a different parser</option>
                {versions.ingestion.document_versions.map((item) => (
                  <option key={item.parser_version} value={item.parser_version}>
                    {item.parser_version} ({item.document_count})
                  </option>
                ))}
              </select>
            </label>
          </div>
        ) : null}
        {!review ? (
          <button
            className="button-primary"
            disabled={Boolean(active) || working !== null}
            onClick={() => void createReview()}
            type="button"
          >
            {working === "preview" ? "Calculating impact…" : "Review rebuild"}
          </button>
        ) : (
          <div className="system-change-review" role="region" aria-label="Rebuild review">
            <h3>Review before starting</h3>
            <p>
              {review.document_count} documents and {review.chunk_count} chunks match.
              The additional working copy is estimated at {formatStorage(review.estimated_bytes)}.
            </p>
            <dl className="system-facts">
              <Fact label="Current search during rebuild" value="Available" />
              <Fact label="Backup gate" value={review.backup_verified ? "Passed" : "Required"} />
              <Fact label="Automatic switch" value="Only after qualification" />
            </dl>
            {!review.backup_verified || !backupVerified ? (
              <p className="system-gate-note" role="status">
                Start is locked until a coordinated backup passes isolated restore verification.
              </p>
            ) : (
              <label className="system-password-field">
                <span>Confirm with your admin password</span>
                <input
                  autoComplete="current-password"
                  onChange={(event) => setPassword(event.target.value)}
                  type="password"
                  value={password}
                />
              </label>
            )}
            <div className="system-actions">
              <button className="button-secondary" onClick={() => setReview(null)} type="button">
                Cancel
              </button>
              <button
                className="button-primary"
                disabled={!review.backup_verified || !backupVerified || !password || working !== null}
                onClick={() => void confirmStart()}
                type="button"
              >
                {working === "start" ? "Starting safely…" : "Start rebuild"}
              </button>
            </div>
          </div>
        )}
        <div className="system-rows" aria-live="polite">
          {operations.slice(0, 5).map((operation) => {
            const total = operation.operation_type === "reindex"
              ? operation.total_chunks
              : operation.total_documents;
            const completed = operation.operation_type === "reindex"
              ? operation.completed_chunks
              : operation.completed_documents;
            return (
              <div className="system-row system-row--stack" key={operation.operation_id}>
                <div>
                  <strong>{operation.operation_type === "reindex" ? "Search index" : "Document parsing"}</strong>
                  <span>{humanize(operation.stage)} · {completed} of {total}</span>
                  <progress aria-label={`${humanize(operation.operation_type)} progress`} max={Math.max(total, 1)} value={completed} />
                  {operation.reason_code ? <small>{humanize(operation.reason_code)}</small> : null}
                </div>
                <div className="system-actions">
                  {operation.state === "running" ? (
                    <button className="button-secondary" onClick={() => void control(operation, "pause")} type="button">Pause</button>
                  ) : null}
                  {operation.state === "paused" ? (
                    <>
                      <button className="button-secondary" onClick={() => void control(operation, "resume")} type="button">Resume</button>
                      <button className="button-secondary" onClick={() => void control(operation, "cancel")} type="button">Cancel rebuild</button>
                    </>
                  ) : null}
                  {operation.state === "failed" ? (
                    <>
                      <button className="button-secondary" onClick={() => void control(operation, "retry")} type="button">Retry failed work</button>
                      <button className="button-secondary" onClick={() => void control(operation, "cancel")} type="button">Discard unfinished copy</button>
                    </>
                  ) : null}
                  <Status value={operation.state} />
                </div>
              </div>
            );
          })}
        </div>
      </section>

      {versions.ingestion.generations.length > 0 ? (
        <section className="system-section" aria-labelledby="document-generations-title">
          <h2 id="document-generations-title">Document processing copies</h2>
          <p className="system-intro">
            Each row names the exact document copy created by a rebuild. The active copy is
            protected; only retained or failed copies marked below can be removed.
          </p>
          <div className="system-rows">
            {versions.ingestion.generations.map((generation) => (
              <div className="system-row" key={generation.generation_id}>
                <div>
                  <strong>{generation.filename}</strong>
                  <span>{generation.parser_version} · {generation.chunk_count} chunks · {shortId(generation.generation_id)}</span>
                </div>
                <div className="system-actions">
                  {generation.cleanup_available ? (
                    <button
                      className="button-secondary"
                      disabled={working !== null}
                      onClick={() => void generationAction("document", generation.generation_id, "cleanup")}
                      type="button"
                    >
                      Delete this copy
                    </button>
                  ) : null}
                  <Status value={generation.state} />
                </div>
              </div>
            ))}
          </div>
        </section>
      ) : null}

      <section className="system-section" aria-labelledby="embedding-generations-title">
        <h2 id="embedding-generations-title">Search index versions</h2>
        <p className="system-intro">
          The active version serves every search. Retained versions provide a bounded rollback;
          cleanup removes only the exact version shown.
        </p>
        <div className="system-rows">
          {versions.embedding.generations.map((generation) => (
            <div className="system-row" key={generation.generation_id}>
              <div>
                <strong>{generation.state === "active" ? "Active search index" : humanize(generation.state)}</strong>
                <span>{generation.embedding_version} · {generation.chunk_count} chunks · {shortId(generation.generation_id)}</span>
              </div>
              <div className="system-actions">
                {generation.state === "retained" ? (
                  <button className="button-secondary" disabled={working !== null} onClick={() => void generationAction("embedding", generation.generation_id, "rollback")} type="button">Roll back</button>
                ) : null}
                {generation.cleanup_available && ["retained", "abandoned"].includes(generation.state) ? (
                  <button className="button-secondary" disabled={working !== null} onClick={() => void generationAction("embedding", generation.generation_id, "cleanup")} type="button">Delete this copy</button>
                ) : null}
                <Status value={generation.state} />
              </div>
            </div>
          ))}
        </div>
      </section>
    </>
  );
}

function ProfileCard({ busy, onRun, profile }: { busy: boolean; onRun: (profile: Capability) => Promise<void>; profile: Capability }) {
  const canRun = !["not_detected", "detected", "package_available"].includes(profile.local_validation_state);
  return (
    <article className="system-profile">
      <div className="system-profile__heading">
        <div><p className="system-label">{profile.function}</p><h3>{profile.model_identity}</h3></div>
        {profile.effective ? <TrustStatus tone="verified">Effective</TrustStatus> : null}
      </div>
      <dl className="system-profile__states">
        <Fact label="Release support" value={humanize(profile.release_support_class)} />
        <Fact label="This computer" value={humanize(profile.local_validation_state)} />
        <Fact label="Engine" value={profile.engine} mono />
        <Fact label="Device" value={profile.accelerator_vendor === "none" ? "CPU" : profile.accelerator_vendor} />
      </dl>
      <p>{profile.reason}</p>
      {profile.evidence ? <p className="system-evidence">Last result: {humanize(profile.evidence.reason_code)} · {new Date(profile.evidence.evidence_at).toLocaleString()}</p> : null}
      <div className="system-actions">
        <button className="button-secondary" disabled={!canRun || busy} onClick={() => void onRun(profile)} type="button">
          {busy ? "Running fixed validation…" : "Run local validation"}
        </button>
      </div>
    </article>
  );
}

function Fact({ label, mono, value }: { label: string; mono?: boolean; value: string }) {
  return <div><dt>{label}</dt><dd className={mono ? "system-mono" : undefined}>{value}</dd></div>;
}

function Status({ value }: { value: string }) {
  return (
    <TrustStatus tone={statusTones[value] ?? "neutral"}>
      {humanize(value)}
    </TrustStatus>
  );
}

function Loading() {
  return <p className="system-loading" role="status">Loading System information…</p>;
}

function humanize(value: string): string {
  return value.replaceAll("_", " ").replace(/^./, (letter) => letter.toUpperCase());
}

function formatBytes(value: number): string {
  if (value <= 0) return "Unavailable";
  return `${(value / 1024 ** 3).toFixed(1)} GiB`;
}

function formatStorage(value: number): string {
  if (value < 1024 ** 2) return `${Math.ceil(value / 1024)} KiB`;
  if (value < 1024 ** 3) return `${(value / 1024 ** 2).toFixed(1)} MiB`;
  return `${(value / 1024 ** 3).toFixed(1)} GiB`;
}

function shortId(value: string): string {
  return value.slice(0, 8);
}
