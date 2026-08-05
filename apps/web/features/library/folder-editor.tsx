import { Icon } from "@/components/shell/icons";

import type {
  DocumentSummary,
  LibraryBrowse,
  LibraryNode,
} from "./contracts";

export function WelcomeEditor({
  canUpload,
  onUpload,
}: {
  canUpload: boolean;
  onUpload: () => void;
}) {
  return (
    <section className="kb-welcome" aria-labelledby="kb-welcome-title">
      <Icon name="knowledge" />
      <h1 id="kb-welcome-title">Knowledge Base</h1>
      <p>Open a PDF or folder from the Explorer.</p>
      <button
        className="button-primary"
        disabled={!canUpload}
        onClick={onUpload}
        type="button"
      >
        <Icon name="upload" />
        Upload PDF
      </button>
    </section>
  );
}

export function FolderEditor({
  folder,
  browse,
  documents,
  canUpload,
  onUpload,
  onDocument,
}: {
  folder: LibraryNode | null;
  browse: LibraryBrowse | null;
  documents: readonly DocumentSummary[];
  canUpload: boolean;
  onUpload: () => void;
  onDocument: (document: DocumentSummary) => void;
}) {
  const folderId = folder?.node_id ?? null;
  const childFolders =
    browse?.children.filter((node) => node.kind === "folder").length ?? 0;
  const childDocuments = documents.filter(
    (document) => document.parent_id === folderId,
  );

  return (
    <section className="kb-folder-editor" aria-labelledby="kb-folder-title">
      <div className="kb-folder-editor__heading">
        <span className="kb-folder-editor__icon" aria-hidden="true">
          <Icon name="folder" />
        </span>
        <div>
          <p>Folder overview</p>
          <h1 id="kb-folder-title">{folder?.name ?? "Root"}</h1>
          <span>{folder?.logical_path ?? "/"}</span>
        </div>
        <button
          className="button-primary"
          disabled={!canUpload}
          onClick={onUpload}
          type="button"
        >
          <Icon name="upload" />
          Upload PDF
        </button>
      </div>
      <dl className="kb-folder-stats">
        <div>
          <dt>Folders</dt>
          <dd>{childFolders}</dd>
        </div>
        <div>
          <dt>PDFs</dt>
          <dd>{childDocuments.length}</dd>
        </div>
        <div>
          <dt>Ready</dt>
          <dd>
            {
              childDocuments.filter((document) => document.state === "ready")
                .length
            }
          </dd>
        </div>
      </dl>
      <div className="kb-recent">
        <h2>Documents</h2>
        {childDocuments.length === 0 ? (
          <p>This folder is empty. Upload a PDF to begin.</p>
        ) : (
          <ul>
            {childDocuments.slice(0, 8).map((document) => (
              <li key={document.document_id}>
                <button onClick={() => onDocument(document)} type="button">
                  <Icon name="file" />
                  <span>
                    <strong>{document.display_name}</strong>
                    <small>{document.state}</small>
                  </span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
    </section>
  );
}
