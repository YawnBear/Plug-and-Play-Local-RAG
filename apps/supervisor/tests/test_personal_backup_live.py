from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from uuid import uuid4

import pytest
from apps.supervisor.controller import (
    ConfigurationController,
    ControllerStage,
    ResolvedProfile,
    RuntimeConfiguration,
    SignedProfileResolver,
)
from apps.supervisor.personal_backup import PersonalBackupService

ROOT = Path(__file__).resolve().parents[3]
COMPOSE = ROOT / "ops" / "windows" / "v8a" / "compose.restore-verifier.yaml"
PROVISION = ROOT / "ops" / "windows" / "v8a" / "Initialize-RagPersonalPostgres.ps1"


def _run(
    command: list[str],
    *,
    environment: dict[str, str] | None = None,
    cwd: Path = ROOT,
) -> str:
    result = subprocess.run(
        command,
        cwd=cwd,
        env=environment,
        capture_output=True,
        text=True,
        timeout=1800,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


def _compose(project: str, environment: Path, *arguments: str) -> str:
    return _run(
        [
            "docker.exe",
            "compose",
            "-p",
            project,
            "--env-file",
            str(environment),
            "-f",
            str(COMPOSE),
            *arguments,
        ]
    )


def _port(project: str, environment: Path, service: str, target: str) -> int:
    value = _compose(project, environment, "port", service, target).strip()
    match = re.fullmatch(r"127\.0\.0\.1:(\d+)", value)
    assert match is not None, value
    return int(match.group(1))


def _psql(project: str, environment: Path, sql: str) -> str:
    return _compose(
        project,
        environment,
        "exec",
        "-T",
        "postgres",
        "psql",
        "-X",
        "-A",
        "-t",
        "-v",
        "ON_ERROR_STOP=1",
        "-U",
        "rag_cluster_admin",
        "-d",
        "rag",
        "-c",
        sql,
    ).strip()


def _exercise_live_apply_and_rollback(project: str, environment: Path) -> None:
    user_id, session_id, backup_id = uuid4(), uuid4(), uuid4()
    preview_one, preview_two = uuid4(), uuid4()
    change_one, change_two = uuid4(), uuid4()
    nonce_hash = "a" * 64
    impact = "b" * 64
    _psql(
        project,
        environment,
        f"""
        INSERT INTO users (id,username,display_name,role,status,password_hash)
        VALUES ('{user_id}','liveadmin','Live Admin','admin','active','fixture');
        INSERT INTO sessions (
            id,user_id,token_hash,csrf_token_hash,issued_authentication_version,
            issued_authentication_epoch,issued_session_epoch,idle_expires_at,
            absolute_expires_at
        ) VALUES (
            '{session_id}','{user_id}',repeat('c',64),repeat('d',64),1,1,
            '{uuid4()}',statement_timestamp()+interval '30 minutes',
            statement_timestamp()+interval '30 minutes'
        );
        INSERT INTO backup_runs (
            id,status,destination_id,database_sha256,storage_manifest_sha256,
            database_bytes,storage_bytes,finished_at
        ) VALUES (
            '{backup_id}','succeeded','live.fixture',repeat('e',64),
            repeat('f',64),1,1,statement_timestamp()
        );
        INSERT INTO backup_restore_verifications (
            backup_run_id,manifest_sha256,verification_profile
        ) VALUES ('{backup_id}',repeat('f',64),'personal.isolated-restore.v1');
        INSERT INTO runtime_configuration_revisions (
            revision_id,generation_profile_id,reranker_profile_id,ocr_mode,
            ocr_profile_id,ocr_preset_id,created_by,effective
        ) VALUES (
            'v8d-live-0002','generation.qwen3-8b.ollama.windows-x64',
            'reranking.bge-v2-m3.cpu.windows-x64','explicit',
            'ocr.paddleocr-vl-1.6.cpu.windows-x64','balanced','{user_id}',false
        );
        INSERT INTO runtime_configuration_previews (
            id,actor_user_id,session_id,base_revision_id,generation_profile_id,
            reranker_profile_id,ocr_mode,ocr_profile_id,ocr_preset_id,
            impact_digest,operation_class,expires_at,consumed_at
        ) VALUES (
            '{preview_one}','{user_id}','{session_id}','v8d-baseline-0001',
            'generation.qwen3-8b.ollama.windows-x64',
            'reranking.bge-v2-m3.cpu.windows-x64','explicit',
            'ocr.paddleocr-vl-1.6.cpu.windows-x64','balanced','{impact}',
            'restart_scoped',statement_timestamp()+interval '5 minutes',
            statement_timestamp()
        );
        INSERT INTO runtime_configuration_changes (
            id,actor_user_id,prior_revision_id,desired_revision_id,preview_id,
            backup_run_id,impact_digest,operation_class,controller_nonce_hash
        ) VALUES (
            '{change_one}','{user_id}','v8d-baseline-0001','v8d-live-0002',
            '{preview_one}','{backup_id}','{impact}','restart_scoped','{nonce_hash}'
        );
        """,
    )

    prior = RuntimeConfiguration(
        "generation.qwen3-8b.ollama.windows-x64",
        "reranking.bge-v2-m3.cpu.windows-x64",
        "auto",
        "ocr.paddleocr-vl-1.6.cpu.windows-x64",
        "balanced",
    )
    desired = RuntimeConfiguration(
        prior.generation_profile_id,
        prior.reranker_profile_id,
        "explicit",
        prior.ocr_profile_id,
        prior.ocr_preset_id,
    )
    resolver = SignedProfileResolver(
        (
            ResolvedProfile(
                prior.generation_profile_id,
                "inference",
                (("GENERATION_MODEL", "qwen3:8b"),),
            ),
            ResolvedProfile(
                prior.reranker_profile_id,
                "inference",
                (("RERANKER_MODEL", "BAAI/bge-reranker-v2-m3"),),
            ),
            ResolvedProfile(
                prior.ocr_profile_id,
                "ocr",
                (("OCR_DEVICE", "cpu"), ("OCR_PIPELINE_VERSION", "v1.6")),
            ),
        ),
        {"balanced": (("OCR_CPU_THREADS", "10"), ("OCR_PAGE_BATCH_SIZE", "8"), ("OCR_PROCESS_COUNT", "1"))},
    )

    class Runtime:
        def __init__(self, fail_once: bool) -> None:
            self.fail_once = fail_once
            self.values: dict[str, str] = {}

        def drain(self, service: str) -> None:
            assert service == "ocr"

        def replace_environment(self, service: str, values: dict[str, str]) -> None:
            assert service == "ocr"
            self.values = values

        def restart(self, service: str) -> None:
            assert service == "ocr"

        def validate(self, service: str) -> None:
            assert service == "ocr"
            if self.fail_once:
                self.fail_once = False
                raise RuntimeError("forced live validation failure")

    class Reporter:
        def __init__(self, change_id: object) -> None:
            self.change_id = change_id

        def stage(self, stage: ControllerStage, reason_code: str | None = None) -> None:
            if stage is ControllerStage.PREFLIGHT:
                _psql(
                    project,
                    environment,
                    "SET ROLE rag_api; SELECT * FROM "
                    f"v9_controller_runtime_configuration_change('{self.change_id}',"
                    f"'{nonce_hash}');",
                )
            elif stage in {ControllerStage.EFFECTIVE, ControllerStage.ROLLED_BACK}:
                result = (
                    "effective" if stage is ControllerStage.EFFECTIVE else "rolled_back"
                )
                _psql(
                    project,
                    environment,
                    "SET ROLE rag_api; SELECT "
                    "v9_controller_finish_runtime_configuration("
                    f"'{self.change_id}','{nonce_hash}','{result}',"
                    f"'{reason_code or 'configuration_applied'}');",
                )
            else:
                _psql(
                    project,
                    environment,
                    "SET ROLE rag_api; SELECT "
                    "v9_controller_advance_runtime_configuration("
                    f"'{self.change_id}','{nonce_hash}','{stage.value}');",
                )

    failed_runtime = Runtime(fail_once=True)
    with pytest.raises(RuntimeError, match="forced live validation failure"):
        ConfigurationController(failed_runtime, resolver, Reporter(change_one)).apply(
            desired, prior
        )
    assert failed_runtime.values["OCR_SELECTION_MODE"] == "auto"
    assert (
        _psql(
            project,
            environment,
            "SELECT state || '|' || (SELECT revision_id FROM "
            "runtime_configuration_revisions WHERE effective) FROM "
            f"runtime_configuration_changes WHERE id='{change_one}';",
        )
        == "rolled_back|v8d-baseline-0001"
    )

    _psql(
        project,
        environment,
        f"""
        INSERT INTO runtime_configuration_previews (
            id,actor_user_id,session_id,base_revision_id,generation_profile_id,
            reranker_profile_id,ocr_mode,ocr_profile_id,ocr_preset_id,
            impact_digest,operation_class,expires_at,consumed_at
        ) VALUES (
            '{preview_two}','{user_id}','{session_id}','v8d-baseline-0001',
            'generation.qwen3-8b.ollama.windows-x64',
            'reranking.bge-v2-m3.cpu.windows-x64','explicit',
            'ocr.paddleocr-vl-1.6.cpu.windows-x64','balanced','{impact}',
            'restart_scoped',statement_timestamp()+interval '5 minutes',
            statement_timestamp()
        );
        INSERT INTO runtime_configuration_changes (
            id,actor_user_id,prior_revision_id,desired_revision_id,preview_id,
            backup_run_id,impact_digest,operation_class,controller_nonce_hash
        ) VALUES (
            '{change_two}','{user_id}','v8d-baseline-0001','v8d-live-0002',
            '{preview_two}','{backup_id}','{impact}','restart_scoped','{nonce_hash}'
        );
        """,
    )
    ConfigurationController(
        Runtime(fail_once=False), resolver, Reporter(change_two)
    ).apply(desired, prior)
    assert (
        _psql(
            project,
            environment,
            "SELECT state || '|' || (SELECT revision_id FROM "
            "runtime_configuration_revisions WHERE effective) FROM "
            f"runtime_configuration_changes WHERE id='{change_two}';",
        )
        == "effective|v8d-live-0002"
    )


def _write_environment(path: Path, values: dict[str, str]) -> None:
    path.write_text(
        "".join(f"{key}={value}\n" for key, value in sorted(values.items())),
        encoding="utf-8",
    )


def _exercise_schema_update_and_forced_rollback(
    project: str,
    compose_environment: Path,
    python: Path,
    migration_environment: dict[str, str],
) -> None:
    alembic = [str(python), "-m", "alembic"]
    _run(
        [*alembic, "upgrade", "0010_versioned_reprocessing"],
        environment=migration_environment,
        cwd=ROOT / "apps" / "api",
    )
    _compose(
        project,
        compose_environment,
        "exec",
        "-T",
        "postgres",
        "pg_dump",
        "--format=custom",
        "--file=/tmp/v8f-preupdate.dump",
        "--username=rag_cluster_admin",
        "rag",
    )
    _run(
        [*alembic, "upgrade", "head"],
        environment=migration_environment,
        cwd=ROOT / "apps" / "api",
    )
    assert (
        _psql(
            project,
            compose_environment,
            "SELECT version_num FROM alembic_version;",
        )
        == "0014_restart_without_backup"
    )
    assert (
        _psql(
            project,
            compose_environment,
            "SELECT has_function_privilege('rag_api', "
            "'v11_admin_personal_backup_history(integer)', 'EXECUTE');",
        )
        == "t"
    )

    candidate_readiness_failed = True
    assert candidate_readiness_failed, "the fixture must force candidate failure"
    _compose(
        project,
        compose_environment,
        "exec",
        "-T",
        "postgres",
        "dropdb",
        "--username=rag_cluster_admin",
        "--maintenance-db=postgres",
        "--if-exists",
        "--force",
        "rag",
    )
    _compose(
        project,
        compose_environment,
        "exec",
        "-T",
        "postgres",
        "createdb",
        "--username=rag_cluster_admin",
        "--maintenance-db=postgres",
        "--template=template0",
        "--owner=rag_cluster_admin",
        "rag",
    )
    _compose(
        project,
        compose_environment,
        "exec",
        "-T",
        "postgres",
        "pg_restore",
        "--exit-on-error",
        "--username=rag_cluster_admin",
        "--dbname=rag",
        "/tmp/v8f-preupdate.dump",
    )
    assert (
        _psql(
            project,
            compose_environment,
            "SELECT version_num FROM alembic_version;",
        )
        == "0010_versioned_reprocessing"
    )
    assert (
        _psql(
            project,
            compose_environment,
            "SELECT to_regprocedure('v11_admin_personal_backup_history(integer)') "
            "IS NULL;",
        )
        == "t"
    )

    _run(
        [*alembic, "upgrade", "head"],
        environment=migration_environment,
        cwd=ROOT / "apps" / "api",
    )


def test_live_disposable_migration_backup_and_isolated_restore(
    tmp_path: Path,
) -> None:
    if os.environ.get("RUN_V8D_PERSONAL_BACKUP_LIVE") != "1":
        pytest.skip("RUN_V8D_PERSONAL_BACKUP_LIVE is not enabled")
    python = Path(os.environ["V8D_API_PYTHON"]).resolve()
    identity = uuid4().hex
    project = f"localrag-personal-{identity[:12]}"
    source = tmp_path / "source"
    install = source / "install"
    data = source / "data"
    config = install / "config"
    destination = tmp_path / "destination"
    for path in (
        install / "state",
        config,
        data / "postgres",
        data / "rustfs",
        data / "application",
        destination,
    ):
        path.mkdir(parents=True, exist_ok=True)
    secrets = PersonalBackupService._verification_secrets(identity)
    values = secrets["values"]
    assert isinstance(values, dict)
    secret_path = install / "secrets.json"
    secret_path.write_text(json.dumps(secrets), encoding="utf-8")
    compose_environment = config / "compose.env"
    _write_environment(
        compose_environment,
        {
            "RAG_VERIFY_ID": identity,
            "RAG_VERIFY_POSTGRES_DATA": (data / "postgres").as_posix(),
            "RAG_VERIFY_RUSTFS_DATA": (data / "rustfs").as_posix(),
            "POSTGRES_CLUSTER_ADMIN_PASSWORD": str(values["postgres_cluster_admin"]),
            "RUSTFS_ROOT_ACCESS_KEY": str(values["rustfs_root_access"]),
            "RUSTFS_ROOT_SECRET_KEY": str(values["rustfs_root_secret"]),
        },
    )
    shutil.copy2(COMPOSE, config / "compose.personal.yaml")
    (install / "state" / "installation-journal.json").write_text(
        json.dumps({"compose_project": project}), encoding="utf-8"
    )
    try:
        _compose(project, compose_environment, "up", "-d", "--wait")
        _run(
            [
                "powershell.exe",
                "-NoLogo",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(PROVISION),
                "-ComposeFile",
                str(COMPOSE),
                "-ComposeProject",
                project,
                "-ComposeEnvironment",
                str(compose_environment),
                "-SecretDocument",
                str(secret_path),
            ]
        )
        postgres_port = _port(project, compose_environment, "postgres", "5432")
        rustfs_port = _port(project, compose_environment, "rustfs", "9000")
        migration_environment = {
            **os.environ,
            "PYTHONPATH": str(ROOT / "apps" / "api"),
            "MIGRATION_DATABASE_URL": (
                "postgresql+psycopg://rag_migrator:"
                f"{values['postgres_migrator']}@127.0.0.1:{postgres_port}/rag"
            ),
        }
        _exercise_schema_update_and_forced_rollback(
            project,
            compose_environment,
            python,
            migration_environment,
        )
        _exercise_live_apply_and_rollback(project, compose_environment)
        maintenance = {
            "MAINTENANCE_DATABASE_URL": (
                "postgresql+psycopg://rag_maintenance:"
                f"{values['postgres_maintenance']}@127.0.0.1:{postgres_port}/rag"
            ),
            "DATA_ROOT": str(data / "application"),
            "OBJECT_STORAGE_ENDPOINT_URL": f"http://127.0.0.1:{rustfs_port}",
            "OBJECT_STORAGE_REGION": "us-east-1",
            "OBJECT_STORAGE_BUCKET": "rag-originals",
            "OBJECT_STORAGE_FORCE_PATH_STYLE": "true",
            "OBJECT_STORAGE_USE_TLS": "false",
            "OBJECT_STORAGE_ACCESS_KEY_ID": str(values["rustfs_root_access"]),
            "OBJECT_STORAGE_SECRET_ACCESS_KEY": str(values["rustfs_root_secret"]),
        }
        _write_environment(config / "maintenance.env", maintenance)
        maintenance_environment = {
            **os.environ,
            **maintenance,
            "PYTHONPATH": str(ROOT / "apps" / "api"),
        }
        _run(
            [str(python), "-m", "app.maintenance_cli", "storage-bootstrap"],
            environment=maintenance_environment,
            cwd=ROOT / "apps" / "api",
        )
        service = PersonalBackupService(
            install_root=install,
            release_root=ROOT,
            data_root=data,
            api_python=python,
            select_destination=lambda: destination,
        )
        backup_id = uuid4()
        exported = service.export(destination, backup_id)
        evidence = service.verify(exported, backup_id)
        verified = json.loads(evidence.read_text(encoding="utf-8"))
        assert verified["database_revision"] == "0014_restart_without_backup"
        assert verified["database_security"] == "pass"
        assert verified["storage_inventory"] == "pass"
    finally:
        _compose(project, compose_environment, "down", "--remove-orphans")
