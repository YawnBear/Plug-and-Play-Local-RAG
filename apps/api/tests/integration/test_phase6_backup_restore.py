import asyncio
import hashlib
import os
import re
import subprocess
import time
from collections.abc import Coroutine
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.engine import make_url

from app.config import Settings
from app.db.session import DatabaseManager
from app.main import create_app
from app.services.object_lifecycle import canonical_object_key
from app.services.object_storage import ObjectStoreError, S3ObjectStore
from app.services.storage_maintenance import StorageMaintenanceService
from app.services.storage_transfer import StorageTransferService
from tests.integration.test_phase6_end_to_end import (
    _assert_dedicated_object_store,
)
from tests.integration.test_phase6_restart_persistence import (
    _digital_pdf,
    _events,
    _wait_for_job,
)

pytestmark = pytest.mark.integration
_CONFIRMATION = "PHASE6-DEDICATED-ONLY"


def _environment() -> dict[str, str]:
    names = (
        "TEST_DATABASE_URL",
        "PHASE6_TEST_DATABASE_NAME",
        "PHASE6_COMPOSE_PROJECT",
        "PHASE6_TEST_DEPLOYMENT_ID",
        "PHASE6_TEST_OBJECT_STORAGE_ENDPOINT_URL",
        "PHASE6_TEST_OBJECT_STORAGE_BUCKET",
        "PHASE6_RESTORE_DATABASE_URL",
        "PHASE6_RESTORE_DATABASE_NAME",
        "PHASE6_RESTORE_COMPOSE_PROJECT",
        "PHASE6_RESTORE_OBJECT_STORAGE_ENDPOINT_URL",
        "PHASE6_RESTORE_OBJECT_STORAGE_ACCESS_KEY_ID",
        "PHASE6_RESTORE_OBJECT_STORAGE_SECRET_ACCESS_KEY",
        "OCR_PYTHON_EXECUTABLE",
        "PHASE6_DEDICATED_DATABASE_CONFIRM",
    )
    values = {name: os.environ.get(name, "") for name in names}
    missing = [name for name, value in values.items() if not value]
    if missing:
        pytest.skip(f"Phase 6 restore environment is missing: {', '.join(missing)}")
    if values["PHASE6_DEDICATED_DATABASE_CONFIRM"] != _CONFIRMATION:
        pytest.fail(f"restore gate requires confirmation {_CONFIRMATION!r}")
    if (
        re.fullmatch(
            r"rag-phase6-test-[A-Za-z0-9_-]+", values["PHASE6_COMPOSE_PROJECT"]
        )
        is None
    ):
        pytest.fail("restore gate refuses an unsafe source project")
    if (
        re.fullmatch(
            r"rag-restore-drill-[A-Za-z0-9_-]+",
            values["PHASE6_RESTORE_COMPOSE_PROJECT"],
        )
        is None
    ):
        pytest.fail("restore gate refuses an unsafe destination project")
    source = make_url(values["TEST_DATABASE_URL"])
    restore = make_url(values["PHASE6_RESTORE_DATABASE_URL"])
    primary = make_url(Settings().database_url)
    identities = {
        (url.host, url.port, url.database) for url in (source, restore, primary)
    }
    if len(identities) != 3:
        pytest.fail("restore gate requires source, restore, and primary isolation")
    if source.database != values["PHASE6_TEST_DATABASE_NAME"]:
        pytest.fail("source database name does not match its explicit guard")
    if restore.database != values["PHASE6_RESTORE_DATABASE_NAME"]:
        pytest.fail("restore database name does not match its explicit guard")
    source_settings = Settings()
    if source_settings.deployment_id != values["PHASE6_TEST_DEPLOYMENT_ID"]:
        pytest.fail("source deployment identity does not match Settings")
    if (
        str(source_settings.object_storage_endpoint_url).rstrip("/")
        != values["PHASE6_TEST_OBJECT_STORAGE_ENDPOINT_URL"].rstrip("/")
        or source_settings.object_storage_bucket
        != values["PHASE6_TEST_OBJECT_STORAGE_BUCKET"]
    ):
        pytest.fail("source object-storage identity does not match Settings")
    _assert_dedicated_object_store(values["PHASE6_COMPOSE_PROJECT"], source_settings)
    return values


def _compose_environment(
    database_url: str,
    settings: Settings,
    *,
    postgres_port: int,
    rustfs_api_port: int,
    rustfs_console_port: int,
) -> dict[str, str]:
    url = make_url(database_url)
    access_key = settings.object_storage_access_key_id.get_secret_value()
    secret_key = settings.object_storage_secret_access_key.get_secret_value()
    assert url.username and url.password and url.database
    assert access_key and secret_key
    return {
        **os.environ,
        "POSTGRES_DB": url.database,
        "POSTGRES_USER": url.username,
        "POSTGRES_PASSWORD": url.password,
        "POSTGRES_PORT": str(postgres_port),
        "RUSTFS_API_PORT": str(rustfs_api_port),
        "RUSTFS_CONSOLE_PORT": str(rustfs_console_port),
        "RUSTFS_ACCESS_KEY": access_key,
        "RUSTFS_SECRET_KEY": secret_key,
    }


def _run_compose(
    repository: Path,
    project: str,
    environment: dict[str, str],
    *arguments: str,
) -> None:
    result = subprocess.run(
        ["docker", "compose", "-p", project, *arguments],
        cwd=repository,
        env=environment,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def _run[Result](coroutine: Coroutine[object, object, Result]) -> Result:
    return asyncio.run(
        coroutine,
        loop_factory=(asyncio.SelectorEventLoop if os.name == "nt" else None),
    )


async def _export(settings: Settings, backup: Path, database_dump: Path) -> Path:
    database = DatabaseManager.from_settings(settings)
    try:
        service = StorageTransferService(
            database.session_factory,
            S3ObjectStore.from_settings(settings),
            settings,
        )
        return await service.export(backup, database_dump)
    finally:
        await database.dispose()


async def _bootstrap_and_import(settings: Settings, backup: Path) -> int:
    store = S3ObjectStore.from_settings(settings)
    await StorageMaintenanceService(store).bootstrap_bucket()
    database = DatabaseManager.from_settings(settings)
    try:
        service = StorageTransferService(
            database.session_factory,
            store,
            settings,
        )
        return await service.import_archive(backup)
    finally:
        await database.dispose()


def test_paired_database_and_bucket_restore_reproduces_application_state(
    tmp_path: Path,
) -> None:
    if os.environ.get("RUN_PHASE6_BACKUP_RESTORE_E2E") != "1":
        pytest.skip("RUN_PHASE6_BACKUP_RESTORE_E2E is not enabled")
    environment = _environment()
    repository = Path(__file__).resolve().parents[4]
    source_url = make_url(environment["TEST_DATABASE_URL"])
    restore_url = make_url(environment["PHASE6_RESTORE_DATABASE_URL"])
    source_settings = Settings(
        database_url=environment["TEST_DATABASE_URL"],
        data_root=tmp_path / "source-data",
        ocr_python_executable=Path(environment["OCR_PYTHON_EXECUTABLE"]),
        worker_poll_seconds=0.05,
    )
    restore_settings = Settings(
        database_url=environment["PHASE6_RESTORE_DATABASE_URL"],
        data_root=tmp_path / "restore-data",
        ocr_python_executable=Path(environment["OCR_PYTHON_EXECUTABLE"]),
        object_storage_endpoint_url=(
            environment["PHASE6_RESTORE_OBJECT_STORAGE_ENDPOINT_URL"]
        ),
        object_storage_access_key_id=(
            environment["PHASE6_RESTORE_OBJECT_STORAGE_ACCESS_KEY_ID"]
        ),
        object_storage_secret_access_key=(
            environment["PHASE6_RESTORE_OBJECT_STORAGE_SECRET_ACCESS_KEY"]
        ),
        worker_poll_seconds=0.05,
    )
    if str(source_settings.object_storage_endpoint_url).rstrip("/") == str(
        restore_settings.object_storage_endpoint_url
    ).rstrip("/"):
        pytest.fail("source and restore object-storage endpoints must differ")
    _assert_dedicated_object_store(
        environment["PHASE6_RESTORE_COMPOSE_PROJECT"], restore_settings
    )
    source_compose = _compose_environment(
        environment["TEST_DATABASE_URL"],
        source_settings,
        postgres_port=source_url.port or 5432,
        rustfs_api_port=59000,
        rustfs_console_port=59001,
    )
    restore_compose = _compose_environment(
        environment["PHASE6_RESTORE_DATABASE_URL"],
        restore_settings,
        postgres_port=restore_url.port or 5432,
        rustfs_api_port=59002,
        rustfs_console_port=59003,
    )
    pdf = _digital_pdf()
    object_key = canonical_object_key(hashlib.sha256(pdf).hexdigest())
    accepted: dict[str, str] | None = None
    folder_id: str | None = None
    chat_id: str | None = None
    try:
        with TestClient(create_app(source_settings)) as client:
            upload = client.post(
                "/api/documents",
                files={"file": ("restore-source.pdf", pdf, "application/pdf")},
            )
            assert upload.status_code == 202, upload.text
            accepted = upload.json()
            assert _wait_for_job(client, accepted["job_id"])["status"] == "completed"
            folder = client.post(
                "/api/library/folders",
                json={"name": "Recovery Archive", "parent_id": None},
            )
            assert folder.status_code == 201, folder.text
            folder_id = folder.json()["node_id"]
            moved = client.patch(
                f"/api/library/nodes/{accepted['node_id']}",
                json={"name": "durable-4815.pdf", "parent_id": folder_id},
            )
            assert moved.status_code == 200, moved.text
            chat = client.post("/api/chats", json={})
            assert chat.status_code == 201, chat.text
            chat_id = chat.json()["chat_id"]
            scope = client.put(
                f"/api/chats/{chat_id}/scope",
                json={"mode": "selected", "node_ids": [accepted["node_id"]]},
            )
            assert scope.status_code == 200, scope.text
            answer = client.post(
                f"/api/chats/{chat_id}/messages/stream",
                headers={"Accept": "text/event-stream"},
                json={"question": "What is the amber recovery code?"},
            )
            assert answer.status_code == 200, answer.text
            final = _events(answer.text)[-1]
            assert final[0] == "final"
            assert "4815" in str(final[1]["answer"])
            assert final[1]["citations"][0]["page_start"] == 1

        dump = tmp_path / "source.database.dump"
        _run_compose(
            repository,
            environment["PHASE6_COMPOSE_PROJECT"],
            source_compose,
            "exec",
            "-T",
            "postgres",
            "pg_dump",
            "-U",
            source_url.username or "",
            "-d",
            source_url.database or "",
            "--format=custom",
            "--file=/tmp/phase6-source.dump",
        )
        _run_compose(
            repository,
            environment["PHASE6_COMPOSE_PROJECT"],
            source_compose,
            "cp",
            "postgres:/tmp/phase6-source.dump",
            str(dump),
        )
        backup = tmp_path / "paired-backup"
        manifest = _run(_export(source_settings, backup, dump))
        assert manifest == backup / "manifest.json"

        restore_dump = "/tmp/phase6-restore.dump"
        _run_compose(
            repository,
            environment["PHASE6_RESTORE_COMPOSE_PROJECT"],
            restore_compose,
            "cp",
            str(backup / "database.dump"),
            f"postgres:{restore_dump}",
        )
        _run_compose(
            repository,
            environment["PHASE6_RESTORE_COMPOSE_PROJECT"],
            restore_compose,
            "exec",
            "-T",
            "postgres",
            "pg_restore",
            "-U",
            restore_url.username or "",
            "-d",
            restore_url.database or "",
            "--clean",
            "--if-exists",
            "--no-owner",
            restore_dump,
        )
        assert _run(_bootstrap_and_import(restore_settings, backup)) == 1

        with TestClient(create_app(restore_settings)) as client:
            document = next(
                item
                for item in client.get("/api/documents").json()
                if item["document_id"] == accepted["document_id"]
            )
            assert document["logical_path"] == ("/Recovery Archive/durable-4815.pdf")
            assert document["state"] == "ready"
            assert (
                client.get(f"/api/jobs/{accepted['job_id']}").json()["status"]
                == "completed"
            )
            browse = client.get(
                "/api/library/browse", params={"parent_id": folder_id}
            ).json()
            assert browse["children"][0]["document_id"] == accepted["document_id"]
            detail = client.get(f"/api/chats/{chat_id}").json()
            assert detail["turns"][0]["citation_ranks"] == [1]
            assert detail["turns"][0]["sources"][0]["page_start"] == 1
            preview = client.get(
                f"/api/documents/{accepted['document_id']}/content",
                headers={"Range": "bytes=16-47"},
            )
            assert preview.status_code == 206
            assert preview.content == pdf[16:48]
            repeated = client.post(
                f"/api/chats/{chat_id}/messages/stream",
                headers={"Accept": "text/event-stream"},
                json={"question": "Repeat the amber recovery code."},
            )
            assert repeated.status_code == 200, repeated.text
            repeated_final = _events(repeated.text)[-1]
            assert repeated_final[0] == "final"
            assert "4815" in str(repeated_final[1]["answer"])
            assert repeated_final[1]["citations"][0]["page_start"] == 1
    finally:
        if accepted is not None or chat_id is not None or folder_id is not None:
            cleanup_app = create_app(source_settings)
            with TestClient(cleanup_app) as client:
                if accepted is not None:
                    client.delete(f"/api/documents/{accepted['document_id']}")
                if chat_id is not None:
                    client.delete(f"/api/chats/{chat_id}")
                if folder_id is not None:
                    client.delete(f"/api/library/folders/{folder_id}")
                deadline = time.monotonic() + 15
                while accepted is not None and time.monotonic() < deadline:
                    try:
                        assert client.portal is not None
                        client.portal.call(
                            cleanup_app.state.container.object_store.head,
                            object_key,
                        )
                    except ObjectStoreError as exc:
                        if exc.not_found:
                            break
                        raise
                    time.sleep(0.1)
                else:
                    if accepted is not None:
                        pytest.fail("source backup object cleanup did not complete")
