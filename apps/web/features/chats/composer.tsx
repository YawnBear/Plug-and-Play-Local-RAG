"use client";

import {
  useEffect,
  useId,
  useRef,
  useState,
  type FormEvent,
  type KeyboardEvent,
} from "react";

import { Icon } from "@/components/shell/icons";
import { Tooltip } from "@/components/ui/tooltip";

import { codePointLength } from "./state";

export function Composer({
  disabled,
  generating,
  onSend,
  onStop,
}: {
  disabled: boolean;
  generating: boolean;
  onSend: (question: string) => Promise<boolean>;
  onStop: () => void;
}) {
  const [question, setQuestion] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const helpId = useId();
  const length = codePointLength(question.trim());
  const invalid = length > 2_000;

  useEffect(() => {
    const textarea = textareaRef.current;
    if (!textarea) return;
    textarea.style.height = "0px";
    textarea.style.height = `${Math.min(textarea.scrollHeight, 192)}px`;
  }, [question]);

  async function submit(event?: FormEvent) {
    event?.preventDefault();
    if (disabled || generating || submitting) return;
    setSubmitting(true);
    try {
      const sent = await onSend(question);
      if (sent) setQuestion("");
    } finally {
      setSubmitting(false);
    }
  }

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      void submit();
    }
  }

  return (
    <form onSubmit={submit} className="chat-composer">
      <div className="chat-composer__surface">
        <label htmlFor="chat-question" className="sr-only">
          Question
        </label>
        <textarea
          ref={textareaRef}
          id="chat-question"
          value={question}
          onChange={(event) => setQuestion(event.target.value)}
          onKeyDown={onKeyDown}
          disabled={disabled || generating}
          aria-describedby={helpId}
          aria-invalid={invalid || undefined}
          rows={1}
          className="chat-composer__input"
          placeholder="Ask your knowledge base"
        />
        <div className="chat-composer__footer">
          <p
            id={helpId}
            className={`chat-composer__help ${
              invalid ? "chat-composer__help--invalid" : ""
            }`}
          >
            Enter to send · Shift+Enter for a new line ·{" "}
            {length.toLocaleString()}/2,000
          </p>
          {generating ? (
            <Tooltip content="Stop generating"><button
              type="button"
              onClick={onStop}
              className="chat-composer__send"
              aria-label="Stop"
            >
              <Icon name="stop" />
            </button></Tooltip>
          ) : (
            <Tooltip content="Send"><button
              type="submit"
              disabled={disabled || submitting || length === 0 || invalid}
              className="chat-composer__send"
              aria-label={submitting ? "Sending" : "Send"}
            >
              <Icon name="send" />
            </button></Tooltip>
          )}
        </div>
      </div>
    </form>
  );
}
