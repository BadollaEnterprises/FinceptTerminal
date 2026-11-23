"""Policy for configuration that may be written to durable agent storage.

Runtime configuration can contain provider credentials and per-run terminal
bridge capabilities.  Tasks and schedules need the model/policy settings for a
future run, but a future run must resolve fresh credentials instead of reading
old secrets from SQLite.
"""
from __future__ import annotations

import json
import re
import sqlite3
from typing import Any


_NEVER_PERSIST = {
    "api_keys",
    "terminal_mcp_endpoint",
    "terminal_mcp_token",
    "terminal_mcp_destructive_token",
    "terminal_tools",
}

_SECRET_KEYS = {
    "api_key",
    "api_keys",
    "authorization",
    "proxy_authorization",
    "password",
    "secret",
    "secret_key",
    "cookie",
    "set_cookie",
}


def _normalized_key(key: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(key).strip().lower()).strip("_")


def _is_sensitive_key(key: Any) -> bool:
    normalized = _normalized_key(key)
    return (
        normalized in _NEVER_PERSIST
        or normalized in _SECRET_KEYS
        or normalized.endswith("_api_key")
        or normalized.endswith("_api_keys")
        or normalized == "token"
        or normalized.endswith("_token")
        or normalized.endswith("_password")
        or normalized.endswith("_secret")
    )


def sanitize_persisted_config(value: Any) -> Any:
    """Return a recursively copied JSON-like value with credentials removed.

    Matching is based on complete normalized key names, so policy controls such
    as ``max_tokens`` and ``token_budget`` remain intact.
    """
    if isinstance(value, dict):
        return {
            key: sanitize_persisted_config(child)
            for key, child in value.items()
            if not _is_sensitive_key(key)
        }
    if isinstance(value, list):
        return [sanitize_persisted_config(child) for child in value]
    if isinstance(value, tuple):
        return tuple(sanitize_persisted_config(child) for child in value)
    return value


def scrub_persisted_config_rows(conn: sqlite3.Connection, table: str) -> int:
    """Remove credentials from existing config rows in a known agent table.

    Invalid legacy JSON is replaced with an empty policy instead of leaving an
    unreadable (and potentially secret-bearing) blob that later breaks list or
    resume operations.
    """
    if table not in {"agent_tasks", "agent_scheduled_tasks"}:
        raise ValueError(f"unsupported config table: {table}")
    changed = 0
    rows = conn.execute(f"SELECT id, config_json FROM {table}").fetchall()
    for row in rows:
        raw = row[1] or "{}"
        try:
            decoded = json.loads(raw)
        except (TypeError, ValueError):
            decoded = {}
        sanitized = sanitize_persisted_config(decoded)
        if not isinstance(sanitized, dict):
            sanitized = {}
        encoded = json.dumps(sanitized)
        if encoded != raw:
            conn.execute(
                f"UPDATE {table} SET config_json = ? WHERE id = ?",
                (encoded, row[0]),
            )
            changed += 1
    return changed
