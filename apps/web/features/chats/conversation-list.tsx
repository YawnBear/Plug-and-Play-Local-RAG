"use client";

import {
  useId,
  useMemo,
  useRef,
  useState,
  type FormEvent,
} from "react";

import { Icon } from "@/components/shell/icons";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { NativeDialog } from "@/components/ui/native-dialog";
import { Tooltip } from "@/components/ui/tooltip";

import type { ChatSummary } from "./contracts";

type DateGroup = "Today" | "Yesterday" | "Previous 7 days" | "Older";

const GROUPS: readonly DateGroup[] = [
  "Today",
  "Yesterday",
  "Previous 7 days",
  "Older",
];

function dateGroup(value: string, now = new Date()): DateGroup {
  const updated = new Date(value);
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const target = new Date(
    updated.getFullYear(),
    updated.getMonth(),
    updated.getDate(),
  );
  const days = Math.floor((today.getTime() - target.getTime()) / 86_400_000);
  if (days <= 0) return "Today";
  if (days === 1) return "Yesterday";
  if (days <= 7) return "Previous 7 days";
  return "Older";
}

export function ConversationList({
  chats,
  activeChatId,
  loading,
  busy,
  error,
  actionError,
  onRetry,
  onOpen,
  onRename,
  onDelete,
}: {
  chats: readonly ChatSummary[];
  activeChatId: string | null;
  loading: boolean;
  busy: boolean;
  error: string | null;
  actionError: string | null;
  onRetry: () => Promise<unknown>;
  onOpen: (chatId: string) => void;
  onRename: (chatId: string, title: string) => Promise<boolean>;
  onDelete: (chatId: string) => Promise<boolean>;
}) {
  const [query, setQuery] = useState("");
  const [renaming, setRenaming] = useState<ChatSummary | null>(null);
  const [deleting, setDeleting] = useState<ChatSummary | null>(null);
  const [title, setTitle] = useState("");
  const [mutating, setMutating] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const instanceId = useId();
  const titleId = `${instanceId}-conversations-title`;
  const inputId = `${instanceId}-chat-title`;

  const grouped = useMemo(() => {
    const normalized = query.trim().toLocaleLowerCase();
    const filtered = normalized
      ? chats.filter((chat) =>
          chat.title.toLocaleLowerCase().includes(normalized),
        )
      : chats;
    const buckets = new Map<DateGroup, ChatSummary[]>();
    for (const chat of filtered) {
      const group = dateGroup(chat.updated_at);
      buckets.set(group, [...(buckets.get(group) ?? []), chat]);
    }
    return GROUPS.flatMap((group) => {
      const items = buckets.get(group);
      return items?.length ? [{ group, items }] : [];
    });
  }, [chats, query]);

  async function submitRename(event: FormEvent) {
    event.preventDefault();
    if (!renaming || !title.trim()) return;
    setMutating(true);
    const saved = await onRename(renaming.chat_id, title);
    setMutating(false);
    if (saved) setRenaming(null);
  }

  async function confirmDelete() {
    if (!deleting) return;
    setMutating(true);
    const removed = await onDelete(deleting.chat_id);
    setMutating(false);
    if (removed) setDeleting(null);
  }

  return (
    <section aria-labelledby={titleId} className="conversation-history">
      <div className="conversation-history__heading">
        <h2 id={titleId}>Conversations</h2>
      </div>
      <label className="conversation-search">
        <span className="sr-only">Search conversations by title</span>
        <Icon name="search" />
        <input
          type="search"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="Search chats"
        />
      </label>
      {actionError ? (
        <p role="alert" className="conversation-history__error">
          {actionError}
        </p>
      ) : null}
      {error ? (
        <div className="conversation-history__error">
          <p role="alert">{error}</p>
          <button
            type="button"
            onClick={() => void onRetry()}
            disabled={loading}
            className="button-secondary"
            aria-label="Retry conversations"
          >
            Retry
          </button>
        </div>
      ) : null}
      {loading ? (
        <p role="status" className="conversation-history__empty">
          Loading chats…
        </p>
      ) : null}
      {!loading && chats.length === 0 ? (
        <p className="conversation-history__empty">
          Your conversations will appear here.
        </p>
      ) : null}
      {!loading && chats.length > 0 && grouped.length === 0 ? (
        <p className="conversation-history__empty">
          No chat titles match “{query}”.
        </p>
      ) : null}
      <div className="conversation-groups">
        {grouped.map(({ group, items }) => (
          <section key={group} aria-labelledby={`${instanceId}-${group}`}>
            <h3 id={`${instanceId}-${group}`} className="conversation-group__title">
              {group}
            </h3>
            <ol className="conversation-list">
              {items.map((chat) => {
                const active = chat.chat_id === activeChatId;
                return (
                  <li key={chat.chat_id} className="conversation-item">
                    <Tooltip content={chat.title}><button
                      type="button"
                      onClick={() => onOpen(chat.chat_id)}
                      disabled={busy}
                      aria-current={active ? "page" : undefined}
                      className="conversation-item__open"
                    >
                      <span>{chat.title}</span>
                    </button></Tooltip>
                    <div className="conversation-item__actions">
                      <button
                        type="button"
                        className="conversation-item__action conversation-item__action--rename"
                        onClick={() => {
                          setRenaming(chat);
                          setTitle(chat.title);
                        }}
                        disabled={busy}
                        aria-label={`Rename ${chat.title}`}
                      >
                        Rename
                      </button>
                      <button
                        type="button"
                        className="conversation-item__action conversation-item__action--delete"
                        onClick={() => setDeleting(chat)}
                        disabled={busy}
                        aria-label={`Delete ${chat.title}`}
                      >
                        Delete
                      </button>
                    </div>
                  </li>
                );
              })}
            </ol>
          </section>
        ))}
      </div>

      <NativeDialog
        open={renaming !== null}
        onClose={() => setRenaming(null)}
        title="Rename conversation"
        initialFocusRef={inputRef}
      >
        <form onSubmit={submitRename} className="dialog-form">
          <label htmlFor={inputId}>Conversation title</label>
          <input
            ref={inputRef}
            id={inputId}
            value={title}
            onChange={(event) => setTitle(event.target.value)}
            maxLength={255}
            disabled={mutating}
          />
          <div className="dialog-actions">
            <button
              type="button"
              onClick={() => setRenaming(null)}
              disabled={mutating}
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={mutating || !title.trim()}
              className="button-primary"
            >
              {mutating ? "Saving…" : "Save title"}
            </button>
          </div>
        </form>
      </NativeDialog>

      <ConfirmDialog
        open={deleting !== null}
        onClose={() => setDeleting(null)}
        onConfirm={() => void confirmDelete()}
        title="Delete conversation?"
        confirmLabel="Delete conversation"
        busy={mutating}
      >
        <p>
          Delete <strong>{deleting?.title}</strong> and its complete turn
          history? This cannot be undone.
        </p>
      </ConfirmDialog>
    </section>
  );
}
