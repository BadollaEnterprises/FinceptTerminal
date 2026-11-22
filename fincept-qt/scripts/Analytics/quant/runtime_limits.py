"""Small, dependency-free guards for user-controlled local analytics work."""

from __future__ import annotations

import math
from typing import Iterable


def bounded_positive_int(value, name: str, limit: int) -> int:
    """Return an exact positive integer no greater than ``limit``."""
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a positive integer")
    try:
        numeric = float(value)
        parsed = int(value)
    except (TypeError, ValueError, OverflowError):
        raise ValueError(f"{name} must be a positive integer") from None
    if not math.isfinite(numeric) or numeric != parsed or parsed < 1:
        raise ValueError(f"{name} must be a positive integer")
    if parsed > limit:
        raise ValueError(f"{name} exceeds local limit of {limit}")
    return parsed


def bounded_workload(name: str, factors: Iterable[int], limit: int) -> int:
    """Multiply non-negative integer factors, refusing work above ``limit`` early."""
    total = 1
    for factor in factors:
        if isinstance(factor, bool) or not isinstance(factor, int) or factor < 0:
            raise ValueError(f"{name} workload factors must be non-negative integers")
        if factor and total > limit // factor:
            raise ValueError(f"{name} workload exceeds local limit of {limit}")
        total *= factor
    return total


def open_unit_interval(value, name: str) -> float:
    """Return a finite number strictly between zero and one."""
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        raise ValueError(f"{name} must be strictly between 0 and 1") from None
    if not math.isfinite(parsed) or not 0.0 < parsed < 1.0:
        raise ValueError(f"{name} must be strictly between 0 and 1")
    return parsed


def finite_nonnegative(value, name: str) -> float:
    """Return a finite number greater than or equal to zero."""
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        raise ValueError(f"{name} must be a finite non-negative number") from None
    if not math.isfinite(parsed) or parsed < 0.0:
        raise ValueError(f"{name} must be a finite non-negative number")
    return parsed
