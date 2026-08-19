from __future__ import annotations

import hashlib
import ipaddress
import json
import re
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from typing import Any


class ManifestError(ValueError):
    """Raised when a deployment manifest violates the Windows contract."""


LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})
REQUIRED_SERVICES = frozenset(
    {"caddy", "web", "api", "ingestion", "deletion", "inference", "ocr"}
)
DEPLOYMENT_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
SERVICE_IDENTITY_PATTERN = re.compile(r"^\.\\Rag[A-Za-z0-9]+Svc$")
ENVIRONMENT_NAME_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{0,127}$")
CADDY_VERSION = "2.11.3"
CADDY_EXECUTABLE_SHA256 = (
    "67514bc0449ae9b1465cf3d59ab269cb451e8ed88d991e461b24d1337b67f536"
)
CADDY_ARCHIVE_SHA512 = (
    "338f5557a1554677875b79dbc4b10d008781111ad29223811e64217936fa5d586"
    "02ddd54724ef1cb1473b7ec07805cf5286d6aa1e810febde7e36daf497d791f"
)


@dataclass(frozen=True, slots=True)
class RestartPolicy:
    maximum_restarts: int
    window_seconds: int
    backoff_seconds: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class Service:
    name: str
    executable: str
    arguments: tuple[str, ...]
    working_directory: str
    identity: str
    listen_host: str | None
    listen_port: int | None
    dependencies: tuple[str, ...]
    environment_file: str | None
    identity_secret_file: str | None = None
    environment_keys: tuple[str, ...] = ()
    readiness_url: str | None = None
    readiness_token_environment: str | None = None
    readiness_timeout_seconds: int = 15


@dataclass(frozen=True, slots=True)
class DeploymentManifest:
    schema_version: int
    deployment_id: str
    canonical_host: str
    firewall_profile: str
    services: tuple[Service, ...]
    restart_policy: RestartPolicy
    backup_state: str
    backup_destination: str | None
    update_state: str
    update_public_key_sha256: str | None
    caddy_version: str | None
    caddy_archive_sha512: str | None
    caddy_sha256: str | None
    deployment_state: str
    deployment_blockers: tuple[str, ...]
    prerequisite_checks: tuple[str, ...]
    product_profile: str = "team_lan"

    @classmethod
    def load(cls, path: Path) -> DeploymentManifest:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ManifestError(f"cannot read deployment manifest: {path}") from exc
        if not isinstance(value, dict):
            raise ManifestError("deployment manifest root must be an object")
        return cls.from_dict(value)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> DeploymentManifest:
        profile = value.get("product_profile", "team_lan")
        allowed_keys = {
            "schema_version",
            "deployment_id",
            "canonical_host",
            "firewall_profile",
            "services",
            "restart_policy",
            "backup",
            "updates",
            "caddy",
            "deployment_readiness",
        }
        if "product_profile" in value:
            allowed_keys.add("product_profile")
        _require_exact_keys(
            value,
            allowed_keys,
            "deployment manifest",
        )
        if profile not in {"team_lan", "team_lan_preview_unsigned"}:
            raise ManifestError(
                "product_profile must be team_lan or team_lan_preview_unsigned"
            )
        if value["schema_version"] != 2:
            raise ManifestError("schema_version must be 2")
        if value["canonical_host"] != "rag.home.arpa":
            raise ManifestError("canonical_host must be rag.home.arpa")
        if value["firewall_profile"] != "Private":
            raise ManifestError("only the Private firewall profile is permitted")
        deployment_id = _required_string(value["deployment_id"], "deployment_id")
        if DEPLOYMENT_ID_PATTERN.fullmatch(deployment_id) is None:
            raise ManifestError("deployment_id is unsafe for Windows global objects")

        services_value = value["services"]
        if not isinstance(services_value, list):
            raise ManifestError("services must be an array")
        services = tuple(
            _parse_service(item, product_profile=profile) for item in services_value
        )
        names = [item.name for item in services]
        if len(names) != len(set(names)):
            raise ManifestError("service names must be unique")
        if set(names) != REQUIRED_SERVICES:
            raise ManifestError(
                "services must contain exactly caddy, web, api, ingestion, "
                "deletion, inference, and ocr"
            )
        _validate_listeners(services)
        _validate_dependencies(services)

        policy_value = value["restart_policy"]
        if not isinstance(policy_value, dict):
            raise ManifestError("restart_policy must be an object")
        _require_exact_keys(
            policy_value,
            {"maximum_restarts", "window_seconds", "backoff_seconds"},
            "restart_policy",
        )
        maximum_restarts = policy_value["maximum_restarts"]
        window_seconds = policy_value["window_seconds"]
        backoff = policy_value["backoff_seconds"]
        if (
            not isinstance(maximum_restarts, int)
            or isinstance(maximum_restarts, bool)
            or not 1 <= maximum_restarts <= 10
        ):
            raise ManifestError("maximum_restarts must be an integer from 1 to 10")
        if (
            not isinstance(window_seconds, int)
            or isinstance(window_seconds, bool)
            or not 60 <= window_seconds <= 3600
        ):
            raise ManifestError("window_seconds must be an integer from 60 to 3600")
        if (
            not isinstance(backoff, list)
            or not backoff
            or any(
                not isinstance(item, int)
                or isinstance(item, bool)
                or item < 1
                or item > 300
                for item in backoff
            )
        ):
            raise ManifestError("backoff_seconds must contain integers from 1 to 300")

        backup = _state_config(value["backup"], "backup")
        if backup["state"] != "not_configured" or backup["destination"] is not None:
            raise ManifestError(
                "production backup must remain not_configured without a destination"
            )
        updates = _state_config(
            value["updates"], "updates", key_name="public_key_sha256"
        )
        caddy = value["caddy"]
        if not isinstance(caddy, dict):
            raise ManifestError("caddy must be an object")
        _require_exact_keys(
            caddy,
            {"version", "archive_sha512", "executable_sha256"},
            "caddy",
        )
        caddy_version = _optional_string(caddy["version"], "caddy.version")
        caddy_archive_sha512 = _optional_sha512(
            caddy["archive_sha512"], "caddy.archive_sha512"
        )
        caddy_sha = _optional_sha256(
            caddy["executable_sha256"], "caddy.executable_sha256"
        )
        if (
            caddy_version != CADDY_VERSION
            or caddy_archive_sha512 != CADDY_ARCHIVE_SHA512
            or caddy_sha != CADDY_EXECUTABLE_SHA256
        ):
            raise ManifestError(
                f"Caddy must pin version {CADDY_VERSION} and its approved "
                "SHA-256 together"
            )
        readiness = value["deployment_readiness"]
        if not isinstance(readiness, dict):
            raise ManifestError("deployment_readiness must be an object")
        _require_exact_keys(
            readiness,
            {"state", "blockers", "prerequisite_checks"},
            "deployment_readiness",
        )
        if readiness["state"] not in {"installable", "installed"}:
            raise ManifestError("deployment readiness must be installable or installed")
        blockers = _string_array(readiness["blockers"], "deployment_readiness.blockers")
        prerequisite_checks = _string_array(
            readiness["prerequisite_checks"],
            "deployment_readiness.prerequisite_checks",
        )
        required_checks = {
            "elevated_installer",
            "dependency_evidence",
            "signed_release_chain",
            "caddy_artifact_hash",
            "openssl_artifact_hash",
            "service_accounts_and_acls",
            "private_ca_and_leaf_certificates",
            "certificate_semantics_and_renewal",
            "firewall_rule",
            "ocr_outbound_firewall",
            "rag_supervisor_service",
            "full_graph_network_evidence",
        }
        if profile == "team_lan_preview_unsigned":
            required_checks = (required_checks - {"signed_release_chain"}) | {
                "unsigned_inventory",
                "preview_profile_isolation",
                "rfc1918_ipv4",
            }
        if set(prerequisite_checks) != required_checks:
            raise ManifestError("deployment prerequisite checks are incomplete")
        if readiness["state"] == "installable" and blockers:
            raise ManifestError("installable deployment cannot contain stale blockers")
        if readiness["state"] == "installed" and blockers:
            raise ManifestError("installed deployment cannot contain blockers")

        return cls(
            schema_version=2,
            deployment_id=deployment_id,
            canonical_host="rag.home.arpa",
            firewall_profile="Private",
            services=services,
            restart_policy=RestartPolicy(
                maximum_restarts=maximum_restarts,
                window_seconds=window_seconds,
                backoff_seconds=tuple(backoff),
            ),
            backup_state=backup["state"],
            backup_destination=backup["destination"],
            update_state=updates["state"],
            update_public_key_sha256=updates["public_key_sha256"],
            caddy_version=caddy_version,
            caddy_archive_sha512=caddy_archive_sha512,
            caddy_sha256=caddy_sha,
            deployment_state=readiness["state"],
            deployment_blockers=blockers,
            prerequisite_checks=prerequisite_checks,
            product_profile=profile,
        )

    def digest(self) -> str:
        value = {
            "schema_version": self.schema_version,
            "deployment_id": self.deployment_id,
            "canonical_host": self.canonical_host,
            "firewall_profile": self.firewall_profile,
            "services": [
                {
                    "name": item.name,
                    "executable": item.executable,
                    "arguments": list(item.arguments),
                    "working_directory": item.working_directory,
                    "identity": item.identity,
                    "listen_host": item.listen_host,
                    "listen_port": item.listen_port,
                    "dependencies": list(item.dependencies),
                    "environment_file": item.environment_file,
                    "identity_secret_file": item.identity_secret_file,
                    "environment_keys": list(item.environment_keys),
                    "readiness": {
                        "url": item.readiness_url,
                        "token_environment": item.readiness_token_environment,
                        "timeout_seconds": item.readiness_timeout_seconds,
                    },
                }
                for item in self.services
            ],
            "restart_policy": {
                "maximum_restarts": self.restart_policy.maximum_restarts,
                "window_seconds": self.restart_policy.window_seconds,
                "backoff_seconds": list(self.restart_policy.backoff_seconds),
            },
            "backup": {
                "state": self.backup_state,
                "destination": self.backup_destination,
            },
            "updates": {
                "state": self.update_state,
                "public_key_sha256": self.update_public_key_sha256,
            },
            "caddy": {
                "version": self.caddy_version,
                "archive_sha512": self.caddy_archive_sha512,
                "executable_sha256": self.caddy_sha256,
            },
            "deployment_readiness": {
                "state": self.deployment_state,
                "blockers": list(self.deployment_blockers),
                "prerequisite_checks": list(self.prerequisite_checks),
            },
        }
        if self.product_profile != "team_lan":
            value["product_profile"] = self.product_profile
        encoded = json.dumps(
            value, ensure_ascii=True, separators=(",", ":"), sort_keys=True
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def assert_runnable(self) -> None:
        if self.deployment_state != "installed":
            raise ManifestError(
                "deployment is not installed; run the elevated installer and use "
                "its protected installed manifest"
            )


def _parse_service(value: Any, *, product_profile: str = "team_lan") -> Service:
    if not isinstance(value, dict):
        raise ManifestError("each service must be an object")
    _require_exact_keys(
        value,
        {
            "name",
            "executable",
            "arguments",
            "working_directory",
            "identity",
            "listen_host",
            "listen_port",
            "dependencies",
            "environment_file",
            "identity_secret_file",
            "environment_keys",
            "readiness",
        },
        "service",
    )
    name = _required_string(value["name"], "service.name")
    executable = _required_string(value["executable"], f"{name}.executable")
    working_directory = _required_string(
        value["working_directory"], f"{name}.working_directory"
    )
    identity = _required_string(value["identity"], f"{name}.identity")
    if identity.upper().startswith("NT SERVICE\\"):
        raise ManifestError(
            f"{name}.identity cannot claim an NT SERVICE identity because the "
            "supervisor launches children with explicit user tokens"
        )
    if SERVICE_IDENTITY_PATTERN.fullmatch(identity) is None:
        raise ManifestError(
            f"{name}.identity must be a dedicated local Rag*Svc account"
        )
    arguments = _string_array(value["arguments"], f"{name}.arguments")
    if name == "caddy":
        executable_path = PureWindowsPath(executable)
        work_path = PureWindowsPath(working_directory)
        if (
            executable_path.name.casefold() != "caddy.exe"
            or executable_path.parent != work_path
            or (
                product_profile == "team_lan"
                and (
                    "signed-stage" not in {part.casefold() for part in work_path.parts}
                    or not work_path.name.startswith("release-")
                )
            )
            or (
                product_profile == "team_lan_preview_unsigned"
                and work_path
                != PureWindowsPath(r"C:\Program Files\LocalRAG\current")
            )
            or arguments != ("run", "--config", str(work_path / "Caddyfile"))
        ):
            raise ManifestError(
                "Caddy executable and config must be co-located in the profile's "
                "immutable release stage"
            )
    dependencies = _string_array(value["dependencies"], f"{name}.dependencies")
    environment_file = _optional_string(
        value["environment_file"], f"{name}.environment_file"
    )
    if environment_file is None:
        raise ManifestError(f"{name}.environment_file is required")
    identity_secret_file = _optional_string(
        value["identity_secret_file"], f"{name}.identity_secret_file"
    )
    if identity_secret_file is None:
        raise ManifestError(f"{name}.identity_secret_file is required")
    environment_keys = _string_array(
        value["environment_keys"], f"{name}.environment_keys"
    )
    if (
        len(environment_keys) != len(set(environment_keys))
        or any(
            ENVIRONMENT_NAME_PATTERN.fullmatch(item) is None
            for item in environment_keys
        )
        or "RAG_WINDOWS_ACCOUNT_PASSWORD" in environment_keys
    ):
        raise ManifestError(f"{name}.environment_keys are invalid")
    readiness = value["readiness"]
    if not isinstance(readiness, dict):
        raise ManifestError(f"{name}.readiness must be an object")
    _require_exact_keys(
        readiness,
        {"url", "token_environment", "timeout_seconds"},
        f"{name}.readiness",
    )
    readiness_url = _optional_string(readiness["url"], f"{name}.readiness.url")
    readiness_token = _optional_string(
        readiness["token_environment"], f"{name}.readiness.token_environment"
    )
    if readiness_token is not None and readiness_token not in environment_keys:
        raise ManifestError(
            f"{name}.readiness.token_environment must be an allowed environment key"
        )
    readiness_timeout = readiness["timeout_seconds"]
    if (
        not isinstance(readiness_timeout, int)
        or isinstance(readiness_timeout, bool)
        or not 1 <= readiness_timeout <= 300
    ):
        raise ManifestError(f"{name}.readiness.timeout_seconds is invalid")
    readiness_parsed = None
    if readiness_url is not None:
        from urllib.parse import urlsplit

        parsed = urlsplit(readiness_url)
        readiness_parsed = parsed
        if (
            parsed.scheme not in {"http", "https"}
            or parsed.hostname not in LOOPBACK_HOSTS
            or parsed.username is not None
            or parsed.password is not None
            or parsed.fragment
        ):
            raise ManifestError(f"{name}.readiness.url must be a loopback HTTP URL")
    host = value["listen_host"]
    port = value["listen_port"]
    if (host is None) != (port is None):
        raise ManifestError(f"{name} listener host and port must be set together")
    if host is not None:
        host = _required_string(host, f"{name}.listen_host")
        if (
            not isinstance(port, int)
            or isinstance(port, bool)
            or not 1 <= port <= 65535
        ):
            raise ManifestError(f"{name}.listen_port is invalid")
    if readiness_parsed is not None and (
        host is None
        or port is None
        or readiness_parsed.hostname != host
        or (
            readiness_parsed.port or (443 if readiness_parsed.scheme == "https" else 80)
        )
        != port
    ):
        raise ManifestError(
            f"{name}.readiness.url must match the declared loopback listener"
        )
    return Service(
        name=name,
        executable=executable,
        arguments=arguments,
        working_directory=working_directory,
        identity=identity,
        listen_host=host,
        listen_port=port,
        dependencies=dependencies,
        environment_file=environment_file,
        identity_secret_file=identity_secret_file,
        environment_keys=environment_keys,
        readiness_url=readiness_url,
        readiness_token_environment=readiness_token,
        readiness_timeout_seconds=readiness_timeout,
    )


def _validate_listeners(services: tuple[Service, ...]) -> None:
    for service in services:
        if service.name == "caddy":
            if service.listen_host != "0.0.0.0" or service.listen_port != 443:
                raise ManifestError("Caddy must be the sole LAN listener on TCP 443")
            continue
        if service.listen_host is None:
            continue
        if service.listen_host not in LOOPBACK_HOSTS:
            try:
                if not ipaddress.ip_address(service.listen_host).is_loopback:
                    raise ManifestError(
                        f"{service.name} must listen only on IPv4/IPv6 loopback"
                    )
            except ValueError as exc:
                raise ManifestError(
                    f"{service.name} listen_host must be a literal loopback address"
                ) from exc
        if service.listen_port == 443:
            raise ManifestError("only Caddy may listen on TCP 443")


def _validate_dependencies(services: tuple[Service, ...]) -> None:
    names = {service.name for service in services}
    for service in services:
        unknown = set(service.dependencies) - names
        if unknown:
            raise ManifestError(
                f"{service.name} has unknown dependencies: {', '.join(sorted(unknown))}"
            )
        if service.name in service.dependencies:
            raise ManifestError(f"{service.name} cannot depend on itself")
        if len(service.dependencies) != len(set(service.dependencies)):
            raise ManifestError(f"{service.name} dependencies must be unique")

    pending = {service.name: set(service.dependencies) for service in services}
    while pending:
        ready = {name for name, dependencies in pending.items() if not dependencies}
        if not ready:
            raise ManifestError("service dependencies contain a cycle")
        for name in ready:
            pending.pop(name)
        for dependencies in pending.values():
            dependencies.difference_update(ready)


def _state_config(
    value: Any, label: str, *, key_name: str = "destination"
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ManifestError(f"{label} must be an object")
    _require_exact_keys(value, {"state", key_name}, label)
    state = value["state"]
    if state not in {"not_configured", "configured"}:
        raise ManifestError(f"{label}.state is invalid")
    configured_value = value[key_name]
    if configured_value is not None:
        if key_name.endswith("sha256"):
            configured_value = _optional_sha256(configured_value, f"{label}.{key_name}")
        else:
            configured_value = _required_string(configured_value, f"{label}.{key_name}")
    if (state == "configured") != (configured_value is not None):
        raise ManifestError(f"{label} state does not match {key_name}")
    return {"state": state, key_name: configured_value}


def _require_exact_keys(value: dict[str, Any], keys: set[str], label: str) -> None:
    if set(value) != keys:
        raise ManifestError(f"{label} fields are invalid")


def _required_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ManifestError(f"{label} must be a nonempty string")
    return value


def _optional_string(value: Any, label: str) -> str | None:
    if value is None:
        return None
    return _required_string(value, label)


def _optional_sha256(value: Any, label: str) -> str | None:
    if value is None:
        return None
    result = _required_string(value, label).lower()
    if len(result) != 64 or any(
        character not in "0123456789abcdef" for character in result
    ):
        raise ManifestError(f"{label} must be a lowercase SHA-256 digest")
    return result


def _optional_sha512(value: Any, label: str) -> str | None:
    if value is None:
        return None
    result = _required_string(value, label).lower()
    if len(result) != 128 or any(
        character not in "0123456789abcdef" for character in result
    ):
        raise ManifestError(f"{label} must be a lowercase SHA-512 digest")
    return result


def _string_array(value: Any, label: str) -> tuple[str, ...]:
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item for item in value
    ):
        raise ManifestError(f"{label} must be an array of nonempty strings")
    return tuple(value)
