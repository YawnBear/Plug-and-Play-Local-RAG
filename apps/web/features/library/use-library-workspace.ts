"use client";

import { useRouter } from "next/navigation";
import {
  useCallback,
  useEffect,
  useReducer,
  useRef,
} from "react";

import { ApiError } from "@/lib/http";
import {
  knowledgeBaseRoute,
  type KnowledgeBaseRouteState,
} from "@/lib/route-state";

import {
  browseLibrary,
  createFolder,
  deleteDocument,
  deleteFolder,
  getLibraryTree,
  listDocuments,
  previewNodeMove,
  reingestDocument,
  updateNode,
  uploadDocument,
} from "./api";
import type {
  DocumentSummary,
  LibraryNode,
  NodeMovePreview,
} from "./contracts";
import {
  currentDocument,
  currentFolderNode,
  initialLibraryWorkspaceState,
  libraryWorkspaceReducer,
  normalizeDocumentPage,
  type LibrarySelection,
} from "./state";
import { useJobPolling } from "./use-job-polling";

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "An unexpected error occurred.";
}

function isAbort(error: unknown): boolean {
  return (
    typeof error === "object" &&
    error !== null &&
    "name" in error &&
    error.name === "AbortError"
  );
}

function selectionFromRoute(route: KnowledgeBaseRouteState): LibrarySelection {
  return {
    folderId: route.folderId,
    documentId: route.documentId,
    page: route.documentId ? (route.page ?? 1) : null,
  };
}

function browserUrl(): string | null {
  if (typeof window === "undefined") return null;
  return `${window.location.pathname}${window.location.search}`;
}

export function useLibraryWorkspace(initialRoute: KnowledgeBaseRouteState) {
  const router = useRouter();
  const [state, dispatch] = useReducer(
    libraryWorkspaceReducer,
    undefined,
    () => initialLibraryWorkspaceState(selectionFromRoute(initialRoute)),
  );
  const stateRef = useRef(state);
  const requestGeneration = useRef(0);
  const requestController = useRef<AbortController | null>(null);
  const mounted = useRef(true);
  const mutationControllers = useRef(new Set<AbortController>());
  const routeKey = [
    initialRoute.folderId,
    initialRoute.documentId,
    initialRoute.page,
    initialRoute.invalidFolder,
    initialRoute.invalidDocument,
    initialRoute.invalidPage,
  ].join("|");

  useEffect(() => {
    stateRef.current = state;
  }, [state]);

  useEffect(() => {
    const controllers = mutationControllers.current;
    mounted.current = true;
    return () => {
      mounted.current = false;
      for (const controller of controllers) {
        controller.abort();
      }
      controllers.clear();
    };
  }, []);

  const replaceIfChanged = useCallback(
    (selection: LibrarySelection) => {
      const canonical = knowledgeBaseRoute({
        folderId: selection.folderId,
        documentId: selection.documentId,
        page: selection.page,
      });
      if (browserUrl() !== canonical) router.replace(canonical);
    },
    [router],
  );

  const load = useCallback(
    async ({
      selection,
      invalidFolder = false,
      invalidDocument = false,
      invalidPage = false,
      initial = false,
      notice: suppliedNotice = null,
      reconcileUrl = true,
    }: {
      selection: LibrarySelection;
      invalidFolder?: boolean;
      invalidDocument?: boolean;
      invalidPage?: boolean;
      initial?: boolean;
      notice?: string | null;
      reconcileUrl?: boolean;
    }) => {
      requestController.current?.abort();
      const controller = new AbortController();
      requestController.current = controller;
      const generation = ++requestGeneration.current;

      let requested: LibrarySelection = {
        folderId: invalidFolder ? null : selection.folderId,
        documentId: invalidDocument ? null : selection.documentId,
        page: invalidDocument ? null : selection.page,
      };
      let notice = suppliedNotice;
      if (invalidFolder) {
        notice =
          "That folder link is invalid. Showing the knowledge-base root.";
      }
      if (invalidDocument) {
        notice =
          "That document link is invalid. The current folder remains selected.";
      }
      if (invalidPage && requested.documentId) {
        requested = { ...requested, page: 1 };
        notice = "That page number is invalid. Showing page 1.";
      }
      dispatch({
        type: "load-start",
        initial,
        folderId: requested.folderId,
      });

      const [treeResult, documentsResult, browseResult] =
        await Promise.allSettled([
          getLibraryTree(controller.signal),
          listDocuments(controller.signal),
          browseLibrary(requested.folderId, controller.signal),
        ]);
      if (controller.signal.aborted || generation !== requestGeneration.current) {
        return null;
      }

      const tree =
        treeResult.status === "fulfilled" ? treeResult.value : undefined;
      const documents =
        documentsResult.status === "fulfilled"
          ? documentsResult.value
          : undefined;
      const authoritativeDocuments =
        documents ?? stateRef.current.documents;
      let browse =
        browseResult.status === "fulfilled" ? browseResult.value : undefined;
      let treeError =
        treeResult.status === "rejected" && !isAbort(treeResult.reason)
          ? errorMessage(treeResult.reason)
          : null;
      const documentsError =
        documentsResult.status === "rejected" &&
        !isAbort(documentsResult.reason)
          ? errorMessage(documentsResult.reason)
          : null;
      let browseError =
        browseResult.status === "rejected" && !isAbort(browseResult.reason)
          ? errorMessage(browseResult.reason)
          : null;

      if (
        browseResult.status === "rejected" &&
        browseResult.reason instanceof ApiError &&
        browseResult.reason.status === 404 &&
        requested.folderId
      ) {
        requested = { ...requested, folderId: null };
        notice =
          "That folder no longer exists. Showing the knowledge-base root.";
        try {
          browse = await browseLibrary(null, controller.signal);
          browseError = null;
        } catch (error) {
          if (!isAbort(error)) browseError = errorMessage(error);
        }
      }

      if (requested.documentId && documents !== undefined) {
        const document = documents.find(
          (item) => item.document_id === requested.documentId,
        );
        if (!document) {
          requested = {
            ...requested,
            documentId: null,
            page: null,
          };
          notice =
            "That document no longer exists. The current folder remains selected.";
        } else {
          requested = {
            folderId: document.parent_id,
            documentId: document.document_id,
            page: normalizeDocumentPage(requested.page, document.page_count),
          };
          if (browse?.parent_id !== document.parent_id) {
            try {
              browse = await browseLibrary(
                document.parent_id,
                controller.signal,
              );
              browseError = null;
            } catch (error) {
              if (!isAbort(error)) browseError = errorMessage(error);
            }
          }
        }
      }

      if (controller.signal.aborted || generation !== requestGeneration.current) {
        return null;
      }
      if (treeResult.status === "rejected" && isAbort(treeResult.reason)) {
        treeError = null;
      }
      dispatch({
        type: "load-complete",
        selection: requested,
        tree,
        browse,
        documents,
        treeError,
        browseError,
        documentsError,
        notice,
      });
      if (reconcileUrl) replaceIfChanged(requested);
      return {
        selection: requested,
        tree: tree ?? stateRef.current.tree,
        browse: browse ?? stateRef.current.browse,
        documents: authoritativeDocuments,
      };
    },
    [replaceIfChanged],
  );

  useEffect(() => {
    void load({
      selection: selectionFromRoute(initialRoute),
      invalidFolder: initialRoute.invalidFolder,
      invalidDocument: initialRoute.invalidDocument,
      invalidPage: initialRoute.invalidPage,
      initial: true,
    });
    return () => {
      requestGeneration.current += 1;
      requestController.current?.abort();
    };
    // routeKey represents the complete server route state.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [routeKey, load]);

  const refreshCurrent = useCallback(
    (notice?: string | null) =>
      load({
        selection: stateRef.current.selection,
        notice,
      }),
    [load],
  );

  const polling = useJobPolling({
    onJob: (job) => dispatch({ type: "job", job }),
    onTerminal: (job) => {
      dispatch({ type: "job", job });
      void refreshCurrent(
        job.status === "completed"
          ? "Document processing completed."
          : `Document processing ${job.status}.`,
      );
    },
  });

  const commitSelection = useCallback((selection: LibrarySelection) => {
    stateRef.current = {
      ...stateRef.current,
      selection,
      browse:
        stateRef.current.browse?.parent_id === selection.folderId
          ? stateRef.current.browse
          : null,
    };
    dispatch({ type: "select", selection });
  }, []);

  const pushSelection = useCallback(
    (selection: LibrarySelection) => {
      commitSelection(selection);
      router.push(
        knowledgeBaseRoute({
          folderId: selection.folderId,
          documentId: selection.documentId,
          page: selection.page,
        }),
      );
      void load({ selection, reconcileUrl: false });
    },
    [commitSelection, load, router],
  );

  const selectFolder = useCallback(
    (folderId: string | null) =>
      pushSelection({ folderId, documentId: null, page: null }),
    [pushSelection],
  );

  const selectDocument = useCallback(
    (document: DocumentSummary) =>
      pushSelection({
        folderId: document.parent_id,
        documentId: document.document_id,
        page: 1,
      }),
    [pushSelection],
  );

  const selectPage = useCallback(
    (page: number) => {
      const document = currentDocument(stateRef.current);
      if (!document) return;
      const selection = {
        ...stateRef.current.selection,
        page: normalizeDocumentPage(page, document.page_count),
      };
      commitSelection(selection);
      replaceIfChanged(selection);
    },
    [commitSelection, replaceIfChanged],
  );

  const mutation = useCallback(
    async <T,>(
      operation: (
        signal: AbortSignal,
        assertActive: () => void,
      ) => Promise<T>,
    ): Promise<T> => {
      const controller = new AbortController();
      mutationControllers.current.add(controller);
      const assertActive = () => {
        if (!mounted.current || controller.signal.aborted) {
          throw (
            controller.signal.reason ??
            new DOMException("The operation was aborted.", "AbortError")
          );
        }
      };
      try {
        const result = await operation(controller.signal, assertActive);
        assertActive();
        return result;
      } catch (error) {
        if (
          mounted.current &&
          !controller.signal.aborted &&
          error instanceof ApiError &&
          error.status === 409
        ) {
          await refreshCurrent();
        }
        throw error;
      } finally {
        mutationControllers.current.delete(controller);
      }
    },
    [refreshCurrent],
  );

  const addFolder = useCallback(
    (name: string) =>
      mutation(async (signal, assertActive) => {
        const result = await createFolder(
          name,
          stateRef.current.selection.folderId,
          signal,
        );
        assertActive();
        await refreshCurrent("Folder created.");
        assertActive();
        return result;
      }),
    [mutation, refreshCurrent],
  );

  const patchNode = useCallback(
    (
      nodeId: string,
      patch: {
        name?: string;
        parent_id?: string | null;
        preview_id?: string;
        impact_digest?: string;
      },
    ) =>
      mutation(async (signal, assertActive) => {
        const result = await updateNode(nodeId, patch, signal);
        assertActive();
        await refreshCurrent();
        assertActive();
        return result;
      }),
    [mutation, refreshCurrent],
  );

  const previewMove = useCallback(
    (nodeId: string, parentId: string | null): Promise<NodeMovePreview> =>
      mutation((signal) => previewNodeMove(nodeId, parentId, signal)),
    [mutation],
  );

  const removeFolder = useCallback(
    (folder: LibraryNode) =>
      mutation(async (signal, assertActive) => {
        await deleteFolder(folder.node_id, signal);
        assertActive();
        const active =
          stateRef.current.selection.folderId === folder.node_id;
        if (active) {
          const selection = {
            folderId: folder.parent_id,
            documentId: null,
            page: null,
          };
          commitSelection(selection);
          replaceIfChanged(selection);
          await load({
            selection,
            notice: "Folder deleted.",
            reconcileUrl: false,
          });
          assertActive();
        } else {
          await refreshCurrent("Folder deleted.");
          assertActive();
        }
      }),
    [commitSelection, load, mutation, refreshCurrent, replaceIfChanged],
  );

  const removeDocument = useCallback(
    (document: DocumentSummary) =>
      mutation(async (signal, assertActive) => {
        await deleteDocument(document.document_id, signal);
        assertActive();
        polling.untrackDocument(document.document_id);
        const selected =
          stateRef.current.selection.documentId === document.document_id;
        if (selected) {
          const selection = {
            folderId: stateRef.current.selection.folderId,
            documentId: null,
            page: null,
          };
          commitSelection(selection);
          replaceIfChanged(selection);
          await load({
            selection,
            notice: "Document deleted.",
            reconcileUrl: false,
          });
          assertActive();
        } else {
          await refreshCurrent("Document deleted.");
          assertActive();
        }
      }),
    [
      commitSelection,
      load,
      mutation,
      polling,
      refreshCurrent,
      replaceIfChanged,
    ],
  );

  const upload = useCallback(
    async (file: File, teamIds: readonly string[]) =>
      mutation(async (signal, assertActive) => {
        const result = await uploadDocument(
          file,
          stateRef.current.selection.folderId,
          teamIds,
          signal,
        );
        assertActive();
        const duplicate = result.location_reused || result.duplicate_of !== null;
        dispatch({
          type: "upload-notice",
          message: duplicate
            ? `This PDF already exists at ${result.logical_path}. Your existing authorized file was reused; ownership and access were not changed.`
            : `Upload accepted at ${result.logical_path}.`,
        });
        const selection = {
          folderId: result.parent_id,
          documentId: result.document_id,
          page: 1,
        };
        commitSelection(selection);
        await load({ selection, reconcileUrl: false });
        assertActive();
        router.push(
          knowledgeBaseRoute({
            folderId: selection.folderId,
            documentId: selection.documentId,
            page: selection.page,
          }),
        );
        polling.track(result);
        return result;
      }),
    [commitSelection, load, mutation, polling, router],
  );

  const reingest = useCallback(
    (document: DocumentSummary) =>
      mutation(async (signal, assertActive) => {
        const result = await reingestDocument(document.document_id, signal);
        assertActive();
        await refreshCurrent("Document reingest queued.");
        assertActive();
        polling.track(result);
        return result;
      }),
    [mutation, polling, refreshCurrent],
  );

  const reconcileMissingDocument = useCallback(() => {
    const selection = {
      folderId: stateRef.current.selection.folderId,
      documentId: null,
      page: null,
    };
    commitSelection(selection);
    replaceIfChanged(selection);
    void load({
      selection,
      notice: "That document is no longer available.",
    });
  }, [commitSelection, load, replaceIfChanged]);

  return {
    state,
    selectedDocument: currentDocument(state),
    currentFolder: currentFolderNode(state.browse),
    routeKey: knowledgeBaseRoute({
      folderId: state.selection.folderId,
      documentId: state.selection.documentId,
      page: state.selection.page,
    }),
    polling,
    refreshCurrent,
    selectFolder,
    selectDocument,
    selectPage,
    addFolder,
    patchNode,
    previewMove,
    removeFolder,
    removeDocument,
    reingest,
    upload,
    reconcileMissingDocument,
  };
}

export type LibraryWorkspaceController = ReturnType<
  typeof useLibraryWorkspace
>;
