"""Contract tests for the local-first BYO analytics engine.

Run with:
    python3 -m unittest scripts/Analytics/tests/test_byo_analytics.py
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[2] / "byo_analytics.py"


def load_module():
    if not SCRIPT.exists():
        raise AssertionError(f"BYO analytics script is missing: {SCRIPT}")
    spec = importlib.util.spec_from_file_location("byo_analytics", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


class ByoAnalyticsTest(unittest.TestCase):
    def test_portfolio_risk_reports_losses_drawdown_and_normalized_contributions(self):
        mod = load_module()
        result = mod.portfolio_risk(
            {
                "returns": {
                    "ALPHA": [0.01, -0.02, 0.015, -0.01, 0.005, -0.03, 0.02, 0.01],
                    "BETA": [0.005, -0.01, 0.004, -0.006, 0.003, -0.012, 0.009, 0.004],
                },
                "weights": {"ALPHA": 0.6, "BETA": 0.4},
                "confidence": 0.95,
            }
        )

        self.assertEqual(result["kind"], "portfolio_risk")
        self.assertGreater(result["annualized_volatility"], 0)
        self.assertGreaterEqual(result["historical_var"], 0)
        self.assertGreaterEqual(result["expected_shortfall"], result["historical_var"])
        self.assertLess(result["max_drawdown"], 0)
        self.assertAlmostEqual(sum(result["risk_contribution"].values()), 1.0, places=7)

    def test_portfolio_risk_reports_benchmark_and_diversification_metrics(self):
        mod = load_module()
        benchmark = [0.01, -0.02, 0.015, -0.01, 0.004, -0.006, 0.012, -0.004]
        asset = [1.5 * value + 0.001 for value in benchmark]

        result = mod.portfolio_risk(
            {
                "returns": {"ALPHA": asset},
                "benchmark_returns": benchmark,
                "weights": {"ALPHA": 1.0},
            }
        )

        self.assertAlmostEqual(result["benchmark"]["beta"], 1.5, places=7)
        self.assertAlmostEqual(result["benchmark"]["annualized_alpha"], 0.252, places=7)
        self.assertAlmostEqual(result["benchmark"]["correlation"], 1.0, places=7)
        self.assertAlmostEqual(result["diversification_ratio"], 1.0, places=7)
        self.assertEqual(result["correlation_matrix"]["ALPHA"]["ALPHA"], 1.0)

    def test_technical_rating_detects_a_persistent_uptrend(self):
        mod = load_module()
        closes = [100 + i * 0.4 + (i % 5) * 0.03 for i in range(240)]
        result = mod.technical_rating({"closes": closes})

        self.assertEqual(result["kind"], "technical_rating")
        self.assertIn(result["rating"], {"BUY", "STRONG_BUY"})
        self.assertGreater(result["score"], 25)
        self.assertGreater(result["indicators"]["sma20"], result["indicators"]["sma50"])
        self.assertGreater(result["indicators"]["sma50"], result["indicators"]["sma200"])
        self.assertEqual(result["votes"]["moving_averages"]["total"], 10)
        self.assertEqual(result["votes"]["oscillators"]["total"], 6)
        self.assertEqual(len(result["moving_averages"]), 10)
        self.assertEqual(len(result["oscillators"]), 6)

    def test_market_pulse_weights_available_signals_and_reports_coverage(self):
        mod = load_module()
        result = mod.market_pulse(
            {
                "signals": {
                    "trend": 0.8,
                    "breadth": 0.6,
                    "volatility": -0.4,
                    "credit": 0.2,
                },
                "weights": {"trend": 3, "breadth": 2, "volatility": 2, "credit": 1},
                "expected_signals": 6,
            }
        )

        self.assertEqual(result["kind"], "market_pulse")
        self.assertGreater(result["score"], 50)
        self.assertEqual(result["regime"], "RISK_ON")
        self.assertAlmostEqual(result["coverage"], 4 / 6)

    def test_options_positioning_computes_ratios_walls_and_max_pain(self):
        mod = load_module()
        result = mod.options_positioning(
            {
                "spot": 100,
                "chain": [
                    {"strike": 90, "call_oi": 300, "put_oi": 900, "call_volume": 30, "put_volume": 80},
                    {"strike": 100, "call_oi": 700, "put_oi": 600, "call_volume": 90, "put_volume": 70},
                    {"strike": 110, "call_oi": 1200, "put_oi": 200, "call_volume": 120, "put_volume": 20},
                ],
            }
        )

        self.assertEqual(result["kind"], "options_positioning")
        self.assertAlmostEqual(result["put_call_oi_ratio"], 1700 / 2200)
        self.assertEqual(result["call_wall"], 110)
        self.assertEqual(result["put_wall"], 90)
        self.assertIn(result["max_pain"], {90.0, 100.0, 110.0})

    def test_options_positioning_derives_iv_expected_move_skew_gamma_and_unusual_activity(self):
        mod = load_module()
        result = mod.options_positioning(
            {
                "spot": 100,
                "time_to_expiry": 30 / 365,
                "risk_free_rate": 0.04,
                "chain": [
                    {"strike": 90, "call_oi": 400, "put_oi": 800, "call_volume": 20, "put_volume": 500,
                     "call_bid": 11.0, "call_ask": 11.5, "put_bid": 1.1, "put_ask": 1.3},
                    {"strike": 100, "call_oi": 900, "put_oi": 850, "call_volume": 200, "put_volume": 180,
                     "call_bid": 3.0, "call_ask": 3.2, "put_bid": 2.7, "put_ask": 2.9},
                    {"strike": 110, "call_oi": 1_200, "put_oi": 300, "call_volume": 100, "put_volume": 20,
                     "call_bid": 0.9, "call_ask": 1.1, "put_bid": 10.5, "put_ask": 11.0},
                ],
            }
        )

        self.assertGreater(result["expected_move"], 0)
        self.assertIsNotNone(result["atm_implied_volatility"])
        self.assertIsNotNone(result["volatility_skew"])
        self.assertEqual(len(result["gamma_by_strike"]), 3)
        self.assertEqual(result["unusual_activity"][0]["strike"], 90.0)
        self.assertEqual(result["unusual_activity"][0]["side"], "PUT")

    def test_put_implied_volatility_accepts_valid_price_above_spot(self):
        mod = load_module()
        spot, strike, years, rate, volatility = 50.0, 120.0, 1.0, 0.04, 0.30
        price = mod._black_scholes_price(spot, strike, years, rate, volatility, False)
        self.assertGreater(price, spot)

        implied = mod._implied_volatility(price, spot, strike, years, rate, False)
        self.assertIsNotNone(implied)
        self.assertAlmostEqual(implied, volatility, places=6)

    def test_fixed_income_matches_zero_coupon_price_and_duration(self):
        mod = load_module()
        result = mod.fixed_income(
            {
                "face_value": 1000,
                "coupon_rate": 0,
                "years": 2,
                "yield_rate": 0.10,
                "frequency": 1,
            }
        )

        self.assertAlmostEqual(result["clean_price"], 1000 / 1.1**2, places=8)
        self.assertAlmostEqual(result["macaulay_duration"], 2.0, places=8)
        self.assertAlmostEqual(result["modified_duration"], 2 / 1.1, places=8)
        self.assertGreater(result["convexity"], 0)
        self.assertGreater(result["dv01"], 0)

    def test_cli_capabilities_is_machine_readable_and_marks_data_limits(self):
        if not SCRIPT.exists():
            self.fail(f"BYO analytics script is missing: {SCRIPT}")
        completed = subprocess.run(
            [sys.executable, str(SCRIPT), "capabilities"],
            check=True,
            capture_output=True,
            text=True,
        )
        result = json.loads(completed.stdout)

        self.assertEqual(result["engine"], "byo_analytics")
        self.assertIn("portfolio_risk", result["commands"])
        self.assertEqual(result["options_positioning"]["data_quality"], "input_dependent")
        self.assertFalse(result["uses_fincept_credits"])

    def test_bundle_runs_multiple_engines_in_one_tool_call(self):
        mod = load_module()
        self.assertTrue(hasattr(mod, "bundle"), "bundle command is missing")
        result = mod.bundle(
            {
                "market_pulse": {"signals": {"trend": 0.5, "breadth": 0.25}},
                "fixed_income": {
                    "face_value": 1000,
                    "coupon_rate": 0.05,
                    "years": 3,
                    "yield_rate": 0.06,
                    "frequency": 2,
                },
            }
        )

        self.assertEqual(result["kind"], "bundle")
        self.assertEqual(result["requested"], ["market_pulse", "fixed_income"])
        self.assertEqual(result["results"]["market_pulse"]["kind"], "market_pulse")
        self.assertEqual(result["results"]["fixed_income"]["kind"], "fixed_income")

    def test_quantlib_routes_cover_every_terminal_module_without_claiming_false_parity(self):
        mod = load_module()
        result = mod.quantlib_routes({})

        self.assertEqual(result["kind"], "quantlib_routes")
        self.assertFalse(result["uses_fincept_credits"])
        self.assertEqual(len(result["modules"]), 18)
        self.assertEqual(
            set(result["modules"]),
            {
                "core", "analysis", "curves", "economics", "instruments", "ml",
                "models", "numerical", "physics", "portfolio", "pricing", "regulatory",
                "risk", "scheduling", "solver", "statistics", "stochastic", "volatility",
            },
        )
        self.assertEqual(result["modules"]["portfolio"]["coverage"], "strong")
        self.assertEqual(result["modules"]["physics"]["coverage"], "limited")
        self.assertIn("scripts", result["modules"]["pricing"])


if __name__ == "__main__":
    unittest.main()
