"""Strict dependency-free Ed25519 verification for benchmark evidence envelopes."""

from __future__ import annotations

import base64
import hashlib
import json
from datetime import UTC, datetime
from typing import Any

_Q = 2**255 - 19
_L = 2**252 + 27742317777372353535851937790883648493
_D = -121665 * pow(121666, _Q - 2, _Q) % _Q
_I = pow(2, (_Q - 1) // 4, _Q)


class EvidenceError(ValueError):
    """Raised when signed benchmark evidence is absent, stale, or invalid."""


def canonical_json(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def key_fingerprint(public_key: bytes) -> str:
    if len(public_key) != 32:
        raise EvidenceError("Ed25519 public key must contain 32 bytes")
    return "sha256:" + hashlib.sha256(public_key).hexdigest()


def decode_public_key(value: str) -> bytes:
    try:
        decoded = base64.b64decode(value, validate=True)
    except (ValueError, TypeError) as exc:
        raise EvidenceError("public key is not canonical base64") from exc
    if base64.b64encode(decoded).decode("ascii") != value or len(decoded) != 32:
        raise EvidenceError("public key must be canonical base64 Ed25519 bytes")
    return decoded


def _xrecover(y: int) -> int:
    xx = (y * y - 1) * pow(_D * y * y + 1, _Q - 2, _Q)
    x = pow(xx, (_Q + 3) // 8, _Q)
    if (x * x - xx) % _Q != 0:
        x = x * _I % _Q
    if x & 1:
        x = _Q - x
    return x


_BY = 4 * pow(5, _Q - 2, _Q) % _Q
_BX = _xrecover(_BY)
_B = (_BX, _BY)


def _decode_point(encoded: bytes) -> tuple[int, int]:
    if len(encoded) != 32:
        raise EvidenceError("Ed25519 point has invalid length")
    value = int.from_bytes(encoded, "little")
    y = value & ((1 << 255) - 1)
    if y >= _Q:
        raise EvidenceError("Ed25519 point is non-canonical")
    x = _xrecover(y)
    sign = value >> 255
    # The Edwards encoding has a single canonical representation for x=0.
    # A set sign bit would otherwise turn the recovered zero into Q (which is
    # congruent to zero in the curve equation) and admit the negative-zero
    # encoding.
    if x == 0 and sign:
        raise EvidenceError("Ed25519 point has non-canonical negative zero")
    if (x & 1) != sign:
        x = _Q - x
    if (-x * x + y * y - 1 - _D * x * x * y * y) % _Q != 0:
        raise EvidenceError("Ed25519 point is not on the curve")
    point = (x, y)
    if point == (0, 1):
        raise EvidenceError("Ed25519 identity point is forbidden")
    return point


def _add(left: tuple[int, int], right: tuple[int, int]) -> tuple[int, int]:
    x1, y1 = left
    x2, y2 = right
    denominator_x = pow(1 + _D * x1 * x2 * y1 * y2, _Q - 2, _Q)
    denominator_y = pow(1 - _D * x1 * x2 * y1 * y2, _Q - 2, _Q)
    return (
        (x1 * y2 + x2 * y1) * denominator_x % _Q,
        (y1 * y2 + x1 * x2) * denominator_y % _Q,
    )


def _scalar_mult(point: tuple[int, int], scalar: int) -> tuple[int, int]:
    result = (0, 1)
    addend = point
    while scalar:
        if scalar & 1:
            result = _add(result, addend)
        addend = _add(addend, addend)
        scalar >>= 1
    return result


def verify_ed25519(public_key: bytes, message: bytes, signature: bytes) -> None:
    if len(public_key) != 32 or len(signature) != 64:
        raise EvidenceError("Ed25519 key or signature length is invalid")
    encoded_r, encoded_s = signature[:32], signature[32:]
    scalar_s = int.from_bytes(encoded_s, "little")
    if scalar_s >= _L:
        raise EvidenceError("Ed25519 signature scalar is non-canonical")
    public_point = _decode_point(public_key)
    r_point = _decode_point(encoded_r)
    # RFC 8032 verification alone is not enough when a verifier accepts
    # low-order/non-prime-subgroup encodings. Require both the public key and R
    # to be non-identity members of the prime-order subgroup.
    if _scalar_mult(public_point, _L) != (0, 1):
        raise EvidenceError("Ed25519 public key is outside the prime-order subgroup")
    if _scalar_mult(r_point, _L) != (0, 1):
        raise EvidenceError(
            "Ed25519 signature point is outside the prime-order subgroup"
        )
    challenge = (
        int.from_bytes(
            hashlib.sha512(encoded_r + public_key + message).digest(), "little"
        )
        % _L
    )
    if _scalar_mult(_B, scalar_s) != _add(
        r_point, _scalar_mult(public_point, challenge)
    ):
        raise EvidenceError("Ed25519 signature verification failed")


def verify_envelope(
    envelope: Any,
    public_key: bytes,
    *,
    expected_kind: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    if not isinstance(envelope, dict) or set(envelope) != {"payload", "signature"}:
        raise EvidenceError("signed evidence envelope shape is invalid")
    payload = envelope["payload"]
    signature_value = envelope["signature"]
    if not isinstance(payload, dict) or not isinstance(signature_value, str):
        raise EvidenceError("signed evidence envelope values are invalid")
    try:
        signature = base64.b64decode(signature_value, validate=True)
    except (ValueError, TypeError) as exc:
        raise EvidenceError("evidence signature is not base64") from exc
    if base64.b64encode(signature).decode("ascii") != signature_value:
        raise EvidenceError("evidence signature is not canonical base64")
    verify_ed25519(public_key, canonical_json(payload), signature)
    if expected_kind is not None and payload.get("kind") != expected_kind:
        raise EvidenceError("signed evidence kind is invalid")
    try:
        issued = datetime.fromisoformat(payload["issued_at"].replace("Z", "+00:00"))
        expires = datetime.fromisoformat(payload["expires_at"].replace("Z", "+00:00"))
    except (KeyError, AttributeError, ValueError) as exc:
        raise EvidenceError("signed evidence timestamps are invalid") from exc
    current = now or datetime.now(UTC)
    if issued.tzinfo is None or expires.tzinfo is None:
        raise EvidenceError("signed evidence timestamps require a timezone")
    if issued > current or expires <= current or expires <= issued:
        raise EvidenceError("signed evidence is not currently valid")
    return payload
