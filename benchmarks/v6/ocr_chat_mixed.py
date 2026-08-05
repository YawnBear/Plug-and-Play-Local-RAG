"""Measure interactive RAG latency while PaddleOCR-VL runs in the background."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import os
import statistics
import sys
import time
from pathlib import Path
from typing import Any

from app.services.ollama_embeddings import OllamaEmbeddingClient
from app.services.ollama_generation import OllamaGenerationClient
from app.services.reranker import BgeReranker
from benchmarks.v6.ocr_processes import (
    WORKLOAD_PAGES,
    _host_memory,
    _run_once,
)

LAYOUTS = ((1, 10), (2, 4))
OCR_WARM_REPETITIONS = 5
CHAT_SAMPLES_PER_WARM_OCR = 4
QUEUE_FREE_CHAT_SAMPLES = 20
OCR_OVERLAP_DELAY_SECONDS = 15.0
EXPECTED_ANSWER = "7391"
SYSTEM_RAM_HEADROOM_BYTES = 4 * 1024**3
QUEUE_FREE_FIRST_TOKEN_LIMIT_SECONDS = 10.0
CONTENDED_DEGRADATION_LIMIT = 1.25
FINAL_ANSWER_LIMIT_SECONDS = 60.0

QUERY = "What is the cobalt access code?"
PASSAGES = (
    "The cobalt access code is 7391. Use it only for the synthetic benchmark.",
    "The amber storage cabinet is inspected every Wednesday.",
    "The north loading door closes automatically at 18:00.",
    "The maintenance checklist uses revision twelve.",
    "Blue visitor badges expire at the end of the business day.",
    "The archive room humidity target is forty-five percent.",
    "Emergency lighting tests occur on the first Monday of each month.",
    "The synthetic inventory contains twenty-four empty sample containers.",
    "The east stairwell is designated as route beta.",
    "The calibration weight is stored in drawer seven.",
    "Training records are reviewed at the end of each quarter.",
    "The demonstration network uses a private, non-routable address.",
    "The mock incident ticket is closed after supervisor review.",
    "The sample generator uses deterministic document identifiers.",
    "All benchmark documents contain invented operational details.",
    "The west conference room seats ten people.",
    "The fictional safety drill begins at 09:30.",
    "Synthetic receipts are retained for thirty test days.",
    "The example shipment contains six cardboard boxes.",
    "The test handbook was approved in the spring release.",
)


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        raise ValueError("cannot calculate a percentile without samples")
    ordered = sorted(values)
    rank = max(1, math.ceil(percentile * len(ordered)))
    return ordered[rank - 1]


def _cosine(left: list[float], right: list[float]) -> float:
    numerator = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0 or right_norm == 0:
        raise ValueError("embedding vector has zero magnitude")
    return numerator / (left_norm * right_norm)


def _summarize_chat(samples: list[dict[str, Any]]) -> dict[str, Any]:
    first_token = [float(sample["first_token_seconds"]) for sample in samples]
    final = [float(sample["total_seconds"]) for sample in samples]
    return {
        "sample_count": len(samples),
        "first_token_median_seconds": round(statistics.median(first_token), 6),
        "first_token_p95_seconds": round(_percentile(first_token, 0.95), 6),
        "first_token_range_seconds": [
            round(min(first_token), 6),
            round(max(first_token), 6),
        ],
        "final_median_seconds": round(statistics.median(final), 6),
        "final_p95_seconds": round(_percentile(final, 0.95), 6),
        "final_range_seconds": [round(min(final), 6), round(max(final), 6)],
        "quality_passed": all(bool(sample["quality_passed"]) for sample in samples),
    }


class _InteractiveWorkload:
    def __init__(
        self,
        *,
        ollama_url: str,
        embedding_model: str,
        generation_model: str,
        reranker_model: Path,
    ) -> None:
        self.embedding = OllamaEmbeddingClient(ollama_url, embedding_model)
        self.generation = OllamaGenerationClient(
            ollama_url,
            generation_model,
            context_size=4096,
            output_tokens=64,
            timeout_seconds=120,
        )
        self.reranker = BgeReranker(str(reranker_model))
        self.passage_embeddings: list[list[float]] = []

    async def prepare(self) -> None:
        await self.generation.check_available()
        self.passage_embeddings = await self.embedding.embed(PASSAGES)

    async def close(self) -> None:
        await self.embedding.close()
        await self.generation.close()

    async def run_once(self) -> dict[str, Any]:
        started = time.perf_counter()

        embedding_started = time.perf_counter()
        query_embedding = (await self.embedding.embed([QUERY]))[0]
        embedding_seconds = time.perf_counter() - embedding_started

        retrieval_started = time.perf_counter()
        dense = sorted(
            (
                (_cosine(query_embedding, embedding), index)
                for index, embedding in enumerate(self.passage_embeddings)
            ),
            reverse=True,
        )[:20]
        retrieval_seconds = time.perf_counter() - retrieval_started

        rerank_started = time.perf_counter()
        dense_passages = [PASSAGES[index] for _, index in dense]
        scores = await self.reranker.score(QUERY, dense_passages)
        ranked = sorted(
            zip(scores, dense_passages, strict=True),
            key=lambda value: value[0],
            reverse=True,
        )
        rerank_seconds = time.perf_counter() - rerank_started
        retained = [passage for _, passage in ranked[:6]]
        relevant_in_top_six = any(EXPECTED_ANSWER in passage for passage in retained)

        prompt = (
            "Answer the question using only the labeled sources. "
            "Return only the factual four-digit access-code value from the "
            "source text, not a source label.\n\n"
            + "\n".join(
                f"[Source {chr(64 + index)}] {passage}"
                for index, passage in enumerate(retained, start=1)
            )
            + f"\n\nQuestion: {QUERY}\nAnswer:"
        )
        answer_parts: list[str] = []
        first_token_seconds: float | None = None
        usage: dict[str, int] | None = None
        done_reason: str | None = None
        async for chunk in self.generation.stream(prompt, think=False):
            if chunk.type == "answer":
                if first_token_seconds is None:
                    first_token_seconds = time.perf_counter() - started
                answer_parts.append(chunk.text)
            elif chunk.type == "done":
                done_reason = chunk.done_reason
                if chunk.usage is not None:
                    usage = {
                        "prompt_eval_count": chunk.usage.prompt_eval_count,
                        "eval_count": chunk.usage.eval_count,
                        "total_duration_ns": chunk.usage.total_duration_ns,
                        "load_duration_ns": chunk.usage.load_duration_ns,
                        "prompt_eval_duration_ns": (
                            chunk.usage.prompt_eval_duration_ns
                        ),
                        "eval_duration_ns": chunk.usage.eval_duration_ns,
                    }
        total_seconds = time.perf_counter() - started
        answer = "".join(answer_parts).strip()
        if first_token_seconds is None:
            raise RuntimeError("generation completed without an answer token")
        quality_passed = relevant_in_top_six and EXPECTED_ANSWER in answer
        return {
            "embedding_seconds": round(embedding_seconds, 6),
            "retrieval_seconds": round(retrieval_seconds, 6),
            "rerank_seconds": round(rerank_seconds, 6),
            "first_token_seconds": round(first_token_seconds, 6),
            "total_seconds": round(total_seconds, 6),
            "relevant_in_rerank_top_six": relevant_in_top_six,
            "quality_passed": quality_passed,
            "answer_sha256": hashlib.sha256(answer.encode()).hexdigest(),
            "done_reason": done_reason,
            "usage": usage,
        }


async def _chat_samples(
    workload: _InteractiveWorkload,
    count: int,
    *,
    label: str,
    ocr_task: asyncio.Task[dict[str, object]] | None = None,
) -> list[dict[str, Any]]:
    samples: list[dict[str, Any]] = []
    for index in range(count):
        if ocr_task is not None and ocr_task.done():
            break
        sample = await workload.run_once()
        samples.append(sample)
        print(
            json.dumps(
                {
                    "event": "chat_sample_completed",
                    "label": label,
                    "sample": index + 1,
                    "first_token_seconds": sample["first_token_seconds"],
                    "total_seconds": sample["total_seconds"],
                    "relevant_in_rerank_top_six": (
                        sample["relevant_in_rerank_top_six"]
                    ),
                    "quality_passed": sample["quality_passed"],
                }
            ),
            flush=True,
        )
    return samples


async def _run(
    *,
    executable: Path,
    fixture: Path,
    output: Path,
    reranker_model: Path,
    ollama_url: str,
    embedding_model: str,
    generation_model: str,
    pipeline_version: str,
) -> dict[str, Any]:
    if os.name != "nt":
        raise RuntimeError("the supported mixed benchmark target is Windows")
    for required in (executable, fixture, reranker_model):
        if not required.exists():
            raise ValueError(f"required benchmark input does not exist: {required}")

    import torch

    workload = _InteractiveWorkload(
        ollama_url=ollama_url,
        embedding_model=embedding_model,
        generation_model=generation_model,
        reranker_model=reranker_model,
    )
    try:
        print(json.dumps({"event": "interactive_warmup_started"}), flush=True)
        await workload.prepare()
        warmup = await workload.run_once()
        if not warmup["quality_passed"]:
            raise RuntimeError(
                "interactive warmup failed the synthetic retrieval and answer check"
            )
        print(
            json.dumps(
                {
                    "event": "interactive_warmup_completed",
                    "first_token_seconds": warmup["first_token_seconds"],
                    "total_seconds": warmup["total_seconds"],
                }
            ),
            flush=True,
        )
        queue_free = await _chat_samples(
            workload,
            QUEUE_FREE_CHAT_SAMPLES,
            label="queue_free",
        )
        queue_free_summary = _summarize_chat(queue_free)

        settings: list[dict[str, Any]] = []
        for process_count, threads_per_process in LAYOUTS:
            ocr_trials: list[dict[str, object]] = []
            warm_chat: list[dict[str, Any]] = []
            layout_error: str | None = None
            for repetition in range(OCR_WARM_REPETITIONS + 1):
                label = f"{process_count}x{threads_per_process}-r{repetition}"
                print(
                    json.dumps(
                        {
                            "event": "mixed_trial_started",
                            "label": label,
                            "temperature": (
                                "cold" if repetition == 0 else "cached_start"
                            ),
                        }
                    ),
                    flush=True,
                )
                ocr_task = asyncio.create_task(
                    asyncio.to_thread(
                        _run_once,
                        executable,
                        fixture,
                        process_count=process_count,
                        threads_per_process=threads_per_process,
                        pipeline_version=pipeline_version,
                    )
                )
                await asyncio.sleep(OCR_OVERLAP_DELAY_SECONDS)
                _, available_at_chat_start, memory_load_at_chat_start = _host_memory()
                requested_chat_count = (
                    1 if repetition == 0 else CHAT_SAMPLES_PER_WARM_OCR
                )
                chats = await _chat_samples(
                    workload,
                    requested_chat_count,
                    label=label,
                    ocr_task=ocr_task,
                )
                try:
                    ocr_trial = await ocr_task
                except Exception as exc:
                    layout_error = f"{type(exc).__name__}: {exc}"
                    print(
                        json.dumps(
                            {
                                "event": "mixed_trial_failed",
                                "label": label,
                                "error": layout_error,
                            }
                        ),
                        flush=True,
                    )
                    break
                ocr_trial["chat_samples_requested"] = requested_chat_count
                ocr_trial["chat_samples_completed"] = len(chats)
                ocr_trial["ocr_active_for_all_chat_starts"] = (
                    len(chats) == requested_chat_count
                )
                ocr_trial["available_physical_bytes_at_chat_start"] = (
                    available_at_chat_start
                )
                ocr_trial["memory_load_percent_at_chat_start"] = (
                    memory_load_at_chat_start
                )
                ocr_trials.append(ocr_trial)
                if repetition > 0:
                    warm_chat.extend(chats)
                print(
                    json.dumps(
                        {
                            "event": "mixed_trial_completed",
                            "label": label,
                            "ocr_elapsed_seconds": ocr_trial["elapsed_seconds"],
                            "chat_samples": len(chats),
                            "minimum_available_physical_bytes": (
                                ocr_trial["minimum_available_physical_bytes"]
                            ),
                        }
                    ),
                    flush=True,
                )

            complete = (
                layout_error is None
                and len(ocr_trials) == OCR_WARM_REPETITIONS + 1
                and len(warm_chat) == OCR_WARM_REPETITIONS * CHAT_SAMPLES_PER_WARM_OCR
            )
            chat_summary = _summarize_chat(warm_chat) if warm_chat else None
            warm_ocr = ocr_trials[1:]
            minimum_available = min(
                (
                    int(trial["minimum_available_physical_bytes"])
                    for trial in ocr_trials
                ),
                default=0,
            )
            ocr_fingerprints = {str(trial["quality_sha256"]) for trial in ocr_trials}
            degradation = (
                float(chat_summary["first_token_p95_seconds"])
                / float(queue_free_summary["first_token_p95_seconds"])
                if chat_summary is not None
                else None
            )
            gate = {
                "complete": complete,
                "queue_free_first_token_p95_within_10_seconds": (
                    float(queue_free_summary["first_token_p95_seconds"])
                    <= QUEUE_FREE_FIRST_TOKEN_LIMIT_SECONDS
                ),
                "contended_first_token_p95_degradation_within_25_percent": (
                    degradation is not None
                    and degradation <= CONTENDED_DEGRADATION_LIMIT
                ),
                "final_answer_p95_within_60_seconds": (
                    chat_summary is not None
                    and float(chat_summary["final_p95_seconds"])
                    <= FINAL_ANSWER_LIMIT_SECONDS
                ),
                "system_ram_headroom_at_least_4_gib": (
                    minimum_available >= SYSTEM_RAM_HEADROOM_BYTES
                ),
                "ocr_quality_stable": len(ocr_fingerprints) == 1,
                "chat_quality_passed": (
                    chat_summary is not None and bool(chat_summary["quality_passed"])
                ),
            }
            gate["passed"] = all(gate.values())
            settings.append(
                {
                    "process_count": process_count,
                    "threads_per_process": threads_per_process,
                    "error": layout_error,
                    "cold_ocr": ocr_trials[0] if ocr_trials else None,
                    "warm_ocr": warm_ocr,
                    "warm_ocr_elapsed_median_seconds": (
                        round(
                            statistics.median(
                                float(trial["elapsed_seconds"]) for trial in warm_ocr
                            ),
                            6,
                        )
                        if warm_ocr
                        else None
                    ),
                    "warm_chat": warm_chat,
                    "warm_chat_summary": chat_summary,
                    "first_token_p95_degradation_vs_queue_free": (
                        round(degradation, 6) if degradation is not None else None
                    ),
                    "minimum_available_physical_bytes": minimum_available,
                    "gate": gate,
                }
            )

        production = settings[0]
        candidate = settings[1]
        candidate_promoted = bool(candidate["gate"]["passed"])
        report = {
            "schema_version": 1,
            "status": (
                "candidate-promoted" if candidate_promoted else "candidate-rejected"
            ),
            "methodology": {
                "scope": (
                    "synthetic in-memory dense retrieval, real Ollama query "
                    "embedding, real CPU BGE reranking, real streamed Ollama "
                    "generation, and real PaddleOCR-VL"
                ),
                "production_database_touched": False,
                "prompts_or_answers_retained": False,
                "cold_ocr_repetitions": 1,
                "warm_ocr_repetitions": OCR_WARM_REPETITIONS,
                "chat_samples_per_warm_ocr": CHAT_SAMPLES_PER_WARM_OCR,
                "queue_free_chat_samples": QUEUE_FREE_CHAT_SAMPLES,
                "ocr_overlap_delay_seconds": OCR_OVERLAP_DELAY_SECONDS,
                "percentile_method": "nearest-rank",
            },
            "fixture": {
                "name": fixture.name,
                "sha256": hashlib.sha256(fixture.read_bytes()).hexdigest(),
                "repeated_pages_per_trial": WORKLOAD_PAGES,
            },
            "models": {
                "embedding": embedding_model,
                "reranker": reranker_model.name,
                "generation": generation_model,
                "paddle_pipeline": pipeline_version,
            },
            "host": {
                "logical_cpu_count": os.cpu_count(),
                "torch_intraop_threads": torch.get_num_threads(),
                "torch_interop_threads": torch.get_num_interop_threads(),
            },
            "objectives": {
                "queue_free_first_token_p95_seconds": (
                    QUEUE_FREE_FIRST_TOKEN_LIMIT_SECONDS
                ),
                "maximum_contended_first_token_p95_degradation": (
                    CONTENDED_DEGRADATION_LIMIT
                ),
                "final_answer_p95_seconds": FINAL_ANSWER_LIMIT_SECONDS,
                "minimum_system_ram_headroom_bytes": SYSTEM_RAM_HEADROOM_BYTES,
            },
            "interactive_warmup": warmup,
            "queue_free_chat": queue_free,
            "queue_free_summary": queue_free_summary,
            "settings": settings,
            "production_layout_before_test": {
                "process_count": production["process_count"],
                "threads_per_process": production["threads_per_process"],
            },
            "candidate_layout": {
                "process_count": candidate["process_count"],
                "threads_per_process": candidate["threads_per_process"],
            },
            "candidate_promoted": candidate_promoted,
            "selected_layout": (
                {
                    "process_count": candidate["process_count"],
                    "threads_per_process": candidate["threads_per_process"],
                }
                if candidate_promoted
                else {
                    "process_count": production["process_count"],
                    "threads_per_process": production["threads_per_process"],
                }
            ),
        }
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return report
    finally:
        await workload.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--executable", required=True, type=Path)
    parser.add_argument("--fixture", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--reranker-model", required=True, type=Path)
    parser.add_argument("--ollama-url", default="http://127.0.0.1:11434")
    parser.add_argument("--embedding-model", default="qwen3-embedding:0.6b")
    parser.add_argument("--generation-model", default="qwen3:8b")
    parser.add_argument("--pipeline-version", default="v1.6")
    args = parser.parse_args(argv)
    asyncio.run(
        _run(
            executable=args.executable.resolve(),
            fixture=args.fixture.resolve(),
            output=args.output.resolve(),
            reranker_model=args.reranker_model.resolve(),
            ollama_url=args.ollama_url,
            embedding_model=args.embedding_model,
            generation_model=args.generation_model,
            pipeline_version=args.pipeline_version,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
