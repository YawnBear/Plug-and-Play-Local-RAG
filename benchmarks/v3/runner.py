"""Fail-closed runner for a server-attested, isolated fresh-V3 benchmark."""

from __future__ import annotations

import argparse
import codecs
import ctypes
import hashlib
import http.cookiejar
import json
import os
import re
import stat
import time
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path, PurePath
from time import monotonic
from typing import Any, Protocol
from ctypes import wintypes
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import (
    HTTPCookieProcessor,
    HTTPRedirectHandler,
    ProxyHandler,
    Request,
    build_opener,
)

from .fixtures import APPROVED_HASHES_PATH, GENERATED_ROOT
from .harness import (
    EVALUATION_PATH,
    ROOT,
    _repo_relative,
    _validate_samples,
    create_manifest,
    ensure_results_path,
    validate_manifest,
)
from .trust import (
    EvidenceError,
    decode_public_key,
    key_fingerprint,
    verify_envelope,
)

PRIVATE_ROOT = Path(__file__).resolve().parent / "private"
TRUST_PATH = Path(__file__).resolve().parent / "data" / "benchmark-trust.json"
TERMINAL_JOBS = {"completed", "failed", "cancelled", "interrupted"}
MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
REQUIRED_PROFILES = {
    ("cold", "queue-free"),
    ("cold", "contended"),
    ("warm", "queue-free"),
    ("warm", "contended"),
}
SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
SAFE_COOKIE = re.compile(r"^[A-Za-z0-9._~-]{20,512}$")
SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
TIMING_ABSOLUTE_TOLERANCE_MS = 100.0
TIMING_RELATIVE_TOLERANCE = 0.10


class RunnerError(ValueError):
    """Raised before a mutation whenever benchmark safety cannot be proved."""


@dataclass(frozen=True)
class Target:
    base_url: str


@dataclass(frozen=True)
class BenchmarkPermit:
    run_id: str
    deployment_id: str
    store_id: str
    namespace: str
    nonce: str
    adopted_document_ids: tuple[str, ...] = ()


@dataclass
class TransportResponse:
    status: int
    body: bytes = b""
    chunks: Iterable[bytes] = ()


class AuthenticatedTransport(Protocol):
    permit: BenchmarkPermit

    def bootstrap(self) -> None: ...

    def request(
        self,
        method: str,
        path: str,
        body: bytes | None = None,
        headers: dict[str, str] | None = None,
        *,
        stream: bool = False,
    ) -> TransportResponse: ...


class _NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


def validate_target(base_url: str) -> Target:
    parsed = urlsplit(base_url)
    try:
        port = parsed.port
    except ValueError as exc:
        raise RunnerError("target URL has an invalid port") from exc
    if parsed.scheme not in {"http", "https"} or parsed.hostname not in {
        "127.0.0.1",
        "::1",
    }:
        raise RunnerError("target must be an explicitly supplied loopback URL")
    if parsed.path not in {"", "/"}:
        raise RunnerError("target URL must point at the API root")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise RunnerError("target URL must not contain credentials, query, or fragment")
    if port is None:
        raise RunnerError("target must have an explicit port")
    return Target(base_url.rstrip("/"))


def _is_reparse_or_symlink(path: Path) -> bool:
    if path.is_symlink():
        return True
    attributes = getattr(path.stat(follow_symlinks=False), "st_file_attributes", 0)
    return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


def _reject_link_chain(root: Path, candidate: Path) -> None:
    lexical_root = Path(os.path.abspath(root))
    lexical_candidate = Path(os.path.abspath(candidate))
    try:
        relative = lexical_candidate.relative_to(lexical_root)
    except ValueError as exc:
        raise RunnerError("path escapes its approved root") from exc
    current = lexical_root
    if _is_reparse_or_symlink(current):
        raise RunnerError("approved root must not be a link or reparse point")
    for part in relative.parts:
        current = current / part
        if current.exists() and _is_reparse_or_symlink(current):
            raise RunnerError("path must not contain links or reparse points")


def _windows_sid_string(sid: int) -> str:
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    convert = advapi32.ConvertSidToStringSidW
    convert.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_wchar_p)]
    convert.restype = wintypes.BOOL
    rendered = ctypes.c_wchar_p()
    if not convert(ctypes.c_void_p(sid), ctypes.byref(rendered)):
        raise RunnerError("unable to read benchmark private-path SID")
    try:
        return rendered.value or ""
    finally:
        ctypes.windll.kernel32.LocalFree(rendered)


def _windows_current_user_sid() -> str:
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    token = ctypes.c_void_p()
    if not advapi32.OpenProcessToken(
        kernel32.GetCurrentProcess(), 0x0008, ctypes.byref(token)
    ):
        raise RunnerError("unable to inspect benchmark private-path owner")
    try:
        size = ctypes.c_ulong()
        advapi32.GetTokenInformation(token, 1, None, 0, ctypes.byref(size))
        buffer = ctypes.create_string_buffer(size.value)
        if not advapi32.GetTokenInformation(
            token, 1, buffer, size.value, ctypes.byref(size)
        ):
            raise RunnerError("unable to inspect benchmark private-path owner")
        sid = ctypes.c_void_p.from_buffer(buffer).value
        if not sid:
            raise RunnerError("unable to inspect benchmark private-path owner")
        return _windows_sid_string(sid)
    finally:
        kernel32.CloseHandle(token)


def _windows_private_acl(native_handle: int, *, directory: bool) -> None:
    """Validate a Windows DACL from a retained handle via GetSecurityInfo."""
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    get_security_info = advapi32.GetSecurityInfo
    get_security_info.restype = wintypes.DWORD
    security_descriptor = ctypes.c_void_p()
    owner = ctypes.c_void_p()
    dacl = ctypes.c_void_p()
    object_type = 1  # SE_FILE_OBJECT
    information = 0x00000001 | 0x00000004  # OWNER_SECURITY_INFORMATION|DACL_...
    status = get_security_info(
        ctypes.c_void_p(native_handle),
        object_type,
        information,
        ctypes.byref(owner),
        None,
        ctypes.byref(dacl),
        None,
        ctypes.byref(security_descriptor),
    )
    if status != 0 or not security_descriptor.value:
        raise RunnerError("unable to verify benchmark private-path ACL")
    try:
        control = ctypes.c_ushort()
        revision = ctypes.c_ulong()
        if not advapi32.GetSecurityDescriptorControl(
            security_descriptor, ctypes.byref(control), ctypes.byref(revision)
        ):
            raise RunnerError("unable to verify benchmark private-path ACL")
        dacl_present = wintypes.BOOL()
        dacl_defaulted = wintypes.BOOL()
        if not advapi32.GetSecurityDescriptorDacl(
            security_descriptor,
            ctypes.byref(dacl_present),
            ctypes.byref(dacl),
            ctypes.byref(dacl_defaulted),
        ):
            raise RunnerError("unable to verify benchmark private-path ACL")
        current = _windows_current_user_sid()
        owner_sid = _windows_sid_string(owner.value) if owner.value else ""
        entries: list[tuple[str, int, bool, bool]] = []
        if dacl_present.value and dacl.value:

            class AclSizeInformation(ctypes.Structure):
                _fields_ = [
                    ("AceCount", wintypes.DWORD),
                    ("AclBytesInUse", wintypes.DWORD),
                    ("AclBytesFree", wintypes.DWORD),
                ]

            size_info = AclSizeInformation()
            if not advapi32.GetAclInformation(
                dacl,
                ctypes.byref(size_info),
                ctypes.sizeof(size_info),
                2,
            ):
                raise RunnerError("unable to verify benchmark private-path ACL")
            for index in range(size_info.AceCount):
                ace = ctypes.c_void_p()
                if not advapi32.GetAce(dacl, index, ctypes.byref(ace)):
                    raise RunnerError("unable to verify benchmark private-path ACL")
                address = ace.value or 0
                header = ctypes.string_at(address, 8)
                ace_type = header[0]
                ace_flags = header[1]
                rights = int.from_bytes(header[4:8], "little")
                sid = _windows_sid_string(
                    ctypes.c_void_p.from_address(address + 8).value or 0
                )
                entries.append((sid, rights, ace_type == 0, bool(ace_flags & 0x10)))
        allowed = {current, "S-1-5-18", "S-1-5-32-544"}
        if (
            owner_sid != current
            or not dacl_present.value
            or not dacl.value
            or not entries
            or not (control.value & 0x1000)
            or not any(
                sid == current and is_allow and not inherited and rights & 1
                for sid, rights, is_allow, inherited in entries
            )
            or any(
                sid not in allowed or not is_allow or inherited
                for sid, _rights, is_allow, inherited in entries
            )
        ):
            raise RunnerError(
                "benchmark private path DACL is not an explicit SID allowlist"
            )
    finally:
        kernel32.LocalFree(security_descriptor)


def _windows_open_directory(path: Path) -> int:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_file = kernel32.CreateFileW
    create_file.restype = wintypes.HANDLE
    handle = create_file(
        str(path),
        0x80000000,
        0x00000007,
        None,
        3,
        0x02000000,
        None,
    )
    handle_value = handle.value
    if handle_value in {-1, ctypes.c_void_p(-1).value}:
        raise RunnerError("unable to open benchmark private directory")
    return int(handle_value)


def _private_file(
    path: Path,
    *,
    opened_stat: os.stat_result | None = None,
    opened_fd: int | None = None,
) -> Path:
    root = PRIVATE_ROOT.resolve()
    candidate = path.resolve(strict=True)
    if root not in candidate.parents or candidate.parent != root:
        raise RunnerError("session file must be directly inside benchmarks/v3/private")
    if any(_is_reparse_or_symlink(item) for item in (path, path.parent)):
        raise RunnerError("session file path must not contain links or reparse points")
    if not candidate.is_file():
        raise RunnerError("session file is not a regular file")
    if opened_stat is not None:
        current = candidate.stat(follow_symlinks=False)
        if (
            current.st_dev,
            current.st_ino,
            current.st_size,
            current.st_mtime_ns,
        ) != (
            opened_stat.st_dev,
            opened_stat.st_ino,
            opened_stat.st_size,
            opened_stat.st_mtime_ns,
        ):
            raise RunnerError("benchmark session file changed while it was open")
    if os.name != "nt" and stat.S_IMODE(candidate.stat().st_mode) & 0o077:
        raise RunnerError("session file must not be accessible by group or other")
    if os.name == "nt":
        import msvcrt

        close_file = opened_fd is None
        file_fd = opened_fd
        if file_fd is None:
            try:
                file_fd = os.open(
                    candidate,
                    os.O_RDONLY | getattr(os, "O_BINARY", 0),
                )
            except OSError as exc:
                raise RunnerError("unable to open benchmark session file") from exc
        directory_handle: int | None = None
        try:
            _windows_private_acl(
                msvcrt.get_osfhandle(file_fd),
                directory=False,
            )
            directory_handle = _windows_open_directory(candidate.parent)
            _windows_private_acl(directory_handle, directory=True)
        finally:
            if close_file:
                os.close(file_fd)
            if directory_handle is not None:
                ctypes.WinDLL("kernel32", use_last_error=True).CloseHandle(
                    ctypes.c_void_p(directory_handle)
                )
    return candidate


def _load_session(path: Path, target: Target) -> dict[str, Any]:
    # Open the requested path before any ACL or path-based inspection. All
    # subsequent identity checks and JSON parsing remain bound to this handle.
    lexical = Path(os.path.abspath(path))
    try:
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(lexical, flags)
        with os.fdopen(descriptor, "r", encoding="utf-8") as handle:
            opened = os.fstat(handle.fileno())
            parent_opened = lexical.parent.stat(follow_symlinks=False)
            candidate = (
                _private_file(lexical, opened_fd=handle.fileno())
                if os.name == "nt"
                else _private_file(lexical)
            )
            value = json.load(handle)
            after = candidate.stat(follow_symlinks=False)
            parent_after = lexical.parent.stat(follow_symlinks=False)

            def identity(item: os.stat_result) -> tuple[int, int, int, int]:
                return (
                    item.st_dev,
                    item.st_ino,
                    item.st_size,
                    item.st_mtime_ns,
                )

            if identity(opened) != identity(after) or (
                parent_opened.st_dev,
                parent_opened.st_ino,
                parent_opened.st_mtime_ns,
            ) != (
                parent_after.st_dev,
                parent_after.st_ino,
                parent_after.st_mtime_ns,
            ):
                raise RunnerError("benchmark session file changed while it was open")
    except (OSError, json.JSONDecodeError) as exc:
        raise RunnerError("unable to read benchmark session file") from exc
    required = {
        "schema_version",
        "origin",
        "rag_session",
        "csrf_token",
        "benchmark",
    }
    if not isinstance(value, dict) or set(value) != required:
        raise RunnerError("benchmark session file shape is invalid")
    if value["schema_version"] != 1 or value["origin"] != target.base_url:
        raise RunnerError("benchmark session origin does not match target")
    benchmark = value["benchmark"]
    if (
        not isinstance(value["rag_session"], str)
        or not SAFE_COOKIE.fullmatch(value["rag_session"])
        or not isinstance(value["csrf_token"], str)
        or not SAFE_COOKIE.fullmatch(value["csrf_token"])
        or not isinstance(benchmark, dict)
        or set(benchmark)
        != {
            "run_id",
            "deployment_id",
            "store_id",
            "namespace",
            "nonce",
            "adopted_document_ids",
        }
        or not all(
            isinstance(item, str) and SAFE_ID.fullmatch(item)
            for key, item in benchmark.items()
            if key != "adopted_document_ids"
        )
        or not isinstance(benchmark["adopted_document_ids"], list)
    ):
        raise RunnerError("benchmark session values are invalid")
    try:
        benchmark["adopted_document_ids"] = [
            str(uuid.UUID(item)) for item in benchmark["adopted_document_ids"]
        ]
    except (TypeError, ValueError) as exc:
        raise RunnerError("adopted document IDs must be UUIDs") from exc
    if len(set(benchmark["adopted_document_ids"])) != len(
        benchmark["adopted_document_ids"]
    ):
        raise RunnerError("adopted document IDs must be unique")
    return value


class CookieSessionTransport:
    """Cookie-jar transport using a locally pre-issued benchmark session.

    Passwords are intentionally unsupported. The server must first issue an
    session and its pre-issued session-bound CSRF token before authentication,
    then proves that ``/api/auth/me`` returns that exact same CSRF token.
    """

    def __init__(
        self,
        target: Target,
        session_file: Path,
        trust_path: Path = TRUST_PATH,
    ) -> None:
        self.target = target
        self._session = _load_session(session_file, target)
        benchmark = dict(self._session["benchmark"])
        benchmark["adopted_document_ids"] = tuple(benchmark["adopted_document_ids"])
        self.permit = BenchmarkPermit(**benchmark)
        try:
            trust = json.loads(trust_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RunnerError("benchmark trust configuration is unavailable") from exc
        if (
            not isinstance(trust, dict)
            or set(trust)
            != {"schema_version", "status", "ed25519_public_key", "fingerprint"}
            or trust.get("schema_version") != 1
            or trust.get("status") != "configured"
            or not isinstance(trust.get("ed25519_public_key"), str)
            or not isinstance(trust.get("fingerprint"), str)
        ):
            raise RunnerError("benchmark public verification key is not configured")
        try:
            self.public_key = decode_public_key(trust["ed25519_public_key"])
        except EvidenceError as exc:
            raise RunnerError(str(exc)) from exc
        self.public_key_value = trust["ed25519_public_key"]
        self.public_key_fingerprint = key_fingerprint(self.public_key)
        if self.public_key_fingerprint != trust["fingerprint"]:
            raise RunnerError("benchmark public key fingerprint does not match")
        self._jar = http.cookiejar.CookieJar()
        self._opener = build_opener(
            ProxyHandler({}), _NoRedirects(), HTTPCookieProcessor(self._jar)
        )
        self._bootstrapped = False
        self._csrf_token: str | None = None

    def bootstrap(self) -> None:
        parsed = urlsplit(self.target.base_url)

        def install_cookie(name: str, value: str, *, http_only: bool) -> None:
            self._jar.set_cookie(
                http.cookiejar.Cookie(
                    version=0,
                    name=name,
                    value=value,
                    port=None,
                    port_specified=False,
                    domain=parsed.hostname or "127.0.0.1",
                    domain_specified=True,
                    domain_initial_dot=False,
                    path="/",
                    path_specified=True,
                    secure=parsed.scheme == "https",
                    expires=None,
                    discard=True,
                    comment=None,
                    comment_url=None,
                    rest={"HttpOnly": None} if http_only else {},
                    rfc2109=False,
                )
            )

        install_cookie("rag_session", self._session["rag_session"], http_only=True)
        install_cookie("csrf_token", self._session["csrf_token"], http_only=False)
        response = self._open(
            "GET",
            "/api/auth/me",
            None,
            {"Origin": self.target.base_url},
            stream=False,
        )
        try:
            payload = json.loads(response.body)
        except json.JSONDecodeError as exc:
            raise RunnerError(
                "authenticated bootstrap returned malformed JSON"
            ) from exc
        if (
            response.status != 200
            or set(payload) != {"user", "csrf_token"}
            or payload["user"] is None
            or not isinstance(payload["user"], dict)
            or not isinstance(payload["csrf_token"], str)
            or not SAFE_COOKIE.fullmatch(payload["csrf_token"])
            or payload["csrf_token"] != self._session["csrf_token"]
        ):
            raise RunnerError("authenticated bootstrap failed")
        self._csrf_token = self._session["csrf_token"]
        self._bootstrapped = True

    def _open(
        self,
        method: str,
        path: str,
        body: bytes | None,
        headers: dict[str, str],
        *,
        stream: bool,
    ) -> TransportResponse:
        request = Request(
            self.target.base_url + path,
            data=body,
            method=method,
            headers=headers,
        )
        try:
            response = self._opener.open(request, timeout=120)
            if stream:

                def chunks() -> Iterable[bytes]:
                    with response:
                        while block := response.read1(4096):
                            yield block

                return TransportResponse(
                    response.status,
                    chunks=chunks(),
                )
            with response:
                return TransportResponse(response.status, response.read())
        except (HTTPError, URLError, TimeoutError) as exc:
            raise RunnerError(f"benchmark request failed: {method} {path}") from exc

    def request(
        self,
        method: str,
        path: str,
        body: bytes | None = None,
        headers: dict[str, str] | None = None,
        *,
        stream: bool = False,
    ) -> TransportResponse:
        if not self._bootstrapped:
            raise RunnerError("authenticated transport was not bootstrapped")
        request_headers = dict(headers or {})
        if method in MUTATING_METHODS:
            request_headers["Origin"] = self.target.base_url
            request_headers["X-CSRF-Token"] = self._csrf_token or ""
        return self._open(method, path, body, request_headers, stream=stream)


def _safe_fixture_file(directory: Path, filename: Any) -> Path:
    if (
        not isinstance(filename, str)
        or not filename
        or filename != PurePath(filename).name
        or Path(filename).is_absolute()
        or "/" in filename
        or "\\" in filename
        or not filename.endswith(".pdf")
    ):
        raise RunnerError("fixture filename must be a simple PDF basename")
    if _is_reparse_or_symlink(directory):
        raise RunnerError("fixture directory must not be a link or reparse point")
    candidate = directory / filename
    if _is_reparse_or_symlink(candidate):
        raise RunnerError("fixture file must not be a link or reparse point")
    resolved = candidate.resolve(strict=True)
    if resolved.parent != directory.resolve(strict=True) or not resolved.is_file():
        raise RunnerError("fixture file escapes its fixture directory")
    return resolved


def _sha256(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _fixture_manifest(path: Path) -> dict[str, Any]:
    _reject_link_chain(GENERATED_ROOT, path)
    resolved = path.resolve(strict=True)
    root = GENERATED_ROOT.resolve()
    if root not in resolved.parents or resolved.name != "fixture-manifest.json":
        raise RunnerError("fixture manifest must be inside the generated fixture tree")
    try:
        value = json.loads(resolved.read_text(encoding="utf-8"))
        approved = json.loads(APPROVED_HASHES_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RunnerError("unable to read fixture manifest") from exc
    if (
        not isinstance(value, dict)
        or value.get("schema_version") != 2
        or value.get("dataset_id") != "v3-synthetic-en-first-001"
        or value.get("evaluation_identity") != _sha256(EVALUATION_PATH)
        or approved.get("dataset_id") != value.get("dataset_id")
        or approved.get("evaluation_identity") != value.get("evaluation_identity")
        or approved.get("corpus_identity") != value.get("corpus_identity")
    ):
        raise RunnerError("fixture manifest is not the approved synthetic dataset")
    documents = value.get("documents")
    if not isinstance(documents, list) or len(documents) != 10:
        raise RunnerError("fixture manifest must contain ten documents")
    if (
        sum(
            isinstance(item, dict) and item.get("kind") == "digital"
            for item in documents
        )
        != 8
        or sum(
            isinstance(item, dict) and item.get("kind") == "scanned"
            for item in documents
        )
        != 2
    ):
        raise RunnerError("fixture manifest must contain eight digital and two scanned")
    for document in documents:
        if not isinstance(document, dict) or set(document) != {
            "id",
            "kind",
            "pages",
            "filename",
            "sha256",
            "evaluation_content_sha256",
            "source_tokens",
        }:
            raise RunnerError("fixture manifest document shape is invalid")
        pdf = _safe_fixture_file(resolved.parent, document["filename"])
        if _sha256(pdf) != document["sha256"]:
            raise RunnerError("fixture file hash does not match its manifest")
        if approved.get("documents", {}).get(document["id"]) != document["sha256"]:
            raise RunnerError("fixture file hash is not canonically approved")
        evaluation = json.loads(EVALUATION_PATH.read_text(encoding="utf-8"))
        expected = next(
            (item for item in evaluation["documents"] if item["id"] == document["id"]),
            None,
        )
        expected_tokens = (
            [
                {"page": page, "token": content}
                for page, content in enumerate(expected["page_content"], start=1)
            ]
            if expected
            else None
        )
        content_digest = (
            hashlib.sha256(
                json.dumps(
                    {
                        "id": expected["id"],
                        "pages": expected["pages"],
                        "page_content": expected["page_content"],
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                ).encode("utf-8")
            ).hexdigest()
            if expected
            else None
        )
        if (
            document["source_tokens"] != expected_tokens
            or document["evaluation_content_sha256"] != content_digest
        ):
            raise RunnerError("fixture source pages are not bound to the evaluation")
    records = json.dumps(documents, sort_keys=True, separators=(",", ":")).encode()
    if value.get("corpus_identity") != hashlib.sha256(records).hexdigest():
        raise RunnerError("fixture corpus identity does not match document records")
    return value


def _json_response(response: TransportResponse, context: str) -> dict[str, Any]:
    if not 200 <= response.status < 300:
        raise RunnerError(f"{context} returned status {response.status}")
    try:
        value = json.loads(response.body)
    except json.JSONDecodeError as exc:
        raise RunnerError(f"{context} returned malformed JSON") from exc
    if not isinstance(value, dict):
        raise RunnerError(f"{context} returned an invalid JSON shape")
    return value


def _sse_events(
    chunks: Iterable[bytes],
) -> Iterable[tuple[str, dict[str, Any], float]]:
    decoder = codecs.getincrementaldecoder("utf-8")(errors="strict")
    buffer = ""
    for chunk in chunks:
        try:
            buffer += decoder.decode(chunk, final=False)
        except UnicodeDecodeError as exc:
            raise RunnerError("SSE stream contains invalid UTF-8") from exc
        arrived_at = monotonic()
        while delimiter := re.search(r"\r?\n\r?\n", buffer):
            block = buffer[: delimiter.start()]
            buffer = buffer[delimiter.end() :]
            event: str | None = None
            data: list[str] = []
            for line in block.splitlines():
                if line.startswith("event: "):
                    event = line[7:]
                elif line.startswith("data: "):
                    data.append(line[6:])
            if event is None or not data:
                continue
            try:
                payload = json.loads("\n".join(data))
            except json.JSONDecodeError as exc:
                raise RunnerError("SSE stream contained malformed JSON") from exc
            if not isinstance(payload, dict):
                raise RunnerError("SSE event payload must be an object")
            yield event, payload, arrived_at
    try:
        buffer += decoder.decode(b"", final=True)
    except UnicodeDecodeError as exc:
        raise RunnerError("SSE stream ended inside a UTF-8 sequence") from exc
    if buffer.strip():
        raise RunnerError("SSE stream ended with an incomplete event")


def _timings_agree(client_ms: float, server_ms: float) -> bool:
    difference = abs(client_ms - server_ms)
    return difference <= max(
        TIMING_ABSOLUTE_TOLERANCE_MS,
        TIMING_RELATIVE_TOLERANCE * max(client_ms, server_ms),
    )


class GuardedRunner:
    def __init__(
        self,
        transport: AuthenticatedTransport,
        fixture_path: Path,
        timeout_s: float = 120.0,
    ) -> None:
        self.transport = transport
        self.permit = transport.permit
        self.fixture = _fixture_manifest(fixture_path)
        self.fixture_path = fixture_path.resolve()
        self.timeout_s = timeout_s
        self.created_document_ids: set[str] = set()
        self.document_ids: dict[str, str] = {}
        self.samples: list[dict[str, Any]] = []
        self.pre_attestation: dict[str, Any] | None = None
        self.post_attestation: dict[str, Any] | None = None
        self.profile_attestations: dict[str, dict[str, Any]] = {}
        self.execution_evidence: list[dict[str, Any]] = []
        self.sample_attestations: list[dict[str, Any]] = []
        self._correlations: dict[str, dict[str, str]] = {}
        self._upload_hashes: dict[str, str] = {}
        self._last_evidence_sequence = 0
        self._signed_context: dict[str, Any] | None = None
        self.public_key = transport.public_key
        self.public_key_value = transport.public_key_value
        self.public_key_fingerprint = transport.public_key_fingerprint

    def _verified_payload(
        self,
        envelope: Any,
        kind: str,
        extra_fields: set[str],
    ) -> dict[str, Any]:
        try:
            payload = verify_envelope(envelope, self.public_key, expected_kind=kind)
        except EvidenceError as exc:
            raise RunnerError(str(exc)) from exc
        common = {
            "kind",
            "evidence_id",
            "issued_at",
            "expires_at",
            "run_id",
            "nonce",
            "deployment_id",
            "store_id",
            "store_mode",
            "namespace",
            "corpus_identity",
            "fixture_identity",
            "sequence",
            "source_revision",
            "source_content_identity",
            "runtime_artifact_hashes",
        }
        if set(payload) != common | extra_fields:
            raise RunnerError("signed benchmark evidence payload shape is invalid")
        permit = self.permit
        if (
            payload["run_id"] != permit.run_id
            or payload["nonce"] != permit.nonce
            or payload["deployment_id"] != permit.deployment_id
            or payload["store_id"] != permit.store_id
            or payload["store_mode"] != "isolated-benchmark"
            or payload["namespace"] != permit.namespace
            or payload["corpus_identity"] != self.fixture["corpus_identity"]
            or payload["fixture_identity"] != self.fixture["evaluation_identity"]
            or not isinstance(payload["evidence_id"], str)
            or not SAFE_ID.fullmatch(payload["evidence_id"])
            or not isinstance(payload["sequence"], int)
            or isinstance(payload["sequence"], bool)
            or payload["sequence"] <= self._last_evidence_sequence
            or not isinstance(payload["source_revision"], str)
            or not re.fullmatch(r"[0-9a-f]{40,64}", payload["source_revision"])
            or not isinstance(payload["source_content_identity"], str)
            or not re.fullmatch(
                r"sha256:[0-9a-f]{64}", payload["source_content_identity"]
            )
            or not isinstance(payload["runtime_artifact_hashes"], dict)
            or set(payload["runtime_artifact_hashes"])
            != {
                "dependency_lock",
                "container_inventory",
                "model_inventory",
                "server_artifact",
                "schema_evidence",
                "server_evidence",
            }
            or any(
                not isinstance(item, str)
                or not re.fullmatch(r"sha256:[0-9a-f]{64}", item)
                for item in payload["runtime_artifact_hashes"].values()
            )
        ):
            raise RunnerError("signed evidence does not match this benchmark run")
        signed_context = {
            "source_revision": payload["source_revision"],
            "source_content_identity": payload["source_content_identity"],
            "runtime_artifact_hashes": payload["runtime_artifact_hashes"],
        }
        if self._signed_context is None:
            self._signed_context = signed_context
        elif self._signed_context != signed_context:
            raise RunnerError("signed runtime/source identity changed during the run")
        self._last_evidence_sequence = payload["sequence"]
        return payload

    def attest(self) -> None:
        envelope = _json_response(
            self.transport.request("GET", "/api/benchmark/attestation/pre"),
            "benchmark pre-attestation",
        )
        payload = self._verified_payload(
            envelope,
            "pre",
            {
                "namespace_state",
                "namespace_owner_run_id",
                "owned_document_ids",
                "measurement_profiles",
            },
        )
        state = payload["namespace_state"]
        owner = payload["namespace_owner_run_id"]
        try:
            owned_ids = tuple(
                str(uuid.UUID(item)) for item in payload["owned_document_ids"]
            )
        except (TypeError, ValueError) as exc:
            raise RunnerError("signed owned document IDs are invalid") from exc
        if state == "empty":
            if owner is not None or owned_ids or self.permit.adopted_document_ids:
                raise RunnerError("empty namespace attestation contains owned IDs")
        elif not (
            state == "runner-owned"
            and owner == self.permit.run_id
            and owned_ids == self.permit.adopted_document_ids
            and owned_ids
        ):
            raise RunnerError("stale namespace is not exactly signed and adopted")
        profiles = {
            (item.get("temperature"), item.get("queue"))
            for item in payload["measurement_profiles"]
            if isinstance(item, dict)
        }
        if profiles != REQUIRED_PROFILES:
            raise RunnerError(
                "server does not attest every required measurement profile"
            )
        if any(
            set(item) != {"temperature", "queue"}
            for item in payload["measurement_profiles"]
        ):
            raise RunnerError("measurement profile attestation contains extra fields")
        self.created_document_ids.update(owned_ids)
        self.pre_attestation = envelope

    def _json_request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        *,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        body = (
            None
            if payload is None
            else json.dumps(payload, separators=(",", ":")).encode()
        )
        return _json_response(
            self.transport.request(
                method,
                path,
                body,
                {
                    **({"Content-Type": "application/json"} if body else {}),
                    **(headers or {}),
                }
                or None,
            ),
            f"{method} {path}",
        )

    @staticmethod
    def _new_correlation(sample_id: str, nonce: str) -> dict[str, str]:
        return {
            "sample_id": sample_id,
            "nonce": nonce,
            "profile_id": f"profile-{uuid.uuid4()}",
            "evidence_id": f"request-{uuid.uuid4()}",
            "execution_id": f"execution-{uuid.uuid4()}",
        }

    @staticmethod
    def _correlation_headers(correlation: dict[str, str]) -> dict[str, str]:
        return {
            "X-Benchmark-Sample-Id": correlation["sample_id"],
            "X-Benchmark-Nonce": correlation["nonce"],
            "X-Benchmark-Profile-Id": correlation["profile_id"],
            "X-Benchmark-Evidence-Id": correlation["evidence_id"],
            "X-Benchmark-Execution-Id": correlation["execution_id"],
        }

    def set_profile(self, temperature: str, queue: str, sample_id: str) -> str:
        correlation = self._new_correlation(sample_id, self.permit.nonce)
        envelope = self._json_request(
            "POST",
            "/api/benchmark/profile",
            {
                "run_id": self.permit.run_id,
                "benchmark_nonce": self.permit.nonce,
                "temperature": temperature,
                "queue": queue,
            },
            headers=self._correlation_headers(correlation),
        )
        payload = self._verified_payload(
            envelope,
            "profile",
            {
                "profile",
                "applied",
                "profile_token",
                "active_requests",
                "queued_requests",
                "correlation",
            },
        )
        expected_load = (1, 1) if queue == "contended" else (1, 0)
        if (
            payload["profile"] != {"temperature": temperature, "queue": queue}
            or payload["applied"] is not True
            or not isinstance(payload["profile_token"], str)
            or not SAFE_ID.fullmatch(payload["profile_token"])
            or payload["correlation"] != correlation
            or (
                payload["active_requests"],
                payload["queued_requests"],
            )
            != expected_load
        ):
            raise RunnerError("server did not confirm the requested benchmark profile")
        evidence_id = payload["evidence_id"]
        self.profile_attestations[evidence_id] = envelope
        self._correlations[evidence_id] = correlation
        return evidence_id

    def upload(
        self,
        document: dict[str, Any],
        sample_id: str,
        profile_evidence_id: str,
    ) -> tuple[str, str]:
        boundary = "----V3SyntheticBoundary"
        pdf = _safe_fixture_file(self.fixture_path.parent, document["filename"])
        payload = pdf.read_bytes()
        filename = document["filename"]
        prefix = (
            f'--{boundary}\r\nContent-Disposition: form-data; name="file"; '
            f'filename="{filename}"\r\nContent-Type: application/pdf\r\n\r\n'
        ).encode()
        body = prefix + payload + f"\r\n--{boundary}--\r\n".encode()
        # Hash the exact multipart payload after its final construction and
        # immediately before the transport call.
        upload_sha256 = "sha256:" + hashlib.sha256(body).hexdigest()
        correlation = self._correlations[profile_evidence_id]
        headers = {
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "X-Benchmark-Namespace": self.permit.namespace,
            "X-Benchmark-Owner": self.permit.run_id,
            "X-Benchmark-Profile-Evidence": profile_evidence_id,
            "X-Benchmark-Upload-SHA256": upload_sha256,
            **self._correlation_headers(correlation),
        }
        response = _json_response(
            self.transport.request(
                "POST",
                "/api/documents",
                body,
                headers,
            ),
            "document upload",
        )
        try:
            document_id = str(uuid.UUID(str(response["document_id"])))
            job_id = str(uuid.UUID(str(response["job_id"])))
        except (KeyError, TypeError, ValueError) as exc:
            raise RunnerError("upload response did not contain valid IDs") from exc
        if (
            response.get("status") not in {"queued", "pending"}
            or response.get("duplicate_of") is not None
        ):
            raise RunnerError("fixture upload was not accepted as a new document")
        if (
            document["id"] in self.document_ids
            or document_id in self.document_ids.values()
        ):
            raise RunnerError("fixture upload returned a duplicate corpus identity")
        self.created_document_ids.add(document_id)
        self.document_ids[document["id"]] = document_id
        self._upload_hashes[sample_id] = upload_sha256
        return document_id, job_id

    def wait_for_job(
        self, job_id: str, sample_id: str, profile_evidence_id: str
    ) -> dict[str, Any]:
        headers = {
            "X-Benchmark-Profile-Evidence": profile_evidence_id,
            **self._correlation_headers(self._correlations[profile_evidence_id]),
        }
        deadline = monotonic() + self.timeout_s
        while monotonic() < deadline:
            result = self._json_request("GET", f"/api/jobs/{job_id}", headers=headers)
            if result.get("status") in TERMINAL_JOBS:
                if result.get("status") != "completed":
                    raise RunnerError(
                        f"ingestion job terminated as {result.get('status')}"
                    )
                return self._json_request(
                    "GET",
                    f"/api/benchmark/executions/{sample_id}",
                    headers=headers,
                )
            time.sleep(0.25)
        raise RunnerError("job polling timed out")

    @staticmethod
    def _source_pairs(items: Any) -> set[tuple[str, int]]:
        pairs: set[tuple[str, int]] = set()
        if not isinstance(items, list):
            raise RunnerError("source collection must be an array")
        for item in items:
            if not isinstance(item, dict) or not {
                "document_id",
                "page_start",
                "page_end",
            }.issubset(item):
                raise RunnerError("source item shape is invalid")
            try:
                document_id = str(uuid.UUID(item["document_id"]))
            except (TypeError, ValueError) as exc:
                raise RunnerError("source document ID is invalid") from exc
            start = item["page_start"]
            end = item["page_end"]
            if (
                not isinstance(start, int)
                or isinstance(start, bool)
                or not isinstance(end, int)
                or isinstance(end, bool)
                or not 0 < start <= end
            ):
                raise RunnerError("source page range is invalid")
            pairs.update((document_id, page) for page in range(start, end + 1))
        return pairs

    @staticmethod
    def _retrieval_pairs(
        items: Any, *, allowed_document_ids: set[str]
    ) -> set[tuple[str, int]]:
        """Parse the signed, content-free retrieval candidate schema."""
        if not isinstance(items, list) or len(items) > 20:
            raise RunnerError("retrieval candidate cardinality is invalid")
        pairs: set[tuple[str, int]] = set()
        required = {"document_id", "page_start", "page_end"}
        for item in items:
            if not isinstance(item, dict) or set(item) != required:
                raise RunnerError("retrieval candidate shape is invalid")
            try:
                document_id = str(uuid.UUID(item["document_id"]))
            except (TypeError, ValueError) as exc:
                raise RunnerError("retrieval candidate document ID is invalid") from exc
            if document_id not in allowed_document_ids:
                raise RunnerError("retrieval candidate is outside the adopted corpus")
            start = item["page_start"]
            end = item["page_end"]
            if (
                not isinstance(start, int)
                or isinstance(start, bool)
                or not isinstance(end, int)
                or isinstance(end, bool)
                or not 0 < start <= end
            ):
                raise RunnerError("retrieval candidate page range is invalid")
            pairs.update((document_id, page) for page in range(start, end + 1))
        return pairs

    def _execution_sample(
        self,
        envelope: Any,
        *,
        sample_id: str,
        stage: str,
        case_id: str | None,
        document_id: str | None,
        fixture_document_id: str | None,
        temperature: str,
        queue: str,
        repetition: int,
        profile_evidence_id: str,
    ) -> dict[str, Any]:
        payload = self._verified_payload(
            envelope,
            "execution",
            {
                "sample_id",
                "stage",
                "case_id",
                "document_id",
                "fixture_document_id",
                "profile",
                "repetition",
                "profile_evidence_id",
                "success",
                "terminal_status",
                "metrics",
                "retrieval_candidates",
                "reranked_sources",
                "workload",
                "correlation",
                "upload",
            },
        )
        correlation = self._correlations.get(profile_evidence_id)
        if (
            payload["sample_id"] != sample_id
            or payload["stage"] != stage
            or payload["case_id"] != case_id
            or payload["document_id"] != document_id
            or payload["fixture_document_id"] != fixture_document_id
            or payload["profile"] != {"temperature": temperature, "queue": queue}
            or payload["repetition"] != repetition
            or payload["profile_evidence_id"] != profile_evidence_id
            or profile_evidence_id not in self.profile_attestations
            or correlation is None
            or payload["correlation"] != correlation
            or payload["success"] is not True
            or payload["terminal_status"] != "completed"
        ):
            raise RunnerError("signed execution evidence does not match the sample")
        upload = payload["upload"]
        expected_upload_hash = self._upload_hashes.get(sample_id)
        if expected_upload_hash is None:
            if upload is not None:
                raise RunnerError("non-upload execution contains upload evidence")
        elif (
            not isinstance(upload, dict)
            or set(upload) != {"payload_sha256", "fixture_sha256"}
            or upload["payload_sha256"] != expected_upload_hash
            or upload["fixture_sha256"]
            != "sha256:"
            + next(
                item["sha256"]
                for item in self.fixture["documents"]
                if item["id"] == fixture_document_id
            )
        ):
            raise RunnerError("signed upload evidence does not match transmitted bytes")
        metrics = payload["metrics"]
        required = {
            "elapsed_ms",
            "cpu_percent",
            "ram_mb",
            "vram_mb",
            "ram_headroom_mb",
            "vram_headroom_mb",
            "queue_depth",
            "concurrency",
            "corpus_chunks",
        }
        optional = {
            "parse_ms",
            "ocr_ms",
            "embedding_ms",
            "retrieval_ms",
            "rerank_ms",
            "generation_ms",
            "throughput_items_per_s",
            "first_token_ms",
        }
        if (
            not isinstance(metrics, dict)
            or not required.issubset(metrics)
            or set(metrics) - required - optional
            or any(
                not isinstance(metrics[field], (int, float))
                or isinstance(metrics[field], bool)
                or metrics[field] < 0
                for field in metrics
            )
            or not 0 <= metrics["cpu_percent"] <= 100
            or metrics["corpus_chunks"] < 1
        ):
            raise RunnerError("signed execution metrics are invalid")
        if queue == "contended" and (
            metrics["concurrency"] < 2 or metrics["queue_depth"] < 1
        ):
            raise RunnerError("contended sample lacks a concurrent queued pair")
        workload = payload["workload"]
        measured_workload = "ingestion" if stage == "ingest" else stage
        if (
            not isinstance(workload, dict)
            or set(workload)
            != {"synchronized", "active_workloads", "resource_observed"}
            or not isinstance(workload["synchronized"], bool)
            or workload["resource_observed"] is not True
            or not isinstance(workload["active_workloads"], list)
            or not all(
                isinstance(item, str) and SAFE_ID.fullmatch(item)
                for item in workload["active_workloads"]
            )
            or measured_workload not in workload["active_workloads"]
            or (
                queue == "contended"
                and (
                    workload["synchronized"] is not True
                    or not {"generation", "ingestion"}.issubset(
                        workload["active_workloads"]
                    )
                )
            )
            or metrics["ram_headroom_mb"] < 4096
            or metrics["vram_headroom_mb"] < 1024
        ):
            raise RunnerError("signed workload/resource safety evidence is invalid")
        self._retrieval_pairs(
            payload["retrieval_candidates"],
            allowed_document_ids=set(self.document_ids.values()),
        )
        evidence_id = payload["evidence_id"]
        if any(
            item["payload"]["evidence_id"] == evidence_id
            for item in self.execution_evidence
        ):
            raise RunnerError("execution evidence ID was replayed")
        self.execution_evidence.append(envelope)
        return {
            "sample_id": sample_id,
            "stage": stage,
            "temperature": temperature,
            "queue": queue,
            "success": True,
            "repetition": repetition,
            "case_id": case_id,
            "document_id": document_id,
            "fixture_document_id": fixture_document_id,
            "profile_evidence_id": profile_evidence_id,
            "execution_evidence_id": evidence_id,
            "terminal_status": payload["terminal_status"],
            "model_residency": "cold" if temperature == "cold" else "warm",
            **metrics,
        }

    def seal_sample(self, sample: dict[str, Any]) -> None:
        execution_evidence_id = sample.get("execution_evidence_id")
        if not isinstance(execution_evidence_id, str):
            raise RunnerError("sample has no execution evidence reference")
        envelope = self._json_request(
            "POST",
            (f"/api/benchmark/samples/{sample['sample_id']}/{execution_evidence_id}"),
            {"sample": sample},
            headers=self._correlation_headers(
                self._correlations[sample["profile_evidence_id"]]
            ),
        )
        payload = self._verified_payload(
            envelope,
            "sample",
            {"sample_id", "execution_evidence_id", "sample"},
        )
        if (
            payload["sample_id"] != sample["sample_id"]
            or payload["execution_evidence_id"] != execution_evidence_id
            or payload["sample"] != sample
        ):
            raise RunnerError(
                "signed sample evidence does not match server observation"
            )
        sample["sample_evidence_id"] = payload["evidence_id"]
        self.sample_attestations.append(envelope)

    def query(
        self,
        case: dict[str, Any],
        temperature: str,
        queue: str,
        repetition: int,
        profile_evidence_id: str,
        *,
        record: bool,
    ) -> None:
        sample_id = f"query-{case['id']}-{temperature}-{queue}-r{repetition}"
        payload = {
            "question": case["query"],
        }
        correlation = self._correlations[profile_evidence_id]
        started = monotonic()
        response = self.transport.request(
            "POST",
            "/api/query/stream",
            json.dumps(payload, separators=(",", ":")).encode(),
            {
                "Content-Type": "application/json",
                "Accept": "text/event-stream",
                "X-Benchmark-Profile-Evidence": profile_evidence_id,
                **self._correlation_headers(correlation),
            },
            stream=True,
        )
        if response.status != 200:
            raise RunnerError(f"query returned status {response.status}")
        first_token_ms: float | None = None
        retrieval_pairs: set[tuple[str, int]] = set()
        rerank_pairs: set[tuple[str, int]] = set()
        citation_pairs: set[tuple[str, int]] = set()
        insufficient: bool | None = None
        term_correct = False
        saw_final = False
        saw_error = False
        saw_sources = False
        final_ms: float | None = None
        for event, value, arrived_at in _sse_events(response.chunks):
            if saw_final:
                raise RunnerError("SSE event arrived after final")
            if event not in {"sources", "token", "final", "error"}:
                raise RunnerError("SSE stream contains an unsupported event")
            if event == "token":
                if (
                    "text" not in value
                    or not isinstance(value["text"], str)
                    or set(value) - {"text", "chat_id", "turn_id"}
                ):
                    raise RunnerError("token event shape is invalid")
                if first_token_ms is None and value["text"]:
                    first_token_ms = (arrived_at - started) * 1000
            elif event == "error":
                saw_error = True
            elif event == "sources":
                if (
                    saw_sources
                    or "sources" not in value
                    or set(value) - {"sources", "chat_id", "turn_id"}
                ):
                    raise RunnerError("sources event shape is invalid")
                if not isinstance(value["sources"], list) or len(value["sources"]) > 6:
                    raise RunnerError("source candidate cardinality is invalid")
                saw_sources = True
                rerank_pairs = self._source_pairs(value.get("sources"))
                known_documents = set(self.document_ids.values())
                if any(
                    document_id not in known_documents
                    for document_id, _page in retrieval_pairs | rerank_pairs
                ):
                    raise RunnerError(
                        "source event contains a document outside the corpus"
                    )
            elif event == "final":
                if (
                    saw_final
                    or not {"answer", "insufficient_context", "citations"}.issubset(
                        value
                    )
                    or set(value)
                    - {
                        "answer",
                        "insufficient_context",
                        "citations",
                        "chat_id",
                        "turn_id",
                    }
                ):
                    raise RunnerError("final event shape is invalid")
                saw_final = True
                if not isinstance(value["insufficient_context"], bool):
                    raise RunnerError("insufficient_context must be boolean")
                insufficient = value["insufficient_context"]
                citation_pairs = self._source_pairs(value.get("citations"))
                if any(
                    document_id not in set(self.document_ids.values())
                    for document_id, _page in citation_pairs
                ):
                    raise RunnerError(
                        "citation references a document outside the corpus"
                    )
                answer = value.get("answer")
                term_correct = isinstance(answer, str) and all(
                    term in answer for term in case["expected_terms"]
                )
                final_ms = (arrived_at - started) * 1000
        if not record:
            return
        execution_envelope = self._json_request(
            "GET",
            f"/api/benchmark/executions/{sample_id}",
            headers={
                "X-Benchmark-Profile-Evidence": profile_evidence_id,
                **self._correlation_headers(correlation),
            },
        )
        execution_payload = execution_envelope.get("payload")
        if not isinstance(execution_payload, dict):
            raise RunnerError("generation execution evidence is malformed")
        retrieval_pairs = self._source_pairs(
            execution_payload.get("retrieval_candidates")
        )
        signed_rerank_pairs = self._source_pairs(
            execution_payload.get("reranked_sources")
        )
        if signed_rerank_pairs != rerank_pairs:
            raise RunnerError("signed rerank evidence differs from legacy SSE sources")
        expected_pairs = {
            (self.document_ids[source["id"]], page)
            for source in case["expected_sources"]
            for page in source["pages"]
        }
        expects_abstention = case["expect_abstention"]
        retrieval_hit = (
            int(expected_pairs.issubset(retrieval_pairs)) if expected_pairs else None
        )
        rerank_hit = (
            int(expected_pairs.issubset(rerank_pairs)) if expected_pairs else None
        )
        citation_correct = (
            not citation_pairs
            if expects_abstention
            else citation_pairs == expected_pairs
        )
        if (
            not saw_sources
            or not saw_final
            or saw_error
            or final_ms is None
            or not citation_correct
            or insufficient is not expects_abstention
            or not term_correct
        ):
            raise RunnerError("query failed benchmark correctness requirements")
        sample = self._execution_sample(
            execution_envelope,
            sample_id=sample_id,
            stage="generation",
            case_id=case["id"],
            document_id=None,
            fixture_document_id=None,
            temperature=temperature,
            queue=queue,
            repetition=repetition,
            profile_evidence_id=profile_evidence_id,
        )
        sample.update(
            {
                "citation_correct": True,
                "abstention_correct": True,
                "expected_terms_correct": True,
                "corpus_documents": len(self.fixture["documents"]),
                "client_final_ms": final_ms,
            }
        )
        if first_token_ms is not None:
            sample["client_first_token_ms"] = first_token_ms
        if retrieval_hit is not None:
            sample["retrieval_hit_at_20"] = retrieval_hit
        if rerank_hit is not None:
            sample["rerank_hit_at_6"] = rerank_hit
        if first_token_ms is None and not expects_abstention:
            raise RunnerError("non-abstaining generation emitted no nonempty token")
        if (first_token_ms is None) != ("first_token_ms" not in sample):
            raise RunnerError(
                "signed first-token metric does not match the observed token stream"
            )
        if first_token_ms is not None and not _timings_agree(
            first_token_ms, sample["first_token_ms"]
        ):
            raise RunnerError(
                "client first-token timing differs from signed server timing"
            )
        if not _timings_agree(final_ms, sample["elapsed_ms"]):
            raise RunnerError("client final timing differs from signed server timing")
        self.seal_sample(sample)
        self.samples.append(sample)

    def measure_stage(
        self,
        stage: str,
        temperature: str,
        queue: str,
        repetition: int,
        profile_evidence_id: str,
        fixture_document_id: str | None,
    ) -> None:
        sample_id = f"{stage}-{temperature}-{queue}-r{repetition}"
        envelope = self._json_request(
            "POST",
            "/api/benchmark/execute",
            {
                "run_id": self.permit.run_id,
                "nonce": self.permit.nonce,
                "sample_id": sample_id,
                "stage": stage,
                "profile_evidence_id": profile_evidence_id,
                "repetition": repetition,
                "fixture_document_id": fixture_document_id,
            },
            headers={
                "X-Benchmark-Profile-Evidence": profile_evidence_id,
                **self._correlation_headers(self._correlations[profile_evidence_id]),
            },
        )
        document_id = (
            self.document_ids[fixture_document_id]
            if fixture_document_id is not None
            else None
        )
        sample = self._execution_sample(
            envelope,
            sample_id=sample_id,
            stage=stage,
            case_id=None,
            document_id=document_id,
            fixture_document_id=fixture_document_id,
            temperature=temperature,
            queue=queue,
            repetition=repetition,
            profile_evidence_id=profile_evidence_id,
        )
        sample["corpus_documents"] = len(self.fixture["documents"])
        if stage == "ingest":
            sample["document_kind"] = "digital"
        self.seal_sample(sample)
        self.samples.append(sample)

    def warmup_stage(
        self,
        stage: str,
        profile_evidence_id: str,
        fixture_document_id: str | None,
    ) -> None:
        response = self.transport.request(
            "POST",
            f"/api/benchmark/warmups/{stage}",
            json.dumps(
                {
                    "run_id": self.permit.run_id,
                    "nonce": self.permit.nonce,
                    "profile_evidence_id": profile_evidence_id,
                    "fixture_document_id": fixture_document_id,
                },
                separators=(",", ":"),
            ).encode(),
            {
                "Content-Type": "application/json",
                "X-Benchmark-Profile-Evidence": profile_evidence_id,
                **self._correlation_headers(self._correlations[profile_evidence_id]),
            },
        )
        if response.status != 204 or response.body:
            raise RunnerError("benchmark warm-up was not acknowledged")

    def cleanup(self) -> None:
        errors: list[str] = []
        for document_id in sorted(self.created_document_ids):
            try:
                response = self.transport.request(
                    "DELETE",
                    f"/api/documents/{document_id}",
                    headers={
                        "X-Benchmark-Nonce": self.permit.nonce,
                        "X-Benchmark-Namespace": self.permit.namespace,
                        "X-Benchmark-Owner": self.permit.run_id,
                    },
                )
                if response.status != 204:
                    raise RunnerError(f"delete returned status {response.status}")
                deadline = monotonic() + self.timeout_s
                while monotonic() < deadline:
                    evidence = self._json_request(
                        "GET", f"/api/benchmark/cleanup/{document_id}"
                    )
                    if set(evidence) != {
                        "document_id",
                        "deleted",
                        "outbox_completed",
                    }:
                        raise RunnerError("cleanup evidence shape is invalid")
                    if (
                        evidence["document_id"] == document_id
                        and evidence["deleted"] is True
                        and evidence["outbox_completed"] is True
                    ):
                        break
                    time.sleep(0.25)
                else:
                    raise RunnerError("cleanup evidence timed out")
            except (RunnerError, KeyError, TypeError) as exc:
                errors.append(f"{document_id}:{type(exc).__name__}")
        if self.pre_attestation is not None:
            try:
                envelope = _json_response(
                    self.transport.request("GET", "/api/benchmark/attestation/post"),
                    "benchmark post-attestation",
                )
                payload = self._verified_payload(
                    envelope,
                    "post",
                    {
                        "namespace_state",
                        "namespace_owner_run_id",
                        "owned_document_ids",
                    },
                )
                if (
                    payload["namespace_state"] != "empty"
                    or payload["namespace_owner_run_id"] is not None
                    or payload["owned_document_ids"] != []
                ):
                    raise RunnerError("post-attestation namespace is not empty")
                self.post_attestation = envelope
            except RunnerError as exc:
                errors.append(f"post-attestation:{type(exc).__name__}")
        if errors:
            raise RunnerError("cleanup failed: " + ",".join(errors))

    def run(self) -> list[dict[str, Any]]:
        self.transport.bootstrap()
        self.attest()
        primary_error: BaseException | None = None
        try:
            for index, document in enumerate(self.fixture["documents"], start=1):
                sample_id = f"ingest-{document['id']}-r1"
                ingest_profile = self.set_profile("cold", "queue-free", sample_id)
                document_id, job_id = self.upload(document, sample_id, ingest_profile)
                result = self.wait_for_job(job_id, sample_id, ingest_profile)
                sample = self._execution_sample(
                    result,
                    sample_id=sample_id,
                    stage="ingest",
                    case_id=None,
                    document_id=document_id,
                    fixture_document_id=document["id"],
                    temperature="cold",
                    queue="queue-free",
                    repetition=1,
                    profile_evidence_id=ingest_profile,
                )
                sample.update(
                    {
                        "corpus_documents": index,
                        "document_kind": document["kind"],
                    }
                )
                if (
                    "throughput_items_per_s" not in sample
                    or (document["kind"] == "digital" and "parse_ms" not in sample)
                    or (document["kind"] == "scanned" and "ocr_ms" not in sample)
                ):
                    raise RunnerError(
                        "ingestion evidence lacks kind-specific throughput timings"
                    )
                self.seal_sample(sample)
                self.samples.append(sample)
            expected_fixture_ids = {
                document["id"] for document in self.fixture["documents"]
            }
            if (
                len(expected_fixture_ids) != 10
                or set(self.document_ids) != expected_fixture_ids
                or len(set(self.document_ids.values())) != 10
            ):
                raise RunnerError(
                    "successful uploads did not produce the exact ten-document corpus"
                )
            representative = {
                "ingest": "syn-digital-01",
                "ocr": "syn-scanned-01",
                "embedding": "syn-digital-01",
                "retrieval": "syn-digital-01",
                "rerank": "syn-digital-01",
                "api": None,
            }
            for stage, fixture_document_id in representative.items():
                for temperature, queue in sorted(REQUIRED_PROFILES):
                    warmup_id = f"{stage}-{temperature}-{queue}-warmup"
                    profile_evidence_id = self.set_profile(
                        temperature, queue, warmup_id
                    )
                    self.warmup_stage(stage, profile_evidence_id, fixture_document_id)
                    for repetition in range(1, 6):
                        sample_id = f"{stage}-{temperature}-{queue}-r{repetition}"
                        profile_evidence_id = self.set_profile(
                            temperature, queue, sample_id
                        )
                        self.measure_stage(
                            stage,
                            temperature,
                            queue,
                            repetition,
                            profile_evidence_id,
                            fixture_document_id,
                        )
            evaluation = json.loads(EVALUATION_PATH.read_text(encoding="utf-8"))
            for temperature, queue in sorted(REQUIRED_PROFILES):
                for case in evaluation["cases"]:
                    warmup_id = f"query-{case['id']}-{temperature}-{queue}-r0"
                    profile_evidence_id = self.set_profile(
                        temperature, queue, warmup_id
                    )
                    self.query(
                        case,
                        temperature,
                        queue,
                        0,
                        profile_evidence_id,
                        record=False,
                    )
                    for repetition in range(1, 6):
                        sample_id = (
                            f"query-{case['id']}-{temperature}-{queue}-r{repetition}"
                        )
                        profile_evidence_id = self.set_profile(
                            temperature, queue, sample_id
                        )
                        self.query(
                            case,
                            temperature,
                            queue,
                            repetition,
                            profile_evidence_id,
                            record=True,
                        )
            _validate_samples(self.samples)
            return self.samples
        except BaseException as exc:
            primary_error = exc
            raise
        finally:
            try:
                self.cleanup()
            except BaseException:
                if primary_error is None:
                    raise
                raise RunnerError(
                    "cleanup failed after benchmark failure"
                ) from primary_error


def run_guarded(
    base_url: str,
    session_file: Path,
    fixture_manifest: Path,
    output: str,
) -> Path:
    target = validate_target(base_url)
    transport = CookieSessionTransport(target, session_file)
    guarded = GuardedRunner(transport, fixture_manifest)
    samples = guarded.run()
    manifest = create_manifest(
        samples=samples,
        run_id=transport.permit.run_id,
        command="python -m benchmarks.v3.runner run [safe-arguments-redacted]",
    )
    signed_context = guarded._signed_context
    if (
        signed_context is None
        or signed_context["source_revision"] != manifest["source"]["revision"]
        or signed_context["source_content_identity"]
        != manifest["source"]["content_identity"]
        or signed_context["runtime_artifact_hashes"]["dependency_lock"]
        != manifest["dependencies"]["lock_sha256"]
    ):
        raise RunnerError("signed source/runtime identity differs from local artifacts")
    manifest["dataset"]["fixture_manifest_path"] = _repo_relative(
        fixture_manifest, ROOT
    )
    manifest["dataset"]["fixture_identity"] = guarded.fixture["corpus_identity"]
    manifest["dataset"]["fixture_hashes"] = {
        document["id"]: document["sha256"] for document in guarded.fixture["documents"]
    }
    manifest["benchmark_evidence"] = {
        "ed25519_public_key": guarded.public_key_value,
        "fingerprint": guarded.public_key_fingerprint,
        "pre_attestation": guarded.pre_attestation,
        "post_attestation": guarded.post_attestation,
        "profile_attestations": list(guarded.profile_attestations.values()),
        "executions": guarded.execution_evidence,
        "sample_attestations": guarded.sample_attestations,
    }
    validate_manifest(manifest)
    destination = ensure_results_path(output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return destination


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run")
    run.add_argument("--base-url", required=True)
    run.add_argument("--session-file", required=True, type=Path)
    run.add_argument("--fixture-manifest", required=True, type=Path)
    run.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    if args.command == "run":
        print(
            run_guarded(
                args.base_url,
                args.session_file,
                args.fixture_manifest,
                args.output,
            )
        )
        return 0
    return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
