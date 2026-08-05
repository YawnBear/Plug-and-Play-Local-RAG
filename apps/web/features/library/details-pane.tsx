import { Icon } from "@/components/shell/icons";
import { useEffect, useRef, useState } from "react";

import type {
  DocumentSummary,
  LibraryTreeNode,
  NodeMovePreview,
} from "./contracts";
import { NodeDialogs } from "./node-dialogs";

type DocumentAction = "rename" | "move" | "delete";

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "Unable to reingest document.";
}

function ReingestAction({
  busy,
  document,
  onReingest,
}: {
  busy: boolean;
  document: DocumentSummary;
  onReingest: (document: DocumentSummary) => Promise<unknown>;
}) {
  const [reingestBusy, setReingestBusy] = useState(false);
  const [reingestError, setReingestError] = useState<string | null>(null);
  const mountedRef = useRef(true);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  async function handleReingest() {
    if (reingestBusy) return;
    setReingestBusy(true);
    setReingestError(null);
    try {
      await onReingest(document);
    } catch (error) {
      if (mountedRef.current) setReingestError(errorMessage(error));
    } finally {
      if (mountedRef.current) setReingestBusy(false);
    }
  }

  return (
    <div className="kb-details__action">
      <button
        className="button-secondary"
        disabled={busy || reingestBusy}
        onClick={() => void handleReingest()}
        type="button"
      >
        {reingestBusy ? "Reingesting…" : "Reingest"}
      </button>
      {reingestError ? (
        <p role="alert" className="kb-details__error">
          {reingestError}
        </p>
      ) : null}
    </div>
  );
}

function formatDate(value: string): string {
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
}

export function DetailsPane({
  tree,
  document,
  busy,
  onPatch,
  onPreviewMove,
  onDelete,
  onReingest,
  canAdminister,
  requestedAction,
  onRequestedActionHandled,
}: {
  tree: readonly LibraryTreeNode[];
  document: DocumentSummary;
  busy: boolean;
  onPatch: (
    nodeId: string,
    patch: {
      name?: string;
      parent_id?: string | null;
      preview_id?: string;
      impact_digest?: string;
    },
  ) => Promise<unknown>;
  onPreviewMove: (
    nodeId: string,
    parentId: string | null,
  ) => Promise<NodeMovePreview>;
  onDelete: (document: DocumentSummary) => Promise<unknown>;
  onReingest: (document: DocumentSummary) => Promise<unknown>;
  canAdminister: boolean;
  requestedAction: DocumentAction | null;
  onRequestedActionHandled: () => void;
}) {
  return (
    <aside className="kb-details" aria-labelledby="details-title">
      <header>
        <span aria-hidden="true"><Icon name="details" /></span>
        <div>
          <p>Document details</p>
          <h2 id="details-title">{document.display_name}</h2>
        </div>
      </header>
      <div className="kb-details__sections">
        <details open>
          <summary>General</summary>
          <dl>
            <dt>Name</dt>
            <dd>{document.display_name}</dd>
            <dt>Folder path</dt>
            <dd>{document.logical_path}</dd>
            <dt>Original file</dt>
            <dd>{document.filename}</dd>
            <dt>Pages</dt>
            <dd>{document.page_count ?? "Processing"}</dd>
            <dt>Uploaded</dt>
            <dd>{formatDate(document.created_at)}</dd>
            <dt>Updated</dt>
            <dd>{formatDate(document.updated_at)}</dd>
          </dl>
        </details>
        <details open>
          <summary>Ingestion</summary>
          <dl>
            <dt>Status</dt>
            <dd>{document.state}</dd>
            <dt>Chunks</dt>
            <dd>{document.chunk_count}</dd>
          </dl>
          {document.error ? (
            <p role="alert" className="kb-details__error">
              {document.error}
            </p>
          ) : null}
        </details>
        <details>
          <summary>Access</summary>
          <dl>
            <dt>Management</dt>
            <dd>{document.can_manage ? "Can manage" : "Read only"}</dd>
            <dt>Team access</dt>
            <dd>
              {document.team_ids.length > 0
                ? `${document.team_ids.length} team${document.team_ids.length === 1 ? "" : "s"}`
                : "Uploader only"}
            </dd>
          </dl>
          {canAdminister ? (
            <a
              className="kb-details__link"
              href={`/admin/access?node=${document.node_id}`}
            >
              Manage access
            </a>
          ) : null}
        </details>
        {document.can_manage ? (
          <details open>
            <summary>Actions</summary>
            {document.state === "failed" ? (
              <ReingestAction
                busy={busy}
                document={document}
                key={document.document_id}
                onReingest={onReingest}
              />
            ) : null}
            <NodeDialogs
              busy={busy}
              document={document}
              key={`${document.document_id}-${requestedAction ?? "idle"}`}
              onDelete={onDelete}
              onPatch={onPatch}
              onPreviewMove={onPreviewMove}
              onRequestedActionHandled={onRequestedActionHandled}
              requestedAction={requestedAction}
              tree={tree}
            />
          </details>
        ) : requestedAction ? (
          <NodeDialogs
            busy={busy}
            document={document}
            key={`${document.document_id}-${requestedAction}`}
            onDelete={onDelete}
            onPatch={onPatch}
            onPreviewMove={onPreviewMove}
            onRequestedActionHandled={onRequestedActionHandled}
            requestedAction={requestedAction}
            showTriggers={false}
            tree={tree}
          />
        ) : null}
      </div>
    </aside>
  );
}
