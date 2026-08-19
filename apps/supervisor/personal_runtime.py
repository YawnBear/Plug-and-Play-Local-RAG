from __future__ import annotations

import argparse
import json
import os
import re
import signal
import subprocess
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable, Protocol

from .controller import (
    ConfigurationController,
    ControllerAction,
    ControllerProtocolError,
    ControllerRequest,
    ControllerStage,
    ResolvedProfile,
    RuntimeConfiguration,
    SignedProfileResolver,
)
from .personal_backup import BackupExport, PersonalBackupError, PersonalBackupService


class PersonalRuntimeError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class PersonalPaths:
    install_root: Path
    release_root: Path
    config_root: Path
    data_root: Path
    api_python: Path
    node: Path

    @classmethod
    def resolve(
        cls,
        *,
        install_root: Path,
        release_root: Path,
        data_root: Path,
        development_source: bool,
    ) -> PersonalPaths:
        values = (install_root.resolve(), release_root.resolve(), data_root.resolve())
        if any(not path.is_absolute() for path in values):
            raise PersonalRuntimeError("Personal runtime roots must be absolute")
        api_python = (
            Path(os.environ.get("RAG_DEVELOPMENT_PYTHON", os.sys.executable))
            if development_source
            else values[1] / "runtimes" / "api-python" / "python.exe"
        )
        node = (
            _command_path("node.exe")
            if development_source
            else values[1] / "runtimes" / "node" / "node.exe"
        )
        return cls(
            install_root=values[0],
            release_root=values[1],
            config_root=values[0] / "config",
            data_root=values[2],
            api_python=api_python.resolve(),
            node=node.resolve(),
        )


@dataclass(frozen=True, slots=True)
class ChildSpec:
    name: str
    command: tuple[str, ...]
    cwd: Path
    environment_file: Path
    readiness_url: str | None = None
    readiness_token_name: str | None = None


class ProcessHandle(Protocol):
    def poll(self) -> int | None: ...

    def terminate(self) -> None: ...

    def wait(self, timeout: float | None = None) -> int: ...

    def kill(self) -> None: ...

    def send_signal(self, signal: int) -> None: ...


class ControllerApi:
    def __init__(self, base_url: str, service_token: str) -> None:
        if base_url != "http://127.0.0.1:8000" or len(service_token) < 32:
            raise PersonalRuntimeError("controller API identity is invalid")
        self._base_url = base_url
        self._headers = {
            "Authorization": f"Bearer {service_token}",
            "Content-Type": "application/json",
        }

    def claim(
        self, request: ControllerRequest
    ) -> tuple[RuntimeConfiguration, RuntimeConfiguration]:
        value = self._post(
            f"/internal/controller/configuration/{request.change_id}/claim",
            {"nonce": request.nonce},
        )
        return (
            RuntimeConfiguration.parse(value["prior_configuration"]),
            RuntimeConfiguration.parse(value["desired_configuration"]),
        )

    def stage(
        self,
        request: ControllerRequest,
        stage: ControllerStage,
        reason_code: str | None = None,
    ) -> None:
        if stage in {
            ControllerStage.PREFLIGHT,
            ControllerStage.EFFECTIVE,
            ControllerStage.FAILED,
            ControllerStage.ROLLED_BACK,
        }:
            if stage is ControllerStage.PREFLIGHT:
                return
            result = {
                ControllerStage.EFFECTIVE: "effective",
                ControllerStage.FAILED: "failed",
                ControllerStage.ROLLED_BACK: "rolled_back",
            }[stage]
            self._post(
                f"/internal/controller/configuration/{request.change_id}/finish",
                {
                    "nonce": request.nonce,
                    "result": result,
                    "reason_code": reason_code or "configuration_applied",
                },
            )
            return
        self._post(
            f"/internal/controller/configuration/{request.change_id}/stage",
            {"nonce": request.nonce, "stage": stage.value},
        )

    def smoke(self, profile_id: str) -> None:
        value = self._post(f"/internal/controller/profiles/{profile_id}/smoke", {})
        if value.get("succeeded") is not True:
            raise PersonalRuntimeError(
                f"profile smoke test failed: {value.get('reason_code', 'unknown')}"
            )

    def claim_backup(self, request: ControllerRequest) -> None:
        self._post(
            f"/internal/controller/backups/{request.change_id}/claim",
            {"nonce": request.nonce},
        )

    def backup_stage(self, request: ControllerRequest, stage: str) -> None:
        self._post(
            f"/internal/controller/backups/{request.change_id}/stage",
            {"nonce": request.nonce, "stage": stage},
        )

    def backup_exported(self, request: ControllerRequest, backup: BackupExport) -> None:
        self._post(
            f"/internal/controller/backups/{request.change_id}/exported",
            {
                "nonce": request.nonce,
                "database_sha256": backup.database_sha256,
                "manifest_sha256": backup.manifest_sha256,
                "database_bytes": backup.database_bytes,
                "storage_bytes": backup.storage_bytes,
            },
        )

    def backup_verified(self, request: ControllerRequest, manifest_sha256: str) -> None:
        self._post(
            f"/internal/controller/backups/{request.change_id}/verified",
            {
                "nonce": request.nonce,
                "manifest_sha256": manifest_sha256,
                "verification_profile": "personal.isolated-restore.v1",
            },
        )

    def backup_failed(self, request: ControllerRequest, reason_code: str) -> None:
        self._post(
            f"/internal/controller/backups/{request.change_id}/failed",
            {"nonce": request.nonce, "reason_code": reason_code},
        )

    def maintenance_drain(self) -> None:
        self._post("/internal/controller/maintenance/drain", {})

    def maintenance_resume(self) -> None:
        self._post("/internal/controller/maintenance/resume", {})

    def _post(self, path: str, value: dict[str, object]) -> dict[str, object]:
        request = urllib.request.Request(
            self._base_url + path,
            data=json.dumps(value, separators=(",", ":")).encode("utf-8"),
            headers=self._headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=920) as response:
                body = response.read(64 * 1024)
        except (OSError, urllib.error.URLError) as exc:
            raise PersonalRuntimeError("controller API request failed") from exc
        if not body:
            return {}
        parsed = json.loads(body)
        if not isinstance(parsed, dict):
            raise PersonalRuntimeError("controller API returned an invalid response")
        return parsed


class _Reporter:
    def __init__(self, api: ControllerApi, request: ControllerRequest) -> None:
        self.api = api
        self.request = request
        self.last_stage = ControllerStage.PREFLIGHT

    def stage(self, stage: ControllerStage, reason_code: str | None = None) -> None:
        self.api.stage(self.request, stage, reason_code)
        self.last_stage = stage


class PersonalRuntime:
    """Single-user foreground process graph and strict V8D controller runtime."""

    _SERVICE_KEYS = {
        "inference": frozenset({"GENERATION_MODEL", "RERANKER_MODEL"}),
        "ocr": frozenset(
            {
                "OCR_DEVICE",
                "OCR_PIPELINE_VERSION",
                "OCR_CPU_THREADS",
                "OCR_PAGE_BATCH_SIZE",
                "OCR_PROCESS_COUNT",
                "OCR_SELECTION_MODE",
            }
        ),
    }

    def __init__(
        self,
        paths: PersonalPaths,
        specs: tuple[ChildSpec, ...],
        api: ControllerApi,
        resolver: SignedProfileResolver,
        profile_values: dict[str, tuple[str, dict[str, str]]],
        *,
        backup: PersonalBackupService | None = None,
        popen: object = subprocess.Popen,
        sleeper: object = time.sleep,
        recovery_completion: Callable[[], None] | None = None,
    ) -> None:
        self.paths = paths
        self._specs = {spec.name: spec for spec in specs}
        self._api = api
        self._resolver = resolver
        self._profile_values = profile_values
        self._backup = backup
        self._popen = popen
        self._sleep = sleeper
        self._recovery_completion = recovery_completion
        self._children: dict[str, ProcessHandle] = {}
        self._overrides = self._read_overrides()
        self._lock = threading.RLock()

    def start_all(self) -> None:
        for name in ("inference", "ocr", "api", "ingestion", "deletion", "web"):
            if name in self._specs:
                self._start(name)
        if self._recovery_completion is not None:
            self._recovery_completion()

    def monitor(self) -> int:
        while True:
            for name, process in tuple(self._children.items()):
                code = process.poll()
                if code is not None:
                    self.stop_all()
                    raise PersonalRuntimeError(
                        f"Personal child {name} exited with code {code}"
                    )
            self._sleep(0.25)

    def stop_all(self) -> None:
        for name in reversed(tuple(self._children)):
            self._stop(name)

    def apply(self, request: ControllerRequest) -> None:
        if request.action is ControllerAction.CREATE_BACKUP:
            self._create_backup(request)
            return
        if request.action is not ControllerAction.APPLY_CONFIGURATION:
            raise PersonalRuntimeError("unsupported Personal controller action")
        prior, desired = self._api.claim(request)
        reporter = _Reporter(self._api, request)
        controller = ConfigurationController(self, self._resolver, reporter)
        try:
            controller.apply(desired, prior)
        except Exception:
            if reporter.last_stage is not ControllerStage.ROLLED_BACK:
                try:
                    reporter.stage(ControllerStage.FAILED, "rollback_failed")
                except Exception:
                    pass
            raise

    def _create_backup(self, request: ControllerRequest) -> None:
        if self._backup is None:
            raise PersonalRuntimeError("Personal backup service is unavailable")
        claimed = False
        drained = False
        stopped_workers: list[str] = []
        try:
            self._api.claim_backup(request)
            claimed = True
            destination = self._backup.choose_destination()
            self._api.maintenance_drain()
            drained = True
            for service in ("ingestion", "deletion"):
                if service in self._children:
                    self._stop(service)
                    stopped_workers.append(service)
            self._api.backup_stage(request, "exporting")
            exported = self._backup.export(destination, request.change_id)
            self._api.backup_exported(request, exported)
            self._api.backup_stage(request, "verifying")
            self._backup.verify(exported, request.change_id)
            self._api.backup_verified(request, exported.manifest_sha256)
        except PersonalBackupError as exc:
            if claimed:
                reason = (
                    "destination_cancelled"
                    if "cancelled" in str(exc).lower()
                    else "backup_or_restore_verification_failed"
                )
                try:
                    self._api.backup_failed(request, reason)
                except Exception:
                    pass
            raise
        except Exception:
            if claimed:
                try:
                    self._api.backup_failed(request, "backup_controller_failed")
                except Exception:
                    pass
            raise
        finally:
            for service in stopped_workers:
                try:
                    self._start(service)
                except Exception:
                    pass
            if drained:
                try:
                    self._api.maintenance_resume()
                except Exception:
                    pass

    def drain(self, service: str) -> None:
        spec = self._require_mutable_service(service)
        if spec.readiness_url is None or spec.readiness_token_name is None:
            raise PersonalRuntimeError("mutable service has no drain identity")
        environment = self._environment(spec)
        self._service_post(
            spec.readiness_url.replace("/health", "/admin/drain"),
            environment[spec.readiness_token_name],
        )

    def replace_environment(self, service: str, values: dict[str, str]) -> None:
        self._require_mutable_service(service)
        if set(values) - self._SERVICE_KEYS[service]:
            raise PersonalRuntimeError("controller attempted a non-allowlisted setting")
        if any(not isinstance(value, str) or not value for value in values.values()):
            raise PersonalRuntimeError("controller setting value is invalid")
        with self._lock:
            self._overrides[service] = dict(sorted(values.items()))
            self._write_overrides()

    def restart(self, service: str) -> None:
        self._require_mutable_service(service)
        with self._lock:
            self._stop(service)
            self._start(service)

    def validate(self, service: str) -> None:
        spec = self._require_mutable_service(service)
        self._wait_ready(spec, self._children[service])
        selected = self._overrides.get(service, {})
        matched = [
            profile_id
            for profile_id, (profile_service, values) in self._profile_values.items()
            if profile_service == service
            and all(selected.get(key) == value for key, value in values.items())
        ]
        if not matched:
            raise PersonalRuntimeError("effective profile could not be resolved")
        for profile_id in sorted(matched):
            self._api.smoke(profile_id)

    def _require_mutable_service(self, service: str) -> ChildSpec:
        if service not in self._SERVICE_KEYS or service not in self._specs:
            raise PersonalRuntimeError("controller service is not allowlisted")
        return self._specs[service]

    def _start(self, name: str) -> None:
        if name in self._children:
            raise PersonalRuntimeError(f"Personal child {name} is already running")
        spec = self._specs[name]
        environment = self._environment(spec)
        process = self._popen(
            list(spec.command),
            cwd=str(spec.cwd),
            env=environment,
            creationflags=(
                subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
                if os.name == "nt"
                else 0
            ),
        )
        self._children[name] = process
        try:
            self._wait_ready(spec, process)
        except Exception:
            self._stop(name)
            raise

    def _stop(self, name: str) -> None:
        process = self._children.pop(name, None)
        if process is None:
            return
        if process.poll() is None:
            if os.name == "nt":
                try:
                    process.send_signal(signal.CTRL_BREAK_EVENT)
                    process.wait(timeout=30)
                    return
                except (OSError, subprocess.TimeoutExpired):
                    pass
            process.terminate()
            try:
                process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)

    def _wait_ready(self, spec: ChildSpec, process: ProcessHandle) -> None:
        if spec.readiness_url is None:
            self._sleep(1)
            if process.poll() is not None:
                raise PersonalRuntimeError(f"Personal child {spec.name} failed startup")
            return
        deadline = time.monotonic() + 120
        environment = self._environment(spec)
        headers = {}
        if spec.readiness_token_name:
            headers["Authorization"] = (
                "Bearer " + environment[spec.readiness_token_name]
            )
        while time.monotonic() < deadline:
            if process.poll() is not None:
                break
            try:
                request = urllib.request.Request(spec.readiness_url, headers=headers)
                with urllib.request.urlopen(request, timeout=2) as response:
                    if 200 <= response.status < 300:
                        return
            except (OSError, urllib.error.URLError):
                self._sleep(0.25)
        raise PersonalRuntimeError(f"Personal child {spec.name} did not become ready")

    def _environment(self, spec: ChildSpec) -> dict[str, str]:
        values = _read_environment(spec.environment_file)
        values.update(self._overrides.get(spec.name, {}))
        values.update(
            {
                "PATH": os.environ.get("PATH", ""),
                "SystemRoot": os.environ.get("SystemRoot", "C:\\Windows"),
                "TEMP": os.environ.get("TEMP", str(self.paths.install_root / "cache")),
                "TMP": os.environ.get("TMP", str(self.paths.install_root / "cache")),
                "PYTHONPATH": str(self.paths.release_root / "apps" / "api"),
            }
        )
        return values

    def _read_overrides(self) -> dict[str, dict[str, str]]:
        path = self.paths.config_root / "runtime-overrides.json"
        if not path.exists():
            return {}
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise PersonalRuntimeError("runtime override document is invalid") from exc
        if not isinstance(value, dict) or set(value) != {"schema_version", "services"}:
            raise PersonalRuntimeError("runtime override fields are invalid")
        services = value["services"]
        if value["schema_version"] != 1 or not isinstance(services, dict):
            raise PersonalRuntimeError("runtime override schema is invalid")
        result: dict[str, dict[str, str]] = {}
        for service, entries in services.items():
            if service not in self._SERVICE_KEYS or not isinstance(entries, dict):
                raise PersonalRuntimeError("runtime override service is invalid")
            if set(entries) - self._SERVICE_KEYS[service] or any(
                not isinstance(item, str) or not item for item in entries.values()
            ):
                raise PersonalRuntimeError("runtime override value is invalid")
            result[service] = dict(entries)
        return result

    def _write_overrides(self) -> None:
        path = self.paths.config_root / "runtime-overrides.json"
        temporary = path.with_suffix(".tmp")
        encoded = json.dumps(
            {"schema_version": 1, "services": self._overrides},
            sort_keys=True,
            separators=(",", ":"),
        )
        temporary.write_text(encoded, encoding="utf-8")
        os.replace(temporary, path)

    @staticmethod
    def _service_post(url: str, token: str) -> None:
        request = urllib.request.Request(
            url,
            data=b"{}",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=920) as response:
                if not 200 <= response.status < 300:
                    raise PersonalRuntimeError("service drain was refused")
        except (OSError, urllib.error.URLError) as exc:
            raise PersonalRuntimeError("service drain failed") from exc


class ControllerServer:
    def __init__(
        self,
        runtime: PersonalRuntime,
        service_token: str,
        *,
        host: str = "127.0.0.1",
        port: int = 8102,
    ) -> None:
        if host != "127.0.0.1" or len(service_token) < 32:
            raise PersonalRuntimeError("controller listener identity is invalid")
        self._runtime = runtime
        self._token = service_token
        self._operation_lock = threading.Lock()
        parent = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802
                parent._handle(self)

            def log_message(self, _format: str, *args: object) -> None:
                return

        self._server = ThreadingHTTPServer((host, port), Handler)

    def serve_in_thread(self) -> threading.Thread:
        thread = threading.Thread(
            target=self._server.serve_forever,
            name="personal-controller",
            daemon=True,
        )
        thread.start()
        return thread

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()

    def _handle(self, handler: BaseHTTPRequestHandler) -> None:
        if (
            handler.path != "/v1/commands"
            or handler.headers.get("Authorization") != f"Bearer {self._token}"
            or handler.headers.get_content_type() != "application/json"
        ):
            self._respond(handler, HTTPStatus.NOT_FOUND, {"detail": "not found"})
            return
        try:
            length = int(handler.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if not 1 <= length <= 2048:
            self._respond(handler, HTTPStatus.BAD_REQUEST, {"detail": "invalid body"})
            return
        try:
            request = ControllerRequest.parse(handler.rfile.read(length))
        except ControllerProtocolError:
            self._respond(
                handler, HTTPStatus.BAD_REQUEST, {"detail": "invalid command"}
            )
            return
        if not self._operation_lock.acquire(blocking=False):
            self._respond(handler, HTTPStatus.CONFLICT, {"detail": "controller busy"})
            return
        threading.Thread(
            target=self._execute,
            args=(request,),
            name=f"controller-{request.change_id}",
            daemon=True,
        ).start()
        self._respond(handler, HTTPStatus.ACCEPTED, {"accepted": True})

    def _execute(self, request: ControllerRequest) -> None:
        try:
            self._runtime.apply(request)
        finally:
            self._operation_lock.release()

    @staticmethod
    def _respond(
        handler: BaseHTTPRequestHandler, status: HTTPStatus, value: dict[str, object]
    ) -> None:
        body = json.dumps(value, separators=(",", ":")).encode("utf-8")
        handler.send_response(status)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", str(len(body)))
        handler.end_headers()
        handler.wfile.write(body)


def _read_environment(path: Path) -> dict[str, str]:
    if not path.is_absolute() or not path.is_file() or path.stat().st_size > 64 * 1024:
        raise PersonalRuntimeError("Personal environment file is invalid")
    result: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line or "=" not in line:
            raise PersonalRuntimeError("Personal environment file is invalid")
        key, value = line.split("=", 1)
        if not key or key in result or "\x00" in value:
            raise PersonalRuntimeError("Personal environment file is invalid")
        result[key] = value
    return result


def complete_reinstall_recovery(
    paths: PersonalPaths,
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> None:
    capsule = paths.data_root / ".localrag-personal-reinstall.dpapi"
    marker = paths.install_root / "state" / "reinstall-recovery.json"
    if not capsule.exists() and not marker.exists():
        return
    module = paths.release_root / "ops" / "windows" / "v8a" / "RagPersonal.psm1"
    if not module.is_file():
        raise PersonalRuntimeError("Personal reinstall completion module is missing")
    environment = os.environ.copy()
    environment.update(
        {
            "RAG_REINSTALL_MODULE": str(module),
            "RAG_REINSTALL_INSTALL_ROOT": str(paths.install_root),
            "RAG_REINSTALL_DATA_ROOT": str(paths.data_root),
            "RAG_REINSTALL_RELEASE_ROOT": str(paths.release_root),
        }
    )
    command = (
        "$module=[Environment]::GetEnvironmentVariable('RAG_REINSTALL_MODULE');"
        "$install=[Environment]::GetEnvironmentVariable('RAG_REINSTALL_INSTALL_ROOT');"
        "$data=[Environment]::GetEnvironmentVariable('RAG_REINSTALL_DATA_ROOT');"
        "$release=[Environment]::GetEnvironmentVariable('RAG_REINSTALL_RELEASE_ROOT');"
        "Import-Module -LiteralPath $module -Force;"
        "Complete-RagPersonalReinstallRecovery -InstallRoot $install "
        "-DataRoot $data -ReleaseRoot $release | Out-Null"
    )
    result = runner(
        [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            command,
        ],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise PersonalRuntimeError("Personal reinstall completion validation failed")


def _command_path(name: str) -> Path:
    import shutil

    value = shutil.which(name)
    if value is None:
        raise PersonalRuntimeError(f"required development command is missing: {name}")
    return Path(value)


def _load_profiles(
    release_root: Path,
) -> tuple[
    SignedProfileResolver,
    dict[str, tuple[str, dict[str, str]]],
]:
    path = release_root / "ops" / "windows" / "v8a" / "capability-profiles.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    if (
        not isinstance(value, dict)
        or value.get("schema_version") != 1
        or value.get("catalog_id") != "local-rag-v8a-baseline"
        or not isinstance(value.get("profiles"), list)
    ):
        raise PersonalRuntimeError("capability profile catalog is invalid")
    profiles: list[ResolvedProfile] = []
    reverse: dict[str, tuple[str, dict[str, str]]] = {}
    for item in value["profiles"]:
        function = item.get("function")
        profile_id = item.get("profile_id")
        identity = item.get("model_identity")
        if not all(
            isinstance(entry, str) for entry in (function, profile_id, identity)
        ):
            raise PersonalRuntimeError("capability profile is invalid")
        if function == "generation":
            service, environment = "inference", (("GENERATION_MODEL", identity),)
        elif function == "reranking":
            service, environment = "inference", (("RERANKER_MODEL", identity),)
        elif function == "ocr":
            accelerator = item.get("accelerator_vendor")
            runtime_device = item.get("runtime_device")
            if accelerator == "cpu":
                runtime_device = "cpu"
            elif (
                not isinstance(runtime_device, str)
                or re.fullmatch(r"gpu:[0-9]+", runtime_device) is None
            ):
                continue
            service, environment = (
                "ocr",
                (
                    ("OCR_DEVICE", runtime_device),
                    ("OCR_PIPELINE_VERSION", "v1.6"),
                ),
            )
        else:
            continue
        profile = ResolvedProfile(profile_id, service, environment)
        profiles.append(profile)
        reverse[profile_id] = (service, dict(environment))
    resolver = SignedProfileResolver(
        tuple(profiles),
        {
            "balanced": (
                ("OCR_CPU_THREADS", "10"),
                ("OCR_PAGE_BATCH_SIZE", "8"),
                ("OCR_PROCESS_COUNT", "1"),
            )
        },
    )
    return resolver, reverse


def _specs(paths: PersonalPaths) -> tuple[ChildSpec, ...]:
    api_root = paths.release_root / "apps" / "api"
    web_root = paths.release_root / "apps" / "web"
    python = str(paths.api_python)
    config = paths.config_root
    return (
        ChildSpec(
            "inference",
            (python, "-m", "app.coordinator_server", "--port", "8100"),
            api_root,
            config / "inference.env",
            "http://127.0.0.1:8100/health",
            "COORDINATOR_SERVICE_TOKEN",
        ),
        ChildSpec(
            "ocr",
            (python, "-m", "app.ocr_service_server", "--port", "8101"),
            api_root,
            config / "ocr.env",
            "http://127.0.0.1:8101/health",
            "OCR_SERVICE_TOKEN",
        ),
        ChildSpec(
            "api",
            (python, "-m", "app.personal_server", "--port", "8000"),
            api_root,
            config / "api.env",
            "http://127.0.0.1:8000/ready",
        ),
        ChildSpec(
            "ingestion",
            (python, "-m", "app.processes.ingestion_worker"),
            api_root,
            config / "ingestion.env",
        ),
        ChildSpec(
            "deletion",
            (python, "-m", "app.processes.deletion_worker"),
            api_root,
            config / "deletion.env",
        ),
        ChildSpec(
            "web",
            (
                str(paths.node),
                str(web_root / ".next" / "standalone" / "apps" / "web" / "server.js"),
            ),
            web_root,
            config / "web.env",
            "http://127.0.0.1:3000/",
        ),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Local RAG Personal")
    parser.add_argument("--install-root", type=Path, required=True)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--release-root", type=Path, required=True)
    parser.add_argument("--development-source", action="store_true")
    parser.add_argument("--readiness-once", action="store_true")
    arguments = parser.parse_args()
    paths = PersonalPaths.resolve(
        install_root=arguments.install_root,
        data_root=arguments.data_root,
        release_root=arguments.release_root,
        development_source=arguments.development_source,
    )
    api_environment = _read_environment(paths.config_root / "api.env")
    token = api_environment.get("CONTROLLER_SERVICE_TOKEN", "")
    resolver, profile_values = _load_profiles(paths.release_root)
    api = ControllerApi("http://127.0.0.1:8000", token)
    backup = PersonalBackupService(
        install_root=paths.install_root,
        release_root=paths.release_root,
        data_root=paths.data_root,
        api_python=paths.api_python,
    )
    runtime = PersonalRuntime(
        paths,
        _specs(paths),
        api,
        resolver,
        profile_values,
        backup=backup,
        recovery_completion=lambda: complete_reinstall_recovery(paths),
    )
    if arguments.readiness_once:
        try:
            runtime.start_all()
        finally:
            runtime.stop_all()
        return
    controller = ControllerServer(runtime, token)
    stop = threading.Event()

    def request_stop(_signum: int, _frame: object) -> None:
        stop.set()

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)
    controller.serve_in_thread()
    try:
        runtime.start_all()
        while not stop.wait(0.25):
            for name, process in tuple(runtime._children.items()):
                if process.poll() is not None:
                    raise PersonalRuntimeError(f"Personal child {name} exited")
    finally:
        controller.close()
        runtime.stop_all()


if __name__ == "__main__":
    main()
