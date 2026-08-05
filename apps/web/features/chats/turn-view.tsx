"use client";

import { useState } from "react";

import { TrustStatus, type TrustStatusTone } from "@/components/ui/trust-status";

import type { ChatTurn } from "./contracts";
import type { DraftTurn, WorkspacePhase } from "./state";
import { ActivityPanel } from "./activity-panel";
import { MarkdownOutput } from "./markdown-output";
import { SourceList } from "./source-list";
import type { Source } from "./source-list";

function statusLabel(turn: ChatTurn): string {
  if (turn.status === "complete") return "Verified";
  if (turn.status === "interrupted") return "Interrupted";
  if (turn.status === "failed") return "Failed";
  if (turn.status === "length_limited") return "Unverified · Output limit";
  if (turn.status === "citation_failed") return "Unverified · Citation error";
  if (turn.status === "access_revoked") return "Access revoked";
  return "Recovering";
}

function statusTone(turn: ChatTurn): TrustStatusTone {
  if (turn.status === "complete") return "verified";
  if (["failed", "access_revoked"].includes(turn.status)) {
    return "danger";
  }
  if (turn.status === "generating") return "pending";
  return "warning";
}

export function TurnView({
  chatId,
  turn,
  activityDraft,
  canRetry,
  onRetry,
  retrying,
}: {
  chatId: string;
  turn: ChatTurn;
  activityDraft: DraftTurn | null;
  canRetry: boolean;
  onRetry: () => void;
  retrying: boolean;
}) {
  const [selected, setSelected] = useState<Source | null>(null);
  const citations = turn.status === "complete" ? turn.citations : [];
  const selectCitation = (label: string): void => {
    const source = citations.find((item) => item.label === label);
    if (source?.source_available) setSelected(source);
  };
  return (
    <article className="chat-turn" aria-labelledby={`turn-${turn.turn_id}`}>
      <div className="chat-turn__question">
        <p id={`turn-${turn.turn_id}`}>{turn.question}</p>
      </div>
      <div className="chat-turn__answer">
        <div className="chat-turn__status">
          <TrustStatus tone={statusTone(turn)}>
            {statusLabel(turn)}
          </TrustStatus>
          {turn.insufficient_context ? (
            <TrustStatus tone="warning">
              Insufficient context
            </TrustStatus>
          ) : null}
        </div>
        {activityDraft ? (
          <ActivityPanel draft={activityDraft} phase="verified" />
        ) : null}
        {turn.status === "length_limited" ? (
          <p className="turn-notice turn-notice--warning">
            This partial answer reached the output limit and has not been
            citation-verified.
          </p>
        ) : null}
        {turn.status === "citation_failed" ? (
          <p className="turn-notice turn-notice--warning">
            Citation repair could not verify this draft. Its content is
            preserved but is not part of verified conversation history.
          </p>
        ) : null}
        {turn.final_answer || turn.partial_answer ? (
          <MarkdownOutput
            className="chat-turn__output"
            citations={citations}
            selectedLabel={selected?.label ?? null}
            onCitationSelect={selectCitation}
          >
            {turn.final_answer ?? turn.partial_answer ?? ""}
          </MarkdownOutput>
        ) : null}
        {turn.error &&
        !["length_limited", "citation_failed"].includes(turn.status) ? (
          <p role="alert" className="turn-notice turn-notice--danger">
            {turn.error}
          </p>
        ) : null}
        <SourceList
          sources={turn.status === "complete" ? turn.citations : turn.sources}
          title={turn.status === "complete" ? "Citations" : "Source snapshot"}
          chatId={turn.status === "complete" ? chatId : undefined}
          turnId={turn.status === "complete" ? turn.turn_id : undefined}
          selected={selected}
          onSelect={setSelected}
        />
        {canRetry ? (
          <button
            type="button"
            onClick={onRetry}
            disabled={retrying}
            className="button-secondary turn-retry"
          >
            {retrying
              ? turn.status === "length_limited"
                ? "Continuing…"
                : turn.status === "citation_failed"
                  ? "Retrying citations…"
                  : "Retrying…"
              : turn.status === "length_limited"
                ? "Continue response"
                : turn.status === "citation_failed"
                  ? "Retry citations"
                  : "Retry this turn"}
          </button>
        ) : null}
      </div>
    </article>
  );
}

export function PendingTurnView({
  draft,
  phase,
}: {
  draft: DraftTurn;
  phase: WorkspacePhase;
}) {
  const label =
    phase === "stopping"
      ? "Stopping"
      : phase === "recovering"
        ? "Recovering"
        : phase === "failed"
          ? "Failed"
          : "Draft · Unverified";
  return (
    <article className="chat-turn">
      {!draft.isRetry ? (
        <div className="chat-turn__question">
          <p>{draft.question}</p>
        </div>
      ) : null}
      <div className="chat-turn__answer">
        <div className="chat-turn__status">
          <TrustStatus
            tone={phase === "failed" ? "danger" : "pending"}
            live
          >
            {label}
          </TrustStatus>
        </div>
        <ActivityPanel draft={draft} phase={phase} />
        {draft.text ? (
          <section className="draft-output" aria-label="Draft answer">
            <p>Draft answer · Unverified until citation validation completes</p>
            <MarkdownOutput className="chat-turn__output">
              {draft.text}
            </MarkdownOutput>
          </section>
        ) : null}
        <SourceList sources={draft.sources} draft />
      </div>
    </article>
  );
}
