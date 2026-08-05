from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID


class PersonalBackupError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class BackupExport:
    root: Path
    database_sha256: str
    manifest_sha256: str
    database_bytes: int
    storage_bytes: int


class PersonalBackupService:
    """Writes a coordinated pair and proves it in disposable isolated stores."""

    def __init__(
        self,
        *,
        install_root: Path,
        release_root: Path,
        data_root: Path,
        api_python: Path,
        select_destination: Callable[[], Path | None] | None = None,
        runner: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
    ) -> None:
        self.install_root = install_root.resolve()
        self.release_root = release_root.resolve()
        self.data_root = data_root.resolve()
        self.api_python = api_python.resolve()
        self._select_destination = select_destination or select_backup_destination
        self._run = runner
        self._config = self.install_root / "config"
        journal = _read_json(self.install_root / "state" / "installation-journal.json")
        project = journal.get("compose_project")
        if not isinstance(project, str) or re.fullmatch(
            r"localrag-personal-[0-9a-f]{12}", project
        ) is None:
            raise PersonalBackupError("Personal installation journal is invalid")
        self._project = project

    def choose_destination(self) -> Path:
        selected = self._select_destination()
        if selected is None:
            raise PersonalBackupError("backup destination selection was cancelled")
        path = selected.expanduser().resolve()
        if not path.is_absolute() or not path.is_dir() or path.is_symlink():
            raise PersonalBackupError("backup destination must be an existing folder")
        for protected in (self.install_root, self.release_root, self.data_root):
            if (
                path == protected
                or protected in path.parents
                or path in protected.parents
            ):
                raise PersonalBackupError(
                    "backup destination must be outside application and data roots"
                )
        return path

    def export(self, destination: Path, backup_run_id: UUID) -> BackupExport:
        workspace_root = self.data_root / "backup-work"
        workspace_root.mkdir(parents=True, exist_ok=True)
        workspace = Path(
            tempfile.mkdtemp(
                prefix=f"export-{str(backup_run_id)[:8]}-", dir=workspace_root
            )
        )
        dump = workspace / "database.dump"
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        bundle = destination / f"LocalRAG-backup-{timestamp}-{str(backup_run_id)[:8]}"
        try:
            self._pg_dump(dump)
            self._maintenance(
                self._source_maintenance_environment(),
                "--confirm-stopped",
                "storage-export",
                str(bundle),
                "--database-dump",
                str(dump),
            )
            profile_source = (
                self.release_root
                / "ops"
                / "windows"
                / "v8a"
                / "capability-profiles.json"
            )
            release_source = (
                self.release_root / "ops" / "windows" / "v8a" / "personal-release.json"
            )
            shutil.copy2(profile_source, bundle / "capability-profiles.json")
            release = _read_json(release_source)
            safe_release = {
                "schema_version": release.get("schema_version"),
                "manifest_id": release.get("manifest_id"),
                "profile_id": release.get("profile_id"),
                "expected_alembic_revision": release.get("expected_alembic_revision"),
                "stores": release.get("stores"),
                "ollama_models": release.get("ollama_models"),
            }
            _write_json(bundle / "release-metadata.json", safe_release)
            manifest_sha256, _ = _hash_file(bundle / "manifest.json")
            profile_sha256, _ = _hash_file(bundle / "capability-profiles.json")
            release_sha256, _ = _hash_file(bundle / "release-metadata.json")
            _write_json(
                bundle / "backup-bundle.json",
                {
                    "schema_version": 1,
                    "backup_run_id": str(backup_run_id),
                    "manifest_sha256": manifest_sha256,
                    "capability_profiles_sha256": profile_sha256,
                    "release_metadata_sha256": release_sha256,
                    "verification_profile": "personal.isolated-restore.v1",
                },
            )
            database_sha256, database_bytes = _hash_file(bundle / "database.dump")
            storage_bytes = sum(
                path.stat().st_size
                for path in bundle.rglob("*")
                if path.is_file() and path.name != "database.dump"
            )
            return BackupExport(
                root=bundle,
                database_sha256=database_sha256,
                manifest_sha256=manifest_sha256,
                database_bytes=database_bytes,
                storage_bytes=storage_bytes,
            )
        except Exception:
            if bundle.exists():
                shutil.rmtree(bundle, ignore_errors=True)
            raise
        finally:
            shutil.rmtree(workspace, ignore_errors=True)

    def verify(self, backup: BackupExport, backup_run_id: UUID) -> Path:
        envelope = _read_json(backup.root / "backup-bundle.json")
        if envelope.get("backup_run_id") != str(backup_run_id):
            raise PersonalBackupError("backup identity does not match the operation")
        for name, expected in (
            ("manifest.json", envelope.get("manifest_sha256")),
            ("capability-profiles.json", envelope.get("capability_profiles_sha256")),
            ("release-metadata.json", envelope.get("release_metadata_sha256")),
        ):
            actual, _ = _hash_file(backup.root / name)
            if actual != expected:
                raise PersonalBackupError(f"backup metadata is tampered: {name}")

        release_metadata = _read_json(backup.root / "release-metadata.json")
        expected_revision = release_metadata.get("expected_alembic_revision")
        if not isinstance(expected_revision, str) or re.fullmatch(
            r"[0-9]{4}_[a-z0-9_]+", expected_revision
        ) is None:
            raise PersonalBackupError("backup release revision is invalid")

        verification_root = self.data_root / "restore-verification"
        verification_root.mkdir(parents=True, exist_ok=True)
        target = Path(
            tempfile.mkdtemp(
                prefix=f"verify-{str(backup_run_id)[:8]}-", dir=verification_root
            )
        )
        verify_id = secrets.token_hex(16)
        project = f"localrag-verify-{verify_id[:12]}"
        postgres_data = target / "postgres"
        rustfs_data = target / "rustfs"
        app_data = target / "application"
        for path in (postgres_data, rustfs_data, app_data):
            path.mkdir()
        secret_document = self._verification_secrets(verify_id)
        secret_path = target / "secrets.json"
        _write_json(secret_path, secret_document)
        compose_environment = target / "compose.env"
        _write_environment(
            compose_environment,
            {
                "RAG_VERIFY_ID": verify_id,
                "RAG_VERIFY_POSTGRES_DATA": postgres_data.as_posix(),
                "RAG_VERIFY_RUSTFS_DATA": rustfs_data.as_posix(),
                "POSTGRES_CLUSTER_ADMIN_PASSWORD": secret_document["values"][
                    "postgres_cluster_admin"
                ],
                "RUSTFS_ROOT_ACCESS_KEY": secret_document["values"][
                    "rustfs_root_access"
                ],
                "RUSTFS_ROOT_SECRET_KEY": secret_document["values"][
                    "rustfs_root_secret"
                ],
            },
        )
        compose = (
            self.release_root
            / "ops"
            / "windows"
            / "v8a"
            / "compose.restore-verifier.yaml"
        )
        try:
            self._compose(
                project, compose, compose_environment, "up", "-d", "--wait"
            )
            self._provision_verifier(
                project, compose, compose_environment, secret_path
            )
            self._compose(
                project,
                compose,
                compose_environment,
                "cp",
                str(backup.root / "database.dump"),
                "postgres:/tmp/localrag-restore.dump",
            )
            self._compose(
                project,
                compose,
                compose_environment,
                "exec",
                "-T",
                "postgres",
                "pg_restore",
                "-U",
                "rag_cluster_admin",
                "-d",
                "rag",
                "--exit-on-error",
                "--clean",
                "--if-exists",
                "/tmp/localrag-restore.dump",
            )
            postgres_port = self._published_port(
                project, compose, compose_environment, "postgres", "5432"
            )
            rustfs_port = self._published_port(
                project, compose, compose_environment, "rustfs", "9000"
            )
            values = secret_document["values"]
            maintenance = {
                "MAINTENANCE_DATABASE_URL": (
                    "postgresql+psycopg://rag_maintenance:"
                    f"{values['postgres_maintenance']}@127.0.0.1:{postgres_port}/rag"
                ),
                "DATA_ROOT": str(app_data),
                "OBJECT_STORAGE_ENDPOINT_URL": f"http://127.0.0.1:{rustfs_port}",
                "OBJECT_STORAGE_REGION": "us-east-1",
                "OBJECT_STORAGE_BUCKET": "rag-originals",
                "OBJECT_STORAGE_FORCE_PATH_STYLE": "true",
                "OBJECT_STORAGE_USE_TLS": "false",
                "OBJECT_STORAGE_ACCESS_KEY_ID": values["rustfs_root_access"],
                "OBJECT_STORAGE_SECRET_ACCESS_KEY": values["rustfs_root_secret"],
            }
            self._maintenance(maintenance, "storage-bootstrap")
            self._maintenance(
                maintenance,
                "--confirm-stopped",
                "storage-import",
                str(backup.root),
            )
            self._maintenance(maintenance, "storage-audit")
            security = self._compose_output(
                project,
                compose,
                compose_environment,
                "exec",
                "-T",
                "postgres",
                "psql",
                "-X",
                "-A",
                "-t",
                "-U",
                "rag_cluster_admin",
                "-d",
                "rag",
                "-c",
                f"SELECT v4_schema_revision() = '{expected_revision}' "
                "AND v9_runtime_configuration_integrity() "
                "AND (SELECT count(*) = 6 FROM pg_roles WHERE rolname IN "
                "('rag_owner','rag_migrator','rag_api','rag_worker',"
                "'rag_maintenance','rag_backup'));",
            ).strip()
            if security != "t":
                raise PersonalBackupError(
                    "isolated database security validation failed"
                )
            evidence = backup.root / "restore-verification.json"
            _write_json(
                evidence,
                {
                    "schema_version": 1,
                    "backup_run_id": str(backup_run_id),
                    "manifest_sha256": backup.manifest_sha256,
                    "verification_profile": "personal.isolated-restore.v1",
                    "database_revision": expected_revision,
                    "database_security": "pass",
                    "storage_inventory": "pass",
                    "isolated_project_id": hashlib.sha256(project.encode()).hexdigest(),
                    "verified_at": datetime.now(UTC).isoformat(),
                },
            )
            self._record_verified_backup(
                backup=backup,
                backup_run_id=backup_run_id,
                evidence=evidence,
                expected_revision=expected_revision,
            )
            return evidence
        finally:
            try:
                self._compose(
                    project,
                    compose,
                    compose_environment,
                    "down",
                    "--remove-orphans",
                    timeout=180,
                )
            finally:
                shutil.rmtree(target, ignore_errors=True)

    def _record_verified_backup(
        self,
        *,
        backup: BackupExport,
        backup_run_id: UUID,
        evidence: Path,
        expected_revision: str,
    ) -> None:
        catalog_path = self.data_root / "backup-catalog.json"
        if catalog_path.is_symlink():
            raise PersonalBackupError("backup catalog cannot be a symbolic link")
        if catalog_path.exists():
            catalog = _read_json(catalog_path)
            if (
                catalog.get("schema_version") != 1
                or catalog.get("retention")
                != {"mode": "keep_all", "maximum_verified_backups": None}
                or not isinstance(catalog.get("entries"), list)
            ):
                raise PersonalBackupError("backup catalog is invalid")
            entries = list(catalog["entries"])
        else:
            entries = []
        evidence_sha256, _ = _hash_file(evidence)
        entry = {
            "backup_run_id": str(backup_run_id),
            "bundle_path": str(backup.root.resolve()),
            "destination_label": backup.root.parent.name or backup.root.parent.anchor,
            "database_revision": expected_revision,
            "database_sha256": backup.database_sha256,
            "manifest_sha256": backup.manifest_sha256,
            "restore_verification_sha256": evidence_sha256,
            "verified_at": datetime.now(UTC).isoformat(),
        }
        entries = [
            item
            for item in entries
            if isinstance(item, dict)
            and item.get("backup_run_id") != str(backup_run_id)
        ]
        entries.insert(0, entry)
        _write_json(
            catalog_path,
            {
                "schema_version": 1,
                "retention": {
                    "mode": "keep_all",
                    "maximum_verified_backups": None,
                },
                "entries": entries[:50],
            },
        )

    def _pg_dump(self, destination: Path) -> None:
        command = self._compose_command(
            self._project,
            self._config / "compose.personal.yaml",
            self._config / "compose.env",
            "exec",
            "-T",
            "postgres",
            "pg_dump",
            "-U",
            "rag_cluster_admin",
            "-d",
            "rag",
            "--format=custom",
        )
        with destination.open("xb") as output:
            result = self._run(
                command,
                cwd=self.release_root,
                stdout=output,
                stderr=subprocess.PIPE,
                timeout=600,
                check=False,
            )
        if result.returncode != 0:
            raise PersonalBackupError("PostgreSQL backup failed")
        _hash_file(destination)

    def _source_maintenance_environment(self) -> dict[str, str]:
        return _read_environment(self._config / "maintenance.env")

    def _maintenance(self, environment: dict[str, str], *arguments: str) -> None:
        merged = {
            "PATH": os.environ.get("PATH", ""),
            "SystemRoot": os.environ.get("SystemRoot", "C:\\Windows"),
            "PYTHONPATH": str(self.release_root / "apps" / "api"),
            **environment,
        }
        result = self._run(
            [str(self.api_python), "-m", "app.maintenance_cli", *arguments],
            cwd=self.release_root / "apps" / "api",
            env=merged,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=1800,
            check=False,
        )
        if result.returncode != 0:
            raise PersonalBackupError("backup storage validation failed")

    def _provision_verifier(
        self, project: str, compose: Path, environment: Path, secrets_path: Path
    ) -> None:
        script = (
            self.release_root
            / "ops"
            / "windows"
            / "v8a"
            / "Initialize-RagPersonalPostgres.ps1"
        )
        result = self._run(
            [
                "powershell.exe",
                "-NoLogo",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(script),
                "-ComposeFile",
                str(compose),
                "-ComposeProject",
                project,
                "-ComposeEnvironment",
                str(environment),
                "-SecretDocument",
                str(secrets_path),
            ],
            cwd=self.release_root,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=300,
            check=False,
        )
        if result.returncode != 0:
            raise PersonalBackupError("isolated PostgreSQL provisioning failed")

    def _published_port(
        self,
        project: str,
        compose: Path,
        environment: Path,
        service: str,
        port: str,
    ) -> int:
        output = self._compose_output(
            project, compose, environment, "port", service, port
        ).strip()
        match = re.fullmatch(r"127\.0\.0\.1:(\d+)", output)
        if match is None:
            raise PersonalBackupError("isolated store is not bound to IPv4 loopback")
        return int(match.group(1))

    def _compose(
        self,
        project: str,
        compose: Path,
        environment: Path,
        *arguments: str,
        timeout: int = 600,
    ) -> None:
        result = self._run(
            self._compose_command(project, compose, environment, *arguments),
            cwd=self.release_root,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
        )
        if result.returncode != 0:
            raise PersonalBackupError("isolated restore container operation failed")

    def _compose_output(
        self, project: str, compose: Path, environment: Path, *arguments: str
    ) -> str:
        result = self._run(
            self._compose_command(project, compose, environment, *arguments),
            cwd=self.release_root,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=300,
            check=False,
        )
        if result.returncode != 0:
            raise PersonalBackupError("isolated restore inspection failed")
        return result.stdout.decode("utf-8", errors="strict")

    @staticmethod
    def _compose_command(
        project: str, compose: Path, environment: Path, *arguments: str
    ) -> list[str]:
        return [
            "docker.exe",
            "compose",
            "-p",
            project,
            "--env-file",
            str(environment),
            "-f",
            str(compose),
            *arguments,
        ]

    @staticmethod
    def _verification_secrets(installation_id: str) -> dict[str, object]:
        def token() -> str:
            return secrets.token_urlsafe(32)

        return {
            "schema_version": 1,
            "installation_id": installation_id,
            "values": {
                "postgres_cluster_admin": token(),
                "postgres_migrator": token(),
                "postgres_api": token(),
                "postgres_worker": token(),
                "postgres_maintenance": token(),
                "rustfs_root_access": "verify" + secrets.token_hex(10),
                "rustfs_root_secret": token(),
            },
        }


def select_backup_destination() -> Path | None:
    script = (
        "Add-Type -AssemblyName System.Windows.Forms;"
        "$dialog=New-Object System.Windows.Forms.FolderBrowserDialog;"
        "$dialog.Description='Choose a folder for the Local RAG backup';"
        "$dialog.ShowNewFolderButton=$true;"
        "if($dialog.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK){"
        "[Console]::Out.Write($dialog.SelectedPath)}"
    )
    result = subprocess.run(
        [
            "powershell.exe",
            "-NoLogo",
            "-NoProfile",
            "-STA",
            "-WindowStyle",
            "Hidden",
            "-Command",
            script,
        ],
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    selected = result.stdout.strip()
    return Path(selected) if result.returncode == 0 and selected else None


def _hash_file(path: Path) -> tuple[str, int]:
    if not path.is_file() or path.is_symlink():
        raise PersonalBackupError(f"backup file is invalid: {path.name}")
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def _read_json(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PersonalBackupError(f"invalid local document: {path.name}") from exc
    if not isinstance(value, dict):
        raise PersonalBackupError(f"invalid local document: {path.name}")
    return value


def _write_json(path: Path, value: dict[str, object]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    os.replace(temporary, path)


def _read_environment(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line or "=" not in line:
            raise PersonalBackupError("maintenance environment is invalid")
        key, value = line.split("=", 1)
        if not key or key in result:
            raise PersonalBackupError("maintenance environment is invalid")
        result[key] = value
    return result


def _write_environment(path: Path, values: dict[str, str]) -> None:
    if any("\n" in key or "\n" in value or "=" in key for key, value in values.items()):
        raise PersonalBackupError("verification environment is invalid")
    path.write_text(
        "".join(f"{key}={value}\n" for key, value in sorted(values.items())),
        encoding="utf-8",
    )
