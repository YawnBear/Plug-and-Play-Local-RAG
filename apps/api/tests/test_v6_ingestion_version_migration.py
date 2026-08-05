from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

API_ROOT = Path(__file__).resolve().parents[1]
MIGRATION = API_ROOT / "alembic" / "versions" / "0003_v6_ingestion_version_guard.py"


def test_v6_version_guard_is_forward_only_head() -> None:
    scripts = ScriptDirectory.from_config(Config(str(API_ROOT / "alembic.ini")))

    assert scripts.get_heads() == ["0014_restart_without_backup"]
    source = MIGRATION.read_text(encoding="utf-8")
    assert 'down_revision: str | None = "0002_v5_citation_highlights"' in source
    assert "forward-only" in source


def test_version_guard_precedes_destructive_chunk_replacement() -> None:
    source = MIGRATION.read_text(encoding="utf-8")

    guard = source.index("chunk ingestion version does not match document version")
    deletion = source.index(
        "DELETE FROM public.chunks WHERE document_id = v_document_id"
    )
    assert guard < deletion
    assert "source.parser_version IS DISTINCT FROM" in source
    assert "source.chunking_version IS DISTINCT FROM" in source
    assert "source.embedding_version IS DISTINCT FROM" in source
    assert "'0003_v6_ingestion_version_guard'::text" in source
