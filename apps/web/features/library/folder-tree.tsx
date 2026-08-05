"use client";

import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent,
} from "react";

import { Icon } from "@/components/shell/icons";

import type { LibraryTreeNode } from "./contracts";
import {
  collectTreeNodeIds,
  flattenLibraryTree,
  toggleTreeNode,
  treePathToNode,
  treeKeyAction,
  treeTypeaheadMatch,
  type TreeNavigationKey,
} from "./tree-model";

const ROOT_ID = "__knowledge_base_root__";

function rootNode(tree: readonly LibraryTreeNode[]): LibraryTreeNode {
  return {
    node_id: ROOT_ID,
    parent_id: null,
    name: "Knowledge base root",
    logical_path: "/",
    children: [...tree],
  };
}

export function FolderTree({
  tree,
  selectedFolderId,
  onSelect,
}: {
  tree: readonly LibraryTreeNode[];
  selectedFolderId: string | null;
  onSelect: (folderId: string | null) => void;
}) {
  const [expanded, setExpanded] = useState<Set<string>>(
    () => new Set([ROOT_ID, ...collectTreeNodeIds(tree)]),
  );
  const [focusedId, setFocusedId] = useState(selectedFolderId ?? ROOT_ID);
  const refs = useRef(new Map<string, HTMLDivElement>());
  const treeRef = useRef<HTMLDivElement>(null);
  const typeahead = useRef("");
  const typeaheadTimer = useRef<number | null>(null);
  const pendingSelection = useRef<string | null | undefined>(
    selectedFolderId,
  );
  const model = useMemo(() => rootNode(tree), [tree]);
  const rows = useMemo(
    () => flattenLibraryTree([model], new Set([...expanded, ROOT_ID])),
    [expanded, model],
  );
  const available = useMemo(
    () => new Set(rows.map((row) => row.node.node_id)),
    [rows],
  );
  const rowMetadata = useMemo(
    () => new Map(rows.map((row) => [row.node.node_id, row])),
    [rows],
  );

  useEffect(() => {
    pendingSelection.current = selectedFolderId;
  }, [selectedFolderId]);

  useEffect(() => {
    if (!selectedFolderId) return;
    const ancestors = treePathToNode(tree, selectedFolderId).slice(0, -1);
    if (ancestors.length === 0) return;
    const frame = requestAnimationFrame(() => {
      setExpanded((current) => {
        const next = new Set(current);
        let changed = false;
        for (const nodeId of ancestors) {
          if (!next.has(nodeId)) {
            next.add(nodeId);
            changed = true;
          }
        }
        return changed ? next : current;
      });
    });
    return () => cancelAnimationFrame(frame);
  }, [selectedFolderId, tree]);

  useEffect(() => {
    if (pendingSelection.current !== undefined) {
      const pending = pendingSelection.current ?? ROOT_ID;
      if (available.has(pending)) {
        pendingSelection.current = undefined;
        requestAnimationFrame(() => {
          setFocusedId(pending);
          if (treeRef.current?.contains(document.activeElement)) {
            refs.current.get(pending)?.focus();
          }
        });
        return;
      }
    }
    const desired =
      selectedFolderId && available.has(selectedFolderId)
        ? selectedFolderId
        : ROOT_ID;
    if (available.has(focusedId)) return;
    requestAnimationFrame(() => {
      setFocusedId(desired);
      if (treeRef.current?.contains(document.activeElement)) {
        refs.current.get(desired)?.focus();
      }
    });
  }, [available, focusedId, selectedFolderId]);

  useEffect(
    () => () => {
      if (typeaheadTimer.current !== null) {
        window.clearTimeout(typeaheadTimer.current);
      }
    },
    [],
  );

  function focus(nodeId: string) {
    setFocusedId(nodeId);
    requestAnimationFrame(() => refs.current.get(nodeId)?.focus());
  }

  function activate(nodeId: string) {
    onSelect(nodeId === ROOT_ID ? null : nodeId);
  }

  function keyDown(
    event: KeyboardEvent<HTMLDivElement>,
    nodeId: string,
  ) {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      activate(nodeId);
      return;
    }
    const navigation = [
      "ArrowDown",
      "ArrowUp",
      "ArrowRight",
      "ArrowLeft",
      "Home",
      "End",
    ] as const;
    if (navigation.includes(event.key as (typeof navigation)[number])) {
      event.preventDefault();
      const action = treeKeyAction(
        rows,
        nodeId,
        event.key as TreeNavigationKey,
      );
      if (action?.type === "focus") focus(action.nodeId);
      if (action?.type === "expand" && action.nodeId !== ROOT_ID) {
        setExpanded((current) => {
          const next = new Set(current);
          next.add(action.nodeId);
          return next;
        });
      }
      if (action?.type === "collapse" && action.nodeId !== ROOT_ID) {
        setExpanded((current) => {
          const next = new Set(current);
          next.delete(action.nodeId);
          return next;
        });
      }
      return;
    }
    if (
      event.key.length === 1 &&
      !event.altKey &&
      !event.ctrlKey &&
      !event.metaKey
    ) {
      typeahead.current += event.key;
      if (typeaheadTimer.current !== null) {
        window.clearTimeout(typeaheadTimer.current);
      }
      typeaheadTimer.current = window.setTimeout(() => {
        typeahead.current = "";
      }, 500);
      const match = treeTypeaheadMatch(rows, nodeId, typeahead.current);
      if (match) {
        event.preventDefault();
        focus(match);
      }
    }
  }

  function renderNode(node: LibraryTreeNode) {
    const metadata = rowMetadata.get(node.node_id);
    const root = node.node_id === ROOT_ID;
    const isExpanded = root || expanded.has(node.node_id);
    const expandable = node.children.length > 0;
    const selected =
      root ? selectedFolderId === null : selectedFolderId === node.node_id;
    return (
      <div
        key={node.node_id}
        role="treeitem"
        aria-label={root ? "Root" : node.name}
        aria-expanded={expandable ? isExpanded : undefined}
        aria-selected={selected}
        aria-level={metadata?.level}
        aria-posinset={metadata?.positionInSet}
        aria-setsize={metadata?.setSize}
        tabIndex={focusedId === node.node_id ? 0 : -1}
        ref={(element) => {
          if (element) refs.current.set(node.node_id, element);
          else refs.current.delete(node.node_id);
        }}
        onFocus={(event) => {
          if (event.target === event.currentTarget) {
            setFocusedId(node.node_id);
          }
        }}
        onKeyDown={(event) => {
          if (event.target !== event.currentTarget) return;
          event.stopPropagation();
          keyDown(event, node.node_id);
        }}
        onClick={(event) => {
          if (
            !(event.target instanceof Element) ||
            event.target.closest('[role="treeitem"]') !== event.currentTarget
          ) {
            return;
          }
          event.stopPropagation();
          activate(node.node_id);
        }}
        className={`cursor-pointer py-2 text-sm focus:outline focus:outline-2 focus:outline-offset-[-2px] ${
          selected ? "bg-[var(--surface-soft)] font-bold" : ""
        }`}
      >
        <span className="flex min-h-11 items-center gap-2 px-2">
          {!root && expandable ? (
            <button
              type="button"
              tabIndex={-1}
              aria-label={`${isExpanded ? "Collapse" : "Expand"} ${node.name}`}
              onClick={(event) => {
                event.stopPropagation();
                setExpanded((current) =>
                  toggleTreeNode(current, node.node_id),
                );
              }}
              className="min-h-11 min-w-11"
            >
              <Icon
                name="chevron"
                className={isExpanded ? "rotate-90 transition-transform" : "transition-transform"}
              />
            </button>
          ) : (
            <span aria-hidden="true" className="inline-flex min-h-11 min-w-11 items-center justify-center">
              <Icon name="folder" />
            </span>
          )}
          <span className="min-w-0 [overflow-wrap:anywhere]">
            {root ? "Root" : node.name}
          </span>
        </span>
        {isExpanded && node.children.length > 0 ? (
          <div
            role="group"
            className="motion-disclosure-content ml-4 border-l border-[var(--hairline)] pl-2"
          >
            {node.children.map(renderNode)}
          </div>
        ) : null}
      </div>
    );
  }

  return (
    <div
      ref={treeRef}
      role="tree"
      aria-label="Knowledge base folders"
      className="min-w-0"
    >
      {renderNode(model)}
    </div>
  );
}
