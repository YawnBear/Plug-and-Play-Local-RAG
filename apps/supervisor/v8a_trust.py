from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

POLICY_ID = "local-rag-v8-release-trust"
MAXIMUM_METADATA_LIFETIME = timedelta(days=30)
SAFE_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{5,127}$")
SAFE_PROFILE_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{2,95}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
REQUIRED_ARTIFACTS = {
    "personal-release.json",
    "product-profiles.json",
    "capability-profiles.json",
}


class TrustMetadataError(ValueError):
    """Authenticated V8 release metadata violates the frozen trust policy."""


@dataclass(frozen=True, slots=True)
class VerifiedTrustMetadata:
    root_id: str
    release_id: str
    release_sequence: int
    issued_at: datetime
    expires_at: datetime
    artifacts_sha256: dict[str, str]
    revoked_release_ids: frozenset[str]
    revoked_profile_ids: frozenset[str]


def _reject_duplicate_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise TrustMetadataError(f"duplicate trust metadata field: {key}")
        value[key] = item
    return value


def _utc_timestamp(value: object, label: str) -> datetime:
    if (
        not isinstance(value, str)
        or re.fullmatch(
            r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z", value
        )
        is None
    ):
        raise TrustMetadataError(f"{label} must be a whole-second UTC timestamp")
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except ValueError as exc:
        raise TrustMetadataError(f"{label} is not a valid UTC timestamp") from exc


def _string_set(value: object, label: str, pattern: re.Pattern[str]) -> frozenset[str]:
    if (
        not isinstance(value, list)
        or any(
            not isinstance(item, str) or pattern.fullmatch(item) is None
            for item in value
        )
        or len(value) != len(set(value))
    ):
        raise TrustMetadataError(f"{label} must contain unique safe IDs")
    return frozenset(value)


def verify_trust_metadata(
    path: Path,
    *,
    authenticated_root_id: str,
    installed_release_sequence: int = 0,
    installed_release_id: str | None = None,
    selected_profile_ids: frozenset[str] = frozenset(),
    now: datetime | None = None,
) -> VerifiedTrustMetadata:
    """Validate policy after an outer release signature authenticated root_id.

    Cryptographic verification remains owned by the signed-release verifier.
    This function binds its authenticated root to freshness, revocation, and
    monotonic anti-rollback policy without accepting caller-selected fallback
    roots.
    """

    if not path.is_file() or path.is_symlink():
        raise TrustMetadataError("trust metadata must be a regular file")
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"), object_pairs_hook=_reject_duplicate_pairs
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise TrustMetadataError("trust metadata JSON is invalid") from exc
    expected_fields = {
        "schema_version",
        "policy_id",
        "root_id",
        "release_id",
        "release_sequence",
        "issued_at",
        "expires_at",
        "artifacts_sha256",
        "revoked_release_ids",
        "revoked_profile_ids",
    }
    if not isinstance(value, dict) or set(value) != expected_fields:
        raise TrustMetadataError("trust metadata fields are invalid")
    if value["schema_version"] != 1 or value["policy_id"] != POLICY_ID:
        raise TrustMetadataError("trust metadata schema or policy is invalid")
    root_id = value["root_id"]
    release_id = value["release_id"]
    if (
        not isinstance(root_id, str)
        or SAFE_ID.fullmatch(root_id) is None
        or not isinstance(release_id, str)
        or SAFE_ID.fullmatch(release_id) is None
    ):
        raise TrustMetadataError("trust root or release ID is invalid")
    if root_id != authenticated_root_id:
        raise TrustMetadataError(
            "trust metadata root differs from the authenticated signer"
        )
    sequence = value["release_sequence"]
    if not isinstance(sequence, int) or isinstance(sequence, bool) or sequence < 1:
        raise TrustMetadataError("release sequence is invalid")
    if installed_release_sequence < 0:
        raise TrustMetadataError("installed release sequence is invalid")
    if sequence < installed_release_sequence:
        raise TrustMetadataError("release metadata is older than installed state")
    if (
        sequence == installed_release_sequence
        and installed_release_id is not None
        and release_id != installed_release_id
    ):
        raise TrustMetadataError("release sequence is already bound to another release")
    issued_at = _utc_timestamp(value["issued_at"], "issued_at")
    expires_at = _utc_timestamp(value["expires_at"], "expires_at")
    current = now or datetime.now(UTC)
    if current.tzinfo is None or current.utcoffset() is None:
        raise TrustMetadataError("current time must be timezone-aware")
    current = current.astimezone(UTC)
    if expires_at <= issued_at or expires_at - issued_at > MAXIMUM_METADATA_LIFETIME:
        raise TrustMetadataError("trust metadata lifetime is invalid")
    if current < issued_at:
        raise TrustMetadataError("trust metadata is not valid yet")
    if current >= expires_at:
        raise TrustMetadataError("trust metadata is expired")
    artifacts = value["artifacts_sha256"]
    if (
        not isinstance(artifacts, dict)
        or set(artifacts) != REQUIRED_ARTIFACTS
        or any(
            not isinstance(item, str) or SHA256.fullmatch(item) is None
            for item in artifacts.values()
        )
    ):
        raise TrustMetadataError("trust metadata artifact set or digest is invalid")
    revoked_releases = _string_set(
        value["revoked_release_ids"], "revoked release IDs", SAFE_ID
    )
    revoked_profiles = _string_set(
        value["revoked_profile_ids"], "revoked profile IDs", SAFE_PROFILE_ID
    )
    if release_id in revoked_releases:
        raise TrustMetadataError("release is revoked")
    selected = _string_set(
        list(selected_profile_ids), "selected profile IDs", SAFE_PROFILE_ID
    )
    revoked_selected = selected.intersection(revoked_profiles)
    if revoked_selected:
        raise TrustMetadataError("a selected capability profile is revoked")
    return VerifiedTrustMetadata(
        root_id=root_id,
        release_id=release_id,
        release_sequence=sequence,
        issued_at=issued_at,
        expires_at=expires_at,
        artifacts_sha256=dict(artifacts),
        revoked_release_ids=revoked_releases,
        revoked_profile_ids=revoked_profiles,
    )
