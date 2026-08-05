from __future__ import annotations

import json
from uuid import uuid4

import pytest

from apps.supervisor.controller import (
    ConfigurationController,
    ControllerAction,
    ControllerProtocolError,
    ControllerRequest,
    ControllerStage,
    ResolvedProfile,
    RuntimeConfiguration,
    SignedProfileResolver,
)


def configuration(*, ocr_mode: str = "auto") -> RuntimeConfiguration:
    return RuntimeConfiguration(
        generation_profile_id="generation.qwen3-8b.ollama.windows-x64",
        reranker_profile_id="reranking.bge-v2-m3.cpu.windows-x64",
        ocr_mode=ocr_mode,
        ocr_profile_id="ocr.paddleocr-vl-1.6.cpu.windows-x64",
        ocr_preset_id="balanced",
    )


def resolver() -> SignedProfileResolver:
    return SignedProfileResolver(
        (
            ResolvedProfile(
                configuration().generation_profile_id,
                "coordinator",
                (("GENERATION_MODEL", "qwen3:8b"),),
            ),
            ResolvedProfile(
                configuration().reranker_profile_id,
                "coordinator",
                (("RERANKER_MODEL", "BAAI/bge-reranker-v2-m3"),),
            ),
            ResolvedProfile(
                configuration().ocr_profile_id,
                "ocr",
                (("OCR_DEVICE", "cpu"),),
            ),
        ),
        {
            "balanced": (
                ("OCR_CPU_THREADS", "10"),
                ("OCR_PAGE_BATCH_SIZE", "8"),
                ("OCR_PROCESS_COUNT", "1"),
            )
        },
    )


def test_controller_request_accepts_only_uuid_nonce_and_fixed_action() -> None:
    request = ControllerRequest(
        action=ControllerAction.APPLY_CONFIGURATION,
        change_id=uuid4(),
        nonce="A" * 43,
    )
    assert ControllerRequest.parse(request.encode()) == request
    for forbidden in ("command", "path", "url", "environment", "arguments"):
        value = json.loads(request.encode())
        value[forbidden] = "powershell.exe"
        with pytest.raises(ControllerProtocolError):
            ControllerRequest.parse(json.dumps(value).encode())
    value = json.loads(request.encode())
    value["action"] = "powershell.exe"
    with pytest.raises(ControllerProtocolError):
        ControllerRequest.parse(json.dumps(value).encode())
    value = json.loads(request.encode())
    value["nonce"] = "; Start-Process powershell"
    with pytest.raises(ControllerProtocolError):
        ControllerRequest.parse(json.dumps(value).encode())


def test_runtime_configuration_rejects_unbounded_fields() -> None:
    value = (
        configuration().__dict__
        if hasattr(configuration(), "__dict__")
        else {
            field: getattr(configuration(), field)
            for field in configuration().__dataclass_fields__
        }
    )
    value["OLLAMA_BASE_URL"] = "https://attacker.invalid"
    with pytest.raises(ControllerProtocolError):
        RuntimeConfiguration.parse(value)


class Runtime:
    def __init__(self, fail_validation: bool = False) -> None:
        self.fail_validation = fail_validation
        self.calls: list[tuple[str, str, object | None]] = []

    def drain(self, service: str) -> None:
        self.calls.append(("drain", service, None))

    def replace_environment(self, service: str, values: dict[str, str]) -> None:
        self.calls.append(("environment", service, dict(values)))

    def restart(self, service: str) -> None:
        self.calls.append(("restart", service, None))

    def validate(self, service: str) -> None:
        self.calls.append(("validate", service, None))
        if self.fail_validation:
            self.fail_validation = False
            raise RuntimeError("smoke test failed")


class Reporter:
    def __init__(self) -> None:
        self.stages: list[tuple[ControllerStage, str | None]] = []

    def stage(self, stage: ControllerStage, reason_code: str | None = None) -> None:
        self.stages.append((stage, reason_code))


def test_failed_application_restores_and_validates_prior_configuration() -> None:
    runtime = Runtime(fail_validation=True)
    reporter = Reporter()
    controller = ConfigurationController(runtime, resolver(), reporter)
    with pytest.raises(RuntimeError, match="smoke test failed"):
        controller.apply(configuration(ocr_mode="explicit"), configuration())
    assert reporter.stages[-2:] == [
        (ControllerStage.ROLLING_BACK, "application_failed"),
        (ControllerStage.ROLLED_BACK, "prior_revision_restored"),
    ]
    assert runtime.calls[-2][0] == "restart"
    assert runtime.calls[-1][0] == "validate"


def test_unknown_profile_cannot_resolve_to_runtime_values() -> None:
    desired = RuntimeConfiguration(
        generation_profile_id="generation.unknown",
        reranker_profile_id=configuration().reranker_profile_id,
        ocr_mode="auto",
        ocr_profile_id=configuration().ocr_profile_id,
        ocr_preset_id="balanced",
    )
    with pytest.raises(ControllerProtocolError, match="unavailable signed profile"):
        resolver().resolve(desired)


def test_manual_ocr_tuning_resolves_only_bounded_numeric_environment() -> None:
    desired = configuration(ocr_mode="explicit")
    desired = RuntimeConfiguration(
        generation_profile_id=desired.generation_profile_id,
        reranker_profile_id=desired.reranker_profile_id,
        ocr_mode=desired.ocr_mode,
        ocr_profile_id=desired.ocr_profile_id,
        ocr_preset_id="manual-t12-p3",
    )

    values = resolver().resolve(desired)["ocr"]

    assert values["OCR_CPU_THREADS"] == "12"
    assert values["OCR_PROCESS_COUNT"] == "3"
    assert values["OCR_PAGE_BATCH_SIZE"] == "8"
