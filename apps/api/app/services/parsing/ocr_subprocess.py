import asyncio
import ctypes
import json
import math
import os
import re
import subprocess
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from app.domain import ParseMethod
from app.services.parsing.types import OcrMode, ParsedBlock, ParsedOcrBatch, ParsedPage

PAGE_STEM_PATTERN = re.compile(r"page-(?P<page>\d{6})_0_res\.json$")
HEADING_LABELS = {"doc_title", "paragraph_title", "title"}
PROCESS_POLL_SECONDS = 0.1
PROCESS_STOP_WAIT_SECONDS = 2.0
PROCESS_OUTPUT_LIMIT = 4000
MAX_OCR_BLOCKS_PER_PAGE = 256
_OCR_CHILD_ENVIRONMENT_KEYS = (
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
_VISUAL_BLOCK_LABEL_MARKERS = (
    "chart",
    "diagram",
    "figure",
    "formula",
    "image",
    "seal",
    "table",
    "caption",
)


class OcrError(RuntimeError):
    pass


class OcrTimeoutError(OcrError):
    pass


@dataclass(frozen=True, slots=True)
class OcrCommand:
    executable: Path
    input_directory: Path
    output_directory: Path
    pipeline_version: str
    device: str
    cpu_threads: int

    def argv(self) -> tuple[str, ...]:
        return (
            str(self.executable),
            "-m",
            "paddleocr",
            "doc_parser",
            "-i",
            str(self.input_directory),
            "--save_path",
            str(self.output_directory),
            "--pipeline_version",
            self.pipeline_version,
            "--device",
            self.device,
            "--cpu_threads",
            str(self.cpu_threads),
        )


@dataclass(frozen=True, slots=True)
class _ProcessOutcome:
    returncode: int
    stdout: bytes
    stderr: bytes
    timed_out: bool = False
    duration_seconds: float = 0.0
    peak_working_set_bytes: int | None = None


class OcrSubprocessAdapter:
    def __init__(
        self,
        executable: Path,
        *,
        timeout_seconds: int,
        pipeline_version: str = "v1.6",
        device: str = "cpu",
        cpu_threads: int = 10,
    ) -> None:
        self.executable = executable
        self.timeout_seconds = timeout_seconds
        self.pipeline_version = pipeline_version
        self.device = device
        self.cpu_threads = cpu_threads
        self.last_duration_seconds: float | None = None
        self.last_peak_working_set_bytes: int | None = None
        if self.cpu_threads < 1:
            raise ValueError("OCR CPU threads must be positive")

    async def parse_pages(
        self,
        input_directory: Path,
        output_directory: Path,
        expected_pages: set[int],
        *,
        mode: OcrMode = OcrMode.FULL_PAGE,
    ) -> ParsedOcrBatch:
        if not self.executable.is_file():
            raise OcrError(f"OCR Python executable is missing: {self.executable}")
        output_directory.mkdir(parents=True, exist_ok=True)
        command = OcrCommand(
            self.executable,
            input_directory,
            output_directory,
            self.pipeline_version,
            self.device,
            self.cpu_threads,
        )
        cancel_event = threading.Event()
        worker = asyncio.create_task(
            asyncio.to_thread(
                self._run_process_sync,
                command.argv(),
                cancel_event,
                build_ocr_child_environment(os.environ, self.cpu_threads),
            )
        )
        try:
            completed = await asyncio.shield(worker)
        except asyncio.CancelledError:
            cancel_event.set()
            try:
                await asyncio.wait_for(
                    asyncio.shield(worker),
                    timeout=PROCESS_STOP_WAIT_SECONDS * 2 + PROCESS_POLL_SECONDS,
                )
            except Exception:
                pass
            raise
        except OSError as exc:
            raise OcrError(
                f"unable to start OCR subprocess: {_bounded(str(exc))}"
            ) from exc
        if completed.timed_out:
            raise OcrTimeoutError(f"OCR timed out after {self.timeout_seconds} seconds")
        if completed.returncode != 0:
            detail = _decode_output(completed.stderr)
            if not detail:
                detail = _decode_output(completed.stdout)
            if not detail:
                detail = "no subprocess output"
            raise OcrError(f"OCR exited with code {completed.returncode}: {detail}")
        self.last_duration_seconds = completed.duration_seconds
        self.last_peak_working_set_bytes = completed.peak_working_set_bytes
        parsed = load_ocr_results(output_directory, expected_pages, mode=mode)
        return ParsedOcrBatch(
            parsed.pages,
            parsed.staged_bytes,
            duration_seconds=completed.duration_seconds,
            peak_working_set_bytes=completed.peak_working_set_bytes,
        )

    def _run_process_sync(
        self,
        argv: tuple[str, ...],
        cancel_event: threading.Event,
        environment: Mapping[str, str],
    ) -> _ProcessOutcome:
        process = subprocess.Popen(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=dict(environment),
        )
        started = time.monotonic()
        peak_working_set_bytes = 0
        try:
            deadline = time.monotonic() + self.timeout_seconds
            while True:
                if cancel_event.is_set():
                    stdout, stderr = _terminate_process(process)
                    return _ProcessOutcome(
                        process.returncode or -1,
                        stdout,
                        stderr,
                        duration_seconds=time.monotonic() - started,
                        peak_working_set_bytes=peak_working_set_bytes or None,
                    )
                peak_working_set_bytes = max(
                    peak_working_set_bytes,
                    _windows_peak_working_set(process),
                )
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    stdout, stderr = _terminate_process(process)
                    return _ProcessOutcome(
                        process.returncode or -1,
                        stdout,
                        stderr,
                        timed_out=True,
                        duration_seconds=time.monotonic() - started,
                        peak_working_set_bytes=peak_working_set_bytes or None,
                    )
                try:
                    stdout, stderr = process.communicate(
                        timeout=min(PROCESS_POLL_SECONDS, remaining)
                    )
                    peak_working_set_bytes = max(
                        peak_working_set_bytes,
                        _windows_peak_working_set(process),
                    )
                    return _ProcessOutcome(
                        process.returncode or 0,
                        stdout,
                        stderr,
                        duration_seconds=time.monotonic() - started,
                        peak_working_set_bytes=peak_working_set_bytes or None,
                    )
                except subprocess.TimeoutExpired:
                    continue
        except BaseException:
            _terminate_process(process)
            raise


def _windows_peak_working_set(process: subprocess.Popen[bytes]) -> int:
    if os.name != "nt" or process.poll() is not None:
        return 0

    class ProcessMemoryCounters(ctypes.Structure):
        _fields_ = [
            ("cb", ctypes.c_ulong),
            ("PageFaultCount", ctypes.c_ulong),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
        ]

    counters = ProcessMemoryCounters()
    counters.cb = ctypes.sizeof(counters)
    try:
        succeeded = ctypes.windll.psapi.GetProcessMemoryInfo(
            int(process._handle), ctypes.byref(counters), counters.cb
        )
    except (AttributeError, OSError, TypeError):
        return 0
    return int(counters.PeakWorkingSetSize) if succeeded else 0


def _terminate_process(process: subprocess.Popen[bytes]) -> tuple[bytes, bytes]:
    try:
        exited = process.poll() is not None
    except Exception:
        exited = False
    if exited:
        try:
            return process.communicate(timeout=PROCESS_STOP_WAIT_SECONDS)
        except Exception:
            return b"", b""
    try:
        process.terminate()
    except Exception:
        pass
    try:
        return process.communicate(timeout=PROCESS_STOP_WAIT_SECONDS)
    except Exception:
        try:
            still_running = process.poll() is None
        except Exception:
            still_running = True
        if still_running:
            try:
                process.kill()
            except Exception:
                pass
        try:
            return process.communicate(timeout=PROCESS_STOP_WAIT_SECONDS)
        except Exception:
            return b"", b""


def _decode_output(output: bytes) -> str:
    return _bounded(output.decode("utf-8", errors="replace").strip())


def _bounded(detail: str) -> str:
    if len(detail) <= PROCESS_OUTPUT_LIMIT:
        return detail
    suffix = "... [truncated]"
    return f"{detail[: PROCESS_OUTPUT_LIMIT - len(suffix)]}{suffix}"


def load_ocr_results(
    output_directory: Path,
    expected_pages: set[int],
    *,
    mode: OcrMode = OcrMode.FULL_PAGE,
) -> ParsedOcrBatch:
    results: dict[int, ParsedPage] = {}
    staged_bytes = 0
    for path in sorted(output_directory.glob("page-*_res.json")):
        staged_bytes += path.stat().st_size
        match = PAGE_STEM_PATTERN.fullmatch(path.name)
        if match is None:
            raise OcrError(f"unexpected OCR result filename: {path.name}")
        page_number = int(match.group("page"))
        if page_number not in expected_pages:
            raise OcrError(f"unexpected OCR page result: {path.name}")
        if page_number in results:
            raise OcrError(f"duplicate OCR page result: {page_number}")
        results[page_number] = normalize_ocr_json(path, page_number, mode=mode)
    missing = expected_pages.difference(results)
    if missing:
        raise OcrError(f"OCR did not produce pages: {sorted(missing)}")
    return ParsedOcrBatch(results, staged_bytes)


def normalize_ocr_json(
    path: Path,
    page_number: int,
    *,
    mode: OcrMode = OcrMode.FULL_PAGE,
) -> ParsedPage:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise OcrError(f"invalid OCR JSON {path.name}: {exc}") from exc
    if not isinstance(payload, dict):
        raise OcrError(f"invalid OCR JSON object: {path.name}")
    if payload.get("page_index") != 0 or payload.get("page_count") != 1:
        raise OcrError(
            f"OCR page mapping mismatch in {path.name}: expected index 0 of 1"
        )
    blocks = payload.get("parsing_res_list")
    if not isinstance(blocks, list):
        raise OcrError(f"missing parsing_res_list in {path.name}")
    if not all(isinstance(block, dict) for block in blocks):
        raise OcrError(f"invalid OCR block in {path.name}")
    width = payload.get("width")
    height = payload.get("height")
    if (
        isinstance(width, bool)
        or not isinstance(width, (int, float))
        or isinstance(height, bool)
        or not isinstance(height, (int, float))
        or not math.isfinite(width)
        or not math.isfinite(height)
        or width <= 0
        or height <= 0
    ):
        raise OcrError(f"missing OCR page geometry in {path.name}")
    ordered = sorted(blocks, key=_ocr_block_sort_key)
    content: list[str] = []
    parsed_blocks: list[ParsedBlock] = []
    for block in ordered:
        value = block.get("block_content")
        if not isinstance(value, str):
            continue
        cleaned = _clean_ocr_text(value)
        if not cleaned:
            continue
        raw_label = block.get("block_label")
        label = raw_label if isinstance(raw_label, str) else None
        if mode is OcrMode.VISUAL_SUPPLEMENT and not _is_visual_block(label):
            continue
        if label in HEADING_LABELS:
            cleaned = f"# {cleaned}"
        block_id = block.get("block_id")
        raw_order = block.get("block_order")
        order = block_id if raw_order is None else raw_order
        bbox = block.get("block_bbox")
        if (
            isinstance(block_id, bool)
            or not isinstance(block_id, int)
            or isinstance(raw_order, bool)
            or (raw_order is not None and not isinstance(raw_order, int))
            or not isinstance(bbox, list)
            or len(bbox) != 4
            or any(
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                for value in bbox
            )
        ):
            raise OcrError(f"invalid OCR block geometry in {path.name}")
        left, top, right, bottom = (float(value) for value in bbox)
        if (
            left < 0
            or top < 0
            or right <= left
            or bottom <= top
            or right > width
            or bottom > height
        ):
            raise OcrError(f"OCR block is outside page bounds in {path.name}")
        parsed_blocks.append(
            ParsedBlock(
                block_id=block_id,
                order=order,
                text=cleaned,
                region=(
                    left / float(width),
                    top / float(height),
                    (right - left) / float(width),
                    (bottom - top) / float(height),
                ),
                label=label,
            )
        )
        content.append(cleaned)
    if len(parsed_blocks) > MAX_OCR_BLOCKS_PER_PAGE or (
        mode is OcrMode.FULL_PAGE and not parsed_blocks
    ):
        raise OcrError(f"OCR page has an invalid block count in {path.name}")
    return ParsedPage(
        page_number,
        "\n\n".join(content),
        ParseMethod.OCR,
        tuple(parsed_blocks),
    )


def _ocr_block_sort_key(block: dict[str, object]) -> tuple[int, int]:
    order = block.get("block_order")
    if isinstance(order, int) and not isinstance(order, bool):
        return (0, order)
    block_id = block.get("block_id")
    fallback = (
        block_id
        if isinstance(block_id, int) and not isinstance(block_id, bool)
        else 0
    )
    return (1, fallback)


def build_ocr_child_environment(
    source: Mapping[str, str],
    cpu_threads: int,
) -> dict[str, str]:
    if cpu_threads < 1:
        raise ValueError("OCR CPU threads must be positive")
    casefolded = {key.casefold(): value for key, value in source.items()}
    environment = {
        key: casefolded[key.casefold()]
        for key in _OCR_CHILD_ENVIRONMENT_KEYS
        if key.casefold() in casefolded and casefolded[key.casefold()]
    }
    environment["FLAGS_paddle_num_threads"] = str(cpu_threads)
    environment.setdefault("PYTHONUTF8", "1")
    environment.setdefault("PYTHONNOUSERSITE", "1")
    environment.setdefault("PYTHONIOENCODING", "utf-8")
    environment.setdefault("TOKENIZERS_PARALLELISM", "false")
    return environment


def _is_visual_block(label: str | None) -> bool:
    if label is None:
        return False
    normalized = label.casefold().replace("-", "_")
    return any(marker in normalized for marker in _VISUAL_BLOCK_LABEL_MARKERS)


def _clean_ocr_text(text: str) -> str:
    lines = []
    for line in text.replace("\x00", "").splitlines():
        cleaned = " ".join(line.split())
        if cleaned:
            lines.append(cleaned)
    return "\n".join(lines)
