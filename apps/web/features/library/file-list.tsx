import { Icon } from "@/components/shell/icons";

import type {
  DocumentSummary,
  JobStatus,
  LibraryBrowse,
} from "./contracts";

function documentStatus(
  document: DocumentSummary,
  job: JobStatus | undefined,
): string {
  if (job?.status === "completed") return "ready";
  return job?.status ?? document.state;
}

export function FileList({
  browse,
  documents,
  jobs,
  selectedDocumentId,
  onFolder,
  onDocument,
}: {
  browse: LibraryBrowse | null;
  documents: readonly DocumentSummary[];
  jobs: Readonly<Record<string, JobStatus>>;
  selectedDocumentId: string | null;
  onFolder: (folderId: string) => void;
  onDocument: (document: DocumentSummary) => void;
}) {
  const byNode = new Map(
    documents.map((document) => [document.node_id, document]),
  );
  const children = browse?.children ?? [];
  return (
    <section aria-labelledby="folder-contents-title" className="min-w-0">
      <div className="flex min-h-11 items-center justify-between gap-3 border-b border-[var(--hairline)]">
        <h2 id="folder-contents-title">Folder contents</h2>
        <span className="text-sm text-[var(--mute)]">
          {children.length} item{children.length === 1 ? "" : "s"}
        </span>
      </div>
      {children.length === 0 ? (
        <p className="py-6 text-sm leading-7 text-[var(--mute)]">
          This folder is empty. Create a folder or upload a PDF here.
        </p>
      ) : (
        <ul className="m-0 list-none p-0">
          {children.map((node) => {
            if (node.kind === "folder") {
              return (
                <li key={node.node_id} className="border-b border-[var(--hairline)]">
                  <button
                    type="button"
                    onClick={() => onFolder(node.node_id)}
                    className="grid min-h-14 w-full grid-cols-[auto_minmax(0,1fr)] items-center gap-3 px-2 text-left"
                  >
                    <span className="library-item__icon" aria-hidden="true">
                      <Icon name="folder" />
                    </span>
                    <span className="min-w-0">
                      <strong className="block">{node.name}</strong>
                      <span className="block text-sm text-[var(--mute)]">
                        {node.logical_path}
                      </span>
                    </span>
                  </button>
                </li>
              );
            }
            const document = byNode.get(node.node_id);
            if (!document) {
              return (
                <li
                  key={node.node_id}
                  className="grid min-h-14 grid-cols-[auto_minmax(0,1fr)] items-center gap-3 border-b border-[var(--hairline)] px-2 text-sm text-[var(--mute)]"
                >
                  <span className="library-item__icon"><Icon name="file" /></span>
                  <span>
                    {node.name}
                    <span className="block">
                      Live document metadata is temporarily unavailable.
                    </span>
                  </span>
                </li>
              );
            }
            const selected = document.document_id === selectedDocumentId;
            const status = documentStatus(
              document,
              jobs[document.document_id],
            );
            return (
              <li
                key={node.node_id}
                className={`border-b border-[var(--hairline)] ${
                  selected ? "bg-[var(--surface-soft)]" : ""
                }`}
              >
                <button
                  type="button"
                  onClick={() => onDocument(document)}
                  aria-current={selected ? "true" : undefined}
                  className="grid min-h-14 w-full grid-cols-[auto_minmax(0,1fr)_auto] items-center gap-3 px-2 text-left"
                >
                  <span className="library-item__icon" aria-hidden="true">
                    <Icon name="file" />
                  </span>
                  <span className="min-w-0">
                    <strong className="block">{document.display_name}</strong>
                    <span className="block text-sm text-[var(--mute)]">
                      {document.logical_path}
                    </span>
                  </span>
                  <span className="text-sm uppercase text-[var(--mute)]">
                    {status}
                  </span>
                </button>
                {jobs[document.document_id] ? (
                  <p
                    role="status"
                    className="m-0 px-2 pb-2 pl-14 text-sm text-[var(--mute)]"
                  >
                    {jobs[document.document_id].stage}:{" "}
                    {jobs[document.document_id].completed_units}
                    {jobs[document.document_id].total_units !== null
                      ? `/${jobs[document.document_id].total_units}`
                      : ""}
                  </p>
                ) : null}
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
