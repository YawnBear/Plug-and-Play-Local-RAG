import math

import pytest

from app.domain import validate_embedding


def test_embedding_validation_accepts_1024_finite_nonzero_values() -> None:
    validate_embedding([1.0, *([0.0] * 1023)])


@pytest.mark.parametrize(
    ("values", "message"),
    [
        ([1.0], "exactly 1024"),
        ([0.0] * 1024, "non-zero norm"),
        ([math.inf, *([0.0] * 1023)], "finite"),
    ],
)
def test_embedding_validation_rejects_invalid_vectors(
    values: list[float], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        validate_embedding(values)
