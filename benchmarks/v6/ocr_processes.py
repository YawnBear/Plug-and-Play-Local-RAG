"""Benchmark one versus two concurrent PaddleOCR-VL processes on Windows."""

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

from benchmarks.v6.ocr_threads import (
    _child_environment,
    _FileTime,
    _ProcessMemoryCounters,
)

PROCESS_MATRIX = ((1, 10), (2, 4), (2, 6))
WORKLOAD_PAGES = 4
WARM_REPETITIONS = 5
MINIMUM_AVAILABLE_PHYSICAL_BYTES = 2 * 1024**3


class _MemoryStatusEx(ctypes.Structure):
    _fields_ = [
        ("length", ctypes.c_uint32),
        ("memory_load_percent", ctypes.c_uint32),
        ("total_physical_bytes", ctypes.c_uint64),
        ("available_physical_bytes", ctypes.c_uint64),
        ("total_page_file_bytes", ctypes.c_uint64),
        ("available_page_file_bytes", ctypes.c_uint64),
        ("total_virtual_bytes", ctypes.c_uint64),
        ("available_virtual_bytes", ctypes.c_uint64),
        ("available_extended_virtual_bytes", ctypes.c_uint64),
    ]


def _host_memory() -> tuple[int, int, int]:
    status = _MemoryStatusEx()
    status.length = ctypes.sizeof(status)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
        raise OSError("GlobalMemoryStatusEx failed")
    return (
        int(status.total_physical_bytes),
        int(status.available_physical_bytes),
        int(status.memory_load_percent),
    )


def _process_snapshot(
    process: subprocess.Popen[bytes],
) -> tuple[float, int, int]:
    handle = ctypes.c_void_p(int(process._handle))  # type: ignore[attr-defined]
    created = _FileTime()
    exited = _FileTime()
    kernel = _FileTime()
    user = _FileTime()
    if not ctypes.windll.kernel32.GetProcessTimes(
        handle,
        ctypes.byref(created),
        ctypes.byref(exited),
        ctypes.byref(kernel),
        ctypes.byref(user),
    ):
        raise OSError("GetProcessTimes failed")
    counters = _ProcessMemoryCounters()
    counters.cb = ctypes.sizeof(counters)
    if not ctypes.windll.psapi.GetProcessMemoryInfo(
        handle, ctypes.byref(counters), counters.cb
    ):
        raise OSError("GetProcessMemoryInfo failed")

    def ticks(value: _FileTime) -> int:
        return (int(value.high) << 32) | int(value.low)

    cpu_seconds = (ticks(kernel) + ticks(user)) / 10_000_000
    return (
        cpu_seconds,
        int(counters.WorkingSetSize),
        int(counters.PeakWorkingSetSize),
    )


def _distribute_page_numbers(
    page_count: int,
    process_count: int,
) -> tuple[tuple[int, ...], ...]:
    if page_count < 1 or process_count < 1 or process_count > page_count:
        raise ValueError("invalid OCR process workload shape")
    return tuple(
        tuple(range(worker + 1, page_count + 1, process_count))
        for worker in range(process_count)
    )


def _prepare_inputs(
    fixture: Path,
    root: Path,
    *,
    page_count: int,
    process_count: int,
) -> list[Path]:
    reader = PdfReader(fixture)
    if len(reader.pages) != 1:
        raise ValueError("the process benchmark requires a one-page fixture")
    source_page = reader.pages[0]
    inputs: list[Path] = []
    for worker, page_numbers in enumerate(
        _distribute_page_numbers(page_count, process_count),
        start=1,
    ):
        input_directory = root / f"worker-{worker:02d}" / "input"
        input_directory.mkdir(parents=True)
        inputs.append(input_directory)
        for page_number in page_numbers:
            writer = PdfWriter()
            writer.add_page(source_page)
            with (input_directory / f"page-{page_number:06d}.pdf").open("xb") as output:
                writer.write(output)
    return inputs


def _quality_fingerprint(outputs: list[Path]) -> tuple[str, int, int]:
    records: list[dict[str, object]] = []
    result_bytes = 0
    for output in outputs:
        for path in output.glob("page-*_res.json"):
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
    records.sort(key=lambda value: str(value["filename"]))
    if len(records) != WORKLOAD_PAGES:
        raise RuntimeError(
            f"PaddleOCR produced {len(records)} results, expected {WORKLOAD_PAGES}"
        )
    serialized = json.dumps(
        records,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode()
    return hashlib.sha256(serialized).hexdigest(), result_bytes, len(records)


def _terminate_all(processes: list[subprocess.Popen[bytes]]) -> None:
    for process in processes:
        if process.poll() is None:
            process.terminate()
    deadline = time.monotonic() + 5
    for process in processes:
        remaining = max(deadline - time.monotonic(), 0.1)
        try:
            process.wait(timeout=remaining)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def _run_once(
    executable: Path,
    fixture: Path,
    *,
    process_count: int,
    threads_per_process: int,
    pipeline_version: str,
) -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="v6-ocr-process-") as temporary:
        root = Path(temporary)
        inputs = _prepare_inputs(
            fixture,
            root,
            page_count=WORKLOAD_PAGES,
            process_count=process_count,
        )
        outputs = [path.parent / "output" for path in inputs]
        processes: list[subprocess.Popen[bytes]] = []
        logs: list[tuple[Path, Path]] = []
        handles: list[tuple[Any, Any]] = []
        started = time.perf_counter()
        try:
            for worker, (input_directory, output_directory) in enumerate(
                zip(inputs, outputs, strict=True),
                start=1,
            ):
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
                    str(threads_per_process),
                ]
                stdout_path = root / f"worker-{worker:02d}" / "stdout.log"
                stderr_path = root / f"worker-{worker:02d}" / "stderr.log"
                stdout = stdout_path.open("wb")
                stderr = stderr_path.open("wb")
                handles.append((stdout, stderr))
                logs.append((stdout_path, stderr_path))
                processes.append(
                    subprocess.Popen(
                        command,
                        stdout=stdout,
                        stderr=stderr,
                        env=_child_environment(threads_per_process),
                    )
                )

            process_cpu_seconds = [0.0] * process_count
            individual_peaks = [0] * process_count
            peak_combined_working_set_bytes = 0
            total_physical_bytes, available, memory_load = _host_memory()
            minimum_available_physical_bytes = available
            maximum_memory_load_percent = memory_load
            while any(process.poll() is None for process in processes):
                combined_working_set = 0
                for index, process in enumerate(processes):
                    try:
                        cpu_seconds, working_set, peak = _process_snapshot(process)
                    except OSError:
                        if process.poll() is None:
                            raise
                        continue
                    process_cpu_seconds[index] = cpu_seconds
                    individual_peaks[index] = max(individual_peaks[index], peak)
                    if process.poll() is None:
                        combined_working_set += working_set
                peak_combined_working_set_bytes = max(
                    peak_combined_working_set_bytes,
                    combined_working_set,
                )
                _, available, memory_load = _host_memory()
                minimum_available_physical_bytes = min(
                    minimum_available_physical_bytes,
                    available,
                )
                maximum_memory_load_percent = max(
                    maximum_memory_load_percent,
                    memory_load,
                )
                if available < MINIMUM_AVAILABLE_PHYSICAL_BYTES:
                    _terminate_all(processes)
                    raise RuntimeError(
                        "OCR process benchmark stopped because available "
                        "physical memory fell below 2 GiB"
                    )
                time.sleep(0.10)

            for index, process in enumerate(processes):
                try:
                    cpu_seconds, _, peak = _process_snapshot(process)
                except OSError:
                    continue
                process_cpu_seconds[index] = cpu_seconds
                individual_peaks[index] = max(individual_peaks[index], peak)
        finally:
            _terminate_all(processes)
            for stdout, stderr in handles:
                stdout.close()
                stderr.close()

        elapsed = time.perf_counter() - started
        for worker, (process, (_, stderr_path)) in enumerate(
            zip(processes, logs, strict=True),
            start=1,
        ):
            if process.returncode != 0:
                detail = stderr_path.read_text(encoding="utf-8", errors="replace")[
                    -2000:
                ]
                raise RuntimeError(
                    f"OCR worker {worker} failed with code "
                    f"{process.returncode}: {detail}"
                )
        fingerprint, result_bytes, result_count = _quality_fingerprint(outputs)
        total_cpu_seconds = sum(process_cpu_seconds)
        return {
            "elapsed_seconds": round(elapsed, 6),
            "pages": WORKLOAD_PAGES,
            "process_count": process_count,
            "threads_per_process": threads_per_process,
            "total_thread_ceiling": process_count * threads_per_process,
            "quality_sha256": fingerprint,
            "result_count": result_count,
            "result_bytes": result_bytes,
            "process_cpu_seconds": round(total_cpu_seconds, 6),
            "average_host_cpu_percent": round(
                (
                    total_cpu_seconds
                    / max(elapsed, 0.000001)
                    / max(os.cpu_count() or 1, 1)
                    * 100
                ),
                3,
            ),
            "individual_peak_working_set_bytes": individual_peaks,
            "peak_combined_working_set_bytes": (peak_combined_working_set_bytes),
            "total_physical_bytes": total_physical_bytes,
            "minimum_available_physical_bytes": (minimum_available_physical_bytes),
            "maximum_memory_load_percent": maximum_memory_load_percent,
        }


def run(
    *,
    executable: Path,
    fixture: Path,
    output: Path,
    pipeline_version: str,
) -> dict[str, Any]:
    if os.name != "nt":
        raise RuntimeError("the supported V6 OCR benchmark target is Windows")
    if not executable.is_file() or not fixture.is_file():
        raise ValueError("OCR executable and fixture must exist")
    fixture_sha256 = hashlib.sha256(fixture.read_bytes()).hexdigest()
    settings: list[dict[str, object]] = []
    for process_count, threads_per_process in PROCESS_MATRIX:
        trials: list[dict[str, object]] = []
        for repetition in range(WARM_REPETITIONS + 1):
            print(
                json.dumps(
                    {
                        "event": "trial_started",
                        "processes": process_count,
                        "threads_per_process": threads_per_process,
                        "repetition": repetition,
                        "temperature": ("cold" if repetition == 0 else "cached_start"),
                    }
                ),
                flush=True,
            )
            trial = _run_once(
                executable,
                fixture,
                process_count=process_count,
                threads_per_process=threads_per_process,
                pipeline_version=pipeline_version,
            )
            trials.append(trial)
            print(
                json.dumps(
                    {
                        "event": "trial_completed",
                        "processes": process_count,
                        "threads_per_process": threads_per_process,
                        "repetition": repetition,
                        "elapsed_seconds": trial["elapsed_seconds"],
                        "average_host_cpu_percent": (trial["average_host_cpu_percent"]),
                        "minimum_available_physical_bytes": (
                            trial["minimum_available_physical_bytes"]
                        ),
                    }
                ),
                flush=True,
            )
        warm = trials[1:]
        elapsed = [float(trial["elapsed_seconds"]) for trial in warm]
        fingerprints = {str(trial["quality_sha256"]) for trial in trials}
        settings.append(
            {
                "process_count": process_count,
                "threads_per_process": threads_per_process,
                "cold": trials[0],
                "warm": warm,
                "warm_elapsed_median_seconds": round(statistics.median(elapsed), 6),
                "warm_elapsed_range_seconds": [
                    round(min(elapsed), 6),
                    round(max(elapsed), 6),
                ],
                "quality_stable": len(fingerprints) == 1,
            }
        )
    baseline = float(settings[0]["warm_elapsed_median_seconds"])
    for setting in settings:
        setting["throughput_speedup_vs_1x10"] = round(
            baseline / float(setting["warm_elapsed_median_seconds"]),
            6,
        )
    all_fingerprints = {
        str(trial["quality_sha256"])
        for setting in settings
        for trial in [setting["cold"], *setting["warm"]]  # type: ignore[list-item]
    }
    report = {
        "schema_version": 1,
        "status": "measured-not-promoted",
        "fixture": {
            "path": fixture.name,
            "sha256": fixture_sha256,
            "repeated_pages": WORKLOAD_PAGES,
        },
        "pipeline_version": pipeline_version,
        "process_matrix": [
            {
                "process_count": process_count,
                "threads_per_process": threads,
            }
            for process_count, threads in PROCESS_MATRIX
        ],
        "cold_repetitions": 1,
        "warm_repetitions": WARM_REPETITIONS,
        "settings": settings,
        "quality_identical_across_matrix": len(all_fingerprints) == 1,
        "winner": None,
        "winner_gate": (
            "Select only after memory safety, stable quality, and a mixed "
            "OCR plus interactive-chat trial pass."
        ),
        "mixed_workload": {"status": "not_run"},
        "memory_guard": {
            "minimum_available_physical_bytes": (MINIMUM_AVAILABLE_PHYSICAL_BYTES)
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
    args = parser.parse_args(argv)
    run(
        executable=args.executable.resolve(),
        fixture=args.fixture.resolve(),
        output=args.output.resolve(),
        pipeline_version=args.pipeline_version,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
