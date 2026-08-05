import asyncio
import json
import time
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from typing import Literal

import httpx

from app.services.chunking import count_tokens


class GenerationServiceError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class GenerationUsage:
    prompt_eval_count: int
    eval_count: int
    total_duration_ns: int
    load_duration_ns: int
    prompt_eval_duration_ns: int
    eval_duration_ns: int
    estimated_prompt_tokens: int

    def __post_init__(self) -> None:
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value < 0
            for value in (
                self.prompt_eval_count,
                self.eval_count,
                self.total_duration_ns,
                self.load_duration_ns,
                self.prompt_eval_duration_ns,
                self.eval_duration_ns,
                self.estimated_prompt_tokens,
            )
        ):
            raise ValueError("generation usage counters must be nonnegative integers")


@dataclass(frozen=True, slots=True)
class GenerationChunk:
    type: Literal["thinking", "answer", "done"]
    text: str = ""
    done_reason: Literal["stop", "length"] | None = None
    usage: GenerationUsage | None = None

    def __post_init__(self) -> None:
        if self.type not in {"thinking", "answer", "done"}:
            raise ValueError("generation chunk type is invalid")
        if self.type == "done":
            if self.text or self.done_reason not in {"stop", "length"}:
                raise ValueError("generation terminal chunk is invalid")
            return
        if (
            self.done_reason is not None
            or self.usage is not None
            or not self.text
            or len(self.text) > 32_768
        ):
            raise ValueError("generation chunk text must contain 1-32768 characters")


@dataclass(frozen=True, slots=True)
class OllamaModelReadiness:
    reachable: bool
    available_models: frozenset[str]
    detail: str


class OllamaGenerationClient:
    def __init__(
        self,
        base_url: str,
        model: str,
        *,
        context_size: int = 16_384,
        output_tokens: int = 3_072,
        timeout_seconds: float = 300,
        availability_cache_seconds: float = 5.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not 0.0 <= availability_cache_seconds <= 30.0:
            raise ValueError("availability cache must be between 0 and 30 seconds")
        self.model = model
        self.context_size = context_size
        self.output_tokens = output_tokens
        self._availability_cache_seconds = availability_cache_seconds
        self._availability_verified_at: float | None = None
        self._availability_lock = asyncio.Lock()
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            base_url=base_url.rstrip("/"), timeout=timeout_seconds
        )

    async def check_available(self) -> None:
        if self._availability_verified_at is not None and (
            time.monotonic() - self._availability_verified_at
            < self._availability_cache_seconds
        ):
            return
        async with self._availability_lock:
            if self._availability_verified_at is not None and (
                time.monotonic() - self._availability_verified_at
                < self._availability_cache_seconds
            ):
                return
            try:
                response = await self._client.post(
                    "/api/show", json={"model": self.model}
                )
                response.raise_for_status()
            except httpx.ConnectError as exc:
                raise GenerationServiceError(
                    "Ollama is unavailable; start Ollama and verify its base URL"
                ) from exc
            except httpx.TimeoutException as exc:
                raise GenerationServiceError("Ollama model check timed out") from exc
            except httpx.HTTPStatusError as exc:
                raise GenerationServiceError(
                    f"required Ollama model {self.model!r} is unavailable: "
                    f"{exc.response.text.strip()}"
                ) from exc
            self._availability_verified_at = time.monotonic()

    async def readiness(self, required_models: Sequence[str]) -> OllamaModelReadiness:
        try:
            response = await self._client.get("/api/tags", timeout=5.0)
            response.raise_for_status()
            payload = response.json()
        except httpx.RequestError:
            return OllamaModelReadiness(
                False,
                frozenset(),
                "Ollama request failed; start Ollama and verify OLLAMA_BASE_URL",
            )
        except httpx.HTTPStatusError as exc:
            return OllamaModelReadiness(
                False,
                frozenset(),
                f"Ollama readiness returned HTTP {exc.response.status_code}",
            )
        except (json.JSONDecodeError, TypeError, ValueError):
            return OllamaModelReadiness(
                False,
                frozenset(),
                "Ollama returned an invalid model-list response",
            )
        models = payload.get("models") if isinstance(payload, dict) else None
        if not isinstance(models, list):
            return OllamaModelReadiness(
                False,
                frozenset(),
                "Ollama returned an invalid model-list response",
            )
        available = frozenset(
            name
            for model in models
            if isinstance(model, dict)
            for name in (model.get("name"), model.get("model"))
            if isinstance(name, str)
        )
        missing = [model for model in required_models if model not in available]
        if missing:
            pulls = ", ".join(f"ollama pull {model}" for model in missing)
            return OllamaModelReadiness(
                True,
                available,
                f"required Ollama model(s) missing; run: {pulls}",
            )
        return OllamaModelReadiness(True, available, "ready")

    async def stream(
        self, prompt: str, *, think: bool = True
    ) -> AsyncIterator[GenerationChunk]:
        terminal_frame_received = False
        try:
            async with self._client.stream(
                "POST",
                "/api/generate",
                json={
                    "model": self.model,
                    "prompt": prompt,
                    "stream": True,
                    "think": think,
                    "options": {
                        "num_ctx": self.context_size,
                        "num_predict": self.output_tokens,
                        "temperature": 0.2,
                    },
                },
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line:
                        continue
                    try:
                        payload = json.loads(line)
                    except json.JSONDecodeError as exc:
                        raise GenerationServiceError(
                            "Ollama returned malformed streaming JSON"
                        ) from exc
                    if not isinstance(payload, dict):
                        raise GenerationServiceError(
                            "Ollama returned malformed streaming JSON"
                        )
                    if payload.get("error"):
                        raise GenerationServiceError(str(payload["error"]))
                    thinking = payload.get("thinking")
                    answer = payload.get("response")
                    if thinking is not None and not isinstance(thinking, str):
                        raise GenerationServiceError(
                            "Ollama returned malformed thinking text"
                        )
                    if answer is not None and not isinstance(answer, str):
                        raise GenerationServiceError(
                            "Ollama returned malformed answer text"
                        )
                    if thinking:
                        yield GenerationChunk("thinking", thinking)
                    if answer:
                        yield GenerationChunk("answer", answer)
                    if payload.get("done") is True:
                        done_reason = payload.get("done_reason")
                        if done_reason not in {"stop", "length"}:
                            raise GenerationServiceError(
                                "Ollama returned an invalid generation stop reason"
                            )
                        usage = _usage_from_terminal_payload(
                            payload,
                            estimated_prompt_tokens=count_tokens(prompt),
                        )
                        yield GenerationChunk(
                            "done",
                            done_reason=done_reason,
                            usage=usage,
                        )
                        terminal_frame_received = True
                        break
                if not terminal_frame_received:
                    raise GenerationServiceError(
                        "Ollama generation stream ended before a terminal done frame"
                    )
        except GenerationServiceError:
            raise
        except httpx.ConnectError as exc:
            raise GenerationServiceError(
                "Ollama disconnected during generation"
            ) from exc
        except httpx.TimeoutException as exc:
            raise GenerationServiceError("Ollama generation timed out") from exc
        except httpx.HTTPStatusError as exc:
            raise GenerationServiceError(
                f"Ollama generation failed: {exc.response.text.strip()}"
            ) from exc
        except httpx.RequestError as exc:
            raise GenerationServiceError(
                "Ollama transport failed during generation"
            ) from exc

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()


def _usage_from_terminal_payload(
    payload: dict[str, object],
    *,
    estimated_prompt_tokens: int,
) -> GenerationUsage | None:
    fields = (
        "prompt_eval_count",
        "eval_count",
        "total_duration",
        "load_duration",
        "prompt_eval_duration",
        "eval_duration",
    )
    if not any(field in payload for field in fields):
        return None
    values: list[int] = []
    for field in fields:
        value = payload.get(field)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise GenerationServiceError(
                "Ollama returned invalid terminal usage counters"
            )
        values.append(value)
    return GenerationUsage(*values, estimated_prompt_tokens)
