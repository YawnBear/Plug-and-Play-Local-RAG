"use client";

import { Tooltip } from "@/components/ui/tooltip";

export function KnowledgeStatusBar({
  readyDocuments,
  activeJobs,
  selectedFolder,
}: {
  readyDocuments: number;
  activeJobs: number;
  selectedFolder: string;
}) {
  return (
    <footer className="kb-status-bar" aria-label="Knowledge Base status">
      <Tooltip content="Documents ready for retrieval">
        <span>{readyDocuments} ready</span>
      </Tooltip>
      <Tooltip content="Current-session ingestion jobs">
        <button
          onClick={() =>
            window.dispatchEvent(new Event("rag:toggle-ingestion-panel"))
          }
          type="button"
        >
          {activeJobs} indexing
        </button>
      </Tooltip>
      <Tooltip content={`Selected folder: ${selectedFolder}`}>
        <span className="kb-status-bar__folder">{selectedFolder}</span>
      </Tooltip>
      <span className="kb-status-bar__spacer" />
      <Tooltip content="Private LAN deployment; no cloud document transfer">
        <span>Local only</span>
      </Tooltip>
    </footer>
  );
}
