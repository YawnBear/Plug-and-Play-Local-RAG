from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest
from apps.supervisor.controller import (
    ControllerAction,
    ControllerRequest,
    ControllerStage,
    ResolvedProfile,
    RuntimeConfiguration,
    SignedProfileResolver,
)
from apps.supervisor.personal_backup import (
    BackupExport,
    PersonalBackupError,
    PersonalBackupService,
)
from apps.supervisor.personal_runtime import (
    ChildSpec,
    PersonalPaths,
    PersonalRuntime,
    PersonalRuntimeError,
    complete_reinstall_recovery,
)


class FakeProcess:
    def __init__(self) -> None:
        self.code: int | None = None

    def poll(self) -> int | None:
        return self.code

    def terminate(self) -> None:
        self.code = 0

    def wait(self, timeout: float | None = None) -> int:
        self.code = 0
        return 0

    def kill(self) -> None:
        self.code = 1

    def send_signal(self, _signal: int) -> None:
        self.code = 0


class Api:
    def __init__(self) -> None:
        self.stages: list[tuple[ControllerStage, str | None]] = []
        self.smokes = 0

    def claim(
        self, _request: ControllerRequest
    ) -> tuple[RuntimeConfiguration, RuntimeConfiguration]:
        prior = RuntimeConfiguration(
            generation_profile_id="generation.test",
            reranker_profile_id="reranker.test",
            ocr_mode="auto",
            ocr_profile_id="ocr.test",
            ocr_preset_id="balanced",
        )
        return prior, RuntimeConfiguration(
            generation_profile_id=prior.generation_profile_id,
            reranker_profile_id=prior.reranker_profile_id,
            ocr_mode="explicit",
            ocr_profile_id=prior.ocr_profile_id,
            ocr_preset_id=prior.ocr_preset_id,
        )

    def stage(
        self,
        _request: ControllerRequest,
        stage: ControllerStage,
        reason_code: str | None = None,
    ) -> None:
        self.stages.append((stage, reason_code))

    def smoke(self, _profile_id: str) -> None:
        self.smokes += 1
        if self.smokes == 1:
            raise RuntimeError("forced readiness failure")


def _runtime(tmp_path: Path) -> tuple[PersonalRuntime, Api, list[FakeProcess]]:
    install = tmp_path / "install"
    release = tmp_path / "release"
    data = tmp_path / "data"
    config = install / "config"
    api_root = release / "apps" / "api"
    for path in (config, api_root, data):
        path.mkdir(parents=True, exist_ok=True)
    (config / "ocr.env").write_text(
        "OCR_SERVICE_TOKEN=" + "o" * 32 + "\n", encoding="utf-8"
    )
    paths = PersonalPaths(
        install_root=install,
        release_root=release,
        config_root=config,
        data_root=data,
        api_python=Path("C:/python.exe"),
        node=Path("C:/node.exe"),
    )
    spec = ChildSpec(
        "ocr",
        ("python", "-m", "ocr"),
        api_root,
        config / "ocr.env",
        "http://127.0.0.1:8101/health",
        "OCR_SERVICE_TOKEN",
    )
    profiles = (
        ResolvedProfile("generation.test", "inference", (("GENERATION_MODEL", "g"),)),
        ResolvedProfile("reranker.test", "inference", (("RERANKER_MODEL", "r"),)),
        ResolvedProfile(
            "ocr.test",
            "ocr",
            (("OCR_DEVICE", "cpu"), ("OCR_PIPELINE_VERSION", "v1.6")),
        ),
    )
    resolver = SignedProfileResolver(
        profiles,
        {
            "balanced": (
                ("OCR_CPU_THREADS", "10"),
                ("OCR_PAGE_BATCH_SIZE", "8"),
                ("OCR_PROCESS_COUNT", "1"),
            )
        },
    )
    api = Api()
    processes: list[FakeProcess] = []

    def popen(*_args: object, **_kwargs: object) -> FakeProcess:
        process = FakeProcess()
        processes.append(process)
        return process

    runtime = PersonalRuntime(
        paths,
        (spec,),
        api,  # type: ignore[arg-type]
        resolver,
        {
            "ocr.test": (
                "ocr",
                {"OCR_DEVICE": "cpu", "OCR_PIPELINE_VERSION": "v1.6"},
            )
        },
        popen=popen,
        sleeper=lambda _seconds: None,
    )
    runtime._service_post = lambda *_args: None  # type: ignore[method-assign]
    runtime._wait_ready = lambda *_args: None  # type: ignore[method-assign]
    runtime._children["ocr"] = FakeProcess()
    return runtime, api, processes


def test_personal_runtime_forced_failure_restarts_and_restores_prior_values(
    tmp_path: Path,
) -> None:
    runtime, api, processes = _runtime(tmp_path)
    request = ControllerRequest(
        ControllerAction.APPLY_CONFIGURATION,
        uuid4(),
        "n" * 43,
    )
    with pytest.raises(RuntimeError, match="forced readiness failure"):
        runtime.apply(request)

    persisted = json.loads(
        (runtime.paths.config_root / "runtime-overrides.json").read_text(
            encoding="utf-8"
        )
    )
    assert persisted["services"]["ocr"]["OCR_SELECTION_MODE"] == "auto"
    assert len(processes) == 2
    assert api.stages[-1] == (
        ControllerStage.ROLLED_BACK,
        "prior_revision_restored",
    )


def test_reinstall_completion_runs_only_after_all_children_are_ready(
    tmp_path: Path,
) -> None:
    runtime, _api, _processes = _runtime(tmp_path)
    events: list[str] = []
    runtime._specs = {
        name: runtime._specs["ocr"]
        for name in ("inference", "ocr", "api", "ingestion", "deletion", "web")
    }
    runtime._children.clear()
    runtime._start = lambda name: events.append(f"ready:{name}")  # type: ignore[method-assign]
    runtime._recovery_completion = lambda: events.append("completed")

    runtime.start_all()

    assert events == [
        "ready:inference",
        "ready:ocr",
        "ready:api",
        "ready:ingestion",
        "ready:deletion",
        "ready:web",
        "completed",
    ]


def test_failed_full_start_does_not_complete_reinstall(tmp_path: Path) -> None:
    runtime, _api, _processes = _runtime(tmp_path)
    completed = False
    runtime._specs = {
        name: runtime._specs["ocr"]
        for name in ("inference", "ocr", "api", "ingestion", "deletion", "web")
    }
    runtime._children.clear()

    def start(name: str) -> None:
        if name == "web":
            raise PersonalRuntimeError("forced full-start failure")

    def complete() -> None:
        nonlocal completed
        completed = True

    runtime._start = start  # type: ignore[method-assign]
    runtime._recovery_completion = complete

    with pytest.raises(PersonalRuntimeError, match="forced full-start failure"):
        runtime.start_all()

    assert completed is False


def test_reinstall_completion_uses_fixed_module_command(tmp_path: Path) -> None:
    install = tmp_path / "install"
    release = tmp_path / "release"
    data = tmp_path / "data"
    for path in (
        install / "state",
        release / "ops" / "windows" / "v8a",
        data,
    ):
        path.mkdir(parents=True, exist_ok=True)
    (install / "state" / "reinstall-recovery.json").write_text("{}", encoding="utf-8")
    (data / ".localrag-personal-reinstall.dpapi").write_bytes(b"ciphertext")
    (release / "ops" / "windows" / "v8a" / "RagPersonal.psm1").write_text(
        "", encoding="utf-8"
    )
    paths = PersonalPaths(
        install_root=install.resolve(),
        release_root=release.resolve(),
        config_root=(install / "config").resolve(),
        data_root=data.resolve(),
        api_python=Path("C:/python.exe"),
        node=Path("C:/node.exe"),
    )
    calls: list[tuple[list[str], dict[str, str]]] = []

    def runner(command: list[str], **kwargs: object) -> object:
        calls.append((command, kwargs["env"]))  # type: ignore[arg-type]
        return type("Result", (), {"returncode": 0})()

    complete_reinstall_recovery(paths, runner=runner)  # type: ignore[arg-type]

    assert len(calls) == 1
    command, environment = calls[0]
    assert command[:6] == [
        "powershell.exe",
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-Command",
    ]
    assert environment["RAG_REINSTALL_INSTALL_ROOT"] == str(paths.install_root)
    assert "Complete-RagPersonalReinstallRecovery" in command[-1]


def test_backup_destination_never_accepts_application_or_data_paths(
    tmp_path: Path,
) -> None:
    install = tmp_path / "install"
    release = tmp_path / "release"
    data = tmp_path / "data"
    for path in (install / "state", release, data):
        path.mkdir(parents=True, exist_ok=True)
    (install / "state" / "installation-journal.json").write_text(
        json.dumps({"compose_project": "localrag-personal-abcdef123456"}),
        encoding="utf-8",
    )
    service = PersonalBackupService(
        install_root=install,
        release_root=release,
        data_root=data,
        api_python=Path("C:/python.exe"),
        select_destination=lambda: data,
    )
    with pytest.raises(PersonalBackupError, match="outside"):
        service.choose_destination()


def test_verified_backup_catalog_keeps_history_without_deleting_bundles(
    tmp_path: Path,
) -> None:
    install = tmp_path / "install"
    release = tmp_path / "release"
    data = tmp_path / "data"
    bundle = tmp_path / "backups" / "LocalRAG-backup-test"
    for path in (install / "state", release, data, bundle):
        path.mkdir(parents=True, exist_ok=True)
    (install / "state" / "installation-journal.json").write_text(
        json.dumps({"compose_project": "localrag-personal-abcdef123456"}),
        encoding="utf-8",
    )
    evidence = bundle / "restore-verification.json"
    evidence.write_text("{}", encoding="utf-8")
    service = PersonalBackupService(
        install_root=install,
        release_root=release,
        data_root=data,
        api_python=Path("C:/python.exe"),
    )
    identifier = uuid4()
    service._record_verified_backup(
        backup=BackupExport(bundle, "a" * 64, "b" * 64, 10, 20),
        backup_run_id=identifier,
        evidence=evidence,
        expected_revision="0014_restart_without_backup",
    )
    catalog = json.loads((data / "backup-catalog.json").read_text("utf-8"))
    assert catalog["retention"] == {
        "mode": "keep_all",
        "maximum_verified_backups": None,
    }
    assert catalog["entries"][0]["backup_run_id"] == str(identifier)
    assert bundle.is_dir()
