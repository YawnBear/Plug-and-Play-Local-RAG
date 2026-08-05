from __future__ import annotations

import re

DEFAULT_OCR_CPU_THREADS = 10
DEFAULT_OCR_PROCESS_COUNT = 1
MAXIMUM_OCR_CPU_THREADS = 256
MAXIMUM_OCR_PROCESS_COUNT = 16

_MANUAL_PRESET = re.compile(
    r"^manual-t(?P<threads>[1-9][0-9]{0,2})-p(?P<processes>[1-9][0-9]?)$"
)


def encode_ocr_preset(cpu_threads: int, process_count: int) -> str:
    _validate(cpu_threads, process_count)
    return f"manual-t{cpu_threads}-p{process_count}"


def decode_ocr_preset(preset_id: str) -> tuple[int, int]:
    if preset_id == "balanced":
        return DEFAULT_OCR_CPU_THREADS, DEFAULT_OCR_PROCESS_COUNT
    match = _MANUAL_PRESET.fullmatch(preset_id)
    if match is None:
        raise ValueError("OCR tuning preset is invalid")
    cpu_threads = int(match.group("threads"))
    process_count = int(match.group("processes"))
    _validate(cpu_threads, process_count)
    return cpu_threads, process_count


def maximum_ocr_processes(*, logical_cpu_count: int, system_memory_bytes: int) -> int:
    reserved_bytes = 8 * 1024**3
    per_process_bytes = 10 * 1024**3
    memory_bound = max(1, (system_memory_bytes - reserved_bytes) // per_process_bytes)
    cpu_bound = max(1, logical_cpu_count // 2)
    return min(MAXIMUM_OCR_PROCESS_COUNT, memory_bound, cpu_bound)


def _validate(cpu_threads: int, process_count: int) -> None:
    if not 1 <= cpu_threads <= MAXIMUM_OCR_CPU_THREADS:
        raise ValueError("OCR CPU thread count is outside the supported range")
    if not 1 <= process_count <= MAXIMUM_OCR_PROCESS_COUNT:
        raise ValueError("OCR process count is outside the supported range")
