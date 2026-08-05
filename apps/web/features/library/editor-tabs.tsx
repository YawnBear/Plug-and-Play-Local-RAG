"use client";

import { Icon } from "@/components/shell/icons";

export interface LibraryEditorTab {
  id: string;
  kind: "welcome" | "folder" | "document";
  title: string;
  resourceId: string | null;
  preview: boolean;
}

export function EditorTabs({
  tabs,
  activeId,
  onActivate,
  onClose,
  onPin,
}: {
  tabs: readonly LibraryEditorTab[];
  activeId: string;
  onActivate: (tab: LibraryEditorTab) => void;
  onClose: (tab: LibraryEditorTab) => void;
  onPin: (tab: LibraryEditorTab) => void;
}) {
  return (
    <div className="kb-editor-tabs" role="tablist" aria-label="Open editors">
      {tabs.map((tab) => {
        const active = tab.id === activeId;
        const closable = tab.kind !== "welcome" || tabs.length > 1;
        return (
          <button
            aria-keyshortcuts={closable ? "Delete" : undefined}
            aria-selected={active}
            className="kb-editor-tab"
            data-active={active ? "true" : undefined}
            data-preview={tab.preview ? "true" : undefined}
            key={tab.id}
            onClick={(event) => {
              if (
                closable &&
                (event.target as HTMLElement).closest("[data-close-editor]")
              ) {
                onClose(tab);
              } else {
                onActivate(tab);
              }
            }}
            onDoubleClick={(event) => {
              if (!(event.target as HTMLElement).closest("[data-close-editor]")) {
                onPin(tab);
              }
            }}
            onKeyDown={(event) => {
              if (closable && event.key === "Delete") {
                event.preventDefault();
                onClose(tab);
              }
            }}
            role="tab"
            tabIndex={active ? 0 : -1}
            type="button"
          >
            <span className="kb-editor-tab__select">
              <Icon name={tab.kind === "document" ? "file" : "folder"} />
              <span>{tab.title}</span>
              {tab.preview ? (
                <span className="sr-only">Preview</span>
              ) : (
                <Icon className="kb-editor-tab__pin" name="pin" />
              )}
            </span>
            {closable ? (
              <span
                aria-hidden="true"
                className="kb-editor-tab__close"
                data-close-editor
              >
                <Icon name="close" />
              </span>
            ) : null}
          </button>
        );
      })}
    </div>
  );
}
