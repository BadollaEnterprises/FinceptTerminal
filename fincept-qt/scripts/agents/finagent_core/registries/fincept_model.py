"""Compatibility tombstone for the retired hosted Fincept model adapter.

The agent runtime now accepts user-owned providers only, with DeepSeek as the
default. Keeping this importable module gives older extensions a clear error
without retaining an HTTP path that could consume Fincept credits.
"""

from __future__ import annotations

from typing import Any


class FinceptChat:
    """Legacy name that refuses construction before handling credentials."""

    def __init__(self, *_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError(
            "The hosted Fincept model is disabled by the community credit firewall. "
            "Configure your DeepSeek API key or another user-owned provider."
        )
