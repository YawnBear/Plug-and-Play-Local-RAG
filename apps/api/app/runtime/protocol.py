import asyncio
import json
import math
from collections.abc import Awaitable, Callable
from typing import Annotated, Literal

from fastapi import Header, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, model_validator
from starlette.types import Message, Receive, Scope, Send

MAXIMUM_PROTOCOL_BODY_BYTES = 256 * 1024
REQUEST_ID_PATTERN = r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$"


class BoundedBodyMiddleware:
    """Buffer at most the protocol limit before request parsing begins."""

    def __init__(
        self,
        app: Callable[
            [Scope, Receive, Send],
            Awaitable[None],
        ],
        *,
        maximum_bytes: int = MAXIMUM_PROTOCOL_BODY_BYTES,
    ) -> None:
        self._app = app
        self._maximum_bytes = maximum_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        if scope.get("method") == "PUT" and str(scope.get("path", "")).endswith(
            "/input"
        ):
            await self._app(scope, receive, send)
            return
        messages: list[Message] = []
        size = 0
        more_body = True
        while more_body:
            message = await receive()
            messages.append(message)
            if message["type"] == "http.disconnect":
                break
            if message["type"] != "http.request":
                continue
            size += len(message.get("body", b""))
            if size > self._maximum_bytes:
                body = json.dumps({"detail": "request body is too large"}).encode()
                await send(
                    {
                        "type": "http.response.start",
                        "status": status.HTTP_413_CONTENT_TOO_LARGE,
                        "headers": [
                            (b"content-type", b"application/json"),
                            (b"content-length", str(len(body)).encode("ascii")),
                        ],
                    }
                )
                await send({"type": "http.response.body", "body": body})
                return
            more_body = message.get("more_body", False)
        iterator = iter(messages)

        async def replay() -> Message:
            try:
                return next(iterator)
            except StopIteration:
                await asyncio.Event().wait()
                raise RuntimeError("unreachable receive state")

        await self._app(scope, replay, send)


async def require_bounded_json(
    content_length: Annotated[int | None, Header(ge=0)] = None,
    content_type: Annotated[str | None, Header()] = None,
) -> None:
    if content_length is None:
        raise HTTPException(
            status_code=status.HTTP_411_LENGTH_REQUIRED,
            detail="Content-Length is required",
        )
    if content_length > MAXIMUM_PROTOCOL_BODY_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail="request body is too large",
        )
    if content_type is None or content_type.partition(";")[0].strip().lower() != (
        "application/json"
    ):
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="application/json is required",
        )


class CoordinatorRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    request_id: str = Field(pattern=REQUEST_ID_PATTERN, max_length=64)
    stage: Literal["generation", "rerank", "embedding"]
    priority: Literal["interactive", "background"]
    inputs: list[str] = Field(min_length=1, max_length=32)
    think: bool = True

    @model_validator(mode="after")
    def validate_inputs(self) -> "CoordinatorRequest":
        if any(not value or len(value) > 32_768 for value in self.inputs):
            raise ValueError("inputs must contain 1-32768 characters")
        if self.stage == "generation" and len(self.inputs) != 1:
            raise ValueError("generation requires exactly one input")
        if self.stage == "rerank" and len(self.inputs) < 2:
            raise ValueError("rerank requires a query and at least one passage")
        return self


class EmbedRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    request_id: str = Field(pattern=REQUEST_ID_PATTERN, max_length=64)
    priority: Literal["interactive", "background"]
    texts: list[str] = Field(min_length=1, max_length=32)

    @model_validator(mode="after")
    def validate_texts(self) -> "EmbedRequest":
        if any(not text or len(text) > 32_768 for text in self.texts):
            raise ValueError("texts must contain 1-32768 characters")
        return self


class EmbedResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    request_id: str
    embeddings: list[list[float]] = Field(min_length=1, max_length=32)

    @model_validator(mode="after")
    def validate_embeddings(self) -> "EmbedResponse":
        if any(
            len(vector) != 1024 or any(not math.isfinite(value) for value in vector)
            for vector in self.embeddings
        ):
            raise ValueError("embeddings must contain finite 1024-dimensional vectors")
        return self


class RerankRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    request_id: str = Field(pattern=REQUEST_ID_PATTERN, max_length=64)
    priority: Literal["interactive", "background"]
    query: str = Field(min_length=1, max_length=32_768)
    passages: list[str] = Field(min_length=1, max_length=32)

    @model_validator(mode="after")
    def validate_passages(self) -> "RerankRequest":
        if any(not passage or len(passage) > 32_768 for passage in self.passages):
            raise ValueError("passages must contain 1-32768 characters")
        return self


class RerankResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    request_id: str
    scores: list[float] = Field(min_length=1, max_length=32)

    @model_validator(mode="after")
    def validate_scores(self) -> "RerankResponse":
        if any(not math.isfinite(score) for score in self.scores):
            raise ValueError("reranker scores must be finite")
        return self


class GenerateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    request_id: str = Field(pattern=REQUEST_ID_PATTERN, max_length=64)
    priority: Literal["interactive", "background"]
    prompt: str = Field(min_length=1, max_length=32_768)
    think: bool = True


class OcrRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    request_id: str = Field(pattern=REQUEST_ID_PATTERN, max_length=64)
    job_id: str = Field(pattern=REQUEST_ID_PATTERN, max_length=64)
    pages: list[int] = Field(min_length=1, max_length=32)
    mode: Literal["full_page", "visual_supplement"]

    @model_validator(mode="after")
    def validate_pages(self) -> "OcrRequest":
        invalid_page = any(
            type(page) is not int or page < 1 or page > 100_000 for page in self.pages
        )
        if invalid_page:
            raise ValueError("page numbers must be integers from 1 to 100000")
        if len(set(self.pages)) != len(self.pages):
            raise ValueError("page numbers must be unique")
        return self


class OcrResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    request_id: str
    completed_pages: list[int] = Field(max_length=32)
    mode: Literal["full_page", "visual_supplement"]
    duration_seconds: float | None = Field(default=None, ge=0)
    peak_working_set_bytes: int | None = Field(default=None, ge=0)
