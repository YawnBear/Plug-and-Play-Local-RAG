"use client";

import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type UIEvent,
} from "react";

import type { ChatDetail } from "./contracts";
import type { DraftTurn, WorkspacePhase } from "./state";
import { PendingTurnView, TurnView } from "./turn-view";

const PIN_THRESHOLD = 48;

export function Transcript({
  detail,
  draft,
  phase,
  onRetry,
}: {
  detail: ChatDetail;
  draft: DraftTurn | null;
  phase: WorkspacePhase;
  onRetry: () => void;
}) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const previousTop = useRef(0);
  const pinnedRef = useRef(true);
  const [pinned, setPinned] = useState(true);
  const latest = detail.turns.at(-1);

  const updatePinned = useCallback((value: boolean) => {
    pinnedRef.current = value;
    setPinned(value);
  }, []);

  const scrollLatest = useCallback((force = false) => {
    requestAnimationFrame(() => {
      const node = scrollRef.current;
      if (!node || (!force && !pinnedRef.current)) return;
      const reduced = window.matchMedia(
        "(prefers-reduced-motion: reduce)",
      ).matches;
      node.scrollTo({
        top: node.scrollHeight,
        behavior: reduced ? "auto" : "instant",
      });
      previousTop.current = node.scrollTop;
    });
  }, []);

  useEffect(() => {
    requestAnimationFrame(() => {
      updatePinned(true);
      scrollLatest(true);
    });
  }, [detail.chat_id, draft?.question, scrollLatest, updatePinned]);

  useEffect(() => {
    scrollLatest();
  }, [draft?.activity, draft?.text, detail.turns.length, scrollLatest]);

  function handleScroll(event: UIEvent<HTMLDivElement>) {
    const node = event.currentTarget;
    const distance = node.scrollHeight - node.scrollTop - node.clientHeight;
    const movedUp = node.scrollTop < previousTop.current;
    if (movedUp && distance > PIN_THRESHOLD) updatePinned(false);
    else if (distance <= PIN_THRESHOLD) updatePinned(true);
    previousTop.current = node.scrollTop;
  }

  return (
    <section className="transcript" aria-label="Conversation transcript">
      <div
        ref={scrollRef}
        onScroll={handleScroll}
        className="transcript__scroll"
        data-testid="transcript-scroll"
      >
        {detail.turns.length === 0 && !draft ? (
          <div className="transcript__empty">
            <h2>Ask from your local knowledge base</h2>
            <p>
              Answers are verified against retrieved source snapshots before they
              become part of this conversation.
            </p>
          </div>
        ) : null}
        {detail.turns.map((turn) => (
          <TurnView
            key={`${turn.turn_id}-${turn.attempt}-${turn.updated_at}`}
            chatId={detail.chat_id}
            turn={turn}
            activityDraft={
              draft?.final && turn.turn_id === latest?.turn_id ? draft : null
            }
            canRetry={
              turn.turn_id === latest?.turn_id &&
              [
                "failed",
                "interrupted",
                "length_limited",
                "citation_failed",
              ].includes(turn.status) &&
              !draft
            }
            onRetry={onRetry}
            retrying={phase === "starting" && Boolean(draft?.isRetry)}
          />
        ))}
        {draft && !draft.final ? (
          <PendingTurnView draft={draft} phase={phase} />
        ) : null}
      </div>
      {!pinned ? (
        <button
          type="button"
          onClick={() => {
            updatePinned(true);
            scrollLatest(true);
          }}
          className="jump-latest min-h-11"
        >
          Jump latest
        </button>
      ) : null}
    </section>
  );
}
