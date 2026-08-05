"use client";

import { useEffect, useRef, useState } from "react";

import { documentContentUrl } from "@/features/library/api";

import type { CitationEvidence } from "./contracts";
import {
  ocrRegionRectangles,
  resolveTextQuote,
  textSpanRectangles,
  type OverlayRect,
  type TextItemLike,
} from "./pdf-highlight";

export function CitationPdfViewer({
  evidence,
}: {
  evidence: CitationEvidence;
}) {
  const [pageNumber, setPageNumber] = useState(
    evidence.highlight_anchor.pages[0].page,
  );
  const [rectangles, setRectangles] = useState<OverlayRect[]>([]);
  const [viewportSize, setViewportSize] = useState({ width: 1, height: 1 });
  const [status, setStatus] = useState<"loading" | "ready" | "unresolved" | "error">(
    "loading",
  );
  const [retry, setRetry] = useState(0);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const firstHighlightRef = useRef<SVGRectElement>(null);
  const anchorPage =
    evidence.highlight_anchor.pages.find((page) => page.page === pageNumber) ??
    evidence.highlight_anchor.pages[0];

  useEffect(() => {
    let active = true;
    let loadingTask: { destroy(): Promise<void> } | null = null;
    let document: { destroy(): Promise<void> } | null = null;
    let renderTask: { cancel(): void; promise: Promise<void> } | null = null;
    const run = async (): Promise<void> => {
      setStatus("loading");
      setRectangles([]);
      try {
        const pdfjs = await import("pdfjs-dist");
        pdfjs.GlobalWorkerOptions.workerSrc = new URL(
          "pdfjs-dist/build/pdf.worker.min.mjs",
          import.meta.url,
        ).toString();
        const task = pdfjs.getDocument({
          url: documentContentUrl(evidence.document_id),
          withCredentials: true,
          useWasm: false,
          isEvalSupported: false,
        });
        loadingTask = task;
        const loaded = await task.promise;
        document = loaded;
        const page = await loaded.getPage(pageNumber);
        const base = page.getViewport({ scale: 1 });
        const available = Math.max(containerRef.current?.clientWidth ?? 420, 280);
        const scale = Math.min(Math.max(available / base.width, 0.5), 2);
        const viewport = page.getViewport({ scale });
        const dpr = Math.min(window.devicePixelRatio || 1, 2);
        const canvas = canvasRef.current;
        if (!canvas || !active) return;
        canvas.width = Math.ceil(viewport.width * dpr);
        canvas.height = Math.ceil(viewport.height * dpr);
        const context = canvas.getContext("2d");
        if (!context) throw new Error("Canvas is unavailable.");
        const rendering = page.render({
          canvas,
          canvasContext: context,
          viewport,
          transform: dpr === 1 ? undefined : [dpr, 0, 0, dpr, 0, 0],
        });
        renderTask = rendering;
        await rendering.promise;
        let nextRectangles: OverlayRect[];
        let unresolved = false;
        if (anchorPage.kind === "ocr_regions") {
          nextRectangles = ocrRegionRectangles(
            anchorPage.regions,
            page.view,
            viewport,
          );
        } else {
          const textContent = await page.getTextContent();
          const items: TextItemLike[] = textContent.items.flatMap((item) =>
            "str" in item
              ? [
                  {
                    str: item.str,
                    width: item.width,
                    height: item.height,
                    transform: item.transform,
                    hasEOL: item.hasEOL,
                  },
                ]
              : [],
          );
          const spans = resolveTextQuote(items, anchorPage.selector);
          unresolved = spans === null;
          nextRectangles = spans
            ? textSpanRectangles(items, spans, viewport)
            : [];
        }
        if (!active) return;
        setViewportSize({ width: viewport.width, height: viewport.height });
        setRectangles(nextRectangles);
        setStatus(unresolved ? "unresolved" : "ready");
      } catch (error) {
        if (!active || (error instanceof Error && error.name === "RenderingCancelledException")) {
          return;
        }
        setStatus("error");
      }
    };
    void run();
    return () => {
      active = false;
      renderTask?.cancel();
      void document?.destroy();
      void loadingTask?.destroy();
    };
  }, [anchorPage, evidence.document_id, pageNumber, retry]);

  useEffect(() => {
    if (status !== "ready" || rectangles.length === 0) return;
    const frame = requestAnimationFrame(() => {
      firstHighlightRef.current?.scrollIntoView({
        block: "center",
        behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches
          ? "auto"
          : "smooth",
      });
    });
    return () => cancelAnimationFrame(frame);
  }, [rectangles, status]);

  return (
    <section className="citation-pdf" aria-label="Highlighted PDF evidence">
      {evidence.highlight_anchor.pages.length > 1 ? (
        <div className="citation-pdf__pages" aria-label="Cited pages">
          {evidence.highlight_anchor.pages.map((page) => (
            <button
              key={page.page}
              type="button"
              aria-pressed={page.page === pageNumber}
              onClick={() => setPageNumber(page.page)}
            >
              Page {page.page}
            </button>
          ))}
        </div>
      ) : null}
      <div ref={containerRef} className="citation-pdf__viewport">
        <canvas
          ref={canvasRef}
          className="citation-pdf__canvas"
          aria-label={`${evidence.display_name}, page ${pageNumber}`}
        />
        {status === "ready" ? (
          <svg
            className="citation-pdf__overlay"
            viewBox={`0 0 ${viewportSize.width} ${viewportSize.height}`}
            preserveAspectRatio="xMidYMid meet"
            aria-hidden="true"
          >
            {rectangles.map((rectangle, index) => (
              <rect
                key={`${index}-${rectangle.x}-${rectangle.y}`}
                ref={index === 0 ? firstHighlightRef : undefined}
                x={rectangle.x}
                y={rectangle.y}
                width={rectangle.width}
                height={rectangle.height}
              />
            ))}
          </svg>
        ) : null}
        {status === "loading" ? (
          <p className="citation-pdf__status" role="status">
            Loading cited page…
          </p>
        ) : null}
      </div>
      {status === "unresolved" ? (
        <p className="turn-notice turn-notice--warning">
          Exact PDF highlight unavailable. The cited page and authorized
          excerpt are still shown.
        </p>
      ) : null}
      {status === "error" ? (
        <div className="citation-pdf__error">
          <p role="alert">The cited PDF page could not be displayed.</p>
          <button type="button" onClick={() => setRetry((value) => value + 1)}>
            Retry
          </button>
        </div>
      ) : null}
    </section>
  );
}
