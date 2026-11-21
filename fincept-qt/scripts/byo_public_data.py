#!/usr/bin/env python3
"""Public, zero-Fincept-credit data adapter used by community routes.

Every request goes directly to the named public publisher.  The adapter has no
Fincept endpoint, credential access, paid fallback, or model dependency.
"""

from __future__ import annotations

import csv
import io
import json
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from typing import Any, Dict, Mapping


USER_AGENT = "FinceptTerminalCommunity/4.5 public-data-adapter"
GEOPOLITICS_RSS_FALLBACK = "https://feeds.bbci.co.uk/news/world/rss.xml"


def _sources():
    rows = [
        ("fred", "FRED", "macro", "Federal Reserve graph CSV; no account required"),
        ("world_bank", "World Bank Open Data", "macro", "Official development indicators"),
        ("imf", "IMF DataMapper", "macro", "World Economic Outlook indicators and forecasts"),
        ("gdelt", "GDELT DOC 2.0", "geopolitics", "Global news-event discovery"),
        ("rss", "RSS / Atom", "news", "Publisher feeds configured in News"),
        ("yahoo", "Yahoo Finance", "market", "Delayed quotes and history"),
        ("stooq", "Stooq", "market", "CSV history fallback"),
    ]
    return [{"id": sid, "name": name, "category": category, "note": note,
             "cost_route": "public_zero_credit", "fincept_credits": 0}
            for sid, name, category, note in rows]


def catalog() -> Dict[str, Any]:
    return {"source": "community", "cost_route": "public_zero_credit", "fincept_credits": 0,
            "sources": _sources()}


def _float(value):
    if value in (None, "", ".", "null"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_fred_csv(text: str, series: str) -> Dict[str, Any]:
    rows = []
    for row in csv.DictReader(io.StringIO(text)):
        value = _float(row.get(series) if series in row else next(iter(row.values()), None))
        date = row.get("DATE") or row.get("observation_date")
        if date and value is not None:
            rows.append({"date": date, "value": value})
    return {"source": "FRED", "series": series, "rows": rows,
            "cost_route": "public_zero_credit", "fincept_credits": 0}


def parse_world_bank(payload: Any, indicator: str) -> Dict[str, Any]:
    records = payload[1] if isinstance(payload, list) and len(payload) > 1 and isinstance(payload[1], list) else []
    rows = []
    for row in records:
        value = _float(row.get("value"))
        if value is not None:
            rows.append({"date": str(row.get("date", "")), "value": value,
                         "country": row.get("countryiso3code") or row.get("country", {}).get("id", "")})
    return {"source": "World Bank Open Data", "indicator": indicator, "rows": rows,
            "cost_route": "public_zero_credit", "fincept_credits": 0}


def parse_imf(payload: Mapping[str, Any], indicator: str, country: str) -> Dict[str, Any]:
    values = (((payload.get("values") or {}).get(indicator) or {}).get(country) or {})
    rows = [{"date": str(period), "value": value}
            for period, raw in sorted(values.items()) if (value := _float(raw)) is not None]
    if not rows:
        documents = ((payload.get("series") or {}).get("docs") or [])
        if documents:
            document = documents[0]
            rows = [{"date": str(period), "value": value}
                    for period, raw in zip(document.get("period") or [], document.get("value") or [])
                    if (value := _float(raw)) is not None]
    return {"source": "IMF WEO via DBnomics", "indicator": indicator, "country": country, "rows": rows,
            "cost_route": "public_zero_credit", "fincept_credits": 0}


def parse_gdelt(payload: Mapping[str, Any]) -> Dict[str, Any]:
    rows = []
    for item in payload.get("articles", []):
        if not item.get("title") or not item.get("url"):
            continue
        rows.append({"title": item["title"], "url": item["url"], "seen_at": item.get("seendate", ""),
                     "domain": item.get("domain", ""), "language": item.get("language", "")})
    return {"source": "GDELT DOC 2.0", "rows": rows, "cost_route": "public_zero_credit",
            "fincept_credits": 0}


def parse_stooq_csv(text: str, symbol: str) -> Dict[str, Any]:
    rows = []
    for row in csv.DictReader(io.StringIO(text)):
        close = _float(row.get("Close"))
        if close is None:
            continue
        rows.append({"date": row.get("Date", ""), "open": _float(row.get("Open")),
                     "high": _float(row.get("High")), "low": _float(row.get("Low")), "close": close,
                     "volume": _float(row.get("Volume"))})
    return {"source": "Stooq", "symbol": symbol.upper(), "rows": rows,
            "cost_route": "public_zero_credit", "fincept_credits": 0}


def parse_yahoo(payload: Mapping[str, Any], symbol: str) -> Dict[str, Any]:
    result = ((payload.get("chart") or {}).get("result") or [{}])[0]
    times = result.get("timestamp") or []
    quote = (((result.get("indicators") or {}).get("quote") or [{}])[0])
    closes = quote.get("close") or []
    rows = [{"timestamp": ts, "close": _float(closes[index])}
            for index, ts in enumerate(times) if index < len(closes) and _float(closes[index]) is not None]
    return {"source": "Yahoo Finance", "symbol": symbol.upper(), "rows": rows,
            "cost_route": "public_zero_credit", "fincept_credits": 0}


def parse_feed(text: str) -> Dict[str, Any]:
    root = ET.fromstring(text)
    rows = []
    for node in list(root.iter("item")) + list(root.iter("{http://www.w3.org/2005/Atom}entry")):
        def value(name):
            plain = node.find(name)
            atom = node.find("{http://www.w3.org/2005/Atom}" + name)
            target = plain if plain is not None else atom
            return (target.text or "").strip() if target is not None else ""
        link = value("link")
        if not link:
            atom_link = node.find("{http://www.w3.org/2005/Atom}link")
            link = atom_link.get("href", "") if atom_link is not None else ""
        if value("title") and link:
            rows.append({"title": value("title"), "url": link,
                         "published": value("pubDate") or value("updated"),
                         "domain": urllib.parse.urlparse(link).netloc})
    return {"source": "RSS / Atom", "rows": rows, "cost_route": "public_zero_credit",
            "fincept_credits": 0}


def _get(url: str, *, json_response: bool = False):
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json,text/csv,*/*"})
    with urllib.request.urlopen(request, timeout=30) as response:
        raw = response.read().decode("utf-8", errors="replace")
    return json.loads(raw) if json_response else raw


def fetch(command: str, args: Mapping[str, Any]) -> Dict[str, Any]:
    if command == "fred":
        series = str(args.get("series", "GDP")).strip().upper()
        url = "https://fred.stlouisfed.org/graph/fredgraph.csv?" + urllib.parse.urlencode({"id": series})
        return parse_fred_csv(_get(url), series)
    if command == "world_bank":
        country = str(args.get("country", "all")).strip()
        indicator = str(args.get("indicator", "NY.GDP.MKTP.KD.ZG")).strip()
        url = f"https://api.worldbank.org/v2/country/{urllib.parse.quote(country)}/indicator/{urllib.parse.quote(indicator)}?format=json&per_page=200"
        return parse_world_bank(_get(url, json_response=True), indicator)
    if command == "imf":
        indicator = str(args.get("indicator", "NGDP_RPCH")).strip().upper()
        country = str(args.get("country", "IND")).strip().upper()
        series = urllib.parse.quote(f"{country}.{indicator}")
        url = f"https://api.db.nomics.world/v22/series/IMF/WEO:latest/{series}?observations=1"
        return parse_imf(_get(url, json_response=True), indicator, country)
    if command == "gdelt":
        query = str(args.get("query", "economy OR markets")).strip()
        params = {"query": query, "mode": "artlist", "maxrecords": min(int(args.get("limit", 50)), 250),
                  "format": "json", "sort": "datedesc"}
        try:
            result = parse_gdelt(_get("https://api.gdeltproject.org/api/v2/doc/doc?" +
                                      urllib.parse.urlencode(params), json_response=True))
            if result["rows"]:
                return result
        except (OSError, ValueError, json.JSONDecodeError):
            pass
        fallback_url = str(args.get("rss_fallback_url", GEOPOLITICS_RSS_FALLBACK)).strip()
        result = parse_feed(_get(fallback_url))
        result["source"] = "RSS / Atom fallback"
        result["fallback_from"] = "GDELT DOC 2.0"
        return result
    if command == "stooq":
        symbol = str(args.get("symbol", "aapl.us")).strip().lower()
        result = parse_stooq_csv(
            _get("https://stooq.com/q/d/l/?" + urllib.parse.urlencode({"s": symbol, "i": "d"})), symbol)
        if result["rows"]:
            return result
        yahoo_symbol = symbol[:-3] if symbol.endswith(".us") else symbol
        params = {"range": str(args.get("range", "1y")), "interval": str(args.get("interval", "1d"))}
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(yahoo_symbol.upper())}?" + urllib.parse.urlencode(params)
        return parse_yahoo(_get(url, json_response=True), yahoo_symbol)
    if command == "yahoo":
        symbol = str(args.get("symbol", "SPY")).strip().upper()
        params = {"range": str(args.get("range", "1y")), "interval": str(args.get("interval", "1d"))}
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(symbol)}?" + urllib.parse.urlencode(params)
        return parse_yahoo(_get(url, json_response=True), symbol)
    if command == "rss":
        return parse_feed(_get(str(args.get("url", "")).strip()))
    raise ValueError(f"unknown public-data command: {command}")


def main(argv):
    command = argv[1] if len(argv) > 1 else "catalog"
    args = json.loads(argv[2]) if len(argv) > 2 else {}
    answer = catalog() if command == "catalog" else fetch(command, args)
    print(json.dumps(answer, separators=(",", ":"), allow_nan=False))


if __name__ == "__main__":
    try:
        main(sys.argv)
    except Exception as exc:
        print(json.dumps({"error": str(exc), "cost_route": "public_zero_credit", "fincept_credits": 0}))
        raise SystemExit(1)
