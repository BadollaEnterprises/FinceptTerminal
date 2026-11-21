"""Shared zero-credit provider selection for every bundled agent path."""

from __future__ import annotations

from typing import Any, Dict, Mapping


DEPRECATED_PROVIDERS = frozenset({"fincept", "ollama", "codex", "harness:codex"})
PREFERRED_BYO_PROVIDERS = ("deepseek", "anthropic", "google", "groq", "openai", "openrouter")


def _normalized(keys: Mapping[str, Any] | None) -> Dict[str, Any]:
    return {str(key).strip().lower(): value for key, value in (keys or {}).items()}


def _has_key(keys: Mapping[str, Any], provider: str) -> bool:
    return bool(keys.get(provider) or keys.get(f"{provider}_api_key"))


def resolve_byo_provider(api_keys: Mapping[str, Any] | None, fallback: str = "deepseek") -> str:
    """Choose DeepSeek first and never infer a metered/local legacy backend."""
    keys = _normalized(api_keys)
    for provider in PREFERRED_BYO_PROVIDERS:
        if _has_key(keys, provider):
            return provider
    for key, value in keys.items():
        if not key.endswith("_api_key") or not value:
            continue
        provider = key[:-8]
        if provider and provider not in DEPRECATED_PROVIDERS:
            return provider
    normalized_fallback = str(fallback or "deepseek").strip().lower()
    return normalized_fallback if normalized_fallback not in DEPRECATED_PROVIDERS else "deepseek"


def resolve_byo_model_config(api_keys: Mapping[str, Any] | None,
                             fallback: str = "deepseek") -> Dict[str, Any]:
    keys = _normalized(api_keys)
    provider = resolve_byo_provider(keys, fallback=fallback)
    config: Dict[str, Any] = {"provider": provider}
    base_url = keys.get(f"{provider}_base_url")
    if base_url:
        config["base_url"] = base_url
    return config


def normalize_byo_model_config(model_config: Mapping[str, Any] | None,
                               api_keys: Mapping[str, Any] | None) -> Dict[str, Any]:
    """Migrate an explicitly saved legacy route without carrying its model or endpoint."""
    config = dict(model_config or {})
    provider = str(config.get("provider") or "").strip().lower()
    if provider not in DEPRECATED_PROVIDERS:
        return config
    for legacy_field in ("model_id", "api_key", "base_url", "profile_id", "profile_name"):
        config.pop(legacy_field, None)
    config.update(resolve_byo_model_config(api_keys))
    return config


def normalize_embedder_config(provider: str | None,
                              model: str | None) -> tuple[str, str | None]:
    """Move saved Ollama embedding routes to the bundled local embedder."""
    normalized_provider = str(provider or "").strip().lower()
    if normalized_provider == "ollama":
        return "sentence_transformer", None
    return normalized_provider, model
