"""Runtime-budget contracts for the built-in quantitative analytics engine."""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


ANALYTICS = Path(__file__).resolve().parents[1]
LIMITS = ANALYTICS / "quant" / "runtime_limits.py"
QUANT_MODULES = ANALYTICS / "quant" / "quant_modules_3042.py"


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


if __name__ == "__main__":
    unittest.main()
