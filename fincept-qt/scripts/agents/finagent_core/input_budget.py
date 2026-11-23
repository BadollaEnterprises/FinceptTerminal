"""Shared request-side character budget for bundled BYO AI agent paths."""

from __future__ import annotations

import json
from typing import Any


DEFAULT_INPUT_MAX_CHARS = 48_000
MAX_INPUT_MAX_CHARS = 2_000_000
DEFAULT_INPUT_MAX_TOKENS = 32_000
MAX_INPUT_MAX_TOKENS = 2_000_000
DEFAULT_OUTPUT_MAX_TOKENS = 12_000
MAX_OUTPUT_MAX_TOKENS = 131_072

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


def request_budget_error(params: Any, config: Any, limit_tokens: Any) -> str | None:
    """Reject a complete serialized agent request before loading a provider SDK."""
    try:
        parsed_limit = int(limit_tokens)
    except (TypeError, ValueError, OverflowError):
        parsed_limit = DEFAULT_INPUT_MAX_TOKENS
    parsed_limit = max(0, min(parsed_limit, MAX_INPUT_MAX_TOKENS))
    if parsed_limit == 0:
        return None

    try:
        wire = json.dumps(
            {"params": params, "config": config},
            ensure_ascii=False,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    except (TypeError, ValueError, OverflowError):
        return "Agent request could not be serialized safely. No provider request was sent."

    estimated_tokens = (len(wire) + 2) // 3
    if estimated_tokens <= parsed_limit:
        return None
    return (
        f"Agent request is approximately {estimated_tokens} tokens, above the configured "
        f"{parsed_limit}-token BYO AI turn-input guardrail. No provider request was sent. "
        "Shorten the query, instructions, or attached tool context, or raise Turn Input "
        "Tokens in Settings > LLM Configuration."
    )


def apply_output_budget(config: Any, limit_tokens: Any) -> int | None:
    """Clamp an agent model block to the terminal-wide per-turn output ceiling."""
    if not isinstance(config, dict) or not isinstance(config.get("model"), dict):
        return None
    model = config["model"]
    try:
        requested = int(model.get("max_tokens", 4096))
    except (TypeError, ValueError, OverflowError):
        requested = 4096
    if requested <= 0:
        requested = 4096
    requested = min(requested, MAX_OUTPUT_MAX_TOKENS)

    try:
        turn_limit = int(limit_tokens)
    except (TypeError, ValueError, OverflowError):
        turn_limit = DEFAULT_OUTPUT_MAX_TOKENS
    turn_limit = max(0, min(turn_limit, MAX_OUTPUT_MAX_TOKENS))
    resolved = min(requested, turn_limit) if turn_limit > 0 else requested
    model["max_tokens"] = resolved
    return resolved


def apply_nested_output_budgets(value: Any, limit_tokens: Any) -> int:
    """Clamp every nested agent/team ``model`` block; return blocks changed."""
    count = 0
    if isinstance(value, dict):
        if apply_output_budget(value, limit_tokens) is not None:
            count += 1
        for key, item in value.items():
            # Agentic Mode swaps these model blocks into critic_config["model"]
            # only after the ordinary config has been prepared. Clamp the
            # source blocks now so hidden reflector/distillation calls cannot
            # regain a larger completion allowance.
            if key in {"reflector_model", "distill_model"} and isinstance(item, dict):
                if apply_output_budget({"model": item}, limit_tokens) is not None:
                    count += 1
            count += apply_nested_output_budgets(item, limit_tokens)
    elif isinstance(value, (list, tuple)):
        for item in value:
            count += apply_nested_output_budgets(item, limit_tokens)
    return count
