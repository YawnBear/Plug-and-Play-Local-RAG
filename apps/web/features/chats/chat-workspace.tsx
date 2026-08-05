"use client";

import { useMemo } from "react";

import { useSidebarContent } from "@/components/shell/protected-shell";
import { ErrorState, LoadingState } from "@/components/ui/async-state";
import { homeRoute } from "@/lib/route-state";

import { Composer } from "./composer";
import { ConversationList } from "./conversation-list";
import { ScopeEditor } from "./scope-editor";
import { Transcript } from "./transcript";
import { useChatWorkspace } from "./use-chat-workspace";

export function ChatWorkspace({
  initialChatId,
  invalidChatRoute,
}: {
  initialChatId: string | null;
  invalidChatRoute: boolean;
}) {
  const controller = useChatWorkspace({
    initialChatId,
    invalidChatRoute,
  });
  const { state, generating } = controller;
  const {
    actionError,
    openChat,
    refreshChats,
    remove,
    rename,
  } = controller;
  const interactionBlocked = generating || state.detailRefreshing;
  const routeKey = homeRoute(state.activeChatId);

  const conversations = useMemo(
    () => ({
      render: (onNavigate?: () => void) => (
        <ConversationList
          key={routeKey}
          chats={state.chats}
          activeChatId={state.activeChatId}
          loading={state.chatsLoading}
          busy={interactionBlocked}
          error={state.chatsError}
          actionError={actionError}
          onRetry={refreshChats}
          onOpen={(chatId) => {
            openChat(chatId);
            onNavigate?.();
          }}
          onRename={rename}
          onDelete={remove}
        />
      ),
    }),
    [
      actionError,
      interactionBlocked,
      openChat,
      refreshChats,
      remove,
      rename,
      routeKey,
      state.activeChatId,
      state.chats,
      state.chatsError,
      state.chatsLoading,
    ],
  );
  useSidebarContent(conversations);

  return (
    <div className="chat-workspace">
      <div className="chat-workspace__inner">
        <div className="chat-workspace__column">
          {!state.activeChatId ? (
            <section className="chat-empty" aria-labelledby="chat-empty-title">
              <div className="chat-empty__copy">
                <p className="chat-empty__eyebrow">Local knowledge</p>
                <h1 id="chat-empty-title">Ask your documents</h1>
                <p>
                  Start with a question. Answers stay grounded in the sources
                  available to your account.
                </p>
                {state.routeNotice ? <p role="status">{state.routeNotice}</p> : null}
                {state.error || actionError ? (
                  <p role="alert">{state.error ?? actionError}</p>
                ) : null}
              </div>
              <Composer
                disabled={false}
                generating={generating}
                onSend={controller.send}
                onStop={() => void controller.stop()}
              />
            </section>
          ) : null}

          {state.activeChatId && state.phase === "loading" && !state.detail ? (
            <LoadingState label="Loading conversation…" />
          ) : null}

          {state.activeChatId &&
          !state.detail &&
          state.phase === "failed" ? (
            <ErrorState
              message={
                state.error ?? "The conversation could not be loaded."
              }
              onRetry={() => {
                if (state.activeChatId) {
                  void controller.loadDetail(state.activeChatId);
                }
              }}
              retryLabel="Reload conversation"
            />
          ) : null}

          {state.detail ? (
            <>
              <header className="conversation-header">
                <div className="conversation-header__copy">
                  <h1>
                    {state.detail.title}
                  </h1>
                  <p>
                    Local evidence · Only committed final answers are verified
                  </p>
                </div>
                <ScopeEditor
                  key={routeKey}
                  detail={state.detail}
                  generating={interactionBlocked}
                  onSave={controller.saveScope}
                />
              </header>
              {state.error ? (
                <div className="workspace-error">
                  <p role="alert">
                    {state.error}
                  </p>
                  <button
                    type="button"
                    disabled={state.detailRefreshing}
                    onClick={() => {
                      if (state.activeChatId) {
                        void controller.loadDetail(state.activeChatId);
                      }
                    }}
                    className="button-secondary"
                  >
                    {state.detailRefreshing ? "Refreshing…" : "Refresh"}
                  </button>
                </div>
              ) : null}
              <Transcript
                detail={state.detail}
                draft={state.draft}
                phase={state.phase}
                onRetry={() => void controller.retry()}
              />
              <Composer
                disabled={!state.detail || state.detailRefreshing}
                generating={generating}
                onSend={controller.send}
                onStop={() => void controller.stop()}
              />
            </>
          ) : null}
        </div>
      </div>
    </div>
  );
}
