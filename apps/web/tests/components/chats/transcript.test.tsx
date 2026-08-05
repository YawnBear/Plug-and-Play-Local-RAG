import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { Transcript } from "@/features/chats/transcript";

import { chatDetail, completedTurn, streamSource } from "../../chat-fixtures";

describe("transcript scrolling", () => {
  beforeEach(() => {
    vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) => {
      callback(0);
      return 1;
    });
    vi.stubGlobal(
      "matchMedia",
      vi.fn().mockReturnValue({ matches: true }),
    );
    Element.prototype.scrollTo = vi.fn();
  });

  it("unpins on manual upward scrolling and offers a 44px jump action", () => {
    render(
      <Transcript
        detail={chatDetail(undefined, [completedTurn()])}
        draft={null}
        phase="verified"
        onRetry={vi.fn()}
      />,
    );
    const transcript = screen.getByTestId("transcript-scroll");
    Object.defineProperties(transcript, {
      scrollHeight: { configurable: true, value: 1_000 },
      clientHeight: { configurable: true, value: 300 },
      scrollTop: { configurable: true, writable: true, value: 650 },
    });
    fireEvent.scroll(transcript);
    (transcript as HTMLDivElement).scrollTop = 400;
    fireEvent.scroll(transcript);

    const jump = screen.getByRole("button", { name: "Jump latest" });
    expect(jump).toBeVisible();
    expect(jump.className).toContain("min-h-11");
  });

  it("follows token updates without making token text live or self-unpinning", () => {
    const draft = {
      question: "Question",
      isRetry: false,
      turnId: null,
      sources: [streamSource],
      activity: [],
      reasoningActive: false,
      reasoningComplete: false,
      reasoningTruncated: false,
      text: "first",
      final: null,
    };
    const { rerender } = render(
      <Transcript
        detail={chatDetail()}
        draft={draft}
        phase="streaming-draft"
        onRetry={vi.fn()}
      />,
    );
    const transcript = screen.getByTestId("transcript-scroll");
    Object.defineProperties(transcript, {
      scrollHeight: { configurable: true, value: 1_000 },
      clientHeight: { configurable: true, value: 300 },
      scrollTop: { configurable: true, writable: true, value: 650 },
    });
    fireEvent.scroll(transcript);
    vi.mocked(Element.prototype.scrollTo).mockClear();

    rerender(
      <Transcript
        detail={chatDetail()}
        draft={{
          ...draft,
          activity: [
            { kind: "thinking" as const, text: "partial reasoning" },
          ],
          reasoningActive: true,
          text: "first token",
        }}
        phase="streaming-draft"
        onRetry={vi.fn()}
      />,
    );

    expect(Element.prototype.scrollTo).toHaveBeenCalledWith({
      top: 1_000,
      behavior: "auto",
    });
    expect(screen.queryByRole("button", { name: "Jump latest" })).toBeNull();
    expect(screen.getByText("partial reasoning")).toBeVisible();
    const tokenText = screen.getByText("first token");
    expect(tokenText.closest("[aria-live]")).toBeNull();
    const status = screen
      .getAllByText("Draft · Unverified")[0]
      ?.closest("[aria-live]");
    expect(status).not.toBeNull();
    if (!status) throw new Error("live draft status is missing");
    expect(status).toHaveAttribute("aria-live", "polite");
    expect(status).toHaveAttribute("aria-atomic", "true");
    expect(status).toHaveClass("trust-status", "trust-status--pending");
    expect(status).not.toHaveClass("trust-pill");
  });

  it("rechecks pinned state when a queued token scroll runs", () => {
    const frames: FrameRequestCallback[] = [];
    vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) => {
      frames.push(callback);
      return frames.length;
    });
    vi.mocked(window.matchMedia).mockReturnValue({
      matches: false,
    } as MediaQueryList);
    const draft = {
      question: "Question",
      isRetry: false,
      turnId: null,
      sources: [],
      activity: [],
      reasoningActive: false,
      reasoningComplete: false,
      reasoningTruncated: false,
      text: "first",
      final: null,
    };
    const { rerender } = render(
      <Transcript
        detail={chatDetail()}
        draft={draft}
        phase="streaming-draft"
        onRetry={vi.fn()}
      />,
    );
    const transcript = screen.getByTestId("transcript-scroll");
    Object.defineProperties(transcript, {
      scrollHeight: { configurable: true, value: 1_000 },
      clientHeight: { configurable: true, value: 300 },
      scrollTop: { configurable: true, writable: true, value: 650 },
    });
    while (frames.length > 0) {
      frames.splice(0).forEach((callback) => callback(0));
    }
    vi.mocked(Element.prototype.scrollTo).mockClear();

    rerender(
      <Transcript
        detail={chatDetail()}
        draft={{ ...draft, text: "queued token" }}
        phase="streaming-draft"
        onRetry={vi.fn()}
      />,
    );
    (transcript as HTMLDivElement).scrollTop = 400;
    fireEvent.scroll(transcript);
    frames.splice(0).forEach((callback) => callback(0));

    expect(Element.prototype.scrollTo).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Jump latest" })).toBeVisible();
  });

  it("uses instant scrolling when reduced motion is not requested", () => {
    vi.mocked(window.matchMedia).mockReturnValue({
      matches: false,
    } as MediaQueryList);
    render(
      <Transcript
        detail={chatDetail(undefined, [completedTurn()])}
        draft={null}
        phase="verified"
        onRetry={vi.fn()}
      />,
    );
    expect(Element.prototype.scrollTo).toHaveBeenCalledWith(
      expect.objectContaining({ behavior: "instant" }),
    );
  });

  it("keeps reconciled Activity above the authoritative final answer", () => {
    const turn = completedTurn();
    const draft = {
      question: turn.question,
      isRetry: false,
      turnId: turn.turn_id,
      sources: [streamSource],
      activity: [{ kind: "thinking" as const, text: "Ephemeral reasoning" }],
      reasoningActive: false,
      reasoningComplete: true,
      reasoningTruncated: false,
      text: "Draft answer",
      final: {
        chat_id: "11111111-1111-4111-8111-111111111111",
        turn_id: turn.turn_id,
        seq: 12,
        answer: turn.final_answer!,
        insufficient_context: false,
        citations: [streamSource],
      },
    };
    render(
      <Transcript
        detail={chatDetail(undefined, [turn])}
        draft={draft}
        phase="verified"
        onRetry={vi.fn()}
      />,
    );

    const activity = screen.getByText("Activity");
    const verified = screen
      .getByText("Verified", { exact: true })
      .closest(".trust-status");
    const answer = screen.getByRole("button", {
      name: "S1, Research.pdf, page 2",
    }).closest("p");
    expect(verified).not.toBeNull();
    expect(verified).toHaveClass("trust-status", "trust-status--verified");
    expect(answer).not.toBeNull();
    if (!answer) throw new Error("verified answer paragraph is missing");
    expect(activity.compareDocumentPosition(answer)).toBe(
      Node.DOCUMENT_POSITION_FOLLOWING,
    );
  });

  it("labels a length-limited partial as unverified and offers continuation", () => {
    const onRetry = vi.fn();
    const turn = completedTurn({
      status: "length_limited",
      final_answer: null,
      partial_answer: "| Name | Year |",
      error: "response reached generation limit",
      citations: [],
      citation_ranks: [],
      completed_at: null,
    });

    render(
      <Transcript
        detail={chatDetail(undefined, [turn])}
        draft={null}
        phase="length-limited"
        onRetry={onRetry}
      />,
    );

    expect(screen.getByText(/Unverified/)).toBeVisible();
    expect(screen.getByText(/has not been citation-verified/)).toBeVisible();
    fireEvent.click(
      screen.getByRole("button", { name: "Continue response" }),
    );
    expect(onRetry).toHaveBeenCalledOnce();
  });

  it("preserves a citation-failed draft and offers citation retry", () => {
    const onRetry = vi.fn();
    const turn = completedTurn({
      status: "citation_failed",
      final_answer: null,
      partial_answer: "| Name | Year |\n|---|---|\n| KitaHack | 2026 |",
      error: "citation validation failed",
      citations: [],
      citation_ranks: [],
      completed_at: null,
    });

    render(
      <Transcript
        detail={chatDetail(undefined, [turn])}
        draft={null}
        phase="citation-failed"
        onRetry={onRetry}
      />,
    );

    expect(screen.getByText(/Citation error/)).toBeVisible();
    expect(screen.getByText(/content is preserved/i)).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Retry citations" }));
    expect(onRetry).toHaveBeenCalledOnce();
  });
});
