import asyncio
import sys
from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory

from app.db.models import (
    Chat,
    ChatScope,
    ChatTurn,
    Chunk,
    Document,
    IngestionJob,
    LibraryNode,
    ObjectDeletion,
    TurnCitation,
    TurnSource,
)
from app.db.session import EXPECTED_ALEMBIC_REVISION


@pytest.mark.skipif(sys.platform != "win32", reason="Windows event-loop contract")
def test_windows_application_policy_creates_selector_loops() -> None:
    loop = asyncio.new_event_loop()
    try:
        assert isinstance(loop, asyncio.SelectorEventLoop)
    finally:
        loop.close()


def _constraint_names(table: object) -> set[str | None]:
    return {constraint.name for constraint in table.constraints}


def test_models_expose_fresh_v4_document_and_queue_invariants() -> None:
    assert {
        "ck_documents_state",
        "ck_documents_stage",
        "ck_documents_state_stage",
        "ck_documents_object_key_nonempty",
        "ck_documents_page_count_nonnegative",
        "ck_documents_chunk_count_nonnegative",
    } <= _constraint_names(Document.__table__)
    assert "content_path" not in Document.__table__.c
    assert not Document.__table__.c.object_key.nullable
    document_indexes = {index.name: index for index in Document.__table__.indexes}
    assert document_indexes["uq_documents_sha256"].unique
    assert document_indexes["uq_documents_object_key"].unique

    assert {
        "ck_ingestion_jobs_status",
        "ck_ingestion_jobs_stage",
        "ck_ingestion_jobs_lease_consistency",
        "ck_ingestion_jobs_fencing_nonnegative",
    } <= _constraint_names(IngestionJob.__table__)
    queue_index = {index.name: index for index in IngestionJob.__table__.indexes}[
        "ix_ingestion_jobs_queue"
    ]
    assert queue_index.dialect_options["postgresql"]["where"] is not None

    assert {
        "ck_chunks_page_range_ordered",
        "ck_chunks_parse_method",
        "ck_chunks_text_sha256",
    } <= _constraint_names(Chunk.__table__)
    chunk_indexes = {index.name: index for index in Chunk.__table__.indexes}
    assert (
        chunk_indexes["chunks_embedding_hnsw"].dialect_options["postgresql"]["using"]
        == "hnsw"
    )
    assert Chunk.__table__.c.embedding.type.dim == 1024

    assert {
        "ck_object_deletions_status",
        "ck_object_deletions_attempt_nonnegative",
        "ck_object_deletions_lease_consistency",
    } <= _constraint_names(ObjectDeletion.__table__)
    deletion_indexes = {index.name: index for index in ObjectDeletion.__table__.indexes}
    assert deletion_indexes["uq_object_deletions_object_key"].unique
    assert (
        deletion_indexes["ix_object_deletions_queue"].dialect_options["postgresql"][
            "where"
        ]
        is not None
    )


def test_models_expose_fresh_v4_library_and_chat_invariants() -> None:
    assert {
        "ck_library_nodes_kind",
        "ck_library_nodes_kind_document",
        "ck_library_nodes_boundary_folder",
        "uq_library_nodes_parent_name_key",
    } <= _constraint_names(LibraryNode.__table__)
    sibling_constraint = next(
        constraint
        for constraint in LibraryNode.__table__.constraints
        if constraint.name == "uq_library_nodes_parent_name_key"
    )
    assert sibling_constraint.dialect_options["postgresql"]["nulls_not_distinct"]

    assert {
        "ck_chats_scope_mode",
        "ck_chats_scope_version_positive",
        "ck_chats_next_turn_ordinal_positive",
        "ck_chats_title_length",
    } <= _constraint_names(Chat.__table__)
    assert {column.name for column in ChatScope.__table__.primary_key.columns} == {
        "chat_id",
        "node_id",
    }
    assert {
        "ck_chat_turns_status",
        "ck_chat_turns_status_consistency",
        "uq_chat_turns_chat_ordinal",
    } <= _constraint_names(ChatTurn.__table__)
    generating = {index.name: index for index in ChatTurn.__table__.indexes}[
        "uq_chat_turns_one_generating"
    ]
    assert generating.unique
    assert generating.dialect_options["postgresql"]["where"] is not None

    assert {
        "ck_turn_sources_deleted_disposition",
        "ck_turn_sources_rank",
        "ck_turn_sources_label",
        "uq_turn_sources_turn_label",
    } <= _constraint_names(TurnSource.__table__)
    assert not TurnSource.__table__.c.snapshot_text.nullable
    source_foreign_keys = {
        foreign_key.parent.name: foreign_key
        for foreign_key in TurnSource.__table__.foreign_keys
    }
    assert source_foreign_keys["document_id"].ondelete == "SET NULL"
    assert source_foreign_keys["chunk_id"].ondelete == "SET NULL"

    assert {
        "ck_turn_citations_ordinal_positive",
        "ck_turn_citations_source_rank",
        "uq_turn_citations_turn_source",
        "fk_turn_citations_source",
    } <= _constraint_names(TurnCitation.__table__)


def test_alembic_has_only_the_fresh_v4_root() -> None:
    api_root = Path(__file__).parents[1]
    config = Config(str(api_root / "alembic.ini"))
    scripts = ScriptDirectory.from_config(config)

    assert scripts.get_heads() == [EXPECTED_ALEMBIC_REVISION]
    revisions = list(scripts.walk_revisions())
    assert [revision.revision for revision in revisions] == [
        EXPECTED_ALEMBIC_REVISION,
        "0013_effective_runtime_identity",
        "0012_dynamic_ocr_tuning",
        "0011_v8f_release_maintenance",
        "0010_versioned_reprocessing",
        "0009_runtime_configuration",
        "0008_system_visibility",
        "0007_owner_setup",
        "0006_versioned_claim",
        "0005_document_reingest_action",
        "0004_create_children_capability",
        "0003_v6_ingestion_version_guard",
        "0002_v5_citation_highlights",
        "0001_v4_baseline",
    ]
    assert revisions[0].down_revision == "0013_effective_runtime_identity"
    assert revisions[1].down_revision == "0012_dynamic_ocr_tuning"
    assert revisions[2].down_revision == "0011_v8f_release_maintenance"
    assert revisions[3].down_revision == "0010_versioned_reprocessing"
    assert revisions[4].down_revision == "0009_runtime_configuration"
    assert revisions[5].down_revision == "0008_system_visibility"
    assert revisions[6].down_revision == "0007_owner_setup"
    assert revisions[7].down_revision == "0006_versioned_claim"
    assert revisions[8].down_revision == "0005_document_reingest_action"
    assert revisions[9].down_revision == "0004_create_children_capability"
    assert revisions[10].down_revision == "0003_v6_ingestion_version_guard"
    assert revisions[11].down_revision == "0002_v5_citation_highlights"
    assert revisions[12].down_revision == "0001_v4_baseline"
    assert revisions[13].down_revision is None

    migration = (api_root / "alembic" / "versions" / "0001_v4_baseline.py").read_text(
        encoding="utf-8"
    )
    assert "CREATE FUNCTION v4_readiness()" in migration
    assert "CREATE FUNCTION v4_runtime_identity" in migration
    assert "CREATE FUNCTION v4_admin_upload_preflight" in migration
    assert "CREATE FUNCTION v4_finalize_turn" in migration
    assert "content_path" not in migration
