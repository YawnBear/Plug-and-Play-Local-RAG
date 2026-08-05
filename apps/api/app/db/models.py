import uuid
from datetime import datetime

from pgvector.sqlalchemy import VECTOR
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from app.domain import DocumentState, JobStatus


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class Document(TimestampMixin, Base):
    __tablename__ = "documents"
    __table_args__ = (
        CheckConstraint("byte_size >= 0", name="ck_documents_byte_size_nonnegative"),
        CheckConstraint(
            "state IN ('uploaded','parsing','chunking','embedding','indexing',"
            "'ready','failed')",
            name="ck_documents_state",
        ),
        CheckConstraint(
            "stage IN ('uploaded','parsing','chunking','embedding','indexing',"
            "'ready','failed')",
            name="ck_documents_stage",
        ),
        CheckConstraint(
            "page_count IS NULL OR page_count >= 0",
            name="ck_documents_page_count_nonnegative",
        ),
        CheckConstraint(
            "chunk_count >= 0", name="ck_documents_chunk_count_nonnegative"
        ),
        CheckConstraint(
            "char_length(object_key) > 0",
            name="ck_documents_object_key_nonempty",
        ),
        CheckConstraint("sha256 ~ '^[0-9a-f]{64}$'", name="ck_documents_sha256"),
        CheckConstraint("mime_type = 'application/pdf'", name="ck_documents_pdf_only"),
        CheckConstraint("state = stage", name="ck_documents_state_stage"),
        CheckConstraint(
            "(state = 'failed' AND error IS NOT NULL) OR "
            "(state <> 'failed' AND error IS NULL)",
            name="ck_documents_error_state",
        ),
        Index("uq_documents_sha256", "sha256", unique=True),
        Index("uq_documents_object_key", "object_key", unique=True),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    original_filename: Mapped[str] = mapped_column(String(512), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(127), nullable=False)
    byte_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    object_key: Mapped[str] = mapped_column(String(512), nullable=False)
    state: Mapped[str] = mapped_column(
        String(32), default=DocumentState.UPLOADED.value, nullable=False
    )
    stage: Mapped[str] = mapped_column(
        String(32), default=DocumentState.UPLOADED.value, nullable=False
    )
    error: Mapped[str | None] = mapped_column(Text)
    parser_version: Mapped[str] = mapped_column(String(64), nullable=False)
    chunking_version: Mapped[str] = mapped_column(String(64), nullable=False)
    embedding_version: Mapped[str] = mapped_column(String(128), nullable=False)
    page_count: Mapped[int | None] = mapped_column(Integer)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    jobs: Mapped[list["IngestionJob"]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )
    chunks: Mapped[list["Chunk"]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )


class LibraryNode(TimestampMixin, Base):
    __tablename__ = "library_nodes"
    __table_args__ = (
        CheckConstraint("kind IN ('folder','file')", name="ck_library_nodes_kind"),
        CheckConstraint(
            "(kind = 'folder' AND document_id IS NULL) OR "
            "(kind = 'file' AND document_id IS NOT NULL)",
            name="ck_library_nodes_kind_document",
        ),
        CheckConstraint(
            "(kind = 'folder' AND uploader_user_id IS NULL) OR "
            "(kind = 'file' AND uploader_user_id IS NOT NULL)",
            name="ck_library_nodes_kind_uploader",
        ),
        CheckConstraint(
            "access_boundary = false OR kind = 'folder'",
            name="ck_library_nodes_boundary_folder",
        ),
        CheckConstraint(
            "char_length(name) BETWEEN 1 AND 255",
            name="ck_library_nodes_name_length",
        ),
        CheckConstraint(
            "octet_length(name_key) <= 1024",
            name="ck_library_nodes_name_key_bytes",
        ),
        CheckConstraint(
            "char_length(name_key) > 0", name="ck_library_nodes_name_key_nonempty"
        ),
        UniqueConstraint(
            "parent_id",
            "name_key",
            name="uq_library_nodes_parent_name_key",
            postgresql_nulls_not_distinct=True,
        ),
        Index("uq_library_nodes_document_id", "document_id", unique=True),
        Index("ix_library_nodes_parent_id", "parent_id"),
        Index(
            "ix_library_nodes_name_fts",
            text("to_tsvector('simple', name)"),
            postgresql_using="gin",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("library_nodes.id", ondelete="RESTRICT"),
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    name: Mapped[str] = mapped_column(String(255, collation="C"), nullable=False)
    name_key: Mapped[str] = mapped_column(Text(collation="C"), nullable=False)
    document_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="CASCADE"),
    )
    uploader_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
    )
    access_boundary: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )


class Chat(TimestampMixin, Base):
    __tablename__ = "chats"
    __table_args__ = (
        CheckConstraint(
            "scope_mode IN ('all_ready','selected')", name="ck_chats_scope_mode"
        ),
        CheckConstraint("scope_version >= 1", name="ck_chats_scope_version_positive"),
        CheckConstraint(
            "next_turn_ordinal >= 1",
            name="ck_chats_next_turn_ordinal_positive",
        ),
        CheckConstraint(
            "char_length(title) BETWEEN 1 AND 255",
            name="ck_chats_title_length",
        ),
        Index(
            "ix_chats_updated_id_desc",
            text("updated_at DESC"),
            text("id DESC"),
        ),
        Index(
            "ix_chats_owner_updated_id_desc",
            "owner_user_id",
            text("updated_at DESC"),
            text("id DESC"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    title: Mapped[str] = mapped_column(
        String(255), default="New chat", server_default="New chat", nullable=False
    )
    title_is_manual: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )
    scope_mode: Mapped[str] = mapped_column(
        String(16), default="all_ready", server_default="all_ready", nullable=False
    )
    scope_version: Mapped[int] = mapped_column(
        BigInteger, default=1, server_default="1", nullable=False
    )
    next_turn_ordinal: Mapped[int] = mapped_column(
        BigInteger, default=1, server_default="1", nullable=False
    )
    owner_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )


class ChatScope(Base):
    __tablename__ = "chat_scopes"
    __table_args__ = (Index("ix_chat_scopes_node_id", "node_id"),)

    chat_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("chats.id", ondelete="CASCADE"),
        primary_key=True,
    )
    node_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("library_nodes.id", ondelete="CASCADE"),
        primary_key=True,
    )


class ChatTurn(TimestampMixin, Base):
    __tablename__ = "chat_turns"
    __table_args__ = (
        CheckConstraint("ordinal >= 1", name="ck_chat_turns_ordinal_positive"),
        CheckConstraint("attempt >= 1", name="ck_chat_turns_attempt_positive"),
        CheckConstraint(
            "scope_version >= 1", name="ck_chat_turns_scope_version_positive"
        ),
        CheckConstraint(
            "char_length(question) BETWEEN 1 AND 2000",
            name="ck_chat_turns_question_length",
        ),
        CheckConstraint(
            "status IN "
            "('generating','complete','failed','interrupted','length_limited',"
            "'citation_failed','access_revoked')",
            name="ck_chat_turns_status",
        ),
        CheckConstraint(
            "(status = 'generating' AND generation_token IS NOT NULL "
            "AND final_answer IS NULL AND error IS NULL "
            "AND (partial_answer IS NULL OR char_length(partial_answer) > 0) "
            "AND completed_at IS NULL AND insufficient_context = false) OR "
            "(status = 'complete' AND generation_token IS NULL "
            "AND final_answer IS NOT NULL AND char_length(final_answer) > 0 "
            "AND partial_answer IS NULL AND error IS NULL "
            "AND completed_at IS NOT NULL) OR "
            "(status = 'length_limited' AND generation_token IS NULL "
            "AND final_answer IS NULL AND partial_answer IS NOT NULL "
            "AND char_length(partial_answer) > 0 AND completed_at IS NULL "
            "AND error = 'response reached generation limit' "
            "AND insufficient_context = false) OR "
            "(status = 'citation_failed' AND generation_token IS NULL "
            "AND final_answer IS NULL AND partial_answer IS NOT NULL "
            "AND char_length(partial_answer) > 0 AND completed_at IS NULL "
            "AND error = 'citation validation failed' "
            "AND insufficient_context = false) OR "
            "(status IN ('failed','interrupted','access_revoked') "
            "AND generation_token IS NULL "
            "AND final_answer IS NULL AND partial_answer IS NULL "
            "AND completed_at IS NULL "
            "AND error IS NOT NULL AND char_length(error) BETWEEN 1 AND 500 "
            "AND insufficient_context = false)",
            name="ck_chat_turns_status_consistency",
        ),
        UniqueConstraint("chat_id", "ordinal", name="uq_chat_turns_chat_ordinal"),
        Index(
            "uq_chat_turns_one_generating",
            "chat_id",
            unique=True,
            postgresql_where=text("status = 'generating'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    chat_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("chats.id", ondelete="CASCADE"),
        nullable=False,
    )
    ordinal: Mapped[int] = mapped_column(BigInteger, nullable=False)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    attempt: Mapped[int] = mapped_column(
        Integer, default=1, server_default="1", nullable=False
    )
    scope_version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    generation_token: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    final_answer: Mapped[str | None] = mapped_column(Text)
    partial_answer: Mapped[str | None] = mapped_column(Text)
    insufficient_context: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )
    error: Mapped[str | None] = mapped_column(String(500))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class TurnSource(Base):
    __tablename__ = "turn_sources"
    __table_args__ = (
        CheckConstraint("rank BETWEEN 1 AND 8", name="ck_turn_sources_rank"),
        CheckConstraint("label = 'S' || rank::text", name="ck_turn_sources_label"),
        CheckConstraint(
            "page_start >= 1 AND page_end >= page_start",
            name="ck_turn_sources_page_range",
        ),
        CheckConstraint(
            "source_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_turn_sources_source_sha256",
        ),
        CheckConstraint(
            "text_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_turn_sources_text_sha256",
        ),
        CheckConstraint("token_count > 0", name="ck_turn_sources_token_count_positive"),
        CheckConstraint(
            "document_id IS NOT NULL OR owner_authorized_at_deletion IS NOT NULL",
            name="ck_turn_sources_deleted_disposition",
        ),
        UniqueConstraint("turn_id", "label", name="uq_turn_sources_turn_label"),
        Index("ix_turn_sources_document_id", "document_id"),
        Index("ix_turn_sources_chunk_id", "chunk_id"),
    )

    turn_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("chat_turns.id", ondelete="CASCADE"),
        primary_key=True,
    )
    rank: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    label: Mapped[str] = mapped_column(String(16), nullable=False)
    document_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("documents.id", ondelete="SET NULL")
    )
    chunk_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("chunks.id", ondelete="SET NULL")
    )
    document_id_snapshot: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )
    chunk_id_snapshot: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )
    original_filename: Mapped[str] = mapped_column(String(512), nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    logical_path: Mapped[str] = mapped_column(Text, nullable=False)
    page_start: Mapped[int] = mapped_column(Integer, nullable=False)
    page_end: Mapped[int] = mapped_column(Integer, nullable=False)
    section: Mapped[str | None] = mapped_column(Text)
    source_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    text_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    retrieval_distance: Mapped[float] = mapped_column(Float, nullable=False)
    rerank_score: Mapped[float] = mapped_column(Float, nullable=False)
    snapshot_text: Mapped[str] = mapped_column(Text, nullable=False)
    highlight_anchor: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    token_count: Mapped[int] = mapped_column(Integer, nullable=False)
    owner_authorized_at_deletion: Mapped[bool | None] = mapped_column(Boolean)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class TurnCitation(Base):
    __tablename__ = "turn_citations"
    __table_args__ = (
        CheckConstraint("ordinal >= 1", name="ck_turn_citations_ordinal_positive"),
        CheckConstraint(
            "source_rank BETWEEN 1 AND 8", name="ck_turn_citations_source_rank"
        ),
        ForeignKeyConstraint(
            ["turn_id", "source_rank"],
            ["turn_sources.turn_id", "turn_sources.rank"],
            ondelete="CASCADE",
            name="fk_turn_citations_source",
        ),
        UniqueConstraint(
            "turn_id", "source_rank", name="uq_turn_citations_turn_source"
        ),
    )

    turn_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    ordinal: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    source_rank: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ObjectDeletion(TimestampMixin, Base):
    __tablename__ = "object_deletions"
    __table_args__ = (
        CheckConstraint(
            "status IN ('queued','leased','failed')",
            name="ck_object_deletions_status",
        ),
        CheckConstraint("attempt >= 0", name="ck_object_deletions_attempt_nonnegative"),
        CheckConstraint(
            "char_length(object_key) > 0",
            name="ck_object_deletions_object_key_nonempty",
        ),
        CheckConstraint(
            "last_error IS NULL OR char_length(last_error) <= 2000",
            name="ck_object_deletions_last_error_bounded",
        ),
        CheckConstraint(
            "(status = 'queued' AND lease_expires_at IS NULL "
            "AND lease_token IS NULL AND lease_owner IS NULL) OR "
            "(status = 'leased' AND lease_expires_at IS NOT NULL "
            "AND lease_token IS NOT NULL AND lease_owner IS NOT NULL) OR "
            "(status = 'failed' AND lease_expires_at IS NULL "
            "AND lease_token IS NULL AND lease_owner IS NULL)",
            name="ck_object_deletions_lease_consistency",
        ),
        CheckConstraint(
            "fencing_token >= 0", name="ck_object_deletions_fencing_nonnegative"
        ),
        Index("uq_object_deletions_object_key", "object_key", unique=True),
        Index(
            "ix_object_deletions_queue",
            "available_at",
            "created_at",
            postgresql_where=text("status = 'queued'"),
        ),
        Index(
            "ix_object_deletions_lease_expiry",
            "lease_expires_at",
            postgresql_where=text("status = 'leased'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    object_key: Mapped[str] = mapped_column(String(512), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), default="queued", server_default="queued", nullable=False
    )
    attempt: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_token: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    lease_owner: Mapped[str | None] = mapped_column(String(255))
    fencing_token: Mapped[int] = mapped_column(
        BigInteger, default=0, server_default="0", nullable=False
    )
    last_error: Mapped[str | None] = mapped_column(String(2000))


class UploadReservation(TimestampMixin, Base):
    __tablename__ = "upload_reservations"
    __table_args__ = (
        CheckConstraint(
            "sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_upload_reservations_sha256",
        ),
        CheckConstraint(
            "metadata_digest ~ '^[0-9a-f]{64}$'",
            name="ck_upload_reservations_metadata_digest",
        ),
        CheckConstraint(
            "expires_at > created_at",
            name="ck_upload_reservations_expiry",
        ),
        CheckConstraint(
            "(consumed_at IS NULL AND outcome IS NULL) OR "
            "(consumed_at IS NOT NULL AND outcome IS NOT NULL)",
            name="ck_upload_reservations_terminal_state",
        ),
        CheckConstraint(
            "outcome IS NULL OR outcome IN ('created','duplicate','expired')",
            name="ck_upload_reservations_outcome",
        ),
        Index(
            "uq_upload_reservations_active_object",
            "object_key",
            unique=True,
            postgresql_where=text("consumed_at IS NULL"),
        ),
        Index(
            "ix_upload_reservations_expiry",
            "expires_at",
            postgresql_where=text("consumed_at IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    actor_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    sha256: Mapped[str] = mapped_column(String(64, collation="C"), nullable=False)
    object_key: Mapped[str] = mapped_column(String(512), nullable=False)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("library_nodes.id", ondelete="RESTRICT"),
    )
    selected_team_ids: Mapped[list[uuid.UUID]] = mapped_column(
        ARRAY(UUID(as_uuid=True)), nullable=False
    )
    metadata_digest: Mapped[str] = mapped_column(
        String(64, collation="C"), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    outcome: Mapped[str | None] = mapped_column(String(16))


class IngestionJob(TimestampMixin, Base):
    __tablename__ = "ingestion_jobs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('queued','running','completed','failed','interrupted')",
            name="ck_ingestion_jobs_status",
        ),
        CheckConstraint("attempt >= 0", name="ck_ingestion_jobs_attempt_nonnegative"),
        CheckConstraint(
            "completed_units >= 0", name="ck_ingestion_jobs_completed_nonnegative"
        ),
        CheckConstraint(
            "total_units IS NULL OR total_units >= completed_units",
            name="ck_ingestion_jobs_total_units",
        ),
        CheckConstraint(
            "stage IN ('uploaded','parsing','chunking','embedding','indexing',"
            "'ready','failed')",
            name="ck_ingestion_jobs_stage",
        ),
        CheckConstraint(
            "(status = 'running' AND lease_expires_at IS NOT NULL "
            "AND lease_token IS NOT NULL AND lease_owner IS NOT NULL) OR "
            "(status <> 'running' AND lease_expires_at IS NULL "
            "AND lease_token IS NULL AND lease_owner IS NULL)",
            name="ck_ingestion_jobs_lease_consistency",
        ),
        CheckConstraint(
            "fencing_token >= 0", name="ck_ingestion_jobs_fencing_nonnegative"
        ),
        CheckConstraint(
            "(status = 'completed' AND stage = 'ready' AND error IS NULL) OR "
            "(status = 'failed' AND stage = 'failed' AND error IS NOT NULL) OR "
            "(status IN ('queued','running') "
            "AND stage <> 'ready' AND error IS NULL) OR "
            "(status = 'interrupted' AND stage <> 'ready')",
            name="ck_ingestion_jobs_state_consistency",
        ),
        Index(
            "ix_ingestion_jobs_queue",
            "available_at",
            "created_at",
            postgresql_where=text("status = 'queued'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        String(32), default=JobStatus.QUEUED.value, nullable=False
    )
    stage: Mapped[str] = mapped_column(String(32), default="uploaded", nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    completed_units: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_units: Mapped[int | None] = mapped_column(Integer)
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_token: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    lease_owner: Mapped[str | None] = mapped_column(String(255))
    fencing_token: Mapped[int] = mapped_column(
        BigInteger, default=0, server_default="0", nullable=False
    )
    error: Mapped[str | None] = mapped_column(Text)

    document: Mapped[Document] = relationship(back_populates="jobs")


class Chunk(TimestampMixin, Base):
    __tablename__ = "chunks"
    __table_args__ = (
        CheckConstraint("ordinal >= 0", name="ck_chunks_ordinal_nonnegative"),
        CheckConstraint("page_start >= 1", name="ck_chunks_page_start_positive"),
        CheckConstraint("page_end >= page_start", name="ck_chunks_page_range_ordered"),
        CheckConstraint("token_count > 0", name="ck_chunks_token_count_positive"),
        CheckConstraint(
            "parse_method IN ('direct','ocr')", name="ck_chunks_parse_method"
        ),
        CheckConstraint(
            "text_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_chunks_text_sha256",
        ),
        CheckConstraint(
            "source_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_chunks_source_sha256",
        ),
        CheckConstraint("char_length(text) > 0", name="ck_chunks_text_nonempty"),
        Index(
            "uq_chunks_generation_ordinal",
            "document_generation_id",
            "ordinal",
            unique=True,
        ),
        Index("ix_chunks_document_id", "document_id"),
        Index(
            "chunks_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
        Index(
            "ix_chunks_text_fts",
            text("to_tsvector('simple', text)"),
            postgresql_using="gin",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
    )
    document_generation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    filename: Mapped[str] = mapped_column(String(512), nullable=False)
    page_start: Mapped[int] = mapped_column(Integer, nullable=False)
    page_end: Mapped[int] = mapped_column(Integer, nullable=False)
    section: Mapped[str | None] = mapped_column(Text)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    token_count: Mapped[int] = mapped_column(Integer, nullable=False)
    text_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    source_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    parse_method: Mapped[str] = mapped_column(String(16), nullable=False)
    parser_version: Mapped[str] = mapped_column(String(64), nullable=False)
    chunking_version: Mapped[str] = mapped_column(String(64), nullable=False)
    embedding_version: Mapped[str] = mapped_column(String(128), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(64), nullable=False)
    citation_label: Mapped[str] = mapped_column(String(64), nullable=False)
    highlight_anchor: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    embedding: Mapped[list[float] | None] = mapped_column(VECTOR(1024))

    document: Mapped[Document] = relationship(back_populates="chunks")


class User(TimestampMixin, Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(
            "username ~ '^[a-z0-9][a-z0-9._-]{1,30}[a-z0-9]$'",
            name="ck_users_username_format",
        ),
        CheckConstraint(
            "char_length(display_name) BETWEEN 1 AND 80",
            name="ck_users_display_name_length",
        ),
        CheckConstraint(
            "display_name !~ '[[:cntrl:]]'",
            name="ck_users_display_name_no_controls",
        ),
        CheckConstraint("role IN ('admin','member')", name="ck_users_role"),
        CheckConstraint(
            "status IN ('pending_activation','active','disabled','deleted')",
            name="ck_users_status",
        ),
        CheckConstraint(
            "(status = 'active' AND password_hash IS NOT NULL) OR (status <> 'active')",
            name="ck_users_active_password",
        ),
        CheckConstraint(
            "(status = 'deleted' AND password_hash IS NULL "
            "AND deleted_at IS NOT NULL) OR "
            "(status <> 'deleted' AND deleted_at IS NULL)",
            name="ck_users_deleted_irreversible",
        ),
        CheckConstraint("authentication_version >= 1", name="ck_users_auth_version"),
        Index("uq_users_username", "username", unique=True),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    username: Mapped[str] = mapped_column(String(32, collation="C"), nullable=False)
    display_name: Mapped[str] = mapped_column(String(80), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    password_hash: Mapped[str | None] = mapped_column(Text)
    authentication_version: Mapped[int] = mapped_column(
        BigInteger, default=1, server_default="1", nullable=False
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Session(Base):
    __tablename__ = "sessions"
    __table_args__ = (
        CheckConstraint(
            "char_length(token_hash) = 64", name="ck_sessions_token_hash_length"
        ),
        CheckConstraint(
            "char_length(csrf_token_hash) = 64",
            name="ck_sessions_csrf_hash_length",
        ),
        CheckConstraint(
            "issued_at <= last_seen_at "
            "AND last_seen_at <= idle_expires_at "
            "AND idle_expires_at = absolute_expires_at",
            name="ck_sessions_expiry_order",
        ),
        CheckConstraint(
            "recent_reauthenticated_at IS NULL OR "
            "(recent_reauthenticated_at >= issued_at "
            "AND recent_reauthenticated_at <= last_seen_at)",
            name="ck_sessions_reauthentication_order",
        ),
        CheckConstraint(
            "issued_authentication_version >= 1 AND issued_authentication_epoch >= 1",
            name="ck_sessions_versions_positive",
        ),
        Index("uq_sessions_token_hash", "token_hash", unique=True),
        Index("ix_sessions_user_active", "user_id", "revoked_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    token_hash: Mapped[str] = mapped_column(String(64, collation="C"), nullable=False)
    csrf_token_hash: Mapped[str] = mapped_column(
        String(64, collation="C"), nullable=False
    )
    issued_authentication_version: Mapped[int] = mapped_column(
        BigInteger, nullable=False
    )
    issued_authentication_epoch: Mapped[int] = mapped_column(BigInteger, nullable=False)
    issued_session_epoch: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )
    issued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    idle_expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    absolute_expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    recent_reauthenticated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PreAuthChallenge(TimestampMixin, Base):
    __tablename__ = "pre_auth_challenges"
    __table_args__ = (
        CheckConstraint(
            "purpose IN ('activation','password_reset')",
            name="ck_pre_auth_challenges_purpose",
        ),
        CheckConstraint(
            "char_length(token_hash) = 64",
            name="ck_pre_auth_challenges_token_hash_length",
        ),
        CheckConstraint(
            "expires_at > created_at", name="ck_pre_auth_challenges_expiry"
        ),
        CheckConstraint(
            "expires_at <= created_at + interval '30 minutes'",
            name="ck_pre_auth_challenges_max_expiry",
        ),
        CheckConstraint(
            "consumed_at IS NULL OR revoked_at IS NULL",
            name="ck_pre_auth_challenges_terminal_state",
        ),
        CheckConstraint(
            "consumed_at IS NULL OR consumed_at >= created_at",
            name="ck_pre_auth_challenges_consumed_order",
        ),
        CheckConstraint(
            "revoked_at IS NULL OR revoked_at >= created_at",
            name="ck_pre_auth_challenges_revoked_order",
        ),
        Index("uq_pre_auth_challenges_token_hash", "token_hash", unique=True),
        Index(
            "uq_pre_auth_challenges_user_active",
            "user_id",
            unique=True,
            postgresql_where=text("consumed_at IS NULL AND revoked_at IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    purpose: Mapped[str] = mapped_column(String(24), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64, collation="C"), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class LoginThrottle(Base):
    __tablename__ = "login_throttles"
    __table_args__ = (
        CheckConstraint("failure_count >= 0", name="ck_login_throttles_failure_count"),
    )

    key_hash: Mapped[str] = mapped_column(String(64, collation="C"), primary_key=True)
    failure_count: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    first_failure_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    blocked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Team(TimestampMixin, Base):
    __tablename__ = "teams"
    __table_args__ = (
        CheckConstraint(
            "char_length(name) BETWEEN 1 AND 80", name="ck_teams_name_length"
        ),
        Index("uq_teams_name_key", "name_key", unique=True),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    name_key: Mapped[str] = mapped_column(String(160, collation="C"), nullable=False)
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true", nullable=False
    )


class TeamMember(Base):
    __tablename__ = "team_members"

    team_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("teams.id", ondelete="CASCADE"),
        primary_key=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class AccessGrant(TimestampMixin, Base):
    __tablename__ = "access_grants"
    __table_args__ = (
        CheckConstraint(
            "(user_id IS NOT NULL AND team_id IS NULL) OR "
            "(user_id IS NULL AND team_id IS NOT NULL)",
            name="ck_access_grants_one_principal",
        ),
        UniqueConstraint(
            "node_id",
            "user_id",
            "team_id",
            name="uq_access_grants_node_principal",
            postgresql_nulls_not_distinct=True,
        ),
        Index("ix_access_grants_user_id", "user_id"),
        Index("ix_access_grants_team_id", "team_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    node_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("library_nodes.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE")
    )
    team_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("teams.id", ondelete="CASCADE")
    )


class FolderCreateGrant(TimestampMixin, Base):
    __tablename__ = "folder_create_grants"
    __table_args__ = (
        CheckConstraint(
            "(user_id IS NOT NULL AND team_id IS NULL) OR "
            "(user_id IS NULL AND team_id IS NOT NULL)",
            name="ck_folder_create_grants_one_principal",
        ),
        UniqueConstraint(
            "folder_id",
            "user_id",
            "team_id",
            name="uq_folder_create_grants_folder_principal",
            postgresql_nulls_not_distinct=True,
        ),
        Index("ix_folder_create_grants_user_id", "user_id"),
        Index("ix_folder_create_grants_team_id", "team_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    folder_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("library_nodes.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE")
    )
    team_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("teams.id", ondelete="CASCADE")
    )


class EffectiveDocumentAccess(Base):
    __tablename__ = "effective_document_access"
    __table_args__ = (
        UniqueConstraint("user_id", "document_id", name="uq_effective_document_access"),
        Index("ix_effective_access_document_user", "document_id", "user_id"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="CASCADE"),
        primary_key=True,
    )
    authorization_version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class SecurityEpoch(Base):
    __tablename__ = "security_epochs"
    __table_args__ = (
        CheckConstraint("singleton = true", name="ck_security_epochs_singleton"),
        CheckConstraint(
            "authentication_version >= 1 AND authorization_version >= 1",
            name="ck_security_epochs_versions",
        ),
    )

    singleton: Mapped[bool] = mapped_column(
        Boolean, primary_key=True, default=True, server_default="true"
    )
    authentication_version: Mapped[int] = mapped_column(
        BigInteger, default=1, server_default="1", nullable=False
    )
    authorization_version: Mapped[int] = mapped_column(
        BigInteger, default=1, server_default="1", nullable=False
    )
    session_epoch: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), default=uuid.uuid4, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class AclPreview(TimestampMixin, Base):
    __tablename__ = "acl_previews"
    __table_args__ = (
        CheckConstraint(
            "char_length(impact_digest) = 64", name="ck_acl_previews_digest_length"
        ),
        CheckConstraint("expires_at > created_at", name="ck_acl_previews_expiry"),
        Index("ix_acl_previews_actor_active", "actor_user_id", "expires_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    actor_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    operation: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    impact_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    authorization_version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuditEvent(Base):
    __tablename__ = "audit_events"
    __table_args__ = (
        CheckConstraint(
            "char_length(event_type) BETWEEN 1 AND 80",
            name="ck_audit_events_type_length",
        ),
        Index("ix_audit_events_created_id", "created_at", "id"),
        Index("ix_audit_events_actor_id", "actor_user_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    event_type: Mapped[str] = mapped_column(String(80), nullable=False)
    target_type: Mapped[str | None] = mapped_column(String(80))
    target_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    details: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    correlation_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ServiceLease(TimestampMixin, Base):
    __tablename__ = "service_leases"
    __table_args__ = (
        CheckConstraint(
            "service_name IN "
            "('ingestion_worker','deletion_worker','inference_coordinator',"
            "'ocr_service')",
            name="ck_service_leases_name",
        ),
        CheckConstraint(
            "char_length(owner_id) BETWEEN 1 AND 255",
            name="ck_service_leases_owner",
        ),
        CheckConstraint(
            "fencing_token >= 1", name="ck_service_leases_fencing_positive"
        ),
        CheckConstraint(
            "heartbeat_at <= lease_expires_at",
            name="ck_service_leases_expiry_order",
        ),
        Index("ix_service_leases_expiry", "lease_expires_at"),
    )

    service_name: Mapped[str] = mapped_column(String(32), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(255), nullable=False)
    lease_token: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    fencing_token: Mapped[int] = mapped_column(BigInteger, nullable=False)
    heartbeat_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    lease_expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class BackupRun(Base):
    __tablename__ = "backup_runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('running','succeeded','failed')",
            name="ck_backup_runs_status",
        ),
        CheckConstraint(
            "finished_at IS NULL OR finished_at >= started_at",
            name="ck_backup_runs_finished_order",
        ),
        CheckConstraint(
            "(status = 'running' AND finished_at IS NULL) OR "
            "(status IN ('succeeded','failed') AND finished_at IS NOT NULL)",
            name="ck_backup_runs_status_consistency",
        ),
        CheckConstraint(
            "database_bytes IS NULL OR database_bytes >= 0",
            name="ck_backup_runs_database_bytes",
        ),
        CheckConstraint(
            "storage_bytes IS NULL OR storage_bytes >= 0",
            name="ck_backup_runs_storage_bytes",
        ),
        CheckConstraint(
            "char_length(destination_id) > 0",
            name="ck_backup_runs_destination_nonempty",
        ),
        CheckConstraint(
            "database_sha256 IS NULL OR database_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_backup_runs_database_sha256",
        ),
        CheckConstraint(
            "storage_manifest_sha256 IS NULL OR "
            "storage_manifest_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_backup_runs_storage_manifest_sha256",
        ),
        CheckConstraint(
            "(status = 'succeeded' AND database_sha256 IS NOT NULL "
            "AND storage_manifest_sha256 IS NOT NULL "
            "AND database_bytes IS NOT NULL AND storage_bytes IS NOT NULL "
            "AND error_code IS NULL) OR "
            "(status = 'failed' AND error_code IS NOT NULL) OR "
            "(status = 'running' AND database_sha256 IS NULL "
            "AND storage_manifest_sha256 IS NULL "
            "AND database_bytes IS NULL AND storage_bytes IS NULL "
            "AND error_code IS NULL)",
            name="ck_backup_runs_result_consistency",
        ),
        Index("ix_backup_runs_started_id", text("started_at DESC"), text("id DESC")),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    destination_id: Mapped[str] = mapped_column(String(255), nullable=False)
    database_sha256: Mapped[str | None] = mapped_column(String(64))
    storage_manifest_sha256: Mapped[str | None] = mapped_column(String(64))
    database_bytes: Mapped[int | None] = mapped_column(BigInteger)
    storage_bytes: Mapped[int | None] = mapped_column(BigInteger)
    error_code: Mapped[str | None] = mapped_column(String(80))
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
