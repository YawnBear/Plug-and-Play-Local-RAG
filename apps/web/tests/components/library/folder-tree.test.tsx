import { act, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { FolderTree } from "@/features/library/folder-tree";

import { FOLDER_A, FOLDER_B, FOLDER_C, tree } from "../../library-fixtures";

describe("folder tree", () => {
  beforeEach(() => {
    vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) => {
      callback(0);
      return 1;
    });
  });

  it("uses one roving tab stop and implements every standard tree key", async () => {
    const user = userEvent.setup();
    const select = vi.fn();
    const { container } = render(
      <FolderTree tree={tree} selectedFolderId={null} onSelect={select} />,
    );
    const items = screen.getAllByRole("treeitem");
    const root = items[0];
    root.focus();
    expect(container.querySelectorAll('[role="treeitem"][tabindex="0"]')).toHaveLength(1);

    await user.keyboard("{ArrowDown}");
    expect(screen.getByRole("treeitem", { name: "Alpha" })).toHaveFocus();
    await user.keyboard("{ArrowRight}");
    expect(screen.getByRole("treeitem", { name: "Child" })).toHaveAttribute(
      "tabindex",
      "0",
    );
    await user.keyboard("{ArrowLeft}");
    expect(screen.getByRole("treeitem", { name: "Alpha" })).toHaveFocus();
    await user.keyboard("{ArrowLeft}");
    expect(screen.getByRole("treeitem", { name: "Alpha" })).toHaveAttribute(
      "aria-expanded",
      "false",
    );
    await user.keyboard("{ArrowRight}");
    expect(screen.getByRole("treeitem", { name: "Alpha" })).toHaveAttribute(
      "aria-expanded",
      "true",
    );
    await user.keyboard("{End}");
    expect(screen.getByRole("treeitem", { name: "Beta" })).toHaveFocus();
    await user.keyboard("{Home}");
    expect(root).toHaveFocus();
    await user.keyboard("{ArrowUp}");
    expect(root).toHaveFocus();
    await user.keyboard("{ArrowDown}");
    await user.keyboard(" ");
    expect(select).toHaveBeenCalledWith(FOLDER_A);
    await user.keyboard("{Enter}");
    expect(select).toHaveBeenCalledWith(FOLDER_A);
  });

  it("buffers printable typeahead for 500ms and retains valid selected focus after refetch", async () => {
    vi.useFakeTimers();
    const { rerender } = render(
      <FolderTree tree={tree} selectedFolderId={null} onSelect={vi.fn()} />,
    );
    const root = screen.getAllByRole("treeitem")[0];
    root.focus();
    fireEvent.keyDown(root, { key: "b" });
    await act(async () => {
      await vi.advanceTimersToNextFrame();
    });
    expect(screen.getByRole("treeitem", { name: "Beta" })).toHaveFocus();
    await act(() => vi.advanceTimersByTimeAsync(500));

    const betaOnly = tree.filter((node) => node.node_id === FOLDER_B);
    rerender(
      <FolderTree
        tree={betaOnly}
        selectedFolderId={FOLDER_B}
        onSelect={vi.fn()}
      />,
    );
    await act(async () => {
      await vi.advanceTimersToNextFrame();
    });
    expect(screen.getByRole("treeitem", { name: "Beta" })).toHaveFocus();
    expect(
      screen.queryByRole("treeitem", { name: "Child" }),
    ).not.toBeInTheDocument();
    vi.useRealTimers();
  });

  it("exposes nested groups and stable folder identifiers", () => {
    render(
      <FolderTree tree={tree} selectedFolderId={FOLDER_C} onSelect={vi.fn()} />,
    );
    expect(screen.getByRole("tree")).toBeVisible();
    expect(screen.getAllByRole("group").length).toBeGreaterThan(0);
    expect(
      screen.getByRole("treeitem", { name: "Child" }),
    ).toHaveAttribute("aria-selected", "true");
  });

  it("expands and focuses an async nested deep-link selection", async () => {
    const view = render(
      <FolderTree tree={[]} selectedFolderId={FOLDER_C} onSelect={vi.fn()} />,
    );
    expect(
      screen.queryByRole("treeitem", { name: "Child" }),
    ).not.toBeInTheDocument();

    view.rerender(
      <FolderTree
        tree={tree}
        selectedFolderId={FOLDER_C}
        onSelect={vi.fn()}
      />,
    );
    await act(async () => {
      await Promise.resolve();
    });

    expect(screen.getByRole("treeitem", { name: "Child" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    expect(screen.getByRole("treeitem", { name: "Child" })).toHaveAttribute(
      "tabindex",
      "0",
    );
  });
});
