"""Source-attributed revenue segments from an SEC inline-XBRL annual filing.

This extractor intentionally accepts only a narrow, auditable case:

* annual revenue facts from the filing's inline XBRL;
* contexts with exactly one explicit dimension;
* at least two distinct members of the same axis; and
* member values whose sum reconciles to a company-reported consolidated revenue
  value within 0.1% (to allow filing-level rounding).

Nested product hierarchies, multi-dimensional facts, non-reconciling values and
missing filings are left unavailable.  No LLM fills or labels missing numbers.

CLI::

    python sec_segment_revenue.py segments GOOG

The network path uses SEC's declared-identity / <=8 requests-per-second policy
from ``sec_company_facts.py`` and caches the extracted answer for 12 hours.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from html.parser import HTMLParser


_SCRIPTS_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if _SCRIPTS_ROOT not in sys.path:
    sys.path.insert(0, _SCRIPTS_ROOT)

import fincept_json  # noqa: E402


ANNUAL_FORMS = {"10-K", "20-F", "40-F"}
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
ARCHIVE_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{accession}/{document}"
REVENUE_CONCEPTS = (
    "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax",
    "us-gaap:Revenues",
    "us-gaap:SalesRevenueNet",
    "us-gaap:RevenueFromContractWithCustomerIncludingAssessedTax",
    "us-gaap:RevenuesNetOfInterestExpense",
    "ifrs-full:Revenue",
)
_REVENUE_KEYS = {concept.lower() for concept in REVENUE_CONCEPTS}
_CONCEPT_PRIORITY = {concept.lower(): i for i, concept in enumerate(REVENUE_CONCEPTS)}
_CACHE_TTL_S = 12 * 3600
_MAX_PERIODS = 3


def _attrs(items):
    return {str(k).lower(): v for k, v in items}


def _number(text: str, attrs: dict):
    raw = re.sub(r"\s+", "", (text or "").replace("\xa0", ""))
    if not raw or raw in {"-", "—", "–"}:
        return None
    negative_parentheses = raw.startswith("(") and raw.endswith(")")
    raw = raw.strip("()")
    transform = str(attrs.get("format", "")).lower()
    if "num-comma-decimal" in transform:
        raw = raw.replace(".", "").replace(",", ".")
    else:
        raw = raw.replace(",", "")
    raw = re.sub(r"[^0-9.+\-eE]", "", raw)
    try:
        value = float(raw)
        value *= 10.0 ** int(attrs.get("scale") or 0)
    except (TypeError, ValueError, OverflowError):
        return None
    if negative_parentheses or attrs.get("sign") == "-":
        value = -abs(value)
    return value


class _InlineXbrlParser(HTMLParser):
    """Small streaming reader for contexts and numeric facts; no DOM dependency."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.contexts = {}
        self.facts = []
        self._context = None
        self._capture = None
        self._capture_text = []
        self._member_dimension = ""
        self._fact = None
        self._fact_text = []

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        a = _attrs(attrs)
        if tag.endswith(":context"):
            self._context = {"id": a.get("id", ""), "start": "", "end": "", "members": []}
        elif self._context is not None and tag.endswith(":explicitmember"):
            self._capture = "member"
            self._member_dimension = a.get("dimension", "")
            self._capture_text = []
        elif self._context is not None and tag.endswith(":startdate"):
            self._capture = "start"
            self._capture_text = []
        elif self._context is not None and tag.endswith(":enddate"):
            self._capture = "end"
            self._capture_text = []

        if tag == "ix:nonfraction":
            self._fact = a
            self._fact_text = []

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_data(self, data):
        if self._capture is not None:
            self._capture_text.append(data)
        if self._fact is not None:
            self._fact_text.append(data)

    def handle_endtag(self, tag):
        tag = tag.lower()
        if self._context is not None and self._capture is not None:
            value = "".join(self._capture_text).strip()
            if self._capture == "member" and tag.endswith(":explicitmember"):
                if self._member_dimension and value:
                    self._context["members"].append((self._member_dimension, value))
                self._capture = None
            elif self._capture == "start" and tag.endswith(":startdate"):
                self._context["start"] = value
                self._capture = None
            elif self._capture == "end" and tag.endswith(":enddate"):
                self._context["end"] = value
                self._capture = None

        if self._context is not None and tag.endswith(":context"):
            if self._context["id"]:
                self.contexts[self._context["id"]] = self._context
            self._context = None
            self._capture = None

        if self._fact is not None and tag == "ix:nonfraction":
            concept = str(self._fact.get("name", ""))
            if concept.lower() in _REVENUE_KEYS:
                value = _number("".join(self._fact_text), self._fact)
                if value is not None:
                    self.facts.append({
                        "concept": concept,
                        "context": self._fact.get("contextref", ""),
                        "unit": self._fact.get("unitref", ""),
                        "value": value,
                    })
            self._fact = None
            self._fact_text = []


def _annual_period(context):
    try:
        start = _dt.date.fromisoformat(context.get("start", ""))
        end = _dt.date.fromisoformat(context.get("end", ""))
    except ValueError:
        return None
    days = (end - start).days
    return (start.isoformat(), end.isoformat()) if 330 <= days <= 380 else None


def _friendly(qname: str, suffix: str):
    local = (qname or "").split(":")[-1]
    if suffix and local.lower().endswith(suffix.lower()):
        local = local[: -len(suffix)]
    local = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", local)
    local = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", local)
    return re.sub(r"\s+", " ", local.replace("_", " ")).strip()


def _dedupe_members(rows):
    values = {}
    order = []
    for row in rows:
        member = row["member"]
        if member not in values:
            values[member] = row["value"]
            order.append(member)
        elif abs(values[member] - row["value"]) > max(abs(row["value"]) * 1e-9, 1e-6):
            return None
    return [(member, values[member]) for member in order]


def extract_revenue_segments(html: str):
    """Parse an inline-XBRL document into reconciled annual revenue breakdowns."""
    parser = _InlineXbrlParser()
    parser.feed(html or "")
    parser.close()

    totals = {}
    segments = {}
    for fact in parser.facts:
        context = parser.contexts.get(fact["context"])
        if not context:
            continue
        period = _annual_period(context)
        if not period:
            continue
        key = (period[0], period[1], fact["unit"], fact["concept"].lower())
        members = context.get("members") or []
        if not members:
            totals[key] = fact
        elif len(members) == 1:
            axis, member = members[0]
            segments.setdefault(key, {}).setdefault(axis, []).append({
                "member": member,
                "value": fact["value"],
            })

    period_keys = sorted({(key[0], key[1], key[2]) for key in segments}, key=lambda k: k[1], reverse=True)
    periods = []
    for start, end, unit in period_keys:
        period_totals = {
            key[3]: fact for key, fact in totals.items()
            if key[:3] == (start, end, unit)
        }
        concept_keys = sorted(
            {key[3] for key in segments if key[:3] == (start, end, unit)},
            key=lambda c: _CONCEPT_PRIORITY.get(c, 999),
        )
        selected = None
        for concept_key in concept_keys:
            target = period_totals.get(concept_key)
            if target is None and period_totals:
                target = min(period_totals.values(), key=lambda row: _CONCEPT_PRIORITY.get(row["concept"].lower(), 999))
            if target is None or target["value"] <= 0:
                continue
            total_value = target["value"]
            reconciled = []
            axis_rows = segments.get((start, end, unit, concept_key), {})
            for axis, rows in axis_rows.items():
                members = _dedupe_members(rows)
                if not members or len(members) < 2 or any(value < 0 for _, value in members):
                    continue
                reported_sum = sum(value for _, value in members)
                tolerance = max(abs(total_value) * 0.001, 1.0)
                delta = reported_sum - total_value
                if abs(delta) > tolerance:
                    continue
                reconciled.append({
                    "axis": axis,
                    "label": _friendly(axis, "Axis"),
                    "reported_sum": reported_sum,
                    "reconciliation_delta": delta,
                    "members": [{
                        "member": member,
                        "label": _friendly(member, "Member"),
                        "value": value,
                        "share": value / reported_sum if reported_sum else None,
                    } for member, value in members],
                })
            if reconciled:
                selected = {
                    "start": start,
                    "end": end,
                    "unit": unit,
                    "concept": next(
                        (row["concept"] for row in parser.facts if row["concept"].lower() == concept_key),
                        concept_key,
                    ),
                    "total_concept": target["concept"],
                    "total": total_value,
                    "breakdowns": reconciled,
                }
                break
        if selected:
            periods.append(selected)
        if len(periods) >= _MAX_PERIODS:
            break

    answer = {"periods": periods}
    if not periods:
        answer["reason"] = (
            "No single-axis revenue breakdown reconciled to a reported annual total; "
            "multi-dimensional or incomplete facts were not estimated."
        )
    return answer


def _latest_annual_filing(submissions):
    recent = (submissions.get("filings") or {}).get("recent") or {}
    forms = recent.get("form") or []
    for i, form in enumerate(forms):
        if form not in ANNUAL_FORMS:
            continue
        try:
            return {
                "form": form,
                "filed": recent.get("filingDate", [])[i],
                "accession": recent.get("accessionNumber", [])[i],
                "document": recent.get("primaryDocument", [])[i],
            }
        except IndexError:
            continue
    return None


def _get_text(url: str):
    # Share the SEC fair-access clock and declared identity with company facts.
    import sec_company_facts as company_facts

    wait = company_facts.MIN_INTERVAL_S - (time.monotonic() - company_facts._last_request[0])
    if wait > 0:
        time.sleep(wait)
    req = urllib.request.Request(url, headers={
        "User-Agent": company_facts.USER_AGENT,
        "Accept-Encoding": "identity",
        "Accept": "text/html,application/xhtml+xml",
    })
    try:
        with urllib.request.urlopen(req, timeout=company_facts.TIMEOUT_S) as response:
            return response.read().decode("utf-8", errors="replace")
    finally:
        company_facts._last_request[0] = time.monotonic()


def segments(ticker: str, *, refresh: bool = False):
    import sec_company_facts as company_facts

    ticker = (ticker or "").strip().upper()
    if not ticker:
        return {"success": False, "error": "No ticker given."}
    cik, title = company_facts.lookup_cik(ticker)
    if cik is None:
        return {"success": False, "code": "not_found",
                "error": f"{ticker} is not in SEC EDGAR's list of filers."}
    cache_name = f"segments_{cik:010d}.json"
    cached = None if refresh else company_facts._read_cache(cache_name, _CACHE_TTL_S)
    if cached is not None:
        cached["symbol"] = ticker
        return {"success": True, "data": cached}

    submissions = company_facts._get_json(SUBMISSIONS_URL.format(cik=cik))
    filing = _latest_annual_filing(submissions)
    if not filing:
        return {"success": False, "code": "no_filing",
                "error": f"SEC lists no recent annual inline-XBRL filing for {ticker}."}
    accession = filing["accession"].replace("-", "")
    url = ARCHIVE_URL.format(cik=cik, accession=accession, document=filing["document"])
    parsed = extract_revenue_segments(_get_text(url))
    data = {
        "symbol": ticker,
        "cik": cik,
        "name": submissions.get("name") or title,
        "source": {
            "name": "SEC EDGAR inline XBRL",
            "form": filing["form"],
            "filed": filing["filed"],
            "accession": filing["accession"],
            "url": url,
        },
        "periods": parsed["periods"],
        "reason": parsed.get("reason", ""),
        "as_of": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
        "fincept_credits": 0,
    }
    company_facts._write_cache(cache_name, data)
    return {"success": True, "data": data}


def main(argv):
    if len(argv) < 2 or argv[1] != "segments":
        print(fincept_json.dumps({"success": False, "error": "usage: sec_segment_revenue.py segments <TICKER>"}))
        return 1
    try:
        out = segments(argv[2] if len(argv) > 2 else "", refresh="--refresh" in argv[3:])
    except urllib.error.HTTPError as error:
        message = "SEC did not answer - try again in a minute." if error.code in (403, 429) else f"SEC answered HTTP {error.code}."
        out = {"success": False, "code": "rate_limited" if error.code in (403, 429) else "network", "error": message}
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        out = {"success": False, "code": "network", "error": f"Could not reach SEC EDGAR ({type(error).__name__})."}
    except Exception as error:
        out = {"success": False, "code": "unknown", "error": f"SEC revenue segments could not be read: {error}"}
    print(fincept_json.dumps(out))
    return 0 if out.get("success") else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
