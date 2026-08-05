import argparse
import asyncio
import json
import sys
import uuid
from collections.abc import Coroutine, Sequence
from pathlib import Path

from sqlalchemy import text

from app.config import get_maintenance_settings
from app.db.session import EXPECTED_ALEMBIC_REVISION, DatabaseManager
from app.security.bootstrap import bootstrap_first_admin, issue_owner_setup_code
from app.services.maintenance import MaintenanceError, MaintenanceService
from app.services.object_storage import ObjectStoreError, S3ObjectStore
from app.services.storage_maintenance import StorageMaintenanceService
from app.services.storage_transfer import StorageTransferError, StorageTransferService


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Document and object-storage maintenance"
    )
    parser.add_argument(
        "--confirm-stopped",
        action="store_true",
        help="confirm the API and ingestion worker are stopped",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser(
        "bootstrap-admin",
        help="interactively create the first administrator on a fresh database",
    )
    commands.add_parser(
        "setup-code-issue",
        help="rotate and display a short-lived unused Personal setup code",
    )
    retry = commands.add_parser("retry", help="retry one failed/interrupted document")
    retry.add_argument("document_id", type=uuid.UUID)
    rebuild = commands.add_parser("rebuild", help="rebuild one retained document")
    rebuild.add_argument("document_id", type=uuid.UUID)
    rebuild_all = commands.add_parser("rebuild-all", help="rebuild every document")
    rebuild_all.add_argument("--confirm", required=True)
    commands.add_parser(
        "repair-interrupted-turns",
        help="mark turns left generating by a stopped API as interrupted",
    )
    commands.add_parser(
        "storage-bootstrap",
        help="explicitly create or verify the configured private object bucket",
    )
    commands.add_parser(
        "version-inventory",
        help="report aggregate document/chunk versions without document content",
    )
    audit = commands.add_parser(
        "storage-audit",
        help="read and validate the complete configured object inventory",
    )
    audit.add_argument("--prefix", default="")
    export = commands.add_parser(
        "storage-export", help="export verified objects and a strict manifest"
    )
    export.add_argument("destination", type=Path)
    export.add_argument("--database-dump", required=True, type=Path)
    import_command = commands.add_parser(
        "storage-import", help="import a verified manifest into an empty bucket"
    )
    import_command.add_argument("source", type=Path)
    return parser


async def _execute(arguments: argparse.Namespace) -> int:
    if (
        arguments.command
        in {"retry", "rebuild", "rebuild-all", "repair-interrupted-turns"}
        and not arguments.confirm_stopped
    ):
        raise MaintenanceError(
            "maintenance requires --confirm-stopped after stopping the API/worker"
        )
    settings = get_maintenance_settings()
    if arguments.command == "bootstrap-admin":
        if not arguments.confirm_stopped:
            raise MaintenanceError(
                "bootstrap-admin requires --confirm-stopped after stopping services"
            )
        database = DatabaseManager.from_maintenance_settings(settings)
        try:
            await _require_maintenance_schema(database)
            admin_id = await bootstrap_first_admin(database.session_factory)
        except RuntimeError as exc:
            raise MaintenanceError(str(exc)) from exc
        finally:
            await database.dispose()
        print(f"created first administrator {admin_id}")
        return 0
    if arguments.command == "setup-code-issue":
        if not arguments.confirm_stopped:
            raise MaintenanceError(
                "setup-code-issue requires --confirm-stopped after stopping services"
            )
        database = DatabaseManager.from_maintenance_settings(settings)
        try:
            await _require_maintenance_schema(database)
            issued = await issue_owner_setup_code(database.session_factory)
        except RuntimeError as exc:
            raise MaintenanceError(str(exc)) from exc
        finally:
            await database.dispose()
        print("One-time Personal setup code (expires in 15 minutes):")
        print(issued.code)
        return 0
    if arguments.command == "storage-bootstrap":
        service = StorageMaintenanceService(S3ObjectStore.from_settings(settings))
        result = await service.bootstrap_bucket()
        print(result.detail)
        return 0
    if arguments.command == "version-inventory":
        database = DatabaseManager.from_maintenance_settings(settings)
        try:
            await _require_maintenance_schema(database)
            inventory = await _version_inventory(database)
        finally:
            await database.dispose()
        print(json.dumps(inventory, indent=2, sort_keys=True))
        return 0
    if arguments.command == "storage-audit" and arguments.prefix:
        service = StorageMaintenanceService(S3ObjectStore.from_settings(settings))
        result = await service.audit_bucket(arguments.prefix)
        print(
            f"audited {result.object_count} object(s), {result.byte_count} byte(s); "
            f"missing checksum metadata={len(result.missing_checksum_metadata)}; "
            f"invalid checksum metadata={len(result.invalid_checksum_metadata)}; "
            f"invalid size metadata={len(result.invalid_size_metadata)}"
        )
        return (
            0
            if not (
                result.missing_checksum_metadata
                or result.invalid_checksum_metadata
                or result.invalid_size_metadata
            )
            else 1
        )
    if (
        arguments.command
        in {
            "storage-export",
            "storage-import",
        }
        and not arguments.confirm_stopped
    ):
        raise MaintenanceError(
            "maintenance requires --confirm-stopped after stopping all mutation workers"
        )
    if arguments.command in {
        "storage-audit",
        "storage-export",
        "storage-import",
    }:
        database = DatabaseManager.from_maintenance_settings(settings)
        try:
            await _require_maintenance_schema(database)
            transfer = StorageTransferService(
                database.session_factory,
                S3ObjectStore.from_settings(settings),
                settings,
            )
            if arguments.command == "storage-audit":
                result = await transfer.audit()
                print(
                    f"audited documents={result.documents}, objects={result.objects}; "
                    f"missing={len(result.missing_keys)}; "
                    f"mismatched={len(result.mismatched_keys)}; "
                    f"orphans={len(result.orphan_keys)}; "
                    f"pending deletion={len(result.pending_deletion_keys)}"
                )
                return 0 if result.clean else 1
            if arguments.command == "storage-export":
                manifest = await transfer.export(
                    arguments.destination, arguments.database_dump
                )
                print(f"storage export completed: {manifest}")
                return 0
            imported = await transfer.import_archive(arguments.source)
            print(f"storage import completed: {imported} object(s)")
            return 0
        finally:
            await database.dispose()
    if not arguments.confirm_stopped:
        raise MaintenanceError(
            "maintenance requires --confirm-stopped after stopping the API/worker"
        )
    database = DatabaseManager.from_maintenance_settings(settings)
    try:
        readiness = await database.readiness()
        if not readiness.ready:
            raise MaintenanceError(readiness.message)
        service = MaintenanceService(
            database.session_factory,
            settings,
            S3ObjectStore.from_settings(settings),
        )
        if arguments.command == "retry":
            results = [await service.retry(arguments.document_id)]
        elif arguments.command == "rebuild":
            results = [await service.rebuild(arguments.document_id)]
        elif arguments.command == "rebuild-all":
            results = await service.rebuild_all(arguments.confirm)
        elif arguments.command == "repair-interrupted-turns":
            repaired = await service.repair_interrupted_turns()
            print(f"repaired {repaired} interrupted turn(s)")
            return 0
        else:
            raise MaintenanceError(
                f"unsupported maintenance command: {arguments.command}"
            )
    finally:
        await database.dispose()
    if not results:
        print("No documents found; nothing was requeued.")
        return 0
    for result in results:
        print(
            f"requeued document {result.document_id} as job {result.job_id}; "
            "start the API to resume ingestion"
        )
    return 0


def _run(coroutine: Coroutine[object, object, int]) -> int:
    loop_factory = asyncio.SelectorEventLoop if sys.platform == "win32" else None
    return asyncio.run(coroutine, loop_factory=loop_factory)


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        return _run(_execute(arguments))
    except (MaintenanceError, ObjectStoreError, StorageTransferError) as exc:
        print(f"maintenance refused: {exc}", file=sys.stderr)
        return 2


async def _require_maintenance_schema(database: DatabaseManager) -> None:
    async with database.engine.connect() as connection:
        identity_valid = await connection.scalar(
            text("SELECT v4_runtime_identity('rag_maintenance')")
        )
        revision = await connection.scalar(text("SELECT v4_schema_revision()"))
    if not identity_valid:
        raise MaintenanceError(
            "database identity validation failed; use the dedicated "
            "rag_maintenance database credential"
        )
    if revision != EXPECTED_ALEMBIC_REVISION:
        raise MaintenanceError(
            "database migration is not current; run alembic upgrade head "
            f"(expected {EXPECTED_ALEMBIC_REVISION}, found {revision})"
        )


async def _version_inventory(database: DatabaseManager) -> dict[str, object]:
    async with database.engine.connect() as connection:
        document_rows = (
            await connection.execute(
                text(
                    "SELECT state, parser_version, chunking_version, "
                    "embedding_version, count(*) AS count "
                    "FROM documents "
                    "GROUP BY state, parser_version, chunking_version, "
                    "embedding_version "
                    "ORDER BY state, parser_version, chunking_version, "
                    "embedding_version"
                )
            )
        ).mappings()
        chunk_rows = (
            await connection.execute(
                text(
                    "SELECT parser_version, chunking_version, "
                    "embedding_version, schema_version, count(*) AS count "
                    "FROM chunks "
                    "GROUP BY parser_version, chunking_version, "
                    "embedding_version, schema_version "
                    "ORDER BY parser_version, chunking_version, "
                    "embedding_version, schema_version"
                )
            )
        ).mappings()
        turn_source_count = int(
            await connection.scalar(text("SELECT count(*) FROM turn_sources")) or 0
        )
    return {
        "schema_version": 1,
        "documents": [dict(row) for row in document_rows],
        "chunks": [dict(row) for row in chunk_rows],
        "immutable_turn_source_snapshots": turn_source_count,
        "contains_document_content": False,
    }


if __name__ == "__main__":
    raise SystemExit(main())
