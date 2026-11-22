#!/usr/bin/env python3
"""Public Equity Research data without Fincept credits or third-party packages.

Usage: byo_equity_data.py fetch '{"operation":"options","symbol":"AAPL",...}'
"""

from __future__ import annotations

import calendar
import json
import math
import re
import sys
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, Iterable, Mapping, Sequence


NASDAQ_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Origin": "https://www.nasdaq.com",
    "Referer": "https://www.nasdaq.com/",
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 Chrome/124 Safari/537.36",
}
_MAX_PUBLIC_RESPONSE_BYTES = 8 * 1024 * 1024


def _read_json_response(response: Any, *, max_bytes: int = _MAX_PUBLIC_RESPONSE_BYTES) -> Any:
    """Decode one bounded public JSON response without buffering an unlimited body."""
    body = response.read(max_bytes + 1)
    if len(body) > max_bytes:
        raise ValueError(f"Public data response exceeds {max_bytes} bytes")
    return json.loads(body.decode("utf-8", errors="replace"))


def _num(value: Any) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value) if math.isfinite(float(value)) else None
    text = str(value or "").strip().replace(",", "").replace("$", "").replace("%", "")
    if not text or text in {"--", "—", "N/A", "NA"}:
        return None
    negative = text.startswith("(") and text.endswith(")")
    if negative:
        text = text[1:-1]
    try:
        number = float(text)
        return -number if negative else number
    except ValueError:
        return None


def _clean(data: Any) -> Any:
    """Drop missing values so Qt's numeric parser sees missing fields as NaN."""
    if isinstance(data, dict):
        return {key: _clean(value) for key, value in data.items() if value is not None}
    if isinstance(data, list):
        return [_clean(value) for value in data]
    return data


def _envelope(dataset: str, data: Dict[str, Any], *, max_age: int = 900,
              pagination: Mapping[str, Any] | None = None,
              source: str = "nasdaq_public",
              source_name: str = "Nasdaq public market data") -> Dict[str, Any]:
    meta: Dict[str, Any] = {
        "dataset": dataset,
        "source": source,
        "source_name": source_name,
        "as_of": datetime.now(timezone.utc).isoformat(),
        "cached": False,
        "age_seconds": 0,
        "max_age_seconds": max_age,
        "stale": False,
        "delayed_minutes": 15,
        "cost_route": "public_zero_credit",
        "fincept_credits": 0,
    }
    if pagination:
        meta["pagination"] = dict(pagination)
    return {"data": _clean(data), "meta": meta}


def _nasdaq(path: str) -> Dict[str, Any]:
    url = "https://api.nasdaq.com" + path
    request = urllib.request.Request(url, headers=NASDAQ_HEADERS)
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = _read_json_response(response)
    data = payload.get("data")
    if not isinstance(data, dict):
        message = payload.get("message") or payload.get("status", {}).get("bCodeMessage") or "No data returned"
        raise ValueError(str(message))
    return data


def _json_url(url: str, *, headers: Mapping[str, str] | None = None) -> Any:
    request = urllib.request.Request(url, headers=dict(headers or NASDAQ_HEADERS))
    with urllib.request.urlopen(request, timeout=30) as response:
        return _read_json_response(response)


def _parse_expiry(text: str) -> date | None:
    text = str(text or "").strip()
    for fmt in ("%B %d, %Y", "%b %d, %Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            pass
    return None


def _occ_symbol(symbol: str, expiry: date, option_type: str, strike: float) -> str:
    root = re.sub(r"[^A-Z0-9]", "", symbol.upper())[:6]
    return f"{root}{expiry:%y%m%d}{option_type[0].upper()}{round(strike * 1000):08d}"


def _option_contract(symbol: str, expiry: date, strike: float, row: Mapping[str, Any],
                     prefix: str, spot: float | None) -> Dict[str, Any] | None:
    last = _num(row.get(f"{prefix}_Last"))
    bid = _num(row.get(f"{prefix}_Bid"))
    ask = _num(row.get(f"{prefix}_Ask"))
    volume = _num(row.get(f"{prefix}_Volume"))
    open_interest = _num(row.get(f"{prefix}_Openinterest"))
    if all(value is None for value in (last, bid, ask, volume, open_interest)):
        return None
    option_type = "call" if prefix == "c" else "put"
    mid = (bid + ask) / 2 if bid is not None and ask is not None else None
    itm = None
    if spot is not None:
        itm = strike < spot if option_type == "call" else strike > spot
    return {
        "contract": _occ_symbol(symbol, expiry, option_type, strike),
        "expiration": expiry.isoformat(),
        "type": option_type,
        "strike": strike,
        "bid": bid,
        "ask": ask,
        "mid": mid,
        "last": last,
        "change": _num(row.get(f"{prefix}_Change")),
        "volume": volume,
        "open_interest": open_interest,
        "dte": max(0, (expiry - date.today()).days),
        "in_the_money": itm,
    }


def parse_option_chain(symbol: str, raw: Mapping[str, Any], *, expiration: str = "",
                       strikes_around_atm: int = 20, page: int = 1,
                       page_size: int = 100) -> Dict[str, Any]:
    last_trade = str(raw.get("lastTrade") or "")
    match = re.search(r"\$([\d,.]+)", last_trade)
    spot = _num(match.group(1)) if match else None
    rows = ((raw.get("table") or {}).get("rows") or [])
    current_expiry: date | None = None
    normalized: list[tuple[date, float, Mapping[str, Any]]] = []
    expirations: list[str] = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        group_expiry = _parse_expiry(str(row.get("expirygroup") or ""))
        if group_expiry:
            current_expiry = group_expiry
            if current_expiry.isoformat() not in expirations:
                expirations.append(current_expiry.isoformat())
        strike = _num(row.get("strike"))
        if current_expiry and strike is not None:
            normalized.append((current_expiry, strike, row))
    if not normalized:
        raise ValueError(f"Nasdaq returned no listed options for {symbol.upper()}")

    selected = expiration if expiration in expirations else expirations[0]
    chosen = [(expiry, strike, row) for expiry, strike, row in normalized if expiry.isoformat() == selected]
    if strikes_around_atm > 0 and spot is not None:
        strikes = sorted({strike for _, strike, _ in chosen})
        nearest = min(range(len(strikes)), key=lambda index: abs(strikes[index] - spot))
        allowed = set(strikes[max(0, nearest - strikes_around_atm):nearest + strikes_around_atm + 1])
        chosen = [entry for entry in chosen if entry[1] in allowed]

    contracts: list[Dict[str, Any]] = []
    for expiry, strike, row in chosen:
        for prefix in ("c", "p"):
            contract = _option_contract(symbol, expiry, strike, row, prefix, spot)
            if contract:
                contracts.append(contract)
    page = max(1, int(page))
    page_size = max(1, min(500, int(page_size)))
    total = len(contracts)
    total_pages = max(1, math.ceil(total / page_size))
    start = (page - 1) * page_size
    page_contracts = contracts[start:start + page_size]
    pagination = {"page": page, "page_size": page_size, "total": total,
                  "total_pages": total_pages, "has_more": page < total_pages}
    return _envelope("options.chain", {
        "symbol": symbol.upper(),
        "resolved_symbol": symbol.upper(),
        "exchange": "NASDAQ",
        "expiration": selected,
        "expirations": expirations,
        "greeks_available": False,
        "underlying": {"symbol": symbol.upper(), "security_type": "Equity", "price": spot,
                       "last_trade_time": last_trade},
        "contracts": page_contracts,
    }, max_age=300, pagination=pagination)


def _month_end(text: str) -> str:
    try:
        parsed = datetime.strptime(str(text), "%b %Y")
        return date(parsed.year, parsed.month, calendar.monthrange(parsed.year, parsed.month)[1]).isoformat()
    except ValueError:
        return ""


def _forecast_rows(forecast: Mapping[str, Any], key: str, suffix: str) -> Iterable[Dict[str, Any]]:
    rows = ((forecast.get(key) or {}).get("rows") or [])
    for index, row in enumerate(rows):
        avg = _num(row.get("consensusEPSForecast"))
        yield {
            "period": f"{'' if index == 0 else '+'}{index}{suffix}",
            "end_date": _month_end(str(row.get("fiscalEnd") or "")),
            "eps": {"avg": avg, "low": _num(row.get("lowEPSForecast")),
                    "high": _num(row.get("highEPSForecast")),
                    "analysts": _num(row.get("noOfEstimates")), "currency": "USD"},
            "revenue": {},
            "eps_trend": {"current": avg},
            "eps_revisions": {"up_last_30_days": _num(row.get("up")),
                              "down_last_30_days": _num(row.get("down"))},
        }


def parse_analyst(symbol: str, info: Mapping[str, Any], ratings: Mapping[str, Any],
                  targets: Mapping[str, Any], forecast: Mapping[str, Any]) -> Dict[str, Any]:
    consensus = targets.get("consensusOverview") or {}
    rating_name = str(ratings.get("meanRatingType") or "").strip()
    rating_key = rating_name.lower().replace(" ", "_")
    rating_mean = {"strong_buy": 1.0, "buy": 2.0, "hold": 3.0,
                   "sell": 4.0, "strong_sell": 5.0}.get(rating_key)
    analyst_match = re.search(r"([\d,]+)\s+analysts?", str(ratings.get("ratingsSummary") or ""), re.I)
    analyst_count = _num(analyst_match.group(1)) if analyst_match else None
    if analyst_count is None:
        analyst_count = sum(value or 0 for value in
                            (_num(consensus.get("buy")), _num(consensus.get("hold")), _num(consensus.get("sell"))))
    primary = info.get("primaryData") or {}
    price_target = _num(consensus.get("priceTarget"))
    price_targets = {
        "current_price": _num(primary.get("lastSalePrice")),
        "low": _num(consensus.get("lowPriceTarget")),
        "mean": price_target,
        "median": price_target,
        "high": _num(consensus.get("highPriceTarget")),
        "analysts": analyst_count,
        "recommendation_mean": rating_mean,
        "recommendation_key": rating_key,
    }
    trend = [{"period": "0m", "strong_buy": 0, "buy": _num(consensus.get("buy")),
              "hold": _num(consensus.get("hold")), "sell": _num(consensus.get("sell")),
              "strong_sell": 0}]
    history = list(targets.get("historicalConsensus") or [])
    history.reverse()
    for index, item in enumerate(history[:11], 1):
        row = item.get("z") or {}
        trend.append({"period": f"-{index}m", "strong_buy": 0, "buy": _num(row.get("buy")),
                      "hold": _num(row.get("hold")), "sell": _num(row.get("sell")),
                      "strong_sell": 0})
    estimates = list(_forecast_rows(forecast, "quarterlyForecast", "q"))
    estimates.extend(_forecast_rows(forecast, "yearlyForecast", "y"))
    return _envelope("analyst.overview", {
        "symbol": symbol.upper(), "resolved_symbol": symbol.upper(),
        "name": info.get("companyName") or symbol.upper(), "currency": "USD",
        "price_targets": price_targets, "recommendation_trend": trend,
        "estimates": estimates, "earnings_history": [], "upgrades_downgrades": [],
        "upgrades_downgrades_total": 0,
    }, max_age=21600)


def parse_earnings_calendar(start: str, end: str, rows: Sequence[Mapping[str, Any]], *,
                            symbol: str = "", sort: str = "date", page: int = 1,
                            page_size: int = 50) -> Dict[str, Any]:
    events = []
    wanted = symbol.strip().upper()
    for row in rows:
        ticker = str(row.get("symbol") or "").strip().upper()
        if not ticker or (wanted and ticker != wanted):
            continue
        timing_text = str(row.get("time") or "").lower()
        if "pre-market" in timing_text:
            timing, hour = "BMO", 13
        elif "after-hours" in timing_text:
            timing, hour = "AMC", 21
        else:
            timing, hour = "TAS", 16
        day = str(row.get("_date") or start)
        events.append({"datetime": f"{day}T{hour:02d}:00:00Z", "symbol": ticker,
                       "company": str(row.get("name") or ""), "event": "Earnings announcement",
                       "timing": timing, "market_cap": _num(row.get("marketCap")),
                       "eps_estimate": _num(row.get("epsForecast"))})
    if sort == "market_cap":
        events.sort(key=lambda item: (-(_num(item.get("market_cap")) or 0), item["datetime"]))
    else:
        events.sort(key=lambda item: (item["datetime"], item["symbol"]))
    page = max(1, int(page))
    page_size = max(1, min(100, int(page_size)))
    total = len(events)
    total_pages = max(1, math.ceil(total / page_size))
    offset = (page - 1) * page_size
    pagination = {"page": page, "page_size": page_size, "total": total,
                  "total_pages": total_pages, "has_more": page < total_pages}
    return _envelope("calendar.earnings", {"start_date": start, "end_date": end,
                                            "events": events[offset:offset + page_size]},
                     max_age=21600, pagination=pagination)


def find_earnings_session(symbol: str, start: str, end: str, *, fetcher=None) -> Dict[str, Any]:
    """Return one exact public-calendar match for ``symbol`` within the date range.

    The small normalized result is suitable for enriching a company profile without
    coupling that caller to the full calendar response.  Empty means that the public
    source did not provide a trustworthy exact match; callers should retain their
    existing date and omit the session label.
    """
    wanted = str(symbol or "").strip().upper()
    if not wanted:
        return {}
    # Validate locally even when a test double is supplied.  Production fetching also
    # enforces the public endpoint's 31-day range limit.
    start_day, end_day = date.fromisoformat(str(start)), date.fromisoformat(str(end))
    if end_day < start_day or (end_day - start_day).days >= 31:
        return {}
    request = {
        "operation": "earnings", "symbol": wanted,
        "start_date": start_day.isoformat(), "end_date": end_day.isoformat(),
        "page": 1, "page_size": 10,
    }
    answer = (fetcher or fetch)(request)
    data = answer.get("data") if isinstance(answer, Mapping) else None
    events = data.get("events") if isinstance(data, Mapping) else None
    if not isinstance(events, list):
        return {}
    for event in events:
        if not isinstance(event, Mapping) or str(event.get("symbol") or "").strip().upper() != wanted:
            continue
        timing = str(event.get("timing") or "").strip().upper()
        event_day = str(event.get("datetime") or "")[:10]
        try:
            parsed_day = date.fromisoformat(event_day)
        except ValueError:
            continue
        if timing not in {"BMO", "AMC", "TAS"} or not start_day <= parsed_day <= end_day:
            continue
        result: Dict[str, Any] = {
            "date": parsed_day.isoformat(), "timing": timing,
            "source": "nasdaq_public", "fincept_credits": 0,
        }
        estimate = _num(event.get("eps_estimate"))
        if estimate is not None:
            result["eps_estimate"] = estimate
        return result
    return {}


def _page(events: Sequence[Dict[str, Any]], page: int, page_size: int) -> tuple[list[Dict[str, Any]], Dict[str, Any]]:
    page = max(1, int(page))
    page_size = max(1, min(100, int(page_size)))
    total = len(events)
    total_pages = max(1, math.ceil(total / page_size))
    offset = (page - 1) * page_size
    return list(events[offset:offset + page_size]), {
        "page": page, "page_size": page_size, "total": total,
        "total_pages": total_pages, "has_more": page < total_pages,
    }


def _price_range(value: Any) -> tuple[float | None, float | None]:
    values = [_num(item) for item in re.findall(r"[-+]?\$?[\d,.]+", str(value or ""))]
    numbers = [item for item in values if item is not None]
    if not numbers:
        return None, None
    return numbers[0], numbers[-1]


def parse_ipo_calendar(start: str, end: str, months: Sequence[Mapping[str, Any]], *,
                       symbol: str = "", page: int = 1, page_size: int = 50) -> Dict[str, Any]:
    wanted = symbol.strip().upper()
    start_day, end_day = date.fromisoformat(start), date.fromisoformat(end)
    events: list[Dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()

    def add(rows: Sequence[Mapping[str, Any]], kind: str, date_key: str) -> None:
        for row in rows:
            ticker = str(row.get("proposedTickerSymbol") or "").strip().upper()
            if wanted and ticker != wanted:
                continue
            day_text = _date_iso(row.get(date_key))
            if not day_text:
                continue
            day = date.fromisoformat(day_text)
            if day < start_day or day > end_day:
                continue
            key = (str(row.get("dealID") or ticker), kind, day_text)
            if key in seen:
                continue
            seen.add(key)
            low, high = _price_range(row.get("proposedSharePrice"))
            priced = str(row.get("dealStatus") or kind).strip() or kind
            event = {
                "date": day_text,
                "symbol": ticker,
                "company": str(row.get("companyName") or "").strip(),
                "exchange": str(row.get("proposedExchange") or "").strip(),
                "currency": "USD",
                "deal_type": priced,
                "filing_date": _date_iso(row.get("filedDate")),
                "amended_date": _date_iso(row.get("amendedDate")),
                "price_from": low,
                "price_to": high,
                "offer_price": low if kind == "Priced" and low == high else None,
                "shares": _num(row.get("sharesOffered")),
            }
            events.append(_clean(event))

    for data in months:
        add(((data.get("priced") or {}).get("rows") or []), "Priced", "pricedDate")
        add((((data.get("upcoming") or {}).get("upcomingTable") or {}).get("rows") or []),
            "Expected", "expectedPriceDate")
        add(((data.get("filed") or {}).get("rows") or []), "Filed", "filedDate")
        add(((data.get("withdrawn") or {}).get("rows") or []), "Withdrawn", "withdrawDate")
    events.sort(key=lambda item: (item["date"], item.get("symbol", ""), item.get("company", "")))
    visible, pagination = _page(events, page, page_size)
    return _envelope("calendar.ipos", {"start_date": start, "end_date": end,
                                        "resolved_symbol": wanted, "events": visible},
                     max_age=21600, pagination=pagination)


def _split_ratio(value: Any) -> tuple[float | None, float | None]:
    text = str(value or "").strip()
    percent = re.fullmatch(r"([\d,.]+)\s*%", text)
    if percent:
        pct = _num(percent.group(1))
        return ((100.0 + pct), 100.0) if pct is not None else (None, None)
    match = re.fullmatch(r"\s*([\d,.]+)\s*:\s*([\d,.]+)\s*", text)
    if not match:
        return None, None
    return _num(match.group(1)), _num(match.group(2))


def parse_split_calendar(start: str, end: str, rows: Sequence[Mapping[str, Any]], *,
                         symbol: str = "", page: int = 1, page_size: int = 50) -> Dict[str, Any]:
    wanted = symbol.strip().upper()
    start_day, end_day = date.fromisoformat(start), date.fromisoformat(end)
    events = []
    seen = set()
    for row in rows:
        ticker = str(row.get("symbol") or "").strip().upper()
        if not ticker or (wanted and ticker != wanted):
            continue
        day_text = _date_iso(row.get("executionDate"))
        if not day_text:
            continue
        day = date.fromisoformat(day_text)
        if day < start_day or day > end_day:
            continue
        key = (ticker, day_text, str(row.get("ratio") or ""))
        if key in seen:
            continue
        seen.add(key)
        new_shares, old_shares = _split_ratio(row.get("ratio"))
        events.append(_clean({"payable_date": day_text, "symbol": ticker,
                              "company": str(row.get("name") or "").strip(),
                              "new_shares": new_shares, "old_shares": old_shares,
                              "optionable": False}))
    events.sort(key=lambda item: (item["payable_date"], item["symbol"]))
    visible, pagination = _page(events, page, page_size)
    return _envelope("calendar.splits", {"start_date": start, "end_date": end,
                                          "resolved_symbol": wanted, "events": visible},
                     max_age=21600, pagination=pagination)


def _economic_number(value: Any) -> float | None:
    text = str(value or "").strip().replace(",", "").replace("%", "")
    match = re.fullmatch(r"([-+]?\d+(?:\.\d+)?)\s*([KMBT]?)", text, re.I)
    if not match:
        return None
    scale = {"": 1.0, "K": 1_000.0, "M": 1_000_000.0,
             "B": 1_000_000_000.0, "T": 1_000_000_000_000.0}[match.group(2).upper()]
    return float(match.group(1)) * scale


def parse_economic_calendar(start: str, end: str, rows: Sequence[Mapping[str, Any]], *,
                            page: int = 1, page_size: int = 50) -> Dict[str, Any]:
    start_day, end_day = date.fromisoformat(start), date.fromisoformat(end)
    events = []
    for row in rows:
        title = str(row.get("title") or "").strip()
        try:
            source_time = datetime.fromisoformat(str(row.get("date") or "").replace("Z", "+00:00"))
        except ValueError:
            continue
        if not title or source_time.date() < start_day or source_time.date() > end_day:
            continue
        if source_time.tzinfo is None:
            source_time = source_time.replace(tzinfo=timezone.utc)
        utc = source_time.astimezone(timezone.utc).replace(microsecond=0)
        events.append(_clean({"datetime": utc.isoformat().replace("+00:00", "Z"),
                              "region": str(row.get("country") or "").strip(),
                              "event": title, "period": str(row.get("period") or "").strip(),
                              "actual": _economic_number(row.get("actual")),
                              "consensus": _economic_number(row.get("forecast")),
                              "prior": _economic_number(row.get("previous")),
                              "revised_from": _economic_number(row.get("revised")),
                              "impact": str(row.get("impact") or "").strip()}))
    events.sort(key=lambda item: (item["datetime"], item.get("region", ""), item["event"]))
    visible, pagination = _page(events, page, page_size)
    return _envelope("calendar.economic", {"start_date": start, "end_date": end, "events": visible},
                     max_age=21600, pagination=pagination,
                     source="forex_factory_public",
                     source_name="Forex Factory public weekly export")


def _table_rows(block: Any) -> Sequence[Mapping[str, Any]]:
    if not isinstance(block, Mapping):
        return []
    table = block.get("table")
    if isinstance(table, Mapping):
        return table.get("rows") or []
    return block.get("rows") or []


def _labeled(rows: Sequence[Mapping[str, Any]], label: str, column: str = "months3") -> float | None:
    for row in rows:
        if str(row.get("insiderTrade") or row.get("positions") or "").lower() == label.lower():
            return _num(row.get(column))
    return None


def parse_ownership(symbol: str, institutional: Mapping[str, Any], insider: Mapping[str, Any],
                    *, transactions_limit: int = 100) -> Dict[str, Any]:
    summary = institutional.get("ownershipSummary") or {}
    institutions_pct = _num((summary.get("SharesOutstandingPCT") or {}).get("value"))
    shares_millions = _num((summary.get("ShareoutstandingTotal") or {}).get("value"))
    shares_outstanding = shares_millions * 1_000_000 if shares_millions is not None else None
    active = (institutional.get("activePositions") or {}).get("rows") or []
    institutions_count = _labeled(active, "Total Institutional Shares", "holders")
    holders = []
    for row in _table_rows(institutional.get("holdingsTransactions")):
        shares = _num(row.get("sharesHeld"))
        pct_held = shares / shares_outstanding * 100 if shares is not None and shares_outstanding else None
        value = _num(row.get("marketValue"))
        holders.append({"holder": row.get("ownerName"), "report_date": _date_iso(row.get("date")),
                        "pct_held": pct_held, "shares": shares,
                        "value": value * 1000 if value is not None else None,
                        "pct_change": _num(row.get("sharesChangePCT"))})

    transaction_rows = list(_table_rows(insider.get("transactionTable")))
    transactions = []
    roster: Dict[str, Dict[str, Any]] = {}
    for row in transaction_rows[:max(0, int(transactions_limit))]:
        shares = _num(row.get("sharesTraded"))
        price = _num(row.get("lastPrice"))
        kind = str(row.get("transactionType") or "")
        transaction = f"{kind} at price {price:.2f} per share." if price is not None else kind
        own_type = str(row.get("ownType") or "").lower()
        name = str(row.get("insider") or "")
        day = _date_iso(row.get("lastDate"))
        held = _num(row.get("sharesHeld"))
        transactions.append({"date": day, "insider": name, "relation": row.get("relation"),
                             "transaction": transaction, "ownership": own_type,
                             "shares": shares, "value": shares * price if shares is not None and price is not None else None})
        if name and name not in roster:
            roster[name] = {"name": name, "relation": row.get("relation"), "latest_transaction": kind,
                            "latest_transaction_date": day,
                            "shares_direct": held if own_type == "direct" else None,
                            "shares_indirect": held if own_type == "indirect" else None}

    trade_rows = (insider.get("numberOfTrades") or {}).get("rows") or []
    share_rows = (insider.get("numberOfSharesTraded") or {}).get("rows") or []
    buy_count = _labeled(trade_rows, "Number of Open Market Buys")
    sell_count = _labeled(trade_rows, "Number of Sells")
    buy_shares = _labeled(share_rows, "Number of Shares Bought")
    sell_shares = _labeled(share_rows, "Number of Shares Sold")
    net_shares = (buy_shares or 0) - (sell_shares or 0) if buy_shares is not None or sell_shares is not None else None
    total_records = _num((insider.get("transactionTable") or {}).get("totalRecords"))
    return _envelope("ownership.overview", {
        "symbol": symbol.upper(), "resolved_symbol": symbol.upper(),
        "breakdown": {"institutions_pct": institutions_pct,
                      "institutions_count": institutions_count},
        "institutional_holders": holders, "fund_holders": [],
        "insider_transactions": transactions,
        "insider_transactions_total": int(total_records or len(transaction_rows)),
        "insider_roster": list(roster.values()),
        "insider_activity": {"period": "3m", "buy_count": buy_count,
                             "buy_shares": buy_shares, "sell_count": sell_count,
                             "sell_shares": sell_shares, "net_count": (buy_count or 0) - (sell_count or 0),
                             "net_shares": net_shares},
    }, max_age=21600)


def _date_iso(value: Any) -> str:
    text = str(value or "").strip()
    for fmt in ("%m/%d/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            pass
    return ""


def _fetch_options(request: Mapping[str, Any]) -> Dict[str, Any]:
    symbol = str(request.get("symbol") or "").strip().upper()
    if not symbol:
        raise ValueError("A symbol is required")
    query = urllib.parse.urlencode({"assetclass": "stocks", "limit": 5000})
    try:
        raw = _nasdaq(f"/api/quote/{urllib.parse.quote(symbol, safe='')}/option-chain?{query}")
    except Exception:
        query = urllib.parse.urlencode({"assetclass": "etf", "limit": 5000})
        raw = _nasdaq(f"/api/quote/{urllib.parse.quote(symbol, safe='')}/option-chain?{query}")
    return parse_option_chain(symbol, raw, expiration=str(request.get("expiration") or ""),
                              strikes_around_atm=int(request.get("strikes_around_atm") or 20),
                              page=int(request.get("page") or 1), page_size=int(request.get("page_size") or 100))


def _fetch_analyst(request: Mapping[str, Any]) -> Dict[str, Any]:
    symbol = str(request.get("symbol") or "").strip().upper()
    if not symbol:
        raise ValueError("A symbol is required")
    quoted = urllib.parse.quote(symbol, safe="")
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(_nasdaq, path) for path in (
            f"/api/quote/{quoted}/info?assetclass=stocks", f"/api/analyst/{quoted}/ratings",
            f"/api/analyst/{quoted}/targetprice", f"/api/analyst/{quoted}/earnings-forecast")]
        info, ratings, targets, forecast = [future.result() for future in futures]
    return parse_analyst(symbol, info, ratings, targets, forecast)


def _fetch_ownership(request: Mapping[str, Any]) -> Dict[str, Any]:
    symbol = str(request.get("symbol") or "").strip().upper()
    if not symbol:
        raise ValueError("A symbol is required")
    quoted = urllib.parse.quote(symbol, safe="")
    with ThreadPoolExecutor(max_workers=2) as pool:
        institutional = pool.submit(_nasdaq, f"/api/company/{quoted}/institutional-holdings")
        insider = pool.submit(_nasdaq, f"/api/company/{quoted}/insider-trades")
        return parse_ownership(symbol, institutional.result(), insider.result(),
                               transactions_limit=int(request.get("transactions_limit") or 100))


def _fetch_earnings(request: Mapping[str, Any]) -> Dict[str, Any]:
    start = date.fromisoformat(str(request.get("start_date") or date.today().isoformat()))
    end = date.fromisoformat(str(request.get("end_date") or (start + timedelta(days=7)).isoformat()))
    if end < start:
        raise ValueError("End date must not be before start date")
    days = (end - start).days + 1
    if days > 31:
        raise ValueError("The public Nasdaq earnings calendar supports ranges up to 31 days")

    def fetch_day(day: date) -> list[Dict[str, Any]]:
        query = urllib.parse.urlencode({"date": day.isoformat()})
        data = _nasdaq(f"/api/calendar/earnings?{query}")
        rows = []
        for source in data.get("rows") or []:
            row = dict(source)
            row["_date"] = day.isoformat()
            rows.append(row)
        return rows

    dates = [start + timedelta(days=offset) for offset in range(days)]
    rows: list[Dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=min(8, len(dates))) as pool:
        for result in pool.map(fetch_day, dates):
            rows.extend(result)
    return parse_earnings_calendar(start.isoformat(), end.isoformat(), rows,
                                   symbol=str(request.get("symbol") or ""),
                                   sort=str(request.get("sort") or "date"),
                                   page=int(request.get("page") or 1),
                                   page_size=int(request.get("page_size") or 50))


def _month_keys(start: date, end: date) -> list[str]:
    keys = []
    cursor = date(start.year, start.month, 1)
    last = date(end.year, end.month, 1)
    while cursor <= last:
        keys.append(cursor.strftime("%Y-%m"))
        cursor = date(cursor.year + (cursor.month == 12), 1 if cursor.month == 12 else cursor.month + 1, 1)
    return keys


def _fetch_ipos(request: Mapping[str, Any]) -> Dict[str, Any]:
    start = date.fromisoformat(str(request.get("start_date") or date.today().isoformat()))
    end = date.fromisoformat(str(request.get("end_date") or (start + timedelta(days=30)).isoformat()))
    months = _month_keys(start, end)
    with ThreadPoolExecutor(max_workers=min(6, len(months))) as pool:
        payloads = list(pool.map(lambda month: _nasdaq("/api/ipo/calendar?" +
                                                       urllib.parse.urlencode({"date": month})), months))
    return parse_ipo_calendar(start.isoformat(), end.isoformat(), payloads,
                              symbol=str(request.get("symbol") or ""),
                              page=int(request.get("page") or 1),
                              page_size=int(request.get("page_size") or 50))


def _fetch_splits(request: Mapping[str, Any]) -> Dict[str, Any]:
    start = date.fromisoformat(str(request.get("start_date") or date.today().isoformat()))
    end = date.fromisoformat(str(request.get("end_date") or (start + timedelta(days=30)).isoformat()))
    query = urllib.parse.urlencode({"date": start.isoformat()})
    data = _nasdaq("/api/calendar/splits?" + query)
    return parse_split_calendar(start.isoformat(), end.isoformat(), data.get("rows") or [],
                                symbol=str(request.get("symbol") or ""),
                                page=int(request.get("page") or 1),
                                page_size=int(request.get("page_size") or 50))


def _fetch_economic(request: Mapping[str, Any]) -> Dict[str, Any]:
    start = str(request.get("start_date") or date.today().isoformat())
    end = str(request.get("end_date") or (date.fromisoformat(start) + timedelta(days=7)).isoformat())
    rows = _json_url("https://nfs.faireconomy.media/ff_calendar_thisweek.json",
                     headers={"User-Agent": NASDAQ_HEADERS["User-Agent"], "Accept": "application/json"})
    if not isinstance(rows, list):
        raise ValueError("Public economic calendar returned an invalid response")
    return parse_economic_calendar(start, end, rows,
                                   page=int(request.get("page") or 1),
                                   page_size=int(request.get("page_size") or 50))


def fetch(request: Mapping[str, Any]) -> Dict[str, Any]:
    operation = str(request.get("operation") or "").lower()
    if operation == "options":
        return _fetch_options(request)
    if operation == "analyst":
        return _fetch_analyst(request)
    if operation == "ownership":
        return _fetch_ownership(request)
    if operation == "earnings":
        return _fetch_earnings(request)
    if operation == "ipos":
        return _fetch_ipos(request)
    if operation == "splits":
        return _fetch_splits(request)
    if operation == "economic":
        return _fetch_economic(request)
    raise ValueError(f"Unsupported public equity operation: {operation or '(empty)'}")


def main(argv: Sequence[str]) -> int:
    try:
        if len(argv) != 3 or argv[1] != "fetch":
            raise ValueError("Usage: byo_equity_data.py fetch '<json request>'")
        request = json.loads(argv[2])
        print(json.dumps(fetch(request), separators=(",", ":"), allow_nan=False))
        return 0
    except Exception as exc:
        print(json.dumps({"error": str(exc), "error_code": "public_equity_unavailable"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
