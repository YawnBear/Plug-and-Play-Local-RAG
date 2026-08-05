import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import app.routes.library as library_routes
from app.config import Settings
from app.main import create_app
from app.schemas.library import AccountTeamResponse
from app.security.actor import ActorContext, ActorRole
from app.services.library import (
    InvalidLibraryName,
    LibraryBrowse,
    LibraryConflict,
    LibraryCorruption,
    LibraryNodeView,
    LibraryNotFolder,
    LibraryNotFound,
    LibraryTreeNode,
)

_ACTOR = ActorContext(uuid.uuid4(), ActorRole.ADMIN, 1, 1, uuid.uuid4())


@pytest.fixture(autouse=True)
def _authenticated(monkeypatch: pytest.MonkeyPatch) -> None:
    @asynccontextmanager
    async def fake_authenticated_request(request: object, *, mutation: bool = False):
        yield SimpleNamespace(
            actor=_ACTOR,
            session=object(),
            session_token="opaque",
        )

    monkeypatch.setattr(
        library_routes, "authenticated_request", fake_authenticated_request
    )


def _view(
    name: str,
    *,
    node_id: uuid.UUID | None = None,
    parent_id: uuid.UUID | None = None,
    kind: str = "folder",
) -> LibraryNodeView:
    resolved_id = node_id or uuid.uuid4()
    return LibraryNodeView(
        resolved_id,
        parent_id,
        kind,
        name,
        name.casefold(),
        f"/{name}",
        uuid.uuid4() if kind == "file" else None,
        _ACTOR.user_id if kind == "file" else None,
    )


class _Library:
    def __init__(self) -> None:
        self.error: Exception | None = None
        self.root = _view("Policies")
        self.child = _view("2026", parent_id=self.root.node_id)
        self.deleted: uuid.UUID | None = None

    def _raise(self) -> None:
        if self.error is not None:
            raise self.error

    async def browse(
        self,
        actor: ActorContext,
        session: object,
        parent_id: uuid.UUID | None,
        *,
        page: int,
        limit: int,
    ) -> LibraryBrowse:
        self._raise()
        if parent_id is None:
            return LibraryBrowse(None, (), (self.root,), page, limit, 1)
        return LibraryBrowse(parent_id, (self.root,), (self.child,), page, limit, 1)

    async def tree(
        self, actor: ActorContext, session: object
    ) -> tuple[LibraryTreeNode, ...]:
        self._raise()
        return (
            LibraryTreeNode(
                self.root.node_id,
                None,
                self.root.name,
                self.root.logical_path,
                (
                    LibraryTreeNode(
                        self.child.node_id,
                        self.root.node_id,
                        self.child.name,
                        "/Policies/2026",
                        (),
                    ),
                ),
            ),
        )

    async def create_folder(
        self,
        actor: ActorContext,
        session: object,
        name: str,
        parent_id: uuid.UUID | None,
    ) -> LibraryNodeView:
        self._raise()
        return _view(name, parent_id=parent_id)

    async def update_node(
        self,
        actor: ActorContext,
        session: object,
        node_id: uuid.UUID,
        *,
        name: str | None,
        parent_id: uuid.UUID | None,
        update_name: bool,
        update_parent: bool,
        preview_id: uuid.UUID | None,
        impact_digest: str | None,
    ) -> LibraryNodeView:
        self._raise()
        if not update_name and not update_parent:
            raise InvalidLibraryName("at least one field is required")
        if update_name and name is None:
            raise InvalidLibraryName("name cannot be null")
        return _view(name or "Policies", node_id=node_id, parent_id=parent_id)

    async def delete_folder(
        self, actor: ActorContext, session: object, folder_id: uuid.UUID
    ) -> None:
        self._raise()
        self.deleted = folder_id


class _Authorization:
    async def account_teams(
        self, actor: ActorContext, session_token: str
    ) -> list[AccountTeamResponse]:
        assert actor == _ACTOR
        assert session_token == "opaque"
        return [
            AccountTeamResponse(
                id=uuid.UUID(int=20),
                name="Research",
                is_active=True,
            )
        ]


def _client(library: _Library) -> TestClient:
    return TestClient(
        create_app(
            Settings(),
            SimpleNamespace(library=library, authorization=_Authorization()),
        )
    )


def test_account_team_options_are_authoritative() -> None:
    response = _client(_Library()).get("/api/account/teams")

    assert response.status_code == 200
    assert response.json() == {
        "teams": [
            {
                "id": str(uuid.UUID(int=20)),
                "name": "Research",
                "is_active": True,
            }
        ],
        "requires_team_selection": True,
    }


def test_browse_tree_and_folder_crud_contracts() -> None:
    library = _Library()
    client = _client(library)

    root = client.get("/api/library/browse")
    nested = client.get(
        "/api/library/browse", params={"parent_id": str(library.root.node_id)}
    )
    tree = client.get("/api/library/tree")
    created = client.post(
        "/api/library/folders", json={"name": "New", "parent_id": None}
    )
    renamed = client.patch(
        f"/api/library/nodes/{library.root.node_id}", json={"name": "Renamed"}
    )
    moved = client.patch(
        f"/api/library/nodes/{library.child.node_id}",
        json={
            "parent_id": None,
            "preview_id": str(uuid.uuid4()),
            "impact_digest": "a" * 64,
        },
    )
    deleted = client.delete(f"/api/library/folders/{library.child.node_id}")

    assert root.status_code == 200
    assert root.json()["parent_id"] is None
    assert nested.status_code == 200
    assert [item["name"] for item in nested.json()["breadcrumbs"]] == ["Policies"]
    assert tree.status_code == 200
    assert tree.json()[0]["children"][0]["name"] == "2026"
    assert created.status_code == 201
    assert renamed.status_code == 200
    assert renamed.json()["name"] == "Renamed"
    assert moved.status_code == 200
    assert moved.json()["parent_id"] is None
    assert deleted.status_code == 204
    assert library.deleted == library.child.node_id


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (LibraryNotFound("missing"), 404),
        (LibraryNotFolder("file parent"), 409),
        (LibraryConflict("conflict"), 409),
        (InvalidLibraryName("invalid"), 422),
        (LibraryCorruption("corrupt"), 500),
    ],
)
def test_library_errors_have_stable_status_mapping(
    error: Exception, expected: int
) -> None:
    library = _Library()
    library.error = error
    response = _client(library).get("/api/library/browse")
    assert response.status_code == expected


@pytest.mark.parametrize("payload", [{}, {"name": None}])
def test_patch_rejects_empty_or_null_name(payload: dict[str, object]) -> None:
    library = _Library()
    response = _client(library).patch(
        f"/api/library/nodes/{library.root.node_id}", json=payload
    )
    assert response.status_code == 422


def test_patch_rejects_unconfirmed_or_combined_move() -> None:
    library = _Library()
    endpoint = f"/api/library/nodes/{library.child.node_id}"

    assert _client(library).patch(endpoint, json={"parent_id": None}).status_code == 422
    assert (
        _client(library)
        .patch(
            endpoint,
            json={
                "name": "Renamed",
                "parent_id": None,
                "preview_id": str(uuid.uuid4()),
                "impact_digest": "a" * 64,
            },
        )
        .status_code
        == 422
    )


def test_malformed_uuid_is_422() -> None:
    response = _client(_Library()).get(
        "/api/library/browse", params={"parent_id": "not-a-uuid"}
    )
    assert response.status_code == 422
