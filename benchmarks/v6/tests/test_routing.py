import hashlib
import json

import pytest
from benchmarks.v6.routing import (
    RoutingGateError,
    _contains_token,
    _cosine,
    _exception_chain,
    _load_manifest,
)


def test_token_check_normalizes_table_separators() -> None:
    assert _contains_token(
        "| VISUAL ROUTING TOKEN | 29 |",
        "VISUAL ROUTING TOKEN 29",
    )
    assert _contains_token(
        "<td>VISUAL ROUTING TOKEN</td><td>29</td>",
        "VISUAL ROUTING TOKEN 29",
    )


def test_cosine_orders_matching_vectors() -> None:
    assert _cosine([1.0, 0.0], [1.0, 0.0]) == 1.0
    assert _cosine([1.0, 0.0], [0.0, 1.0]) == 0.0


def test_exception_chain_retains_root_cause() -> None:
    root = ValueError("root")
    outer = RuntimeError("outer")
    outer.__cause__ = root

    assert _exception_chain(outer) == "RuntimeError: outer <- ValueError: root"


def test_manifest_rejects_live_fixture_digest_mismatch(tmp_path) -> None:
    payload = b"original fixture"
    document = {
        "id": "hybrid-visual-table",
        "filename": "hybrid-visual-table.pdf",
        "sha256": hashlib.sha256(payload).hexdigest(),
    }
    documents = [document]
    manifest = {
        "dataset_id": "routing-test",
        "corpus_identity": hashlib.sha256(
            json.dumps(
                documents,
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest(),
        "documents": documents,
    }
    (tmp_path / document["filename"]).write_bytes(b"modified fixture")
    (tmp_path / "fixture-manifest.json").write_text(
        json.dumps(manifest),
        encoding="utf-8",
    )

    with pytest.raises(RoutingGateError, match="fixture digest mismatch"):
        _load_manifest(tmp_path, "routing-test")


def test_manifest_rejects_corpus_identity_mismatch(tmp_path) -> None:
    payload = b"fixture"
    document = {
        "id": "hybrid-visual-table",
        "filename": "hybrid-visual-table.pdf",
        "sha256": hashlib.sha256(payload).hexdigest(),
    }
    (tmp_path / document["filename"]).write_bytes(payload)
    (tmp_path / "fixture-manifest.json").write_text(
        json.dumps(
            {
                "dataset_id": "routing-test",
                "corpus_identity": "0" * 64,
                "documents": [document],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(RoutingGateError, match="corpus identity mismatch"):
        _load_manifest(tmp_path, "routing-test")
