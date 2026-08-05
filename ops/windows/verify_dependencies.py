from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import UTC, datetime
from pathlib import Path


def no_duplicate_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise RuntimeError(f"duplicate release evidence key: {key}")
        value[key] = item
    return value


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def json_request(url: str, *, body: dict[str, object] | None = None) -> object:
    data = None if body is None else json.dumps(body).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"} if data is not None else {},
        method="POST" if data is not None else "GET",
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def anonymous_denied(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=10):
            return False
    except urllib.error.HTTPError as exc:
        return exc.code == 403


def verify_rustfs(
    *,
    endpoint: str,
    bucket: str,
    key: str,
    expected_sha256: str,
    credentials: Path,
) -> dict[str, object]:
    parsed_endpoint = urllib.parse.urlsplit(endpoint)
    try:
        endpoint_address = ipaddress.ip_address(parsed_endpoint.hostname or "")
    except ValueError as exc:
        raise RuntimeError(
            "RustFS endpoint must use a literal loopback address"
        ) from exc
    if (
        parsed_endpoint.scheme not in {"http", "https"}
        or not endpoint_address.is_loopback
        or parsed_endpoint.username is not None
        or parsed_endpoint.password is not None
        or parsed_endpoint.path not in {"", "/"}
        or parsed_endpoint.query
        or parsed_endpoint.fragment
    ):
        raise RuntimeError("RustFS endpoint must be an origin on literal loopback")
    import boto3

    access_key, secret_key = load_object_credentials(credentials)
    client = boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name="us-east-1",
    )
    head = client.head_object(Bucket=bucket, Key=key)
    body = client.get_object(Bucket=bucket, Key=key)["Body"].read()
    actual_sha256 = hashlib.sha256(body).hexdigest()
    inventory = client.list_objects_v2(Bucket=bucket, Prefix=key)
    keys = [item["Key"] for item in inventory.get("Contents", [])]
    base = endpoint.rstrip("/") + "/" + urllib.parse.quote(bucket, safe="")
    object_url = base + "/" + urllib.parse.quote(key, safe="/")
    return {
        "authenticated_head_size": int(head["ContentLength"]),
        "authenticated_object_sha256": actual_sha256,
        "authenticated_object_matches": actual_sha256 == expected_sha256,
        "inventory_exact": keys == [key] and not inventory.get("IsTruncated", False),
        "anonymous_object_get_denied": anonymous_denied(object_url),
        "anonymous_list_denied": anonymous_denied(base + "?list-type=2"),
        "anonymous_policy_denied": anonymous_denied(base + "?policy"),
    }


def load_object_credentials(path: Path) -> tuple[str, str]:
    stat = path.stat(follow_symlinks=False)
    if (
        path.is_symlink()
        or not path.is_file()
        or bool(getattr(stat, "st_file_attributes", 0) & 0x400)
        or stat.st_size > 16 * 1024
    ):
        raise RuntimeError("RustFS credential input must be a bounded regular file")
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise RuntimeError("RustFS credential input is invalid")
        key, value = line.split("=", 1)
        if key in values:
            raise RuntimeError("RustFS credential input contains a duplicate key")
        values[key] = value
    expected = {
        "OBJECT_STORAGE_ACCESS_KEY_ID",
        "OBJECT_STORAGE_SECRET_ACCESS_KEY",
    }
    if set(values) != expected or any(not value for value in values.values()):
        raise RuntimeError("RustFS credential input fields are invalid")
    return (
        values["OBJECT_STORAGE_ACCESS_KEY_ID"],
        values["OBJECT_STORAGE_SECRET_ACCESS_KEY"],
    )


def verify_rustfs_scoped_iam(
    *,
    endpoint: str,
    bucket: str,
    api_credentials: Path,
    ingestion_credentials: Path,
    deletion_credentials: Path,
    maintenance_credentials: Path,
) -> dict[str, object]:
    import boto3
    from botocore.exceptions import ClientError

    def client(path: Path):
        access_key, secret_key = load_object_credentials(path)
        return boto3.client(
            "s3",
            endpoint_url=endpoint,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name="us-east-1",
        )

    def denied(call) -> bool:
        try:
            call()
        except ClientError as exc:
            return exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode") == 403
        return False

    api = client(api_credentials)
    ingestion = client(ingestion_credentials)
    deletion = client(deletion_credentials)
    maintenance = client(maintenance_credentials)
    prefix = f"dependency-verification/{uuid.uuid4().hex}"
    deletion_key = prefix + "/delete.bin"
    api_key = prefix + "/api.bin"
    ingestion_key = prefix + "/ingestion.bin"
    body = b"scoped RustFS IAM verification"
    credential_ids = {
        load_object_credentials(path)[0]
        for path in (
            api_credentials,
            ingestion_credentials,
            deletion_credentials,
            maintenance_credentials,
        )
    }
    maintenance.put_object(Bucket=bucket, Key=deletion_key, Body=body)
    maintenance.put_object(Bucket=bucket, Key=ingestion_key, Body=body)
    deletion_completed = False
    try:
        api_get = api.get_object(Bucket=bucket, Key=deletion_key)["Body"].read() == body
        api_list = deletion_key in [
            item["Key"]
            for item in api.list_objects_v2(Bucket=bucket, Prefix=prefix).get(
                "Contents", []
            )
        ]
        api.put_object(Bucket=bucket, Key=api_key, Body=body)
        api_put = api.get_object(Bucket=bucket, Key=api_key)["Body"].read() == body
        api_delete_denied = denied(
            lambda: api.delete_object(Bucket=bucket, Key=api_key)
        )
        ingestion_get = (
            ingestion.get_object(Bucket=bucket, Key=deletion_key)["Body"].read() == body
        )
        ingestion_head = ingestion.head_object(Bucket=bucket, Key=ingestion_key)[
            "ContentLength"
        ] == len(body)
        ingestion_put_denied = denied(
            lambda: ingestion.put_object(
                Bucket=bucket, Key=prefix + "/ingestion-denied.bin", Body=b"x"
            )
        )
        ingestion_list_denied = denied(
            lambda: ingestion.list_objects_v2(Bucket=bucket, Prefix=prefix)
        )
        ingestion_delete_denied = denied(
            lambda: ingestion.delete_object(Bucket=bucket, Key=ingestion_key)
        )
        deletion_get_denied = denied(
            lambda: deletion.get_object(Bucket=bucket, Key=deletion_key)
        )
        deletion_put_denied = denied(
            lambda: deletion.put_object(
                Bucket=bucket, Key=prefix + "/denied.bin", Body=b"x"
            )
        )
        deletion_list_denied = denied(
            lambda: deletion.list_objects_v2(Bucket=bucket, Prefix=prefix)
        )
        deletion.delete_object(Bucket=bucket, Key=deletion_key)
        deletion_completed = True
        maintenance_keys = [
            item["Key"]
            for item in maintenance.list_objects_v2(Bucket=bucket, Prefix=prefix).get(
                "Contents", []
            )
        ]
        maintenance_list = (
            deletion_key not in maintenance_keys
            and api_key in maintenance_keys
            and ingestion_key in maintenance_keys
        )
    finally:
        for key in (deletion_key, api_key, ingestion_key):
            maintenance.delete_object(Bucket=bucket, Key=key)
    return {
        "credentials_distinct": len(credential_ids) == 4,
        "api_get": api_get,
        "api_list": api_list,
        "api_put": api_put,
        "api_delete_denied": api_delete_denied,
        "ingestion_get": ingestion_get,
        "ingestion_head": ingestion_head,
        "ingestion_put_denied": ingestion_put_denied,
        "ingestion_list_denied": ingestion_list_denied,
        "ingestion_delete_denied": ingestion_delete_denied,
        "deletion_get_denied": deletion_get_denied,
        "deletion_put_denied": deletion_put_denied,
        "deletion_list_denied": deletion_list_denied,
        "deletion_delete": deletion_completed,
        "maintenance_put_list_delete": maintenance_list,
        "root_credentials_used": False,
    }


def verify_ollama(models: dict[str, str]) -> dict[str, object]:
    tags = json_request("http://127.0.0.1:11434/api/tags")
    if not isinstance(tags, dict) or not isinstance(tags.get("models"), list):
        raise RuntimeError("Ollama model inventory response is invalid")
    actual = {
        item["name"]: item["digest"]
        for item in tags["models"]
        if isinstance(item, dict)
        and isinstance(item.get("name"), str)
        and isinstance(item.get("digest"), str)
        and item["name"] in models
    }
    embed = json_request(
        "http://127.0.0.1:11434/api/embed",
        body={
            "model": "qwen3-embedding:0.6b",
            "input": ["signed release dependency verification"],
        },
    )
    embeddings = embed.get("embeddings") if isinstance(embed, dict) else None
    dimension = (
        len(embeddings[0])
        if isinstance(embeddings, list)
        and len(embeddings) == 1
        and isinstance(embeddings[0], list)
        else 0
    )
    return {
        "models": actual,
        "models_match": actual == models,
        "embedding_dimension": dimension,
    }


def verify_reranker(model_root: Path, expected_assets_sha256: str) -> dict[str, object]:
    import torch
    from transformers import AutoModelForSequenceClassification, AutoTokenizer

    identity = "BAAI/bge-reranker-v2-m3"
    resolved_root = model_root.resolve(strict=True)
    if (
        not resolved_root.is_dir()
        or is_reparse(resolved_root)
        or tree_sha256(resolved_root) != expected_assets_sha256
    ):
        raise RuntimeError("reranker model assets do not match the signed release")
    tokenizer = AutoTokenizer.from_pretrained(str(resolved_root), local_files_only=True)
    model = AutoModelForSequenceClassification.from_pretrained(
        str(resolved_root),
        local_files_only=True,
    ).to("cpu")
    inputs = tokenizer(
        [
            ["What is the capital of France?", "Paris is the capital of France."],
            ["What is the capital of France?", "Bananas are yellow."],
        ],
        padding=True,
        truncation=True,
        return_tensors="pt",
    )
    with torch.no_grad():
        scores = model(**inputs).logits.view(-1).tolist()
    return {
        "identity": identity,
        "device": "cpu",
        "model_assets_sha256": expected_assets_sha256,
        "smoke_completed": len(scores) == 2 and scores[0] > scores[1],
    }


def tree_sha256(
    root: Path,
    *,
    excluded_suffixes: frozenset[str] = frozenset(),
) -> str:
    digest = hashlib.sha256()
    entries = list(root.rglob("*"))
    if any(is_reparse(path) for path in entries):
        raise RuntimeError("verified trees must not contain symlinks")
    files = sorted(
        (
            path
            for path in entries
            if path.is_file() and path.suffix.lower() not in excluded_suffixes
        ),
        key=lambda path: path.relative_to(root).as_posix().encode("utf-8"),
    )
    if not files:
        raise RuntimeError("OCR smoke produced no files")
    for path in files:
        relative = path.relative_to(root).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        digest.update(bytes.fromhex(sha256_file(path)))
    return digest.hexdigest()


def ocr_output_sha256(root: Path) -> str:
    # PaddleOCR's DOCX exports embed ZIP timestamps and are byte-different on
    # every otherwise-identical run. Hash all deterministic OCR artifacts;
    # structured JSON/text semantics are independently canonicalized below.
    return tree_sha256(root, excluded_suffixes=frozenset({".docx"}))


def is_reparse(path: Path) -> bool:
    stat_result = path.stat(follow_symlinks=False)
    return path.is_symlink() or bool(
        getattr(stat_result, "st_file_attributes", 0) & 0x400
    )


def structured_ocr_semantics(root: Path) -> dict[str, object]:
    result_files = sorted(root.rglob("*_res.json"))
    if not result_files or any(path.is_symlink() for path in result_files):
        raise RuntimeError("OCR smoke produced no regular structured result")
    documents: list[object] = []
    for path in result_files:
        documents.append(
            json.loads(
                path.read_text(encoding="utf-8"),
                object_pairs_hook=no_duplicate_object,
            )
        )
    page_indexes: set[int] = set()
    text_fragments: list[str] = []

    def visit(value: object, key: str | None = None) -> None:
        if isinstance(value, dict):
            for child_key, child in value.items():
                if child_key in {"page_index", "page_id", "page_num"}:
                    if child is not None:
                        if not isinstance(child, int) or isinstance(child, bool):
                            raise RuntimeError(
                                "OCR structured page identifier is invalid"
                            )
                        page_indexes.add(child)
                visit(child, child_key)
        elif isinstance(value, list):
            for child in value:
                visit(child, key)
        elif (
            isinstance(value, str)
            and key in {"text", "block_content", "rec_text", "markdown"}
            and value.strip()
        ):
            text_fragments.append(value.strip())

    visit(documents)
    implicit_image_pages = False
    if not page_indexes:
        implicit_image_pages = all(
            isinstance(document, dict)
            and document.get("page_index") is None
            and isinstance(document.get("input_path"), str)
            and bool(document["input_path"].strip())
            and isinstance(document.get("width"), int)
            and not isinstance(document["width"], bool)
            and document["width"] > 0
            and isinstance(document.get("height"), int)
            and not isinstance(document["height"], bool)
            and document["height"] > 0
            for document in documents
        )
        if not implicit_image_pages:
            raise RuntimeError("OCR structured output has no page semantics")
    if not text_fragments:
        raise RuntimeError("OCR structured output has no text semantics")
    canonical = json.dumps(
        documents,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    normalized_text = "\n".join(text_fragments).encode("utf-8")
    return {
        "structured_sha256": hashlib.sha256(canonical).hexdigest(),
        "text_sha256": hashlib.sha256(normalized_text).hexdigest(),
        "page_count": len(documents) if implicit_image_pages else len(page_indexes),
    }


def verify_ocr(
    *,
    python: Path,
    fixture: Path,
    output: Path,
    temp_root: Path,
    expected_fixture_sha256: str,
    expected_paddleocr_version: str,
    expected_output_sha256: str,
    expected_structured_sha256: str,
    expected_text_sha256: str,
    expected_page_count: int,
    model_root: Path,
    expected_model_assets_sha256: str,
) -> dict[str, object]:
    if fixture.is_symlink() or not fixture.is_file():
        raise RuntimeError("OCR fixture must be a regular non-symlink file")
    if sha256_file(fixture) != expected_fixture_sha256:
        raise RuntimeError("OCR fixture does not match the signed release")
    if (
        model_root.is_symlink()
        or not model_root.is_dir()
        or tree_sha256(model_root) != expected_model_assets_sha256
    ):
        raise RuntimeError("OCR local model assets do not match the signed release")
    resolved_temp = temp_root.resolve(strict=True)
    resolved_output = output.resolve(strict=False)
    if (
        output.exists()
        or resolved_output == resolved_temp
        or not resolved_output.is_relative_to(resolved_temp)
    ):
        raise RuntimeError("OCR output must be a new child of the temp root")
    probe = subprocess.run(
        [
            str(python),
            "-I",
            "-B",
            "-c",
            (
                "import json,paddle,paddleocr;"
                "print(json.dumps({'paddleocr_version':paddleocr.__version__,"
                "'device':paddle.device.get_device()}))"
            ),
        ],
        capture_output=True,
        check=True,
        text=True,
        timeout=60,
    )
    runtime = json.loads(probe.stdout)
    environment = os.environ.copy()
    environment["HF_HUB_OFFLINE"] = "1"
    environment["TRANSFORMERS_OFFLINE"] = "1"
    command = [
        str(python),
        "-I",
        "-B",
        "-m",
        "paddleocr",
        "doc_parser",
        "-i",
        str(fixture),
        "--pipeline_version",
        "v1.6",
        "--device",
        "cpu",
        "--save_path",
        str(output),
    ]
    result = subprocess.run(
        command,
        capture_output=True,
        check=False,
        env=environment,
        text=True,
        timeout=1800,
    )
    if result.returncode != 0:
        raise RuntimeError("pinned OCR smoke command failed")
    output_sha256 = ocr_output_sha256(output)
    semantics = structured_ocr_semantics(output)
    semantics_match = (
        output_sha256 == expected_output_sha256
        and semantics["structured_sha256"] == expected_structured_sha256
        and semantics["text_sha256"] == expected_text_sha256
        and semantics["page_count"] == expected_page_count
    )
    return {
        "paddleocr_version": runtime.get("paddleocr_version"),
        "pipeline_version": "1.6",
        "device": runtime.get("device"),
        "fixture_sha256": expected_fixture_sha256,
        "output_sha256": output_sha256,
        **semantics,
        "model_assets_sha256": expected_model_assets_sha256,
        "captured_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "smoke_completed": (
            runtime.get("paddleocr_version") == expected_paddleocr_version
            and runtime.get("device") == "cpu"
            and semantics_match
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--release-evidence", type=Path, required=True)
    parser.add_argument("--rustfs-endpoint", required=True)
    parser.add_argument("--rustfs-api-credentials", type=Path, required=True)
    parser.add_argument("--rustfs-ingestion-credentials", type=Path, required=True)
    parser.add_argument("--rustfs-deletion-credentials", type=Path, required=True)
    parser.add_argument("--rustfs-maintenance-credentials", type=Path, required=True)
    parser.add_argument("--ocr-python", type=Path, required=True)
    parser.add_argument("--ocr-fixture", type=Path, required=True)
    parser.add_argument("--ocr-output", type=Path, required=True)
    parser.add_argument("--ocr-temp-root", type=Path, required=True)
    parser.add_argument("--ocr-model-root", type=Path, required=True)
    parser.add_argument("--reranker-model-root", type=Path, required=True)
    parser.add_argument("--machine-fingerprint", required=True, type=str)
    arguments = parser.parse_args()

    release = json.loads(
        arguments.release_evidence.read_text(encoding="utf-8"),
        object_pairs_hook=no_duplicate_object,
    )
    verifier_sha256 = sha256_file(Path(__file__).resolve())
    if verifier_sha256 != release["verifier_sha256"]:
        raise RuntimeError("dependency verifier does not match the signed release")
    if (
        sha256_file(Path(sys.executable).resolve())
        != release["runtimes"]["api_python_sha256"]
        or sha256_file(arguments.ocr_python.resolve())
        != release["runtimes"]["ocr_python_sha256"]
    ):
        raise RuntimeError("dependency verifier runtimes do not match signed pins")
    evidence = {
        "schema_version": 1,
        "captured_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "machine_fingerprint": arguments.machine_fingerprint,
        "release_manifest_sha256": sha256_file(arguments.release_evidence),
        "verifier_sha256": verifier_sha256,
        "api_python_sha256": sha256_file(Path(sys.executable).resolve()),
        "ocr_python_sha256": sha256_file(arguments.ocr_python.resolve()),
        "rustfs": verify_rustfs(
            endpoint=arguments.rustfs_endpoint,
            bucket=release["rustfs"]["bucket"],
            key=release["rustfs"]["probe_object_key"],
            expected_sha256=release["rustfs"]["probe_object_sha256"],
            credentials=arguments.rustfs_maintenance_credentials,
        ),
        "rustfs_scoped_iam": verify_rustfs_scoped_iam(
            endpoint=arguments.rustfs_endpoint,
            bucket=release["rustfs"]["bucket"],
            api_credentials=arguments.rustfs_api_credentials,
            ingestion_credentials=arguments.rustfs_ingestion_credentials,
            deletion_credentials=arguments.rustfs_deletion_credentials,
            maintenance_credentials=arguments.rustfs_maintenance_credentials,
        ),
        "ollama": verify_ollama(release["ollama_models"]),
        "reranker": verify_reranker(
            arguments.reranker_model_root,
            release["reranker"]["model_assets_sha256"],
        ),
        "ocr": verify_ocr(
            python=arguments.ocr_python,
            fixture=arguments.ocr_fixture,
            output=arguments.ocr_output,
            temp_root=arguments.ocr_temp_root,
            expected_fixture_sha256=release["ocr"]["fixture_sha256"],
            expected_paddleocr_version=release["ocr"]["paddleocr_version"],
            expected_output_sha256=release["ocr"]["expected_output_sha256"],
            expected_structured_sha256=release["ocr"]["expected_structured_sha256"],
            expected_text_sha256=release["ocr"]["expected_text_sha256"],
            expected_page_count=release["ocr"]["expected_page_count"],
            model_root=arguments.ocr_model_root,
            expected_model_assets_sha256=release["ocr"]["model_assets_sha256"],
        ),
    }
    print(json.dumps(evidence, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
