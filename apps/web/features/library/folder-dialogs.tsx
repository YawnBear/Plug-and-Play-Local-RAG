"use client";

import { useRef, useState, type FormEvent } from "react";

import { Icon } from "@/components/shell/icons";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { NativeDialog } from "@/components/ui/native-dialog";

import type {
  LibraryNode,
  LibraryTreeNode,
  NodeMovePreview,
} from "./contracts";
import { collectTreeNodeIds, findTreeNode } from "./tree-model";

function message(error: unknown): string {
  return error instanceof Error ? error.message : "Folder operation failed.";
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

export function FolderDialogs({
  tree,
  currentFolder,
  busy,
  onCreate,
  onPatch,
  onPreviewMove,
  onDelete,
  requestedAction = null,
  onRequestedActionHandled,
  showTriggers = true,
}: {
  tree: readonly LibraryTreeNode[];
  currentFolder: LibraryNode | null;
  busy: boolean;
  onCreate: (name: string) => Promise<unknown>;
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
  onDelete: (folder: LibraryNode) => Promise<unknown>;
  requestedAction?: "create" | "rename" | "move" | "delete" | null;
  onRequestedActionHandled?: () => void;
  showTriggers?: boolean;
}) {
  const [dialog, setDialog] = useState<
    "create" | "rename" | "move" | "delete" | null
  >(requestedAction);
  const [name, setName] = useState("");
  const [parentId, setParentId] = useState<string>("");
  const [working, setWorking] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [movePreview, setMovePreview] = useState<NodeMovePreview | null>(null);
  const [confirmation, setConfirmation] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);
  const currentTreeNode = currentFolder
    ? findTreeNode(tree, currentFolder.node_id)
    : null;
  const disallowed = currentTreeNode
    ? new Set([
        currentTreeNode.node_id,
        ...collectTreeNodeIds(currentTreeNode.children),
      ])
    : new Set<string>();

  function open(next: typeof dialog) {
    if (working) return;
    setError(null);
    setDialog(next);
    setName(next === "rename" ? (currentFolder?.name ?? "") : "");
    setParentId(currentFolder?.parent_id ?? "");
    setMovePreview(null);
    setConfirmation("");
  }

  function close() {
    setDialog(null);
    if (requestedAction) onRequestedActionHandled?.();
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!dialog) return;
    setWorking(true);
    setError(null);
    try {
      if (dialog === "create") await onCreate(name);
      if (dialog === "rename" && currentFolder) {
        await onPatch(currentFolder.node_id, { name });
      }
      if (dialog === "move" && currentFolder) {
        if (!movePreview) {
          setMovePreview(
            await onPreviewMove(currentFolder.node_id, parentId || null),
          );
          return;
        }
        if (confirmation !== currentFolder.name) {
          setError(`Type ${currentFolder.name} exactly to confirm.`);
          return;
        }
        await onPatch(currentFolder.node_id, {
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
    if (!currentFolder) return;
    if (confirmation !== currentFolder.name) {
      setError(`Type ${currentFolder.name} exactly to confirm.`);
      return;
    }
    setWorking(true);
    setError(null);
    try {
      await onDelete(currentFolder);
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
            onClick={() => open("create")}
            disabled={busy || working}
            className="button-primary"
          >
            <Icon name="new" />
            New folder
          </button>
          {currentFolder ? (
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
        </>
      ) : null}

      <NativeDialog
        open={dialog === "create" || dialog === "rename" || dialog === "move"}
        onClose={() => {
          if (!working) close();
        }}
        title={
          dialog === "create"
            ? "Create folder"
            : dialog === "rename"
              ? "Rename folder"
              : "Move folder"
        }
        initialFocusRef={dialog === "move" ? undefined : inputRef}
        closeDisabled={working}
      >
        <form onSubmit={submit} className="mt-4 min-w-0 w-full">
          {dialog === "create" || dialog === "rename" ? (
            <>
              <label htmlFor="folder-name" className="block text-sm font-medium">
                Folder name
              </label>
              <input
                ref={inputRef}
                id="folder-name"
                value={name}
                onChange={(event) => setName(event.target.value)}
                maxLength={255}
                disabled={working}
                className="mt-1 min-h-11 w-full rounded-[4px] border border-[var(--hairline)] bg-[var(--surface-soft)] px-3"
              />
            </>
          ) : null}
          {dialog === "move" && currentFolder ? (
            <>
              <label
                htmlFor="folder-parent"
                className="block text-sm font-medium"
              >
                Destination
              </label>
              <select
                id="folder-parent"
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
                  <option
                    key={node.node_id}
                    value={node.node_id}
                    disabled={disallowed.has(node.node_id)}
                  >
                    {"— ".repeat(depth)}
                    {node.name}
                  </option>
                ))}
              </select>
              <p className="mt-2 text-sm text-[var(--mute)]">
                A folder cannot move into itself or one of its descendants.
              </p>
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
                  <label htmlFor="folder-move-confirm" className="mt-3 block">
                    Type <strong>{currentFolder.name}</strong> to confirm
                  </label>
                  <input
                    id="folder-move-confirm"
                    value={confirmation}
                    onChange={(event) => setConfirmation(event.target.value)}
                    className="mt-1 w-full rounded-lg border border-[var(--border)] bg-[var(--background)] px-3"
                  />
                </div>
              ) : null}
            </>
          ) : null}
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
              disabled={
                working ||
                ((dialog === "create" || dialog === "rename") && !name.trim())
              }
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
        onClose={close}
        onConfirm={() => void confirmDelete()}
        title="Delete empty folder?"
        confirmLabel="Delete folder"
        busy={working}
      >
        <p>
          Delete <strong>{currentFolder?.name}</strong>? Only empty folders can
          be deleted. This cannot be undone.
        </p>
        <label htmlFor="folder-delete-confirm" className="block">
          Type <strong>{currentFolder?.name}</strong> to confirm
        </label>
        <input
          id="folder-delete-confirm"
          value={confirmation}
          onChange={(event) => setConfirmation(event.target.value)}
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
