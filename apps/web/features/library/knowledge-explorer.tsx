"use client";

import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type KeyboardEvent,
  type MouseEvent,
} from "react";

import { Icon } from "@/components/shell/icons";
import { Tooltip } from "@/components/ui/tooltip";

import type {
  DocumentSummary,
  JobStatus,
  LibraryNode,
  LibraryTreeNode,
} from "./contracts";

const ROOT_ID = "__knowledge_base_root__";
const EXPANDED_KEY = "local-rag.kb-explorer-expanded.v1";

type FolderAction = "create" | "rename" | "move" | "delete";
type DocumentAction = "rename" | "move" | "delete";

type ExplorerRow =
  | {
      id: string;
      kind: "folder";
      level: number;
      name: string;
      node: LibraryTreeNode | null;
      parentId: string | null;
    }
  | {
      id: string;
      kind: "document";
      level: number;
      name: string;
      document: DocumentSummary;
      parentId: string | null;
    };

type MenuTarget =
  | { kind: "folder"; id: string }
  | { kind: "document"; document: DocumentSummary };

type ExplorerRowStyle = CSSProperties & {
  "--kb-explorer-node-x": string;
};

function restoredExpanded(): Set<string> {
  if (typeof window === "undefined") return new Set([ROOT_ID]);
  try {
    const value: unknown = JSON.parse(
      window.localStorage.getItem(EXPANDED_KEY) ?? "[]",
    );
    return new Set([
      ROOT_ID,
      ...(Array.isArray(value)
        ? value.filter((item): item is string => typeof item === "string")
        : []),
    ]);
  } catch {
    return new Set([ROOT_ID]);
  }
}

function persistExpanded(expanded: Set<string>) {
  try {
    window.localStorage.setItem(
      EXPANDED_KEY,
      JSON.stringify([...expanded].filter((id) => id !== ROOT_ID)),
    );
  } catch {
    // Expansion remains available for the current render.
  }
}

function buildRows(
  tree: readonly LibraryTreeNode[],
  documents: readonly DocumentSummary[],
  expanded: ReadonlySet<string>,
): ExplorerRow[] {
  const documentsByParent = new Map<string | null, DocumentSummary[]>();
  for (const document of documents) {
    const siblings = documentsByParent.get(document.parent_id) ?? [];
    siblings.push(document);
    documentsByParent.set(document.parent_id, siblings);
  }
  for (const siblings of documentsByParent.values()) {
    siblings.sort((left, right) =>
      left.display_name.localeCompare(right.display_name),
    );
  }

  const rows: ExplorerRow[] = [
    {
      id: ROOT_ID,
      kind: "folder",
      level: 1,
      name: "Root",
      node: null,
      parentId: null,
    },
  ];

  function appendContents(
    folders: readonly LibraryTreeNode[],
    parentId: string | null,
    level: number,
  ) {
    for (const folder of folders) {
      rows.push({
        id: folder.node_id,
        kind: "folder",
        level,
        name: folder.name,
        node: folder,
        parentId: folder.parent_id,
      });
      if (expanded.has(folder.node_id)) {
        appendContents(folder.children, folder.node_id, level + 1);
      }
    }
    for (const document of documentsByParent.get(parentId) ?? []) {
      rows.push({
        id: document.document_id,
        kind: "document",
        level,
        name: document.display_name,
        document,
        parentId,
      });
    }
  }

  if (expanded.has(ROOT_ID)) {
    appendContents(tree, null, 2);
  }
  return rows;
}

function folderPath(
  tree: readonly LibraryTreeNode[],
  targetId: string,
): string[] {
  for (const node of tree) {
    if (node.node_id === targetId) return [node.node_id];
    const childPath = folderPath(node.children, targetId);
    if (childPath.length > 0) return [node.node_id, ...childPath];
  }
  return [];
}

export function KnowledgeExplorer({
  tree,
  documents,
  jobs,
  selectedFolderId,
  selectedDocumentId,
  currentFolder,
  loading,
  refreshing,
  error,
  canUpload,
  canCreateFolder,
  canAdminister,
  onFolder,
  onDocument,
  onUpload,
  onFolderAction,
  onDocumentAction,
  onShowDocumentDetails,
  onRefresh,
  onNavigate,
}: {
  tree: readonly LibraryTreeNode[];
  documents: readonly DocumentSummary[];
  jobs: Readonly<Record<string, JobStatus>>;
  selectedFolderId: string | null;
  selectedDocumentId: string | null;
  currentFolder: LibraryNode | null;
  loading: boolean;
  refreshing: boolean;
  error: string | null;
  canUpload: boolean;
  canCreateFolder: boolean;
  canAdminister: boolean;
  onFolder: (folderId: string | null) => void;
  onDocument: (document: DocumentSummary, pinned: boolean) => void;
  onUpload: () => void;
  onFolderAction: (action: FolderAction) => void;
  onDocumentAction: (
    document: DocumentSummary,
    action: DocumentAction,
  ) => void;
  onShowDocumentDetails: (document: DocumentSummary) => void;
  onRefresh: () => void;
  onNavigate?: () => void;
}) {
  const [expanded, setExpanded] = useState(restoredExpanded);
  const [focusedId, setFocusedId] = useState(ROOT_ID);
  const [menu, setMenu] = useState<{
    target: MenuTarget;
    left: number;
    top: number;
  } | null>(null);
  const selectedDocument = documents.find(
    (document) => document.document_id === selectedDocumentId,
  );
  const rows = useMemo(
    () => buildRows(tree, documents, expanded),
    [documents, expanded, tree],
  );
  const visibleParentIds = useMemo(
    () =>
      new Set(
        rows
          .slice(1)
          .map((row) => row.parentId ?? ROOT_ID),
      ),
    [rows],
  );
  const rowRefs = useRef(new Map<string, HTMLDivElement>());
  const menuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const targetFolderId = selectedDocument?.parent_id ?? selectedFolderId;
    if (!targetFolderId) return;
    const path = folderPath(tree, targetFolderId);
    if (path.length === 0) return;
    queueMicrotask(() => {
      setExpanded((current) => {
        if (path.every((id) => current.has(id))) return current;
        const next = new Set(current);
        for (const id of path) next.add(id);
        persistExpanded(next);
        return next;
      });
      setFocusedId(selectedDocumentId ?? targetFolderId);
    });
  }, [selectedDocument?.parent_id, selectedDocumentId, selectedFolderId, tree]);

  useEffect(() => {
    if (!menu) return;
    const frame = window.requestAnimationFrame(() => {
      menuRef.current?.querySelector<HTMLElement>('[role="menuitem"]')?.focus();
    });
    const dismiss = (event: PointerEvent) => {
      if (!menuRef.current?.contains(event.target as Node)) setMenu(null);
    };
    const escape = (event: globalThis.KeyboardEvent) => {
      if (event.key !== "Escape") return;
      setMenu(null);
      rowRefs.current.get(focusedId)?.focus();
    };
    document.addEventListener("pointerdown", dismiss);
    document.addEventListener("keydown", escape);
    return () => {
      window.cancelAnimationFrame(frame);
      document.removeEventListener("pointerdown", dismiss);
      document.removeEventListener("keydown", escape);
    };
  }, [focusedId, menu]);

  function toggle(folderId: string) {
    setExpanded((current) => {
      const next = new Set(current);
      if (next.has(folderId)) next.delete(folderId);
      else next.add(folderId);
      persistExpanded(next);
      return next;
    });
  }

  function activate(row: ExplorerRow, pinned = false) {
    if (row.kind === "folder") {
      onFolder(row.id === ROOT_ID ? null : row.id);
    } else {
      onDocument(row.document, pinned);
    }
    onNavigate?.();
  }

  function openMenu(
    event: MouseEvent<HTMLElement>,
    target: MenuTarget,
  ) {
    event.preventDefault();
    event.stopPropagation();
    const rect = event.currentTarget.getBoundingClientRect();
    const left = "clientX" in event && event.clientX > 0
      ? event.clientX
      : rect.right;
    const top = "clientY" in event && event.clientY > 0
      ? event.clientY
      : rect.bottom;
    setMenu({
      target,
      left: Math.min(left, window.innerWidth - 220),
      top: Math.min(top, window.innerHeight - 300),
    });
  }

  function keyDown(event: KeyboardEvent<HTMLDivElement>, row: ExplorerRow) {
    const index = rows.findIndex((item) => item.id === row.id);
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      const nextIndex = Math.max(
        0,
        Math.min(rows.length - 1, index + (event.key === "ArrowDown" ? 1 : -1)),
      );
      const next = rows[nextIndex];
      if (next) {
        setFocusedId(next.id);
        rowRefs.current.get(next.id)?.focus();
      }
      return;
    }
    if (event.key === "Home" || event.key === "End") {
      event.preventDefault();
      const next = event.key === "Home" ? rows[0] : rows.at(-1);
      if (next) {
        setFocusedId(next.id);
        rowRefs.current.get(next.id)?.focus();
      }
      return;
    }
    if (event.key === "ArrowRight" && row.kind === "folder") {
      event.preventDefault();
      if (!expanded.has(row.id)) toggle(row.id);
      return;
    }
    if (event.key === "ArrowLeft" && row.kind === "folder") {
      event.preventDefault();
      if (expanded.has(row.id)) {
        toggle(row.id);
      } else if (row.parentId) {
        setFocusedId(row.parentId);
        rowRefs.current.get(row.parentId)?.focus();
      }
      return;
    }
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      activate(row, event.detail > 1);
      return;
    }
    if (event.key === "F2") {
      if (
        row.kind === "document" &&
        row.document.can_manage
      ) {
        event.preventDefault();
        onDocumentAction(row.document, "rename");
      } else if (
        row.kind === "folder" &&
        (row.id === ROOT_ID ? canCreateFolder : currentFolder?.can_manage)
      ) {
        event.preventDefault();
        onFolderAction(row.id === ROOT_ID ? "create" : "rename");
      }
    }
  }

  function menuItem(
    label: string,
    action: () => void,
    danger = false,
  ) {
    return (
      <button
        className={danger ? "explorer-menu__danger" : undefined}
        onClick={() => {
          setMenu(null);
          action();
        }}
        role="menuitem"
        type="button"
      >
        {label}
      </button>
    );
  }

  function menuContents(target: MenuTarget) {
    if (target.kind === "folder") {
      const targetIsSelectedFolder =
        target.id === ROOT_ID
          ? selectedFolderId === null
          : target.id === selectedFolderId;
      return (
        <>
          {menuItem("Open", () =>
            onFolder(target.id === ROOT_ID ? null : target.id),
          )}
          {targetIsSelectedFolder && canUpload
            ? menuItem("Upload PDF", onUpload)
            : null}
          {targetIsSelectedFolder && canCreateFolder
            ? menuItem("New folder", () => onFolderAction("create"))
            : null}
          {targetIsSelectedFolder && currentFolder?.can_manage
            ? menuItem("Rename", () => onFolderAction("rename"))
            : null}
          {targetIsSelectedFolder && currentFolder?.can_manage
            ? menuItem("Move", () => onFolderAction("move"))
            : null}
          {targetIsSelectedFolder && canAdminister && currentFolder ? (
            <a
              href={`/admin/access?node=${currentFolder.node_id}`}
              role="menuitem"
            >
              Manage access
            </a>
          ) : null}
          {targetIsSelectedFolder && currentFolder?.can_manage
            ? menuItem("Delete", () => onFolderAction("delete"), true)
            : null}
        </>
      );
    }

    const document = target.document;
    return (
      <>
        {menuItem("Open", () => onDocument(document, true))}
        {menuItem("View details", () => onShowDocumentDetails(document))}
        {document.can_manage
          ? menuItem("Rename", () => onDocumentAction(document, "rename"))
          : null}
        {document.can_manage
          ? menuItem("Move", () => onDocumentAction(document, "move"))
          : null}
        {canAdminister ? (
          <a href={`/admin/access?node=${document.node_id}`} role="menuitem">
            Manage access
          </a>
        ) : null}
        {document.can_manage
          ? menuItem(
              "Delete",
              () => onDocumentAction(document, "delete"),
              true,
            )
          : null}
      </>
    );
  }

  return (
    <section className="kb-explorer" aria-labelledby="kb-explorer-title">
      <div className="kb-explorer__header">
        <div>
          <h2 id="kb-explorer-title">Local PDF Library</h2>
          {refreshing ? <span role="status">Refreshing…</span> : null}
        </div>
        <div className="kb-explorer__tools">
          <Tooltip content="Upload PDF">
            <button
              aria-label="Upload PDF"
              disabled={!canUpload}
              onClick={onUpload}
              type="button"
            >
              <Icon name="upload" />
            </button>
          </Tooltip>
          <Tooltip content="New folder">
            <button
              aria-label="New folder"
              disabled={!canCreateFolder}
              onClick={() => onFolderAction("create")}
              type="button"
            >
              <Icon name="new" />
            </button>
          </Tooltip>
          <Tooltip content="Refresh explorer">
            <button
              aria-label="Refresh explorer"
              disabled={loading || refreshing}
              onClick={onRefresh}
              type="button"
            >
              <Icon name="refresh" />
            </button>
          </Tooltip>
          <Tooltip content="Collapse all">
            <button
              aria-label="Collapse all folders"
              onClick={() => {
                const next = new Set([ROOT_ID]);
                setExpanded(next);
                persistExpanded(next);
              }}
              type="button"
            >
              <Icon name="collapse-all" />
            </button>
          </Tooltip>
        </div>
      </div>
      {error ? (
        <p className="kb-explorer__error" role="alert">
          {error}
        </p>
      ) : null}
      {loading && rows.length === 1 ? (
        <p className="kb-explorer__state" role="status">
          Loading library…
        </p>
      ) : (
        <div className="kb-explorer__tree" role="tree" aria-label="Knowledge base files">
          {rows.map((row) => {
            const folder = row.kind === "folder";
            const expandable = folder;
            const selected =
              row.kind === "document"
                ? row.document.document_id === selectedDocumentId
                : row.id === ROOT_ID
                  ? selectedFolderId === null && selectedDocumentId === null
                  : row.id === selectedFolderId && selectedDocumentId === null;
            const target: MenuTarget =
              row.kind === "document"
                ? { kind: "document", document: row.document }
                : { kind: "folder", id: row.id };
            const status =
              row.kind === "document"
                ? jobs[row.document.document_id]?.status ?? row.document.state
                : null;
            const indent = (row.level - 1) * 14 + 2;
            const rowStyle: ExplorerRowStyle = {
              paddingInlineStart: `${indent}px`,
              "--kb-explorer-node-x": `${indent + 15}px`,
            };
            const expandedWithChildren =
              folder &&
              expanded.has(row.id) &&
              visibleParentIds.has(row.id);
            return (
              <div
                aria-expanded={expandable ? expanded.has(row.id) : undefined}
                aria-level={row.level}
                aria-selected={selected}
                className="kb-explorer__row"
                data-expanded-children={
                  expandedWithChildren ? "true" : undefined
                }
                key={`${row.kind}-${row.id}`}
                onContextMenu={(event) => {
                  activate(row);
                  openMenu(event, target);
                }}
                onDoubleClick={() => activate(row, true)}
                onFocus={() => setFocusedId(row.id)}
                onKeyDown={(event) => keyDown(event, row)}
                ref={(element) => {
                  if (element) rowRefs.current.set(row.id, element);
                  else rowRefs.current.delete(row.id);
                }}
                role="treeitem"
                style={rowStyle}
                tabIndex={focusedId === row.id ? 0 : -1}
              >
                {Array.from({ length: Math.max(0, row.level - 1) }, (_, depth) => (
                  <span
                    aria-hidden="true"
                    className="kb-explorer__guide"
                    data-tree-guide=""
                    key={depth}
                    style={{ left: `${depth * 14 + 17}px` }}
                  />
                ))}
                {folder ? (
                  <button
                    aria-label={`${expanded.has(row.id) ? "Collapse" : "Expand"} ${row.name}`}
                    className="kb-explorer__chevron"
                    onClick={(event) => {
                      event.stopPropagation();
                      toggle(row.id);
                    }}
                    tabIndex={-1}
                    type="button"
                  >
                    <Icon
                      className={
                        expanded.has(row.id)
                          ? "rotate-90 transition-transform"
                          : "transition-transform"
                      }
                      name="chevron"
                    />
                  </button>
                ) : (
                  <span className="kb-explorer__chevron" aria-hidden="true" />
                )}
                <button
                  className="kb-explorer__label"
                  onClick={() => activate(row)}
                  tabIndex={-1}
                  type="button"
                >
                  <Icon name={folder ? "folder" : "file"} />
                  <span>{row.name}</span>
                  {status ? <small>{status}</small> : null}
                </button>
                <Tooltip content={`Actions for ${row.name}`}>
                  <button
                    aria-label={`Actions for ${row.name}`}
                    className="kb-explorer__more"
                    onClick={(event) => openMenu(event, target)}
                    tabIndex={-1}
                    type="button"
                  >
                    <Icon name="more" />
                  </button>
                </Tooltip>
              </div>
            );
          })}
        </div>
      )}
      {menu ? (
        <div
          className="explorer-menu"
          ref={menuRef}
          role="menu"
          style={{ left: menu.left, top: menu.top }}
        >
          {menuContents(menu.target)}
        </div>
      ) : null}
    </section>
  );
}
