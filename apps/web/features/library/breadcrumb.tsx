import type { LibraryBrowse } from "./contracts";

export function Breadcrumb({
  browse,
  onSelect,
}: {
  browse: LibraryBrowse | null;
  onSelect: (folderId: string | null) => void;
}) {
  return (
    <nav aria-label="Folder breadcrumb">
      <ol className="m-0 flex list-none flex-wrap items-center gap-x-2 p-0 text-sm leading-7">
        <li>
          <button
            type="button"
            onClick={() => onSelect(null)}
            className="min-h-11 min-w-11 px-1 underline underline-offset-4"
          >
            Root
          </button>
        </li>
        {browse?.breadcrumbs.map((node) => (
          <li key={node.node_id} className="flex items-center gap-2">
            <span aria-hidden="true">/</span>
            <button
              type="button"
              onClick={() => onSelect(node.node_id)}
              aria-current={
                node.node_id === browse.parent_id ? "location" : undefined
              }
              className="min-h-11 min-w-11 px-1 underline underline-offset-4"
            >
              {node.name}
            </button>
          </li>
        ))}
      </ol>
    </nav>
  );
}
