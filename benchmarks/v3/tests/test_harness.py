import hashlib
import json
import subprocess
from pathlib import Path

import benchmarks.v3.harness as harness
import pytest
from benchmarks.v3.harness import (
    ALLOWED_SAMPLE_FIELDS,
    EVALUATION_PATH,
    HarnessError,
    create_manifest,
    ensure_results_path,
    load_evaluation,
    percentile,
    summarize_samples,
    validate_manifest,
)
from benchmarks.v3.schema_validation import SchemaValidationError, validate_schema


def _sample(**extra):
    value = {
        "sample_id": "sample-1",
        "stage": "generation",
        "temperature": "warm",
        "queue": "queue-free",
        "elapsed_ms": 10,
        "success": True,
        "repetition": 1,
    }
    value.update(extra)
    return value


def test_evaluation_has_every_required_category_and_page_facts():
    fixture = load_evaluation()
    assert len(fixture["documents"]) == 10
    assert sum(row["kind"] == "digital" for row in fixture["documents"]) == 8
    assert sum(row["kind"] == "scanned" for row in fixture["documents"]) == 2
    assert all(len(row["page_content"]) == row["pages"] for row in fixture["documents"])
    assert {row["category"] for row in fixture["cases"]} >= {
        "supported",
        "unsupported",
        "exact-term",
        "unicode-multilingual-safety",
        "adversarial",
        "citation",
        "abstention",
    }


def test_percentile_and_summary_are_reproducible():
    assert percentile([1, 2, 3, 4], 50) == 2.5
    assert percentile([1, 2, 3, 4], 95) == 3.85
    summary = summarize_samples(
        [_sample(), _sample(sample_id="sample-2", elapsed_ms=20)]
    )
    assert summary["generation|warm|queue-free"]["elapsed_ms"]["p50"] == 15


def test_promotion_rejects_server_59s_client_64s_generation_timing():
    with pytest.raises(HarnessError, match="client stream timings"):
        harness._validate_client_final_timing(
            _sample(elapsed_ms=59_000, client_final_ms=64_000)
        )


def test_output_path_cannot_escape_owned_results():
    assert ensure_results_path("run.json").parent.name == "results"
    with pytest.raises(HarnessError):
        ensure_results_path(Path("..") / "outside.json")


def test_manifest_is_fresh_v3_draft_with_actual_source_content_hash():
    manifest = create_manifest(run_id="test-run")
    assert manifest["manifest_kind"] == "draft"
    assert manifest["source"]["revision"]
    assert manifest["source"]["content_identity"].startswith("sha256:")
    assert manifest["alembic"]["head_revision"] == ["0001_v3_baseline"]
    assert manifest["models"]["ocr"]["installed_evidence"] == "not-collected"
    assert validate_manifest(manifest) is manifest


def test_source_identity_hashes_untracked_and_modified_contents(tmp_path):
    root = tmp_path
    scope = root / "benchmarks" / "v3"
    scope.mkdir(parents=True)
    source = scope / "new.py"
    source.write_text("one", encoding="utf-8")
    first = harness._source_content_identity(root)
    source.write_text("two", encoding="utf-8")
    second = harness._source_content_identity(root)
    assert first != second


def test_source_identity_excludes_runtime_evidence_and_drafts(tmp_path):
    scope = tmp_path / "benchmarks" / "v3"
    (scope / "evidence").mkdir(parents=True)
    (scope / "drafts").mkdir()
    (scope / "runtime").mkdir()
    (scope / "source.py").write_text("stable", encoding="utf-8")
    first = harness._source_content_identity(tmp_path)
    for directory in ("evidence", "drafts", "runtime"):
        (scope / directory / "mutable.json").write_text(directory, encoding="utf-8")
    assert harness._source_content_identity(tmp_path) == first


def test_git_source_requires_annotated_tag_object(tmp_path):
    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(tmp_path),
            "config",
            "user.email",
            "benchmark@example.invalid",
        ],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(tmp_path), "config", "user.name", "Benchmark Test"],
        check=True,
    )
    scope = tmp_path / "benchmarks" / "v3"
    scope.mkdir(parents=True)
    (scope / "source.py").write_text("stable", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "commit", "-m", "baseline"],
        check=True,
        capture_output=True,
    )
    subprocess.run(["git", "-C", str(tmp_path), "tag", "lightweight"], check=True)
    with pytest.raises(HarnessError, match="annotated tag"):
        harness._git_source(tmp_path, "lightweight")
    subprocess.run(
        ["git", "-C", str(tmp_path), "tag", "-a", "annotated", "-m", "baseline"],
        check=True,
    )
    source = harness._git_source(tmp_path, "annotated")
    assert source["reference_tag"] == "annotated"
    assert source["reference_tag_object"] != source["revision"]


def test_verifier_report_reopens_output_and_rejects_tampering(tmp_path, monkeypatch):
    monkeypatch.setattr(harness, "ROOT", tmp_path)
    evidence = tmp_path / "benchmarks" / "v3" / "evidence"
    evidence.mkdir(parents=True)
    output = evidence / "generation-output.txt"
    output.write_text("verified local output", encoding="utf-8")
    output_hash = "sha256:" + hashlib.sha256(output.read_bytes()).hexdigest()
    report = evidence / "generation-report.json"
    report.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "kind": "runtime-verification",
                "verifier": "v3-isolated-runtime-verifier-v1",
                "subject": "generation",
                "command": "ollama show qwen3:8b",
                "exit_code": 0,
                "version": "qwen3:8b",
                "digest": "sha256:" + "a" * 64,
                "output": {
                    "path": "benchmarks/v3/evidence/generation-output.txt",
                    "sha256": output_hash,
                },
            }
        ),
        encoding="utf-8",
    )
    report_entry = {
        "path": "benchmarks/v3/evidence/generation-report.json",
        "sha256": "sha256:" + hashlib.sha256(report.read_bytes()).hexdigest(),
    }
    harness._verify_verifier_report(
        report_entry,
        "generation",
        subject="generation",
        expected_version="qwen3:8b",
    )
    output.write_text("tampered", encoding="utf-8")
    with pytest.raises(HarnessError, match="hash"):
        harness._verify_verifier_report(
            report_entry,
            "generation",
            subject="generation",
            expected_version="qwen3:8b",
        )


def test_verifier_report_retains_hashed_output_bytes_during_path_swap(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(harness, "ROOT", tmp_path)
    report = tmp_path / "report.json"
    output = tmp_path / "output.txt"
    original = b"verified bytes"
    output.write_bytes(original)
    output_hash = "sha256:" + hashlib.sha256(original).hexdigest()
    report.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "kind": "runtime-verification",
                "verifier": "v3-isolated-runtime-verifier-v1",
                "subject": "generation",
                "command": "ollama show qwen3:8b",
                "exit_code": 0,
                "version": "qwen3:8b",
                "digest": "sha256:" + "a" * 64,
                "output": {
                    "path": "output.txt",
                    "sha256": output_hash,
                },
            }
        ),
        encoding="utf-8",
    )
    report_entry = {
        "path": "report.json",
        "sha256": "sha256:" + hashlib.sha256(report.read_bytes()).hexdigest(),
    }
    original_open = Path.open

    def swapping_open(path, *args, **kwargs):
        handle = original_open(path, *args, **kwargs)
        if path == output:

            class Reader:
                def __enter__(self):
                    handle.__enter__()
                    return self

                def __exit__(self, *exc):
                    return handle.__exit__(*exc)

                def read(self, *read_args):
                    data = handle.read(*read_args)
                    with original_open(output, "wb") as replacement:
                        replacement.write(b"replacement")
                    return data

            return Reader()
        return handle

    monkeypatch.setattr(Path, "open", swapping_open)
    _hash, verified = harness._verify_verifier_report(
        report_entry,
        "generation",
        subject="generation",
        expected_version="qwen3:8b",
    )
    assert verified["__retained_output_bytes"] == original
    assert output.read_bytes() == b"replacement"


def test_promotion_writes_accepted_manifest_for_clean_annotated_tag(
    tmp_path, monkeypatch
):
    results = tmp_path / "benchmarks" / "v3" / "results"
    evidence = tmp_path / "benchmarks" / "v3" / "evidence"
    results.mkdir(parents=True)
    evidence.mkdir()
    monkeypatch.setattr(harness, "ROOT", tmp_path)
    monkeypatch.setattr(harness, "RESULTS_ROOT", results)
    draft = {
        "manifest_kind": "draft",
        "source": {},
        "alembic": {"current_revision": None},
        "containers": {"images": []},
        "models": {role: {} for role in harness.MODEL_IDENTIFIERS},
    }
    (results / "draft.json").write_text(json.dumps(draft), encoding="utf-8")
    retained = {
        key: {
            "path": f"benchmarks/v3/evidence/{key}.json",
            "sha256": "sha256:" + "a" * 64,
        }
        for key in ("alembic", "containers", "schema", "server_build")
    }
    retained["models"] = {
        role: {
            "path": f"benchmarks/v3/evidence/{role}.json",
            "sha256": "sha256:" + "b" * 64,
        }
        for role in harness.MODEL_IDENTIFIERS
    }
    (evidence / "retained.json").write_text(json.dumps(retained), encoding="utf-8")
    container_output = evidence / "container-output.json"
    container_output.write_text(json.dumps({"images": []}), encoding="utf-8")
    source = {
        "revision": "a" * 40,
        "dirty": False,
        "changed_file_count": 0,
        "content_identity": "sha256:" + "c" * 64,
        "reference_tag": "v3-phase1",
        "reference_tag_object": "d" * 40,
    }
    monkeypatch.setattr(harness, "validate_manifest", lambda value: value)
    monkeypatch.setattr(harness, "_git_source", lambda *_args: source)

    def report(_entry, _name, *, subject, expected_version=None):
        if subject == "containers":
            return "sha256:" + "e" * 64, {
                "output": {
                    "path": "benchmarks/v3/evidence/container-output.json",
                    "sha256": "sha256:"
                    + hashlib.sha256(container_output.read_bytes()).hexdigest(),
                }
            }
        return "sha256:" + "e" * 64, {"version": expected_version or "verified"}

    monkeypatch.setattr(harness, "_verify_verifier_report", report)
    monkeypatch.setattr(
        harness,
        "_verify_artifact",
        lambda entry, _name: (entry["sha256"], container_output),
    )
    output = harness.promote_manifest(
        "draft.json",
        "benchmarks/v3/evidence/retained.json",
        "accepted.json",
        "v3-phase1",
    )
    promoted = json.loads(output.read_text(encoding="utf-8"))
    assert promoted["manifest_kind"] == "accepted"
    assert promoted["source"]["reference_tag_object"] == "d" * 40


def test_accepted_manifest_requires_samples_clean_tag_runtime_and_models():
    manifest = create_manifest(run_id="accepted")
    manifest["manifest_kind"] = "accepted"
    with pytest.raises(HarnessError, match="non-zero samples"):
        validate_manifest(manifest)
    manifest["samples"] = [_sample()]
    manifest["summaries"] = summarize_samples(manifest["samples"])
    with pytest.raises(HarnessError, match="clean annotated-tag"):
        validate_manifest(manifest)


def test_cli_cannot_create_an_accepted_manifest_directly():
    with pytest.raises(SystemExit):
        harness.main(
            [
                "create-manifest",
                "--output",
                "unsafe.json",
                "--kind",
                "accepted",
            ]
        )


def test_compose_images_are_pinned_and_digest_tampering_is_rejected():
    manifest = create_manifest(run_id="pinned")
    images = manifest["containers"]["images"]
    assert images and all(image["digest"] for image in images)
    validate_manifest(manifest)
    tampered = json.loads(json.dumps(manifest))
    tampered["containers"]["images"][0]["digest"] = "sha256:" + "0" * 64
    with pytest.raises(HarnessError, match="digest does not match"):
        validate_manifest(tampered)


@pytest.mark.parametrize(
    "sample",
    [
        _sample(prompt="private"),
        _sample(elapsed_ms=True),
        _sample(elapsed_ms=float("inf")),
        _sample(cpu_percent=101),
        _sample(repetition=0),
        _sample(sample_id="../escape"),
    ],
)
def test_samples_reject_unknown_private_and_invalid_values(sample):
    with pytest.raises(HarnessError):
        summarize_samples([sample])


def test_manifest_rejects_canaries_and_absolute_paths():
    manifest = create_manifest(run_id="test-run")
    manifest["models"]["ocr"]["installed_evidence"] = "CANARY_SECRET_123"
    with pytest.raises(HarnessError, match="canary"):
        validate_manifest(manifest)
    manifest = create_manifest(run_id="test-run")
    manifest["dataset"]["path"] = "C:/private/evaluation.json"
    with pytest.raises(HarnessError, match="repository-relative"):
        validate_manifest(manifest)
    manifest = create_manifest(run_id="test-run")
    manifest["models"]["ocr"]["installed_evidence"] = (
        "The synthetic retention window is 45 days. "
        "Token SYN-RETENTION-45 identifies this fact."
    )
    with pytest.raises(HarnessError, match="evaluation content"):
        validate_manifest(manifest)


def test_schema_round_trip_matches_real_manifest_fields():
    manifest = create_manifest(run_id="round-trip", samples=[_sample()])
    encoded = json.dumps(manifest)
    decoded = json.loads(encoded)
    validate_manifest(decoded)

    manifest_schema = json.loads(harness.MANIFEST_SCHEMA.read_text(encoding="utf-8"))
    sample_schema = json.loads(harness.SAMPLES_SCHEMA.read_text(encoding="utf-8"))
    assert set(manifest_schema["required"]) == set(manifest)
    assert set(manifest_schema["properties"]) == set(manifest)
    assert set(sample_schema["items"]["properties"]) == ALLOWED_SAMPLE_FIELDS
    assert set(sample_schema["items"]["required"]).issubset(ALLOWED_SAMPLE_FIELDS)
    validate_schema(decoded, harness.MANIFEST_SCHEMA)

    invalid = json.loads(encoded)
    invalid["samples"][0]["unknown"] = True
    with pytest.raises(SchemaValidationError):
        validate_schema(invalid, harness.MANIFEST_SCHEMA)


def test_cli_validation_accepts_sample_wrapper(tmp_path):
    path = tmp_path / "samples.json"
    path.write_text(json.dumps({"samples": [_sample()]}), encoding="utf-8")
    assert harness.main(["validate", str(path)]) == 0


def test_custom_evaluation_outside_repository_is_rejected(tmp_path):
    custom = tmp_path / "custom-evaluation.json"
    custom.write_bytes(EVALUATION_PATH.read_bytes())
    isolated_repo = tmp_path / "repo"
    isolated_repo.mkdir()
    with pytest.raises(HarnessError, match="repository-relative"):
        create_manifest(
            repo_root=isolated_repo, evaluation_path=custom, run_id="custom"
        )


def test_windows_drive_relative_dataset_path_is_rejected():
    manifest = create_manifest(run_id="drive-relative")
    manifest["dataset"]["path"] = "C:private/evaluation.json"
    with pytest.raises(HarnessError, match="repository-relative"):
        validate_manifest(manifest)
