"""Contract tests for zero-credit reports rendered by ProAnalyticsPanel."""

from __future__ import annotations

import importlib.util
import inspect
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[2] / "byo_pro_analytics.py"


def load_module():
    if not SCRIPT.exists():
        raise AssertionError(f"BYO pro analytics script is missing: {SCRIPT}")
    spec = importlib.util.spec_from_file_location("byo_pro_analytics", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


class ByoProAnalyticsTest(unittest.TestCase):
    def test_nasdaq_history_needs_no_yfinance_dependency(self):
        mod = load_module()
        seen = []

        def get_json(url):
            seen.append(url)
            return {
                "status": {"rCode": 200},
                "data": {
                    "tradesTable": {
                        "rows": [
                            {"date": "11/19/2026", "close": "$498.30"},
                            {"date": "11/18/2026", "close": "$487.12"},
                            {"date": "bad", "close": "--"},
                        ]
                    }
                },
            }

        self.assertIn("get_json", inspect.signature(mod._history).parameters)
        history = mod._history("MSFT", "1y", get_json=get_json)

        self.assertEqual(history, [("2026-11-18", 487.12), ("2026-11-19", 498.30)])
        self.assertIn("api.nasdaq.com/api/quote/MSFT/historical", seen[0])
        self.assertIn("fromdate=", seen[0])
        self.assertNotIn("import yfinance", SCRIPT.read_text(encoding="utf-8"))

    def test_nasdaq_history_bars_capture_volume_for_technical_rating(self):
        mod = load_module()

        def get_json(_url):
            return {
                "data": {"tradesTable": {"rows": [
                    {"date": "11/19/2026", "close": "$498.30", "volume": "25,100,000"},
                    {"date": "11/18/2026", "close": "$487.12", "volume": "20,500,000"},
                ]}}
            }

        bars = mod._history_bars("MSFT", "1y", get_json=get_json)

        self.assertEqual(bars[0], {"date": "2026-11-18", "close": 487.12, "volume": 20_500_000.0})
        self.assertEqual(bars[1]["volume"], 25_100_000.0)

    def test_history_falls_back_to_public_yahoo_chart_when_nasdaq_is_unavailable(self):
        mod = load_module()
        seen = []

        def get_json(url):
            seen.append(url)
            if "api.nasdaq.com" in url:
                raise OSError("Nasdaq unavailable")
            return {
                "chart": {
                    "result": [{
                        "timestamp": [1794960000, 1795046400],
                        "indicators": {
                            "quote": [{
                                "close": [487.12, 498.30],
                                "volume": [20_500_000, 25_100_000],
                            }]
                        },
                    }],
                    "error": None,
                }
            }

        try:
            bars = mod._history_bars("MSFT", "1y", get_json=get_json)
        except ValueError as exc:
            self.fail(f"public Yahoo fallback was not attempted: {exc}")

        self.assertEqual([row["close"] for row in bars], [487.12, 498.30])
        self.assertEqual([row["volume"] for row in bars], [20_500_000.0, 25_100_000.0])
        self.assertTrue(any("query1.finance.yahoo.com/v8/finance/chart/MSFT" in url for url in seen))
        self.assertIn("range=1y", seen[-1])

    def test_nasdaq_option_chain_parses_nearest_expiry_without_pandas(self):
        mod = load_module()

        def get_json(_url):
            return {
                "status": {"rCode": 200},
                "data": {
                    "lastTrade": "LAST TRADE: $708.35 (AS OF NOV 20, 2026 10:44 AM ET)",
                    "table": {
                        "rows": [
                            {"expirygroup": "November 20, 2026", "strike": None},
                            {"expirygroup": "", "expiryDate": "Nov 20", "strike": "700.00",
                             "c_Volume": "1,200", "c_Openinterest": "3,400",
                             "p_Volume": "800", "p_Openinterest": "2,100",
                             "c_Bid": "12.20", "c_Ask": "12.50", "p_Bid": "3.10", "p_Ask": "3.30"},
                            {"expirygroup": "November 27, 2026", "strike": None},
                            {"expirygroup": "", "expiryDate": "Nov 27", "strike": "710.00",
                             "c_Volume": "900", "c_Openinterest": "2,000",
                             "p_Volume": "700", "p_Openinterest": "1,800"},
                        ]
                    },
                },
            }

        self.assertTrue(hasattr(mod, "_option_chain"), "a dependency-free option-chain loader is required")
        spot, expiry, chain = mod._option_chain("SPY", get_json=get_json)

        self.assertEqual(spot, 708.35)
        self.assertEqual(expiry, "November 20, 2026")
        self.assertEqual(chain, [{"strike": 700.0, "call_oi": 3400.0, "put_oi": 2100.0,
                                  "call_volume": 1200.0, "put_volume": 800.0,
                                  "call_bid": 12.2, "call_ask": 12.5,
                                  "put_bid": 3.1, "put_ask": 3.3}])

    def test_option_chain_falls_back_to_public_cboe_when_nasdaq_is_unavailable(self):
        mod = load_module()
        seen = []

        def get_json(url):
            seen.append(url)
            if "api.nasdaq.com" in url:
                raise OSError("Nasdaq unavailable")
            return {
                "data": {
                    "current_price": 708.35,
                    "options": [
                        {"option": "SPY270115C00700000", "open_interest": 3400,
                         "volume": 1200, "bid": 12.2, "ask": 12.5},
                        {"option": "SPY270115P00700000", "open_interest": 2100,
                         "volume": 800, "bid": 3.1, "ask": 3.3},
                        {"option": "SPY270122C00710000", "open_interest": 2000,
                         "volume": 900, "bid": 9.0, "ask": 9.3},
                    ],
                }
            }

        try:
            spot, expiry, chain = mod._option_chain("SPY", get_json=get_json)
        except ValueError as exc:
            self.fail(f"public Cboe fallback was not attempted: {exc}")

        self.assertEqual(spot, 708.35)
        self.assertEqual(expiry, "2027-01-15")
        self.assertEqual(chain, [{"strike": 700.0, "call_oi": 3400.0, "put_oi": 2100.0,
                                  "call_volume": 1200.0, "put_volume": 800.0,
                                  "call_bid": 12.2, "call_ask": 12.5,
                                  "put_bid": 3.1, "put_ask": 3.3}])
        self.assertTrue(any("cdn.cboe.com/api/global/delayed_quotes/options/SPY.json" in url for url in seen))

    def test_public_options_uses_iso_cboe_expiry_for_time_to_expiry(self):
        mod = load_module()
        captured = {}
        mod._option_chain = lambda _symbol: (100.0, "2027-11-21", [])

        def options_report(_symbol, _spot, _chain, *, time_to_expiry, expiry):
            captured["time_to_expiry"] = time_to_expiry
            captured["expiry"] = expiry
            return {"meta": {}}

        mod.options_report = options_report

        result = mod._public_options({"symbol": "SPY"})

        self.assertGreater(captured["time_to_expiry"], 0.9)
        self.assertEqual(captured["expiry"], "2027-11-21")
        self.assertEqual(result["meta"]["expiry"], "2027-11-21")

    def test_technical_report_matches_panel_schema(self):
        mod = load_module()
        closes = [100 + i * 0.4 + (i % 5) * 0.03 for i in range(240)]
        benchmark = [100 + i * 0.2 for i in range(240)]
        volumes = [1_000_000 + i * 1_000 for i in range(240)]
        result = mod.technical_report("ACME", closes, benchmark, volumes)
        self.assertEqual(result["data"]["report"]["title"], "Technical rating")
        self.assertIn(result["data"]["report"]["headline"], {"BUY", "STRONG BUY"})
        self.assertEqual(result["meta"]["cost_route"], "local_public_data")
        report = result["data"]["report"]
        self.assertEqual(
            {table["title"] for table in report["tables"]},
            {"Moving averages · 10", "Oscillators · 6", "Performance"},
        )
        labels = {stat["label"] for stat in report["stats"]}
        self.assertTrue({"MA votes", "Oscillator votes", "52-week range", "Relative strength vs SPY", "Volume vs 20d"} <= labels)

    def test_portfolio_report_contains_risk_contributions(self):
        mod = load_module()
        result = mod.portfolio_report(
            ["ALPHA", "BETA"],
            [0.6, 0.4],
            {
                "ALPHA": [0.01, -0.02, 0.015, -0.01, 0.005, -0.03, 0.02, 0.01],
                "BETA": [0.005, -0.01, 0.004, -0.006, 0.003, -0.012, 0.009, 0.004],
            },
            [0.004, -0.008, 0.006, -0.004, 0.002, -0.01, 0.008, 0.003],
        )
        report = result["data"]["report"]
        self.assertEqual(report["title"], "Portfolio risk")
        self.assertEqual(report["tables"][0]["title"], "Risk contribution")
        self.assertEqual(len(report["tables"][0]["rows"]), 2)
        stat_labels = {stat["label"] for stat in report["stats"]}
        self.assertTrue({"Beta vs S&P 500", "Annualized alpha", "Diversification ratio"} <= stat_labels)
        self.assertIn("Correlation matrix", {table["title"] for table in report["tables"]})

    def test_portfolio_report_rejects_partial_weights_instead_of_using_equal_weights(self):
        mod = load_module()
        returns = {
            "ALPHA": [0.01, -0.02, 0.015],
            "BETA": [0.005, -0.01, 0.004],
        }
        with self.assertRaisesRegex(ValueError, "one weight per symbol"):
            mod.portfolio_report(["ALPHA", "BETA"], [0.6], returns)

    def test_portfolio_report_rejects_duplicate_holdings(self):
        mod = load_module()
        with self.assertRaisesRegex(ValueError, "duplicate symbols"):
            mod.portfolio_report(
                ["ALPHA", "alpha"],
                [0.5, 0.5],
                {"ALPHA": [0.01, -0.02, 0.015]},
            )

    def test_options_and_market_reports_are_zero_credit(self):
        mod = load_module()
        options = mod.options_report(
            "ACME",
            100,
            [
                {"strike": 90, "call_oi": 300, "put_oi": 900, "call_volume": 20, "put_volume": 300,
                 "call_bid": 11.0, "call_ask": 11.5, "put_bid": 1.1, "put_ask": 1.3},
                {"strike": 100, "call_oi": 700, "put_oi": 600, "call_volume": 100, "put_volume": 80,
                 "call_bid": 3.0, "call_ask": 3.2, "put_bid": 2.7, "put_ask": 2.9},
                {"strike": 110, "call_oi": 1200, "put_oi": 200, "call_volume": 90, "put_volume": 10,
                 "call_bid": 0.9, "call_ask": 1.1, "put_bid": 10.5, "put_ask": 11.0},
            ],
            time_to_expiry=30 / 365,
        )
        pulse = mod.market_pulse_report({"trend": 0.8, "breadth": 0.4, "volatility": -0.2})
        self.assertEqual(options["data"]["report"]["title"], "Options positioning")
        self.assertEqual(pulse["data"]["report"]["title"], "Market pulse")
        self.assertEqual(options["meta"]["fincept_credits"], 0)
        self.assertEqual(pulse["meta"]["fincept_credits"], 0)
        option_labels = {stat["label"] for stat in options["data"]["report"]["stats"]}
        self.assertTrue({"Expected move", "ATM implied volatility", "Volatility skew", "Net gamma"} <= option_labels)
        self.assertIn("Gamma by strike", {table["title"] for table in options["data"]["report"]["tables"]})
        self.assertIn("Unusual activity", {table["title"] for table in options["data"]["report"]["tables"]})

    def test_options_report_caps_gamma_table_around_spot(self):
        mod = load_module()
        chain = [
            {"strike": float(strike), "call_oi": 100 + strike, "put_oi": 200 + strike}
            for strike in range(50, 151)
        ]

        options = mod.options_report("ACME", 100, chain)
        table = next(table for table in options["data"]["report"]["tables"] if table["title"] == "Gamma by strike")

        self.assertLessEqual(len(table["rows"]), 31)
        self.assertIn("100.00", {row[0] for row in table["rows"]})

    def test_market_pulse_shows_seven_signals_sector_leadership_and_cross_asset_horizons(self):
        mod = load_module()
        signals = {
            "equity_trend": 0.8, "breadth": 0.5, "credit": 0.3, "volatility": -0.2,
            "bonds": 0.1, "dollar": -0.1, "crypto": 0.4,
        }
        performance = {
            "SPY": {"1D": 0.01, "1W": 0.02, "1M": 0.03, "3M": 0.06, "1Y": 0.15},
            "TLT": {"1D": -0.01, "1W": 0.01, "1M": 0.02, "3M": -0.03, "1Y": 0.04},
        }
        sectors = {"Technology": 0.08, "Financials": 0.03, "Energy": -0.02}

        pulse = mod.market_pulse_report(signals, performance, sectors)
        report = pulse["data"]["report"]

        self.assertEqual(report["stats"][1]["value"], "100.0%")
        self.assertEqual(
            {table["title"] for table in report["tables"]},
            {"Seven risk-appetite signals", "Sector leadership · 1 month", "Cross-asset performance"},
        )

    def test_public_market_pulse_populates_all_signals_sectors_and_assets(self):
        mod = load_module()
        slopes = {"TLT": -0.0002, "UUP": -0.0001, "BTC-USD": 0.001, "HYG": 0.0004}

        def fake_history(symbol, _period="1y"):
            slope = slopes.get(symbol, 0.0005)
            return [(f"2026-{1 + index // 28:02d}-{1 + index % 28:02d}", 100 * (1 + slope * index))
                    for index in range(260)]

        mod._history = fake_history
        pulse = mod._public_market_pulse({})
        report = pulse["data"]["report"]
        tables = {table["title"]: table["rows"] for table in report["tables"]}

        self.assertEqual(report["stats"][1]["value"], "100.0%")
        self.assertEqual(len(tables["Seven risk-appetite signals"]), 7)
        self.assertEqual(len(tables["Sector leadership · 1 month"]), 11)
        self.assertGreaterEqual(len(tables["Cross-asset performance"]), 8)


if __name__ == "__main__":
    unittest.main()
