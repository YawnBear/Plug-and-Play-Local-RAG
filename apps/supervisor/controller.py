from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol  # noqa: UP035 - Python 3.12 runtime compatibility
from uuid import UUID

_NONCE = re.compile(r"^[A-Za-z0-9_-]{43,128}$")
_PROFILE_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{2,95}$")
_PRESET_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{1,31}$")
_MANUAL_OCR_PRESET = re.compile(
    r"^manual-t(?P<threads>[1-9][0-9]{0,2})-p(?P<processes>[1-9][0-9]?)$"
)
_MAX_MESSAGE_BYTES = 2048


class ControllerProtocolError(ValueError):
    """A caller attempted to cross the bounded local controller protocol."""


class ControllerAction(StrEnum):
    APPLY_CONFIGURATION = "apply_configuration"
    CREATE_BACKUP = "create_backup"
    VERIFY_BACKUP = "verify_backup"


class ControllerStage(StrEnum):
    PREFLIGHT = "preflight"
    BACKING_UP = "backing_up"
    DRAINING = "draining"
    APPLYING = "applying"
    RESTARTING = "restarting"
    VALIDATING = "validating"
    EFFECTIVE = "effective"
    FAILED = "failed"
    ROLLING_BACK = "rolling_back"
    ROLLED_BACK = "rolled_back"


@dataclass(frozen=True, slots=True)
class ControllerRequest:
    action: ControllerAction
    change_id: UUID
    nonce: str

    @classmethod
    def parse(cls, payload: bytes) -> ControllerRequest:
        if len(payload) > _MAX_MESSAGE_BYTES:
            raise ControllerProtocolError("controller message is too large")
        try:
            value = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ControllerProtocolError(
                "controller message is not valid JSON"
            ) from exc
        if not isinstance(value, dict) or set(value) != {
            "action",
            "change_id",
            "nonce",
        }:
            raise ControllerProtocolError("controller message fields are invalid")
        try:
            action = ControllerAction(value["action"])
            change_id = UUID(value["change_id"])
        except (TypeError, ValueError) as exc:
            raise ControllerProtocolError(
                "controller message values are invalid"
            ) from exc
        nonce = value["nonce"]
        if not isinstance(nonce, str) or _NONCE.fullmatch(nonce) is None:
            raise ControllerProtocolError("controller nonce is invalid")
        return cls(action=action, change_id=change_id, nonce=nonce)

    def encode(self) -> bytes:
        return json.dumps(
            {
                "action": self.action.value,
                "change_id": str(self.change_id),
                "nonce": self.nonce,
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")


@dataclass(frozen=True, slots=True)
class RuntimeConfiguration:
    generation_profile_id: str
    reranker_profile_id: str
    ocr_mode: str
    ocr_profile_id: str
    ocr_preset_id: str

    @classmethod
    def parse(cls, value: object) -> RuntimeConfiguration:
        expected = {
            "generation_profile_id",
            "reranker_profile_id",
            "ocr_mode",
            "ocr_profile_id",
            "ocr_preset_id",
        }
        if not isinstance(value, dict) or set(value) != expected:
            raise ControllerProtocolError("configuration fields are invalid")
        for key in (
            "generation_profile_id",
            "reranker_profile_id",
            "ocr_profile_id",
        ):
            if (
                not isinstance(value[key], str)
                or _PROFILE_ID.fullmatch(value[key]) is None
            ):
                raise ControllerProtocolError("configuration profile ID is invalid")
        if value["ocr_mode"] not in {"auto", "explicit"}:
            raise ControllerProtocolError("OCR selection mode is invalid")
        preset = value["ocr_preset_id"]
        if not isinstance(preset, str) or _PRESET_ID.fullmatch(preset) is None:
            raise ControllerProtocolError("OCR preset ID is invalid")
        return cls(**value)


@dataclass(frozen=True, slots=True)
class ResolvedProfile:
    profile_id: str
    service: str
    environment: tuple[tuple[str, str], ...]


class SignedProfileResolver:
    """Resolves catalog IDs to release-owned values; callers never provide env names."""

    def __init__(
        self,
        profiles: tuple[ResolvedProfile, ...],
        presets: dict[str, tuple[tuple[str, str], ...]],
    ) -> None:
        self._profiles = {profile.profile_id: profile for profile in profiles}
        self._presets = dict(presets)

    def resolve(self, configuration: RuntimeConfiguration) -> dict[str, dict[str, str]]:
        requested = (
            configuration.generation_profile_id,
            configuration.reranker_profile_id,
            configuration.ocr_profile_id,
        )
        try:
            profiles = [self._profiles[profile_id] for profile_id in requested]
        except KeyError as exc:
            raise ControllerProtocolError(
                "configuration references an unavailable signed profile"
            ) from exc
        preset = self._presets.get(configuration.ocr_preset_id)
        if preset is None:
            preset = _resolve_manual_ocr_preset(configuration.ocr_preset_id)
        values: dict[str, dict[str, str]] = {}
        for profile in profiles:
            service_values = values.setdefault(profile.service, {})
            for name, value in profile.environment:
                if name in service_values:
                    raise ControllerProtocolError("signed profile fields overlap")
                service_values[name] = value
        ocr_values = values.setdefault("ocr", {})
        for name, value in preset:
            if name in ocr_values:
                raise ControllerProtocolError("signed OCR preset fields overlap")
            ocr_values[name] = value
        ocr_values["OCR_SELECTION_MODE"] = configuration.ocr_mode
        return values


def _resolve_manual_ocr_preset(preset_id: str) -> tuple[tuple[str, str], ...]:
    match = _MANUAL_OCR_PRESET.fullmatch(preset_id)
    if match is None:
        raise ControllerProtocolError(
            "configuration references an unavailable signed profile"
        )
    cpu_threads = int(match.group("threads"))
    process_count = int(match.group("processes"))
    if not 1 <= cpu_threads <= 256 or not 1 <= process_count <= 16:
        raise ControllerProtocolError("OCR tuning is outside the supported range")
    return (
        ("OCR_CPU_THREADS", str(cpu_threads)),
        ("OCR_PAGE_BATCH_SIZE", "8"),
        ("OCR_PROCESS_COUNT", str(process_count)),
    )


class ControllerRuntime(Protocol):
    def drain(self, service: str) -> None: ...

    def replace_environment(self, service: str, values: dict[str, str]) -> None: ...

    def restart(self, service: str) -> None: ...

    def validate(self, service: str) -> None: ...


class ControllerReporter(Protocol):
    def stage(self, stage: ControllerStage, reason_code: str | None = None) -> None: ...


class ConfigurationController:
    """Applies only resolved profiles and restores the prior values on any failure."""

    def __init__(
        self,
        runtime: ControllerRuntime,
        resolver: SignedProfileResolver,
        reporter: ControllerReporter,
    ) -> None:
        self._runtime = runtime
        self._resolver = resolver
        self._reporter = reporter

    def apply(
        self,
        desired: RuntimeConfiguration,
        prior: RuntimeConfiguration,
    ) -> None:
        self._reporter.stage(ControllerStage.PREFLIGHT)
        desired_values = self._resolver.resolve(desired)
        prior_values = self._resolver.resolve(prior)
        affected = tuple(
            sorted(
                service
                for service in set(desired_values) | set(prior_values)
                if desired_values.get(service) != prior_values.get(service)
            )
        )
        if not affected:
            raise ControllerProtocolError("configuration change has no runtime impact")
        try:
            self._reporter.stage(ControllerStage.DRAINING)
            for service in affected:
                self._runtime.drain(service)
            self._reporter.stage(ControllerStage.APPLYING)
            for service in affected:
                self._runtime.replace_environment(service, desired_values[service])
            self._reporter.stage(ControllerStage.RESTARTING)
            for service in affected:
                self._runtime.restart(service)
            self._reporter.stage(ControllerStage.VALIDATING)
            for service in affected:
                self._runtime.validate(service)
        except Exception:
            self._reporter.stage(ControllerStage.ROLLING_BACK, "application_failed")
            for service in affected:
                self._runtime.replace_environment(service, prior_values[service])
                self._runtime.restart(service)
                self._runtime.validate(service)
            self._reporter.stage(ControllerStage.ROLLED_BACK, "prior_revision_restored")
            raise
        self._reporter.stage(ControllerStage.EFFECTIVE)
