import { describe, expect, it } from "vitest";

import {
  initialLibraryWorkspaceState,
  libraryWorkspaceReducer,
  normalizeDocumentPage,
} from "@/features/library/state";

import { DOCUMENT, FOLDER_A, browse, documentSummary, tree } from "../library-fixtures";

describe("library workspace state", () => {
  it("normalizes selected pages against live page counts", () => {
    expect(normalizeDocumentPage(null, 5)).toBe(1);
    expect(normalizeDocumentPage(0, 5)).toBe(1);
    expect(normalizeDocumentPage(99, 5)).toBe(5);
    expect(normalizeDocumentPage(99, null)).toBe(99);
  });

  it("retains prior data when an authoritative refresh fails", () => {
    let state = initialLibraryWorkspaceState({
      folderId: FOLDER_A,
      documentId: DOCUMENT,
      page: 2,
    });
    state = libraryWorkspaceReducer(state, {
      type: "load-complete",
      selection: state.selection,
      tree,
      browse: browse(),
      documents: [documentSummary()],
      treeError: null,
      browseError: null,
      documentsError: null,
      notice: null,
    });
    state = libraryWorkspaceReducer(state, {
      type: "load-complete",
      selection: state.selection,
      treeError: "tree offline",
      browseError: "browse offline",
      documentsError: "documents offline",
      notice: null,
    });
    expect(state.tree).toEqual(tree);
    expect(state.browse).toEqual(browse());
    expect(state.documents).toEqual([documentSummary()]);
  });
});
