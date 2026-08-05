"""Benchmark PaddleOCR-VL thread settings without selecting a winner."""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from pypdf import PdfReader, PdfWriter

THREAD_MATRIX = (1, 8, 10, 12, 16)
WARM_REPETITIONS = 5


class _FileTime(ctypes.Structure):
    _fields_ = [("low", ctypes.c_uint32), ("high", ctypes.c_uint32)]


class _ProcessMemoryCounters(ctypes.Structure):
    _fields_ = [
        ("cb", ctypes.c_uint32),
        ("PageFaultCount", ctypes.c_uint32),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
    ]


def _prepare_input(fixture: Path, target: Path) -> int:
    reader = PdfReader(fixture)
    for page_number, page in enumerate(reader.pages, start=1):
        writer = PdfWriter()
        writer.add_page(page)
        with (target / f"page-{page_number:06d}.pdf").open("xb") as output:
            writer.write(output)
    return len(reader.pages)


def _child_environment(cpu_threads: int) -> dict[str, str]:
    allowed = (
        "SYSTEMROOT",
        "WINDIR",
        "SYSTEMDRIVE",
        "COMSPEC",
        "PATH",
        "PATHEXT",
        "TEMP",
        "TMP",
        "HOME",
        "USERPROFILE",
        "PYTHONUTF8",
        "PYTHONNOUSERSITE",
        "PYTHONIOENCODING",
        "PADDLE_HOME",
        "PADDLE_PDX_CACHE_HOME",
        "OCR_MODEL_ASSET_ROOT",
        "HF_HOME",
        "HF_HUB_CACHE",
        "TRANSFORMERS_CACHE",
        "XDG_CACHE_HOME",
        "HF_HUB_OFFLINE",
        "TRANSFORMERS_OFFLINE",
        "TOKENIZERS_PARALLELISM",
    )
    source = {key.casefold(): value for key, value in os.environ.items()}
    environment = {
        key: source[key.casefold()]
        for key in allowed
        if source.get(key.casefold())
    }
    environment.update(
        {
            "FLAGS_paddle_num_threads": str(cpu_threads),
            "PYTHONUTF8": "1",
            "PYTHONNOUSERSITE": "1",
            "PYTHONIOENCODING": "utf-8",
            "TOKENIZERS_PARALLELISM": "false",
        }
    )
    return environment


def _quality_fingerprint(output: Path) -> tuple[str, int]:
    records: list[dict[str, object]] = []
    result_bytes = 0
    for path in sorted(output.glob("page-*_res.json")):
        result_bytes += path.stat().st_size
        payload = json.loads(path.read_text(encoding="utf-8"))
        records.append(
            {
                "filename": path.name,
                "blocks": payload.get("parsing_res_list"),
                "width": payload.get("width"),
                "height": payload.get("height"),
            }
        )
    if not records:
        raise RuntimeError("PaddleOCR produced no result JSON")
    serialized = json.dumps(
        records,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode()
    return hashlib.sha256(serialized).hexdigest(), result_bytes


def _windows_process_metrics(
    process: subprocess.Popen[bytes],
) -> tuple[float, int]:
    handle = ctypes.c_void_p(int(process._handle))  # type: ignore[attr-defined]
    created = _FileTime()
    exited = _FileTime()
    kernel = _FileTime()
    user = _FileTime()
    get_times = ctypes.windll.kernel32.GetProcessTimes
    if not get_times(
        handle,
        ctypes.byref(created),
        ctypes.byref(exited),
        ctypes.byref(kernel),
        ctypes.byref(user),
    ):
        raise OSError("GetProcessTimes failed")
    counters = _ProcessMemoryCounters()
    counters.cb = ctypes.sizeof(counters)
    get_memory = ctypes.windll.psapi.GetProcessMemoryInfo
    if not get_memory(handle, ctypes.byref(counters), counters.cb):
        raise OSError("GetProcessMemoryInfo failed")

    def ticks(value: _FileTime) -> int:
        return (int(value.high) << 32) | int(value.low)

    cpu_seconds = (ticks(kernel) + ticks(user)) / 10_000_000
    return cpu_seconds, int(counters.PeakWorkingSetSize)


def _run_once(
    executable: Path,
    fixture: Path,
    *,
    cpu_threads: int,
    pipeline_version: str,
) -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="v6-ocr-thread-") as temporary:
        root = Path(temporary)
        input_directory = root / "input"
        output_directory = root / "output"
        input_directory.mkdir()
        page_count = _prepare_input(fixture, input_directory)
        command = [
            str(executable),
            "-m",
            "paddleocr",
            "doc_parser",
            "-i",
            str(input_directory),
            "--save_path",
            str(output_directory),
            "--pipeline_version",
            pipeline_version,
            "--device",
            "cpu",
            "--cpu_threads",
            str(cpu_threads),
        ]
        stdout_path = root / "stdout.log"
        stderr_path = root / "stderr.log"
        started = time.perf_counter()
        peak_working_set_bytes = 0
        process_cpu_seconds = 0.0
        with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
            process = subprocess.Popen(
                command,
                stdout=stdout,
                stderr=stderr,
                env=_child_environment(cpu_threads),
            )
            while True:
                try:
                    process_cpu_seconds, peak = _windows_process_metrics(process)
                    peak_working_set_bytes = max(peak_working_set_bytes, peak)
                except OSError:
                    if process.poll() is None:
                        raise
                if process.poll() is not None:
                    break
                time.sleep(0.10)
        elapsed = time.perf_counter() - started
        if process.returncode != 0:
            detail = stderr_path.read_text(
                encoding="utf-8", errors="replace"
            )[-2000:]
            raise RuntimeError(
                f"OCR threads={cpu_threads} failed with "
                f"code {process.returncode}: {detail}"
            )
        fingerprint, result_bytes = _quality_fingerprint(output_directory)
        return {
            "elapsed_seconds": round(elapsed, 6),
            "pages": page_count,
            "quality_sha256": fingerprint,
            "result_bytes": result_bytes,
            "process_cpu_seconds": round(process_cpu_seconds, 6),
            "average_host_cpu_percent": round(
                (
                    process_cpu_seconds
                    / max(elapsed, 0.000001)
                    / max(os.cpu_count() or 1, 1)
                    * 100
                ),
                3,
            ),
            "peak_working_set_bytes": peak_working_set_bytes,
        }


def run(
    *,
    executable: Path,
    fixture: Path,
    output: Path,
    pipeline_version: str,
    threads: tuple[int, ...] = THREAD_MATRIX,
) -> dict[str, Any]:
    if os.name != "nt":
        raise RuntimeError("the supported V6 OCR benchmark target is Windows")
    if not executable.is_file() or not fixture.is_file():
        raise ValueError("OCR executable and fixture must exist")
    if any(value not in THREAD_MATRIX for value in threads):
        raise ValueError(f"threads must be selected from {THREAD_MATRIX}")
    fixture_sha256 = hashlib.sha256(fixture.read_bytes()).hexdigest()
    settings: list[dict[str, object]] = []
    for cpu_threads in threads:
        trials = [
            _run_once(
                executable,
                fixture,
                cpu_threads=cpu_threads,
                pipeline_version=pipeline_version,
            )
            for _ in range(WARM_REPETITIONS + 1)
        ]
        warm = trials[1:]
        elapsed = [float(trial["elapsed_seconds"]) for trial in warm]
        fingerprints = {
            str(trial["quality_sha256"]) for trial in trials
        }
        settings.append(
            {
                "cpu_threads": cpu_threads,
                "cold": trials[0],
                "warm": warm,
                "warm_elapsed_median_seconds": round(
                    statistics.median(elapsed), 6
                ),
                "warm_elapsed_range_seconds": [
                    round(min(elapsed), 6),
                    round(max(elapsed), 6),
                ],
                "quality_stable": len(fingerprints) == 1,
            }
        )
    report = {
        "schema_version": 1,
        "status": "measured-not-promoted",
        "fixture": {
            "path": fixture.name,
            "sha256": fixture_sha256,
        },
        "pipeline_version": pipeline_version,
        "thread_matrix": list(threads),
        "cold_repetitions": 1,
        "warm_repetitions": WARM_REPETITIONS,
        "settings": settings,
        "winner": None,
        "winner_gate": (
            "Select only after isolated and mixed OCR+interactive-chat trials "
            "also record CPU/RAM and preserve output quality."
        ),
        "mixed_workload": {"status": "not_run"},
        "resource_metrics": {
            "status": "process-cpu-and-peak-working-set-collected",
            "scope": (
                "The PaddleOCR CLI process only. Retain deployment-monitor "
                "host metrics for the mixed-workload promotion gate."
            ),
        },
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--executable", required=True, type=Path)
    parser.add_argument("--fixture", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--pipeline-version", default="v1.6")
    parser.add_argument(
        "--threads",
        nargs="+",
        type=int,
        default=list(THREAD_MATRIX),
    )
    args = parser.parse_args(argv)
    run(
        executable=args.executable.resolve(),
        fixture=args.fixture.resolve(),
        output=args.output.resolve(),
        pipeline_version=args.pipeline_version,
        threads=tuple(args.threads),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
