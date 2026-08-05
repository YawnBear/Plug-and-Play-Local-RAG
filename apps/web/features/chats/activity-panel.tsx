"use client";

import { useLayoutEffect, useRef, useState } from "react";

import { Icon } from "@/components/shell/icons";
import { TrustStatus } from "@/components/ui/trust-status";

import type { StreamStatusPhase } from "./contracts";
import type { DraftActivityEntry, DraftTurn, WorkspacePhase } from "./state";

const PHASE_LABELS: Record<StreamStatusPhase, string> = {
  retrieving: "Retrieving relevant documents",
  reranking: "Reranking evidence",
  preparing_answer: "Preparing the answer",
  reasoning: "Running model reasoning",
  streaming_answer: "Streaming the answer",
  continuing_answer: "Continuing after the output limit",
  validating_citations: "Validating citations",
  repairing_citations: "Repairing answer citations",
};

function latestProgress(
  activity: readonly DraftActivityEntry[],
): StreamStatusPhase | null {
  for (let index = activity.length - 1; index >= 0; index -= 1) {
    const entry = activity[index];
    if (entry?.kind === "progress") return entry.phase;
  }
  return null;
}

export function ActivityPanel({
  draft,
  phase,
}: {
  draft: DraftTurn;
  phase: WorkspacePhase;
}) {
  const completed = phase === "recovering" || draft.final !== null;
  const [completedOpen, setCompletedOpen] = useState(false);
  const currentProgress = latestProgress(draft.activity);
  const thinkingRef = useRef<HTMLPreElement>(null);
  const latestThinkingIndex = draft.activity.findLastIndex(
    (entry) => entry.kind === "thinking",
  );
  const latestThinkingEntry = draft.activity[latestThinkingIndex];
  const latestThinking =
    latestThinkingEntry?.kind === "thinking" ? latestThinkingEntry.text : null;

  useLayoutEffect(() => {
    const node = thinkingRef.current;
    if (!node || latestThinking === null) return;
    node.scrollTop = node.scrollHeight;
  }, [latestThinking]);

  return (
    <section className="activity-panel">
      <p className="sr-only" aria-live="polite" aria-atomic="true">
        {currentProgress ? PHASE_LABELS[currentProgress] : "Starting request"}
      </p>
      <details
        open={completed ? completedOpen : true}
        onToggle={(event) => {
          if (completed) setCompletedOpen(event.currentTarget.open);
        }}
      >
        <summary className="activity-panel__summary">
          <span className="activity-panel__title">
            <span className={completed ? "activity-dot activity-dot--complete" : "activity-dot"} />
            Activity
          </span>
          <span className="activity-panel__current">
            {currentProgress ? PHASE_LABELS[currentProgress] : "Starting request"}
          </span>
          <Icon className="disclosure-chevron" name="chevron" />
        </summary>
        <div className="activity-panel__body">
          <ol className="activity-list">
            {draft.activity.map((entry, index) =>
              entry.kind === "progress" ? (
                <li
                  key={`progress-${index}-${entry.phase}`}
                  className="activity-list__progress"
                >
                  <strong>Progress</strong>
                  <span>
                    {PHASE_LABELS[entry.phase]}
                  </span>
                </li>
              ) : (
                <li
                  key={`thinking-${index}`}
                  className="activity-list__thinking"
                >
                  <strong>Model thinking</strong>
                  <div>
                    <TrustStatus
                      tone="warning"
                      className="activity-list__warning"
                    >
                      Unverified model thinking
                    </TrustStatus>
                    {entry.text ? (
                      <pre
                        ref={index === latestThinkingIndex ? thinkingRef : undefined}
                      >
                        {entry.text}
                      </pre>
                    ) : (
                      <p className="activity-list__waiting">
                        {draft.reasoningComplete
                          ? "The model returned no visible thinking."
                          : "Waiting for model thinking…"}
                      </p>
                    )}
                    {draft.reasoningTruncated ? (
                      <p className="activity-list__limit">
                        Display limited to 20,000 characters.
                      </p>
                    ) : null}
                  </div>
                </li>
              ),
            )}
          </ol>
        </div>
      </details>
    </section>
  );
}
