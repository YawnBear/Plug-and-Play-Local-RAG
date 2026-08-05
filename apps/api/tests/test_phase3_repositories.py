import asyncio
import uuid

from sqlalchemy.dialects import postgresql

from app.db.repositories import ChunkRepository
from app.security.actor import ActorContext, ActorRole


class _Rows:
    def __iter__(self):
        return iter(())

    def all(self) -> list[object]:
        return []


class _RetrievalSession:
    statement = None

    async def execute(
        self, statement: object, parameters: dict[str, object] | None = None
    ) -> _Rows:
        if "v10_retrieve_active_chunks" in str(statement):
            self.statement = statement
        return _Rows()


def test_retrieval_uses_the_single_active_generation_contract() -> None:
    session = _RetrievalSession()
    vector = [1.0, *([0.0] * 1023)]
    actor = ActorContext(
        user_id=uuid.uuid4(),
        role=ActorRole.MEMBER,
        authentication_version=1,
        authorization_version=1,
        session_id=uuid.uuid4(),
    )

    result = asyncio.run(ChunkRepository(session).retrieve(actor, vector, limit=20))

    sql = str(session.statement.compile(dialect=postgresql.dialect()))
    assert result == []
    assert "v10_retrieve_active_chunks" in sql
    assert "query_vector" in sql
    assert "document_ids" in sql
