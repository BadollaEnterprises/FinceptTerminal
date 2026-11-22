"""Cost-conscious bounds for values restored from saved agent configs."""

from __future__ import annotations

from typing import Any, Mapping, Optional, Tuple


DEFAULT_AGENT_STEPS = 10
MAX_AGENT_STEPS = 20


def bounded_agent_steps(value: Any, default: int = DEFAULT_AGENT_STEPS) -> int:
    """Return a positive tool/reasoning step count capped at the runtime limit."""
    try:
        if isinstance(value, bool):
            raise ValueError
        parsed = int(value)
    except (TypeError, ValueError, OverflowError):
        parsed = default
    if parsed <= 0:
        parsed = default
    return min(parsed, MAX_AGENT_STEPS)


def _optional_steps(config: Mapping[str, Any], key: str) -> Optional[int]:
    if key not in config or config.get(key) in (None, "", False):
        return None
    return bounded_agent_steps(config.get(key))


def bounded_reasoning_steps(config: Mapping[str, Any]) -> Tuple[Optional[int], Optional[int]]:
    """Bound an optional Agno reasoning range and keep min no greater than max."""
    minimum = _optional_steps(config, "min_steps")
    maximum = _optional_steps(config, "max_steps")
    if minimum is not None and maximum is not None:
        minimum = min(minimum, maximum)
    return minimum, maximum
