#!/usr/bin/env python3
"""Zero-Fincept-credit reports for the terminal's Pro Analytics panel.

The report builders are deterministic and testable with supplied data.  The
``report`` command obtains delayed public market data from Nasdaq's public JSON
endpoints with a Yahoo chart fallback, using only Python's standard library, then passes it to the local
``byo_analytics`` calculation engine.  It never calls a Fincept API endpoint,
never reads an LLM key, and does not depend on pandas or yfinance.

Usage: byo_pro_analytics.py report '{"kind":"technical_rating","symbol":"MSFT"}'
"""

from __future__ import annotations

import json
import math
import sys
import importlib.util
import re
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Mapping, Sequence

try:
    import byo_analytics as engine
except ModuleNotFoundError:
    _engine_path = Path(__file__).with_name("byo_analytics.py")
    _engine_spec = importlib.util.spec_from_file_location("byo_analytics", _engine_path)
    if not _engine_spec or not _engine_spec.loader:
        raise
    engine = importlib.util.module_from_spec(_engine_spec)
    _engine_spec.loader.exec_module(engine)


def _pct(value: float | None, digits: int = 1) -> str:
    return "—" if value is None or not math.isfinite(value) else f"{value * 100:.{digits}f}%"


def _num(value: float | None, digits: int = 2) -> str:
    return "—" if value is None or not math.isfinite(value) else f"{value:.{digits}f}"


def _envelope(report: Dict[str, Any], *, delayed_minutes: int = 15) -> Dict[str, Any]:
    return {
        "data": {"report": report},
        "meta": {
            "as_of": datetime.now(timezone.utc).isoformat(),
            "delayed_minutes": delayed_minutes,
            "stale": False,
            "cost_route": "local_public_data",
            "fincept_credits": 0,
        },
    }


def technical_report(
    symbol: str,
    closes: Sequence[float],
    benchmark_closes: Sequence[float] | None = None,
    volumes: Sequence[float] | None = None,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {"closes": closes}
    if benchmark_closes is not None:
        payload["benchmark_closes"] = benchmark_closes
    if volumes is not None:
        payload["volumes"] = volumes
    result = engine.technical_rating(payload)
    indicators = result["indicators"]
    performance = result["performance"]
    ma_votes = result["votes"]["moving_averages"]
    oscillator_votes = result["votes"]["oscillators"]
    rating = result["rating"].replace("_", " ")
    tone = "positive" if result["score"] >= 25 else "negative" if result["score"] <= -25 else "neutral"
    stats = [
        {"label": "Composite score", "value": _num(result["score"], 0), "tone": tone},
        {"label": "MA votes", "value": f'{ma_votes["buy"]} BUY · {ma_votes["sell"]} SELL'},
        {"label": "Oscillator votes", "value": f'{oscillator_votes["buy"]} BUY · {oscillator_votes["sell"]} SELL'},
        {"label": "RSI 14", "value": _num(indicators["rsi14"])},
        {"label": "52-week range",
         "value": f'{_num(performance["range_52_week_low"])}–{_num(performance["range_52_week_high"])}',
         "hint": f'{_pct(performance["range_52_week_position"])} through range'},
        {"label": "Annualized volatility", "value": _pct(performance["annualized_volatility"])},
    ]
    if "relative_strength_3m" in performance:
        stats.append({"label": "Relative strength vs SPY", "value": _pct(performance["relative_strength_3m"])})
    if result.get("volume"):
        stats.append({"label": "Volume vs 20d", "value": f'{_num(result["volume"]["relative_to_average"])}x',
                      "hint": f'{result["volume"]["latest"]:,.0f} latest'})

    moving_average_rows = [
        [row["name"], _num(row["value"]), row["signal"]]
        for row in result["moving_averages"]
    ]
    oscillator_rows = [
        [row["name"], _num(row["value"]), row["signal"]]
        for row in result["oscillators"]
    ]
    performance_rows = []
    for key, label in (("one_week", "1 week"), ("one_month", "1 month"),
                       ("three_month", "3 months"), ("six_month", "6 months"), ("one_year", "1 year")):
        if key in performance:
            performance_rows.append([label, _pct(performance[key])])
    return _envelope(
        {
            "title": "Technical rating",
            "headline": rating,
            "tone": tone,
            "stats": stats,
            "tables": [
                {
                    "title": "Moving averages · 10",
                    "columns": ["Average", "Value", "Signal"],
                    "rows": moving_average_rows,
                },
                {
                    "title": "Oscillators · 6",
                    "columns": ["Oscillator", "Value", "Signal"],
                    "rows": oscillator_rows,
                },
                {"title": "Performance", "columns": ["Period", "Return"], "rows": performance_rows},
            ],
            "notes": [f"{symbol.upper()} · deterministic local consensus from 10 moving averages and 6 oscillators.",
                      "Relative strength compares the trailing three-month return with SPY."],
        }
    )


def portfolio_report(
    symbols: Sequence[str],
    weights: Sequence[float],
    returns: Mapping[str, Sequence[float]],
    benchmark_returns: Sequence[float] | None = None,
) -> Dict[str, Any]:
    normalized_symbols = [str(symbol).strip().upper() for symbol in symbols]
    if len(set(normalized_symbols)) != len(normalized_symbols):
        raise ValueError("portfolio risk does not accept duplicate symbols")
    if weights and len(weights) != len(symbols):
        raise ValueError("portfolio risk needs exactly one weight per symbol, or no weights for equal weighting")
    weight_map = dict(zip(symbols, weights)) if weights else None
    risk_payload: Dict[str, Any] = {"returns": returns, "weights": weight_map}
    if benchmark_returns is not None:
        risk_payload["benchmark_returns"] = benchmark_returns
    result = engine.portfolio_risk(risk_payload)
    rows = [
        [symbol, _pct(result["weights"].get(symbol)), _pct(result["asset_volatility"].get(symbol)),
         _pct(result["risk_contribution"].get(symbol))]
        for symbol in symbols
    ]
    correlation_rows = [
        [left] + [_num(result["correlation_matrix"][left][right]) for right in symbols]
        for left in symbols
    ]
    tone = "negative" if result["annualized_volatility"] >= 0.30 else "neutral"
    benchmark = result.get("benchmark") or {}
    stats = [
        {"label": "Annualized volatility", "value": _pct(result["annualized_volatility"])},
        {"label": "Historical VaR", "value": _pct(result["historical_var"])},
        {"label": "Expected shortfall", "value": _pct(result["expected_shortfall"])},
        {"label": "Maximum drawdown", "value": _pct(result["max_drawdown"]), "tone": "negative"},
        {"label": "Diversification ratio", "value": f'{_num(result["diversification_ratio"])}x'},
    ]
    if benchmark:
        stats.extend([
            {"label": "Beta vs S&P 500", "value": _num(benchmark.get("beta"))},
            {"label": "Annualized alpha", "value": _pct(benchmark.get("annualized_alpha"))},
            {"label": "S&P 500 correlation", "value": _num(benchmark.get("correlation"))},
        ])
    return _envelope(
        {
            "title": "Portfolio risk",
            "headline": "LOCAL RISK SNAPSHOT",
            "tone": tone,
            "stats": stats,
            "tables": [
                {"title": "Risk contribution", "columns": ["Holding", "Weight", "Volatility", "Risk share"],
                 "rows": rows},
                {"title": "Correlation matrix", "columns": ["Holding"] + list(symbols),
                 "rows": correlation_rows},
            ],
            "notes": ["One-day historical VaR and expected shortfall from aligned delayed public closes.",
                      "Risk contribution is covariance based and sums to 100%.",
                      "Beta and alpha use SPY as the S&P 500 proxy; alpha is annualized from daily observations."],
        }
    )


def options_report(
    symbol: str,
    spot: float,
    chain: Sequence[Mapping[str, Any]],
    *,
    time_to_expiry: float = 30 / 365,
    expiry: str = "",
) -> Dict[str, Any]:
    result = engine.options_positioning({"spot": spot, "chain": chain, "time_to_expiry": time_to_expiry})
    ratio = result["put_call_oi_ratio"]
    gamma = result["gamma_regime"]
    tone = "negative" if ratio is not None and ratio > 1.2 else "positive" if ratio is not None and ratio < 0.8 else "neutral"
    visible_gamma = sorted(
        sorted(result["gamma_by_strike"], key=lambda row: abs(row["strike"] - spot))[:31],
        key=lambda row: row["strike"],
    )
    gamma_rows = [
        [
            _num(row["strike"]),
            _num(row["call_gamma"], 0),
            _num(row["put_gamma"], 0),
            _num(row["net_gamma"], 0),
        ]
        for row in visible_gamma
    ]
    unusual_rows = [
        [
            row["side"],
            _num(row["strike"]),
            _num(row["volume"], 0),
            _num(row["open_interest"], 0),
            f'{_num(row["volume_oi_ratio"])}x',
        ]
        for row in result["unusual_activity"]
    ]
    notes = ["Nearest listed expiry from delayed public option-chain data.",
             "Implied volatility, gamma and expected move are derived locally from public bid/ask midpoints."]
    if expiry:
        notes.insert(0, f"Expiry: {expiry}.")
    return _envelope(
        {
            "title": "Options positioning",
            "headline": f"{symbol.upper()} · {gamma} GAMMA",
            "tone": tone,
            "stats": [
                {"label": "Put / call OI", "value": _num(ratio)},
                {"label": "Put / call volume", "value": _num(result["put_call_volume_ratio"])},
                {"label": "Max pain", "value": _num(result["max_pain"])},
                {"label": "Spot distance", "value": _pct(result["max_pain_distance_pct"])},
                {"label": "Expected move", "value": _pct(result["expected_move_pct"]),
                 "hint": _num(result["expected_move"])},
                {"label": "ATM implied volatility", "value": _pct(result["atm_implied_volatility"])},
                {"label": "Volatility skew", "value": _pct(result["volatility_skew"])},
                {"label": "Net gamma", "value": _num(result["net_gamma_exposure"], 0)},
            ],
            "tables": [
                {"title": "Open-interest walls", "columns": ["Level", "Strike"],
                 "rows": [["Call wall", _num(result["call_wall"])], ["Put wall", _num(result["put_wall"])]]},
                {"title": "Gamma by strike", "columns": ["Strike", "Call GEX", "Put GEX", "Net GEX"],
                 "rows": gamma_rows},
                {"title": "Unusual activity", "columns": ["Side", "Strike", "Volume", "Open interest", "Vol/OI"],
                 "rows": unusual_rows},
            ],
            "notes": notes,
        }
    )


def market_pulse_report(
    signals: Mapping[str, float],
    performance: Mapping[str, Mapping[str, float]] | None = None,
    sectors: Mapping[str, float] | None = None,
) -> Dict[str, Any]:
    result = engine.market_pulse({"signals": signals, "expected_signals": 7})
    tone = "positive" if result["regime"] == "RISK_ON" else "negative" if result["regime"] == "RISK_OFF" else "neutral"
    rows = [[name.replace("_", " ").title(), _num(value)] for name, value in result["signals"].items()]
    sector_rows = [[name, _pct(value)] for name, value in sorted((sectors or {}).items(), key=lambda item: item[1], reverse=True)]
    horizons = ["1D", "1W", "1M", "3M", "1Y"]
    performance_rows = [
        [symbol] + [_pct(values.get(horizon)) for horizon in horizons]
        for symbol, values in (performance or {}).items()
    ]
    stats = [
        {"label": "Risk score", "value": _num(result["score"], 0), "tone": tone},
        {"label": "Signal coverage", "value": _pct(result["coverage"])},
    ]
    if sectors:
        positive = sum(1 for value in sectors.values() if value > 0)
        leader = max(sectors, key=sectors.get)
        laggard = min(sectors, key=sectors.get)
        stats.extend([
            {"label": "Sector breadth", "value": f"{positive} / {len(sectors)} positive"},
            {"label": "Sector leader", "value": leader, "hint": _pct(sectors[leader])},
            {"label": "Sector laggard", "value": laggard, "hint": _pct(sectors[laggard])},
        ])
    return _envelope(
        {
            "title": "Market pulse",
            "headline": result["regime"].replace("_", " "),
            "tone": tone,
            "stats": stats,
            "tables": [
                {"title": "Seven risk-appetite signals", "columns": ["Signal", "Normalized value"], "rows": rows},
                {"title": "Sector leadership · 1 month", "columns": ["Sector", "Return"], "rows": sector_rows},
                {"title": "Cross-asset performance", "columns": ["Asset"] + horizons, "rows": performance_rows},
            ],
            "notes": ["Composite of seven delayed public cross-asset signals; values are normalized to -1…+1.",
                      "Sector leadership and cross-asset returns are calculated locally from public adjusted closes."],
        }
    )


_NASDAQ_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Origin": "https://www.nasdaq.com",
    "Referer": "https://www.nasdaq.com/",
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 Chrome/124 Safari/537.36",
}
_ETF_SYMBOLS = {"SPY", "RSP", "HYG", "TLT", "UUP", "QQQ", "GLD", "IWM", "DIA"}
_MAX_PUBLIC_RESPONSE_BYTES = 8 * 1024 * 1024


def _read_json_response(response: Any, *, max_bytes: int = _MAX_PUBLIC_RESPONSE_BYTES) -> Any:
    """Decode one bounded public JSON response without buffering an unlimited body."""
    body = response.read(max_bytes + 1)
    if len(body) > max_bytes:
        raise ValueError(f"Public data response exceeds {max_bytes} bytes")
    return json.loads(body.decode("utf-8", errors="replace"))


def _get_json(url: str) -> Mapping[str, Any]:
    request = urllib.request.Request(url, headers=_NASDAQ_HEADERS)
    with urllib.request.urlopen(request, timeout=30) as response:
        return _read_json_response(response)


def _number(value: Any) -> float | None:
    text = str(value or "").strip().replace(",", "").replace("$", "").replace("%", "")
    if not text or text in {"--", "—", "N/A"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _asset_classes(symbol: str) -> Sequence[str]:
    if symbol in _ETF_SYMBOLS:
        return ("etf", "stocks")
    if symbol in {"BTC", "BTC-USD"}:
        return ("crypto",)
    return ("stocks", "etf")


def _period_start(period: str) -> datetime:
    days = {"5d": 15, "1mo": 40, "3mo": 110, "6mo": 210, "1y": 400,
            "2y": 800, "5y": 1900}.get(period, 400)
    return datetime.now(timezone.utc) - timedelta(days=days)


def _yahoo_history_bars(symbol: str, period: str = "1y", *, get_json=_get_json):
    params = urllib.parse.urlencode({"range": period, "interval": "1d", "events": "history"})
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(symbol, safe='')}?" + params)
    payload = get_json(url)
    results = ((payload.get("chart") or {}).get("result") or [])
    if not results:
        raise ValueError("Yahoo Finance returned no chart history")
    result = results[0]
    timestamps = result.get("timestamp") or []
    quote = (((result.get("indicators") or {}).get("quote") or [{}])[0])
    closes = quote.get("close") or []
    volumes = quote.get("volume") or []
    history = []
    for index, timestamp in enumerate(timestamps):
        if index >= len(closes) or closes[index] is None:
            continue
        volume = volumes[index] if index < len(volumes) else None
        history.append({
            "date": datetime.fromtimestamp(int(timestamp), timezone.utc).date().isoformat(),
            "close": float(closes[index]),
            "volume": None if volume is None else float(volume),
        })
    if not history:
        raise ValueError("Yahoo Finance returned no usable chart history")
    return sorted(history, key=lambda bar: bar["date"])


def _history_bars(symbol: str, period: str = "1y", *, get_json=_get_json):
    symbol = symbol.strip().upper()
    request_symbol = "BTC" if symbol == "BTC-USD" else symbol
    end = datetime.now(timezone.utc)
    params = {"fromdate": _period_start(period).date().isoformat(),
              "todate": end.date().isoformat(), "limit": 5000}
    last_error = None
    for asset_class in _asset_classes(symbol):
        url = (f"https://api.nasdaq.com/api/quote/{urllib.parse.quote(request_symbol, safe='')}/historical?" +
               urllib.parse.urlencode({"assetclass": asset_class, **params}))
        try:
            payload = get_json(url)
            rows = ((((payload.get("data") or {}).get("tradesTable") or {}).get("rows")) or [])
            history = []
            for row in rows:
                close = _number(row.get("close"))
                try:
                    date = datetime.strptime(str(row.get("date", "")), "%m/%d/%Y").date().isoformat()
                except ValueError:
                    continue
                if close is not None:
                    history.append({"date": date, "close": close, "volume": _number(row.get("volume"))})
            if history:
                return sorted(history, key=lambda bar: bar["date"])
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            last_error = exc
    try:
        return _yahoo_history_bars(symbol, period, get_json=get_json)
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        last_error = exc
    detail = f": {last_error}" if last_error else ""
    raise ValueError(f"No public price history for {symbol}{detail}")


def _history(symbol: str, period: str = "1y", *, get_json=_get_json):
    return [(bar["date"], bar["close"]) for bar in _history_bars(symbol, period, get_json=get_json)]


def _public_technical(payload: Mapping[str, Any]) -> Dict[str, Any]:
    symbol = str(payload.get("symbol", "")).strip().upper()
    if not symbol:
        raise ValueError("symbol is required")
    bars = _history_bars(symbol)
    benchmark = [close for _, close in _history("SPY")]
    volumes = [bar["volume"] for bar in bars]
    if any(value is None for value in volumes):
        volumes = None
    return technical_report(symbol, [bar["close"] for bar in bars], benchmark, volumes)


def _public_portfolio(payload: Mapping[str, Any]) -> Dict[str, Any]:
    symbols = [str(value).strip().upper() for value in payload.get("symbols", []) if str(value).strip()]
    if not symbols:
        raise ValueError("symbols are required")
    if len(set(symbols)) != len(symbols):
        raise ValueError("portfolio risk does not accept duplicate symbols")
    weights = [float(value) for value in payload.get("weights", [])]
    if weights and len(weights) != len(symbols):
        raise ValueError("portfolio risk needs exactly one weight per symbol, or no weights for equal weighting")
    price_series = {symbol: dict(_history(symbol)) for symbol in symbols}
    benchmark_series = price_series.get("SPY") or dict(_history("SPY"))
    common = set(price_series[symbols[0]])
    for series in price_series.values():
        common.intersection_update(series)
    common.intersection_update(benchmark_series)
    if len(common) < 30:
        raise ValueError("Not enough overlapping public history for the portfolio")
    returns = {}
    dates = sorted(common)
    for symbol, series in price_series.items():
        closes = [series[date] for date in dates]
        returns[symbol] = [closes[index] / closes[index - 1] - 1.0 for index in range(1, len(closes))]
    benchmark_closes = [benchmark_series[date] for date in dates]
    benchmark_returns = [
        benchmark_closes[index] / benchmark_closes[index - 1] - 1.0
        for index in range(1, len(benchmark_closes))
    ]
    return portfolio_report(symbols, weights, returns, benchmark_returns)


_CBOE_OPTION = re.compile(r"^([A-Z0-9.]{1,8})\s*(\d{6})([CP])(\d{8})$")


def _cboe_option_chain(symbol: str, *, get_json=_get_json):
    base = symbol.strip().upper().lstrip("^")
    candidates = (["_" + base, base] if base in {"SPX", "VIX", "RUT", "NDX", "DJX"}
                  else [base, "_" + base])
    last_error = None
    for candidate in candidates:
        url = f"https://cdn.cboe.com/api/global/delayed_quotes/options/{urllib.parse.quote(candidate, safe='')}.json"
        try:
            data = get_json(url).get("data") or {}
            options = data.get("options") or []
            parsed = []
            for row in options:
                match = _CBOE_OPTION.match(str(row.get("option") or "").strip().upper())
                if not match:
                    continue
                expiry = datetime.strptime(match.group(2), "%y%m%d").date().isoformat()
                parsed.append((expiry, match.group(3), int(match.group(4)) / 1000.0, row))
            if not parsed:
                continue
            nearest = min(row[0] for row in parsed)
            by_strike: Dict[float, Dict[str, float]] = {}
            for expiry, side, strike, row in parsed:
                if expiry != nearest:
                    continue
                contract = by_strike.setdefault(
                    strike,
                    {"strike": strike, "call_oi": 0.0, "put_oi": 0.0,
                     "call_volume": 0.0, "put_volume": 0.0},
                )
                prefix = "call" if side == "C" else "put"
                contract[f"{prefix}_oi"] = _number(row.get("open_interest")) or 0.0
                contract[f"{prefix}_volume"] = _number(row.get("volume")) or 0.0
                for source, target in (("bid", f"{prefix}_bid"), ("ask", f"{prefix}_ask")):
                    value = _number(row.get(source))
                    if value is not None:
                        contract[target] = value
            spot = _number(data.get("current_price")) or _number(data.get("close"))
            if spot is None:
                bid, ask = _number(data.get("bid")), _number(data.get("ask"))
                spot = (bid + ask) / 2.0 if bid is not None and ask is not None else None
            if spot is None:
                raise ValueError("Cboe returned no underlying price")
            return spot, nearest, [by_strike[strike] for strike in sorted(by_strike)]
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            last_error = exc
    detail = f": {last_error}" if last_error else ""
    raise ValueError(f"Cboe returned no public option chain for {symbol}{detail}")


def _option_chain(symbol: str, *, get_json=_get_json):
    last_error = None
    for asset_class in _asset_classes(symbol):
        params = urllib.parse.urlencode({"assetclass": asset_class, "limit": 5000})
        url = f"https://api.nasdaq.com/api/quote/{urllib.parse.quote(symbol, safe='')}/option-chain?{params}"
        try:
            payload = get_json(url)
            data = payload.get("data") or {}
            rows = ((data.get("table") or {}).get("rows")) or []
            spot_match = re.search(r"\$([0-9,]+(?:\.[0-9]+)?)", str(data.get("lastTrade", "")))
            spot = _number(spot_match.group(1)) if spot_match else None
            expiry = ""
            chain = []
            for row in rows:
                group = str(row.get("expirygroup") or "").strip()
                if group:
                    if expiry and group != expiry:
                        break
                    expiry = group
                    continue
                if not expiry:
                    continue
                strike = _number(row.get("strike"))
                if strike is None:
                    continue
                contract = {"strike": strike,
                            "call_oi": _number(row.get("c_Openinterest")) or 0.0,
                            "put_oi": _number(row.get("p_Openinterest")) or 0.0,
                            "call_volume": _number(row.get("c_Volume")) or 0.0,
                            "put_volume": _number(row.get("p_Volume")) or 0.0}
                for source, target in (("c_Bid", "call_bid"), ("c_Ask", "call_ask"),
                                       ("p_Bid", "put_bid"), ("p_Ask", "put_ask")):
                    value = _number(row.get(source))
                    if value is not None:
                        contract[target] = value
                chain.append(contract)
            if chain:
                if spot is None:
                    history = _history(symbol, "5d", get_json=get_json)
                    spot = history[-1][1]
                return spot, expiry, chain
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            last_error = exc
    try:
        return _cboe_option_chain(symbol, get_json=get_json)
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        last_error = exc
    detail = f": {last_error}" if last_error else ""
    raise ValueError(f"No public option chain for {symbol}{detail}")


def _public_options(payload: Mapping[str, Any]) -> Dict[str, Any]:

    symbol = str(payload.get("symbol", "")).strip().upper()
    if not symbol:
        raise ValueError("symbol is required")
    spot, expiry, chain = _option_chain(symbol)
    expiry_date = None
    for expiry_format in ("%B %d, %Y", "%Y-%m-%d"):
        try:
            expiry_date = datetime.strptime(expiry, expiry_format).replace(tzinfo=timezone.utc)
            break
        except ValueError:
            continue
    if expiry_date is not None:
        time_to_expiry = max(1.0 / 365.0, (expiry_date - datetime.now(timezone.utc)).total_seconds() / (365.0 * 86400.0))
    else:
        time_to_expiry = 30 / 365
    result = options_report(symbol, spot, chain, time_to_expiry=time_to_expiry, expiry=expiry)
    result["meta"]["expiry"] = expiry
    return result


def _normalized_return(symbol: str, days: int = 20) -> float:
    closes = [close for _, close in _history(symbol, "3mo")]
    if len(closes) <= days:
        raise ValueError(f"Not enough public history for {symbol}")
    return max(-1.0, min(1.0, float(closes[-1] / closes[-days - 1] - 1) * 5.0))


def _public_market_pulse(_: Mapping[str, Any]) -> Dict[str, Any]:
    sector_symbols = {
        "XLK": "Technology",
        "XLF": "Financials",
        "XLY": "Consumer discretionary",
        "XLC": "Communication services",
        "XLI": "Industrials",
        "XLP": "Consumer staples",
        "XLV": "Health care",
        "XLE": "Energy",
        "XLB": "Materials",
        "XLRE": "Real estate",
        "XLU": "Utilities",
    }
    asset_symbols = {
        "SPY": "US equities",
        "QQQ": "Nasdaq 100",
        "IWM": "Small caps",
        "TLT": "Treasuries",
        "HYG": "High yield",
        "GLD": "Gold",
        "UUP": "US dollar",
        "BTC-USD": "Bitcoin",
    }
    requested = set(sector_symbols) | set(asset_symbols) | {"RSP"}
    histories: Dict[str, Sequence[tuple[str, float]]] = {}
    # Public endpoints are independent; parallel fetch keeps a full pulse under
    # the terminal's two-minute local-script timeout and partial failures do not
    # erase the signals that are available.
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(_history, symbol, "1y"): symbol for symbol in requested}
        for future in as_completed(futures):
            symbol = futures[future]
            try:
                histories[symbol] = future.result()
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                continue

    def trailing_return(symbol: str, days: int) -> float | None:
        history = histories.get(symbol)
        if not history or len(history) <= days:
            return None
        closes = [close for _, close in history]
        return closes[-1] / closes[-days - 1] - 1.0

    def normalized(symbol: str, days: int = 20) -> float | None:
        value = trailing_return(symbol, days)
        return None if value is None else max(-1.0, min(1.0, value * 5.0))

    sector_returns = {
        name: value
        for symbol, name in sector_symbols.items()
        if (value := trailing_return(symbol, 21)) is not None
    }
    signals: Dict[str, float] = {}
    if (value := normalized("SPY")) is not None:
        signals["equity_trend"] = value
    if sector_returns:
        positive_fraction = sum(value > 0 for value in sector_returns.values()) / len(sector_returns)
        signals["breadth"] = positive_fraction * 2.0 - 1.0
    hyg, tlt = normalized("HYG"), normalized("TLT")
    if hyg is not None and tlt is not None:
        signals["credit"] = max(-1.0, min(1.0, hyg - tlt))
    spy_history = histories.get("SPY")
    if spy_history and len(spy_history) >= 21:
        closes = [close for _, close in spy_history]
        daily = [closes[index] / closes[index - 1] - 1.0 for index in range(len(closes) - 20, len(closes))]
        realized_volatility = math.sqrt(252.0) * math.sqrt(sum((value - sum(daily) / len(daily)) ** 2 for value in daily) / (len(daily) - 1))
        signals["volatility"] = max(-1.0, min(1.0, (0.22 - realized_volatility) / 0.15))
    if tlt is not None:
        signals["bonds"] = tlt
    if (value := normalized("UUP")) is not None:
        signals["dollar"] = -value
    if (value := normalized("BTC-USD")) is not None:
        signals["crypto"] = value

    horizon_days = {"1D": 1, "1W": 5, "1M": 21, "3M": 63, "1Y": 252}
    performance = {}
    for symbol, name in asset_symbols.items():
        values = {label: value for label, days in horizon_days.items()
                  if (value := trailing_return(symbol, days)) is not None}
        if values:
            performance[name] = values
    return market_pulse_report(signals, performance, sector_returns)


PUBLIC_REPORTS = {
    "technical_rating": _public_technical,
    "portfolio_risk": _public_portfolio,
    "options_positioning": _public_options,
    "market_pulse": _public_market_pulse,
}


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    try:
        if len(args) < 2 or args[0] != "report":
            raise ValueError("usage: byo_pro_analytics.py report <json>")
        payload = json.loads(args[1])
        kind = str(payload.get("kind", ""))
        result = PUBLIC_REPORTS[kind](payload)
        print(json.dumps(result, allow_nan=False, separators=(",", ":")))
        return 0
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"error": str(exc)}, separators=(",", ":")))
        return 1
    except Exception as exc:
        print(json.dumps({"error": f"Public data request failed: {exc}"}, separators=(",", ":")))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
