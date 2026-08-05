import asyncio
import json
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from app.domain import ParseMethod
from app.services.parsing.ocr_subprocess import (
    PROCESS_OUTPUT_LIMIT,
    OcrCommand,
    OcrError,
    OcrSubprocessAdapter,
    OcrTimeoutError,
    _ProcessOutcome,
    build_ocr_child_environment,
    load_ocr_results,
)


def _write_result(path: Path, *, page_index: int = 0, page_count: int = 1) -> None:
    path.write_text(
        json.dumps(
            {
                "page_index": page_index,
                "page_count": page_count,
                "width": 1000,
                "height": 1200,
                "parsing_res_list": [
                    {
                        "block_label": "text",
                        "block_content": "Body\u0000 text   with noise",
                        "block_id": 2,
                        "block_order": 2,
                        "block_bbox": [100, 250, 900, 400],
                    },
                    {
                        "block_label": "paragraph_title",
                        "block_content": "Section",
                        "block_id": 1,
                        "block_order": 1,
                        "block_bbox": [100, 100, 900, 200],
                    },
                    {
                        "block_label": "image",
                        "block_content": "",
                        "block_id": 3,
                        "block_order": None,
                    },
                ],
            }
        ),
        encoding="utf-8",
    )


def test_command_uses_strict_verified_cli_contract(tmp_path: Path) -> None:
    command = OcrCommand(
        Path(sys.executable),
        tmp_path / "in",
        tmp_path / "out",
        "v1.6",
        "cpu",
        10,
    )

    assert command.argv()[1:] == (
        "-m",
        "paddleocr",
        "doc_parser",
        "-i",
        str(tmp_path / "in"),
        "--save_path",
        str(tmp_path / "out"),
        "--pipeline_version",
        "v1.6",
        "--device",
        "cpu",
        "--cpu_threads",
        "10",
    )


def test_child_environment_sets_dynamic_threads_and_excludes_credentials() -> None:
    environment = build_ocr_child_environment(
        {
            "SYSTEMROOT": r"C:\Windows",
            "PATH": r"C:\Python",
            "HF_HOME": r"C:\models",
            "PADDLE_PDX_CACHE_HOME": r"C:\paddlex-cache",
            "OCR_SERVICE_TOKEN": "ocr-secret",
            "COORDINATOR_SERVICE_TOKEN": "coordinator-secret",
            "WORKER_DATABASE_URL": "database-secret",
            "OBJECT_STORAGE_SECRET_ACCESS_KEY": "object-secret",
            "UNRELATED_VALUE": "private",
        },
        12,
    )

    assert environment == {
        "SYSTEMROOT": r"C:\Windows",
        "PATH": r"C:\Python",
        "HF_HOME": r"C:\models",
        "PADDLE_PDX_CACHE_HOME": r"C:\paddlex-cache",
        "FLAGS_paddle_num_threads": "12",
        "PYTHONUTF8": "1",
        "PYTHONNOUSERSITE": "1",
        "PYTHONIOENCODING": "utf-8",
        "TOKENIZERS_PARALLELISM": "false",
    }


def test_adapter_rejects_nonpositive_dynamic_thread_count(tmp_path: Path) -> None:
    executable = tmp_path / "python.exe"
    executable.touch()

    with pytest.raises(ValueError, match="positive"):
        OcrSubprocessAdapter(executable, timeout_seconds=1, cpu_threads=0)


def test_results_map_unique_filenames_to_one_based_pages(tmp_path: Path) -> None:
    _write_result(tmp_path / "page-000002_0_res.json")
    _write_result(tmp_path / "page-000005_0_res.json")

    pages = load_ocr_results(tmp_path, {2, 5})

    assert list(pages) == [2, 5]
    assert pages[2].parse_method is ParseMethod.OCR
    assert pages[2].text == "# Section\n\nBody text with noise"


def test_results_use_block_id_order_for_null_paddle_block_order(
    tmp_path: Path,
) -> None:
    path = tmp_path / "page-000001_0_res.json"
    path.write_text(
        json.dumps(
            {
                "page_index": 0,
                "page_count": 1,
                "width": 1000,
                "height": 1200,
                "parsing_res_list": [
                    {
                        "block_label": "table",
                        "block_content": "| Item | Value |\n| --- | --- |\n| A | 1 |",
                        "block_id": 8,
                        "block_order": None,
                        "block_bbox": [100, 300, 900, 700],
                    },
                    {
                        "block_label": "header_image",
                        "block_content": "Synthetic header figure",
                        "block_id": 3,
                        "block_order": None,
                        "block_bbox": [100, 50, 900, 250],
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    page = load_ocr_results(tmp_path, {1})[1]

    assert [block.block_id for block in page.blocks] == [3, 8]
    assert [block.order for block in page.blocks] == [3, 8]
    assert page.text.startswith("Synthetic header figure\n\n| Item | Value |")


def test_results_use_block_id_order_when_paddle_block_order_is_missing(
    tmp_path: Path,
) -> None:
    path = tmp_path / "page-000001_0_res.json"
    _write_result(path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    del payload["parsing_res_list"][0]["block_order"]
    path.write_text(json.dumps(payload), encoding="utf-8")

    page = load_ocr_results(tmp_path, {1})[1]

    body = next(block for block in page.blocks if block.block_id == 2)
    assert body.order == 2


def test_results_preserve_explicit_order_when_it_differs_from_block_id(
    tmp_path: Path,
) -> None:
    path = tmp_path / "page-000001_0_res.json"
    path.write_text(
        json.dumps(
            {
                "page_index": 0,
                "page_count": 1,
                "width": 1000,
                "height": 1200,
                "parsing_res_list": [
                    {
                        "block_label": "text",
                        "block_content": "Second",
                        "block_id": 1,
                        "block_order": 2,
                        "block_bbox": [100, 300, 900, 500],
                    },
                    {
                        "block_label": "text",
                        "block_content": "First",
                        "block_id": 40,
                        "block_order": 1,
                        "block_bbox": [100, 100, 900, 250],
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    page = load_ocr_results(tmp_path, {1})[1]

    assert [block.block_id for block in page.blocks] == [40, 1]
    assert [block.order for block in page.blocks] == [1, 2]
    assert page.text == "First\n\nSecond"


@pytest.mark.parametrize("block_order", [True, 1.5, "1"])
def test_results_reject_noninteger_nonnull_block_order(
    tmp_path: Path, block_order: object
) -> None:
    path = tmp_path / "page-000001_0_res.json"
    path.write_text(
        json.dumps(
            {
                "page_index": 0,
                "page_count": 1,
                "width": 1000,
                "height": 1200,
                "parsing_res_list": [
                    {
                        "block_label": "table",
                        "block_content": "Synthetic table",
                        "block_id": 1,
                        "block_order": block_order,
                        "block_bbox": [100, 100, 900, 400],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(OcrError, match="invalid OCR block geometry"):
        load_ocr_results(tmp_path, {1})


def test_results_reject_mixed_integer_and_string_orders_as_ocr_error(
    tmp_path: Path,
) -> None:
    path = tmp_path / "page-000001_0_res.json"
    path.write_text(
        json.dumps(
            {
                "page_index": 0,
                "page_count": 1,
                "width": 1000,
                "height": 1200,
                "parsing_res_list": [
                    {
                        "block_label": "text",
                        "block_content": "Valid order",
                        "block_id": 1,
                        "block_order": 1,
                        "block_bbox": [100, 100, 900, 250],
                    },
                    {
                        "block_label": "table",
                        "block_content": "Invalid string order",
                        "block_id": 2,
                        "block_order": "2",
                        "block_bbox": [100, 300, 900, 600],
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(OcrError, match="invalid OCR block geometry"):
        load_ocr_results(tmp_path, {1})


def test_result_mapping_rejects_mismatch_and_missing_page(tmp_path: Path) -> None:
    _write_result(tmp_path / "page-000002_0_res.json", page_index=1)

    with pytest.raises(OcrError, match="mapping mismatch"):
        load_ocr_results(tmp_path, {2})

    (tmp_path / "page-000002_0_res.json").unlink()
    with pytest.raises(OcrError, match="did not produce"):
        load_ocr_results(tmp_path, {2})


@pytest.mark.parametrize(
    "filename",
    ["page-000002_res.json", "page-000002_1_res.json", "page-000002_00_res.json"],
)
def test_result_mapping_rejects_nonzero_or_missing_pdf_page_suffix(
    tmp_path: Path, filename: str
) -> None:
    _write_result(tmp_path / filename)

    with pytest.raises(OcrError, match="unexpected OCR result filename"):
        load_ocr_results(tmp_path, {2})


@pytest.mark.parametrize("returncode", [2, 7])
def test_adapter_reports_nonzero_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, returncode: int
) -> None:
    executable = tmp_path / "python.exe"
    executable.touch()

    adapter = OcrSubprocessAdapter(executable, timeout_seconds=1)
    monkeypatch.setattr(
        adapter,
        "_run_process_sync",
        lambda _argv, _cancel_event, _environment: _ProcessOutcome(
            returncode, b"", b"failure detail"
        ),
    )

    with pytest.raises(OcrError, match=f"code {returncode}.*failure detail"):
        asyncio.run(adapter.parse_pages(tmp_path, tmp_path / "out", {1}))


def test_adapter_reports_timed_out_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable = tmp_path / "python.exe"
    executable.touch()

    adapter = OcrSubprocessAdapter(executable, timeout_seconds=0)
    monkeypatch.setattr(
        adapter,
        "_run_process_sync",
        lambda _argv, _cancel_event, _environment: _ProcessOutcome(
            -15, b"", b"", timed_out=True
        ),
    )

    with pytest.raises(OcrTimeoutError, match="timed out"):
        asyncio.run(adapter.parse_pages(tmp_path, tmp_path / "out", {1}))


def test_adapter_bounds_subprocess_error_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable = tmp_path / "python.exe"
    executable.touch()
    adapter = OcrSubprocessAdapter(executable, timeout_seconds=1)
    monkeypatch.setattr(
        adapter,
        "_run_process_sync",
        lambda _argv, _cancel_event, _environment: _ProcessOutcome(
            2, b"", b"x" * 10000
        ),
    )

    with pytest.raises(OcrError) as error:
        asyncio.run(adapter.parse_pages(tmp_path, tmp_path / "out", {1}))

    assert "[truncated]" in str(error.value)
    assert len(str(error.value)) <= PROCESS_OUTPUT_LIMIT + 30


@pytest.mark.parametrize("stop_on_terminate", [True, False])
def test_adapter_cancellation_terminates_then_kills_when_required(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stop_on_terminate: bool,
) -> None:
    executable = tmp_path / "python.exe"
    executable.touch()
    started = threading.Event()

    class _Process:
        returncode: int | None = None
        terminated = False
        killed = False

        def poll(self) -> int | None:
            return self.returncode

        def communicate(self, timeout: float | None = None) -> tuple[bytes, bytes]:
            started.set()
            if self.killed:
                self.returncode = -9
                return b"", b"killed"
            if self.terminated and stop_on_terminate:
                self.returncode = -15
                return b"", b"terminated"
            raise subprocess.TimeoutExpired("ocr", timeout)

        def terminate(self) -> None:
            self.terminated = True

        def kill(self) -> None:
            self.killed = True

    process = _Process()
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: process)
    adapter = OcrSubprocessAdapter(executable, timeout_seconds=30)

    async def exercise() -> None:
        task = asyncio.create_task(adapter.parse_pages(tmp_path, tmp_path / "out", {1}))
        for _ in range(100):
            if started.is_set():
                break
            await asyncio.sleep(0.01)
        assert started.is_set()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(exercise())

    assert process.terminated is True
    assert process.killed is (not stop_on_terminate)


def test_unexpected_post_popen_exception_still_terminates_and_kills(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable = tmp_path / "python.exe"
    executable.touch()

    class _Process:
        returncode: int | None = None
        terminated = False
        killed = False

        def poll(self) -> int | None:
            return self.returncode

        def communicate(self, timeout: float | None = None) -> tuple[bytes, bytes]:
            if self.killed:
                self.returncode = -9
                return b"", b"killed"
            raise RuntimeError("unexpected pipe failure")

        def terminate(self) -> None:
            self.terminated = True

        def kill(self) -> None:
            self.killed = True

    process = _Process()
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: process)
    adapter = OcrSubprocessAdapter(executable, timeout_seconds=30)

    with pytest.raises(RuntimeError, match="unexpected pipe failure"):
        adapter._run_process_sync((str(executable),), threading.Event(), {})

    assert process.terminated is True
    assert process.killed is True


def test_cancellation_is_preserved_when_cleanup_worker_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable = tmp_path / "python.exe"
    executable.touch()
    adapter = OcrSubprocessAdapter(executable, timeout_seconds=30)

    def fail_after_cancel(
        _argv: tuple[str, ...],
        cancel_event: threading.Event,
        _environment: dict[str, str],
    ) -> _ProcessOutcome:
        cancel_event.wait(2)
        raise RuntimeError("cleanup worker failed")

    monkeypatch.setattr(adapter, "_run_process_sync", fail_after_cancel)

    async def exercise() -> None:
        task = asyncio.create_task(adapter.parse_pages(tmp_path, tmp_path / "out", {1}))
        await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(exercise())


def test_adapter_launches_real_process_from_selector_loop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package = tmp_path / "paddleocr"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "__main__.py").write_text(
        """\
import json
import os
import sys
from pathlib import Path

assert os.environ["FLAGS_paddle_num_threads"] == "10"
assert "OCR_SERVICE_TOKEN" not in os.environ
input_path = Path(sys.argv[sys.argv.index("-i") + 1])
output_path = Path(sys.argv[sys.argv.index("--save_path") + 1])
output_path.mkdir(parents=True, exist_ok=True)
for source in input_path.glob("page-*.pdf"):
    payload = {
        "page_index": 0,
        "page_count": 1,
        "width": 1000,
        "height": 1200,
        "parsing_res_list": [{
            "block_label": "text",
            "block_content": "Child process output",
            "block_id": 1,
            "block_order": 1,
            "block_bbox": [100, 100, 900, 200],
        }],
    }
    (output_path / f"{source.stem}_0_res.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )
""",
        encoding="utf-8",
    )
    input_directory = tmp_path / "input"
    input_directory.mkdir()
    (input_directory / "page-000001.pdf").touch()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OCR_SERVICE_TOKEN", "must-not-reach-child")
    adapter = OcrSubprocessAdapter(Path(sys.executable), timeout_seconds=10)

    pages = asyncio.run(
        adapter.parse_pages(input_directory, tmp_path / "output", {1}),
        loop_factory=asyncio.SelectorEventLoop,
    )

    assert pages[1].text == "Child process output"
