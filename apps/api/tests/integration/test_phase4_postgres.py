import asyncio
import uuid

import pytest
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.db.models import (
    Chat,
    ChatScope,
    ChatTurn,
    Chunk,
    Document,
    LibraryNode,
    TurnCitation,
    TurnSource,
)
from app.db.repositories import RetrievedChunk
from app.services.chats import (
    INSUFFICIENT_CONTEXT_ANSWER,
    RESTART_ERROR,
    BeginTurn,
    ChatConflict,
    ChatService,
    PreparedChatTurn,
    _snapshot_from_row,
)
from app.services.library import acquire_library_lock
from app.services.reranker import RerankedChunk
from tests.integration.phase4_safety import (
    assert_phase4_database,
    dedicated_phase4_environment,
    run_selector,
)

pytestmark = pytest.mark.integration
_PHASE4_TEST_SUITE_LOCK = 5_783_503_296_298_536_020


def _document(marker: str) -> Document:
    checksum = marker.removeprefix("phase4-").ljust(64, "a")[:64]
    return Document(
        id=uuid.uuid4(),
        sha256=checksum,
        original_filename=f"{marker}.pdf",
        mime_type="application/pdf",
        byte_size=1,
        content_path=None,
        object_key=f"documents/{checksum[:2]}/{checksum}.pdf",
        state="ready",
        stage="ready",
        parser_version="test",
        chunking_version="test",
        embedding_version="test",
        page_count=1,
        chunk_count=1,
    )


def _folder(name: str, parent_id: uuid.UUID | None = None) -> LibraryNode:
    return LibraryNode(
        id=uuid.uuid4(),
        parent_id=parent_id,
        kind="folder",
        name=name,
        name_key=name.casefold(),
        document_id=None,
    )


def _file(document: Document, parent_id: uuid.UUID | None) -> LibraryNode:
    return LibraryNode(
        id=uuid.uuid4(),
        parent_id=parent_id,
        kind="file",
        name=document.original_filename,
        name_key=document.original_filename.casefold(),
        document_id=document.id,
    )


def _chunk(document: Document) -> Chunk:
    return Chunk(
        id=uuid.uuid4(),
        document_id=document.id,
        ordinal=0,
        filename=document.original_filename,
        page_start=1,
        page_end=1,
        section="Controlled",
        text="The controlled answer is cobalt.",
        token_count=6,
        text_sha256="b" * 64,
        source_sha256=document.sha256,
        parse_method="direct",
        parser_version="test",
        chunking_version="test",
        embedding_version="test",
        schema_version="test",
        citation_label="page 1",
        embedding=[0.0] * 1024,
    )


async def _factory(
    *,
    effective_single_connection: bool = False,
    application_name: str | None = None,
) -> tuple[AsyncEngine, async_sessionmaker[AsyncSession], AsyncConnection]:
    environment = dedicated_phase4_environment()
    options: dict[str, object] = {}
    if effective_single_connection:
        options.update(pool_size=2, max_overflow=0)
    if application_name is not None:
        options["connect_args"] = {"application_name": application_name}
    engine = create_async_engine(environment["TEST_DATABASE_URL"], **options)
    guard = await engine.connect()
    try:
        await guard.execute(
            text("SELECT pg_advisory_lock(:key)"),
            {"key": _PHASE4_TEST_SUITE_LOCK},
        )
        await guard.commit()
        await assert_phase4_database(guard, environment["PHASE4_TEST_DATABASE_NAME"])
        await guard.commit()
    except BaseException:
        await guard.close()
        await engine.dispose()
        raise
    return (
        engine,
        async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False),
        guard,
    )


async def _release_factory(engine: AsyncEngine, guard: AsyncConnection) -> None:
    try:
        await guard.execute(
            text("SELECT pg_advisory_unlock(:key)"),
            {"key": _PHASE4_TEST_SUITE_LOCK},
        )
        await guard.commit()
    finally:
        await guard.close()
        await engine.dispose()


def test_postgres_mutations_return_fully_materialized_detached_chat() -> None:
    async def exercise() -> None:
        engine, factory, guard = await _factory()
        folder = _folder(f"scope-{uuid.uuid4().hex}")
        service = ChatService(factory, None, None, None)
        chat_id: uuid.UUID | None = None
        try:
            created = await service.create("Created")
            chat_id = created.id
            created_values = (created.created_at, created.updated_at)

            renamed = await service.rename(chat_id, "Renamed")
            assert renamed.title == "Renamed"
            assert renamed.created_at == created_values[0]
            assert renamed.updated_at >= created_values[1]

            async with factory() as session, session.begin():
                session.add(folder)

            scoped, scope_ids = await service.save_scope(
                chat_id, "selected", [folder.id]
            )
            assert scope_ids == (folder.id,)
            assert scoped.scope_mode == "selected"
            assert scoped.scope_version == 2
            assert scoped.created_at == created_values[0]
            assert scoped.updated_at >= renamed.updated_at
        finally:
            async with factory() as session, session.begin():
                if chat_id is not None:
                    await session.execute(delete(Chat).where(Chat.id == chat_id))
                await session.execute(
                    delete(LibraryNode).where(LibraryNode.id == folder.id)
                )
            await _release_factory(engine, guard)

    run_selector(exercise())


def test_postgres_snapshot_waits_for_move_and_freezes_committed_path() -> None:
    async def exercise() -> None:
        engine, factory, guard = await _factory()
        marker = uuid.uuid4().hex
        document = _document(f"phase4-{marker}")
        chunk = _chunk(document)
        old_folder = _folder(f"old-{marker}")
        committed_folder = _folder(f"committed-{marker}")
        later_folder = _folder(f"later-{marker}")
        file_node = _file(document, old_folder.id)
        chat = Chat(id=uuid.uuid4(), title="snapshot move")
        token = uuid.uuid4()
        turn = ChatTurn(
            id=uuid.uuid4(),
            chat_id=chat.id,
            ordinal=1,
            question="controlled question",
            status="generating",
            attempt=1,
            scope_version=1,
            generation_token=token,
        )
        begin = BeginTurn(
            chat.id,
            turn.id,
            token,
            turn.question,
            1,
            (document.id,),
            (),
            turn.question,
        )
        ranked = (RerankedChunk(RetrievedChunk(chunk, 0.1), 0.9),)
        service = ChatService(factory, None, None, None)
        move_flushed = asyncio.Event()
        allow_move_commit = asyncio.Event()
        snapshot_started = asyncio.Event()
        move_task: asyncio.Task[None] | None = None
        snapshot_task: asyncio.Task[tuple] | None = None
        try:
            async with factory() as session, session.begin():
                session.add_all(
                    [
                        document,
                        chunk,
                        old_folder,
                        committed_folder,
                        later_folder,
                        file_node,
                        chat,
                        turn,
                    ]
                )

            async def move_before_snapshot() -> None:
                async with factory() as session, session.begin():
                    await acquire_library_lock(session)
                    moving = await session.get(LibraryNode, file_node.id)
                    assert moving is not None
                    moving.parent_id = committed_folder.id
                    await session.flush()
                    move_flushed.set()
                    await allow_move_commit.wait()

            async def snapshot_after_move_locks() -> tuple:
                snapshot_started.set()
                return await service._snapshot_sources(begin, ranked, ())

            move_task = asyncio.create_task(move_before_snapshot())
            await asyncio.wait_for(move_flushed.wait(), timeout=5)
            snapshot_task = asyncio.create_task(snapshot_after_move_locks())
            await snapshot_started.wait()
            await asyncio.sleep(0)
            assert not snapshot_task.done()

            allow_move_commit.set()
            await move_task
            snapshots, prompt = await snapshot_task
            committed_path = f"/{committed_folder.name}/{document.original_filename}"
            assert prompt is not None
            assert snapshots[0].logical_path == committed_path

            async with factory() as session, session.begin():
                await acquire_library_lock(session)
                moving = await session.get(LibraryNode, file_node.id)
                assert moving is not None
                moving.parent_id = later_folder.id

            async with factory() as session:
                stored = await session.get(TurnSource, (turn.id, 1))
                assert stored is not None
                assert stored.logical_path == committed_path
        finally:
            allow_move_commit.set()
            pending = [
                task
                for task in (move_task, snapshot_task)
                if task is not None and not task.done()
            ]
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
            try:
                async with factory() as session, session.begin():
                    await session.execute(delete(Chat).where(Chat.id == chat.id))
                    await session.execute(
                        delete(Document).where(Document.id == document.id)
                    )
                    await session.execute(
                        delete(LibraryNode).where(
                            LibraryNode.id.in_(
                                [
                                    old_folder.id,
                                    committed_folder.id,
                                    later_folder.id,
                                ]
                            )
                        )
                    )
            finally:
                await _release_factory(engine, guard)

    run_selector(exercise())


def test_postgres_external_phase_barriers_hold_no_database_transaction() -> None:
    async def exercise() -> None:
        application_name = f"phase4-barrier-{uuid.uuid4().hex}"
        engine, factory, guard = await _factory(
            effective_single_connection=True,
            application_name=application_name,
        )
        marker = uuid.uuid4().hex
        document = _document(f"phase4-{marker}")
        chunk = _chunk(document)
        file_node = _file(document, None)
        chat = Chat(id=uuid.uuid4(), title="barriers")

        class Barrier:
            def __init__(self) -> None:
                self.entered = asyncio.Event()
                self.release = asyncio.Event()

            async def pause(self) -> None:
                self.entered.set()
                await self.release.wait()

        embedding_barrier = Barrier()
        rerank_barrier = Barrier()
        availability_barrier = Barrier()
        token_barrier = Barrier()
        candidate = RetrievedChunk(chunk, 0.1)

        class Retrieval:
            async def retrieve(self, *args: object, **kwargs: object) -> list:
                await embedding_barrier.pause()
                return [candidate]

        class Reranker:
            async def rerank(self, *args: object, **kwargs: object) -> list:
                await rerank_barrier.pause()
                return [RerankedChunk(candidate, 0.9)]

        class Generator:
            async def check_available(self) -> None:
                await availability_barrier.pause()

            async def stream(self, prompt: str):
                yield "The controlled answer is "
                await token_barrier.pause()
                yield "cobalt [S1]"

        service = ChatService(factory, Retrieval(), Reranker(), Generator())

        async def assert_database_available() -> None:
            async def query() -> None:
                async with factory() as session:
                    assert await session.scalar(select(1)) == 1
                    idle = await session.scalar(
                        text(
                            "SELECT count(*) FROM pg_stat_activity "
                            "WHERE application_name = :application_name "
                            "AND state = 'idle in transaction'"
                        ),
                        {"application_name": application_name},
                    )
                    assert idle == 0

            await asyncio.wait_for(query(), timeout=2)

        prepare_task: asyncio.Task[PreparedChatTurn] | None = None
        token_task: asyncio.Task[str] | None = None
        try:
            async with factory() as session, session.begin():
                session.add_all([document, chunk, file_node, chat])

            prepare_task = asyncio.create_task(
                service.prepare_message(chat.id, "What is the controlled answer?")
            )
            await embedding_barrier.entered.wait()
            await assert_database_available()
            embedding_barrier.release.set()

            await rerank_barrier.entered.wait()
            await assert_database_available()
            rerank_barrier.release.set()

            await availability_barrier.entered.wait()
            await assert_database_available()
            availability_barrier.release.set()
            prepared = await prepare_task

            stream = service.stream(prepared)
            assert "event: sources" in await anext(stream)
            await assert_database_available()
            assert "event: token" in await anext(stream)
            token_task = asyncio.create_task(anext(stream))
            await token_barrier.entered.wait()
            await assert_database_available()
            token_barrier.release.set()
            assert "event: token" in await token_task
            assert "event: final" in await anext(stream)
            with pytest.raises(StopAsyncIteration):
                await anext(stream)
        finally:
            for barrier in (
                embedding_barrier,
                rerank_barrier,
                availability_barrier,
                token_barrier,
            ):
                barrier.release.set()
            pending = [
                task
                for task in (prepare_task, token_task)
                if task is not None and not task.done()
            ]
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
            try:
                async with factory() as session, session.begin():
                    await session.execute(delete(Chat).where(Chat.id == chat.id))
                    await session.execute(
                        delete(Document).where(Document.id == document.id)
                    )
            finally:
                await _release_factory(engine, guard)

    run_selector(exercise())


def test_postgres_dynamic_scope_concurrency_conflicts_and_ordinals() -> None:
    async def exercise() -> None:
        engine, factory, guard = await _factory()
        marker = uuid.uuid4().hex
        selected = _folder(f"selected-{marker}")
        outside = _folder(f"outside-{marker}")
        first_document = _document(f"phase4-{marker}")
        first_file = _file(first_document, outside.id)
        chat = Chat(id=uuid.uuid4(), title="scope test", scope_mode="selected")
        all_ready_chat = Chat(id=uuid.uuid4(), title="all ready")
        service = ChatService(factory, None, None, None)
        future_document: Document | None = None
        try:
            async with factory() as session, session.begin():
                session.add_all(
                    [
                        selected,
                        outside,
                        first_document,
                        first_file,
                        chat,
                        all_ready_chat,
                    ]
                )
                await session.flush()
                session.add(ChatScope(chat_id=chat.id, node_id=selected.id))

            empty = await service._begin_new(chat.id, "empty selected scope")
            assert empty.already_complete
            assert empty.document_ids == ()

            async with factory() as session, session.begin():
                moved = await session.get(LibraryNode, first_file.id)
                assert moved is not None
                moved.parent_id = selected.id

            attempts = await asyncio.gather(
                service._begin_new(chat.id, "concurrent one"),
                service._begin_new(chat.id, "concurrent two"),
                return_exceptions=True,
            )
            accepted = [item for item in attempts if isinstance(item, BeginTurn)]
            rejected = [item for item in attempts if isinstance(item, ChatConflict)]
            assert len(accepted) == 1
            assert len(rejected) == 1
            active = accepted[0]
            assert active.document_ids == (first_document.id,)

            with pytest.raises(ChatConflict):
                await service.save_scope(chat.id, "all_ready", [])
            with pytest.raises(ChatConflict):
                await service.delete(chat.id)

            await service._transition(active, "interrupted", "controlled disconnect")
            async with factory() as session, session.begin():
                moved = await session.get(LibraryNode, first_file.id)
                assert moved is not None
                moved.parent_id = outside.id

            retried = await service._begin_retry(chat.id, active.turn_id)
            assert retried.turn_id == active.turn_id
            assert retried.attempt == 2
            assert retried.already_complete
            assert retried.document_ids == ()

            future_document = _document(f"phase4-{uuid.uuid4().hex}")
            future_file = _file(future_document, selected.id)
            async with factory() as session, session.begin():
                session.add_all([future_document, future_file])

            future = await service._begin_new(chat.id, "future descendant")
            assert future.document_ids == (future_document.id,)

            all_ready = await service._begin_new(all_ready_chat.id, "all ready")
            assert all_ready.document_ids == tuple(
                sorted([first_document.id, future_document.id], key=str)
            )

            async with factory() as session:
                ordinals = list(
                    await session.scalars(
                        select(ChatTurn.ordinal)
                        .where(ChatTurn.chat_id == chat.id)
                        .order_by(ChatTurn.ordinal)
                    )
                )
                assert ordinals == [1, 2, 3]
                retry_turn = await session.get(ChatTurn, active.turn_id)
                assert retry_turn is not None
                assert retry_turn.attempt == 2
                assert retry_turn.final_answer == INSUFFICIENT_CONTEXT_ANSWER
        finally:
            async with factory() as session, session.begin():
                await session.execute(
                    delete(Chat).where(Chat.id.in_([chat.id, all_ready_chat.id]))
                )
                await session.execute(
                    delete(Document).where(
                        Document.id.in_(
                            [
                                first_document.id,
                                *(
                                    [future_document.id]
                                    if future_document is not None
                                    else []
                                ),
                            ]
                        )
                    )
                )
                await session.execute(
                    delete(LibraryNode).where(
                        LibraryNode.id.in_([selected.id, outside.id])
                    )
                )
            await _release_factory(engine, guard)

    run_selector(exercise())


def test_postgres_startup_repair_and_latest_retry_fencing() -> None:
    async def exercise() -> None:
        engine, factory, guard = await _factory()
        chat = Chat(
            id=uuid.uuid4(),
            title="repair",
            scope_mode="selected",
            next_turn_ordinal=2,
        )
        token = uuid.uuid4()
        turn = ChatTurn(
            id=uuid.uuid4(),
            chat_id=chat.id,
            ordinal=1,
            question="repair me",
            status="generating",
            attempt=1,
            scope_version=1,
            generation_token=token,
        )
        service = ChatService(factory, None, None, None)
        try:
            async with factory() as session, session.begin():
                session.add_all([chat, turn])

            assert await service.repair_interrupted() == 1
            async with factory() as session:
                repaired = await session.get(ChatTurn, turn.id)
                assert repaired is not None
                assert repaired.status == "interrupted"
                assert repaired.error == RESTART_ERROR
                assert repaired.generation_token is None

            retried = await service.prepare_retry(chat.id, turn.id)
            assert retried.turn_id == turn.id
            assert retried.already_complete
            async with factory() as session:
                completed = await session.get(ChatTurn, turn.id)
                assert completed is not None
                assert completed.status == "complete"
                assert completed.attempt == 2
                assert completed.ordinal == 1

            newer = await service._begin_new(chat.id, "newer turn")
            assert newer.already_complete
            async with factory() as session, session.begin():
                older = await session.get(ChatTurn, turn.id)
                assert older is not None
                older.status = "interrupted"
                older.generation_token = None
                older.final_answer = None
                older.insufficient_context = False
                older.error = "older interrupted turn"
                older.completed_at = None

            with pytest.raises(ChatConflict, match="latest"):
                await service._begin_retry(chat.id, turn.id)
        finally:
            async with factory() as session, session.begin():
                await session.execute(delete(Chat).where(Chat.id == chat.id))
            await _release_factory(engine, guard)

    run_selector(exercise())


def test_postgres_deleted_citation_keeps_immutable_snapshot() -> None:
    async def exercise() -> None:
        engine, factory, guard = await _factory()
        marker = uuid.uuid4().hex
        document = _document(f"phase4-{marker}")
        chat = Chat(id=uuid.uuid4(), title="citation")
        token = uuid.uuid4()
        turn = ChatTurn(
            id=uuid.uuid4(),
            chat_id=chat.id,
            ordinal=1,
            question="cited",
            status="generating",
            attempt=1,
            scope_version=1,
            generation_token=token,
            final_answer=None,
            insufficient_context=False,
            completed_at=None,
        )
        chunk_snapshot = uuid.uuid4()
        source = TurnSource(
            turn_id=turn.id,
            rank=1,
            label="S1",
            document_id=document.id,
            chunk_id=None,
            document_id_snapshot=document.id,
            chunk_id_snapshot=chunk_snapshot,
            original_filename=document.original_filename,
            display_name="Original",
            logical_path="/Original",
            page_start=1,
            page_end=1,
            section=None,
            source_sha256=document.sha256,
            text_sha256="b" * 64,
            retrieval_distance=0.1,
            rerank_score=0.9,
            snapshot_text="fact",
            token_count=1,
        )
        prepared_source = _snapshot_from_row(source)
        prepared = PreparedChatTurn(
            chat.id, turn.id, token, "prompt", (prepared_source,)
        )
        service = ChatService(factory, None, None, None)
        delete_locked = asyncio.Event()
        allow_delete_commit = asyncio.Event()
        completion_started = asyncio.Event()
        delete_task: asyncio.Task[None] | None = None
        complete_task: asyncio.Task[tuple] | None = None
        try:
            async with factory() as session, session.begin():
                session.add_all([document, chat, turn, source])

            async def delete_while_holding_library_lock() -> None:
                async with factory() as session, session.begin():
                    await acquire_library_lock(session)
                    stored_document = await session.get(Document, document.id)
                    assert stored_document is not None
                    await session.delete(stored_document)
                    await session.flush()
                    delete_locked.set()
                    await allow_delete_commit.wait()

            async def complete_after_delete_locks() -> tuple:
                completion_started.set()
                return await service._complete(
                    prepared, "answer [S1]", False, (prepared_source,)
                )

            delete_task = asyncio.create_task(delete_while_holding_library_lock())
            await asyncio.wait_for(delete_locked.wait(), timeout=5)
            complete_task = asyncio.create_task(complete_after_delete_locks())
            await completion_started.wait()
            await asyncio.sleep(0)
            assert not complete_task.done()

            allow_delete_commit.set()
            await delete_task
            persisted = await complete_task
            assert persisted[0].document_id is None
            assert persisted[0].document_id_snapshot == document.id
            async with factory() as session:
                stored = await session.get(TurnSource, (turn.id, 1))
                assert stored is not None
                assert stored.document_id is None
                assert stored.document_id_snapshot == document.id
                assert stored.chunk_id_snapshot == chunk_snapshot
                assert stored.logical_path == "/Original"
                assert await session.get(TurnCitation, (turn.id, 1)) is not None
                completed = await session.get(ChatTurn, turn.id)
                assert completed is not None
                assert completed.status == "complete"
        finally:
            allow_delete_commit.set()
            pending = [
                task
                for task in (delete_task, complete_task)
                if task is not None and not task.done()
            ]
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
            async with factory() as session, session.begin():
                await session.execute(delete(Chat).where(Chat.id == chat.id))
                await session.execute(
                    delete(Document).where(Document.id == document.id)
                )
            await _release_factory(engine, guard)

    run_selector(exercise())
