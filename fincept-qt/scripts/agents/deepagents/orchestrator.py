"""Compatibility tombstone for the retired hosted-model orchestrator.

Older integrations imported :class:`FinceptOrchestrator` directly. The
terminal now requires a user-owned, tool-calling provider and executes Deep
Agents through ``cli.py``; DeepSeek is the default. This module deliberately
contains no HTTP client, endpoint, account-key handling, retry loop, or model
fallback, so importing an old integration cannot spend Fincept credits.
"""

from __future__ import annotations

from typing import Any


class FinceptOrchestrator:
    """Retained only to return a safe migration error to legacy callers."""

    def __init__(self, *_args: Any, **_kwargs: Any) -> None:
        pass

    def execute(
        self,
        _task: str,
        _agent_type: str,
        _subagent_names: list[str],
    ) -> dict[str, Any]:
        return {
            "success": False,
            "result": "",
            "todos": [],
            "files": {},
            "error": (
                "The hosted model fallback is disabled by the community credit firewall. "
                "Configure your DeepSeek API key and run this task through the Deep Agents CLI."
            ),
        }
