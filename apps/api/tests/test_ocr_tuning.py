from pathlib import Path

import pytest

from app.system.ocr_tuning import (
    decode_ocr_preset,
    encode_ocr_preset,
    maximum_ocr_processes,
)


def test_manual_ocr_tuning_round_trips_and_balanced_is_backward_compatible() -> None:
    assert decode_ocr_preset("balanced") == (10, 1)
    assert decode_ocr_preset(encode_ocr_preset(12, 3)) == (12, 3)


@pytest.mark.parametrize(
    ("threads", "processes"),
    [(0, 1), (257, 1), (1, 0), (1, 17)],
)
def test_manual_ocr_tuning_rejects_out_of_range_values(
    threads: int, processes: int
) -> None:
    with pytest.raises(ValueError):
        encode_ocr_preset(threads, processes)


def test_process_limit_uses_detected_cpu_and_memory() -> None:
    assert maximum_ocr_processes(
        logical_cpu_count=16, system_memory_bytes=32 * 1024**3
    ) == 2
    assert maximum_ocr_processes(
        logical_cpu_count=64, system_memory_bytes=256 * 1024**3
    ) == 16


def test_dynamic_ocr_migration_preserves_bounded_controller_contract() -> None:
    source = (
        Path(__file__).parents[1]
        / "alembic"
        / "versions"
        / "0012_dynamic_ocr_tuning.py"
    ).read_text(encoding="utf-8")
    assert "manual-t" in source
    assert "25[0-6]" in source
    assert "1[0-6]" in source
    assert (
        "CREATE OR REPLACE FUNCTION public.v9_admin_preview_runtime_configuration"
        in source
    )
    assert "0012_dynamic_ocr_tuning" in source
