#!/usr/bin/env python3
"""Public dashboard datasets with no Fincept request or third-party package.

Usage: byo_dashboard_data.py fetch '{"dataset":"rates.treasury","query":{...}}'
"""

from __future__ import annotations

import csv
import io
import json
import re
import sys
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Dict, Mapping, Sequence


PUBLIC_AGENT = "FinceptTerminalCommunity/4.5 public-dashboard-adapter"
NASDAQ_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Origin": "https://www.nasdaq.com",
    "Referer": "https://www.nasdaq.com/",
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 Chrome/124 Safari/537.36",
}


def _get(url: str, *, headers: Mapping[str, str] | None = None, as_json: bool = False):
    request = urllib.request.Request(url, headers=dict(headers or {"User-Agent": PUBLIC_AGENT}))
    with urllib.request.urlopen(request, timeout=30) as response:
        text = response.read().decode("utf-8", errors="replace")
    return json.loads(text) if as_json else text


def _num(value: Any) -> float | None:
    text = str(value or "").strip().replace(",", "").replace("$", "").replace("%", "")
    if not text or text in {"--", "—", "N/A"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _envelope(dataset: str, source: str, data: Dict[str, Any], *, max_age: int) -> Dict[str, Any]:
    return {
        "data": data,
        "meta": {
            "dataset": dataset,
            "source": source.lower().replace(" ", "_"),
            "source_name": source,
            "as_of": datetime.now(timezone.utc).isoformat(),
            "cached": False,
            "age_seconds": 0,
            "max_age_seconds": max_age,
            "stale": False,
            "cost_route": "public_zero_credit",
            "fincept_credits": 0,
        },
    }


_TREASURY_FIELDS = {
    "1 Mo": "month_1", "3 Mo": "month_3", "6 Mo": "month_6", "1 Yr": "year_1",
    "2 Yr": "year_2", "3 Yr": "year_3", "5 Yr": "year_5", "7 Yr": "year_7",
    "10 Yr": "year_10", "20 Yr": "year_20", "30 Yr": "year_30",
}


def parse_treasury_csv(text: str) -> Dict[str, Any]:
    rows = []
    for source in csv.DictReader(io.StringIO(text)):
        try:
            day = datetime.strptime(source.get("Date", ""), "%m/%d/%Y").date().isoformat()
        except ValueError:
            continue
        row: Dict[str, Any] = {"date": day}
        for original, normalized in _TREASURY_FIELDS.items():
            value = _num(source.get(original))
            if value is not None:
                row[normalized] = value
        if len(row) >= 6:
            rows.append(row)
    rows.sort(key=lambda item: item["date"])
    return _envelope("rates.treasury", "U.S. Treasury", {"rows": rows}, max_age=86400)


def parse_ecb_curve(series: Mapping[float, Sequence[Mapping[str, Any]]]) -> Dict[str, Any]:
    by_maturity: Dict[float, Dict[str, float]] = {}
    for maturity, observations in series.items():
        points = {}
        for row in observations:
            day = str(row.get("PERIOD") or "")[:10]
            value = _num(row.get("OBS_VALUE_AS_IS"))
            if len(day) == 10 and value is not None:
                points[day] = value
        if points:
            by_maturity[float(maturity)] = points
    if not by_maturity:
        raise ValueError("ECB returned no AAA yield-curve observations")
    as_of = min(max(points) for points in by_maturity.values())
    curve = []
    for maturity, points in sorted(by_maturity.items()):
        eligible = [day for day in points if day <= as_of]
        if eligible:
            curve.append({"years": maturity, "rate": points[max(eligible)]})
    if len(curve) < 2:
        raise ValueError("ECB returned too few AAA yield-curve maturities")
    return _envelope("rates.euro_curve", "European Central Bank AAA spot curve",
                     {"date": as_of, "points": curve}, max_age=86400)


def parse_vix_settlements(text: str, settlement_date: str) -> Dict[str, Any]:
    base_day = date.fromisoformat(settlement_date)
    contracts = []
    for row in csv.DictReader(io.StringIO(text)):
        if str(row.get("Product") or "").strip().upper() != "VX":
            continue
        symbol = str(row.get("Symbol") or "").strip().upper()
        expiry_text = str(row.get("Expiration Date") or "").strip()
        price = _num(row.get("Price"))
        try:
            expiry = date.fromisoformat(expiry_text)
        except ValueError:
            continue
        if not symbol or price is None or expiry < base_day:
            continue
        contracts.append({"symbol": symbol, "expiration": expiry.isoformat(),
                          "days_to_expiration": (expiry - base_day).days,
                          "settlement": price, "weekly": not symbol.startswith("VX/")})
    contracts.sort(key=lambda item: item["expiration"])
    monthly = [item for item in contracts if not item["weekly"]]
    spread = None
    structure = "unknown"
    if len(monthly) >= 2 and monthly[0]["settlement"]:
        spread = (monthly[1]["settlement"] / monthly[0]["settlement"] - 1.0) * 100.0
        structure = "contango" if spread > 0.05 else "backwardation" if spread < -0.05 else "flat"
    if not contracts:
        raise ValueError("Cboe returned no VIX futures settlements")
    return _envelope("volatility.vix_futures", "Cboe Futures Exchange settlements",
                     {"contracts": contracts, "settlement_date": settlement_date,
                      "front_to_second_month_pct": spread, "structure": structure}, max_age=86400)


def crypto_answer(global_data: Mapping[str, Any], tickers: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    coins = [{"name": str(row.get("name", "")), "symbol": str(row.get("symbol", "")).upper(),
              "market_cap_rank": row.get("rank")}
             for row in tickers if row.get("name") and row.get("symbol")]
    total_market_cap = _num(global_data.get("market_cap_usd"))
    dominance = {"btc": _num(global_data.get("bitcoin_dominance_percentage"))}
    if total_market_cap and total_market_cap > 0:
        for row in tickers:
            if str(row.get("symbol") or "").upper() != "ETH":
                continue
            eth_cap = _num((((row.get("quotes") or {}).get("USD") or {}).get("market_cap")))
            if eth_cap is not None:
                dominance["eth"] = eth_cap / total_market_cap * 100.0
            break
    data = {
        "total_market_cap_usd": total_market_cap,
        "total_volume_usd": _num(global_data.get("volume_24h_usd")),
        "market_cap_change_percentage_24h_usd": _num(global_data.get("market_cap_change_24h")),
        "market_cap_percentage": dominance,
        "coins": coins,
    }
    return _envelope("crypto.global", "CoinPaprika", data, max_age=300)


def _timing(value: str) -> tuple[str, int]:
    value = value.lower()
    if "pre-market" in value:
        return "BMO", 13
    if "after-hours" in value:
        return "AMC", 21
    return "TAS", 16


def parse_earnings(day: str, payload: Mapping[str, Any]) -> Dict[str, Any]:
    events = []
    for row in ((payload.get("data") or {}).get("rows") or []):
        symbol = str(row.get("symbol") or "").strip().upper()
        if not symbol:
            continue
        timing, hour = _timing(str(row.get("time") or ""))
        events.append({
            "datetime": f"{day}T{hour:02d}:00:00Z",
            "symbol": symbol,
            "company": str(row.get("name") or ""),
            "event": "Earnings announcement",
            "timing": timing,
            "market_cap": _num(row.get("marketCap")),
            "eps_estimate": _num(row.get("epsForecast")),
        })
    return _envelope("calendar.earnings", "Nasdaq earnings calendar",
                     {"start_date": day, "end_date": day, "events": events}, max_age=21600)


def _treasury() -> Dict[str, Any]:
    year = date.today().year
    url = (f"https://home.treasury.gov/resource-center/data-chart-center/interest-rates/"
           f"daily-treasury-rates.csv/{year}/all?" +
           urllib.parse.urlencode({"type": "daily_treasury_yield_curve",
                                   "field_tdr_date_value": year, "_format": "csv"}))
    answer = parse_treasury_csv(_get(url))
    if not answer["data"]["rows"]:
        raise ValueError("U.S. Treasury returned no yield-curve rows")
    return answer


_ECB_MATURITIES = {
    0.25: "3M", 0.5: "6M", 1.0: "1Y", 2.0: "2Y", 3.0: "3Y", 5.0: "5Y",
    7.0: "7Y", 10.0: "10Y", 15.0: "15Y", 20.0: "20Y", 30.0: "30Y",
}


def _euro_curve() -> Dict[str, Any]:
    def load(item):
        years, code = item
        series_id = f"YC.B.U2.EUR.4F.G_N_A.SV_C_YM.SR_{code}"
        url = "https://data.ecb.europa.eu/data-detail-api/" + urllib.parse.quote(series_id)
        return years, _get(url, headers={"User-Agent": PUBLIC_AGENT, "Accept": "application/json"}, as_json=True)

    series = {}
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = [pool.submit(load, item) for item in _ECB_MATURITIES.items()]
        for future in as_completed(futures):
            try:
                years, rows = future.result()
                if isinstance(rows, list):
                    series[years] = rows
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                continue
    return parse_ecb_curve(series)


def _vix_curve() -> Dict[str, Any]:
    cursor = date.today()
    last_error = None
    for _ in range(8):
        if cursor.weekday() < 5:
            try:
                url = ("https://www.cboe.com/us/futures/market_statistics/settlement/csv?" +
                       urllib.parse.urlencode({"dt": cursor.isoformat()}))
                return parse_vix_settlements(_get(url, headers={"User-Agent": "Mozilla/5.0"}),
                                             cursor.isoformat())
            except (OSError, ValueError, TypeError) as exc:
                last_error = exc
        cursor -= timedelta(days=1)
    raise ValueError(f"No recent Cboe VIX settlement curve: {last_error or 'no data'}")


def _crypto(dataset: str) -> Dict[str, Any]:
    if dataset == "crypto.global":
        global_data = _get("https://api.coinpaprika.com/v1/global", as_json=True)
        tickers = _get("https://api.coinpaprika.com/v1/tickers?limit=10", as_json=True)
        answer = crypto_answer(global_data, tickers)
        answer["meta"]["dataset"] = dataset
        return answer
    tickers = _get("https://api.coinpaprika.com/v1/tickers?limit=10", as_json=True)
    answer = crypto_answer({}, tickers)
    answer["meta"]["dataset"] = dataset
    return answer


def _earnings(query: Mapping[str, Any]) -> Dict[str, Any]:
    start = date.fromisoformat(str(query.get("start_date") or date.today().isoformat()))
    end = date.fromisoformat(str(query.get("end_date") or (start + timedelta(days=14)).isoformat()))
    events = []
    cursor = start
    while cursor <= end:
        day = cursor.isoformat()
        url = "https://api.nasdaq.com/api/calendar/earnings?" + urllib.parse.urlencode({"date": day})
        one = parse_earnings(day, _get(url, headers=NASDAQ_HEADERS, as_json=True))
        events.extend(one["data"]["events"])
        cursor += timedelta(days=1)
    events.sort(key=lambda item: (-(_num(item.get("market_cap")) or 0), item["datetime"]))
    answer = _envelope("calendar.earnings", "Nasdaq earnings calendar",
                       {"start_date": start.isoformat(), "end_date": end.isoformat(), "events": events},
                       max_age=21600)
    return answer


def fetch(payload: Mapping[str, Any]) -> Dict[str, Any]:
    dataset = str(payload.get("dataset") or "")
    query = payload.get("query") if isinstance(payload.get("query"), Mapping) else {}
    if dataset == "rates.treasury":
        return _treasury()
    if dataset == "rates.euro_curve":
        return _euro_curve()
    if dataset == "volatility.vix_futures":
        return _vix_curve()
    if dataset in {"crypto.global", "crypto.trending"}:
        return _crypto(dataset)
    if dataset == "calendar.earnings":
        return _earnings(query)
    raise ValueError(f"No public dashboard route for {dataset}")


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    try:
        if len(args) != 2 or args[0] != "fetch":
            raise ValueError("usage: byo_dashboard_data.py fetch <json>")
        print(json.dumps(fetch(json.loads(args[1])), allow_nan=False, separators=(",", ":")))
        return 0
    except Exception as exc:
        print(json.dumps({"error": str(exc), "cost_route": "public_zero_credit", "fincept_credits": 0},
                         separators=(",", ":")))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
