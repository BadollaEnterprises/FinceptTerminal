"""Contracts for public, zero-credit dashboard datasets."""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[2] / "byo_dashboard_data.py"


def load_module():
    if not SCRIPT.exists():
        raise AssertionError(f"BYO dashboard adapter is missing: {SCRIPT}")
    spec = importlib.util.spec_from_file_location("byo_dashboard_data", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


class ByoDashboardDataTest(unittest.TestCase):
    def test_ecb_series_become_a_widget_ready_aaa_curve(self):
        mod = load_module()
        self.assertTrue(hasattr(mod, "parse_ecb_curve"), "ECB curve parser is required")
        answer = mod.parse_ecb_curve({
            0.25: [{"PERIOD": "2026-11-19T00:00:00+01:00", "OBS_VALUE_AS_IS": "2.148"}],
            2.0: [{"PERIOD": "2026-11-19T00:00:00+01:00", "OBS_VALUE_AS_IS": "2.461"}],
            10.0: [{"PERIOD": "2026-11-19T00:00:00+01:00", "OBS_VALUE_AS_IS": "3.441513"}],
        })
        self.assertEqual(answer["data"]["date"], "2026-11-19")
        self.assertEqual(answer["data"]["points"], [
            {"years": 0.25, "rate": 2.148},
            {"years": 2.0, "rate": 2.461},
            {"years": 10.0, "rate": 3.441513},
        ])
        self.assertEqual(answer["meta"]["dataset"], "rates.euro_curve")
        self.assertEqual(answer["meta"]["fincept_credits"], 0)

    def test_cboe_settlements_become_monthly_vix_term_structure(self):
        mod = load_module()
        self.assertTrue(hasattr(mod, "parse_vix_settlements"), "VIX settlement parser is required")
        text = ("Product,Symbol,Expiration Date,Price\n"
                "VX,VX47/X6,2026-11-24,24.5357\n"
                "VX,VX/Z6,2026-12-16,24.5357\n"
                "VX,VX/F7,2027-01-20,23.989\n"
                "VXM,VXM/Z6,2026-12-16,24.5357\n")
        answer = mod.parse_vix_settlements(text, "2026-11-19")
        data = answer["data"]
        self.assertEqual(data["settlement_date"], "2026-11-19")
        self.assertEqual(data["structure"], "backwardation")
        self.assertEqual(len(data["contracts"]), 3)
        self.assertTrue(data["contracts"][0]["weekly"])
        self.assertFalse(data["contracts"][1]["weekly"])
        self.assertAlmostEqual(data["front_to_second_month_pct"], -2.22818, places=3)
        self.assertEqual(answer["meta"]["fincept_credits"], 0)

    def test_treasury_csv_matches_yield_curve_widget_schema(self):
        mod = load_module()
        text = ('Date,"1 Mo","3 Mo","6 Mo","1 Yr","2 Yr","3 Yr","5 Yr","7 Yr","10 Yr","20 Yr","30 Yr"\n'
                '11/19/2026,4.20,4.34,4.42,4.62,4.76,4.81,4.86,4.94,5.04,5.43,5.40\n')
        answer = mod.parse_treasury_csv(text)
        row = answer["data"]["rows"][0]
        self.assertEqual(row["date"], "2026-11-19")
        self.assertEqual(row["month_3"], 4.34)
        self.assertEqual(row["year_10"], 5.04)
        self.assertEqual(answer["meta"]["fincept_credits"], 0)

    def test_coinpaprika_payloads_match_crypto_widget_schema(self):
        mod = load_module()
        answer = mod.crypto_answer(
            {"market_cap_usd": 2_000_000, "volume_24h_usd": 120_000,
             "bitcoin_dominance_percentage": 56.2, "market_cap_change_24h": -2.5},
            [{"name": "Bitcoin", "symbol": "BTC", "rank": 1},
             {"name": "Ethereum", "symbol": "ETH", "rank": 2,
              "quotes": {"USD": {"market_cap": 300_000}}}],
        )
        self.assertEqual(answer["data"]["total_market_cap_usd"], 2_000_000)
        self.assertEqual(answer["data"]["market_cap_percentage"]["btc"], 56.2)
        self.assertIn("eth", answer["data"]["market_cap_percentage"])
        self.assertEqual(answer["data"]["market_cap_percentage"]["eth"], 15.0)
        self.assertEqual(answer["data"]["coins"][1]["symbol"], "ETH")
        self.assertEqual(answer["meta"]["cost_route"], "public_zero_credit")

    def test_nasdaq_earnings_matches_calendar_parser_schema(self):
        mod = load_module()
        answer = mod.parse_earnings(
            "2026-11-20",
            {"data": {"rows": [{"time": "time-pre-market", "symbol": "BJ", "name": "BJ's Wholesale",
                                  "marketCap": "$14,005,773,300", "epsForecast": "$1.24"}]}}
        )
        event = answer["data"]["events"][0]
        self.assertEqual(event["symbol"], "BJ")
        self.assertEqual(event["datetime"], "2026-11-20T13:00:00Z")
        self.assertEqual(event["timing"], "BMO")
        self.assertEqual(event["market_cap"], 14_005_773_300)
        self.assertEqual(event["eps_estimate"], 1.24)


if __name__ == "__main__":
    unittest.main()
