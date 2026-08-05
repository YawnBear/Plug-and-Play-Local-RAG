import asyncio
import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy.exc import IntegrityError

from app.db.models import LibraryNode
from app.security.actor import ActorContext, ActorRole
from app.services.library import (
    InvalidLibraryName,
    LibraryConflict,
    LibraryCorruption,
    LibraryCycle,
    LibraryNotEmpty,
    LibraryService,
    _raise_name_conflict,
    _resolve_views,
    _suffixed_name,
    _validate_insertion,
    _validate_move,
    create_file_node,
    normalize_library_name,
)


def _actor() -> ActorContext:
    return ActorContext(uuid.uuid4(), ActorRole.ADMIN, 1, 1, uuid.uuid4())


def _node(
    name: str,
    *,
    node_id: uuid.UUID,
    parent_id: uuid.UUID | None = None,
    kind: str = "folder",
    document_id: uuid.UUID | None = None,
) -> LibraryNode:
    display, name_key = normalize_library_name(name)
    return LibraryNode(
        id=node_id,
        parent_id=parent_id,
        kind=kind,
        name=display,
        name_key=name_key,
        document_id=document_id,
        uploader_user_id=uuid.uuid4() if kind == "file" else None,
    )


@pytest.mark.parametrize(
    "value",
    ["", "  ", ".", "..", "a/b", "a\\b", "bad\nname", "bad\u200bname", "\ud800"],
)
def test_name_validation_rejects_unsafe_values(value: str) -> None:
    with pytest.raises(InvalidLibraryName):
        normalize_library_name(value)


def test_name_normalization_is_trimmed_nfc_and_casefolded_nfc() -> None:
    display, name_key = normalize_library_name("  Re\u0301sume\u0301.PDF  ")
    assert display == "Résumé.PDF"
    assert name_key == "résumé.pdf"

    _, street_key = normalize_library_name("Straße")
    _, uppercase_key = normalize_library_name("STRASSE")
    assert street_key == uppercase_key == "strasse"


def test_name_validation_enforces_codepoint_and_casefold_byte_bounds() -> None:
    assert normalize_library_name("a" * 255)[0] == "a" * 255
    with pytest.raises(InvalidLibraryName, match="255"):
        normalize_library_name("a" * 256)
    with pytest.raises(InvalidLibraryName, match="1024"):
        normalize_library_name("\u1f80" * 255)


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("report.pdf", "report (2).pdf"),
        ("archive.tar.gz", "archive.tar (2).gz"),
        (".profile", ".profile (2)"),
        ("report.", "report. (2)"),
    ],
)
def test_suffix_uses_only_a_non_edge_final_dot(name: str, expected: str) -> None:
    assert _suffixed_name(name, 2) == expected


def test_suffix_truncates_only_the_stem() -> None:
    candidate = _suffixed_name(f"{'a' * 254}.x", 2)
    assert candidate.endswith(" (2).x")
    assert len(candidate) == 255


def test_suffix_reports_namespace_exhaustion_without_altering_extension() -> None:
    with pytest.raises(LibraryConflict, match="exhausted"):
        _suffixed_name(f"a.{'x' * 253}", 2)


class _FileNodeSession:
    def __init__(self) -> None:
        self.conflicts = [uuid.uuid4(), None]
        self.added: LibraryNode | None = None

    async def scalar(self, statement: object) -> object | None:
        return self.conflicts.pop(0)

    async def scalars(self, statement: object) -> list[LibraryNode]:
        return []

    def add(self, node: LibraryNode) -> None:
        self.added = node


def test_file_node_uses_first_suffix_and_deterministic_document_uuid() -> None:
    session = _FileNodeSession()
    document_id = uuid.uuid4()

    node = asyncio.run(
        create_file_node(
            session,
            document_id=document_id,
            filename="report.pdf",
            parent_id=None,
            uploader_user_id=uuid.uuid4(),
        )
    )

    assert node is session.added
    assert node.name == "report (2).pdf"
    assert node.name_key == "report (2).pdf"
    assert node.id == uuid.uuid5(
        uuid.NAMESPACE_URL,
        f"urn:local-rag:library-node:v1:{document_id}",
    )


class _ReadSession:
    def __init__(self, nodes: list[LibraryNode]) -> None:
        self.nodes = nodes

    async def __aenter__(self) -> "_ReadSession":
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def scalars(self, statement: object) -> list[LibraryNode]:
        return self.nodes


class _ReadFactory:
    def __init__(self, nodes: list[LibraryNode]) -> None:
        self.nodes = nodes

    def __call__(self) -> _ReadSession:
        return _ReadSession(self.nodes)


def test_nested_browse_paths_breadcrumbs_and_binary_order() -> None:
    root_a = uuid.UUID(int=1)
    root_b = uuid.UUID(int=2)
    child = uuid.UUID(int=3)
    file_id = uuid.UUID(int=4)
    document_id = uuid.UUID(int=5)
    nodes = [
        _node("zeta", node_id=root_b),
        _node("Alpha", node_id=root_a),
        _node("Nested", node_id=child, parent_id=root_a),
        _node(
            "aardvark.pdf",
            node_id=file_id,
            parent_id=root_a,
            kind="file",
            document_id=document_id,
        ),
    ]
    views, chains = _resolve_views(nodes)
    root = sorted(
        (item for item in views.values() if item.parent_id is None),
        key=lambda item: (item.kind != "folder", item.name_key, item.node_id),
    )
    nested = sorted(
        (item for item in views.values() if item.parent_id == root_a),
        key=lambda item: (item.kind != "folder", item.name_key, item.node_id),
    )

    assert [item.name for item in root] == ["Alpha", "zeta"]
    assert [item.name for item in nested] == ["Nested", "aardvark.pdf"]
    assert [views[item].name for item in chains[child]] == ["Alpha"]
    assert nested[0].logical_path == "/Alpha/Nested"
    assert nested[1].logical_path == "/Alpha/aardvark.pdf"


def test_tree_is_nested_and_excludes_files() -> None:
    root = uuid.UUID(int=10)
    child = uuid.UUID(int=11)
    nodes = [
        _node("Root", node_id=root),
        _node("Child", node_id=child, parent_id=root),
        _node(
            "file.pdf",
            node_id=uuid.UUID(int=12),
            parent_id=child,
            kind="file",
            document_id=uuid.UUID(int=13),
        ),
    ]

    views, _ = _resolve_views(nodes)
    folders = [item for item in views.values() if item.kind == "folder"]

    assert [item.name for item in folders if item.parent_id is None] == ["Root"]
    assert [item.name for item in folders if item.parent_id == root] == ["Child"]
    assert not [item for item in folders if item.parent_id == child]


def test_view_resolution_fails_explicitly_for_orphan_and_cycle() -> None:
    orphan = _node("orphan", node_id=uuid.UUID(int=20), parent_id=uuid.UUID(int=21))
    with pytest.raises(LibraryCorruption, match="invalid parent"):
        _resolve_views([orphan])

    first = _node("first", node_id=uuid.UUID(int=22), parent_id=uuid.UUID(int=23))
    second = _node("second", node_id=uuid.UUID(int=23), parent_id=uuid.UUID(int=22))
    with pytest.raises(LibraryCorruption, match="cycle"):
        _resolve_views([first, second])


def _chain(length: int, *, id_offset: int = 0) -> list[LibraryNode]:
    nodes: list[LibraryNode] = []
    parent_id: uuid.UUID | None = None
    for ordinal in range(1, length + 1):
        node_id = uuid.UUID(int=id_offset + ordinal)
        nodes.append(
            _node(f"node-{id_offset + ordinal}", node_id=node_id, parent_id=parent_id)
        )
        parent_id = node_id
    return nodes


def test_create_boundary_allows_depth_256_and_rejects_depth_257() -> None:
    depth_255 = _chain(255)
    _validate_insertion(depth_255, depth_255[-1].id)

    depth_256 = _chain(256)
    with pytest.raises(LibraryConflict, match="depth"):
        _validate_insertion(depth_256, depth_256[-1].id)


def test_create_rejects_node_10001_before_it_can_become_unreadable() -> None:
    nodes = [
        _node(f"node-{ordinal}", node_id=uuid.UUID(int=ordinal + 1))
        for ordinal in range(10_000)
    ]
    with pytest.raises(LibraryConflict, match="capacity"):
        _validate_insertion(nodes, None)


def test_move_accounts_for_complete_subtree_height_and_cycles() -> None:
    target_chain = _chain(254)
    moving = _chain(3, id_offset=1000)
    nodes = [*target_chain, *moving]

    _validate_move(nodes, moving[0].id, target_chain[-2].id)
    with pytest.raises(LibraryConflict, match="depth"):
        _validate_move(nodes, moving[0].id, target_chain[-1].id)
    with pytest.raises(LibraryCycle):
        _validate_move(nodes, moving[0].id, moving[-1].id)


def test_only_named_sibling_integrity_violation_becomes_conflict() -> None:
    sibling = IntegrityError(
        "statement",
        {},
        SimpleNamespace(constraint_name="uq_library_nodes_parent_name_key"),
    )
    with pytest.raises(LibraryConflict):
        _raise_name_conflict(sibling)

    unrelated = IntegrityError(
        "statement", {}, SimpleNamespace(constraint_name="other")
    )
    with pytest.raises(IntegrityError):
        _raise_name_conflict(unrelated)


class _MutationStore:
    def __init__(self, nodes: list[LibraryNode] | None = None) -> None:
        self.nodes = list(nodes or [])
        self.pending_move: tuple[uuid.UUID, uuid.UUID | None] | None = None
        self.applied: tuple[uuid.UUID, str] | None = None


class _MutationSession:
    def __init__(self, store: _MutationStore) -> None:
        self.store = store

    async def __aenter__(self) -> "_MutationSession":
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    def begin(self) -> "_MutationSession":
        return self

    def begin_nested(self) -> "_MutationSession":
        return self

    async def execute(self, statement: object, parameters: object = None) -> object:
        if "WITH RECURSIVE visible_paths" not in str(statement):
            return None
        target = parameters["parent_id"]
        views, _ = _resolve_views(self.store.nodes)
        view = views[target]
        row = {
            "id": view.node_id,
            "parent_id": view.parent_id,
            "kind": view.kind,
            "name": view.name,
            "name_key": view.name_key,
            "logical_path": view.logical_path,
            "document_id": view.document_id,
            "uploader_user_id": view.uploader_user_id,
            "can_create_children": True,
        }
        return _OneMapping(row)

    async def scalars(self, statement: object) -> list[LibraryNode]:
        return list(self.store.nodes)

    async def scalar(
        self, statement: object, parameters: dict[str, object] | None = None
    ) -> object | None:
        sql = str(statement)
        parameters = parameters or statement.compile().params
        if "v4_admin_apply_acl" in sql:
            self.store.applied = (
                parameters["preview_id"],
                str(parameters["impact_digest"]),
            )
            if self.store.pending_move is not None:
                node_id, parent_id = self.store.pending_move
                next(
                    node for node in self.store.nodes if node.id == node_id
                ).parent_id = parent_id
            return 2
        if "v4_create_folder" in sql:
            if any(
                node.parent_id == parameters["parent_id"]
                and node.name_key == parameters["name_key"]
                for node in self.store.nodes
            ):
                raise IntegrityError(
                    "statement",
                    parameters,
                    SimpleNamespace(constraint_name="uq_library_nodes_parent_name_key"),
                )
            node = _node(
                str(parameters["name"]),
                node_id=parameters["node_id"],
                parent_id=parameters["parent_id"],
            )
            node.name_key = str(parameters["name_key"])
            self.store.nodes.append(node)
            return node.id
        if "v4_admin_rename_library_node" in sql:
            node = next(
                (item for item in self.store.nodes if item.id == parameters["node_id"]),
                None,
            )
            if node is None:
                return False
            node.name = str(parameters["name"])
            node.name_key = str(parameters["name_key"])
            return True
        if "v4_admin_delete_folder" in sql:
            node = next(
                (item for item in self.store.nodes if item.id == parameters["node_id"]),
                None,
            )
            if node is None:
                return "not_found"
            if node.kind != "folder":
                return "not_folder"
            if any(item.parent_id == node.id for item in self.store.nodes):
                return "not_empty"
            self.store.nodes.remove(node)
            return "deleted"
        if "count(*)" in sql:
            parent_id = next(
                value for value in parameters.values() if isinstance(value, uuid.UUID)
            )
            return sum(node.parent_id == parent_id for node in self.store.nodes)
        if "name_key" in sql.split("WHERE", 1)[-1]:
            name_key = next(
                value for value in parameters.values() if isinstance(value, str)
            )
            parent_id = next(
                (value for key, value in parameters.items() if "parent_id" in key),
                None,
            )
            excluding = next(
                (value for key, value in parameters.items() if key.startswith("id_")),
                None,
            )
            return next(
                (
                    node.id
                    for node in self.store.nodes
                    if node.parent_id == parent_id
                    and node.name_key == name_key
                    and node.id != excluding
                ),
                None,
            )
        node_id = next(
            value for value in parameters.values() if isinstance(value, uuid.UUID)
        )
        return next((node for node in self.store.nodes if node.id == node_id), None)

    def add(self, node: LibraryNode) -> None:
        self.store.nodes.append(node)

    async def flush(self) -> None:
        return None

    async def delete(self, node: LibraryNode) -> None:
        self.store.nodes.remove(node)


class _MutationFactory:
    def __init__(self, nodes: list[LibraryNode] | None = None) -> None:
        self.store = _MutationStore(nodes)
        self.session_count = 0

    def __call__(self) -> _MutationSession:
        self.session_count += 1
        return _MutationSession(self.store)


class _OneMapping:
    def __init__(self, row: dict[str, object]) -> None:
        self.row = row

    def mappings(self) -> "_OneMapping":
        return self

    def one(self) -> dict[str, object]:
        return self.row

    def one_or_none(self) -> dict[str, object] | None:
        return self.row


def test_folder_create_update_move_and_delete_use_controlled_commands() -> None:
    factory = _MutationFactory()
    service = LibraryService(factory)

    actor = _actor()
    root = asyncio.run(service.create_folder(actor, factory(), "Policies", None))
    child = asyncio.run(service.create_folder(actor, factory(), "2026", root.node_id))
    renamed = asyncio.run(
        service.update_node(
            actor,
            factory(),
            child.node_id,
            name="Current",
            parent_id=None,
            update_name=True,
            update_parent=False,
        )
    )
    assert root.logical_path == "/Policies"
    assert renamed.logical_path == "/Policies/Current"
    preview_id = uuid.uuid4()
    factory.store.pending_move = (renamed.node_id, None)
    moved = asyncio.run(
        service.update_node(
            actor,
            factory(),
            renamed.node_id,
            name=None,
            parent_id=None,
            update_name=False,
            update_parent=True,
            preview_id=preview_id,
            impact_digest="a" * 64,
        )
    )
    assert moved.parent_id is None
    assert moved.logical_path == "/Current"
    assert factory.store.applied == (preview_id, "a" * 64)
    asyncio.run(service.delete_folder(actor, factory(), renamed.node_id))
    assert all(node.id != renamed.node_id for node in factory.store.nodes)


def test_cross_kind_conflict_cycle_and_nonempty_delete_are_rejected() -> None:
    root_id = uuid.uuid4()
    child_id = uuid.uuid4()
    file_id = uuid.uuid4()
    document_id = uuid.uuid4()
    nodes = [
        _node("Root", node_id=root_id),
        _node("Child", node_id=child_id, parent_id=root_id),
        _node(
            "report.pdf",
            node_id=file_id,
            parent_id=root_id,
            kind="file",
            document_id=document_id,
        ),
    ]
    service = LibraryService(_MutationFactory(nodes))

    with pytest.raises(LibraryConflict):
        asyncio.run(
            service.create_folder(
                _actor(), service._session_factory(), "REPORT.PDF", root_id
            )
        )
    with pytest.raises(InvalidLibraryName, match="preview_id"):
        asyncio.run(
            service.update_node(
                _actor(),
                service._session_factory(),
                root_id,
                name=None,
                parent_id=child_id,
                update_name=False,
                update_parent=True,
            )
        )
    with pytest.raises(LibraryNotEmpty):
        asyncio.run(
            service.delete_folder(_actor(), service._session_factory(), root_id)
        )


def test_rename_and_move_touch_only_library_metadata() -> None:
    folder_id = uuid.uuid4()
    document_id = uuid.uuid4()
    file_id = uuid.uuid4()
    file_node = _node(
        "original.pdf",
        node_id=file_id,
        kind="file",
        document_id=document_id,
    )
    service = LibraryService(
        _MutationFactory([_node("Folder", node_id=folder_id), file_node])
    )
    provenance = SimpleNamespace(
        object_key="originals/aa/hash.pdf",
        original_filename="original.pdf",
        chunk_filename="original.pdf",
        source_sha256="a" * 64,
    )
    before = vars(provenance).copy()

    result = asyncio.run(
        service.update_node(
            _actor(),
            service._session_factory(),
            file_id,
            name="renamed.pdf",
            parent_id=folder_id,
            update_name=True,
            update_parent=False,
        )
    )

    assert result.logical_path == "/renamed.pdf"
    assert vars(provenance) == before
