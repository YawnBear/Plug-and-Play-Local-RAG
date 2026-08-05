"use client";

import { useRef, useState, type FormEvent } from "react";

import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { NativeDialog } from "@/components/ui/native-dialog";

import type {
  DocumentSummary,
  LibraryTreeNode,
  NodeMovePreview,
} from "./contracts";

function message(error: unknown): string {
  return error instanceof Error ? error.message : "Document operation failed.";
}

function folderOptions(
  tree: readonly LibraryTreeNode[],
  depth = 0,
): Array<{ node: LibraryTreeNode; depth: number }> {
  return tree.flatMap((node) => [
    { node, depth },
    ...folderOptions(node.children, depth + 1),
  ]);
}

export function NodeDialogs({
  tree,
  document,
  busy,
  onPatch,
  onPreviewMove,
  onDelete,
  requestedAction = null,
  onRequestedActionHandled,
  showTriggers = true,
}: {
  tree: readonly LibraryTreeNode[];
  document: DocumentSummary;
  busy: boolean;
  onPatch: (
    nodeId: string,
    patch: {
      name?: string;
      parent_id?: string | null;
      preview_id?: string;
      impact_digest?: string;
    },
  ) => Promise<unknown>;
  onPreviewMove: (
    nodeId: string,
    parentId: string | null,
  ) => Promise<NodeMovePreview>;
  onDelete: (document: DocumentSummary) => Promise<unknown>;
  requestedAction?: "rename" | "move" | "delete" | null;
  onRequestedActionHandled?: () => void;
  showTriggers?: boolean;
}) {
  const [dialog, setDialog] = useState<"rename" | "move" | "delete" | null>(
    requestedAction,
  );
  const [name, setName] = useState(document.display_name);
  const [parentId, setParentId] = useState(document.parent_id ?? "");
  const [working, setWorking] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [movePreview, setMovePreview] = useState<NodeMovePreview | null>(null);
  const [confirmation, setConfirmation] = useState("");
  const [deleteConfirmation, setDeleteConfirmation] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);

  function open(next: typeof dialog) {
    if (working) return;
    setError(null);
    setName(document.display_name);
    setParentId(document.parent_id ?? "");
    setMovePreview(null);
    setConfirmation("");
    setDeleteConfirmation("");
    setDialog(next);
  }

  function close() {
    setDialog(null);
    if (requestedAction) onRequestedActionHandled?.();
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    setWorking(true);
    setError(null);
    try {
      if (dialog === "rename") {
        await onPatch(document.node_id, { name });
      }
      if (dialog === "move") {
        if (!movePreview) {
          setMovePreview(
            await onPreviewMove(document.node_id, parentId || null),
          );
          return;
        }
        if (confirmation !== document.display_name) {
          setError(`Type ${document.display_name} exactly to confirm.`);
          return;
        }
        await onPatch(document.node_id, {
          parent_id: parentId || null,
          preview_id: movePreview.preview_id,
          impact_digest: movePreview.impact_digest,
        });
      }
      close();
    } catch (reason) {
      setError(message(reason));
    } finally {
      setWorking(false);
    }
  }

  async function confirmDelete() {
    if (deleteConfirmation !== document.display_name) {
      setError(`Type ${document.display_name} exactly to confirm.`);
      return;
    }
    setWorking(true);
    setError(null);
    try {
      await onDelete(document);
      close();
    } catch (reason) {
      setError(message(reason));
    } finally {
      setWorking(false);
    }
  }

  return (
    <div className="flex flex-wrap gap-2">
      {showTriggers ? (
        <>
          <button
            type="button"
            onClick={() => open("rename")}
            disabled={busy || working}
            className="min-h-11 rounded-[4px] border border-[var(--hairline-strong)] px-4"
          >
            Rename
          </button>
          <button
            type="button"
            onClick={() => open("move")}
            disabled={busy || working}
            className="min-h-11 rounded-[4px] border border-[var(--hairline-strong)] px-4"
          >
            Move
          </button>
          <button
            type="button"
            onClick={() => open("delete")}
            disabled={busy || working}
            className="kb-action--danger min-h-11 rounded-[4px] border border-[var(--hairline-strong)] px-4"
          >
            Delete
          </button>
        </>
      ) : null}

      <NativeDialog
        open={dialog === "rename" || dialog === "move"}
        onClose={() => {
          if (!working) close();
        }}
        title={dialog === "rename" ? "Rename document" : "Move document"}
        initialFocusRef={dialog === "rename" ? inputRef : undefined}
        closeDisabled={working}
      >
        <form onSubmit={submit} className="mt-4 min-w-0 w-full">
          {dialog === "rename" ? (
            <>
              <label
                htmlFor="document-name"
                className="block text-sm font-medium"
              >
                Document name
              </label>
              <input
                ref={inputRef}
                id="document-name"
                value={name}
                onChange={(event) => setName(event.target.value)}
                maxLength={255}
                disabled={working}
                className="mt-1 min-h-11 w-full rounded-[4px] border border-[var(--hairline)] bg-[var(--surface-soft)] px-3"
              />
            </>
          ) : (
            <>
              <label
                htmlFor="document-parent"
                className="block text-sm font-medium"
              >
                Destination
              </label>
              <select
                id="document-parent"
                value={parentId}
                onChange={(event) => {
                  setParentId(event.target.value);
                  setMovePreview(null);
                  setConfirmation("");
                }}
                disabled={working}
                className="mt-1 min-h-11 w-full rounded-[4px] border border-[var(--hairline)] bg-[var(--surface-soft)] px-3"
              >
                <option value="">Root</option>
                {folderOptions(tree).map(({ node, depth }) => (
                  <option key={node.node_id} value={node.node_id}>
                    {"— ".repeat(depth)}
                    {node.name}
                  </option>
                ))}
              </select>
              {movePreview ? (
                <div className="mt-4 rounded-lg border border-[var(--border)] p-3">
                  <p className="m-0">
                    This move affects{" "}
                    <strong>{movePreview.impact.user_count}</strong> account
                    {movePreview.impact.user_count === 1 ? "" : "s"} and{" "}
                    <strong>{movePreview.impact.document_count}</strong>{" "}
                    document
                    {movePreview.impact.document_count === 1 ? "" : "s"}.
                  </p>
                  <label htmlFor="document-move-confirm" className="mt-3 block">
                    Type <strong>{document.display_name}</strong> to confirm
                  </label>
                  <input
                    id="document-move-confirm"
                    value={confirmation}
                    onChange={(event) => setConfirmation(event.target.value)}
                    className="mt-1 w-full rounded-lg border border-[var(--border)] bg-[var(--background)] px-3"
                  />
                </div>
              ) : null}
            </>
          )}
          {error ? (
            <p role="alert" className="mt-3 text-[var(--danger)]">
              {error}
            </p>
          ) : null}
          <div className="mt-5 flex flex-wrap justify-end gap-3">
            <button
              type="button"
              onClick={close}
              disabled={working}
              className="min-h-11 rounded-[4px] border border-[var(--hairline-strong)] px-4"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={working || (dialog === "rename" && !name.trim())}
              className="min-h-11 rounded-[4px] border border-[var(--ink)] bg-[var(--ink)] px-4 text-[var(--canvas)]"
            >
              {working
                ? "Working…"
                : dialog === "move" && !movePreview
                  ? "Review move"
                  : "Save"}
            </button>
          </div>
        </form>
      </NativeDialog>

      <ConfirmDialog
        open={dialog === "delete"}
        onClose={() => {
          if (!working) close();
        }}
        onConfirm={() => void confirmDelete()}
        title="Delete document?"
        confirmLabel="Delete document"
        busy={working}
      >
        <p>
          Delete <strong>{document.display_name}</strong>, its indexed chunks,
          and its stored original? Historical citation snapshots remain, but
          this file cannot be recovered here.
        </p>
        <label htmlFor="document-delete-confirm" className="block">
          Type <strong>{document.display_name}</strong> to confirm
        </label>
        <input
          id="document-delete-confirm"
          value={deleteConfirmation}
          onChange={(event) => setDeleteConfirmation(event.target.value)}
          className="mt-2 w-full rounded-lg border border-[var(--border)] bg-[var(--background)] px-3"
        />
        {error ? (
          <p role="alert" className="text-[var(--danger)]">
            {error}
          </p>
        ) : null}
      </ConfirmDialog>
    </div>
  );
}
