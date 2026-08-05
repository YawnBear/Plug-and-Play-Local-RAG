from app.services.answer_protocol import is_explicit_insufficient_context


def test_exact_or_terminal_sentinel_is_explicit_abstention() -> None:
    assert is_explicit_insufficient_context("INSUFFICIENT_CONTEXT")
    assert is_explicit_insufficient_context(
        "The supplied sources do not establish that value.\n\n"
        "INSUFFICIENT_CONTEXT"
    )


def test_embedded_quoted_or_partial_sentinel_is_not_abstention() -> None:
    assert not is_explicit_insufficient_context("")
    assert not is_explicit_insufficient_context(
        'The source literally says "INSUFFICIENT_CONTEXT".'
    )
    assert not is_explicit_insufficient_context(
        "INSUFFICIENT_CONTEXT is not the answer."
    )
    assert not is_explicit_insufficient_context("insufficient_context")
