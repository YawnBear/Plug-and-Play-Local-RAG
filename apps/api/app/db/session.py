import asyncio
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import MaintenanceSettings, Settings

EXPECTED_ALEMBIC_REVISION = "0014_restart_without_backup"
DATABASE_READINESS_TIMEOUT_SECONDS = 5.0
RUNTIME_DATABASE_ROLES = frozenset({"rag_api", "rag_worker", "rag_maintenance"})


@dataclass(frozen=True, slots=True)
class DatabaseReadiness:
    database: bool
    vector_extension: bool
    migration_current: bool
    message: str
    bootstrap_required: bool = False
    catalog_integrity: bool = True
    identity_valid: bool = True

    @property
    def ready(self) -> bool:
        return (
            self.database
            and self.vector_extension
            and self.migration_current
            and not self.bootstrap_required
            and self.catalog_integrity
            and self.identity_valid
        )


class DatabaseManager:
    def __init__(
        self,
        engine: AsyncEngine,
        *,
        readiness_timeout_seconds: float = DATABASE_READINESS_TIMEOUT_SECONDS,
        expected_role: str = "rag_api",
    ) -> None:
        if expected_role not in RUNTIME_DATABASE_ROLES:
            raise ValueError("expected_role must be a fixed V4 runtime database role")
        self.engine = engine
        self.readiness_timeout_seconds = readiness_timeout_seconds
        self.expected_role = expected_role
        self.session_factory = async_sessionmaker(
            engine, class_=AsyncSession, expire_on_commit=False
        )

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        *,
        expected_role: str = "rag_api",
    ) -> "DatabaseManager":
        engine = create_async_engine(
            settings.database_url,
            pool_size=settings.database_pool_size,
            max_overflow=settings.database_max_overflow,
            pool_pre_ping=True,
            pool_timeout=settings.database_pool_timeout_seconds,
        )
        return cls(engine, expected_role=expected_role)

    @classmethod
    def from_maintenance_settings(
        cls, settings: MaintenanceSettings
    ) -> "DatabaseManager":
        engine = create_async_engine(
            settings.maintenance_database_url,
            pool_size=settings.database_pool_size,
            max_overflow=settings.database_max_overflow,
            pool_pre_ping=True,
            pool_timeout=settings.database_pool_timeout_seconds,
        )
        return cls(engine, expected_role="rag_maintenance")

    async def readiness(self) -> DatabaseReadiness:
        return await self._readiness()

    async def startup_readiness(self) -> DatabaseReadiness:
        return await self._readiness()

    async def _readiness(self) -> DatabaseReadiness:
        try:
            (
                revision,
                extension,
                bootstrap_required,
                catalog_integrity,
                identity_valid,
            ) = await asyncio.wait_for(
                self._readiness_query(), timeout=self.readiness_timeout_seconds
            )
        except TimeoutError:
            return DatabaseReadiness(
                False,
                False,
                False,
                "database readiness timed out; verify PostgreSQL and the configured "
                "DATABASE_URL",
            )
        except Exception as exc:
            detail = str(exc)
            if len(detail) > 500:
                detail = f"{detail[:497]}..."
            return DatabaseReadiness(
                False, False, False, f"database not ready: {detail}"
            )
        extension_ready = bool(extension)
        schema_current = revision == EXPECTED_ALEMBIC_REVISION
        migration_ready = schema_current and bool(catalog_integrity)
        if not identity_valid:
            message = (
                "database identity validation failed; use the dedicated "
                f"{self.expected_role} database credential"
            )
        elif not extension_ready:
            message = "pgvector extension is missing; run alembic upgrade head"
        elif not schema_current:
            message = (
                "database migration is not current; run alembic upgrade head "
                f"(expected {EXPECTED_ALEMBIC_REVISION}, found {revision})"
            )
        elif not catalog_integrity:
            message = (
                "database catalog integrity check failed; stop the API and restore "
                "or recreate the fresh V4 database"
            )
        elif bootstrap_required:
            message = "first administrator bootstrap is required"
        else:
            message = "ready"
        return DatabaseReadiness(
            True,
            extension_ready,
            migration_ready,
            message,
            bool(bootstrap_required),
            bool(catalog_integrity),
            bool(identity_valid),
        )

    async def _readiness_query(
        self,
    ) -> tuple[object, object, object, object, object]:
        async with self.engine.connect() as connection:
            row = (
                await connection.execute(
                    text(
                        "SELECT schema_revision, vector_extension, "
                        "bootstrap_required, catalog_integrity "
                        ", v4_runtime_identity(:expected_role) AS identity_valid "
                        "FROM v5_readiness()"
                    ),
                    {"expected_role": self.expected_role},
                )
            ).one()
        return (
            row.schema_revision,
            row.vector_extension,
            row.bootstrap_required,
            row.catalog_integrity,
            row.identity_valid,
        )

    async def dispose(self) -> None:
        await self.engine.dispose()
