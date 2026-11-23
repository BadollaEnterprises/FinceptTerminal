"""Process-local BYO provider usage accounting for one RD-Agent task.

RD-Agent 0.8 logs token estimates but does not expose them to callers.  The
terminal runs one research task per process, so a small locked accumulator is
enough to return cumulative, credential-free usage in progress and result
events.  Counts are explicitly marked as estimates because the upstream
backend discards the provider response usage object.
"""

from __future__ import annotations

import json
import threading
from typing import Any


_lock = threading.Lock()
_provider = "unknown"
_prompt_tokens = 0
_completion_tokens = 0
_cached_prompt_tokens = 0
_requests = 0


def reset_usage(provider: Any = "unknown") -> None:
    global _provider, _prompt_tokens, _completion_tokens, _cached_prompt_tokens, _requests
    normalized = str(provider or "unknown").strip().lower()[:64] or "unknown"
    with _lock:
        _provider = normalized
        _prompt_tokens = 0
        _completion_tokens = 0
        _cached_prompt_tokens = 0
        _requests = 0


def _bounded_count(value: Any) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError):
        return 0
    return max(0, min(parsed, 10_000_000))


def record_usage(prompt_tokens: Any, completion_tokens: Any, cached_prompt_tokens: Any = 0) -> None:
    """Record one provider request; no prompt, response, key or URL is retained."""
    global _prompt_tokens, _completion_tokens, _cached_prompt_tokens, _requests
    prompt = _bounded_count(prompt_tokens)
    completion = _bounded_count(completion_tokens)
    cached = min(prompt, _bounded_count(cached_prompt_tokens))
    with _lock:
        _prompt_tokens += prompt
        _completion_tokens += completion
        _cached_prompt_tokens += cached
        _requests += 1


def _fallback_tokens(value: Any) -> int:
    if isinstance(value, str):
        text = value
    else:
        try:
            text = json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":"))
        except Exception:
            text = str(value)
    return (len(text) + 3) // 4


def record_completion(messages: list[dict[str, Any]], content: str, model: str = "") -> None:
    """Estimate and record the request using the same tokenizer as LiteLLM when available."""
    try:
        from litellm import token_counter

        prompt = token_counter(model=model or None, messages=messages)
    except Exception:
        prompt = _fallback_tokens(messages)
    try:
        from litellm import token_counter

        completion = token_counter(model=model or None, text=content)
    except Exception:
        completion = _fallback_tokens(content)
    record_usage(prompt, completion)


def reported_usage() -> dict[str, Any] | None:
    """Return the provider-neutral envelope only after at least one request."""
    with _lock:
        if _requests <= 0:
            return None
        return {
            "provider": _provider,
            "prompt_tokens": _prompt_tokens,
            "completion_tokens": _completion_tokens,
            "cached_prompt_tokens": _cached_prompt_tokens,
            "requests": _requests,
            "estimated": True,
        }
