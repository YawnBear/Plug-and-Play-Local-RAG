"""Shared final-answer protocol helpers for RAG and chat generation."""

from __future__ import annotations

INSUFFICIENT_CONTEXT_SENTINEL = "INSUFFICIENT_CONTEXT"


def is_explicit_insufficient_context(answer: str) -> bool:
    """Return true only when the final nonempty line is the exact sentinel."""

    lines = [line.strip() for line in answer.strip().splitlines() if line.strip()]
    return bool(lines) and lines[-1] == INSUFFICIENT_CONTEXT_SENTINEL
