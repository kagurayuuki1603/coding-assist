from typing import TypeVar


T = TypeVar("T")


def require_unique(values: list[T], label: str) -> list[T]:
    """Reject duplicate model references while preserving their input order."""
    if len(values) != len(set(values)):
        raise ValueError(f"{label} must be unique")
    return values


def validate_evidence_indices_in_range(
    indices: list[int],
    evidence_count: int,
    error_message: str = "Evidence index {index} is outside the evidence set",
) -> None:
    """Validate references that depend on the size of a complete evidence set."""
    for index in indices:
        if index < 0 or index >= evidence_count:
            raise ValueError(error_message.format(index=index))
