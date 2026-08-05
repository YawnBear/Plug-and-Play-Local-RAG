from collections.abc import Sequence

import httpx

from app.domain import validate_embedding


class EmbeddingServiceError(RuntimeError):
    pass


class OllamaEmbeddingClient:
    def __init__(
        self,
        base_url: str,
        model: str,
        *,
        timeout_seconds: float = 120,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.model = model
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            base_url=base_url.rstrip("/"), timeout=timeout_seconds
        )

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        try:
            response = await self._client.post(
                "/api/embed",
                json={
                    "model": self.model,
                    "input": list(texts),
                    "truncate": False,
                },
            )
            response.raise_for_status()
        except httpx.ConnectError as exc:
            raise EmbeddingServiceError(
                "Ollama is unavailable; start Ollama and verify its base URL"
            ) from exc
        except httpx.TimeoutException as exc:
            raise EmbeddingServiceError("Ollama embedding request timed out") from exc
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text.strip()
            raise EmbeddingServiceError(
                f"Ollama rejected embedding model {self.model!r}: {detail}"
            ) from exc
        try:
            payload = response.json()
        except ValueError as exc:
            raise EmbeddingServiceError("Ollama returned invalid JSON") from exc
        embeddings = payload.get("embeddings") if isinstance(payload, dict) else None
        if not isinstance(embeddings, list) or len(embeddings) != len(texts):
            raise EmbeddingServiceError(
                "Ollama returned an unexpected number of embeddings"
            )
        validated: list[list[float]] = []
        for values in embeddings:
            if not isinstance(values, list) or any(
                isinstance(value, bool) or not isinstance(value, int | float)
                for value in values
            ):
                raise EmbeddingServiceError("Ollama returned a malformed embedding")
            vector = [float(value) for value in values]
            try:
                validate_embedding(vector)
            except ValueError as exc:
                raise EmbeddingServiceError(str(exc)) from exc
            validated.append(vector)
        return validated

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()
