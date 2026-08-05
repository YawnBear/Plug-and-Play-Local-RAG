"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { Icon } from "@/components/shell/icons";
import { Drawer } from "@/components/ui/drawer";
import { TrustStatus } from "@/components/ui/trust-status";
import { documentContentUrl } from "@/features/library/api";
import { ApiError } from "@/lib/http";
import { citationRoute } from "@/lib/route-state";

import { getCitationEvidence } from "./api";
import { CitationPdfViewer } from "./citation-pdf-viewer";
import type {
  CitationEvidence,
  HistoricalSource,
  StreamSource,
} from "./contracts";

export type Source = HistoricalSource | StreamSource;

function pageLabel(source: Source): string {
  return source.page_start === source.page_end
    ? `Page ${source.page_start}`
    : `Pages ${source.page_start}–${source.page_end}`;
}

function sourceKey(source: Source): string {
  return `${source.label}-${source.document_id_snapshot}-${source.chunk_id_snapshot}`;
}

export function SourceList({
  sources,
  draft = false,
  title = "Sources",
  chatId,
  turnId,
  selected: controlledSelected,
  onSelect,
}: {
  sources: readonly Source[];
  draft?: boolean;
  title?: string;
  chatId?: string;
  turnId?: string;
  selected?: Source | null;
  onSelect?: (source: Source | null) => void;
}) {
  const [internalSelected, setInternalSelected] = useState<Source | null>(null);
  const selected =
    controlledSelected === undefined ? internalSelected : controlledSelected;
  const setSelected = onSelect ?? setInternalSelected;
  const [evidenceResult, setEvidenceResult] = useState<{
    key: string;
    evidence: CitationEvidence | null;
    state: "idle" | "unavailable" | "error";
  } | null>(null);
  const [retry, setRetry] = useState(0);
  const evidenceKey =
    !draft && selected && selected.source_available && chatId && turnId
      ? `${chatId}:${turnId}:${selected.label}:${retry}`
      : null;
  const evidence =
    evidenceKey && evidenceResult?.key === evidenceKey
      ? evidenceResult.evidence
      : null;
  const evidenceState =
    evidenceKey === null
      ? "idle"
      : evidenceResult?.key === evidenceKey
        ? evidenceResult.state
        : "loading";

  useEffect(() => {
    if (!evidenceKey || !selected || !chatId || !turnId) return;
    const controller = new AbortController();
    void getCitationEvidence(
      chatId,
      turnId,
      selected.label,
      controller.signal,
    )
      .then((value) => {
        if (
          value.label !== selected.label ||
          value.document_id !== selected.document_id_snapshot
        ) {
          throw new TypeError("Citation evidence identity changed.");
        }
        setEvidenceResult({ key: evidenceKey, evidence: value, state: "idle" });
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted) return;
        setEvidenceResult({
          key: evidenceKey,
          evidence: null,
          state:
            error instanceof ApiError && error.status === 404
              ? "unavailable"
              : "error",
        });
      });
    return () => controller.abort();
  }, [chatId, evidenceKey, selected, turnId]);

  if (sources.length === 0) return null;

  const selectedRoute =
    selected && (draft ? selected.source_available : evidence !== null)
      ? citationRoute(selected)
      : null;
  const authorizedDocumentId = draft ? selected?.document_id : evidence?.document_id;
  const previewUrl =
    selected && authorizedDocumentId
      ? `${documentContentUrl(authorizedDocumentId)}#page=${selected.page_start}`
      : null;

  return (
    <>
      <section
        aria-label={draft ? `${title}, draft and unverified` : title}
        className="source-list"
      >
        <div className="source-list__heading">
          <h3>{title}</h3>
          {draft ? (
            <TrustStatus tone="pending">Draft · Unverified</TrustStatus>
          ) : null}
        </div>
        <ol>
          {sources.map((source) => {
            const available =
              Boolean(citationRoute(source)) && source.source_available;
            return (
              <li key={sourceKey(source)}>
                <button
                  type="button"
                  onClick={() => setSelected(source)}
                  disabled={!available}
                  className="source-card"
                  aria-expanded={
                    selected ? sourceKey(selected) === sourceKey(source) : false
                  }
                  aria-label={
                    available
                      ? `Preview ${source.display_name}, ${pageLabel(source)}`
                      : `${source.display_name}, source unavailable`
                  }
                >
                  <span className="source-card__label">{source.label}</span>
                  <span className="source-card__body">
                    <strong>{source.display_name}</strong>
                    <span>
                      {source.logical_path}
                      {source.section ? ` · ${source.section}` : ""}
                    </span>
                  </span>
                  <span className="source-card__page">
                    {available ? pageLabel(source) : "Unavailable"}
                  </span>
                </button>
              </li>
            );
          })}
        </ol>
      </section>

      <Drawer
        open={selected !== null}
        onClose={() => setSelected(null)}
        title={selected ? `${selected.label} · ${selected.display_name}` : "Source"}
        side="right"
        routeKey={selected ? sourceKey(selected) : undefined}
      >
        {selected ? (
          <div className="citation-preview">
            <div className="citation-preview__metadata">
              <span className="citation-preview__page">
                <Icon name="source" />
                {pageLabel(selected)}
              </span>
              <p>{selected.logical_path}</p>
              {selected.section ? <p>Section: {selected.section}</p> : null}
            </div>
            {evidence ? (
              <section
                className="citation-evidence"
                aria-label={`${selected.label} cited evidence`}
              >
                <h3>Cited chunk</h3>
                <mark>{evidence.snapshot_text}</mark>
              </section>
            ) : null}
            {evidenceState === "loading" ? (
              <p className="citation-preview__status" role="status">
                Loading authorized citation evidence…
              </p>
            ) : null}
            {evidenceState === "unavailable" ||
            (!selected.source_available && !draft) ? (
              <p className="citation-preview__unavailable">
                This source is unavailable. Only its historical citation
                metadata remains visible.
              </p>
            ) : null}
            {evidenceState === "error" ? (
              <div className="citation-preview__unavailable">
                <p role="alert">Citation evidence could not be loaded.</p>
                <button type="button" onClick={() => setRetry((value) => value + 1)}>
                  Retry
                </button>
              </div>
            ) : null}
            <div className="citation-preview__actions">
              {selectedRoute ? (
                <Link href={selectedRoute} onClick={() => setSelected(null)}>
                  Open in Knowledge Base
                </Link>
              ) : null}
              {previewUrl ? (
                <a href={previewUrl} target="_blank" rel="noreferrer">
                  Open PDF in new tab
                  <Icon name="external" />
                </a>
              ) : null}
            </div>
            {draft && previewUrl ? (
              <iframe
                key={previewUrl}
                src={previewUrl}
                aria-label={`${selected.display_name}, page ${selected.page_start}`}
                className="citation-preview__frame"
              />
            ) : null}
            {!draft && evidence ? (
              <CitationPdfViewer
                key={`${evidence.document_id}:${evidence.label}`}
                evidence={evidence}
              />
            ) : null}
          </div>
        ) : null}
      </Drawer>
    </>
  );
}
