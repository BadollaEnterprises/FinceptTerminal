"""Cost-conscious bounds for values restored from saved agent configs."""

from __future__ import annotations

from typing import Any, Mapping, Optional, Tuple


DEFAULT_AGENT_STEPS = 10
DEFAULT_REASONING_STEPS = 4
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


def _optional_steps(
    config: Mapping[str, Any], key: str, default: int = DEFAULT_AGENT_STEPS
) -> Optional[int]:
    if key not in config or config.get(key) in (None, "", False):
        return None
    return bounded_agent_steps(config.get(key), default=default)


def bounded_reasoning_steps(config: Mapping[str, Any]) -> Tuple[Optional[int], Optional[int]]:
    """Bound an optional Agno reasoning range and keep min no greater than max."""
    minimum = _optional_steps(config, "min_steps", default=DEFAULT_REASONING_STEPS)
    maximum = _optional_steps(config, "max_steps", default=DEFAULT_REASONING_STEPS)
    if maximum is None:
        # Agno otherwise defaults to ten internal model calls. Four keeps
        # extended reasoning useful without making a bare `reasoning: true`
        # an unexpectedly expensive multiplier. An explicit minimum can raise
        # the implied maximum, still within MAX_AGENT_STEPS.
        maximum = max(DEFAULT_REASONING_STEPS, minimum or 0)
    if minimum is not None:
        minimum = min(minimum, maximum)
    return minimum, maximum
