"""Bounded public-data adapters for Deep Agents.

The terminal already centralises Yahoo session persistence, caching, rate
limiting and error normalisation in ``yfinance_data.py``. Deep Agents reuse
that implementation rather than creating a second unbounded network client.
"""

from __future__ import annotations

import importlib
import json
import re
import sys
from pathlib import Path
from typing import Any


MAX_RESULT_CHARS = 12_000
_SYMBOL = re.compile(r"^[A-Za-z0-9.^=\-]{1,32}$")
_MARKET_VIEWS = frozenset({"quote", "info", "financial_ratios", "company_profile", "historical_period"})


def _data_module() -> Any:
    scripts_dir = Path(__file__).resolve().parents[2]
    scripts_text = str(scripts_dir)
    if scripts_text not in sys.path:
        sys.path.insert(0, scripts_text)
    return importlib.import_module("yfinance_data")


def _clean_symbol(symbol: Any) -> str:
    value = str(symbol or "").strip().upper()
    if not _SYMBOL.fullmatch(value):
        raise ValueError("symbol must be 1-32 ticker characters")
    return value


def _bounded_json(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
    if len(raw) <= MAX_RESULT_CHARS:
        return raw

    preview = raw[: MAX_RESULT_CHARS - 512]
    while True:
        wrapped = json.dumps(
            {"truncated": True, "original_chars": len(raw), "preview": preview},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        if len(wrapped) <= MAX_RESULT_CHARS or not preview:
            return wrapped[:MAX_RESULT_CHARS]
        preview = preview[: max(0, len(preview) - (len(wrapped) - MAX_RESULT_CHARS) - 32)]


def market_snapshot(symbol: Any, view: Any = "quote") -> str:
    """Return a compact public Yahoo snapshot for one validated ticker."""
    ticker = _clean_symbol(symbol)
    command = str(view or "quote").strip().lower()
    if command not in _MARKET_VIEWS:
        raise ValueError(f"view must be one of {sorted(_MARKET_VIEWS)}")
    args = [command, ticker]
    if command == "historical_period":
        args.extend(["1mo", "1d"])
    return _bounded_json(_data_module()._cli_result(args))


def financial_news(symbol: Any, count: Any = 5) -> str:
    """Return at most ten public Yahoo news items for one ticker."""
    ticker = _clean_symbol(symbol)
    try:
        wanted = int(count)
    except (TypeError, ValueError, OverflowError):
        wanted = 5
    wanted = max(1, min(wanted, 10))
    return _bounded_json(_data_module()._cli_result(["news", ticker, str(wanted)]))
