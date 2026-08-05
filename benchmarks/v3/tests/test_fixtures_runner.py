import base64
import hashlib
import json
import os
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import benchmarks.v3.fixtures as fixtures
import benchmarks.v3.runner as runner
import benchmarks.v3.trust as trust
import pytest
from benchmarks.v3.harness import HarnessError, load_evaluation
from pypdf import PdfReader


class FakeTransport:
    def __init__(self, fixture, *, attestation_override=None):
        self.permit = runner.BenchmarkPermit(
            run_id="run-001",
            deployment_id="isolated-v3",
            store_id="store-isolated-001",
            namespace="benchmark-run-001",
            nonce="nonce-001",
        )
        self.attestation_override = attestation_override or {}
        self.fixture = fixture
        self.calls = []
        self.bootstrapped = False
        self.delete_status = 204
        self.public_key = bytes.fromhex(
            "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a"
        )
        self.public_key_value = base64.b64encode(self.public_key).decode()
        self.public_key_fingerprint = trust.key_fingerprint(self.public_key)
        self.sequence = 0

    @staticmethod
    def _sign(payload):
        seed = bytes.fromhex(
            "9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60"
        )
        digest = hashlib.sha512(seed).digest()
        scalar = int.from_bytes(
            bytes([digest[0] & 248]) + digest[1:31] + bytes([(digest[31] & 63) | 64]),
            "little",
        )
        prefix = digest[32:]
        message = trust.canonical_json(payload)
        r = (
            int.from_bytes(hashlib.sha512(prefix + message).digest(), "little")
            % trust._L
        )
        point = trust._scalar_mult(trust._B, r)
        encoded_r = (point[1] | ((point[0] & 1) << 255)).to_bytes(32, "little")
        public_key = bytes.fromhex(
            "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a"
        )
        challenge = (
            int.from_bytes(
                hashlib.sha512(encoded_r + public_key + message).digest(), "little"
            )
            % trust._L
        )
        signature = encoded_r + ((r + challenge * scalar) % trust._L).to_bytes(
            32, "little"
        )
        return {
            "payload": payload,
            "signature": base64.b64encode(signature).decode(),
        }

    def envelope(self, kind, **extra):
        now = datetime.now(UTC)
        self.sequence += 1
        payload = {
            "kind": kind,
            "evidence_id": f"{kind}-evidence-{self.sequence}",
            "issued_at": (now - timedelta(seconds=1)).isoformat(),
            "expires_at": (now + timedelta(minutes=5)).isoformat(),
            "run_id": self.permit.run_id,
            "nonce": self.permit.nonce,
            "deployment_id": self.permit.deployment_id,
            "store_id": self.permit.store_id,
            "store_mode": "isolated-benchmark",
            "namespace": self.permit.namespace,
            "corpus_identity": self.fixture["corpus_identity"],
            "fixture_identity": self.fixture["evaluation_identity"],
            "sequence": self.sequence,
            "source_revision": "a" * 40,
            "source_content_identity": "sha256:" + "b" * 64,
            "runtime_artifact_hashes": {
                "dependency_lock": "sha256:" + "c" * 64,
                "container_inventory": "sha256:" + "d" * 64,
                "model_inventory": "sha256:" + "e" * 64,
                "server_artifact": "sha256:" + "f" * 64,
                "schema_evidence": "sha256:" + "1" * 64,
                "server_evidence": "sha256:" + "2" * 64,
            },
            **extra,
        }
        return self._sign(payload)

    def bootstrap(self):
        self.bootstrapped = True

    def request(self, method, path, body=None, headers=None, *, stream=False):
        self.calls.append((method, path, body, headers, stream))
        if path == "/api/benchmark/attestation/pre":
            extra = {
                "namespace_state": "empty",
                "namespace_owner_run_id": None,
                "owned_document_ids": [],
                "measurement_profiles": [
                    {"temperature": temperature, "queue": queue}
                    for temperature, queue in sorted(runner.REQUIRED_PROFILES)
                ],
            }
            extra.update(self.attestation_override)
            return _json(self.envelope("pre", **extra))
        if path == "/api/benchmark/attestation/post":
            return _json(
                self.envelope(
                    "post",
                    namespace_state="empty",
                    namespace_owner_run_id=None,
                    owned_document_ids=[],
                )
            )
        if path == "/api/benchmark/profile":
            supplied = json.loads(body)
            correlation = {
                "sample_id": headers["X-Benchmark-Sample-Id"],
                "nonce": headers["X-Benchmark-Nonce"],
                "profile_id": headers["X-Benchmark-Profile-Id"],
                "evidence_id": headers["X-Benchmark-Evidence-Id"],
                "execution_id": headers["X-Benchmark-Execution-Id"],
            }
            return _json(
                self.envelope(
                    "profile",
                    profile={
                        "temperature": supplied["temperature"],
                        "queue": supplied["queue"],
                    },
                    applied=True,
                    profile_token="profile-token-001",
                    active_requests=1,
                    queued_requests=(1 if supplied["queue"] == "contended" else 0),
                    correlation=correlation,
                )
            )
        if path.startswith("/api/benchmark/cleanup/"):
            return _json(
                {
                    "document_id": path.rsplit("/", 1)[1],
                    "deleted": True,
                    "outbox_completed": True,
                }
            )
        if method == "DELETE":
            return runner.TransportResponse(self.delete_status)
        raise AssertionError((method, path))


def _json(value, status=200):
    return runner.TransportResponse(status, json.dumps(value).encode())


def _generated(tmp_path, monkeypatch):
    generated = tmp_path / "generated"
    monkeypatch.setattr(fixtures, "GENERATED_ROOT", generated)
    manifest = fixtures.generate_fixture("dataset")
    monkeypatch.setattr(runner, "GENERATED_ROOT", generated)
    return manifest, generated / "dataset" / "fixture-manifest.json"


def _correlation(sample_id: str) -> dict[str, str]:
    return {
        "sample_id": sample_id,
        "nonce": "nonce-001",
        "profile_id": "profile-request-001",
        "evidence_id": "request-evidence-001",
        "execution_id": "execution-request-001",
    }


def test_session_contract_has_fixed_cookie_names_and_requires_configured_key(
    tmp_path, monkeypatch
):
    session = {
        "schema_version": 1,
        "origin": "http://127.0.0.1:18080",
        "rag_session": "a" * 32,
        "csrf_token": "c" * 32,
        "benchmark": {
            "run_id": "run-001",
            "deployment_id": "isolated-v3",
            "store_id": "store-isolated-001",
            "namespace": "benchmark-run-001",
            "nonce": "nonce-001",
            "adopted_document_ids": [],
        },
    }
    path = tmp_path / "session.json"
    path.write_text(json.dumps(session), encoding="utf-8")
    monkeypatch.setattr(runner, "_private_file", lambda supplied, **_kwargs: supplied)
    target = runner.validate_target(session["origin"])
    assert runner._load_session(path, target)["rag_session"] == "a" * 32
    session["session_cookie"] = {"name": "evil", "value": "b" * 32}
    path.write_text(json.dumps(session), encoding="utf-8")
    with pytest.raises(runner.RunnerError, match="shape"):
        runner._load_session(path, target)

    monkeypatch.setattr(
        runner,
        "_load_session",
        lambda *_args: {
            key: value for key, value in session.items() if key != "session_cookie"
        },
    )
    trust_path = tmp_path / "trust.json"
    trust_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "status": "unconfigured",
                "ed25519_public_key": None,
                "fingerprint": None,
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(runner.RunnerError, match="not configured"):
        runner.CookieSessionTransport(target, path, trust_path)


def test_bootstrap_installs_csrf_from_nullable_user_contract(tmp_path, monkeypatch):
    session = {
        "schema_version": 1,
        "origin": "http://127.0.0.1:18080",
        "rag_session": "a" * 32,
        "csrf_token": "c" * 32,
        "benchmark": {
            "run_id": "run-001",
            "deployment_id": "isolated-v3",
            "store_id": "store-isolated-001",
            "namespace": "benchmark-run-001",
            "nonce": "nonce-001",
            "adopted_document_ids": [],
        },
    }
    session_path = tmp_path / "session.json"
    session_path.write_text(json.dumps(session), encoding="utf-8")
    monkeypatch.setattr(runner, "_private_file", lambda supplied, **_kwargs: supplied)
    public_key = bytes.fromhex(
        "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a"
    )
    trust_path = tmp_path / "trust.json"
    trust_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "status": "configured",
                "ed25519_public_key": base64.b64encode(public_key).decode(),
                "fingerprint": trust.key_fingerprint(public_key),
            }
        ),
        encoding="utf-8",
    )
    transport = runner.CookieSessionTransport(
        runner.validate_target(session["origin"]), session_path, trust_path
    )
    csrf = session["csrf_token"]

    def authenticated_me(*_args, **_kwargs):
        assert {cookie.name: cookie.value for cookie in transport._jar} == {
            "rag_session": "a" * 32,
            "csrf_token": csrf,
        }
        return _json(
            {
                "user": {
                    "id": str(uuid.uuid4()),
                    "username": "benchmark",
                    "display_name": "Benchmark",
                    "role": "admin",
                    "status": "active",
                },
                "csrf_token": csrf,
            }
        )

    monkeypatch.setattr(transport, "_open", authenticated_me)
    transport.bootstrap()
    assert {cookie.name: cookie.value for cookie in transport._jar} == {
        "rag_session": "a" * 32,
        "csrf_token": csrf,
    }

    transport._bootstrapped = False
    monkeypatch.setattr(
        transport,
        "_open",
        lambda *_args, **_kwargs: _json({"user": None, "csrf_token": csrf}),
    )
    with pytest.raises(runner.RunnerError, match="bootstrap failed"):
        transport.bootstrap()

    transport._bootstrapped = False
    monkeypatch.setattr(
        transport,
        "_open",
        lambda *_args, **_kwargs: _json(
            {
                "user": {"id": str(uuid.uuid4())},
                "csrf_token": "d" * 32,
            }
        ),
    )
    with pytest.raises(runner.RunnerError, match="bootstrap failed"):
        transport.bootstrap()


@pytest.mark.skipif(os.name != "nt", reason="Windows DACL contract")
def test_windows_session_acl_requires_current_owner_and_sid_allowlist(
    tmp_path, monkeypatch
):
    private = tmp_path / "private"
    private.mkdir()
    path = private / "session.json"
    path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(runner, "PRIVATE_ROOT", private)

    monkeypatch.setattr(
        runner,
        "_windows_private_acl",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            runner.RunnerError("DACL is not an explicit SID allowlist")
        ),
    )
    monkeypatch.setattr(runner, "_windows_open_directory", lambda *_args: 0)
    with pytest.raises(runner.RunnerError, match="allowlist"):
        runner._private_file(path)


@pytest.mark.skipif(os.name != "nt", reason="Windows DACL contract")
def test_windows_session_acl_validates_parent_and_file(tmp_path, monkeypatch):
    private = tmp_path / "private"
    private.mkdir()
    path = private / "session.json"
    path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(runner, "PRIVATE_ROOT", private)
    calls = []

    monkeypatch.setattr(
        runner,
        "_windows_private_acl",
        lambda handle, *, directory: calls.append((handle, directory)),
    )
    monkeypatch.setattr(runner, "_windows_open_directory", lambda *_args: 0)
    assert runner._private_file(path) == path.resolve()
    assert len(calls) == 2
    assert {directory for _handle, directory in calls} == {False, True}


@pytest.mark.skipif(os.name != "nt", reason="Windows DACL contract")
@pytest.mark.parametrize(
    "override",
    [
        {"protected": False},
        {"dacl_present": False},
        {"null_dacl": True},
        {"entries": []},
        {
            "entries": [
                {
                    "sid": "S-1-5-18",
                    "type": "Allow",
                    "inherited": False,
                    "rights": 1,
                }
            ]
        },
    ],
)
def test_windows_session_acl_rejects_unprotected_null_or_no_current_allow(
    tmp_path, monkeypatch, override
):
    private = tmp_path / "private"
    private.mkdir()
    path = private / "session.json"
    path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(runner, "PRIVATE_ROOT", private)
    acl = {
        "current": "S-1-5-21-1000",
        "owner": "S-1-5-21-1000",
        "protected": True,
        "dacl_present": True,
        "null_dacl": False,
        "entries": [
            {
                "sid": "S-1-5-21-1000",
                "type": "Allow",
                "inherited": False,
                "rights": 1,
            }
        ],
    }
    acl.update(override)

    monkeypatch.setattr(
        runner,
        "_windows_private_acl",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            runner.RunnerError("DACL is not an explicit SID allowlist")
        ),
    )
    monkeypatch.setattr(runner, "_windows_open_directory", lambda *_args: 0)
    with pytest.raises(runner.RunnerError, match="allowlist"):
        runner._private_file(path)


def test_session_read_rejects_retained_handle_identity_change(tmp_path, monkeypatch):
    session = {
        "schema_version": 1,
        "origin": "http://127.0.0.1:18080",
        "rag_session": "a" * 32,
        "csrf_token": "c" * 32,
        "benchmark": {
            "run_id": "run-001",
            "deployment_id": "isolated-v3",
            "store_id": "store-isolated-001",
            "namespace": "benchmark-run-001",
            "nonce": "nonce-001",
            "adopted_document_ids": [],
        },
    }
    path = tmp_path / "session.json"
    path.write_text(json.dumps(session), encoding="utf-8")
    monkeypatch.setattr(runner, "_private_file", lambda supplied, **_kwargs: supplied)
    real_fstat = runner.os.fstat

    def changed_fstat(descriptor):
        value = real_fstat(descriptor)
        return type(
            "ChangedStat",
            (),
            {
                "st_dev": value.st_dev,
                "st_ino": value.st_ino,
                "st_size": value.st_size + 1,
                "st_mtime_ns": value.st_mtime_ns,
            },
        )()

    monkeypatch.setattr(runner.os, "fstat", changed_fstat)
    with pytest.raises(runner.RunnerError, match="changed while it was open"):
        runner._load_session(path, runner.validate_target(session["origin"]))


def test_fixture_generation_contains_every_digital_fact_and_scanned_image_fact(
    tmp_path, monkeypatch
):
    manifest, fixture_path = _generated(tmp_path, monkeypatch)
    evaluation = load_evaluation()
    by_id = {item["id"]: item for item in evaluation["documents"]}
    for item in manifest["documents"]:
        path = fixture_path.parent / item["filename"]
        payload = path.read_bytes()
        assert hashlib.sha256(payload).hexdigest() == item["sha256"]
        reader = PdfReader(str(path))
        assert len(reader.pages) == item["pages"]
        expected = by_id[item["id"]]["page_content"]
        if item["kind"] == "digital":
            assert [page.extract_text() for page in reader.pages] == expected
        else:
            assert all(page.extract_text() in (None, "") for page in reader.pages)
            for fact in expected:
                _width, _height, pixels = fixtures._scan_pixels(fact)
                assert pixels in payload


def test_fixture_generation_rejects_bytes_not_in_tracked_registry(
    tmp_path, monkeypatch
):
    generated = tmp_path / "generated"
    monkeypatch.setattr(fixtures, "GENERATED_ROOT", generated)
    original = fixtures._text_pdf
    monkeypatch.setattr(
        fixtures, "_text_pdf", lambda pages: original(pages) + b"tampered"
    )
    with pytest.raises(HarnessError, match="approved bytes"):
        fixtures.generate_fixture("dataset")


@pytest.mark.parametrize("bad_name", ["../outside.pdf", "folder/file.pdf", "C:\\x.pdf"])
def test_fixture_manifest_rejects_traversal_and_absolute_names(
    tmp_path, monkeypatch, bad_name
):
    manifest, fixture_path = _generated(tmp_path, monkeypatch)
    manifest["documents"][0]["filename"] = bad_name
    fixture_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(runner.RunnerError, match="basename"):
        runner._fixture_manifest(fixture_path)


def test_fixture_manifest_rejects_symlink_escape_when_supported(tmp_path, monkeypatch):
    _manifest, fixture_path = _generated(tmp_path, monkeypatch)
    outside = tmp_path / "outside.pdf"
    outside.write_bytes(b"%PDF-1.4\n%%EOF")
    linked = fixture_path.parent / "syn-digital-01.pdf"
    linked.unlink()
    try:
        linked.symlink_to(outside)
    except OSError:
        pytest.skip("symlinks are unavailable")
    with pytest.raises(runner.RunnerError, match="link|reparse"):
        runner._fixture_manifest(fixture_path)


def test_fixture_source_pages_are_bound_to_evaluation(tmp_path, monkeypatch):
    manifest, fixture_path = _generated(tmp_path, monkeypatch)
    manifest["documents"][0]["source_tokens"][0]["token"] = "FORGED"
    records = json.dumps(
        manifest["documents"], sort_keys=True, separators=(",", ":")
    ).encode()
    manifest["corpus_identity"] = hashlib.sha256(records).hexdigest()
    fixture_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(runner.RunnerError, match="approved|bound to the evaluation"):
        runner._fixture_manifest(fixture_path)


def test_profile_and_upload_send_bound_correlation_and_exact_payload_hash(
    tmp_path, monkeypatch
):
    manifest, fixture_path = _generated(tmp_path, monkeypatch)
    transport = FakeTransport(manifest)
    guarded = runner.GuardedRunner(transport, fixture_path)
    sample_id = "ingest-syn-digital-01-r1"
    profile_id = guarded.set_profile("cold", "queue-free", sample_id)
    profile_call = transport.calls[-1]
    profile_headers = profile_call[3]
    assert {
        "X-Benchmark-Sample-Id",
        "X-Benchmark-Nonce",
        "X-Benchmark-Profile-Id",
        "X-Benchmark-Evidence-Id",
        "X-Benchmark-Execution-Id",
    }.issubset(profile_headers)

    captured = {}

    def upload_request(method, path, body=None, headers=None, *, stream=False):
        captured.update(body=body, headers=headers)
        return _json(
            {
                "document_id": "11111111-1111-1111-1111-111111111111",
                "job_id": "22222222-2222-2222-2222-222222222222",
                "status": "queued",
                "duplicate_of": None,
            }
        )

    monkeypatch.setattr(transport, "request", upload_request)
    document = next(
        item for item in manifest["documents"] if item["id"] == "syn-digital-01"
    )
    guarded.upload(document, sample_id, profile_id)
    assert captured["headers"]["X-Benchmark-Upload-SHA256"] == (
        "sha256:" + hashlib.sha256(captured["body"]).hexdigest()
    )
    assert captured["headers"]["X-Benchmark-Profile-Evidence"] == profile_id
    assert captured["headers"]["X-Benchmark-Sample-Id"] == sample_id
    execution = transport.envelope(
        "execution",
        sample_id=sample_id,
        stage="ingest",
        case_id=None,
        document_id="11111111-1111-1111-1111-111111111111",
        fixture_document_id="syn-digital-01",
        profile={"temperature": "cold", "queue": "queue-free"},
        repetition=1,
        profile_evidence_id=profile_id,
        success=True,
        terminal_status="completed",
        metrics={
            "elapsed_ms": 10,
            "cpu_percent": 10,
            "ram_mb": 1000,
            "vram_mb": 1000,
            "ram_headroom_mb": 5000,
            "vram_headroom_mb": 1500,
            "queue_depth": 0,
            "concurrency": 1,
            "corpus_chunks": 1,
            "parse_ms": 5,
            "throughput_items_per_s": 1,
        },
        retrieval_candidates=[],
        reranked_sources=[],
        workload={
            "synchronized": False,
            "active_workloads": ["ingestion"],
            "resource_observed": True,
        },
        correlation=guarded._correlations[profile_id],
        upload={
            "payload_sha256": "sha256:" + "0" * 64,
            "fixture_sha256": "sha256:" + document["sha256"],
        },
    )
    with pytest.raises(runner.RunnerError, match="upload evidence"):
        guarded._execution_sample(
            execution,
            sample_id=sample_id,
            stage="ingest",
            case_id=None,
            document_id="11111111-1111-1111-1111-111111111111",
            fixture_document_id="syn-digital-01",
            temperature="cold",
            queue="queue-free",
            repetition=1,
            profile_evidence_id=profile_id,
        )


def test_alternate_primary_port_cannot_bypass_server_attestation(tmp_path, monkeypatch):
    _manifest, fixture_path = _generated(tmp_path, monkeypatch)
    assert runner.validate_target("http://127.0.0.1:18081").base_url.endswith("18081")
    transport = FakeTransport(
        _manifest, attestation_override={"store_id": "primary-store"}
    )
    guarded = runner.GuardedRunner(transport, fixture_path)
    transport.bootstrap()
    with pytest.raises(runner.RunnerError, match="evidence"):
        guarded.attest()
    assert not any(method in runner.MUTATING_METHODS for method, *_ in transport.calls)


@pytest.mark.parametrize(
    "override",
    [
        {"nonce": "wrong"},
        {"store_id": "primary-store"},
        {"store_mode": "primary"},
        {"namespace_state": "occupied"},
        {
            "namespace_state": "runner-owned",
            "namespace_owner_run_id": "other-run",
            "owned_document_ids": ["11111111-1111-1111-1111-111111111111"],
        },
    ],
)
def test_attestation_mismatch_or_occupied_namespace_fails_closed(
    tmp_path, monkeypatch, override
):
    _manifest, fixture_path = _generated(tmp_path, monkeypatch)
    transport = FakeTransport(_manifest, attestation_override=override)
    guarded = runner.GuardedRunner(transport, fixture_path)
    transport.bootstrap()
    with pytest.raises(runner.RunnerError):
        guarded.attest()


def test_empty_pre_attestation_rejects_adopted_permit_document(tmp_path, monkeypatch):
    _manifest, fixture_path = _generated(tmp_path, monkeypatch)
    transport = FakeTransport(_manifest)
    transport.permit = runner.BenchmarkPermit(
        run_id=transport.permit.run_id,
        deployment_id=transport.permit.deployment_id,
        store_id=transport.permit.store_id,
        namespace=transport.permit.namespace,
        nonce=transport.permit.nonce,
        adopted_document_ids=("11111111-1111-1111-1111-111111111111",),
    )
    guarded = runner.GuardedRunner(transport, fixture_path)
    with pytest.raises(runner.RunnerError, match="empty namespace"):
        guarded.attest()


def test_attestation_rejects_invalid_signature_and_expiry(tmp_path, monkeypatch):
    manifest, fixture_path = _generated(tmp_path, monkeypatch)
    transport = FakeTransport(manifest)
    guarded = runner.GuardedRunner(transport, fixture_path)
    original = transport.request

    def corrupt(method, path, body=None, headers=None, *, stream=False):
        response = original(method, path, body, headers, stream=stream)
        envelope = json.loads(response.body)
        envelope["signature"] = base64.b64encode(b"\0" * 64).decode()
        return _json(envelope)

    monkeypatch.setattr(transport, "request", corrupt)
    with pytest.raises(runner.RunnerError, match="signature"):
        guarded.attest()

    expired = transport.envelope(
        "pre",
        namespace_state="empty",
        namespace_owner_run_id=None,
        owned_document_ids=[],
        measurement_profiles=[
            {"temperature": temperature, "queue": queue}
            for temperature, queue in sorted(runner.REQUIRED_PROFILES)
        ],
    )
    expired["payload"]["issued_at"] = "2020-01-01T00:00:00+00:00"
    expired["payload"]["expires_at"] = "2020-01-01T00:01:00+00:00"
    expired = transport._sign(expired["payload"])
    monkeypatch.setattr(
        transport,
        "request",
        lambda *_args, **_kwargs: _json(expired),
    )
    with pytest.raises(runner.RunnerError, match="currently valid"):
        guarded.attest()


def test_cleanup_uses_only_exact_created_ids_and_fails_closed(tmp_path, monkeypatch):
    _manifest, fixture_path = _generated(tmp_path, monkeypatch)
    transport = FakeTransport(_manifest)
    guarded = runner.GuardedRunner(transport, fixture_path)
    document_id = str(uuid.UUID("11111111-1111-1111-1111-111111111111"))
    guarded.created_document_ids.add(document_id)
    guarded.cleanup()
    assert [(method, path) for method, path, *_ in transport.calls] == [
        ("DELETE", f"/api/documents/{document_id}"),
        ("GET", f"/api/benchmark/cleanup/{document_id}"),
    ]
    transport.delete_status = 409
    with pytest.raises(runner.RunnerError, match="cleanup failed"):
        guarded.cleanup()


def test_incremental_sse_measures_first_token_before_final_and_separates_quality(
    tmp_path, monkeypatch
):
    _manifest, fixture_path = _generated(tmp_path, monkeypatch)
    transport = FakeTransport(_manifest)
    guarded = runner.GuardedRunner(transport, fixture_path)
    source_uuid = "11111111-1111-1111-1111-111111111111"
    guarded.document_ids["syn-digital-04"] = source_uuid
    case = next(item for item in load_evaluation()["cases"] if item["id"] == "case-06")
    correlation = _correlation("query-case-06-warm-queue-free-r1")
    profile = transport.envelope(
        "profile",
        profile={"temperature": "warm", "queue": "queue-free"},
        applied=True,
        profile_token="profile-token-001",
        active_requests=1,
        queued_requests=0,
        correlation=correlation,
    )
    profile_id = profile["payload"]["evidence_id"]
    guarded.profile_attestations[profile_id] = profile
    guarded._correlations[profile_id] = correlation
    execution = transport.envelope(
        "execution",
        sample_id="query-case-06-warm-queue-free-r1",
        stage="generation",
        case_id="case-06",
        document_id=None,
        fixture_document_id=None,
        profile={"temperature": "warm", "queue": "queue-free"},
        repetition=1,
        profile_evidence_id=profile_id,
        success=True,
        terminal_status="completed",
        metrics={
            "elapsed_ms": 400,
            "first_token_ms": 300,
            "cpu_percent": 10,
            "ram_mb": 1000,
            "vram_mb": 2000,
            "ram_headroom_mb": 5000,
            "vram_headroom_mb": 1500,
            "queue_depth": 0,
            "concurrency": 1,
            "corpus_chunks": 25,
            "retrieval_ms": 20,
            "rerank_ms": 30,
            "generation_ms": 40,
        },
        retrieval_candidates=[],
        reranked_sources=[
            {
                "document_id": source_uuid,
                "page_start": 2,
                "page_end": 2,
            }
        ],
        workload={
            "synchronized": False,
            "active_workloads": ["generation"],
            "resource_observed": True,
        },
        correlation=correlation,
        upload=None,
    )
    sources = (
        "event: sources\ndata: "
        + json.dumps(
            {
                "sources": [
                    {
                        "label": "S1",
                        "chunk_id": "22222222-2222-2222-2222-222222222222",
                        "filename": "syn-digital-04.pdf",
                        "document_id": source_uuid,
                        "display_name": "syn-digital-04.pdf",
                        "logical_path": "/syn-digital-04.pdf",
                        "page_start": 2,
                        "page_end": 2,
                    }
                ],
            }
        )
        + "\n\n"
    )
    token = 'event: token\ndata: {"text": "SYN-ALPHA-42 café"}\n\n'
    final = (
        "event: final\ndata: "
        + json.dumps(
            {
                "answer": "SYN-ALPHA-42",
                "insufficient_context": False,
                "citations": [
                    {
                        "label": "S1",
                        "chunk_id": "22222222-2222-2222-2222-222222222222",
                        "filename": "syn-digital-04.pdf",
                        "document_id": source_uuid,
                        "display_name": "syn-digital-04.pdf",
                        "logical_path": "/syn-digital-04.pdf",
                        "page_start": 2,
                        "page_end": 2,
                    }
                ],
            }
        )
        + "\n\n"
    )
    combined = (sources + token).encode()
    split = combined.index("é".encode()) + 1
    events = [combined[:split], combined[split:], final.encode()]

    def request(method, path, body=None, headers=None, *, stream=False):
        if path == "/api/benchmark/executions/query-case-06-warm-queue-free-r1":
            return _json(execution)
        supplied = json.loads(body)
        assert set(supplied) == {"question"}
        return runner.TransportResponse(200, chunks=events)

    monkeypatch.setattr(transport, "request", request)
    monkeypatch.setattr(
        guarded,
        "seal_sample",
        lambda sample: sample.update(sample_evidence_id="sample-evidence"),
    )
    ticks = iter([0.0, 0.1, 0.3, 0.5])
    monkeypatch.setattr(runner, "monotonic", lambda: next(ticks))
    guarded.query(case, "warm", "queue-free", 1, profile_id, record=True)
    sample = guarded.samples[0]
    assert sample["first_token_ms"] == 300
    assert sample["elapsed_ms"] == 400
    assert sample["client_first_token_ms"] == 300
    assert sample["client_final_ms"] == 500
    assert sample["retrieval_hit_at_20"] == 0
    assert sample["rerank_hit_at_6"] == 1
    assert sample["citation_correct"] is True
    assert sample["abstention_correct"] is True
    assert sample["expected_terms_correct"] is True


def test_client_server_timing_agreement_is_bounded():
    assert runner._timings_agree(1_000, 1_100)
    assert not runner._timings_agree(1_000, 1_120)
    assert not runner._timings_agree(250, 500)


def test_retrieval_candidate_schema_is_exact_and_content_free():
    document_id = "11111111-1111-1111-1111-111111111111"
    allowed = {document_id}
    assert runner.GuardedRunner._retrieval_pairs(
        [{"document_id": document_id, "page_start": 1, "page_end": 1}],
        allowed_document_ids=allowed,
    ) == {(document_id, 1)}
    with pytest.raises(runner.RunnerError, match="shape"):
        runner.GuardedRunner._retrieval_pairs(
            [
                {
                    "document_id": document_id,
                    "page_start": 1,
                    "page_end": 1,
                    "text": "secret document content",
                }
            ],
            allowed_document_ids=allowed,
        )
    with pytest.raises(runner.RunnerError, match="outside the adopted corpus"):
        runner.GuardedRunner._retrieval_pairs(
            [
                {
                    "document_id": "22222222-2222-2222-2222-222222222222",
                    "page_start": 1,
                    "page_end": 1,
                }
            ],
            allowed_document_ids=allowed,
        )


def test_retrieval_evidence_uses_exact_ten_uploaded_ids(tmp_path, monkeypatch):
    _manifest, fixture_path = _generated(tmp_path, monkeypatch)
    transport = FakeTransport(_manifest)
    transport.permit = runner.BenchmarkPermit(
        run_id=transport.permit.run_id,
        deployment_id=transport.permit.deployment_id,
        store_id=transport.permit.store_id,
        namespace=transport.permit.namespace,
        nonce=transport.permit.nonce,
        adopted_document_ids=("ffffffff-ffff-ffff-ffff-ffffffffffff",),
    )
    guarded = runner.GuardedRunner(transport, fixture_path)
    uploaded = {
        document["id"]: str(uuid.UUID(int=index + 1))
        for index, document in enumerate(_manifest["documents"])
    }
    guarded.document_ids.update(uploaded)
    assert len(set(guarded.document_ids.values())) == 10
    with pytest.raises(runner.RunnerError, match="outside the adopted corpus"):
        guarded._retrieval_pairs(
            [
                {
                    "document_id": "ffffffff-ffff-ffff-ffff-ffffffffffff",
                    "page_start": 1,
                    "page_end": 1,
                }
            ],
            allowed_document_ids=set(guarded.document_ids.values()),
        )


@pytest.mark.parametrize(
    ("public_key", "signature"),
    [
        (b"\x01" + b"\x00" * 31, b"\x00" * 64),
        (
            (trust._Q - 1).to_bytes(32, "little"),
            (trust._B[1] | ((trust._B[0] & 1) << 255)).to_bytes(32, "little")
            + b"\x00" * 32,
        ),
        (
            bytes.fromhex(
                "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a"
            ),
            (trust._Q - 1).to_bytes(32, "little") + b"\x00" * 32,
        ),
    ],
)
def test_ed25519_rejects_identity_and_small_order_vectors(public_key, signature):
    with pytest.raises(trust.EvidenceError, match="identity|subgroup"):
        trust.verify_ed25519(public_key, b"message", signature)


def test_ed25519_rejects_noncanonical_point_vector():
    with pytest.raises(trust.EvidenceError, match="non-canonical"):
        trust.verify_ed25519(
            trust._Q.to_bytes(32, "little"),
            b"message",
            b"\x00" * 64,
        )


def test_ed25519_rejects_negative_zero_public_key_on_arbitrary_message():
    # y=1, sign=1 is the non-canonical negative-zero encoding for x=0.
    negative_zero = (1 | (1 << 255)).to_bytes(32, "little")
    with pytest.raises(trust.EvidenceError, match="negative zero|non-canonical"):
        trust.verify_ed25519(negative_zero, b"forged arbitrary message", b"\x00" * 64)


def test_legacy_sources_allow_overlapping_chunks_on_the_same_page():
    document_id = "11111111-1111-1111-1111-111111111111"
    assert runner.GuardedRunner._source_pairs(
        [
            {
                "label": "S1",
                "chunk_id": "22222222-2222-2222-2222-222222222222",
                "document_id": document_id,
                "page_start": 2,
                "page_end": 2,
            },
            {
                "label": "S2",
                "chunk_id": "33333333-3333-3333-3333-333333333333",
                "document_id": document_id,
                "page_start": 2,
                "page_end": 3,
            },
        ]
    ) == {(document_id, 2), (document_id, 3)}


def test_fabricated_failed_execution_evidence_is_rejected(tmp_path, monkeypatch):
    manifest, fixture_path = _generated(tmp_path, monkeypatch)
    transport = FakeTransport(manifest)
    guarded = runner.GuardedRunner(transport, fixture_path)
    correlation = _correlation("failed-sample")
    profile = transport.envelope(
        "profile",
        profile={"temperature": "warm", "queue": "queue-free"},
        applied=True,
        profile_token="profile-token-001",
        active_requests=1,
        queued_requests=0,
        correlation=correlation,
    )
    profile_id = profile["payload"]["evidence_id"]
    guarded.profile_attestations[profile_id] = profile
    guarded._correlations[profile_id] = correlation
    failed = transport.envelope(
        "execution",
        sample_id="failed-sample",
        stage="generation",
        case_id="case-01",
        document_id=None,
        fixture_document_id=None,
        profile={"temperature": "warm", "queue": "queue-free"},
        repetition=1,
        profile_evidence_id=profile_id,
        success=False,
        terminal_status="failed",
        metrics={},
        retrieval_candidates=[],
        reranked_sources=[],
        workload={
            "synchronized": False,
            "active_workloads": [],
            "resource_observed": True,
        },
        correlation=correlation,
        upload=None,
    )
    with pytest.raises(runner.RunnerError, match="does not match"):
        guarded._execution_sample(
            failed,
            sample_id="failed-sample",
            stage="generation",
            case_id="case-01",
            document_id=None,
            fixture_document_id=None,
            temperature="warm",
            queue="queue-free",
            repetition=1,
            profile_evidence_id=profile_id,
        )


def test_execution_correlation_tampering_is_rejected(tmp_path, monkeypatch):
    manifest, fixture_path = _generated(tmp_path, monkeypatch)
    transport = FakeTransport(manifest)
    guarded = runner.GuardedRunner(transport, fixture_path)
    expected = _correlation("sample-001")
    profile = transport.envelope(
        "profile",
        profile={"temperature": "warm", "queue": "queue-free"},
        applied=True,
        profile_token="profile-token-001",
        active_requests=1,
        queued_requests=0,
        correlation=expected,
    )
    profile_id = profile["payload"]["evidence_id"]
    guarded.profile_attestations[profile_id] = profile
    guarded._correlations[profile_id] = expected
    tampered = dict(expected)
    tampered["execution_id"] = "execution-request-tampered"
    execution = transport.envelope(
        "execution",
        sample_id="sample-001",
        stage="api",
        case_id=None,
        document_id=None,
        fixture_document_id=None,
        profile={"temperature": "warm", "queue": "queue-free"},
        repetition=1,
        profile_evidence_id=profile_id,
        success=True,
        terminal_status="completed",
        metrics={
            "elapsed_ms": 10,
            "cpu_percent": 10,
            "ram_mb": 1000,
            "vram_mb": 1000,
            "ram_headroom_mb": 5000,
            "vram_headroom_mb": 1500,
            "queue_depth": 0,
            "concurrency": 1,
            "corpus_chunks": 1,
        },
        retrieval_candidates=[],
        reranked_sources=[],
        workload={
            "synchronized": False,
            "active_workloads": ["api"],
            "resource_observed": True,
        },
        correlation=tampered,
        upload=None,
    )
    with pytest.raises(runner.RunnerError, match="does not match"):
        guarded._execution_sample(
            execution,
            sample_id="sample-001",
            stage="api",
            case_id=None,
            document_id=None,
            fixture_document_id=None,
            temperature="warm",
            queue="queue-free",
            repetition=1,
            profile_evidence_id=profile_id,
        )


def test_server_cannot_attest_different_observed_sample(tmp_path, monkeypatch):
    manifest, fixture_path = _generated(tmp_path, monkeypatch)
    transport = FakeTransport(manifest)
    guarded = runner.GuardedRunner(transport, fixture_path)
    original = transport.request

    def wrong_observation(method, path, body=None, headers=None, *, stream=False):
        if not path.startswith("/api/benchmark/samples/"):
            return original(method, path, body, headers, stream=stream)
        return _json(
            transport.envelope(
                "sample",
                sample_id="sample-001",
                execution_evidence_id="execution-001",
                sample={"elapsed_ms": 999},
            )
        )

    monkeypatch.setattr(transport, "request", wrong_observation)
    profile_id = "profile-evidence-001"
    guarded._correlations[profile_id] = _correlation("sample-001")
    with pytest.raises(runner.RunnerError, match="server observation"):
        guarded.seal_sample(
            {
                "sample_id": "sample-001",
                "stage": "generation",
                "temperature": "warm",
                "queue": "queue-free",
                "elapsed_ms": 10,
                "success": True,
                "repetition": 1,
                "execution_evidence_id": "execution-001",
                "profile_evidence_id": profile_id,
            }
        )


def test_every_required_category_is_executed_by_runner_contract():
    categories = {item["category"] for item in load_evaluation()["cases"]}
    assert categories == {
        "supported",
        "unsupported",
        "exact-term",
        "unicode-multilingual-safety",
        "adversarial",
        "citation",
        "abstention",
    }


def test_stream_transport_uses_incremental_read1_not_blocking_fixed_read():
    source = Path(runner.__file__).read_text(encoding="utf-8")
    assert "response.read1(4096)" in source
    assert "response.read(1024)" not in source
