"use client";

import { useEffect, useMemo, useRef, useState } from "react";

import { NativeDialog } from "@/components/ui/native-dialog";
import { Icon } from "@/components/shell/icons";
import {
  browseLibrary,
  getLibraryTree,
} from "@/features/library/api";
import type {
  LibraryBrowse,
  LibraryNode,
  LibraryTreeNode,
} from "@/features/library/contracts";

import type { ChatDetail } from "./contracts";

const ROOT = "__root__";

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "Unable to update scope.";
}

function folderIndex(tree: readonly LibraryTreeNode[]) {
  const parent = new Map<string, string | null>();
  const visit = (nodes: readonly LibraryTreeNode[]) => {
    for (const node of nodes) {
      parent.set(node.node_id, node.parent_id);
      visit(node.children);
    }
  };
  visit(tree);
  return { parent };
}

function hasSelectedAncestor(
  parentId: string | null,
  selected: ReadonlySet<string>,
  parents: ReadonlyMap<string, string | null>,
): boolean {
  let current = parentId;
  while (current) {
    if (selected.has(current)) return true;
    current = parents.get(current) ?? null;
  }
  return false;
}

function descendantFolders(node: LibraryTreeNode): Set<string> {
  const ids = new Set<string>();
  const visit = (children: readonly LibraryTreeNode[]) => {
    for (const child of children) {
      ids.add(child.node_id);
      visit(child.children);
    }
  };
  visit(node.children);
  return ids;
}

export function ScopeEditor({
  detail,
  generating,
  onSave,
}: {
  detail: ChatDetail;
  generating: boolean;
  onSave: (
    mode: "all_ready" | "selected",
    nodeIds: string[],
  ) => Promise<ChatDetail>;
}) {
  const [open, setOpen] = useState(false);
  const [mode, setMode] = useState<"all_ready" | "selected">(
    detail.scope_mode,
  );
  const [selected, setSelected] = useState(
    () => new Set(detail.scope_node_ids),
  );
  const [tree, setTree] = useState<LibraryTreeNode[]>([]);
  const [browse, setBrowse] = useState<Record<string, LibraryBrowse>>({});
  const [expanded, setExpanded] = useState(() => new Set<string>());
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const loadController = useRef<AbortController | null>(null);
  const index = useMemo(() => folderIndex(tree), [tree]);

  useEffect(() => {
    return () => loadController.current?.abort();
  }, []);

  function openEditor() {
    loadController.current?.abort();
    const controller = new AbortController();
    loadController.current = controller;
    setMode(detail.scope_mode);
    setSelected(new Set(detail.scope_node_ids));
    setError(null);
    setOpen(true);
    setLoading(true);
    void Promise.all([
      getLibraryTree(controller.signal),
      browseLibrary(null, controller.signal),
    ])
      .then(([nextTree, root]) => {
        setTree(nextTree);
        setBrowse({ [ROOT]: root });
      })
      .catch((reason: unknown) => {
        if (!controller.signal.aborted) setError(errorMessage(reason));
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
  }

  async function toggleExpanded(folderId: string) {
    const next = new Set(expanded);
    if (next.has(folderId)) {
      next.delete(folderId);
      setExpanded(next);
      return;
    }
    next.add(folderId);
    setExpanded(next);
    if (!browse[folderId]) {
      try {
        const contents = await browseLibrary(folderId);
        setBrowse((current) => ({ ...current, [folderId]: contents }));
      } catch (reason) {
        setError(errorMessage(reason));
      }
    }
  }

  function toggleNode(node: LibraryTreeNode | LibraryNode, checked: boolean) {
    const next = new Set(selected);
    if (checked) {
      next.add(node.node_id);
      if ("children" in node) {
        const descendants = descendantFolders(node);
        const coveredFolders = new Set(descendants);
        coveredFolders.add(node.node_id);
        for (const id of descendants) next.delete(id);
        for (const contents of Object.values(browse)) {
          for (const child of contents.children) {
            if (
              child.kind === "file" &&
              hasSelectedAncestor(
                child.parent_id,
                coveredFolders,
                index.parent,
              )
            ) {
              next.delete(child.node_id);
            }
          }
        }
      }
    } else {
      next.delete(node.node_id);
    }
    setSelected(next);
  }

  async function save() {
    if (mode === "selected" && selected.size === 0) {
      setError("Select at least one folder or document.");
      return;
    }
    setSaving(true);
    setError(null);
    try {
      const updated = await onSave(mode, mode === "selected" ? [...selected] : []);
      setMode(updated.scope_mode);
      setSelected(new Set(updated.scope_node_ids));
      setOpen(false);
    } catch (reason) {
      setError(errorMessage(reason));
    } finally {
      setSaving(false);
    }
  }

  const renderFile = (file: LibraryNode, depth: number) => {
    const inherited = hasSelectedAncestor(
      file.parent_id,
      selected,
      index.parent,
    );
    return (
      <li key={file.node_id}>
        <label
          className="flex min-h-11 items-center gap-2 border-b border-[var(--hairline)] py-2"
          style={{ paddingLeft: depth * 16 }}
        >
          <input
            type="checkbox"
            checked={selected.has(file.node_id) || inherited}
            disabled={saving || generating || inherited}
            onChange={(event) => toggleNode(file, event.target.checked)}
          />
          <span className="min-w-0">
            <Icon name="file" className="mr-2 inline-block align-text-bottom" />
            {file.name}
            {inherited ? (
              <span className="block text-sm text-[var(--mute)]">
                Included by selected folder
              </span>
            ) : null}
          </span>
        </label>
      </li>
    );
  };

  const renderFolder = (folder: LibraryTreeNode, depth: number) => {
    const isExpanded = expanded.has(folder.node_id);
    const inherited = hasSelectedAncestor(
      folder.parent_id,
      selected,
      index.parent,
    );
    const contents = browse[folder.node_id];
    const files =
      contents?.children.filter((child) => child.kind === "file") ?? [];
    return (
      <li key={folder.node_id}>
        <div
          className="grid min-h-11 grid-cols-[44px_44px_minmax(0,1fr)] items-center border-b border-[var(--hairline)]"
          style={{ paddingLeft: depth * 16 }}
        >
          <button
            type="button"
            aria-expanded={isExpanded}
            aria-label={`${isExpanded ? "Collapse" : "Expand"} ${folder.name}`}
            onClick={() => void toggleExpanded(folder.node_id)}
            disabled={saving || generating}
            className="min-h-11"
          >
            <Icon
              name="chevron"
              className={isExpanded ? "rotate-90 transition-transform" : "transition-transform"}
            />
          </button>
          <input
            type="checkbox"
            aria-label={`Include folder ${folder.name}`}
            checked={selected.has(folder.node_id) || inherited}
            disabled={saving || generating || inherited}
            onChange={(event) => toggleNode(folder, event.target.checked)}
          />
          <span>
            <Icon name="folder" className="mr-2 inline-block align-text-bottom" />
            {folder.name}
            {inherited ? (
              <span className="block text-sm text-[var(--mute)]">
                Included by selected folder
              </span>
            ) : null}
          </span>
        </div>
        {isExpanded ? (
          <ul className="motion-disclosure-content m-0 list-none p-0">
            {folder.children.map((child) => renderFolder(child, depth + 1))}
            {files.map((file) => renderFile(file, depth + 1))}
            {!contents ? (
              <li
                role="status"
                className="min-h-11 py-2 text-sm text-[var(--mute)]"
                style={{ paddingLeft: (depth + 1) * 16 }}
              >
                Loading documents…
              </li>
            ) : null}
          </ul>
        ) : null}
      </li>
    );
  };

  const rootFiles =
    browse[ROOT]?.children.filter((child) => child.kind === "file") ?? [];

  return (
    <>
      <button
        type="button"
        onClick={openEditor}
        disabled={generating}
        className="scope-trigger"
        aria-label={`Conversation scope: ${
          detail.scope_mode === "all_ready"
            ? "all ready files"
            : `${detail.scope_node_ids.length} selected`
        }`}
      >
        <Icon name="source" />
        <span>
          {detail.scope_mode === "all_ready"
            ? "All ready files"
            : `${detail.scope_node_ids.length} selected`}
        </span>
      </button>
      <NativeDialog
        open={open}
        onClose={() => setOpen(false)}
        title="Conversation scope"
        description="Choose all ready documents, or save at least one folder or document."
        className="w-[min(720px,calc(100%-32px))] border border-[var(--ink)] bg-[var(--canvas)] p-5 text-[var(--ink)] backdrop:bg-black/50"
      >
        <fieldset disabled={saving || generating} className="mt-4 border-0 p-0">
          <legend className="mb-2 font-bold">Retrieval scope</legend>
          <label className="flex min-h-11 items-center gap-3 border-b border-[var(--hairline)]">
            <input
              type="radio"
              name="scope-mode"
              checked={mode === "all_ready"}
              onChange={() => setMode("all_ready")}
            />
            All ready files
          </label>
          <label className="flex min-h-11 items-center gap-3 border-b border-[var(--hairline)]">
            <input
              type="radio"
              name="scope-mode"
              checked={mode === "selected"}
              onChange={() => setMode("selected")}
            />
            Selected folders and files
          </label>
        </fieldset>
        {mode === "selected" ? (
          <div className="mt-4 max-h-[45dvh] overflow-y-auto border-t border-[var(--hairline)]">
            {loading ? <p role="status">Loading library…</p> : null}
            <ul className="m-0 list-none p-0">
              {tree.map((folder) => renderFolder(folder, 0))}
              {rootFiles.map((file) => renderFile(file, 0))}
            </ul>
            {!loading && tree.length === 0 && rootFiles.length === 0 ? (
              <p className="py-3 text-sm text-[var(--mute)]">
                The knowledge base is empty.
              </p>
            ) : null}
          </div>
        ) : null}
        {error ? (
          <p role="alert" className="mt-3 text-[var(--danger)]">
            {error}
          </p>
        ) : null}
        <div className="mt-5 flex flex-wrap justify-end gap-3">
          <button
            type="button"
            onClick={() => setOpen(false)}
            disabled={saving}
            className="min-h-11 rounded-[4px] border border-[var(--hairline-strong)] px-4"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={() => void save()}
            disabled={
              saving ||
              generating ||
              (mode === "selected" && selected.size === 0)
            }
            className="min-h-11 rounded-[4px] border border-[var(--ink)] bg-[var(--ink)] px-4 text-[var(--canvas)] disabled:border-[var(--surface-card)] disabled:bg-[var(--surface-card)] disabled:text-[var(--ash)]"
          >
            {saving ? "Saving…" : "Save scope"}
          </button>
        </div>
      </NativeDialog>
    </>
  );
}
