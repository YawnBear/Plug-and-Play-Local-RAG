from __future__ import annotations

import json
import unicodedata
import uuid
from collections.abc import Mapping
from dataclasses import dataclass

from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import LibraryNode
from app.schemas.admin import AdminAclImpact
from app.security.actor import ActorContext, ActorRole

LIBRARY_ADVISORY_LOCK_ID = -8193459957385355962
MAX_LIBRARY_NODES = 10_000
MAX_LIBRARY_DEPTH = 256

_VISIBLE_PATHS_CTE = """
WITH RECURSIVE visible_paths AS (
    SELECT node.id, node.parent_id, node.kind, node.name, node.name_key,
           node.document_id, node.uploader_user_id,
           public.v4_can_create_children(node.id) AS can_create_children,
           ('/' || node.name)::text AS logical_path,
           ARRAY[node.id]::uuid[] AS path_ids,
           ARRAY[node.name]::text[] AS path_names
    FROM library_nodes AS node
    WHERE node.parent_id IS NULL
      AND v4_current_actor_id() = :actor_id
      AND v4_can_view_library_node(node.id)
    UNION ALL
    SELECT child.id, child.parent_id, child.kind, child.name, child.name_key,
           child.document_id, child.uploader_user_id,
           public.v4_can_create_children(child.id) AS can_create_children,
           (parent.logical_path || '/' || child.name)::text,
           parent.path_ids || child.id,
           parent.path_names || child.name
    FROM library_nodes AS child
    JOIN visible_paths AS parent ON parent.id = child.parent_id
    WHERE NOT child.id = ANY(parent.path_ids)
      AND cardinality(parent.path_ids) < 256
      AND v4_current_actor_id() = :actor_id
      AND v4_can_view_library_node(child.id)
)
"""

_LIBRARY_PARENT_SQL = text(
    _VISIBLE_PATHS_CTE
    + """
SELECT id, parent_id, kind, name, name_key, document_id, uploader_user_id,
       logical_path, can_create_children
FROM visible_paths
WHERE id = :parent_id
"""
)

_LIBRARY_BREADCRUMBS_SQL = text(
    _VISIBLE_PATHS_CTE
    + """
, target AS (
    SELECT path_ids FROM visible_paths WHERE id = :parent_id
)
SELECT node.id, node.parent_id, node.kind, node.name, node.name_key,
       node.document_id, node.uploader_user_id, node.logical_path,
       node.can_create_children,
       0::bigint AS readable_document_count
FROM target
CROSS JOIN LATERAL unnest(target.path_ids) WITH ORDINALITY AS breadcrumb(id, ord)
JOIN visible_paths AS node ON node.id = breadcrumb.id
ORDER BY breadcrumb.ord
"""
)

_LIBRARY_BROWSE_SQL = text(
    _VISIBLE_PATHS_CTE
    + """
, readable_descendants AS (
    SELECT node.id AS ancestor_id, node.id, node.document_id
    FROM visible_paths AS node
    UNION ALL
    SELECT descendants.ancestor_id, child.id, child.document_id
    FROM readable_descendants AS descendants
    JOIN visible_paths AS child ON child.parent_id = descendants.id
)
SELECT child.id, child.parent_id, child.kind, child.name, child.name_key,
       child.document_id, child.uploader_user_id, child.logical_path,
       child.can_create_children,
       count(DISTINCT descendants.document_id) FILTER (
           WHERE descendants.document_id IS NOT NULL
       )::bigint AS readable_document_count,
       count(*) OVER () AS total
FROM visible_paths AS child
LEFT JOIN readable_descendants AS descendants
  ON descendants.ancestor_id = child.id
WHERE child.parent_id IS NOT DISTINCT FROM :parent_id
GROUP BY child.id, child.parent_id, child.kind, child.name, child.name_key,
         child.document_id, child.uploader_user_id, child.logical_path,
         child.can_create_children
ORDER BY CASE WHEN child.kind = 'folder' THEN 0 ELSE 1 END,
         child.name_key COLLATE "C", child.id
LIMIT :limit OFFSET :offset
"""
)
_LIBRARY_BROWSE_COUNT_SQL = text(
    "SELECT count(*) FROM ("
    + str(_LIBRARY_BROWSE_SQL).split("ORDER BY CASE WHEN child.kind", maxsplit=1)[0]
    + ") AS visible_children"
)

_LIBRARY_LOCATIONS_SQL = text(
    _VISIBLE_PATHS_CTE
    + """
SELECT id AS node_id, parent_id, name, logical_path, document_id,
       uploader_user_id
FROM visible_paths
WHERE document_id = ANY(:document_ids)
  AND v4_can_read_document(document_id)
ORDER BY document_id
"""
)

_LIBRARY_TREE_SQL = text(
    _VISIBLE_PATHS_CTE
    + """
SELECT id, parent_id, name, logical_path
FROM visible_paths
WHERE kind = 'folder'
ORDER BY cardinality(path_ids), name_key COLLATE "C", id
"""
)


class LibraryError(RuntimeError):
    pass


class LibraryNotFound(LibraryError):
    pass


class LibraryConflict(LibraryError):
    pass


class LibraryNotFolder(LibraryConflict):
    pass


class LibraryCycle(LibraryConflict):
    pass


class LibraryNotEmpty(LibraryConflict):
    pass


class LibraryCorruption(LibraryError):
    pass


class InvalidLibraryName(ValueError):
    pass


def _require_admin(actor: ActorContext) -> None:
    if actor.role is not ActorRole.ADMIN:
        raise LibraryNotFound("library node not found")


def _view_from_mapping(row: Mapping[str, object]) -> LibraryNodeView:
    return LibraryNodeView(
        node_id=row["id"],
        parent_id=row["parent_id"],
        kind=row["kind"],
        name=row["name"],
        name_key=row["name_key"],
        logical_path=row["logical_path"],
        document_id=row["document_id"],
        uploader_user_id=row["uploader_user_id"],
        readable_document_count=int(row["readable_document_count"]),
        can_create_children=bool(row["can_create_children"]),
    )


def normalize_library_name(value: str) -> tuple[str, str]:
    name = unicodedata.normalize("NFC", value.strip())
    if not name or name in {".", ".."}:
        raise InvalidLibraryName("name must not be empty, '.' or '..'")
    if "/" in name or "\\" in name:
        raise InvalidLibraryName("name must not contain path separators")
    if len(name) > 255:
        raise InvalidLibraryName("name exceeds 255 characters")
    if any(unicodedata.category(character) in {"Cc", "Cf", "Cs"} for character in name):
        raise InvalidLibraryName(
            "name contains a control, format, or surrogate character"
        )
    name_key = unicodedata.normalize("NFC", name.casefold())
    if len(name_key.encode("utf-8")) > 1024:
        raise InvalidLibraryName("case-folded name exceeds 1024 UTF-8 bytes")
    return name, name_key


async def acquire_library_lock(session: AsyncSession) -> None:
    await session.execute(
        text("SELECT pg_advisory_xact_lock(:lock_id)"),
        {"lock_id": LIBRARY_ADVISORY_LOCK_ID},
    )


@dataclass(frozen=True, slots=True)
class LibraryLocation:
    node_id: uuid.UUID
    parent_id: uuid.UUID | None
    display_name: str
    logical_path: str
    uploader_user_id: uuid.UUID


@dataclass(frozen=True, slots=True)
class LibraryNodeView:
    node_id: uuid.UUID
    parent_id: uuid.UUID | None
    kind: str
    name: str
    name_key: str
    logical_path: str
    document_id: uuid.UUID | None
    uploader_user_id: uuid.UUID | None
    readable_document_count: int = 0
    can_create_children: bool = False

    def can_manage(self, actor: ActorContext) -> bool:
        return actor.role is ActorRole.ADMIN or (
            self.kind == "file" and self.uploader_user_id == actor.user_id
        )


@dataclass(frozen=True, slots=True)
class LibraryBrowse:
    parent_id: uuid.UUID | None
    breadcrumbs: tuple[LibraryNodeView, ...]
    children: tuple[LibraryNodeView, ...]
    page: int = 1
    limit: int = 100
    total: int = 0


@dataclass(frozen=True, slots=True)
class LibraryTreeNode:
    node_id: uuid.UUID
    parent_id: uuid.UUID | None
    name: str
    logical_path: str
    children: tuple[LibraryTreeNode, ...]


@dataclass(frozen=True, slots=True)
class LibraryMovePreview:
    preview_id: uuid.UUID
    impact_digest: str
    impact: AdminAclImpact


class LibraryService:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def browse(
        self,
        actor: ActorContext,
        session: AsyncSession,
        parent_id: uuid.UUID | None,
        *,
        page: int = 1,
        limit: int = 100,
    ) -> LibraryBrowse:
        if page < 1:
            raise ValueError("page must be positive")
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        if parent_id is not None:
            parent_row = (
                (
                    await session.execute(
                        _LIBRARY_PARENT_SQL,
                        {"actor_id": actor.user_id, "parent_id": parent_id},
                    )
                )
                .mappings()
                .one_or_none()
            )
            if parent_row is None:
                raise LibraryNotFound("library folder not found")
            if parent_row["kind"] != "folder":
                raise LibraryNotFolder("parent node is not a folder")
            breadcrumb_rows = (
                await session.execute(
                    _LIBRARY_BREADCRUMBS_SQL,
                    {"actor_id": actor.user_id, "parent_id": parent_id},
                )
            ).mappings()
            breadcrumbs = tuple(_view_from_mapping(row) for row in breadcrumb_rows)
        else:
            breadcrumbs = ()
        rows = list(
            (
                await session.execute(
                    _LIBRARY_BROWSE_SQL,
                    {
                        "actor_id": actor.user_id,
                        "parent_id": parent_id,
                        "limit": limit,
                        "offset": (page - 1) * limit,
                    },
                )
            ).mappings()
        )
        total = (
            int(rows[0]["total"])
            if rows
            else int(
                await session.scalar(
                    _LIBRARY_BROWSE_COUNT_SQL,
                    {"actor_id": actor.user_id, "parent_id": parent_id},
                )
                or 0
            )
        )
        return LibraryBrowse(
            parent_id,
            breadcrumbs,
            tuple(_view_from_mapping(row) for row in rows),
            page,
            limit,
            total,
        )

    async def tree(
        self, actor: ActorContext, session: AsyncSession
    ) -> tuple[LibraryTreeNode, ...]:
        rows = list(
            (
                await session.execute(_LIBRARY_TREE_SQL, {"actor_id": actor.user_id})
            ).mappings()
        )
        if len(rows) > MAX_LIBRARY_NODES:
            raise LibraryCorruption("library exceeds the bounded node limit")
        folders_by_parent: dict[uuid.UUID | None, list[Mapping[str, object]]] = {}
        for row in rows:
            folders_by_parent.setdefault(row["parent_id"], []).append(row)

        def build(parent_id: uuid.UUID | None) -> tuple[LibraryTreeNode, ...]:
            return tuple(
                LibraryTreeNode(
                    node["id"],
                    node["parent_id"],
                    node["name"],
                    node["logical_path"],
                    build(node["id"]),
                )
                for node in folders_by_parent.get(parent_id, ())
            )

        return build(None)

    async def create_folder(
        self,
        actor: ActorContext,
        session: AsyncSession,
        name: str,
        parent_id: uuid.UUID | None,
    ) -> LibraryNodeView:
        display, name_key = normalize_library_name(name)
        node_id = uuid.uuid4()
        try:
            async with session.begin_nested():
                created = await session.scalar(
                    text(
                        "SELECT v4_create_folder("
                        ":node_id, :parent_id, :name, :name_key)"
                    ),
                    {
                        "node_id": node_id,
                        "parent_id": parent_id,
                        "name": display,
                        "name_key": name_key,
                    },
                )
                if created != node_id:
                    raise RuntimeError("folder creation returned an invalid identifier")
                row = (
                    (
                        await session.execute(
                            _LIBRARY_PARENT_SQL,
                            {"actor_id": actor.user_id, "parent_id": node_id},
                        )
                    )
                    .mappings()
                    .one()
                )
                result = LibraryNodeView(
                    row["id"],
                    row["parent_id"],
                    row["kind"],
                    row["name"],
                    row["name_key"],
                    row["logical_path"],
                    row["document_id"],
                    row["uploader_user_id"],
                    can_create_children=bool(row["can_create_children"]),
                )
        except IntegrityError as exc:
            _raise_name_conflict(exc)
        except DBAPIError as exc:
            if getattr(exc.orig, "sqlstate", None) == "42501":
                raise LibraryNotFound("library node not found") from exc
            raise
        return result

    async def preview_move(
        self,
        actor: ActorContext,
        session: AsyncSession,
        node_id: uuid.UUID,
        parent_id: uuid.UUID | None,
    ) -> LibraryMovePreview:
        operation = {
            "kind": "move_node",
            "node_id": str(node_id),
            "parent_id": str(parent_id) if parent_id is not None else None,
        }
        try:
            row = (
                await session.execute(
                    text(
                        "SELECT preview_id, impact_digest, "
                        "impact "
                        "FROM v4_admin_preview_acl(CAST(:operation AS jsonb))"
                    ),
                    {
                        "operation": json.dumps(
                            operation, ensure_ascii=False, separators=(",", ":")
                        )
                    },
                )
            ).one()
        except DBAPIError as exc:
            if getattr(exc.orig, "sqlstate", None) == "42501":
                raise LibraryNotFound("library node not found") from exc
            raise
        return LibraryMovePreview(
            row.preview_id,
            row.impact_digest,
            AdminAclImpact.model_validate(row.impact),
        )

    async def update_node(
        self,
        actor: ActorContext,
        session: AsyncSession,
        node_id: uuid.UUID,
        *,
        name: str | None,
        parent_id: uuid.UUID | None,
        update_name: bool,
        update_parent: bool,
        preview_id: uuid.UUID | None = None,
        impact_digest: str | None = None,
    ) -> LibraryNodeView:
        if not update_name and not update_parent:
            raise InvalidLibraryName("at least one of name or parent_id is required")
        if update_name and update_parent:
            raise InvalidLibraryName("rename and move must be separate operations")
        if update_parent:
            if preview_id is None or impact_digest is None:
                raise InvalidLibraryName("move requires preview_id and impact_digest")
            try:
                async with session.begin_nested():
                    await session.scalar(
                        text("SELECT v4_admin_apply_acl(:preview_id, :impact_digest)"),
                        {
                            "preview_id": preview_id,
                            "impact_digest": impact_digest,
                        },
                    )
                    row = (
                        (
                            await session.execute(
                                _LIBRARY_PARENT_SQL,
                                {"actor_id": actor.user_id, "parent_id": node_id},
                            )
                        )
                        .mappings()
                        .one_or_none()
                    )
                    if row is None:
                        raise LibraryNotFound("library node not found")
                    return LibraryNodeView(
                        row["id"],
                        row["parent_id"],
                        row["kind"],
                        row["name"],
                        row["name_key"],
                        row["logical_path"],
                        row["document_id"],
                        row["uploader_user_id"],
                        can_create_children=bool(row["can_create_children"]),
                    )
            except IntegrityError as exc:
                _raise_name_conflict(exc)
            except DBAPIError as exc:
                message = str(exc.orig)
                sqlstate = getattr(exc.orig, "sqlstate", None)
                if sqlstate == "40001":
                    raise LibraryConflict("ACL preview is stale or invalid") from exc
                if sqlstate == "42501":
                    raise LibraryNotFound("library node not found") from exc
                if sqlstate == "22023" and (
                    "move node does not exist" in message
                    or "move target is not a folder" in message
                ):
                    raise LibraryNotFound("library node not found") from exc
                if sqlstate == "22023" and (
                    "move target cannot be the moved node" in message
                    or "move target creates a cycle" in message
                    or "move exceeds maximum library depth" in message
                ):
                    raise LibraryConflict(message) from exc
                raise
            raise RuntimeError("library move did not return a node")
        try:
            async with session.begin_nested():
                display, key = normalize_library_name(name or "")
                changed = await session.scalar(
                    text(
                        "SELECT v4_admin_rename_library_node("
                        ":node_id, :name, :name_key)"
                    ),
                    {"node_id": node_id, "name": display, "name_key": key},
                )
                if not changed:
                    raise LibraryNotFound("library node not found")
                row = (
                    (
                        await session.execute(
                            _LIBRARY_PARENT_SQL,
                            {"actor_id": actor.user_id, "parent_id": node_id},
                        )
                    )
                    .mappings()
                    .one()
                )
                result = LibraryNodeView(
                    row["id"],
                    row["parent_id"],
                    row["kind"],
                    row["name"],
                    row["name_key"],
                    row["logical_path"],
                    row["document_id"],
                    row["uploader_user_id"],
                    can_create_children=bool(row["can_create_children"]),
                )
        except IntegrityError as exc:
            _raise_name_conflict(exc)
        return result

    async def delete_folder(
        self, actor: ActorContext, session: AsyncSession, folder_id: uuid.UUID
    ) -> None:
        _require_admin(actor)
        outcome = await session.scalar(
            text("SELECT v4_admin_delete_folder(:node_id)"),
            {"node_id": folder_id},
        )
        if outcome == "deleted":
            return
        if outcome == "not_found":
            raise LibraryNotFound("library folder not found")
        if outcome == "not_folder":
            raise LibraryNotFolder("library node is not a folder")
        if outcome == "not_empty":
            raise LibraryNotEmpty("folder is not empty")
        raise RuntimeError("folder deletion returned an invalid outcome")

    async def locations_for_documents(
        self,
        actor: ActorContext,
        session: AsyncSession,
        document_ids: list[uuid.UUID],
    ) -> dict[uuid.UUID, LibraryLocation]:
        if not document_ids:
            return {}
        return await locations_for_documents_in_session(session, actor, document_ids)

    async def _view(
        self, actor: ActorContext, session: AsyncSession, node_id: uuid.UUID
    ) -> LibraryNodeView:
        nodes = await _load_nodes(session)
        return _view_from_nodes(nodes, node_id)


async def locations_for_documents_in_session(
    session: AsyncSession,
    actor: ActorContext,
    document_ids: list[uuid.UUID],
) -> dict[uuid.UUID, LibraryLocation]:
    if not document_ids:
        return {}
    rows = (
        await session.execute(
            _LIBRARY_LOCATIONS_SQL,
            {"actor_id": actor.user_id, "document_ids": document_ids},
        )
    ).mappings()
    locations = {
        row["document_id"]: LibraryLocation(
            row["node_id"],
            row["parent_id"],
            row["name"],
            row["logical_path"],
            row["uploader_user_id"],
        )
        for row in rows
    }
    missing = set(document_ids) - locations.keys()
    if missing:
        raise LibraryCorruption(
            "documents have no canonical library node: "
            + ", ".join(sorted(str(document_id) for document_id in missing))
        )
    return locations


async def create_file_node(
    session: AsyncSession,
    *,
    document_id: uuid.UUID,
    filename: str,
    parent_id: uuid.UUID | None,
    uploader_user_id: uuid.UUID,
) -> LibraryNode:
    _validate_insertion(await _load_nodes(session), parent_id)
    display, _ = normalize_library_name(filename)
    candidate = display
    suffix = 1
    while True:
        candidate_display, candidate_key = normalize_library_name(candidate)
        conflict = await session.scalar(
            select(LibraryNode.id).where(
                LibraryNode.parent_id.is_not_distinct_from(parent_id),
                LibraryNode.name_key == candidate_key,
            )
        )
        if conflict is None:
            break
        suffix += 1
        candidate = _suffixed_name(display, suffix)
    node = LibraryNode(
        id=uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"urn:local-rag:library-node:v1:{document_id}",
        ),
        parent_id=parent_id,
        kind="file",
        name=candidate_display,
        name_key=candidate_key,
        document_id=document_id,
        uploader_user_id=uploader_user_id,
    )
    session.add(node)
    return node


async def validate_new_file_target(
    session: AsyncSession,
    *,
    filename: str,
    parent_id: uuid.UUID | None,
) -> None:
    normalize_library_name(filename)
    _validate_insertion(await _load_nodes(session), parent_id)


async def location_for_document(
    session: AsyncSession, document_id: uuid.UUID
) -> LibraryLocation:
    nodes = await _load_nodes(session)
    views, _ = _resolve_views(nodes)
    for view in views.values():
        if view.document_id == document_id:
            return LibraryLocation(
                view.node_id,
                view.parent_id,
                view.name,
                view.logical_path,
                view.uploader_user_id,
            )
    raise LibraryCorruption(f"document {document_id} has no canonical file node")


def _suffixed_name(name: str, ordinal: int) -> str:
    dot = name.rfind(".")
    if 0 < dot < len(name) - 1:
        stem, extension = name[:dot], name[dot:]
    else:
        stem, extension = name, ""
    marker = f" ({ordinal})"
    while stem and (
        len(f"{stem}{marker}{extension}") > 255
        or len(
            unicodedata.normalize(
                "NFC", f"{stem}{marker}{extension}".casefold()
            ).encode("utf-8")
        )
        > 1024
    ):
        stem = stem[:-1]
    if not stem:
        raise LibraryConflict("filename namespace is exhausted")
    return f"{stem}{marker}{extension}"


async def _ensure_available(
    session: AsyncSession,
    parent_id: uuid.UUID | None,
    name_key: str,
    *,
    excluding: uuid.UUID | None = None,
) -> None:
    statement = select(LibraryNode.id).where(
        LibraryNode.parent_id.is_not_distinct_from(parent_id),
        LibraryNode.name_key == name_key,
    )
    if excluding is not None:
        statement = statement.where(LibraryNode.id != excluding)
    if await session.scalar(statement) is not None:
        raise LibraryConflict("a sibling with that name already exists")


def _resolve_views(
    nodes: list[LibraryNode],
) -> tuple[dict[uuid.UUID, LibraryNodeView], dict[uuid.UUID, tuple[uuid.UUID, ...]]]:
    if len(nodes) > MAX_LIBRARY_NODES:
        raise LibraryCorruption("library exceeds the bounded node limit")
    records = {node.id: node for node in nodes}
    views: dict[uuid.UUID, LibraryNodeView] = {}
    chains: dict[uuid.UUID, tuple[uuid.UUID, ...]] = {}

    def resolve(
        node_id: uuid.UUID, stack: tuple[uuid.UUID, ...] = ()
    ) -> LibraryNodeView:
        if node_id in views:
            return views[node_id]
        if node_id in stack or len(stack) >= MAX_LIBRARY_DEPTH:
            raise LibraryCorruption("library contains a cycle or excessive depth")
        node = records[node_id]
        if node.parent_id is None:
            ancestors: tuple[uuid.UUID, ...] = ()
            names: tuple[str, ...] = ()
        else:
            parent = records.get(node.parent_id)
            if parent is None or parent.kind != "folder":
                raise LibraryCorruption("library node has an invalid parent")
            parent_view = resolve(parent.id, (*stack, node_id))
            ancestors = (*chains[parent.id], parent.id)
            names = tuple(part for part in parent_view.logical_path.split("/") if part)
        logical_path = "/" + "/".join((*names, node.name))
        view = LibraryNodeView(
            node.id,
            node.parent_id,
            node.kind,
            node.name,
            node.name_key,
            logical_path,
            node.document_id,
            node.uploader_user_id,
        )
        views[node_id] = view
        chains[node_id] = ancestors
        return view

    for node_id in records:
        resolve(node_id)
    return views, chains


def _node_order(view: LibraryNodeView) -> tuple[bool, str, str, str]:
    return (view.kind != "folder", view.name_key, view.name, str(view.node_id))


async def _load_nodes(session: AsyncSession) -> list[LibraryNode]:
    nodes = list(
        await session.scalars(select(LibraryNode).limit(MAX_LIBRARY_NODES + 1))
    )
    if len(nodes) > MAX_LIBRARY_NODES:
        raise LibraryCorruption("library exceeds the bounded node limit")
    return nodes


def _view_from_nodes(nodes: list[LibraryNode], node_id: uuid.UUID) -> LibraryNodeView:
    views, _ = _resolve_views(nodes)
    try:
        return views[node_id]
    except KeyError as exc:
        raise LibraryNotFound("library node not found") from exc


def _validate_insertion(nodes: list[LibraryNode], parent_id: uuid.UUID | None) -> None:
    if len(nodes) >= MAX_LIBRARY_NODES:
        raise LibraryConflict("library node capacity is exhausted")
    views, chains = _resolve_views(nodes)
    parent_depth = _validated_parent_depth(views, chains, parent_id)
    if parent_depth + 1 > MAX_LIBRARY_DEPTH:
        raise LibraryConflict("library depth limit would be exceeded")


def _validate_move(
    nodes: list[LibraryNode],
    node_id: uuid.UUID,
    parent_id: uuid.UUID | None,
) -> None:
    views, chains = _resolve_views(nodes)
    if node_id not in views:
        raise LibraryNotFound("library node not found")
    parent_depth = _validated_parent_depth(views, chains, parent_id)
    if parent_id == node_id or (parent_id is not None and node_id in chains[parent_id]):
        raise LibraryCycle("folder move would create a cycle")
    subtree_height = max(
        (
            len(chains[candidate.node_id]) - len(chains[node_id]) + 1
            for candidate in views.values()
            if candidate.node_id == node_id or node_id in chains[candidate.node_id]
        ),
        default=1,
    )
    if parent_depth + subtree_height > MAX_LIBRARY_DEPTH:
        raise LibraryConflict("library depth limit would be exceeded")


def _validated_parent_depth(
    views: dict[uuid.UUID, LibraryNodeView],
    chains: dict[uuid.UUID, tuple[uuid.UUID, ...]],
    parent_id: uuid.UUID | None,
) -> int:
    if parent_id is None:
        return 0
    parent = views.get(parent_id)
    if parent is None:
        raise LibraryNotFound("parent folder not found")
    if parent.kind != "folder":
        raise LibraryNotFolder("parent node is not a folder")
    return len(chains[parent_id]) + 1


def _raise_name_conflict(exc: IntegrityError) -> None:
    if getattr(exc.orig, "constraint_name", None) == "uq_library_nodes_parent_name_key":
        raise LibraryConflict("a sibling with that name already exists") from exc
    raise exc
