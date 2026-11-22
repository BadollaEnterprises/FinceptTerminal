"""
config.py — LiteLLM / rdagent LLM config wiring for Fincept rdagents.

rdagent uses environment variables for its LLM backend (LiteLLMAPIBackend).
This module translates the Fincept LLM config dict into the correct env vars
so any provider (OpenAI, Anthropic, MiniMax, DeepSeek, Azure, etc.) works.

Key env vars rdagent reads:
  CHAT_MODEL              — model name passed to litellm
  OPENAI_API_KEY          — API key (used for all providers via litellm)
  CHAT_OPENAI_BASE_URL    — custom base URL for OpenAI-compatible endpoints
  EMBEDDING_MODEL         — embedding model (optional)
"""

from __future__ import annotations

import os
from typing import Any


# Providers that need anthropic-style API key env var
_ANTHROPIC_PROVIDERS = {"anthropic"}

# Providers that use a custom base_url (OpenAI-compatible)
_CUSTOM_BASE_PROVIDERS = {
    "minimax", "deepseek", "openrouter", "together", "fireworks",
    "groq", "mistral", "cohere",
}

# litellm model prefix map — litellm needs provider prefix for non-OpenAI models
_LITELLM_PREFIXES: dict[str, str] = {
    "anthropic":  "anthropic/",
    "groq":       "groq/",
    "deepseek":   "deepseek/",
    "mistral":    "mistral/",
    "cohere":     "cohere/",
    "together":   "together_ai/",
    "fireworks":  "fireworks_ai/",
    "openrouter": "openrouter/",
}
_PROVIDER_API_KEY_NAMES = tuple(f"{provider.upper()}_API_KEY" for provider in _LITELLM_PREFIXES)

# Default embedding model
_DEFAULT_EMBEDDING = "text-embedding-3-small"
_DEFAULT_OUTPUT_TOKENS = 4096
_MAX_OUTPUT_TOKENS = 2_000_000


def _bounded_output_tokens(value: Any) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError):
        parsed = _DEFAULT_OUTPUT_TOKENS
    if parsed <= 0:
        parsed = _DEFAULT_OUTPUT_TOKENS
    return min(parsed, _MAX_OUTPUT_TOKENS)


def apply_llm_config(config: dict[str, Any]) -> dict[str, str]:
    """
    Translate Fincept LLM config dict into rdagent env vars.

    Sets os.environ in place so rdagent's LiteLLMAPIBackend picks them up.

    Args:
        config: Dict with keys: llm_provider, llm_api_key, llm_model,
                llm_base_url (optional), llm_embedding_model (optional)

    Returns:
        Dict of env vars that were set (for logging/debugging).
    """
    provider   = str(config.get("llm_provider") or "openai").lower()
    api_key    = config.get("llm_api_key", "")
    model      = config.get("llm_model", "gpt-4o")
    base_url   = config.get("llm_base_url", "")
    max_tokens = _bounded_output_tokens(config.get("llm_max_tokens"))

    env_vars: dict[str, str] = {}

    # --- Model name ---
    # litellm needs a provider prefix for non-OpenAI providers
    prefix = _LITELLM_PREFIXES.get(provider, "")
    if prefix and not model.startswith(prefix):
        litellm_model = prefix + model
    else:
        litellm_model = model

    # For custom base_url providers (MiniMax, DeepSeek hosted, etc.)
    # litellm treats them as openai-compatible — no prefix needed
    if provider in _CUSTOM_BASE_PROVIDERS and base_url:
        litellm_model = model  # use raw model name

    env_vars["CHAT_MODEL"] = litellm_model
    os.environ["CHAT_MODEL"] = litellm_model

    # --- API key ---
    env_vars["OPENAI_API_KEY"] = api_key
    os.environ["OPENAI_API_KEY"] = api_key

    # LiteLLM's provider adapters also read their provider-specific standard
    # variable (the RD-Agent DeepSeek setup documents DEEPSEEK_API_KEY). Keep
    # the universal key above for OpenAI-compatible custom gateways.
    provider_key = f"{provider.upper()}_API_KEY"
    env_vars[provider_key] = api_key
    os.environ[provider_key] = api_key

    # Anthropic also reads its own env var
    if provider == "anthropic":
        env_vars["ANTHROPIC_API_KEY"] = api_key
        os.environ["ANTHROPIC_API_KEY"] = api_key

    # --- Base URL (OpenAI-compatible custom endpoints) ---
    if base_url:
        env_vars["CHAT_OPENAI_BASE_URL"] = base_url
        os.environ["CHAT_OPENAI_BASE_URL"] = base_url
        env_vars["OPENAI_API_BASE"] = base_url
        os.environ["OPENAI_API_BASE"] = base_url
    else:
        # Clear stale base URL from previous call
        os.environ.pop("CHAT_OPENAI_BASE_URL", None)
        os.environ.pop("OPENAI_API_BASE", None)

    # RDAgent 0.8's LLMSettings.chat_max_tokens reaches LiteLLM's
    # completion(max_tokens=...). The subclass accepts both the parent name
    # and its LITELLM_ prefixed form; set both so version-specific settings
    # source ordering cannot silently drop the user's profile ceiling.
    token_text = str(max_tokens)
    for name in ("CHAT_MAX_TOKENS", "LITELLM_CHAT_MAX_TOKENS"):
        env_vars[name] = token_text
        os.environ[name] = token_text

    # RD-Agent wraps LiteLLM in its own retry loop. Its default is ten total
    # attempts, which can multiply a single paid agent step even though the
    # inner LiteLLM request already has max_retries=0. One means one attempt.
    for name in ("MAX_RETRY", "LITELLM_MAX_RETRY"):
        env_vars[name] = "1"
        os.environ[name] = "1"

    # --- Embedding model ---
    embedding_model = config.get("llm_embedding_model", _DEFAULT_EMBEDDING)
    env_vars["EMBEDDING_MODEL"] = embedding_model
    os.environ["EMBEDDING_MODEL"] = embedding_model

    return env_vars


def clear_llm_config() -> None:
    """Remove rdagent LLM env vars (call between tasks if needed)."""
    for key in ("CHAT_MODEL", "OPENAI_API_KEY", "ANTHROPIC_API_KEY",
                "CHAT_OPENAI_BASE_URL", "OPENAI_API_BASE", "CHAT_MAX_TOKENS",
                "LITELLM_CHAT_MAX_TOKENS", "MAX_RETRY", "LITELLM_MAX_RETRY",
                "EMBEDDING_MODEL", *_PROVIDER_API_KEY_NAMES):
        os.environ.pop(key, None)
