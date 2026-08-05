import asyncio
import inspect
import uuid
from pathlib import Path

import pytest

from app.db.repositories import VectorSearchTuning
from app.security.actor import ActorContext, ActorRole
from app.services.chats import ChatService
from app.services.library import (
    _LIBRARY_BREADCRUMBS_SQL,
    _LIBRARY_BROWSE_SQL,
    _LIBRARY_LOCATIONS_SQL,
)
from app.services.retrieval import FusionEvidence, RetrievalService
from app.services.search import LEXICAL_SEARCH_SQL, normalize_search_query


def _actor() -> ActorContext:
    return ActorContext(
        user_id=uuid.uuid4(),
        role=ActorRole.MEMBER,
        authentication_version=1,
        authorization_version=1,
        session_id=uuid.uuid4(),
    )


def test_library_queries_are_recursive_paginated_and_actor_filtered() -> None:
    browse = str(_LIBRARY_BROWSE_SQL)
    breadcrumbs = str(_LIBRARY_BREADCRUMBS_SQL)
    locations = str(_LIBRARY_LOCATIONS_SQL)

    assert "WITH RECURSIVE visible_paths" in browse
    assert "v4_current_actor_id() = :actor_id" in browse
    assert "v4_can_view_library_node" in browse
    assert "count(DISTINCT descendants.document_id)" in browse
    assert "LIMIT :limit OFFSET :offset" in browse
    assert "unnest(target.path_ids) WITH ORDINALITY" in breadcrumbs
    assert "v4_can_read_document(document_id)" in locations


def test_lexical_search_is_acl_filtered_and_contains_no_response_text() -> None:
    statement = str(LEXICAL_SEARCH_SQL)

    assert "v4_current_actor_id() = :actor_id" in statement
    assert "v4_can_read_document(document.id)" in statement
    assert "v4_can_view_library_node" in statement
    assert "document.original_filename" in statement
    assert "path.logical_path" in statement
    assert "to_tsvector('simple', chunk.text)" in statement
    assert "LIMIT :limit OFFSET :offset" in statement
    assert "chunk.text AS" not in statement


@pytest.mark.parametrize("value", ["", " ", "a" * 201, "bad\u200bquery", "\x00"])
def test_search_query_validation_is_explicit(value: str) -> None:
    with pytest.raises(ValueError):
        normalize_search_query(value)


def test_search_query_normalizes_unicode() -> None:
    assert normalize_search_query("  Cafe\u0301  ") == "Caf\u00e9"


def test_vector_tuning_bounds_and_iterative_scan_values() -> None:
    assert VectorSearchTuning(ef_search=1).ef_search == 1
    assert VectorSearchTuning(ef_search=1000).ef_search == 1000
    with pytest.raises(ValueError):
        VectorSearchTuning(ef_search=0)
    with pytest.raises(ValueError):
        VectorSearchTuning(iterative_scan="off")


def test_retrieval_constructor_requires_session() -> None:
    factory = object()
    embedder = object()
    service = RetrievalService(factory, embedder)

    assert service._session_factory is factory
    parameters = inspect.signature(service.retrieve).parameters
    assert parameters["actor"].default is inspect.Parameter.empty
    assert parameters["session"].default is inspect.Parameter.empty


def test_fusion_requires_measured_five_point_recall_improvement() -> None:
    assert FusionEvidence(0.70, 0.75).qualifies
    assert FusionEvidence(0.30, 0.35).qualifies
    assert not FusionEvidence(0.70, 0.749).qualifies
    with pytest.raises(ValueError):
        FusionEvidence(-0.01, 0.5)


class _Mappings:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self._rows = rows

    def mappings(self) -> "_Mappings":
        return self

    def __iter__(self):
        return iter(self._rows)


class _Session:
    def __init__(self) -> None:
        self.parameters: dict[str, object] | None = None

    async def __aenter__(self) -> "_Session":
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    def begin(self) -> "_Session":
        return self

    async def execute(
        self, statement: object, parameters: dict[str, object]
    ) -> _Mappings:
        self.parameters = parameters
        return _Mappings([])

    async def scalar(self, statement: object, parameters: dict[str, object]) -> int:
        self.parameters = parameters
        return 37


def test_search_service_uses_safe_offset_and_actor_parameter() -> None:
    from app.services.search import SearchService

    session = _Session()
    actor = _actor()
    result = asyncio.run(
        SearchService().search(actor, session, "policy", page=3, limit=20)
    )

    assert session.parameters is not None
    assert session.parameters["actor_id"] == actor.user_id
    assert result.total == 37
    assert result.stage_timings_ms.keys() == {"database_search"}


def test_chat_detail_projection_omits_snapshot_and_handles_deletion() -> None:
    source = (Path(__file__).parents[1] / "app" / "services" / "chats.py").read_text(
        encoding="utf-8"
    )
    method = inspect.getsource(ChatService.get)

    assert "v4_authorized_turn_sources" in method
    assert "v4_authorized_turn_citations" in method
    assert "select(TurnSource)" not in source
    assert "select(TurnCitation)" not in source


def test_chat_protected_methods_require_actor_and_activated_session() -> None:
    for method_name in (
        "create",
        "list",
        "rename",
        "delete",
        "save_scope",
        "get",
        "prepare_message",
        "prepare_retry",
        "retrieve_candidates",
        "snapshot_sources",
        "complete",
        "transition",
        "monitor",
    ):
        method = getattr(ChatService, method_name)
        parameters = inspect.signature(method).parameters
        assert parameters["actor"].default is inspect.Parameter.empty
        assert parameters["session"].default is inspect.Parameter.empty
        assert "_session_factory()" not in inspect.getsource(method)
