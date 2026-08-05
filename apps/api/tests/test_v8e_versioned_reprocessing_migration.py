from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

MIGRATION = (
    Path(__file__).parents[1]
    / "alembic"
    / "versions"
    / "0010_versioned_reprocessing.py"
)


def _source() -> str:
    return MIGRATION.read_text(encoding="utf-8")


def test_v8e_is_the_single_forward_head() -> None:
    config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
    scripts = ScriptDirectory.from_config(config)

    assert scripts.get_heads() == ["0014_restart_without_backup"]
    assert 'down_revision: str | None = "0009_runtime_configuration"' in _source()
    assert "forward-only; restore a paired pre-V8E backup" in _source()


def test_new_uploads_are_bound_to_one_effective_ingestion_version() -> None:
    source = _source()

    assert "v10_effective_ingestion_version" in source
    assert "trg_prepare_document_generation" in source
    assert "trg_create_document_generation" in source
    assert "trg_bind_ingestion_target_generation" in source
    assert "active_generation_id" in source
    assert "parser.paddleocr-vl-1.6.adaptive-v2" in source
    assert "parser.paddleocr-vl-1.6.legacy-v1" in source


def test_reingestion_preserves_the_prior_ready_generation_on_failure() -> None:
    source = _source()

    assert "document_generations" in source
    assert "SET state = 'retained'" in source
    assert "active_generation_id = v_generation.id" in source
    assert "AND active_generation_id = v_job.target_generation_id" in source
    assert "document_reingestion_failed" in source


def test_shadow_embeddings_cut_over_only_after_qualification() -> None:
    source = _source()

    assert "chunk_embeddings" in source
    assert "reindex_tasks" in source
    assert "v10_complete_reindex_qualification" in source
    assert "source_unchanged" in source
    assert "count_parity" in source
    assert "'retrieval', true" in source
    assert "'rerank', true" in source
    assert "'citation', true" in source
    assert "'insufficient_context', true" in source
    assert "SET active_generation_id = generation.id WHERE singleton" in source


def test_live_retrieval_cannot_mix_embedding_generations() -> None:
    source = _source()

    start = source.index("CREATE FUNCTION public.v10_retrieve_active_chunks")
    retrieval = source[start:]
    assert "item.embedding_generation_id = state.active_generation_id" in retrieval
    assert "document.active_generation_id = chunk.document_generation_id" in retrieval
    assert "public.v4_can_read_document(document.id)" in retrieval
    assert "v10_retrieve_active_chunks(public.vector,integer,uuid[])" in source


def test_reprocessing_is_admin_gated_and_worker_tables_are_not_api_readable() -> None:
    source = _source()

    assert "a current restore-verified backup is required" in source
    assert "v10_admin_issue_reprocessing_grant" in source
    assert "'start_reprocessing'" in source
    assert "GRANT EXECUTE ON FUNCTION" in source
    assert "TO rag_worker" in source
    assert "NOT has_table_privilege('rag_api'," in source
    assert "public.chunk_embeddings','SELECT'" in source


def test_admin_inventory_exposes_only_exact_cleanup_candidates() -> None:
    source = _source()

    start = source.index("CREATE FUNCTION public.v10_admin_version_inventory")
    inventory = source[
        start : source.index(
            "CREATE FUNCTION public.v10_admin_set_ingestion_profile", start
        )
    ]
    assert "'filename', document.original_filename" in inventory
    assert "'generation_id', generation.id" in inventory
    assert "generation.operation_id IS NOT NULL" in inventory
    assert "document.active_generation_id <> generation.id" in inventory
