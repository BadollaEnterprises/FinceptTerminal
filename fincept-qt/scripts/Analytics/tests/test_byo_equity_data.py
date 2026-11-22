"""Contracts for public, zero-credit Equity Research datasets."""

from __future__ import annotations

import importlib.util
import io
import unittest
from datetime import date
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[2] / "byo_equity_data.py"
EQUITY_RESEARCH_SCRIPT = Path(__file__).resolve().parents[2] / "equity_research_data.py"


def load_module():
    if not SCRIPT.exists():
        raise AssertionError(f"BYO equity adapter is missing: {SCRIPT}")
    spec = importlib.util.spec_from_file_location("byo_equity_data", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def load_equity_research_module():
    spec = importlib.util.spec_from_file_location("equity_research_data", EQUITY_RESEARCH_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


class ByoEquityDataTest(unittest.TestCase):
    def test_public_json_reader_rejects_oversized_responses(self):
        mod = load_module()
        self.assertEqual(mod._read_json_response(io.BytesIO(b'{"ok":true}'), max_bytes=32), {"ok": True})
        with self.assertRaisesRegex(ValueError, "response exceeds 8 bytes"):
            mod._read_json_response(io.BytesIO(b'{"payload":"too large"}'), max_bytes=8)

    def test_public_session_enriches_only_supported_future_us_listing(self):
        mod = load_equity_research_module()
        calls = []

        def resolver(symbol, start, end):
            calls.append((symbol, start, end))
            return {"date": "2026-11-24", "timing": "BMO", "source": "nasdaq_public"}

        result = mod._apply_public_earnings_session(
            {"exchange": "NMS", "next_earnings": "2026-11-23",
             "next_earnings_end": "2026-11-25"},
            "msft", today=date(2026, 11, 22), resolver=resolver,
        )

        self.assertEqual(calls, [("MSFT", "2026-11-23", "2026-11-25")])
        self.assertEqual(result["next_earnings"], "2026-11-24")
        self.assertEqual(result["next_earnings_end"], "2026-11-24")
        self.assertEqual(result["next_earnings_timing"], "BMO")
        self.assertEqual(result["next_earnings_timing_source"], "nasdaq_public")

        calls.clear()
        unsupported = mod._apply_public_earnings_session(
            {"exchange": "LSE", "next_earnings": "2026-11-24"},
            "VOD.L", today=date(2026, 11, 22), resolver=resolver,
        )
        self.assertEqual(calls, [])
        self.assertNotIn("next_earnings_timing", unsupported)

    def test_exact_earnings_session_is_selected_from_public_calendar(self):
        mod = load_module()
        requests = []

        def fake_fetch(request):
            requests.append(request)
            return {"data": {"events": [
                {"datetime": "2026-11-24T21:00:00Z", "symbol": "MSFT",
                 "timing": "AMC", "eps_estimate": 4.12},
            ]}, "meta": {"source": "nasdaq_public", "fincept_credits": 0}}

        answer = mod.find_earnings_session(
            "msft", "2026-11-23", "2026-11-25", fetcher=fake_fetch,
        )

        self.assertEqual(requests, [{
            "operation": "earnings", "symbol": "MSFT",
            "start_date": "2026-11-23", "end_date": "2026-11-25",
            "page": 1, "page_size": 10,
        }])
        self.assertEqual(answer, {
            "date": "2026-11-24", "timing": "AMC", "eps_estimate": 4.12,
            "source": "nasdaq_public", "fincept_credits": 0,
        })

    def test_earnings_session_rejects_wrong_symbol_or_invalid_timing(self):
        mod = load_module()

        def fake_fetch(_request):
            return {"data": {"events": [
                {"datetime": "2026-11-24T13:00:00Z", "symbol": "AAPL", "timing": "BMO"},
                {"datetime": "2026-11-24T21:00:00Z", "symbol": "MSFT", "timing": "UNKNOWN"},
            ]}, "meta": {"source": "nasdaq_public", "fincept_credits": 0}}

        self.assertEqual(
            mod.find_earnings_session("MSFT", "2026-11-24", "2026-11-24", fetcher=fake_fetch),
            {},
        )

    def test_nasdaq_earnings_rows_are_filtered_sorted_and_paginated(self):
        mod = load_module()
        answer = mod.parse_earnings_calendar(
            "2026-11-20", "2026-11-20",
            [{"time": "time-pre-market", "symbol": "SMALL", "name": "Small Co",
              "marketCap": "$500,000,000", "epsForecast": "$0.20"},
             {"time": "time-after-hours", "symbol": "BIG", "name": "Big Co",
              "marketCap": "$10,000,000,000", "epsForecast": "$1.25"}],
            symbol="", sort="market_cap", page=1, page_size=1,
        )
        self.assertEqual(answer["data"]["events"][0]["symbol"], "BIG")
        self.assertEqual(answer["data"]["events"][0]["timing"], "AMC")
        self.assertEqual(answer["meta"]["pagination"], {
            "page": 1, "page_size": 1, "total": 2, "total_pages": 2, "has_more": True,
        })
        self.assertEqual(answer["meta"]["fincept_credits"], 0)

    def test_nasdaq_ipo_sections_become_filtered_paginated_events(self):
        mod = load_module()
        answer = mod.parse_ipo_calendar(
            "2026-11-01", "2026-11-30",
            [{
                "priced": {"rows": [
                    {"proposedTickerSymbol": "ANT", "companyName": "Anthropic, PBC",
                     "proposedExchange": "NASDAQ Global Select", "proposedSharePrice": "83.00",
                     "sharesOffered": "980,000,000", "pricedDate": "11/19/2026",
                     "dealStatus": "Priced"},
                ]},
                "upcoming": {"upcomingTable": {"rows": [
                    {"proposedTickerSymbol": "FBLLU", "companyName": "Football Manager SPAC Inc.",
                     "proposedExchange": "NASDAQ Global", "proposedSharePrice": "$10 - $12",
                     "sharesOffered": "10,000,000", "expectedPriceDate": "11/20/2026"},
                ]}},
                "filed": {"rows": [
                    {"proposedTickerSymbol": "OLD", "companyName": "Outside Range",
                     "filedDate": "10/03/2026", "dollarValueOfSharesOffered": "$20,000,000"},
                ]},
                "withdrawn": {"rows": []},
            }],
            symbol="", page=1, page_size=1,
        )
        event = answer["data"]["events"][0]
        self.assertEqual(event["symbol"], "ANT")
        self.assertEqual(event["date"], "2026-11-19")
        self.assertEqual(event["offer_price"], 83.0)
        self.assertEqual(event["shares"], 980_000_000)
        self.assertEqual(event["deal_type"], "Priced")
        self.assertEqual(answer["meta"]["pagination"], {
            "page": 1, "page_size": 1, "total": 2, "total_pages": 2, "has_more": True,
        })
        self.assertEqual(answer["meta"]["fincept_credits"], 0)

    def test_nasdaq_split_rows_become_forward_reverse_and_dividend_ratios(self):
        mod = load_module()
        answer = mod.parse_split_calendar(
            "2026-11-20", "2026-12-10",
            [
                {"symbol": "SITM", "name": "SiTime Corporation", "ratio": "3:1",
                 "executionDate": "12/07/2026"},
                {"symbol": "RIME", "name": "Algorhythm Holdings, Inc.", "ratio": "1 : 10",
                 "executionDate": "11/23/2026"},
                {"symbol": "LARK", "name": "Landmark Bancorp Inc.", "ratio": "5%",
                 "executionDate": "12/02/2026"},
            ],
            symbol="", page=1, page_size=20,
        )
        events = answer["data"]["events"]
        self.assertEqual((events[0]["new_shares"], events[0]["old_shares"]), (1.0, 10.0))
        self.assertEqual((events[1]["new_shares"], events[1]["old_shares"]), (105.0, 100.0))
        self.assertEqual((events[2]["new_shares"], events[2]["old_shares"]), (3.0, 1.0))
        self.assertEqual(answer["meta"]["pagination"]["total"], 3)
        self.assertEqual(answer["meta"]["fincept_credits"], 0)

    def test_forex_factory_rows_become_filtered_economic_events(self):
        mod = load_module()
        answer = mod.parse_economic_calendar(
            "2026-11-20", "2026-11-21",
            [
                {"title": "US Consumer Price Index", "country": "USD",
                 "date": "2026-11-20T08:30:00-05:00", "impact": "High",
                 "actual": "0.4%", "forecast": "0.3%", "previous": "0.2%"},
                {"title": "Outside range", "country": "EUR",
                 "date": "2026-11-24T10:00:00+01:00", "impact": "Low"},
            ],
            page=1, page_size=50,
        )
        self.assertEqual(len(answer["data"]["events"]), 1)
        event = answer["data"]["events"][0]
        self.assertEqual(event["datetime"], "2026-11-20T13:30:00Z")
        self.assertEqual(event["region"], "USD")
        self.assertEqual(event["actual"], 0.4)
        self.assertEqual(event["consensus"], 0.3)
        self.assertEqual(event["prior"], 0.2)
        self.assertEqual(answer["meta"]["fincept_credits"], 0)

    def test_nasdaq_option_rows_become_contract_pairs(self):
        mod = load_module()
        answer = mod.parse_option_chain("AAPL", {
            "totalRecord": 2,
            "lastTrade": "LAST TRADE: $347.485 (AS OF NOV 20, 2026 11:01 AM ET)",
            "table": {"rows": [
                {"expirygroup": "November 20, 2026", "expiryDate": None, "strike": None},
                {"expirygroup": "", "expiryDate": "Nov 20", "strike": "350.00",
                 "c_Last": "4.20", "c_Change": "1.10", "c_Bid": "4.10", "c_Ask": "4.30",
                 "c_Volume": "1,234", "c_Openinterest": "5,678",
                 "p_Last": "6.55", "p_Change": "-0.80", "p_Bid": "6.40", "p_Ask": "6.70",
                 "p_Volume": "987", "p_Openinterest": "4,321"},
            ]},
        }, expiration="", strikes_around_atm=10, page=1, page_size=50)
        data = answer["data"]
        self.assertEqual(data["underlying"]["price"], 347.485)
        self.assertEqual(data["expiration"], "2026-11-20")
        self.assertEqual(data["expirations"], ["2026-11-20"])
        self.assertEqual(len(data["contracts"]), 2)
        self.assertEqual(data["contracts"][0]["type"], "call")
        self.assertEqual(data["contracts"][0]["open_interest"], 5678)
        self.assertEqual(data["contracts"][1]["type"], "put")
        self.assertEqual(answer["meta"]["pagination"]["total"], 2)
        self.assertEqual(answer["meta"]["fincept_credits"], 0)

    def test_nasdaq_analyst_feeds_become_targets_trend_and_estimates(self):
        mod = load_module()
        answer = mod.parse_analyst(
            "AAPL",
            {"companyName": "Apple Inc.", "primaryData": {"lastSalePrice": "$347.48"}},
            {"meanRatingType": "Hold", "ratingsSummary": "Based on 29 analysts."},
            {"consensusOverview": {"lowPriceTarget": 261, "highPriceTarget": 425,
                                   "priceTarget": 343.69, "buy": 15, "sell": 6, "hold": 8},
             "historicalConsensus": [{"z": {"buy": 14, "hold": 9, "sell": 6,
                                                    "date": "10/01/2026", "consensus": "Hold"},
                                      "y": 340.0}]},
            {"quarterlyForecast": {"rows": [
                {"fiscalEnd": "Dec 2026", "consensusEPSForecast": 2.94,
                 "highEPSForecast": 3.18, "lowEPSForecast": 2.86,
                 "noOfEstimates": 10, "up": 4, "down": 2}
            ]}, "yearlyForecast": {"rows": []}},
        )
        data = answer["data"]
        self.assertEqual(data["price_targets"]["current_price"], 347.48)
        self.assertEqual(data["price_targets"]["analysts"], 29)
        self.assertEqual(data["price_targets"]["recommendation_key"], "hold")
        self.assertEqual(data["recommendation_trend"][0]["buy"], 15)
        self.assertEqual(data["estimates"][0]["period"], "0q")
        self.assertEqual(data["estimates"][0]["eps"]["avg"], 2.94)
        self.assertEqual(data["estimates"][0]["eps_revisions"]["up_last_30_days"], 4)

    def test_nasdaq_ownership_feeds_become_holders_and_insider_activity(self):
        mod = load_module()
        answer = mod.parse_ownership(
            "AAPL",
            {"ownershipSummary": {
                "SharesOutstandingPCT": {"value": "76.83%"},
                "ShareoutstandingTotal": {"value": "14,532"}},
             "activePositions": {"rows": [
                 {"positions": "Total Institutional Shares", "holders": "6,522",
                  "shares": "11,164,886,702"}]},
             "holdingsTransactions": {"totalRecords": "2", "table": {"rows": [
                 {"ownerName": "Vanguard Group Inc", "date": "12/31/2025",
                  "sharesHeld": "1,426,283,914", "sharesChangePCT": "1.919%",
                  "marketValue": "$498,072,606"}
             ]}}},
            {"numberOfTrades": {"rows": [
                 {"insiderTrade": "Number of Open Market Buys", "months3": "1"},
                 {"insiderTrade": "Number of Sells", "months3": "3"}]},
             "numberOfSharesTraded": {"rows": [
                 {"insiderTrade": "Number of Shares Bought", "months3": "2,000"},
                 {"insiderTrade": "Number of Shares Sold", "months3": "9,000"}]},
             "transactionTable": {"totalRecords": "1", "table": {"rows": [
                 {"insider": "JANE DOE", "relation": "Officer", "lastDate": "11/16/2026",
                  "transactionType": "Sell", "ownType": "Direct", "sharesTraded": "1,000",
                  "lastPrice": "$352.26", "sharesHeld": "12,000"}
             ]}}},
            transactions_limit=50,
        )
        data = answer["data"]
        self.assertEqual(data["breakdown"]["institutions_pct"], 76.83)
        self.assertEqual(data["breakdown"]["institutions_count"], 6522)
        self.assertAlmostEqual(data["institutional_holders"][0]["pct_held"], 9.815, places=3)
        self.assertEqual(data["insider_transactions"][0]["value"], 352260)
        self.assertEqual(data["insider_activity"]["net_shares"], -7000)
        self.assertEqual(answer["meta"]["fincept_credits"], 0)


if __name__ == "__main__":
    unittest.main()
