"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { Icon } from "@/components/shell/icons";
import { ApiError } from "@/lib/http";

import { documentContentUrl, headDocumentContent } from "./api";
import type { DocumentSummary } from "./contracts";
import { normalizeDocumentPage } from "./state";

type PreviewState =
  | "loading"
  | "ready"
  | "missing"
  | "gone"
  | "unavailable"
  | "error";

export function PdfPreview({
  document,
  page,
  onPage,
  onMissing,
}: {
  document: DocumentSummary;
  page: number;
  onPage: (page: number) => void;
  onMissing: () => void;
}) {
  const [status, setStatus] = useState<PreviewState>("loading");
  const [error, setError] = useState<string | null>(null);
  const request = useRef(0);
  const controllerRef = useRef<AbortController | null>(null);
  const normalizedPage = normalizeDocumentPage(page, document.page_count);
  const contentUrl = `${documentContentUrl(document.document_id)}#page=${normalizedPage}`;

  const inspect = useCallback(() => {
    controllerRef.current?.abort();
    const generation = ++request.current;
    const controller = new AbortController();
    controllerRef.current = controller;
    void headDocumentContent(document.document_id, controller.signal)
      .then(() => {
        if (generation === request.current) setStatus("ready");
      })
      .catch((reason: unknown) => {
        if (generation !== request.current || controller.signal.aborted) return;
        if (reason instanceof ApiError && reason.status === 404) {
          setStatus("missing");
          onMissing();
          return;
        }
        if (reason instanceof ApiError && reason.status === 410) {
          setStatus("gone");
          setError("The retained PDF bytes are unavailable.");
          return;
        }
        if (reason instanceof ApiError && reason.status === 503) {
          setStatus("unavailable");
          setError("Object storage is unavailable. Start it and try again.");
          return;
        }
        setStatus("error");
        setError(
          reason instanceof Error ? reason.message : "Preview failed to load.",
        );
      })
      .finally(() => {
        if (controllerRef.current === controller) {
          controllerRef.current = null;
        }
      });
  }, [document.document_id, onMissing]);

  useEffect(() => {
    inspect();
    return () => {
      request.current += 1;
      controllerRef.current?.abort();
      controllerRef.current = null;
    };
  }, [inspect]);

  return (
    <section aria-labelledby="preview-title" className="kb-pdf-editor">
      <h1 className="sr-only">{document.display_name}</h1>
      <div className="kb-pdf-editor__toolbar">
        <h2 id="preview-title" className="sr-only">PDF preview</h2>
        <button
          aria-label="Previous page"
          disabled={normalizedPage <= 1}
          onClick={() => onPage(normalizedPage - 1)}
          type="button"
        >
          <Icon className="rotate-180" name="chevron" />
        </button>
        <span>
          Page {normalizedPage}
          {document.page_count !== null ? ` / ${document.page_count}` : ""}
        </span>
        <button
          aria-label="Next page"
          disabled={
            document.page_count !== null &&
            normalizedPage >= document.page_count
          }
          onClick={() => onPage(normalizedPage + 1)}
          type="button"
        >
          <Icon name="chevron" />
        </button>
        <span className="kb-pdf-editor__spacer" />
        <a
          aria-label="Open in new tab"
          href={contentUrl}
          rel="noreferrer"
          target="_blank"
        >
          <Icon name="external" />
          <span>Open in new tab</span>
        </a>
      </div>
      {status === "loading" ? (
        <p className="kb-pdf-editor__state" role="status">
          Checking PDF availability…
        </p>
      ) : null}
      {status === "ready" ? (
        <iframe
          aria-label={`${document.display_name}, page ${normalizedPage}`}
          className="kb-pdf-editor__frame"
          key={`${document.document_id}-${normalizedPage}`}
          src={contentUrl}
        />
      ) : null}
      {error ? (
        <div className="kb-pdf-editor__error">
          <p role="alert">{error}</p>
          {status === "unavailable" || status === "error" ? (
            <button
              onClick={() => {
                setStatus("loading");
                setError(null);
                inspect();
              }}
              type="button"
            >
              Retry preview
            </button>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}
