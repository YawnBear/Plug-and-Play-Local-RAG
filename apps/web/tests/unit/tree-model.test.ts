import { describe, expect, it } from "vitest";

import type { LibraryTreeNode } from "@/features/library/contracts";
import {
  collectTreeNodeIds,
  flattenLibraryTree,
  toggleTreeNode,
  treeKeyAction,
  treeTypeaheadMatch,
} from "@/features/library/tree-model";

const ROOT = "11111111-1111-4111-8111-111111111111";
const CHILD = "22222222-2222-4222-8222-222222222222";
const LEAF = "33333333-3333-4333-8333-333333333333";
const OTHER = "44444444-4444-4444-8444-444444444444";

const tree: LibraryTreeNode[] = [
  {
    node_id: ROOT,
    parent_id: null,
    name: "Root",
    logical_path: "/Root",
    children: [
      {
        node_id: CHILD,
        parent_id: ROOT,
        name: "Child",
        logical_path: "/Root/Child",
        children: [
          {
            node_id: LEAF,
            parent_id: CHILD,
            name: "Leaf",
            logical_path: "/Root/Child/Leaf",
            children: [],
          },
        ],
      },
    ],
  },
  {
    node_id: OTHER,
    parent_id: null,
    name: "Other",
    logical_path: "/Other",
    children: [],
  },
];

describe("library tree model", () => {
  it("flattens only expanded branches with ARIA set metadata", () => {
    const rows = flattenLibraryTree(tree, new Set([ROOT]));
    expect(rows.map((row) => row.node.node_id)).toEqual([ROOT, CHILD, OTHER]);
    expect(rows[0]).toMatchObject({
      level: 1,
      positionInSet: 1,
      setSize: 2,
      expanded: true,
    });
    expect(rows[1]).toMatchObject({
      level: 2,
      positionInSet: 1,
      setSize: 1,
      expanded: false,
    });
  });

  it("returns immutable expanded sets", () => {
    const original = new Set([ROOT]);
    expect(toggleTreeNode(original, ROOT)).toEqual(new Set());
    expect(toggleTreeNode(original, CHILD)).toEqual(new Set([ROOT, CHILD]));
    expect(original).toEqual(new Set([ROOT]));
  });

  it("implements standard tree arrow, Home, and End actions", () => {
    const rows = flattenLibraryTree(tree, new Set([ROOT, CHILD]));
    expect(treeKeyAction(rows, ROOT, "ArrowRight")).toEqual({
      type: "focus",
      nodeId: CHILD,
    });
    expect(treeKeyAction(rows, CHILD, "ArrowLeft")).toEqual({
      type: "collapse",
      nodeId: CHILD,
    });
    expect(treeKeyAction(rows, LEAF, "ArrowLeft")).toEqual({
      type: "focus",
      nodeId: CHILD,
    });
    expect(treeKeyAction(rows, ROOT, "End")).toEqual({
      type: "focus",
      nodeId: OTHER,
    });
  });

  it("collects every node regardless of expansion", () => {
    expect(collectTreeNodeIds(tree)).toEqual(
      new Set([ROOT, CHILD, LEAF, OTHER]),
    );
  });

  it("cycles buffered typeahead from the current row", () => {
    const rows = flattenLibraryTree(tree, new Set([ROOT, CHILD]));
    expect(treeTypeaheadMatch(rows, ROOT, "o")).toBe(OTHER);
    expect(treeTypeaheadMatch(rows, OTHER, "ro")).toBe(ROOT);
    expect(treeTypeaheadMatch(rows, ROOT, "missing")).toBeNull();
  });
});
