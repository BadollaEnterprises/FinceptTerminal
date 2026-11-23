"""
cli.py — C++ entry point for Fincept Deep Agents.

Single responsibility:
  - Receive a command + JSON params from C++ (AIQuantLabService::run_deep_agent)
  - Dispatch supported user-owned tool-calling models to agent.py
  - Return one JSON object on stdout; all logs go to stderr

Usage:
  python cli.py <command> --stdin        (params JSON on stdin — used by the terminal,
                                           because execute_task carries the LLM API key)
  python cli.py <command> '<json_params>' (manual use without credentials)

Commands:
  check_status      Check library availability and list capabilities
  list_capabilities List all agent types and subagents
  execute_task      Run a full agent task

Output schema (execute_task):
  { "success": bool, "result": str, "todos": [...], "files": {...},
    "thread_id": str, "error": str|null }
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Any

# Force all logging to stderr — stdout is reserved for JSON output to C++
logging.basicConfig(
    level=logging.WARNING,
    format="%(levelname)-8s %(name)s: %(message)s",
    stream=sys.stderr,
    force=True,
)

# Add parent dirs to path
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
if str(_HERE.parent) not in sys.path:
    sys.path.insert(0, str(_HERE.parent))

logger = logging.getLogger(__name__)

from models import create_model, extract_text, provider_policy_error, recursion_limit, supports_tool_calling
from subagents import list_agent_types, list_subagent_names, AGENT_SUBAGENTS
from finagent_core.input_budget import (
    DEFAULT_INPUT_MAX_CHARS,
    DEFAULT_INPUT_MAX_TOKENS,
    input_budget_error,
    request_budget_error,
)
from finagent_core.run_errors import explain_llm_error

# ---------------------------------------------------------------------------
# Availability check (import deepagents lazily so CLI starts fast)
# ---------------------------------------------------------------------------

def _check_deepagents() -> dict[str, Any]:
    try:
        from deepagents import create_deep_agent  # noqa: F401
        return {"available": True, "error": None}
    except ImportError as exc:
        return {"available": False, "error": str(exc)}


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------

def cmd_check_status(_params: dict[str, Any]) -> dict[str, Any]:
    info = _check_deepagents()
    return {
        "success":          True,
        "available":        info["available"],
        "error":            info["error"],
        "agent_types":      list_agent_types(),
        "subagent_names":   list_subagent_names(),
    }


def cmd_list_capabilities(_params: dict[str, Any]) -> dict[str, Any]:
    return {
        "success":     True,
        "agent_types": {k: v for k, v in AGENT_SUBAGENTS.items()},
        "subagents":   list_subagent_names(),
        "features": [
            "Hierarchical subagent spawning",
            "Built-in todo management",
            "Bounded public Yahoo market snapshots and financial news",
            "Filesystem tools (ls/read/write/edit/glob/grep)",
            "Shell execution via LocalShellBackend (opt-in)",
            "Persistent memory via AGENTS.md",
            "Skills via SKILL.md (add skills/ directory to enable)",
            "Human-in-the-loop interrupt_on",
            "AnthropicPromptCaching (auto)",
            "Summarization middleware (auto)",
            "Structured output via response_schema",
        ],
    }


def cmd_execute_task(params: dict[str, Any]) -> dict[str, Any]:
    task       = params.get("task")
    agent_type = params.get("agent_type", "general")
    thread_id  = params.get("thread_id",  "default-thread")
    config     = _parse_config(params.get("config", {}))

    if not task:
        return _error("task is required")

    context_data = params.get("context", {})

    if budget_error := input_budget_error(
        params, config, params.get("input_max_chars", DEFAULT_INPUT_MAX_CHARS)
    ):
        return _error(budget_error)
    request_params = {key: value for key, value in params.items() if key != "config"}
    if budget_error := request_budget_error(
        request_params, config, params.get("input_max_tokens", DEFAULT_INPUT_MAX_TOKENS)
    ):
        return _error(budget_error)

    policy_error = provider_policy_error(config)
    if policy_error:
        return _error(policy_error)

    if supports_tool_calling(config):
        return _execute_with_deepagents(
            task=task,
            agent_type=agent_type,
            config=config,
            thread_id=thread_id,
            context_data=context_data,
        )

    shown = str(config.get("llm_provider") or "none")
    return _error(
        f"LLM provider '{shown}' does not support the tools required by AI Quant Lab. "
        "Select a user-owned tool-calling provider such as DeepSeek."
    )


# ---------------------------------------------------------------------------
# Internal execution helpers
# ---------------------------------------------------------------------------

def _execute_with_deepagents(
    task: str,
    agent_type: str,
    config: dict[str, Any],
    thread_id: str,
    context_data: dict[str, Any],
) -> dict[str, Any]:
    """Execute via deepagents library (tool-calling LLM path)."""
    try:
        from agent import create_agent, build_fincept_context

        model    = create_model(config)
        compiled = create_agent(
            model=model,
            config={**config, "agent_type": agent_type},
        )
        ctx = build_fincept_context({"agent_type": agent_type, "context": context_data})

        run_config = {"recursion_limit": recursion_limit(config)}
        if thread_id:
            run_config["configurable"] = {"thread_id": thread_id}

        result = compiled.invoke(
            {"messages": [("user", task)]},
            config=run_config,
            context=ctx,
        )
        return _format_result(
            result,
            thread_id,
            str(config.get("llm_provider") or "").strip().lower(),
        )

    except Exception as exc:
        # Provider exceptions may echo authorization headers or API keys. Keep
        # both stderr and the UI response safe without issuing another request.
        safe_error = explain_llm_error(str(exc))
        logger.error("deepagents execution failed: %s", safe_error)
        return _error(safe_error)


def _usage_value(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError, OverflowError):
        return 0


def _message_usage(message: Any) -> tuple[int, int, int]:
    """Return input, output and cache-read tokens for one LangChain message.

    ``usage_metadata`` is the provider-neutral LangChain shape. Older adapters
    expose the same data below ``response_metadata.token_usage``; only consult
    that fallback when the canonical shape is absent so counts are never added
    twice.
    """
    usage = getattr(message, "usage_metadata", None)
    if not isinstance(usage, dict) or not usage:
        response_metadata = getattr(message, "response_metadata", None)
        if isinstance(response_metadata, dict):
            usage = response_metadata.get("token_usage") or response_metadata.get("usage") or {}
    if not isinstance(usage, dict):
        return 0, 0, 0

    prompt = _usage_value(usage.get("input_tokens", usage.get("prompt_tokens")))
    completion = _usage_value(usage.get("output_tokens", usage.get("completion_tokens")))
    details = usage.get("input_token_details") or usage.get("prompt_tokens_details") or {}
    if not isinstance(details, dict):
        details = {}
    cached = _usage_value(
        usage.get(
            "cache_read_tokens",
            details.get("cache_read", details.get("cached_tokens")),
        )
    )
    return prompt, completion, min(cached, prompt)


def _is_ai_message(message: Any) -> bool:
    """Whether one transcript item represents a provider generation.

    LangChain messages expose ``type == "ai"`` even when a gateway omits
    usage metadata. Accept the serialized assistant spelling as well so a
    restored graph state cannot make completed rounds disappear from local
    request accounting.
    """
    if isinstance(message, dict):
        kind = message.get("type") or message.get("role")
    else:
        kind = getattr(message, "type", None) or getattr(message, "role", None)
    return str(kind or "").strip().lower() in {"ai", "assistant"}


def _format_result(raw: dict[str, Any], thread_id: str, provider: str = "") -> dict[str, Any]:
    """Normalise a deepagents invoke() result to the standard output schema."""
    messages = raw.get("messages", [])
    result_text = ""
    for msg in reversed(messages):
        content = getattr(msg, "content", None)
        if content:
            result_text = extract_text(content)
            break

    todos = []
    raw_todos = raw.get("todos", [])
    if isinstance(raw_todos, list):
        for i, t in enumerate(raw_todos):
            if isinstance(t, dict):
                todos.append(t)
            elif isinstance(t, str):
                todos.append({"id": f"todo-{i+1}", "task": t, "status": "completed", "subtasks": []})

    prompt_tokens = completion_tokens = cached_prompt_tokens = requests = 0
    for message in messages:
        prompt, completion, cached = _message_usage(message)
        prompt_tokens += prompt
        completion_tokens += completion
        cached_prompt_tokens += cached
        if prompt > 0 or completion > 0 or cached > 0 or _is_ai_message(message):
            requests += 1

    return {
        "success":   True,
        "result":    result_text,
        "todos":     todos,
        "files":     raw.get("files", {}),
        "thread_id": thread_id,
        "usage": {
            "provider": provider,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
            "cached_prompt_tokens": cached_prompt_tokens,
            "requests": requests,
        },
        "error":     None,
    }


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def _parse_config(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            return {}
    return {}


def _error(msg: str) -> dict[str, Any]:
    return {"success": False, "result": "", "todos": [], "files": {}, "thread_id": "", "error": msg}


def _print_json(obj: Any) -> None:
    print(json.dumps(obj, default=str), flush=True)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

_COMMANDS = {
    "check_status":      cmd_check_status,
    "list_capabilities": cmd_list_capabilities,
    "execute_task":      cmd_execute_task,
}


def main() -> None:
    if len(sys.argv) < 2:
        _print_json(_error(
            f"No command specified. Available: {', '.join(_COMMANDS)}"
        ))
        sys.exit(1)

    command = sys.argv[1]
    raw_params = sys.argv[2] if len(sys.argv) > 2 else "{}"
    if raw_params == "--stdin":
        # The terminal sends execute_task's payload (LLM API key included) on
        # stdin: argv is readable by any same-user process.
        raw_params = sys.stdin.read() or "{}"

    try:
        params = json.loads(raw_params)
    except (json.JSONDecodeError, ValueError) as exc:
        _print_json(_error(f"Invalid JSON params: {exc}"))
        sys.exit(1)
    if not isinstance(params, dict):
        _print_json(_error("JSON params must be an object"))
        sys.exit(1)

    if command not in _COMMANDS:
        _print_json(_error(f"Unknown command '{command}'. Available: {', '.join(_COMMANDS)}"))
        sys.exit(1)

    _print_json(_COMMANDS[command](params))


if __name__ == "__main__":
    main()
