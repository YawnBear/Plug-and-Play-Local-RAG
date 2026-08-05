import time
import unicodedata
import uuid
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.security.actor import ActorContext

MIN_SEARCH_QUERY_LENGTH = 1
MAX_SEARCH_QUERY_LENGTH = 200
MIN_SEARCH_LIMIT = 1
MAX_SEARCH_LIMIT = 100


class SearchValidation(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class LibrarySearchResult:
    document_id: uuid.UUID
    node_id: uuid.UUID
    filename: str
    display_name: str
    logical_path: str
    page_start: int | None
    page_end: int | None
    match_kinds: tuple[str, ...]
    rank: float


@dataclass(frozen=True, slots=True)
class LibrarySearchPage:
    query: str
    page: int
    limit: int
    total: int
    items: tuple[LibrarySearchResult, ...]
    correlation_id: uuid.UUID
    stage_timings_ms: dict[str, float]


LEXICAL_SEARCH_SQL = text(
    """
    WITH RECURSIVE paths AS (
        SELECT node.id, node.parent_id, node.document_id, node.name,
               ('/' || node.name)::text AS logical_path,
               ARRAY[node.id]::uuid[] AS visited
        FROM library_nodes AS node
        WHERE node.parent_id IS NULL
          AND v4_current_actor_id() = :actor_id
          AND v4_can_view_library_node(node.id)
        UNION ALL
        SELECT child.id, child.parent_id, child.document_id, child.name,
               (parent.logical_path || '/' || child.name)::text,
               parent.visited || child.id
        FROM library_nodes AS child
        JOIN paths AS parent ON parent.id = child.parent_id
        WHERE NOT child.id = ANY(parent.visited)
          AND cardinality(parent.visited) < 256
          AND v4_current_actor_id() = :actor_id
          AND v4_can_view_library_node(child.id)
    ),
    query AS (
        SELECT plainto_tsquery('simple', :query) AS value
    ),
    matches AS (
        SELECT document.id AS document_id,
               path.id AS node_id,
               document.original_filename AS filename,
               path.name AS display_name,
               path.logical_path,
               min(chunk.page_start) FILTER (
                   WHERE to_tsvector('simple', chunk.text) @@ query.value
               ) AS page_start,
               max(chunk.page_end) FILTER (
                   WHERE to_tsvector('simple', chunk.text) @@ query.value
               ) AS page_end,
               ARRAY_REMOVE(ARRAY[
                   CASE WHEN to_tsvector(
                       'simple', document.original_filename
                   ) @@ query.value THEN 'filename' END,
                   CASE WHEN to_tsvector(
                       'simple', path.logical_path
                   ) @@ query.value THEN 'path' END,
                   CASE WHEN bool_or(
                       to_tsvector('simple', chunk.text) @@ query.value
                   ) THEN 'content' END
               ], NULL) AS match_kinds,
               greatest(
                   ts_rank_cd(
                       to_tsvector('simple', document.original_filename),
                       query.value
                   ),
                   ts_rank_cd(
                       to_tsvector('simple', path.logical_path),
                       query.value
                   ),
                   coalesce(max(ts_rank_cd(
                       to_tsvector('simple', chunk.text), query.value
                   )), 0)
               )::float AS rank
        FROM paths AS path
        JOIN documents AS document ON document.id = path.document_id
        LEFT JOIN chunks AS chunk ON chunk.document_id = document.id
        CROSS JOIN query
        WHERE path.document_id IS NOT NULL
          AND document.state = 'ready'
          AND v4_current_actor_id() = :actor_id
          AND v4_can_read_document(document.id)
        GROUP BY document.id, path.id, path.name, path.logical_path, query.value
        HAVING to_tsvector(
                   'simple', document.original_filename
               ) @@ query.value
            OR to_tsvector('simple', path.logical_path) @@ query.value
            OR bool_or(to_tsvector('simple', chunk.text) @@ query.value)
    )
    SELECT matches.*, count(*) OVER () AS total
    FROM matches
    ORDER BY rank DESC, logical_path COLLATE "C", document_id
    LIMIT :limit OFFSET :offset
    """
)
LEXICAL_SEARCH_COUNT_SQL = text(
    "SELECT count(*) FROM ("
    + str(LEXICAL_SEARCH_SQL).split("ORDER BY rank DESC", maxsplit=1)[0]
    + ") AS lexical_matches"
)


def normalize_search_query(value: str) -> str:
    query = unicodedata.normalize("NFC", value.strip())
    if not MIN_SEARCH_QUERY_LENGTH <= len(query) <= MAX_SEARCH_QUERY_LENGTH:
        raise SearchValidation("query must contain 1-200 characters")
    if any(unicodedata.category(char) in {"Cc", "Cf", "Cs"} for char in query):
        raise SearchValidation("query contains a forbidden Unicode character")
    return query


class SearchService:
    async def search(
        self,
        actor: ActorContext,
        session: AsyncSession,
        query: str,
        *,
        page: int = 1,
        limit: int = 20,
        correlation_id: uuid.UUID | None = None,
    ) -> LibrarySearchPage:
        normalized = normalize_search_query(query)
        if page < 1:
            raise SearchValidation("page must be positive")
        if not MIN_SEARCH_LIMIT <= limit <= MAX_SEARCH_LIMIT:
            raise SearchValidation("limit must be between 1 and 100")
        request_id = correlation_id or uuid.uuid4()
        started = time.perf_counter()
        rows = (
            await session.execute(
                LEXICAL_SEARCH_SQL,
                {
                    "actor_id": actor.user_id,
                    "query": normalized,
                    "limit": limit,
                    "offset": (page - 1) * limit,
                },
            )
        ).mappings()
        materialized = list(rows)
        total = (
            int(materialized[0]["total"])
            if materialized
            else int(
                await session.scalar(
                    LEXICAL_SEARCH_COUNT_SQL,
                    {"actor_id": actor.user_id, "query": normalized},
                )
                or 0
            )
        )
        elapsed = round((time.perf_counter() - started) * 1000, 3)
        items = tuple(
            LibrarySearchResult(
                document_id=row["document_id"],
                node_id=row["node_id"],
                filename=row["filename"],
                display_name=row["display_name"],
                logical_path=row["logical_path"],
                page_start=row["page_start"],
                page_end=row["page_end"],
                match_kinds=tuple(row["match_kinds"]),
                rank=float(row["rank"]),
            )
            for row in materialized
        )
        return LibrarySearchPage(
            query=normalized,
            page=page,
            limit=limit,
            total=total,
            items=items,
            correlation_id=request_id,
            stage_timings_ms={"database_search": elapsed},
        )
