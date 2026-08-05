import type { LibraryTreeNode } from "./contracts";

export interface FlatTreeNode {
  node: LibraryTreeNode;
  level: number;
  positionInSet: number;
  setSize: number;
  expanded: boolean | undefined;
}

export function flattenLibraryTree(
  roots: readonly LibraryTreeNode[],
  expandedIds: ReadonlySet<string>,
): FlatTreeNode[] {
  const rows: FlatTreeNode[] = [];

  const visit = (
    nodes: readonly LibraryTreeNode[],
    level: number,
  ): void => {
    nodes.forEach((node, index) => {
      const expandable = node.children.length > 0;
      const expanded = expandable ? expandedIds.has(node.node_id) : undefined;
      rows.push({
        node,
        level,
        positionInSet: index + 1,
        setSize: nodes.length,
        expanded,
      });
      if (expanded) visit(node.children, level + 1);
    });
  };

  visit(roots, 1);
  return rows;
}

export function toggleTreeNode(
  expandedIds: ReadonlySet<string>,
  nodeId: string,
): Set<string> {
  const next = new Set(expandedIds);
  if (next.has(nodeId)) next.delete(nodeId);
  else next.add(nodeId);
  return next;
}

export type TreeNavigationKey =
  | "ArrowDown"
  | "ArrowUp"
  | "ArrowRight"
  | "ArrowLeft"
  | "Home"
  | "End";

export type TreeKeyAction =
  | { type: "focus"; nodeId: string }
  | { type: "expand"; nodeId: string }
  | { type: "collapse"; nodeId: string }
  | null;

export function treeKeyAction(
  rows: readonly FlatTreeNode[],
  currentNodeId: string,
  key: TreeNavigationKey,
): TreeKeyAction {
  const index = rows.findIndex((row) => row.node.node_id === currentNodeId);
  if (index < 0 || rows.length === 0) return null;
  const current = rows[index];

  if (key === "ArrowDown" && index < rows.length - 1) {
    return { type: "focus", nodeId: rows[index + 1].node.node_id };
  }
  if (key === "ArrowUp" && index > 0) {
    return { type: "focus", nodeId: rows[index - 1].node.node_id };
  }
  if (key === "Home") {
    return { type: "focus", nodeId: rows[0].node.node_id };
  }
  if (key === "End") {
    return { type: "focus", nodeId: rows.at(-1)!.node.node_id };
  }
  if (key === "ArrowRight") {
    if (current.expanded === false) {
      return { type: "expand", nodeId: currentNodeId };
    }
    if (current.expanded === true) {
      const child = rows[index + 1];
      if (child?.level === current.level + 1) {
        return { type: "focus", nodeId: child.node.node_id };
      }
    }
  }
  if (key === "ArrowLeft") {
    if (current.expanded === true) {
      return { type: "collapse", nodeId: currentNodeId };
    }
    if (current.level > 1) {
      for (let parentIndex = index - 1; parentIndex >= 0; parentIndex -= 1) {
        if (rows[parentIndex].level === current.level - 1) {
          return { type: "focus", nodeId: rows[parentIndex].node.node_id };
        }
      }
    }
  }
  return null;
}

export function collectTreeNodeIds(
  roots: readonly LibraryTreeNode[],
): Set<string> {
  const ids = new Set<string>();
  const visit = (nodes: readonly LibraryTreeNode[]): void => {
    for (const node of nodes) {
      ids.add(node.node_id);
      visit(node.children);
    }
  };
  visit(roots);
  return ids;
}

export function findTreeNode(
  roots: readonly LibraryTreeNode[],
  nodeId: string,
): LibraryTreeNode | null {
  for (const node of roots) {
    if (node.node_id === nodeId) return node;
    const found = findTreeNode(node.children, nodeId);
    if (found) return found;
  }
  return null;
}

export function treePathToNode(
  roots: readonly LibraryTreeNode[],
  nodeId: string,
): string[] {
  for (const node of roots) {
    if (node.node_id === nodeId) return [node.node_id];
    const childPath = treePathToNode(node.children, nodeId);
    if (childPath.length > 0) return [node.node_id, ...childPath];
  }
  return [];
}

export function treeTypeaheadMatch(
  rows: readonly FlatTreeNode[],
  currentNodeId: string,
  query: string,
): string | null {
  const normalized = query.trim().toLocaleLowerCase();
  if (!normalized || rows.length === 0) return null;
  const currentIndex = rows.findIndex(
    (row) => row.node.node_id === currentNodeId,
  );
  const start = currentIndex < 0 ? 0 : currentIndex + 1;
  for (let offset = 0; offset < rows.length; offset += 1) {
    const row = rows[(start + offset) % rows.length];
    if (row.node.name.toLocaleLowerCase().startsWith(normalized)) {
      return row.node.node_id;
    }
  }
  return null;
}
