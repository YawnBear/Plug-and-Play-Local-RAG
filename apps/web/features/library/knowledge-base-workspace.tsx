"use client";

import {
  useCallback,
  useEffect,
  useMemo,
  useState,
  type CSSProperties,
} from "react";

import { Icon } from "@/components/shell/icons";
import { useSidebarContent } from "@/components/shell/protected-shell";
import { useMotionPresence } from "@/lib/motion";
import { ErrorState, LoadingState } from "@/components/ui/async-state";
import { NativeDialog } from "@/components/ui/native-dialog";
import { Tooltip } from "@/components/ui/tooltip";
import { useAuth } from "@/features/auth/auth-provider";
import type { KnowledgeBaseRouteState } from "@/lib/route-state";

import { BottomPanel } from "./bottom-panel";
import { Breadcrumb } from "./breadcrumb";
import { DetailsPane } from "./details-pane";
import { EditorTabs, type LibraryEditorTab } from "./editor-tabs";
import { FolderDialogs } from "./folder-dialogs";
import { FolderEditor, WelcomeEditor } from "./folder-editor";
import { KnowledgeExplorer } from "./knowledge-explorer";
import { NodeDialogs } from "./node-dialogs";
import { PdfPreview } from "./pdf-preview";
import { KnowledgeStatusBar } from "./status-bar";
import { findTreeNode } from "./tree-model";
import { UploadPanel } from "./upload-panel";
import { useAccountTeams } from "./use-account-teams";
import { useLibraryWorkspace } from "./use-library-workspace";
import type { DocumentSummary } from "./contracts";

const DETAILS_HIDDEN_KEY = "local-rag.kb-details-hidden.v1";
const DETAILS_WIDTH_KEY = "local-rag.kb-details-width.v1";

type FolderAction = "create" | "rename" | "move" | "delete";
type DocumentAction = "rename" | "move" | "delete";

const WELCOME_TAB: LibraryEditorTab = {
  id: "welcome",
  kind: "welcome",
  title: "Welcome",
  resourceId: null,
  preview: false,
};

function storedDetailsWidth(): number {
  if (typeof window === "undefined") return 300;
  const parsed = Number(window.localStorage.getItem(DETAILS_WIDTH_KEY));
  return Number.isFinite(parsed) ? Math.min(440, Math.max(260, parsed)) : 300;
}

function resourceTabId(kind: "folder" | "document", id: string | null): string {
  return `${kind}:${id ?? "root"}`;
}

export function KnowledgeBaseWorkspace({
  initialRoute,
}: {
  initialRoute: KnowledgeBaseRouteState;
}) {
  const controller = useLibraryWorkspace(initialRoute);
  const { user } = useAuth();
  const accountTeams = useAccountTeams();
  const { state } = controller;
  const {
    addFolder,
    currentFolder,
    patchNode,
    polling,
    previewMove,
    reconcileMissingDocument,
    refreshCurrent,
    removeDocument,
    reingest,
    removeFolder,
    selectDocument,
    selectedDocument: routeSelectedDocument,
    selectFolder,
    selectPage,
    upload,
  } = controller;
  const [tabs, setTabs] = useState<LibraryEditorTab[]>([WELCOME_TAB]);
  const [activeTabId, setActiveTabId] = useState(WELCOME_TAB.id);
  const [uploadOpen, setUploadOpen] = useState(false);
  const [folderAction, setFolderAction] = useState<FolderAction | null>(null);
  const [documentAction, setDocumentAction] = useState<{
    action: DocumentAction;
    document: DocumentSummary;
  } | null>(null);
  const [detailsHidden, setDetailsHidden] = useState(
    () =>
      typeof window !== "undefined" &&
      window.localStorage.getItem(DETAILS_HIDDEN_KEY) === "true",
  );
  const [detailsWidth, setDetailsWidth] = useState(storedDetailsWidth);
  const busy = state.loading || state.refreshing;
  const folderName = currentFolder?.name ?? "Root";
  const isAdmin = user?.role === "admin";
  const folderLocationResolved =
    state.selection.folderId === null || currentFolder !== null;
  const canCreateFolder =
    folderLocationResolved &&
    (currentFolder === null ? isAdmin : currentFolder.can_create_children);
  const canUpload =
    folderLocationResolved && (isAdmin || state.selection.folderId !== null);

  const showTab = useCallback((tab: LibraryEditorTab, pinned: boolean) => {
    const nextTab = { ...tab, preview: pinned ? false : tab.preview };
    setTabs((current) => {
      const existing = current.find((item) => item.id === tab.id);
      if (existing) {
        if (pinned && existing.preview) {
          return current.map((item) =>
            item.id === tab.id ? { ...item, preview: false } : item,
          );
        }
        return current;
      }
      if (nextTab.preview) {
        const previewIndex = current.findIndex((item) => item.preview);
        if (previewIndex >= 0) {
          return current.map((item, index) =>
            index === previewIndex ? nextTab : item,
          );
        }
      }
      return [...current, nextTab];
    });
    setActiveTabId(tab.id);
  }, []);

  const openFolder = useCallback(
    (folderId: string | null, pinned = false) => {
      const folder = folderId ? findTreeNode(state.tree, folderId) : null;
      selectFolder(folderId);
      showTab(
        {
          id: resourceTabId("folder", folderId),
          kind: "folder",
          title: folder?.name ?? "Root",
          resourceId: folderId,
          preview: !pinned,
        },
        pinned,
      );
    },
    [selectFolder, showTab, state.tree],
  );

  const openDocument = useCallback(
    (document: DocumentSummary, pinned = false) => {
      selectDocument(document);
      showTab(
        {
          id: resourceTabId("document", document.document_id),
          kind: "document",
          title: document.display_name,
          resourceId: document.document_id,
          preview: !pinned,
        },
        pinned,
      );
    },
    [selectDocument, showTab],
  );

  const toggleDocumentDetails = useCallback(() => {
    setDetailsHidden((current) => {
      const next = !current;
      window.localStorage.setItem(DETAILS_HIDDEN_KEY, String(next));
      return next;
    });
  }, []);

  const showDocumentDetails = useCallback(() => {
    setDetailsHidden(false);
    window.localStorage.setItem(DETAILS_HIDDEN_KEY, "false");
  }, []);

  useEffect(() => {
    const document = routeSelectedDocument;
    if (document) {
      queueMicrotask(() =>
        showTab(
          {
            id: resourceTabId("document", document.document_id),
            kind: "document",
            title: document.display_name,
            resourceId: document.document_id,
            preview: true,
          },
          false,
        ),
      );
    } else if (state.selection.folderId) {
      const folder = findTreeNode(state.tree, state.selection.folderId);
      if (folder) {
        queueMicrotask(() =>
          showTab(
            {
              id: resourceTabId("folder", folder.node_id),
              kind: "folder",
              title: folder.name,
              resourceId: folder.node_id,
              preview: true,
            },
            false,
          ),
        );
      }
    }
  }, [
    routeSelectedDocument,
    showTab,
    state.selection.folderId,
    state.tree,
  ]);

  const explorer = useMemo(
    () => ({
      render: (onNavigate?: () => void) => (
        <KnowledgeExplorer
          canAdminister={isAdmin}
          canCreateFolder={canCreateFolder}
          canUpload={canUpload}
          currentFolder={currentFolder}
          documents={state.documents}
          error={state.treeError ?? state.documentsError}
          jobs={state.jobs}
          loading={state.loading}
          onDocument={(document, pinned) => openDocument(document, pinned)}
          onDocumentAction={(document, action) => {
            openDocument(document, false);
            setDocumentAction({ action, document });
            onNavigate?.();
          }}
          onShowDocumentDetails={(document) => {
            if (state.selection.documentId !== document.document_id) {
              openDocument(document, false);
            }
            showDocumentDetails();
            onNavigate?.();
          }}
          onFolder={(folderId) => openFolder(folderId)}
          onFolderAction={(action) => {
            setFolderAction(action);
            onNavigate?.();
          }}
          onNavigate={onNavigate}
          onRefresh={() => void refreshCurrent()}
          onUpload={() => {
            setUploadOpen(true);
            onNavigate?.();
          }}
          refreshing={state.refreshing}
          selectedDocumentId={state.selection.documentId}
          selectedFolderId={state.selection.folderId}
          tree={state.tree}
        />
      ),
    }),
    [
      canCreateFolder,
      canUpload,
      currentFolder,
      isAdmin,
      openDocument,
      openFolder,
      refreshCurrent,
      showDocumentDetails,
      state.documents,
      state.documentsError,
      state.jobs,
      state.loading,
      state.refreshing,
      state.selection.documentId,
      state.selection.folderId,
      state.tree,
      state.treeError,
    ],
  );
  useSidebarContent(explorer);

  const selectedDocument =
    documentAction?.document ?? routeSelectedDocument;
  const detailsMotion = useMotionPresence(
    !detailsHidden && Boolean(selectedDocument),
  );
  const detailsLayoutHidden = !selectedDocument || detailsMotion === "closed";
  const errors = [
    state.treeError,
    state.browseError,
    state.documentsError,
    polling.pollError,
    ...state.documents.map((document) => document.error),
  ].filter((value): value is string => Boolean(value));

  function activateTab(tab: LibraryEditorTab) {
    setActiveTabId(tab.id);
    if (tab.kind === "welcome") {
      selectFolder(null);
      return;
    }
    if (tab.kind === "folder") {
      selectFolder(tab.resourceId);
      return;
    }
    const document = state.documents.find(
      (item) => item.document_id === tab.resourceId,
    );
    if (document) selectDocument(document);
  }

  function closeTab(tab: LibraryEditorTab) {
    setTabs((current) => {
      const index = current.findIndex((item) => item.id === tab.id);
      const next = current.filter((item) => item.id !== tab.id);
      if (next.length === 0) {
        queueMicrotask(() => activateTab(WELCOME_TAB));
        return [WELCOME_TAB];
      }
      if (activeTabId === tab.id) {
        const replacement = next[Math.min(index, next.length - 1)] ?? next[0];
        if (replacement) queueMicrotask(() => activateTab(replacement));
      }
      return next;
    });
  }

  function beginDetailsResize(event: React.PointerEvent<HTMLDivElement>) {
    event.preventDefault();
    const startX = event.clientX;
    const startWidth = detailsWidth;
    const move = (next: PointerEvent) => {
      setDetailsWidth(
        Math.min(440, Math.max(260, startWidth + startX - next.clientX)),
      );
    };
    const finish = (next: PointerEvent) => {
      const value = Math.min(
        440,
        Math.max(260, startWidth + startX - next.clientX),
      );
      setDetailsWidth(value);
      window.localStorage.setItem(DETAILS_WIDTH_KEY, String(value));
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", finish);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", finish);
  }

  const activeTab =
    tabs.find((tab) => tab.id === activeTabId) ?? WELCOME_TAB;
  const currentFolderDocumentCount = state.documents.filter(
    (document) => document.parent_id === state.selection.folderId,
  ).length;

  return (
    <div
      className={`kb-workbench ${
        detailsLayoutHidden ? "kb-workbench--details-hidden" : ""
      }`}
      style={
        {
          "--kb-details-width": `${detailsWidth}px`,
        } as CSSProperties
      }
    >
      <section className="kb-editor-area" aria-label="Knowledge Base editor">
        <EditorTabs
          activeId={activeTabId}
          onActivate={activateTab}
          onClose={closeTab}
          onPin={(tab) => showTab(tab, true)}
          tabs={tabs}
        />
        <div className="kb-breadcrumb-bar">
          <Breadcrumb browse={state.browse} onSelect={(id) => openFolder(id)} />
          <Tooltip content="Ingestion activity">
            <button
              aria-label="Toggle ingestion activity"
              onClick={() =>
                window.dispatchEvent(new Event("rag:toggle-ingestion-panel"))
              }
              type="button"
            >
              <Icon name="activity" />
            </button>
          </Tooltip>
          {routeSelectedDocument ? (
            <Tooltip
              content={detailsHidden ? "Show document details" : "Hide document details"}
            >
              <button
                aria-label={
                  detailsHidden ? "Show document details" : "Hide document details"
                }
                aria-pressed={!detailsHidden}
                onClick={toggleDocumentDetails}
                type="button"
              >
                <Icon name="details" />
              </button>
            </Tooltip>
          ) : null}
        </div>
        {state.notice || state.uploadNotice ? (
          <p className="kb-workbench__notice" role="status">
            {state.notice ?? state.uploadNotice}
          </p>
        ) : null}
        <div className="kb-editor-content">
          {state.loading && !state.browse ? (
            <LoadingState label="Loading knowledge base…" />
          ) : state.browseError ? (
            <ErrorState
              message={state.browseError}
              onRetry={() => void refreshCurrent()}
              retryLabel="Retry current folder"
            />
          ) : activeTab.kind === "document" && routeSelectedDocument ? (
            routeSelectedDocument.state === "ready" ? (
              <PdfPreview
                document={routeSelectedDocument}
                onMissing={reconcileMissingDocument}
                onPage={selectPage}
                page={state.selection.page ?? 1}
              />
            ) : (
              <section className="kb-processing-state">
                <Icon name="file" />
                <h1>{routeSelectedDocument.display_name}</h1>
                <p>
                  Preview becomes available when processing is ready. Current
                  status: <strong>{routeSelectedDocument.state}</strong>.
                </p>
              </section>
            )
          ) : activeTab.kind === "folder" ? (
            <FolderEditor
              browse={state.browse}
              canUpload={canUpload}
              documents={state.documents}
              folder={currentFolder}
              onDocument={(document) => openDocument(document)}
              onUpload={() => setUploadOpen(true)}
            />
          ) : (
            <WelcomeEditor
              canUpload={canUpload}
              onUpload={() => setUploadOpen(true)}
            />
          )}
        </div>
      </section>

      {selectedDocument ? (
        <div className="kb-details-wrap" data-motion={detailsMotion}>
          <div
            aria-label="Resize document details"
            className="kb-details-resize"
            onPointerDown={beginDetailsResize}
            role="separator"
          />
          <DetailsPane
            busy={busy}
            canAdminister={isAdmin}
            document={selectedDocument}
            onDelete={removeDocument}
            onReingest={reingest}
            onPatch={patchNode}
            onPreviewMove={previewMove}
            onRequestedActionHandled={() => setDocumentAction(null)}
            requestedAction={documentAction?.action ?? null}
            tree={state.tree}
          />
        </div>
      ) : null}

      <BottomPanel
        documents={state.documents}
        errors={errors}
        jobs={state.jobs}
        tracked={polling.tracked}
      />
      <KnowledgeStatusBar
        activeJobs={Object.keys(polling.tracked).length}
        readyDocuments={
          state.documents.filter((document) => document.state === "ready").length
        }
        selectedFolder={folderName}
      />

      <NativeDialog
        closeDisabled={busy}
        open={uploadOpen}
        onClose={() => setUploadOpen(false)}
        title={`Upload PDF to ${folderName}`}
      >
        <UploadPanel
          accountTeams={accountTeams.data}
          canUpload={canUpload}
          disabled={busy}
          folderName={folderName}
          notice={state.uploadNotice}
          onRetryPolling={polling.retryNow}
          onRetryTeams={() => void accountTeams.retry()}
          onUpload={async (file, teamIds) => {
            const result = await upload(file, teamIds);
            setUploadOpen(false);
            return result;
          }}
          pollingError={polling.pollError}
          requiresFolder={!isAdmin}
          teamsError={accountTeams.error}
          teamsLoading={accountTeams.loading}
        />
      </NativeDialog>

      <FolderDialogs
        busy={busy}
        currentFolder={currentFolder}
        key={folderAction ?? "idle"}
        onCreate={addFolder}
        onDelete={removeFolder}
        onPatch={patchNode}
        onPreviewMove={previewMove}
        onRequestedActionHandled={() => setFolderAction(null)}
        requestedAction={folderAction}
        showTriggers={false}
        tree={state.tree}
      />

      {documentAction && !selectedDocument ? (
        <NodeDialogs
          busy={busy}
          document={documentAction.document}
          onDelete={removeDocument}
          onPatch={patchNode}
          onPreviewMove={previewMove}
          onRequestedActionHandled={() => setDocumentAction(null)}
          requestedAction={documentAction.action}
          showTriggers={false}
          tree={state.tree}
        />
      ) : null}

      <span className="sr-only">
        {currentFolderDocumentCount} documents in the selected folder
      </span>
    </div>
  );
}
