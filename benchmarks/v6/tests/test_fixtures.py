from pathlib import Path

from benchmarks.v6 import fixtures
from benchmarks.v6.ocr_chat_mixed import (
    CHAT_SAMPLES_PER_WARM_OCR,
    LAYOUTS,
    OCR_WARM_REPETITIONS,
    QUEUE_FREE_CHAT_SAMPLES,
    SYSTEM_RAM_HEADROOM_BYTES,
    _percentile,
    _summarize_chat,
)
from benchmarks.v6.ocr_processes import (
    PROCESS_MATRIX,
    WORKLOAD_PAGES,
    _distribute_page_numbers,
)
from benchmarks.v6.ocr_threads import (
    THREAD_MATRIX,
    WARM_REPETITIONS,
    _child_environment,
)
from pypdf import PdfReader


def test_v6_fixture_generator_covers_required_categories(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(fixtures, "GENERATED_ROOT", tmp_path)

    manifest = fixtures.generate("test-corpus")

    categories = {document["category"] for document in manifest["documents"]}
    assert categories == {
        "clean_digital",
        "image_only_scan",
        "decorative_repeated_asset",
        "mixed_text_image",
        "vector_chart_table",
        "garbled_text_layer",
        "skewed_photographed_page",
        "unresolved_visual_geometry",
        "ocr_batch_boundary",
        "embedding_batch_boundary",
    }
    root = tmp_path / "test-corpus"
    for document in manifest["documents"]:
        path = root / document["filename"]
        assert path.is_file()
        assert len(PdfReader(path).pages) == document["pages"]
        assert document["license"] == "CC0-1.0"


def test_v6_routing_fixture_is_deterministic_and_retrievable(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(fixtures, "GENERATED_ROOT", tmp_path)

    first = fixtures.generate_routing_fixture("routing")
    first_bytes = (tmp_path / "routing" / "hybrid-visual-table.pdf").read_bytes()
    second = fixtures.generate_routing_fixture("routing")

    assert first == second
    assert first_bytes == (
        tmp_path / "routing" / "hybrid-visual-table.pdf"
    ).read_bytes()
    document = first["documents"][0]
    assert document["expected_routing"] == ["hybrid"]
    assert document["required_direct_token"] == "DIRECT ROUTING TOKEN ALPHA"
    assert document["required_visual_token"] == "VISUAL ROUTING TOKEN 29"
    assert len(PdfReader(tmp_path / "routing" / document["filename"]).pages) == 1


def test_ocr_thread_matrix_has_required_trials() -> None:
    assert THREAD_MATRIX == (1, 8, 10, 12, 16)
    assert WARM_REPETITIONS == 5


def test_ocr_child_receives_paddlex_cache_and_thread_controls(
    monkeypatch,
) -> None:
    cache = r"C:\verified-paddlex-cache"
    monkeypatch.setenv("PADDLE_PDX_CACHE_HOME", cache)

    environment = _child_environment(12)

    assert environment["PADDLE_PDX_CACHE_HOME"] == cache
    assert environment["FLAGS_paddle_num_threads"] == "12"


def test_ocr_process_matrix_and_page_distribution() -> None:
    assert PROCESS_MATRIX == ((1, 10), (2, 4), (2, 6))
    assert WORKLOAD_PAGES == 4
    assert _distribute_page_numbers(4, 1) == ((1, 2, 3, 4),)
    assert _distribute_page_numbers(4, 2) == ((1, 3), (2, 4))


def test_mixed_gate_has_enough_samples_and_uses_nearest_rank() -> None:
    assert LAYOUTS == ((1, 10), (2, 4))
    assert OCR_WARM_REPETITIONS == 5
    assert CHAT_SAMPLES_PER_WARM_OCR * OCR_WARM_REPETITIONS == 20
    assert QUEUE_FREE_CHAT_SAMPLES == 20
    assert SYSTEM_RAM_HEADROOM_BYTES == 4 * 1024**3
    assert _percentile([float(value) for value in range(1, 21)], 0.95) == 19


def test_mixed_chat_summary_requires_every_quality_check() -> None:
    samples = [
        {
            "first_token_seconds": float(value),
            "total_seconds": float(value) + 0.5,
            "quality_passed": value != 20,
        }
        for value in range(1, 21)
    ]

    summary = _summarize_chat(samples)

    assert summary["sample_count"] == 20
    assert summary["first_token_p95_seconds"] == 19
    assert summary["final_p95_seconds"] == 19.5
    assert summary["quality_passed"] is False
