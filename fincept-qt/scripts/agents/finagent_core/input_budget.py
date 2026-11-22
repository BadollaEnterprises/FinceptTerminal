"""Shared request-side character budget for bundled BYO AI agent paths."""

from __future__ import annotations

from typing import Any


DEFAULT_INPUT_MAX_CHARS = 48_000
MAX_INPUT_MAX_CHARS = 2_000_000

_PROMPT_FIELDS = frozenset({
    "answer",
    "optimization_target",
    "query",
    "research_goal",
    "task",
    "task_description",
})
_INSTRUCTION_FIELDS = frozenset({"instructions", "system_instruction", "system_prompt"})


def _text_chars(value: Any) -> int:
    if isinstance(value, str):
        return len(value)
    if isinstance(value, dict):
        return sum(_text_chars(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return sum(_text_chars(item) for item in value)
    return 0


def _selected_field_chars(value: Any, field_names: frozenset[str]) -> int:
    if isinstance(value, dict):
        total = 0
        for key, item in value.items():
            if str(key).strip().lower() in field_names:
                total += _text_chars(item)
            else:
                total += _selected_field_chars(item, field_names)
        return total
    if isinstance(value, (list, tuple)):
        return sum(_selected_field_chars(item, field_names) for item in value)
    return 0


def input_budget_error(params: Any, config: Any, limit: Any) -> str | None:
    """Reject oversized prompt material before any provider SDK is loaded."""
    try:
        parsed_limit = int(limit)
    except (TypeError, ValueError, OverflowError):
        parsed_limit = DEFAULT_INPUT_MAX_CHARS
    parsed_limit = max(0, min(parsed_limit, MAX_INPUT_MAX_CHARS))
    if parsed_limit == 0:
        return None

    total = _selected_field_chars(params, _PROMPT_FIELDS)
    total += _selected_field_chars(params, _INSTRUCTION_FIELDS)
    total += _selected_field_chars(config, _INSTRUCTION_FIELDS)
    if total <= parsed_limit:
        return None
    return (
        f"Agent input is {total} characters, above the configured "
        f"{parsed_limit}-character BYO AI request guardrail. "
        "No provider request was sent. Shorten the query or instructions, or raise "
        "History Chars in Settings > LLM Configuration."
    )
