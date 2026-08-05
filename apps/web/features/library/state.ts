import type {
  DocumentSummary,
  JobStatus,
  LibraryBrowse,
  LibraryTreeNode,
} from "./contracts";

export interface LibrarySelection {
  folderId: string | null;
  documentId: string | null;
  page: number | null;
}

export interface LibraryWorkspaceState {
  selection: LibrarySelection;
  tree: LibraryTreeNode[];
  browse: LibraryBrowse | null;
  documents: DocumentSummary[];
  jobs: Record<string, JobStatus>;
  loading: boolean;
  refreshing: boolean;
  treeError: string | null;
  browseError: string | null;
  documentsError: string | null;
  notice: string | null;
  uploadNotice: string | null;
}

export function initialLibraryWorkspaceState(
  selection: LibrarySelection,
): LibraryWorkspaceState {
  return {
    selection,
    tree: [],
    browse: null,
    documents: [],
    jobs: {},
    loading: true,
    refreshing: false,
    treeError: null,
    browseError: null,
    documentsError: null,
    notice: null,
    uploadNotice: null,
  };
}

export type LibraryWorkspaceAction =
  | { type: "load-start"; initial: boolean; folderId: string | null }
  | {
      type: "load-complete";
      selection: LibrarySelection;
      tree?: LibraryTreeNode[];
      browse?: LibraryBrowse;
      documents?: DocumentSummary[];
      treeError: string | null;
      browseError: string | null;
      documentsError: string | null;
      notice: string | null;
    }
  | { type: "select"; selection: LibrarySelection }
  | { type: "job"; job: JobStatus }
  | { type: "upload-notice"; message: string | null };

export function libraryWorkspaceReducer(
  state: LibraryWorkspaceState,
  action: LibraryWorkspaceAction,
): LibraryWorkspaceState {
  switch (action.type) {
    case "load-start":
      return {
        ...state,
        browse:
          state.browse?.parent_id === action.folderId ? state.browse : null,
        loading:
          state.browse === null ||
          state.browse.parent_id !== action.folderId,
        refreshing:
          state.browse !== null &&
          state.browse.parent_id === action.folderId,
      };
    case "load-complete":
      return {
        ...state,
        selection: action.selection,
        tree: action.tree ?? state.tree,
        browse:
          action.browse ??
          (state.browse?.parent_id === action.selection.folderId
            ? state.browse
            : null),
        documents: action.documents ?? state.documents,
        loading: false,
        refreshing: false,
        treeError: action.treeError,
        browseError: action.browseError,
        documentsError: action.documentsError,
        notice: action.notice,
      };
    case "select":
      return {
        ...state,
        selection: action.selection,
        browse:
          state.browse?.parent_id === action.selection.folderId
            ? state.browse
            : null,
      };
    case "job":
      return {
        ...state,
        jobs: { ...state.jobs, [action.job.document_id]: action.job },
      };
    case "upload-notice":
      return { ...state, uploadNotice: action.message };
  }
}

export function normalizeDocumentPage(
  requestedPage: number | null,
  pageCount: number | null,
): number {
  const page =
    requestedPage !== null && Number.isSafeInteger(requestedPage)
      ? Math.max(1, requestedPage)
      : 1;
  return pageCount === null ? page : Math.min(page, Math.max(1, pageCount));
}

export function currentDocument(
  state: Pick<LibraryWorkspaceState, "selection" | "documents">,
): DocumentSummary | null {
  if (!state.selection.documentId) return null;
  return (
    state.documents.find(
      (document) => document.document_id === state.selection.documentId,
    ) ?? null
  );
}

export function currentFolderNode(
  browse: LibraryBrowse | null,
): LibraryBrowse["breadcrumbs"][number] | null {
  return browse?.breadcrumbs.at(-1) ?? null;
}
