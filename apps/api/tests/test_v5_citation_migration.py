from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

from app.db.models import Chunk, TurnSource

API_ROOT = Path(__file__).resolve().parents[1]
MIGRATION = API_ROOT / "alembic" / "versions" / "0002_v5_citation_highlights.py"


def test_v5_remains_the_forward_only_citation_migration() -> None:
    scripts = ScriptDirectory.from_config(Config(str(API_ROOT / "alembic.ini")))

    assert scripts.get_bases() == ["0001_v4_baseline"]
    assert scripts.get_heads() == ["0014_restart_without_backup"]
    source = MIGRATION.read_text(encoding="utf-8")
    assert 'down_revision: str | None = "0001_v4_baseline"' in source
    assert "empty document/source state" in source
    assert "forward-only" in source


def test_v5_anchor_columns_and_controlled_functions_are_declared() -> None:
    source = MIGRATION.read_text(encoding="utf-8")

    assert Chunk.__table__.c.highlight_anchor.nullable is False
    assert TurnSource.__table__.c.highlight_anchor.nullable is False
    assert "ck_chunks_highlight_anchor" in source
    assert "ck_turn_sources_highlight_anchor" in source
    assert "NEW.highlight_anchor" in source
    assert "chunk.highlight_anchor" in source
    assert "v5_citation_evidence" in source
    assert "turn_row.status = 'complete'" in source
    assert "public.v4_can_read_document(document.id)" in source
    assert "REVOKE ALL ON FUNCTION" in source
    assert "private, no-store" not in source


def test_v5_evidence_grant_is_api_only() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    evidence_grant = source[source.index("GRANT EXECUTE ON FUNCTION") :]

    assert "public.v5_citation_evidence(uuid,uuid,smallint)" in evidence_grant
    assert "TO rag_api;" in evidence_grant
    assert "TO rag_api, rag_maintenance;" in evidence_grant
