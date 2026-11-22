"""Runtime-budget contracts for the built-in quantitative analytics engine."""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


ANALYTICS = Path(__file__).resolve().parents[1]
LIMITS = ANALYTICS / "quant" / "runtime_limits.py"
QUANT_MODULES = ANALYTICS / "quant" / "quant_modules_3042.py"
ECONOMICS_ENGINE = ANALYTICS / "economics" / "analytics_engine.py"
PORTFOLIO_RISK = ANALYTICS / "portfolioManagement" / "risk_management.py"
FUNCTIME_SERVICE = ANALYTICS / "functime_wrapper" / "functime_service.py"
LBO_MODEL = ANALYTICS / "corporateFinance" / "lbo" / "lbo_model.py"


def load_limits_module():
    if not LIMITS.exists():
        raise AssertionError(f"quant runtime limits module is missing: {LIMITS}")
    spec = importlib.util.spec_from_file_location("quant_runtime_limits", LIMITS)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


class QuantRuntimeLimitsTest(unittest.TestCase):
    def test_positive_counts_are_integral_and_bounded(self):
        limits = load_limits_module()
        self.assertEqual(limits.bounded_positive_int(10_000, "horizon", 10_000), 10_000)
        for invalid in (0, -1, 1.5, "2.5"):
            with self.subTest(invalid=invalid):
                with self.assertRaisesRegex(ValueError, "positive integer"):
                    limits.bounded_positive_int(invalid, "horizon", 10_000)
        with self.assertRaisesRegex(ValueError, "horizon exceeds local limit"):
            limits.bounded_positive_int(10_001, "horizon", 10_000)

    def test_workload_product_is_bounded_without_overflow(self):
        limits = load_limits_module()
        self.assertEqual(limits.bounded_workload("simulation", [1_000, 5_000], 5_000_000), 5_000_000)
        with self.assertRaisesRegex(ValueError, "simulation workload exceeds local limit"):
            limits.bounded_workload("simulation", [100_001, 100_001], 5_000_000)

    def test_probability_and_threshold_guards_reject_nonfinite_boundaries(self):
        limits = load_limits_module()
        self.assertEqual(limits.open_unit_interval(0.2, "holdout_fraction"), 0.2)
        for invalid in (0, 1, -0.1, 1.1, float("nan"), float("inf"), "bad"):
            with self.subTest(invalid=invalid):
                with self.assertRaisesRegex(ValueError, "strictly between 0 and 1"):
                    limits.open_unit_interval(invalid, "holdout_fraction")

        self.assertEqual(limits.finite_nonnegative(0.0, "threshold"), 0.0)
        for invalid in (-0.1, float("nan"), float("inf"), "bad"):
            with self.subTest(invalid=invalid):
                with self.assertRaisesRegex(ValueError, "finite non-negative number"):
                    limits.finite_nonnegative(invalid, "threshold")

    def test_named_operation_lists_are_nonempty_bounded_and_unique(self):
        limits = load_limits_module()
        self.assertEqual(
            limits.bounded_name_list(["ridge", "random_forest"], "algorithms", 10),
            ["ridge", "random_forest"],
        )
        for invalid in (None, [], "ridge", [""], [1]):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    limits.bounded_name_list(invalid, "algorithms", 10)
        with self.assertRaisesRegex(ValueError, "algorithms exceeds local limit"):
            limits.bounded_name_list([f"model-{i}" for i in range(11)], "algorithms", 10)
        with self.assertRaisesRegex(ValueError, "algorithms contains duplicates"):
            limits.bounded_name_list(["Ridge", " ridge "], "algorithms", 10)

    def test_quant_engine_wires_limits_before_expensive_loops(self):
        source = QUANT_MODULES.read_text(encoding="utf-8")
        self.assertIn('bounded_positive_int(horizon, "horizon", MAX_FORECAST_HORIZON)', source)
        self.assertIn('bounded_positive_int(n_samples, "n_samples", MAX_SIMULATION_COUNT)', source)
        self.assertIn('bounded_positive_int(n_resamples, "n_resamples", MAX_SIMULATION_COUNT)', source)
        self.assertIn('bounded_positive_int(n_simulations, "n_simulations", MAX_SIMULATION_COUNT)', source)
        self.assertGreaterEqual(source.count('bounded_workload("simulation"'), 2)
        self.assertIn('bounded_workload("resampling"', source)
        self.assertIn('bounded_positive_int(cv_folds, "cv_folds", MAX_CV_FOLDS)', source)
        self.assertIn('open_unit_interval(holdout_fraction, "holdout_fraction")', source)
        self.assertIn('finite_nonnegative(overfitting_threshold, "overfitting_threshold")', source)
        self.assertIn('bounded_name_list(algorithms, "algorithms", MAX_METHOD_COUNT)', source)
        self.assertIn('bounded_name_list(methods, "methods", MAX_METHOD_COUNT)', source)
        self.assertIn('bounded_name_list(sampling_methods, "sampling_methods", MAX_METHOD_COUNT)', source)

    def test_economics_monte_carlo_bounds_counts_and_matrix_work(self):
        source = ECONOMICS_ENGINE.read_text(encoding="utf-8")
        self.assertRegex(
            source,
            r'bounded_positive_int\(\s*num_simulations, "num_simulations", MAX_SIMULATION_COUNT\s*\)',
        )
        self.assertRegex(
            source,
            r'bounded_positive_int\(\s*time_periods, "time_periods", MAX_TIME_PERIODS\s*\)',
        )
        self.assertRegex(
            source,
            r'bounded_workload\(\s*"monte_carlo", \[num_simulations, time_periods\], '
            r'MAX_SIMULATION_DRAWS\s*\)',
        )

    def test_economics_forecasts_bound_output_periods(self):
        source = ECONOMICS_ENGINE.read_text(encoding="utf-8")
        expected = (
            r'bounded_positive_int\(\s*forecast_periods, "forecast_periods", '
            r'MAX_FORECAST_PERIODS\s*\)'
        )
        simple = source[source.index("def simple_forecasting_methods("):
                        source.index("def _naive_forecast(")]
        arima = source[source.index("def arima_forecast("):
                       source.index("def calculate(", source.index("def arima_forecast("))]
        self.assertRegex(simple, expected)
        self.assertRegex(arima, expected)

    def test_portfolio_var_bounds_simulation_matrix_before_allocation(self):
        source = PORTFOLIO_RISK.read_text(encoding="utf-8")
        method = source[source.index("def monte_carlo_var("):
                        source.index("def component_var(")]
        self.assertIn(
            "num_simulations, holding_period = _bounded_monte_carlo_shape(", method
        )
        self.assertLess(
            method.index("_bounded_monte_carlo_shape("), method.index("rng.normal(")
        )

    def test_functime_forecasts_bound_user_controlled_horizons(self):
        source = FUNCTIME_SERVICE.read_text(encoding="utf-8")
        forecast = source[source.index("def op_forecast("):
                          source.index("def op_anomaly_detection(")]
        intervals = source[source.index("def op_confidence_intervals("):
                           source.index("def op_stationarity(")]
        expected = 'min(MAX_FORECAST_HORIZON, _safe_int(data.get("horizon", 14)))'
        self.assertIn(expected, forecast)
        self.assertIn(expected, intervals)

    def test_lbo_model_bounds_projection_years_before_projection_loop(self):
        source = LBO_MODEL.read_text(encoding="utf-8")
        build = source[source.index("def build_complete_model("):
                       source.index("def _project(")]
        self.assertIn(
            'bounded_positive_int(projection_years, "projection_years", MAX_PROJECTION_YEARS)',
            build,
        )
        self.assertLess(
            build.index("bounded_positive_int("), build.index("self._project("),
        )


if __name__ == "__main__":
    unittest.main()
