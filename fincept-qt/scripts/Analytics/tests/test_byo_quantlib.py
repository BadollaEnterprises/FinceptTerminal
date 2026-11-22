"""Contracts for the zero-credit QuantLib substitute."""

from __future__ import annotations

import importlib.util
import json
import math
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[2] / "byo_quantlib.py"
MANIFEST = Path(__file__).resolve().parents[2] / "byo_quantlib_routes.json"


def load_module():
    if not SCRIPT.exists():
        raise AssertionError(f"Local QuantLib adapter is missing: {SCRIPT}")
    spec = importlib.util.spec_from_file_location("byo_quantlib", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


class ByoQuantLibTest(unittest.TestCase):
    def test_route_manifest_matches_implemented_routes(self):
        mod = load_module()
        self.assertTrue(MANIFEST.exists())
        routes = json.loads(MANIFEST.read_text(encoding="utf-8"))["routes"]
        self.assertEqual(set(routes), set(mod.ROUTES))

    def test_scheduling_module_is_available_without_cloud_entitlements(self):
        mod = load_module()
        expected = {
            "scheduling/adjustment/adjust-date", "scheduling/adjustment/batch-adjust",
            "scheduling/adjustment/methods", "scheduling/calendar/add-business-days",
            "scheduling/calendar/business-days-between", "scheduling/calendar/is-business-day",
            "scheduling/calendar/list", "scheduling/calendar/next-business-day",
            "scheduling/calendar/previous-business-day", "scheduling/daycount/batch-year-fraction",
            "scheduling/daycount/conventions", "scheduling/daycount/day-count",
            "scheduling/daycount/year-fraction", "scheduling/schedule/generate",
        }
        self.assertTrue(expected.issubset(mod.ROUTES))

        moved = mod.execute("scheduling/calendar/add-business-days", {"date": "2024-01-12", "days": 1})
        fraction = mod.execute("scheduling/daycount/year-fraction", {
            "start_date": "2024-01-01", "end_date": "2024-07-01", "convention": "ACT/365",
        })
        schedule = mod.execute("scheduling/schedule/generate", {
            "effective_date": "2024-01-15", "termination_date": "2025-01-15", "frequency": "3M",
        })
        self.assertEqual(moved["data"]["date"], "2024-01-15")
        self.assertAlmostEqual(fraction["data"]["year_fraction"], 182 / 365)
        self.assertEqual(schedule["data"]["dates"], [
            "2024-01-15", "2024-04-15", "2024-07-15", "2024-10-15", "2025-01-15",
        ])
        self.assertEqual(schedule["meta"]["fincept_credits"], 0)

    def test_business_day_walks_are_bounded(self):
        mod = load_module()
        add = mod.execute("scheduling/calendar/add-business-days", {
            "date": "2024-01-01", "days": 100001,
        })
        between = mod.execute("scheduling/calendar/business-days-between", {
            "start_date": "1700-01-01", "end_date": "2024-01-01",
        })
        self.assertIn("business-day range is outside local limits", add.get("error", ""))
        self.assertIn("business-day range is outside local limits", between.get("error", ""))
        self.assertEqual(add["meta"]["fincept_credits"], 0)
        self.assertEqual(between["meta"]["fincept_credits"], 0)

    def test_numerical_module_is_available_without_cloud_entitlements(self):
        mod = load_module()
        expected = {
            "numerical/differentiation/derivative", "numerical/differentiation/gradient",
            "numerical/differentiation/hessian", "numerical/fft/convolve", "numerical/fft/forward",
            "numerical/fft/inverse", "numerical/integration/monte-carlo",
            "numerical/integration/quadrature", "numerical/integration/stratified",
            "numerical/interpolation/evaluate", "numerical/interpolation/spline-curve",
            "numerical/interpolation/spline-derivative", "numerical/least-squares/fit",
            "numerical/linalg/decompose", "numerical/linalg/dot", "numerical/linalg/inverse",
            "numerical/linalg/lstsq", "numerical/linalg/matmul", "numerical/linalg/matvec",
            "numerical/linalg/norm", "numerical/linalg/outer", "numerical/linalg/solve",
            "numerical/linalg/transpose", "numerical/ode/solve", "numerical/optimize/minimize",
            "numerical/roots/find-1d", "numerical/roots/find-nd", "numerical/roots/newton",
        }
        self.assertTrue(expected.issubset(mod.ROUTES))

        solved = mod.execute("numerical/linalg/solve", {"A": [[4, 1], [1, 3]], "b": [1, 2]})
        derivative = mod.execute("numerical/differentiation/derivative", {"func_name": "sin", "x": 1.0})
        integral = mod.execute("numerical/integration/quadrature", {
            "func_name": "sin", "a": 0.0, "b": math.pi, "n": 64,
        })
        root = mod.execute("numerical/roots/find-1d", {"func_name": "sin", "a": 3.0, "b": 4.0})
        convolution = mod.execute("numerical/fft/convolve", {"a": [1, 2, 3], "b": [0, 1, 0.5]})
        self.assertAlmostEqual(solved["data"]["solution"][0], 1 / 11)
        self.assertAlmostEqual(solved["data"]["solution"][1], 7 / 11)
        self.assertAlmostEqual(derivative["data"]["derivative"], math.cos(1.0), places=6)
        self.assertAlmostEqual(integral["data"]["value"], 2.0, places=6)
        self.assertAlmostEqual(root["data"]["root"], math.pi, places=8)
        self.assertEqual(convolution["data"]["values"], [0.0, 1.0, 2.5, 4.0, 1.5])

    def test_ode_step_must_be_positive_and_finite(self):
        mod = load_module()
        body = {"t_span": [0, 1], "y0": [10, 5], "system": "lotka_volterra"}
        for step in (0, -0.1, float("nan")):
            with self.subTest(step=step):
                answer = mod.execute("numerical/ode/solve", {**body, "h": step})
                self.assertIn("h must be positive and finite", answer.get("error", ""))
                self.assertEqual(answer["meta"]["fincept_credits"], 0)

    def test_financial_solver_module_is_available_without_cloud_entitlements(self):
        mod = load_module()
        expected = {
            "solver/bootstrap/curve", "solver/calibration/vasicek",
            "solver/finance/asw-spread", "solver/finance/bond-yield",
            "solver/finance/convexity", "solver/finance/convexity-adjustment",
            "solver/finance/duration", "solver/finance/forward-futures-conversion",
            "solver/finance/implied-vol", "solver/finance/implied-vol-black76",
            "solver/finance/irr", "solver/finance/modified-duration",
            "solver/finance/oas", "solver/finance/par-rate", "solver/finance/pv01",
            "solver/finance/xirr", "solver/finance/z-spread",
        }
        self.assertTrue(expected.issubset(mod.ROUTES))

        bond_yield = mod.execute("solver/finance/bond-yield", {
            "price": 100, "coupon": 0.05, "maturity": 5.0,
        })
        duration = mod.execute("solver/finance/duration", {
            "coupon": 0.05, "maturity": 5.0, "ytm": 0.05,
        })
        irr = mod.execute("solver/finance/irr", {"cashflows": [-100, 110]})
        xirr = mod.execute("solver/finance/xirr", {
            "cashflows": [-100, 110], "dates": [0, 1],
        })
        curve = mod.execute("solver/bootstrap/curve", {
            "reference_date": "2025-01-15",
            "instruments": [
                {"instrument_type": "deposit", "tenor": 0.5, "rate": 0.05},
                {"instrument_type": "swap", "tenor": 2.0, "rate": 0.045},
            ],
        })
        calibration = mod.execute("solver/calibration/vasicek", {
            "zero_rates": [0.04, 0.042, 0.045, 0.047], "times": [0.25, 0.5, 1, 2],
        })
        self.assertAlmostEqual(bond_yield["data"]["yield"], 0.05, places=8)
        self.assertGreater(duration["data"]["duration"], 0)
        self.assertAlmostEqual(irr["data"]["irr"], 0.1, places=8)
        self.assertAlmostEqual(xirr["data"]["xirr"], 0.1, places=8)
        self.assertEqual(len(curve["data"]["nodes"]), 2)
        self.assertGreater(calibration["data"]["long_term_mean"], 0)
        self.assertEqual(curve["meta"]["fincept_credits"], 0)

    def test_solver_bond_payment_schedules_are_bounded(self):
        mod = load_module()
        body = {"coupon": 0.05, "maturity": 5.0, "ytm": 0.05}
        for frequency in (0, 367):
            with self.subTest(frequency=frequency):
                answer = mod.execute("solver/finance/duration", {**body, "frequency": frequency})
                self.assertIn("payment schedule is outside local limits", answer.get("error", ""))
                self.assertEqual(answer["meta"]["fincept_credits"], 0)

    def test_remaining_instrument_routes_are_local_and_zero_credit(self):
        mod = load_module()
        expected = {
            "instruments/bond/inflation-linked", "instruments/cds/value",
            "instruments/commodity/future", "instruments/equity/variance-swap",
            "instruments/equity/volatility-swap", "instruments/fra/break-even",
            "instruments/fra/value", "instruments/futures/bond/ctd",
            "instruments/futures/stir", "instruments/money-market/deposit",
            "instruments/money-market/repo", "instruments/money-market/tbill",
            "instruments/ois/build-curve", "instruments/ois/value",
        }
        self.assertTrue(expected.issubset(mod.ROUTES))

        deposit = mod.execute("instruments/money-market/deposit", {
            "start_date": "2024-01-01", "maturity_date": "2025-01-01",
            "notional": 1_000_000, "rate": 0.05,
        })
        stir = mod.execute("instruments/futures/stir", {"price": 95.5})
        variance = mod.execute("instruments/equity/variance-swap", {
            "strike_variance": 0.04, "realized_returns": [0.01, -0.01] * 126,
            "notional": 1_000_000,
        })
        ctd = mod.execute("instruments/futures/bond/ctd", {
            "futures_price": 112.5,
            "deliverables": [
                {"price": 105.2, "conversion_factor": 0.93},
                {"price": 108.4, "conversion_factor": 0.96},
            ],
        })
        self.assertAlmostEqual(deposit["data"]["interest"], 50_136.986301, places=3)
        self.assertAlmostEqual(stir["data"]["implied_rate"], 0.045)
        self.assertIn("payoff", variance["data"])
        self.assertEqual(ctd["data"]["cheapest_index"], 1)
        self.assertEqual(ctd["meta"]["fincept_credits"], 0)

    def test_remaining_pricing_routes_are_local_and_zero_credit(self):
        mod = load_module()
        expected = {
            "pricing/bachelier/shifted-lognormal", "pricing/bachelier/vol-conversion",
            "pricing/basket-levy", "pricing/binomial/barrier", "pricing/binomial/bermudan",
            "pricing/kirk/spread-greeks", "pricing/kirk/spread-price", "pricing/margrabe",
        }
        self.assertTrue(expected.issubset(mod.ROUTES))

        spread = mod.execute("pricing/kirk/spread-price", {
            "F1": 100, "F2": 95, "strike": 5, "sigma1": 0.2, "sigma2": 0.18,
            "rho": 0.7, "risk_free_rate": 0.05, "time_to_maturity": 1,
        })
        exchange = mod.execute("pricing/margrabe", {
            "S1": 100, "S2": 95, "q1": 0, "q2": 0,
            "sigma1": 0.2, "sigma2": 0.18, "rho": 0.7, "time_to_maturity": 1,
        })
        bermudan = mod.execute("pricing/binomial/bermudan", {
            "spot": 100, "strike": 105, "risk_free_rate": 0.05, "volatility": 0.2,
            "time_to_maturity": 1, "n_steps": 100, "exercise_dates": [0.5, 1],
            "option_type": "put",
        })
        self.assertGreater(spread["data"]["price"], 0)
        self.assertGreater(exchange["data"]["price"], 0)
        self.assertGreater(bermudan["data"]["price"], 0)
        self.assertEqual(bermudan["meta"]["fincept_credits"], 0)

    def test_tree_pricers_reject_unbounded_or_zero_step_counts(self):
        mod = load_module()
        base = {
            "spot": 100, "strike": 105, "risk_free_rate": 0.05,
            "volatility": 0.2, "time_to_maturity": 1, "option_type": "put",
        }
        bermudan = mod.execute("pricing/binomial/bermudan", {
            **base, "n_steps": 0, "exercise_dates": [0.5, 1],
        })
        barrier = mod.execute("pricing/binomial/barrier", {
            **base, "n_steps": 5001, "barrier": 80,
        })
        self.assertIn("n_steps must be between 1 and 5000", bermudan["error"])
        self.assertIn("n_steps must be between 1 and 5000", barrier["error"])

    def test_remaining_volatility_routes_are_local_and_zero_credit(self):
        mod = load_module()
        expected = {
            "volatility/local-vol/implied-to-local", "volatility/sabr/calibrate",
            "volatility/sabr/density", "volatility/sabr/dynamics",
            "volatility/sabr/normal-vol", "volatility/sabr/smile",
            "volatility/surface/from-points", "volatility/surface/smile",
            "volatility/surface/total-variance",
        }
        self.assertTrue(expected.issubset(mod.ROUTES))

        smile = mod.execute("volatility/sabr/smile", {
            "forward": 100, "expiry": 1, "alpha": 0.2, "beta": 0.5,
            "rho": -0.5, "nu": 0.4, "strikes": [90, 100, 110],
        })
        total_variance = mod.execute("volatility/surface/total-variance", {
            "expiries": [1], "strikes": [100], "vol_matrix": [[0.2]],
            "query_expiry": 1, "query_strike": 100,
        })
        local = mod.execute("volatility/local-vol/implied-to-local", {
            "spot": 100, "rate": 0.05, "strike": 100, "expiry": 1,
            "implied_vol": 0.2,
        })
        self.assertEqual(len(smile["data"]["volatilities"]), 3)
        self.assertAlmostEqual(total_variance["data"]["total_variance"], 0.04)
        self.assertAlmostEqual(local["data"]["local_volatility"], 0.2)
        self.assertEqual(local["meta"]["fincept_credits"], 0)

    def test_financial_model_routes_are_local_and_zero_credit(self):
        mod = load_module()
        expected = {
            "models/dupire/price", "models/heston/implied-vol",
            "models/heston/monte-carlo", "models/heston/price",
            "models/hull-white/calibrate", "models/kou/price",
            "models/merton/fft", "models/merton/price",
            "models/short-rate/bond-option", "models/short-rate/bond-price",
            "models/short-rate/monte-carlo", "models/short-rate/yield-curve",
            "models/svi/calibrate", "models/variance-gamma/price",
        }
        self.assertTrue(expected.issubset(mod.ROUTES))

        curve = mod.execute("models/short-rate/bond-price", {
            "maturities": [0.25, 0.5, 1.0, 2.0], "r0": 0.03,
        })
        heston = mod.execute("models/heston/price", {
            "S0": 100, "v0": 0.04, "r": 0.05, "kappa": 2,
            "theta": 0.04, "sigma_v": 0.3, "rho": -0.5,
            "strike": 100, "T": 1,
        })
        merton = mod.execute("models/merton/price", {
            "S": 100, "K": 100, "T": 1, "r": 0.05, "sigma": 0.2,
            "lambda_jump": 0.5, "mu_jump": -0.05, "sigma_jump": 0.15,
        })
        svi = mod.execute("models/svi/calibrate", {
            "strikes": [90, 95, 100, 105, 110],
            "market_vols": [0.25, 0.22, 0.2, 0.21, 0.23],
            "forward": 100, "T": 1,
        })
        self.assertEqual(len(curve["data"]["prices"]), 4)
        self.assertGreater(curve["data"]["prices"][0], curve["data"]["prices"][-1])
        self.assertGreater(heston["data"]["price"], 0)
        self.assertGreater(merton["data"]["price"], 0)
        self.assertEqual(len(svi["data"]["fitted_vols"]), 5)
        self.assertEqual(svi["meta"]["fincept_credits"], 0)

    def test_remaining_curve_routes_are_local_and_zero_credit(self):
        mod = load_module()
        expected = {
            "curves/build", "curves/butterfly", "curves/composite",
            "curves/constrained-fit", "curves/cross-currency-basis",
            "curves/inflation/bootstrap", "curves/inflation/build",
            "curves/inflation/seasonality", "curves/instantaneous-forward",
            "curves/interpolate-derivative", "curves/key-rate-shift",
            "curves/monotonicity-check", "curves/multicurve/basis-spread",
            "curves/multicurve/setup", "curves/nelson-siegel/evaluate",
            "curves/nelson-siegel/fit", "curves/nss/evaluate", "curves/nss/fit",
            "curves/proxy", "curves/real-rate", "curves/roll", "curves/scale",
            "curves/smoothness-penalty", "curves/time-shift", "curves/twist",
        }
        self.assertTrue(expected.issubset(mod.ROUTES))

        built = mod.execute("curves/build", {
            "reference_date": "2024-01-15", "tenors": [1, 2, 5],
            "values": [0.04, 0.045, 0.05],
        })
        derivative = mod.execute("curves/interpolate-derivative", {
            "x": [1, 2, 3], "y": [2, 4, 6], "query_x": 2.5,
        })
        nelson = mod.execute("curves/nelson-siegel/evaluate", {
            "beta0": 0.05, "beta1": -0.02, "beta2": 0.01,
            "tau": 1.5, "query_tenors": [1, 3, 7],
        })
        basis = mod.execute("curves/multicurve/basis-spread", {
            "ois_tenors": [1, 2, 5], "ois_rates": [0.04, 0.045, 0.05],
            "ibor_tenors": [1, 2, 5], "ibor_rates": [0.045, 0.05, 0.055],
            "query_tenor": 3,
        })
        inflation = mod.execute("curves/inflation/build", {
            "base_cpi": 100, "tenors": [1, 2], "inflation_rates": [0.02, 0.025],
        })
        self.assertEqual(len(built["data"]["points"]), 3)
        self.assertAlmostEqual(derivative["data"]["derivative"], 2.0)
        self.assertEqual(len(nelson["data"]["rates"]), 3)
        self.assertAlmostEqual(basis["data"]["basis_spread"], 0.005)
        self.assertGreater(inflation["data"]["cpi_values"][-1], 100)
        self.assertEqual(inflation["meta"]["fincept_credits"], 0)

    def test_remaining_core_routes_are_local_and_zero_credit(self):
        mod = load_module()
        expected = {
            "core/autodiff/dual-eval", "core/autodiff/gradient",
            "core/autodiff/taylor-expand", "core/distributions/bivariate-normal/cdf",
            "core/distributions/chi2/cdf", "core/distributions/chi2/pdf",
            "core/distributions/exponential/cdf", "core/distributions/exponential/pdf",
            "core/distributions/exponential/ppf", "core/distributions/gamma/cdf",
            "core/distributions/gamma/pdf", "core/distributions/t/cdf",
            "core/distributions/t/pdf", "core/distributions/t/ppf",
            "core/legs/fixed", "core/legs/float", "core/legs/zero-coupon",
            "core/math/eval", "core/math/two-arg", "core/ops/gbm-paths",
            "core/ops/zero-rate-convert", "core/periods/day-count-fraction",
            "core/periods/fixed-coupon", "core/periods/float-coupon",
            "core/types/currencies", "core/types/frequencies",
        }
        self.assertTrue(expected.issubset(mod.ROUTES))

        dual = mod.execute("core/autodiff/dual-eval", {"func_name": "sin", "x": 1})
        student = mod.execute("core/distributions/t/cdf", {"x": 0, "df": 10})
        chi_square = mod.execute("core/distributions/chi2/cdf", {"x": 3.841459, "df": 1})
        exponential = mod.execute("core/distributions/exponential/ppf", {"p": 0.95, "rate": 1})
        fixed = mod.execute("core/legs/fixed", {
            "notional": 1_000_000, "rate": 0.05, "frequency": "6M",
            "start_date": "2024-01-01", "end_date": "2025-01-01",
        })
        currencies = mod.execute("core/types/currencies", {})
        self.assertAlmostEqual(dual["data"]["derivative"], math.cos(1), places=6)
        self.assertAlmostEqual(student["data"]["cdf"], 0.5, places=8)
        self.assertAlmostEqual(chi_square["data"]["cdf"], 0.95, places=4)
        self.assertAlmostEqual(exponential["data"]["value"], -math.log(0.05), places=8)
        self.assertEqual(len(fixed["data"]["cashflows"]), 2)
        self.assertIn("USD", currencies["data"]["currencies"])
        self.assertEqual(currencies["meta"]["fincept_credits"], 0)

    def test_regulatory_routes_are_local_and_zero_credit(self):
        mod = load_module()
        expected = {
            "regulatory/basel/capital-ratios", "regulatory/basel/credit-rwa",
            "regulatory/basel/operational-rwa", "regulatory/ifrs9/ecl-12m",
            "regulatory/ifrs9/ecl-lifetime", "regulatory/ifrs9/sicr",
            "regulatory/ifrs9/stage-assessment", "regulatory/liquidity/lcr",
            "regulatory/liquidity/nsfr", "regulatory/saccr/ead",
            "regulatory/stress/capital-projection",
        }
        self.assertTrue(expected.issubset(mod.ROUTES))

        capital = mod.execute("regulatory/basel/capital-ratios", {
            "cet1_capital": 120, "tier1_capital": 140,
            "total_capital": 180, "risk_weighted_assets": 1000,
        })
        rwa = mod.execute("regulatory/basel/credit-rwa", {
            "exposures": [
                {"amount": 1_000_000, "asset_class": "CORPORATE", "rating": "A"},
                {"amount": 500_000, "asset_class": "RETAIL"},
            ], "method": "standardized",
        })
        ecl = mod.execute("regulatory/ifrs9/ecl-12m", {
            "pd_12m": 0.02, "lgd": 0.4, "ead": 1_000_000,
        })
        stage = mod.execute("regulatory/ifrs9/stage-assessment", {
            "days_past_due": 35, "rating_downgrade": 2, "pd_increase_ratio": 2.5,
        })
        self.assertAlmostEqual(capital["data"]["cet1_ratio"], 0.12)
        self.assertGreater(rwa["data"]["total_rwa"], 0)
        self.assertEqual(ecl["data"]["expected_credit_loss"], 8_000)
        self.assertEqual(stage["data"]["stage"], 2)
        self.assertEqual(stage["meta"]["fincept_credits"], 0)

    def test_black_scholes_price_is_deterministic(self):
        mod = load_module()
        answer = mod.execute(
            "pricing/bs/price",
            {"spot": 100, "strike": 105, "risk_free_rate": 0.05,
             "volatility": 0.2, "time_to_maturity": 1.0, "option_type": "call"},
        )
        self.assertTrue(answer["supported"])
        self.assertAlmostEqual(answer["data"]["price"], 8.021352, places=5)
        self.assertEqual(answer["meta"]["fincept_credits"], 0)

    def test_binary_and_binomial_option_routes_are_local(self):
        mod = load_module()
        base = {"spot": 100, "strike": 105, "risk_free_rate": 0.05,
                "volatility": 0.2, "time_to_maturity": 1.0}
        digital = mod.execute("pricing/bs/digital-call", base)
        european = mod.execute("pricing/binomial/european", {**base, "n_steps": 200, "option_type": "call"})
        american = mod.execute("pricing/binomial/american", {**base, "n_steps": 200, "option_type": "put"})
        self.assertTrue(digital["supported"])
        self.assertGreater(digital["data"]["price"], 0)
        self.assertAlmostEqual(european["data"]["price"], 8.02, delta=0.08)
        self.assertGreaterEqual(american["data"]["price"],
                                mod.execute("pricing/binomial/european", {**base, "n_steps": 200,
                                                                          "option_type": "put"})["data"]["price"])

    def test_all_black_scholes_binary_payoffs_are_local(self):
        mod = load_module()
        base = {"spot": 100, "strike": 105, "risk_free_rate": 0.05,
                "volatility": 0.2, "time_to_maturity": 1.0}
        digital_put = mod.execute("pricing/bs/digital-put", base)
        asset_call = mod.execute("pricing/bs/asset-or-nothing-call", base)
        asset_put = mod.execute("pricing/bs/asset-or-nothing-put", base)
        self.assertTrue(digital_put["supported"])
        self.assertTrue(asset_call["supported"])
        self.assertTrue(asset_put["supported"])
        self.assertGreater(digital_put["data"]["price"], 0)
        self.assertAlmostEqual(asset_call["data"]["price"] + asset_put["data"]["price"], 100.0, places=9)

    def test_black76_rate_option_routes_are_local(self):
        mod = load_module()
        caplet = mod.execute(
            "pricing/black76/caplet",
            {"forward_rate": 0.05, "discount_factor": 0.95, "volatility": 0.2,
             "t_start": 1.0, "t_end": 1.25, "strike": 0.048, "notional": 1_000_000},
        )
        floorlet = mod.execute(
            "pricing/black76/floorlet",
            {"forward_rate": 0.05, "discount_factor": 0.95, "volatility": 0.2,
             "t_start": 1.0, "t_end": 1.25, "strike": 0.052, "notional": 1_000_000},
        )
        swaption = mod.execute(
            "pricing/black76/swaption",
            {"forward_swap_rate": 0.05, "annuity": 4.5, "volatility": 0.2,
             "t_expiry": 1.0, "strike": 0.055, "notional": 1_000_000, "is_payer": True},
        )
        self.assertTrue(caplet["supported"] and floorlet["supported"] and swaption["supported"])
        self.assertGreater(caplet["data"]["price"], 0)
        self.assertGreater(floorlet["data"]["price"], 0)
        self.assertGreater(swaption["data"]["price"], 0)

    def test_bachelier_price_greeks_and_implied_volatility(self):
        mod = load_module()
        body = {"forward": 100, "strike": 105, "normal_volatility": 5.0,
                "time_to_maturity": 1.0, "risk_free_rate": 0.05, "option_type": "call"}
        price = mod.execute("pricing/bachelier/price", body)
        greeks = mod.execute("pricing/bachelier/greeks", body)
        implied = mod.execute("pricing/bachelier/implied-vol", {
            "forward": 100, "strike": 105, "time_to_maturity": 1.0,
            "risk_free_rate": 0.05, "option_type": "call", "market_price": price["data"]["price"]})
        self.assertGreater(price["data"]["price"], 0)
        self.assertIn("delta", greeks["data"])
        self.assertAlmostEqual(implied["data"]["implied_volatility"], 5.0, places=5)

    def test_solver_routes_cover_discount_and_forward_rates(self):
        mod = load_module()
        discount = mod.execute("solver/finance/discount-factor", {"rate": 0.05, "t": 1.0})
        forward = mod.execute("solver/finance/forward-rate", {"df1": 0.98, "df2": 0.95, "t1": 1, "t2": 2})
        self.assertAlmostEqual(discount["data"]["discount_factor"], math.exp(-0.05), places=10)
        self.assertAlmostEqual(forward["data"]["forward_rate"], -math.log(0.95 / 0.98), places=10)

    def test_core_money_rate_and_convention_routes(self):
        mod = load_module()
        money = mod.execute("core/types/money/create", {"amount": 100, "currency": "usd"})
        converted = mod.execute("core/types/money/convert",
                                {"amount": 100, "from_currency": "USD", "to_currency": "EUR", "rate": 0.92})
        rate = mod.execute("core/types/rate/convert", {"value": 5.0, "unit": "percentage"})
        spread = mod.execute("core/types/spread/from-bps", {"bps": 50})
        years = mod.execute("core/conventions/days-to-years", {"value": 360, "convention": "360"})
        days = mod.execute("core/conventions/years-to-days", {"value": 2, "convention": "business"})
        normal_rate = mod.execute("core/conventions/normalize-rate", {"value": 125, "unit": "bps"})
        normal_vol = mod.execute("core/conventions/normalize-volatility",
                                 {"value": 20, "vol_type": "lognormal", "is_percent": True})
        self.assertTrue(all(answer["supported"] for answer in
                            [money, converted, rate, spread, years, days, normal_rate, normal_vol]))
        self.assertEqual(money["data"], {"amount": 100.0, "currency": "USD"})
        self.assertEqual(converted["data"]["converted_amount"], 92.0)
        self.assertEqual(rate["data"], {"decimal": 0.05, "percentage": 5.0, "bps": 500.0})
        self.assertEqual(spread["data"]["decimal"], 0.005)
        self.assertEqual(years["data"]["years"], 1.0)
        self.assertEqual(days["data"]["days"], 504.0)
        self.assertEqual(normal_rate["data"]["value"], 0.0125)
        self.assertEqual(normal_vol["data"]["value"], 0.2)

    def test_core_date_tenor_and_notional_routes(self):
        mod = load_module()
        parsed = mod.execute("core/conventions/parse-date", {"date_string": "2024-01-15"})
        formatted = mod.execute("core/conventions/format-date",
                                {"date_str": "2024-01-15", "format": "%d/%m/%Y"})
        tenor = mod.execute("core/types/tenor/add-to-date", {"start_date": "2024-01-31", "tenor": "1M"})
        schedule = mod.execute("core/types/notional-schedule",
                               {"notional": 1_000_000, "periods": 4, "schedule_type": "amortizing"})
        self.assertTrue(all(answer["supported"] for answer in [parsed, formatted, tenor, schedule]))
        self.assertEqual(parsed["data"]["iso_date"], "2024-01-15")
        self.assertEqual(formatted["data"]["formatted_date"], "15/01/2024")
        self.assertEqual(tenor["data"]["date"], "2024-02-29")
        self.assertEqual(schedule["data"]["notionals"], [1_000_000, 750_000, 500_000, 250_000])

    def test_core_numeric_operations_are_local(self):
        mod = load_module()
        discounted = mod.execute(
            "core/ops/discount-cashflows",
            {"cashflows": [100, 100, 1100], "times": [1, 2, 3], "discount_factors": [0.95, 0.90, 0.86]},
        )
        interpolated = mod.execute(
            "core/ops/interpolate", {"x_data": [1, 2, 3, 4], "y_data": [1, 4, 9, 16], "x": 2.5},
        )
        percentile = mod.execute("core/ops/percentile", {"values": list(range(1, 11)), "p": 0.9})
        covariance = mod.execute(
            "core/ops/covariance-matrix",
            {"returns": [[0.01, 0.02], [0.03, -0.01], [0.02, 0.01], [-0.01, 0.03]]},
        )
        cholesky = mod.execute("core/ops/cholesky", {"matrix": [[4, 2], [2, 3]]})
        self.assertTrue(all(answer["supported"] for answer in
                            [discounted, interpolated, percentile, covariance, cholesky]))
        self.assertEqual(discounted["data"]["present_value"], 1131.0)
        self.assertEqual(interpolated["data"]["value"], 6.5)
        self.assertAlmostEqual(percentile["data"]["value"], 9.1)
        self.assertEqual(len(covariance["data"]["covariance_matrix"]), 2)
        self.assertAlmostEqual(cholesky["data"]["lower"][1][0], 1.0)
        self.assertAlmostEqual(cholesky["data"]["lower"][1][1], math.sqrt(2.0))

    def test_historical_var_route_reports_loss(self):
        mod = load_module()
        answer = mod.execute(
            "risk/var/historical",
            {"returns": [-0.04, -0.02, -0.01, 0.0, 0.01, 0.02], "confidence": 0.95},
        )
        self.assertTrue(answer["supported"])
        self.assertGreaterEqual(answer["data"]["var"], 0.02)
        self.assertGreaterEqual(answer["data"]["expected_shortfall"], answer["data"]["var"])

    def test_unknown_endpoint_is_explicitly_unsupported(self):
        mod = load_module()
        answer = mod.execute("models/not-a-real-model", {"spot": 100})
        self.assertFalse(answer["supported"])
        self.assertEqual(answer["meta"]["fincept_credits"], 0)
        self.assertIn("No paid Fincept request", answer["message"])

    def test_execute_converts_arithmetic_failures_to_structured_errors(self):
        mod = load_module()
        mod.ROUTES["test/arithmetic-error"] = lambda _body: 1 / 0
        answer = mod.execute("test/arithmetic-error", {})
        self.assertTrue(answer["supported"])
        self.assertIn("division by zero", answer.get("error", ""))
        self.assertEqual(answer["meta"]["fincept_credits"], 0)

    def test_curve_routes_interpolate_rates_and_discount_factors(self):
        mod = load_module()
        body = {"tenors": [1, 2, 5], "values": [0.04, 0.047, 0.05], "query_tenor": 3}
        zero = mod.execute("curves/zero-rate", body)
        discount = mod.execute("curves/discount-factor", body)
        self.assertAlmostEqual(zero["data"]["zero_rate"], 0.048, places=12)
        self.assertAlmostEqual(discount["data"]["discount_factor"], math.exp(-0.048 * 3), places=12)

    def test_fixed_bond_analytics_include_duration_and_dv01(self):
        mod = load_module()
        answer = mod.execute(
            "instruments/bond/fixed/analytics",
            {"issue_date": "2024-01-15", "maturity_date": "2029-01-15",
             "coupon_rate": 0.05, "yield_rate": 0.045},
        )
        self.assertTrue(answer["supported"])
        self.assertGreater(answer["data"]["price"], 100)
        self.assertGreater(answer["data"]["modified_duration"], 0)
        self.assertGreater(answer["data"]["dv01"], 0)

    def test_fixed_bond_yield_inverts_par_price(self):
        mod = load_module()
        answer = mod.execute(
            "instruments/bond/fixed/yield",
            {"issue_date": "2024-01-15", "maturity_date": "2029-01-15",
             "coupon_rate": 0.05, "clean_price": 100},
        )
        self.assertAlmostEqual(answer["data"]["yield_rate"], 0.05, places=5)

    def test_fixed_income_payment_schedules_are_bounded(self):
        mod = load_module()
        bond = {"issue_date": "2024-01-15", "maturity_date": "2029-01-15",
                "coupon_rate": 0.05, "yield_rate": 0.045}
        swap = {"effective_date": "2024-01-15", "maturity_date": "2029-01-15",
                "curve_tenors": [1, 5], "curve_rates": [0.04, 0.05]}
        cases = (
            ("instruments/bond/fixed/analytics", {**bond, "frequency": 0}),
            ("instruments/bond/fixed/cashflows", {**bond, "frequency": 367}),
            ("instruments/swap/irs/value", {**swap, "payments_per_year": 367}),
        )
        for route, body in cases:
            with self.subTest(route=route):
                answer = mod.execute(route, body)
                self.assertIn("payment schedule is outside local limits", answer.get("error", ""))
                self.assertEqual(answer["meta"]["fincept_credits"], 0)

    def test_fx_cds_and_zero_coupon_routes(self):
        mod = load_module()
        forward = mod.execute("instruments/fx/forward",
                              {"spot_rate": 1.1, "base_rate": 0.05, "quote_rate": 0.03,
                               "time_to_maturity": 1.0, "notional": 1_000_000})
        fx_option = mod.execute("instruments/fx/garman-kohlhagen",
                                {"spot": 100, "strike": 100, "domestic_rate": 0.05,
                                 "foreign_rate": 0.03, "volatility": 0.2, "time_to_expiry": 1.0})
        hazard = mod.execute("instruments/cds/hazard-rate", {"spread_bps": 100})
        survival = mod.execute("instruments/cds/survival-probability",
                               {"hazard_rate": 0.02, "time_horizon": 1.0})
        zero = mod.execute("instruments/bond/zero-coupon/price",
                           {"issue_date": "2024-01-15", "maturity_date": "2029-01-15", "yield_rate": 0.05})
        self.assertAlmostEqual(forward["data"]["forward_rate"], 1.1 * math.exp(0.02), places=10)
        self.assertGreater(fx_option["data"]["price"], 0)
        self.assertAlmostEqual(hazard["data"]["hazard_rate"], 0.01 / 0.6)
        self.assertAlmostEqual(survival["data"]["survival_probability"], math.exp(-0.02))
        self.assertLess(zero["data"]["price"], 100)

    def test_plain_vanilla_swap_routes_share_par_rate_math(self):
        mod = load_module()
        body = {"effective_date": "2024-01-15", "maturity_date": "2029-01-15", "notional": 1_000_000,
                "fixed_rate": 0.045, "curve_tenors": [0.25, 0.5, 1, 2, 5, 10],
                "curve_rates": [0.04, 0.042, 0.045, 0.047, 0.05, 0.052],
                "reference_date": "2024-01-15"}
        par = mod.execute("instruments/swap/irs/par-rate", body)
        value = mod.execute("instruments/swap/irs/value", body)
        dv01 = mod.execute("instruments/swap/irs/dv01", body)
        self.assertGreater(par["data"]["par_rate"], 0)
        self.assertAlmostEqual(value["data"]["value"],
                               body["notional"] * (par["data"]["par_rate"] - body["fixed_rate"]) *
                               value["data"]["annuity"], places=6)
        self.assertGreater(dv01["data"]["dv01"], 0)

    def test_minimum_variance_weights_are_long_only_and_normalized(self):
        mod = load_module()
        covariance = [[0.04, 0.006, 0.004], [0.006, 0.09, 0.012], [0.004, 0.012, 0.0625]]
        answer = mod.execute(
            "portfolio/optimize/min-variance",
            {"expected_returns": [0.08, 0.10, 0.12], "covariance_matrix": covariance},
        )
        weights = answer["data"]["weights"]
        self.assertAlmostEqual(sum(weights), 1.0, places=10)
        self.assertTrue(all(weight >= 0 for weight in weights))
        self.assertLess(answer["data"]["volatility"], 0.2)

    def test_portfolio_var_accepts_screen_covariance_schema(self):
        mod = load_module()
        answer = mod.execute(
            "portfolio/risk/var",
            {"weights": [0.4, 0.3, 0.3],
             "covariance_matrix": [[0.04, 0.006, 0.004], [0.006, 0.09, 0.012], [0.004, 0.012, 0.0625]],
             "confidence": 0.95},
        )
        self.assertTrue(answer["supported"])
        self.assertGreater(answer["data"]["var"], 0)

    def test_portfolio_risk_contribution_and_ratios(self):
        mod = load_module()
        contribution = mod.execute(
            "portfolio/risk/contribution",
            {"weights": [0.4, 0.3, 0.3],
             "covariance_matrix": [[0.04, 0.006, 0.004], [0.006, 0.09, 0.012], [0.004, 0.012, 0.0625]]},
        )
        ratios = mod.execute("portfolio/risk/ratios", {"returns": [0.01, -0.02, 0.03, 0.01, -0.005] * 30})
        self.assertAlmostEqual(sum(contribution["data"]["percentage_contributions"]), 1.0, places=10)
        self.assertIn("sharpe_ratio", ratios["data"])
        self.assertIn("sortino_ratio", ratios["data"])
        self.assertIn("max_drawdown", ratios["data"])

    def test_portfolio_risk_annualization_is_positive_and_finite(self):
        mod = load_module()
        body = {"returns": [0.01, -0.02, 0.03]}
        for periods in (0, -252, float("nan")):
            with self.subTest(periods=periods):
                answer = mod.execute("portfolio/risk/ratios", {**body, "periods_per_year": periods})
                self.assertIn("periods_per_year must be positive and finite", answer.get("error", ""))
                self.assertEqual(answer["meta"]["fincept_credits"], 0)

    def test_efficient_frontier_returns_normalized_portfolios(self):
        mod = load_module()
        answer = mod.execute(
            "portfolio/optimize/efficient-frontier",
            {"expected_returns": [0.08, 0.10, 0.12],
             "covariance_matrix": [[0.04, 0.006, 0.004], [0.006, 0.09, 0.012], [0.004, 0.012, 0.0625]]},
        )
        points = answer["data"]["frontier"]
        self.assertGreaterEqual(len(points), 5)
        self.assertTrue(all(abs(sum(point["weights"]) - 1) < 1e-9 for point in points))

    def test_gbm_simulation_is_reproducible(self):
        mod = load_module()
        body = {"S0": 100, "mu": 0.05, "sigma": 0.2, "T": 1, "n_steps": 4, "n_paths": 2, "seed": 7}
        first = mod.execute("stochastic/gbm/simulate", body)
        second = mod.execute("stochastic/gbm/simulate", body)
        self.assertEqual(first["data"]["paths"], second["data"]["paths"])
        self.assertEqual(len(first["data"]["paths"]), 2)
        self.assertEqual(len(first["data"]["paths"][0]), 5)

    def test_ou_simulation_and_cir_bond_price_are_local(self):
        mod = load_module()
        ou = mod.execute(
            "stochastic/ou/simulate",
            {"X0": 0, "kappa": 2, "theta": 0.05, "sigma": 0.1,
             "T": 1, "n_steps": 4, "n_paths": 2, "seed": 9},
        )
        cir = mod.execute(
            "stochastic/cir/bond-price",
            {"r0": 0.05, "kappa": 1.5, "theta": 0.04, "sigma": 0.1, "T": 5},
        )
        self.assertEqual(len(ou["data"]["paths"][0]), 5)
        self.assertGreater(cir["data"]["bond_price"], 0)
        self.assertLess(cir["data"]["bond_price"], 1)

    def test_all_stochastic_routes_are_local_and_zero_credit(self):
        mod = load_module()
        routes = {
            "stochastic/gbm/simulate", "stochastic/gbm/properties",
            "stochastic/ou/simulate", "stochastic/cir/simulate",
            "stochastic/cir/bond-price", "stochastic/heston/simulate",
            "stochastic/merton/simulate", "stochastic/vasicek/simulate",
            "stochastic/vasicek/bond-price", "stochastic/wiener/simulate",
            "stochastic/poisson/simulate", "stochastic/variance-gamma/simulate",
            "stochastic/brownian-bridge/simulate", "stochastic/correlated-bm/simulate",
            "stochastic/exact/gbm", "stochastic/exact/ou", "stochastic/exact/cir",
            "stochastic/exact/heston", "stochastic/simulation/euler-maruyama",
            "stochastic/simulation/milstein", "stochastic/simulation/euler-maruyama-nd",
            "stochastic/simulation/milstein-nd", "stochastic/simulation/multilevel-mc",
            "stochastic/sampling/sobol", "stochastic/sampling/antithetic",
            "stochastic/sampling/correlated-normals", "stochastic/sampling/multivariate-normal",
            "stochastic/sampling/distribution", "stochastic/sampling/jump",
            "stochastic/theory/ito-lemma", "stochastic/theory/ito-product-rule",
            "stochastic/theory/quadratic-variation", "stochastic/theory/covariation",
            "stochastic/theory/martingale-test", "stochastic/theory/girsanov/measure-change",
            "stochastic/theory/girsanov/risk-neutral-drift",
        }
        self.assertTrue(routes.issubset(mod.ROUTES))

        cir = mod.execute("stochastic/cir/simulate", {
            "r0": 0.05, "kappa": 1.5, "theta": 0.04, "sigma": 0.1,
            "T": 1, "n_steps": 4, "n_paths": 2, "seed": 7,
        })
        heston = mod.execute("stochastic/heston/simulate", {
            "S0": 100, "v0": 0.04, "r": 0.05, "kappa": 1.5, "theta": 0.04,
            "sigma_v": 0.3, "rho": -0.7, "T": 1, "n_steps": 4, "n_paths": 2, "seed": 7,
        })
        bridge = mod.execute("stochastic/brownian-bridge/simulate", {
            "T": 1, "a": 0, "b": 1, "n_steps": 8, "seed": 7,
        })
        qv = mod.execute("stochastic/theory/quadratic-variation", {
            "path": [100, 101, 99, 102], "times": [0, 0.3, 0.7, 1],
        })
        drift = mod.execute("stochastic/theory/girsanov/risk-neutral-drift", {
            "mu": 0.05, "r": 0.02, "sigma": 0.2,
        })

        self.assertEqual(len(cir["data"]["paths"]), 2)
        self.assertTrue(all(value >= 0 for path in cir["data"]["paths"] for value in path))
        self.assertEqual(len(heston["data"]["asset_paths"][0]), 5)
        self.assertEqual(bridge["data"]["path"][0], 0)
        self.assertEqual(bridge["data"]["path"][-1], 1)
        self.assertEqual(qv["data"]["quadratic_variation"], 14)
        self.assertAlmostEqual(drift["data"]["market_price_of_risk"], 0.15)
        for answer in (cir, heston, bridge, qv, drift):
            self.assertTrue(answer["supported"])
            self.assertEqual(answer["meta"]["fincept_credits"], 0)
            self.assertFalse(answer["meta"]["llm_used"])

    def test_stochastic_sample_generators_enforce_local_size_limits(self):
        mod = load_module()
        cases = [
            ("stochastic/exact/ou", {"X0": 0, "kappa": 2, "theta": 0.05, "sigma": 0.1, "T": 1}),
            ("stochastic/exact/cir", {"r0": 0.05, "kappa": 2, "theta": 0.05, "sigma": 0.1, "T": 1}),
            ("stochastic/sampling/antithetic", {}),
            ("stochastic/sampling/jump", {"jump_mean": 0, "jump_std": 0.1}),
        ]
        for route, body in cases:
            with self.subTest(route=route):
                answer = mod.execute(route, {**body, "n_paths": 0, "n": 0})
                self.assertIn("outside local safety limits", answer["error"])

    def test_all_economics_routes_are_local_and_zero_credit(self):
        mod = load_module()
        routes = {
            "economics/equilibrium/cobb-douglas", "economics/equilibrium/ces",
            "economics/equilibrium/walrasian", "economics/equilibrium/exchange-economy",
            "economics/games/create", "economics/games/classic", "economics/games/mixed-nash",
            "economics/games/nash-check", "economics/games/best-response",
            "economics/games/eliminate-dominated", "economics/games/fictitious-play",
            "economics/auctions/run", "economics/auctions/simulate",
            "economics/auctions/equilibrium-bid", "economics/auctions/expected-revenue",
            "economics/utility/cara", "economics/utility/crra", "economics/utility/log",
            "economics/utility/quadratic", "economics/utility/expected-utility",
            "economics/utility/certainty-equivalent",
            "economics/utility/certainty-equivalent-approximation",
            "economics/utility/risk-premium", "economics/utility/prospect-theory",
            "economics/utility/stochastic-dominance",
        }
        self.assertTrue(routes.issubset(mod.ROUTES))

        demand = mod.execute("economics/equilibrium/cobb-douglas", {
            "alphas": [0.5, 0.5], "prices": [1, 2], "income": 100,
        })
        nash = mod.execute("economics/games/mixed-nash", {
            "payoff_matrix_1": [[3, 0], [5, 1]], "payoff_matrix_2": [[3, 5], [0, 1]],
        })
        auction = mod.execute("economics/auctions/run", {
            "auction_type": "first_price", "n_bidders": 5, "valuations": [120, 105, 95, 80, 60],
        })
        certainty = mod.execute("economics/utility/certainty-equivalent", {
            "utility_type": "crra", "outcomes": [100, 50, 10], "param": 2,
            "probabilities": [0.5, 0.3, 0.2],
        })
        dominance = mod.execute("economics/utility/stochastic-dominance", {
            "cdf_a": [[0, 0.1], [1, 0.4], [2, 1]],
            "cdf_b": [[0, 0.2], [1, 0.6], [2, 1]], "order": 1,
        })

        self.assertAlmostEqual(sum(price * quantity for price, quantity in zip([1, 2], demand["data"]["demand"])), 100)
        self.assertAlmostEqual(sum(nash["data"]["strategy_1"]), 1)
        self.assertAlmostEqual(sum(nash["data"]["strategy_2"]), 1)
        self.assertEqual(auction["data"]["winner"], 0)
        self.assertLess(certainty["data"]["certainty_equivalent"], certainty["data"]["expected_outcome"])
        self.assertEqual(dominance["data"]["dominant"], "a")
        for answer in (demand, nash, auction, certainty, dominance):
            self.assertTrue(answer["supported"])
            self.assertEqual(answer["meta"]["fincept_credits"], 0)
            self.assertFalse(answer["meta"]["llm_used"])

    def test_all_physics_routes_are_local_and_zero_credit(self):
        mod = load_module()
        routes = {
            "physics/entropy/shannon", "physics/entropy/renyi", "physics/entropy/tsallis",
            "physics/entropy/cross", "physics/entropy/conditional", "physics/entropy/joint",
            "physics/entropy/differential", "physics/entropy/mutual-information",
            "physics/entropy/transfer", "physics/entropy/fisher-information",
            "physics/entropy/markov-rate", "physics/divergence/kl", "physics/divergence/js",
            "physics/boltzmann", "physics/max-entropy", "physics/ising",
            "physics/ising/critical-temperature", "physics/thermodynamics/ideal-gas",
            "physics/thermodynamics/van-der-waals", "physics/thermodynamics/carnot",
            "physics/thermodynamics/free-energy", "physics/thermodynamics/clausius-clapeyron",
            "physics/thermodynamics/maxwell-relations", "physics/thermodynamics/joule-thomson",
        }
        self.assertTrue(routes.issubset(mod.ROUTES))

        entropy = mod.execute("physics/entropy/shannon", {"probabilities": [0.5, 0.5]})
        divergence = mod.execute("physics/divergence/kl", {
            "p": [0.4, 0.3, 0.3], "q": [0.3, 0.4, 0.3],
        })
        carnot = mod.execute("physics/thermodynamics/carnot", {"T_hot": 500, "T_cold": 300})
        ideal = mod.execute("physics/thermodynamics/ideal-gas", {
            "n": 1, "temperature": 300, "volume": 1,
        })
        critical = mod.execute("physics/ising/critical-temperature", {"J": 1})

        self.assertAlmostEqual(entropy["data"]["entropy"], math.log(2))
        self.assertGreater(divergence["data"]["divergence"], 0)
        self.assertAlmostEqual(carnot["data"]["efficiency"], 0.4)
        self.assertAlmostEqual(ideal["data"]["pressure"], 8.31446261815324 * 300)
        self.assertAlmostEqual(critical["data"]["critical_temperature"], 2.269185314213022)
        for answer in (entropy, divergence, carnot, ideal, critical):
            self.assertTrue(answer["supported"])
            self.assertEqual(answer["meta"]["fincept_credits"], 0)
            self.assertFalse(answer["meta"]["llm_used"])

    def test_auction_simulation_bounds_bidder_fanout(self):
        mod = load_module()
        answer = mod.execute("economics/auctions/simulate", {
            "auction_type": "first_price", "n_bidders": 1001, "n_simulations": 1,
        })
        self.assertIn("auction simulation sizes are outside local limits", answer.get("error", ""))
        self.assertEqual(answer["meta"]["fincept_credits"], 0)

    def test_all_statistics_routes_are_local_and_zero_credit(self):
        mod = load_module()
        distribution_routes = {
            f"statistics/distributions/{name}/{operation}"
            for name, operations in {
                "normal": ("cdf", "pdf", "ppf", "properties"),
                "lognormal": ("cdf", "pdf", "ppf", "properties"),
                "student-t": ("cdf", "pdf", "properties"),
                "chi-squared": ("cdf", "pdf", "properties"),
                "f": ("pdf", "properties"), "gamma": ("cdf", "pdf", "properties"),
                "beta": ("cdf", "pdf", "properties"),
                "exponential": ("cdf", "pdf", "ppf", "properties"),
                "poisson": ("cdf", "pmf", "properties"),
                "binomial": ("cdf", "pmf", "properties"),
                "geometric": ("cdf", "pmf", "ppf", "properties"),
                "hypergeometric": ("cdf", "pmf", "properties"),
                "negative-binomial": ("cdf", "pmf", "properties"),
            }.items() for operation in operations
        }
        routes = distribution_routes | {
            "statistics/distributions/pgf", "statistics/timeseries/ar/fit",
            "statistics/timeseries/ar/forecast", "statistics/timeseries/arima/fit",
            "statistics/timeseries/arima/forecast", "statistics/timeseries/ma/fit",
            "statistics/timeseries/garch/fit", "statistics/timeseries/garch/forecast",
            "statistics/timeseries/egarch/fit", "statistics/timeseries/gjr-garch/fit",
        }
        self.assertEqual(len(routes), 52)
        self.assertTrue(routes.issubset(mod.ROUTES))

        normal = mod.execute("statistics/distributions/normal/cdf", {"mu": 0, "sigma": 1, "x": 0})
        binomial = mod.execute("statistics/distributions/binomial/pmf", {"n": 10, "p": 0.3, "k": 3})
        beta = mod.execute("statistics/distributions/beta/cdf", {"alpha": 2, "beta": 5, "x": 0.3})
        data = [0.01, -0.02, 0.015, 0.005, -0.01, 0.02, -0.005, 0.012] * 8
        ar = mod.execute("statistics/timeseries/ar/forecast", {"data": data, "order": 2, "steps": 4})
        garch = mod.execute("statistics/timeseries/garch/fit", {"returns": data})
        garch_forecast = mod.execute("statistics/timeseries/garch/forecast", {"returns": data, "steps": 1})

        self.assertAlmostEqual(normal["data"]["cdf"], 0.5)
        self.assertAlmostEqual(binomial["data"]["pmf"], math.comb(10, 3) * 0.3 ** 3 * 0.7 ** 7)
        self.assertGreater(beta["data"]["cdf"], 0)
        self.assertLess(beta["data"]["cdf"], 1)
        self.assertEqual(len(ar["data"]["forecast"]), 4)
        self.assertTrue(all(value > 0 for value in garch["data"]["conditional_variance"]))
        expected_variance = (garch["data"]["omega"] +
                             garch["data"]["alpha"] * garch["data"]["residuals"][-1] ** 2 +
                             garch["data"]["beta"] * garch["data"]["conditional_variance"][-1])
        self.assertAlmostEqual(garch_forecast["data"]["variance_forecast"][0], expected_variance)
        for answer in (normal, binomial, beta, ar, garch):
            self.assertTrue(answer["supported"])
            self.assertEqual(answer["meta"]["fincept_credits"], 0)
            self.assertFalse(answer["meta"]["llm_used"])

    def test_all_ml_routes_are_local_and_zero_credit(self):
        mod = load_module()
        routes = {
            "ml/credit/scorecard", "ml/credit/logistic-regression", "ml/credit/woe-binning",
            "ml/credit/calibration", "ml/credit/discrimination", "ml/credit/performance",
            "ml/credit/migration", "ml/credit/beta-lgd", "ml/credit/two-stage-lgd",
            "ml/regression/fit", "ml/regression/ensemble", "ml/regression/tree",
            "ml/regression/lgd", "ml/regression/ead", "ml/clustering/kmeans",
            "ml/clustering/hierarchical", "ml/clustering/dbscan", "ml/clustering/pca",
            "ml/clustering/isolation-forest", "ml/preprocessing/scale",
            "ml/preprocessing/transform", "ml/preprocessing/outliers",
            "ml/preprocessing/winsorize", "ml/preprocessing/stationarity",
            "ml/features/lags", "ml/features/rolling", "ml/features/calendar",
            "ml/features/technical", "ml/features/financial-ratios",
            "ml/features/cross-sectional", "ml/validation/stability",
            "ml/validation/discrimination-report", "ml/validation/calibration-report",
            "ml/validation/interpretability", "ml/timeseries/feature-importance",
            "ml/changepoint/detect", "ml/gp/curve", "ml/gp/vol-surface",
            "ml/nn/curve", "ml/nn/vol-surface", "ml/nn/portfolio", "ml/hmm/fit",
            "ml/garch-hybrid", "ml/metrics/regression", "ml/metrics/classification",
            "ml/factor/statistical", "ml/factor/cross-sectional", "ml/covariance/estimate",
        }
        self.assertEqual(len(routes), 48)
        self.assertTrue(routes.issubset(mod.ROUTES))

        X = [[1, 2], [2, 1], [3, 4], [4, 3], [5, 6], [6, 5], [7, 8], [8, 7]]
        y = [0, 1, 0, 1, 1, 0, 1, 0]
        logistic = mod.execute("ml/credit/logistic-regression", {"X": X, "y": y})
        scaled = mod.execute("ml/preprocessing/scale", {"X": X})
        clusters = mod.execute("ml/clustering/kmeans", {"X": X, "n_clusters": 2})
        metrics = mod.execute("ml/metrics/classification", {"y_true": y, "y_pred": y})
        returns = [[0.01, 0.02], [-0.01, 0.005], [0.02, -0.01], [0.005, 0.015]]
        covariance = mod.execute("ml/covariance/estimate", {"returns": returns})

        self.assertEqual(len(logistic["data"]["probabilities"]), len(y))
        self.assertTrue(all(0 <= value <= 1 for value in logistic["data"]["probabilities"]))
        self.assertTrue(all(abs(sum(row[index] for row in scaled["data"]["scaled"]) / len(X)) < 1e-10
                            for index in range(2)))
        self.assertEqual(len(clusters["data"]["labels"]), len(X))
        self.assertEqual(metrics["data"]["accuracy"], 1)
        self.assertAlmostEqual(covariance["data"]["covariance_matrix"][0][1],
                               covariance["data"]["covariance_matrix"][1][0])
        for answer in (logistic, scaled, clusters, metrics, covariance):
            self.assertTrue(answer["supported"])
            self.assertEqual(answer["meta"]["fincept_credits"], 0)
            self.assertFalse(answer["meta"]["llm_used"])

    def test_discrete_distribution_counts_are_bounded(self):
        mod = load_module()
        cases = (
            ("statistics/distributions/poisson/cdf", {"lam": 3, "k": 10001}),
            ("statistics/distributions/binomial/pmf", {"n": 10001, "p": 0.3, "k": 3}),
            ("statistics/distributions/hypergeometric/pmf",
             {"N": 10001, "K": 5000, "n": 100, "k": 50}),
            ("statistics/distributions/negative-binomial/cdf", {"r": 2, "p": 0.3, "k": 10001}),
        )
        for route, body in cases:
            with self.subTest(route=route):
                answer = mod.execute(route, body)
                self.assertIn("outside local safety limits", answer.get("error", ""))
                self.assertEqual(answer["meta"]["fincept_credits"], 0)

    def test_logistic_iterations_are_bounded(self):
        mod = load_module()
        body = {"X": [[0], [1]], "y": [0, 1]}
        for iterations in (0, 10001):
            with self.subTest(iterations=iterations):
                answer = mod.execute("ml/credit/logistic-regression",
                                     {**body, "iterations": iterations})
                self.assertIn("iterations are outside local limits", answer.get("error", ""))
                self.assertEqual(answer["meta"]["fincept_credits"], 0)

    def test_arima_forecast_steps_are_bounded(self):
        mod = load_module()
        body = {"data": [0.01, 0.02, 0.015, 0.025, 0.02] * 4, "p": 1, "d": 1, "q": 0}
        for steps in (0, 10001):
            with self.subTest(steps=steps):
                answer = mod.execute("statistics/timeseries/arima/forecast", {**body, "steps": steps})
                self.assertIn("forecast steps are outside local limits", answer.get("error", ""))
                self.assertEqual(answer["meta"]["fincept_credits"], 0)

    def test_credit_metric_bins_are_bounded(self):
        mod = load_module()
        cases = (
            ("ml/credit/woe-binning", {"feature": [1, 2, 3, 4], "target": [0, 1, 0, 1]}),
            ("ml/credit/calibration", {"y_true": [0, 1, 0, 1], "y_score": [0.1, 0.9, 0.2, 0.8]}),
        )
        for route, body in cases:
            for bins in (0, 101):
                with self.subTest(route=route, bins=bins):
                    answer = mod.execute(route, {**body, "n_bins": bins})
                    self.assertIn("n_bins is outside local limits", answer.get("error", ""))
                    self.assertEqual(answer["meta"]["fincept_credits"], 0)

    def test_sabr_and_flat_surface_routes_return_volatility(self):
        mod = load_module()
        sabr = mod.execute(
            "volatility/sabr/implied-vol",
            {"forward": 100, "strike": 100, "expiry": 1, "alpha": 0.2,
             "beta": 0.5, "rho": -0.5, "nu": 0.4},
        )
        flat = mod.execute("volatility/surface/flat", {"volatility": 0.2})
        self.assertGreater(sabr["data"]["implied_volatility"], 0)
        self.assertLess(sabr["data"]["implied_volatility"], 1)
        self.assertEqual(flat["data"]["volatility"], 0.2)

    def test_screen_financial_ratio_schema_and_dcf_routes(self):
        mod = load_module()
        pe = mod.execute("analysis/ratios/valuation/pe", {"market_price": 50, "earnings_per_share": 2.5})
        quick = mod.execute("analysis/ratios/liquidity/quick-ratio",
                            {"current_assets": 800, "inventory": 250, "prepaid_expenses": 20,
                             "current_liabilities": 400})
        fcff = mod.execute("analysis/valuation/dcf/fcff",
                           {"fcff": [100, 110, 121], "wacc": 0.10})
        ddm = mod.execute("analysis/valuation/dcf/ddm",
                          {"dividend": 2, "growth_rate": 0.03, "required_return": 0.10})
        cost = mod.execute("analysis/valuation/dcf/cost-of-equity",
                           {"risk_free_rate": 0.04, "beta": 0.8, "market_premium": 0.055})
        self.assertEqual(pe["data"]["value"], 20)
        self.assertAlmostEqual(quick["data"]["value"], 1.325)
        self.assertGreater(fcff["data"]["present_value"], 0)
        self.assertAlmostEqual(ddm["data"]["value"], 2.06 / 0.07)
        self.assertAlmostEqual(cost["data"]["cost_of_equity"], 0.084)

    def test_volatility_grid_and_term_structure_interpolate(self):
        mod = load_module()
        grid = mod.execute(
            "volatility/surface/grid",
            {"expiries": [0.5, 1.0], "strikes": [90, 100, 110],
             "vol_matrix": [[0.25, 0.22, 0.24], [0.23, 0.20, 0.22]],
             "query_expiry": 1.0, "query_strike": 100},
        )
        term = mod.execute(
            "volatility/surface/term-structure",
            {"expiries": [0.5, 1.0, 2.0], "volatilities": [0.22, 0.20, 0.21], "query_expiry": 1.5},
        )
        self.assertAlmostEqual(grid["data"]["volatility"], 0.20)
        self.assertAlmostEqual(term["data"]["volatility"], 0.205)

    def test_high_value_financial_ratios(self):
        mod = load_module()
        current = mod.execute("analysis/ratios/liquidity/current-ratio",
                              {"current_assets": 250, "current_liabilities": 100})
        roe = mod.execute("analysis/ratios/profitability/roe", {"net_income": 24, "equity": 120})
        pe = mod.execute("analysis/ratios/valuation/pe", {"price": 80, "eps": 5})
        cagr = mod.execute("analysis/ratios/growth/cagr",
                           {"beginning_value": 100, "ending_value": 144, "years": 2})
        self.assertAlmostEqual(current["data"]["value"], 2.5)
        self.assertAlmostEqual(roe["data"]["value"], 0.2)
        self.assertAlmostEqual(pe["data"]["value"], 16.0)
        self.assertAlmostEqual(cagr["data"]["value"], 0.2)

    def test_cagr_requires_positive_finite_inputs(self):
        mod = load_module()
        cases = (
            {"beginning_value": 100, "ending_value": 144, "years": 0},
            {"beginning_value": 100, "ending_value": 144, "years": float("nan")},
            {"beginning_value": -100, "ending_value": 144, "years": 2},
        )
        for body in cases:
            with self.subTest(body=body):
                answer = mod.execute("analysis/ratios/growth/cagr", body)
                self.assertIn("CAGR inputs must be positive and finite", answer.get("error", ""))
                self.assertEqual(answer["meta"]["fincept_credits"], 0)

    def test_all_screen_financial_ratio_routes_are_local(self):
        mod = load_module()
        profitability = {
            "revenue": 1_000_000, "gross_profit": 400_000, "operating_income": 200_000,
            "ebit": 200_000, "ebitda": 250_000, "net_income": 150_000,
            "total_assets": 2_000_000, "total_equity": 1_000_000,
            "invested_capital": 1_500_000, "operating_cash_flow": 180_000,
        }
        liquidity = {
            "current_assets": 800_000, "current_liabilities": 400_000, "cash": 200_000,
            "marketable_securities": 50_000, "accounts_receivable": 150_000,
            "inventory": 250_000, "prepaid_expenses": 20_000, "operating_expenses": 800_000,
        }
        leverage = {
            "total_debt": 600_000, "total_equity": 1_000_000, "total_assets": 2_000_000,
            "total_liabilities": 1_000_000, "long_term_debt": 450_000, "ebit": 200_000,
            "ebitda": 250_000, "interest_expense": 20_000,
            "operating_cash_flow": 180_000, "total_capital": 1_600_000,
        }
        valuation = {
            "market_price": 50, "earnings_per_share": 2.5, "book_value_per_share": 25,
            "sales_per_share": 20, "cash_flow_per_share": 3.2, "dividends_per_share": 1,
            "earnings_growth_rate": 0.12, "enterprise_value": 3_600_000,
            "ebitda": 250_000, "revenue": 1_000_000, "free_cash_flow": 120_000,
            "market_value": 1_500_000, "replacement_cost": 1_200_000,
        }
        efficiency = {
            "revenue": 1_000_000, "cost_of_goods_sold": 600_000,
            "accounts_receivable": 150_000, "inventory": 250_000,
            "accounts_payable": 120_000, "total_assets": 2_000_000,
            "fixed_assets": 900_000, "working_capital": 330_000, "total_equity": 1_000_000,
        }
        cashflow = {
            "operating_cash_flow": 180_000, "net_income": 150_000, "total_debt": 600_000,
            "current_liabilities": 400_000, "capital_expenditures": 60_000,
            "dividends": 40_000, "depreciation": 50_000, "total_assets": 2_000_000,
            "revenue": 1_000_000,
        }
        quality = {"net_income": 150_000, "operating_cash_flow": 180_000,
                   "total_assets": 2_000_000, "earnings_history": [100, 110, 105, 120, 130, 128]}
        groups = {
            "profitability": (["roa", "roe", "roic", "gross-margin", "net-margin",
                               "ebitda-margin", "operating-margin", "roce", "basic-earning-power",
                               "cash-roa", "cash-roe", "cash-roic"], profitability),
            "liquidity": (["current-ratio", "quick-ratio", "cash-ratio", "absolute-liquidity",
                           "defensive-interval", "working-capital", "working-capital-ratio"], liquidity),
            "leverage": (["debt-to-equity", "interest-coverage", "debt-to-assets", "debt-to-capital",
                          "equity-ratio", "equity-multiplier", "ebitda-interest-coverage",
                          "cash-coverage", "net-debt-to-ebitda", "long-term-debt-to-equity",
                          "financial-leverage", "capitalization-ratio", "debt-service-coverage"], leverage),
            "valuation": (["pe", "pb", "ps", "ev-ebitda", "dividend-yield", "pcf", "pfcf", "peg",
                           "ev-revenue", "ev-fcf", "earnings-yield", "fcf-yield", "tobins-q"], valuation),
            "efficiency": (["asset-turnover", "fixed-asset-turnover", "inventory-turnover",
                            "receivables-turnover", "payables-turnover", "days-sales-outstanding",
                            "days-inventory-outstanding", "days-payables-outstanding",
                            "cash-conversion-cycle", "operating-cycle", "working-capital-turnover",
                            "equity-turnover"], efficiency),
            "growth": (["yoy", "cagr", "sustainable-growth", "internal-growth"],
                       {"current_value": 1_500_000, "previous_value": 1_000_000,
                        "retention_ratio": 0.6, "roe": 0.15, "roa": 0.075}),
            "cashflow": (["ocf-to-debt", "ocf-ratio", "ocf-margin", "fcf", "fcf-margin",
                          "cash-conversion-quality", "capex-to-ocf", "capex-to-depreciation",
                          "cash-coverage-of-dividends", "reinvestment-rate"], cashflow),
            "quality": (["accruals-ratio", "sloan-accruals", "earnings-persistence",
                         "earnings-variability", "cash-earnings", "quality-of-earnings"], quality),
        }
        for category, (names, body) in groups.items():
            for name in names:
                endpoint = f"analysis/ratios/{category}/{name}"
                with self.subTest(endpoint=endpoint):
                    answer = mod.execute(endpoint, body)
                    self.assertTrue(answer["supported"], endpoint)
                    self.assertNotIn("error", answer, endpoint)
                    self.assertIn("value", answer["data"], endpoint)
                    self.assertEqual(answer["meta"]["fincept_credits"], 0)
        self.assertAlmostEqual(mod.execute("analysis/ratios/profitability/roa", profitability)["data"]["value"], 0.075)
        self.assertAlmostEqual(mod.execute("analysis/ratios/liquidity/cash-ratio", liquidity)["data"]["value"], 0.625)
        self.assertAlmostEqual(mod.execute("analysis/ratios/leverage/debt-to-capital", leverage)["data"]["value"], 0.375)
        self.assertAlmostEqual(mod.execute("analysis/ratios/efficiency/cash-conversion-cycle", efficiency)["data"]["value"],
                               133.83333333333334)
        self.assertAlmostEqual(mod.execute("analysis/ratios/growth/cagr", groups["growth"][1])["data"]["value"], 0.5)
        self.assertAlmostEqual(mod.execute("analysis/ratios/cashflow/fcf", cashflow)["data"]["value"], 120_000)

    def test_special_ratio_routes_accept_screen_minimal_schemas(self):
        mod = load_module()
        cases = {
            "analysis/ratios/valuation/tobins-q": {"market_value": 1_500_000, "replacement_cost": 1_200_000},
            "analysis/ratios/growth/yoy": {"current_value": 150, "previous_value": 100},
            "analysis/ratios/growth/cagr": {"current_value": 150, "previous_value": 100},
            "analysis/ratios/growth/sustainable-growth": {"retention_ratio": 0.6, "roe": 0.15},
            "analysis/ratios/growth/internal-growth": {"retention_ratio": 0.6, "roa": 0.075},
            "analysis/ratios/cashflow/ocf-margin": {"operating_cash_flow": 180_000, "revenue": 1_000_000},
            "analysis/ratios/cashflow/fcf-margin": {
                "operating_cash_flow": 180_000, "capital_expenditures": 60_000, "revenue": 1_000_000},
            "analysis/ratios/quality/earnings-persistence": {"earnings_history": [100, 110, 105, 120, 130, 128]},
            "analysis/ratios/quality/earnings-variability": {"earnings_history": [100, 110, 105, 120, 130, 128]},
        }
        for endpoint, body in cases.items():
            with self.subTest(endpoint=endpoint):
                answer = mod.execute(endpoint, body)
                self.assertTrue(answer["supported"])
                self.assertNotIn("error", answer)
                self.assertTrue(math.isfinite(answer["data"]["value"]))

    def test_wacc_and_terminal_value(self):
        mod = load_module()
        wacc = mod.execute("analysis/valuation/dcf/wacc",
                           {"equity": 600, "debt": 400, "cost_of_equity": 0.12,
                            "cost_of_debt": 0.06, "tax_rate": 0.25})
        terminal = mod.execute("analysis/valuation/dcf/terminal-value",
                               {"cash_flow": 100, "growth_rate": 0.03, "discount_rate": 0.09})
        self.assertAlmostEqual(wacc["data"]["wacc"], 0.09)
        self.assertAlmostEqual(terminal["data"]["terminal_value"], 1716.6666666666667)

    def test_advanced_dcf_credit_and_predictive_routes_are_local(self):
        mod = load_module()
        gordon = mod.execute("analysis/valuation/dcf/gordon-growth",
                             {"dividend": 2, "growth_rate": 0.03, "required_return": 0.10})
        two_stage = mod.execute("analysis/valuation/dcf/two-stage",
                                {"fcff": [100, 110, 121], "wacc": 0.10})
        weighted_wacc = mod.execute("analysis/valuation/dcf/wacc",
                                    {"equity_weight": 0.6, "debt_weight": 0.4,
                                     "cost_of_equity": 0.10, "cost_of_debt": 0.05, "tax_rate": 0.25})
        spread = mod.execute("analysis/valuation/credit/spread-from-pd", {"pd": 0.02})
        loss = mod.execute("analysis/valuation/credit/expected-loss", {"pd": 0.02, "lgd": 0.4, "ead": 1_000_000})
        rating = mod.execute("analysis/valuation/credit/rating-pd", {"rating": "BBB"})
        altman = mod.execute("analysis/valuation/predictive/altman-z",
                             {"working_capital": 330_000, "total_assets": 2_000_000,
                              "retained_earnings": 500_000, "ebit": 200_000,
                              "market_cap": 3_000_000, "total_liabilities": 1_000_000,
                              "revenue": 1_000_000})
        springate = mod.execute("analysis/valuation/predictive/springate",
                                {"working_capital": 330_000, "total_assets": 2_000_000,
                                 "ebit": 200_000, "pretax_income": 185_000,
                                 "current_liabilities": 300_000, "revenue": 1_000_000})
        for answer in (gordon, two_stage, weighted_wacc, spread, loss, rating, altman, springate):
            self.assertTrue(answer["supported"])
            self.assertNotIn("error", answer)
            self.assertEqual(answer["meta"]["fincept_credits"], 0)
        self.assertAlmostEqual(gordon["data"]["value"], 2.06 / 0.07)
        self.assertAlmostEqual(weighted_wacc["data"]["wacc"], 0.075)
        self.assertAlmostEqual(loss["data"]["expected_loss"], 8_000)
        self.assertAlmostEqual(altman["data"]["score"], 3.178)

    def test_residual_income_and_startup_valuation_routes_are_local(self):
        mod = load_module()
        eva = mod.execute("analysis/valuation/residual-income/eva",
                          {"nopat": 150_000, "invested_capital": 1_000_000, "wacc": 0.09})
        residual = mod.execute("analysis/valuation/residual-income/ri",
                               {"net_income": 150_000, "equity": 1_000_000, "cost_of_equity": 0.10})
        valuation = mod.execute("analysis/valuation/residual-income/valuation",
                                {"book_value": 1_000_000, "residual_incomes": [50_000, 55_000, 60_000],
                                 "cost_of_equity": 0.10})
        venture = mod.execute("analysis/valuation/startup/vc-method",
                              {"investment": 1_000_000, "exit_value": 10_000_000,
                               "years": 5, "target_return": 0.30})
        chicago = mod.execute("analysis/valuation/startup/first-chicago",
                              {"scenarios": [{"name": "bull", "probability": 0.25, "revenue": 20_000_000,
                                               "margin": 0.25, "multiple": 10},
                                              {"name": "base", "probability": 0.5, "revenue": 10_000_000,
                                               "margin": 0.2, "multiple": 8},
                                              {"name": "bear", "probability": 0.25, "revenue": 3_000_000,
                                               "margin": 0.1, "multiple": 5}],
                               "discount_rate": 0.30, "years": 5})
        dilution = mod.execute("analysis/valuation/startup/dilution",
                               {"pre_money": 4_000_000, "investment": 1_000_000, "option_pool": 0.10})
        for answer in (eva, residual, valuation, venture, chicago, dilution):
            self.assertTrue(answer["supported"])
            self.assertNotIn("error", answer)
            self.assertEqual(answer["meta"]["fincept_credits"], 0)
        self.assertEqual(eva["data"]["value"], 60_000)
        self.assertEqual(residual["data"]["value"], 50_000)
        self.assertAlmostEqual(dilution["data"]["investor_ownership"], 0.20)

    def test_credit_structural_models_are_local(self):
        mod = load_module()
        merton = mod.execute("analysis/valuation/credit/merton-model",
                             {"equity_value": 5_000_000, "debt_face": 3_000_000,
                              "risk_free_rate": 0.04, "equity_volatility": 0.40})
        distance = mod.execute("analysis/valuation/credit/distance-to-default",
                               {"asset_value": 8_000_000, "debt": 3_000_000,
                                "asset_volatility": 0.25, "risk_free_rate": 0.04})
        for answer in (merton, distance):
            self.assertTrue(answer["supported"])
            self.assertNotIn("error", answer)
            self.assertEqual(answer["meta"]["fincept_credits"], 0)
            self.assertGreater(answer["data"]["distance_to_default"], 0)
            self.assertGreaterEqual(answer["data"]["default_probability"], 0)
            self.assertLessEqual(answer["data"]["default_probability"], 1)
        self.assertGreater(merton["data"]["asset_value"], 5_000_000)

    def test_sotp_and_berkus_valuation_routes_are_local(self):
        mod = load_module()
        sotp = mod.execute("analysis/valuation/segment/sotp",
                           {"segments": [{"name": "Cloud", "revenue": 500, "ebitda": 150, "ebit": 120,
                                          "comparable_ev_ebitda": 15},
                                         {"name": "Hardware", "revenue": 800, "ebitda": 120, "ebit": 80,
                                          "comparable_ev_ebitda": 8}],
                            "total_debt": 400, "cash": 150, "shares_outstanding": 100,
                            "wacc": 0.10, "terminal_growth": 0.02, "growth_rate": 0.04,
                            "tax_rate": 0.25, "forecast_years": 5})
        berkus = mod.execute("analysis/valuation/startup/berkus",
                             {"sound_idea": 400_000, "prototype": 300_000, "management": 500_000,
                              "strategic_relationships": 200_000, "product_rollout": 100_000})
        self.assertTrue(sotp["supported"] and berkus["supported"])
        self.assertNotIn("error", sotp)
        self.assertNotIn("error", berkus)
        self.assertAlmostEqual(sotp["data"]["comparable_enterprise_value"], 3210)
        self.assertAlmostEqual(sotp["data"]["comparable_value_per_share"], 29.6)
        self.assertEqual(berkus["data"]["value"], 1_500_000)

    def test_industry_analytics_routes_are_local(self):
        mod = load_module()
        cases = {
            "analysis/industry/banking": {
                "net_interest_income": 80_000, "earning_assets": 1_800_000, "total_assets": 2_000_000,
                "total_deposits": 1_500_000, "total_loans": 1_200_000, "non_performing_loans": 24_000,
                "loan_loss_provisions": 12_000, "operating_expenses": 70_000,
                "total_revenue": 120_000, "net_income": 28_500, "tier1_capital": 160_000,
                "total_capital": 190_000, "risk_weighted_assets": 1_400_000},
            "analysis/industry/insurance": {
                "net_premiums_earned": 500_000, "losses_incurred": 320_000,
                "loss_adjustment_expenses": 40_000, "underwriting_expenses": 110_000,
                "net_investment_income": 60_000, "total_assets": 2_000_000,
                "total_liabilities": 1_700_000, "policyholders_surplus": 300_000,
                "reserves": 700_000, "reinsurance_recoverables": 50_000,
                "ceded_premiums": 80_000, "gross_premiums": 580_000},
            "analysis/industry/reits": {
                "net_income": 90_000, "depreciation": 110_000, "capital_expenditures": 30_000,
                "leasing_costs": 10_000, "net_operating_income": 260_000, "total_revenue": 400_000,
                "total_debt": 1_200_000, "ebitda": 240_000, "interest_expense": 40_000,
                "share_price": 30, "shares_outstanding": 100_000, "dividends_per_share": 1.4,
                "property_value": 4_000_000},
            "analysis/industry/utilities": {
                "total_revenue": 240_000_000, "operating_expenses": 192_000_000,
                "fuel_costs": 70_000_000, "fuel_costs_recovered": 66_500_000,
                "ebitda": 78_000_000, "interest_expense": 16_000_000, "rate_base": 600_000_000,
                "total_debt": 330_000_000, "total_equity": 300_000_000,
                "capital_expenditures": 55_000_000, "kwh_generated": 2_628_000_000,
                "capacity_mw": 500, "peak_demand_mw": 450, "heat_rate_btu": 19_710_000_000_000},
        }
        answers = {endpoint: mod.execute(endpoint, body) for endpoint, body in cases.items()}
        for endpoint, answer in answers.items():
            self.assertTrue(answer["supported"], endpoint)
            self.assertNotIn("error", answer, endpoint)
            self.assertEqual(answer["meta"]["fincept_credits"], 0)
        self.assertAlmostEqual(answers["analysis/industry/banking"]["data"]["net_interest_margin"], 80_000 / 1_800_000)
        self.assertAlmostEqual(answers["analysis/industry/insurance"]["data"]["combined_ratio"], 0.94)
        self.assertEqual(answers["analysis/industry/reits"]["data"]["ffo"], 200_000)
        self.assertAlmostEqual(answers["analysis/industry/utilities"]["data"]["capacity_factor"], 0.6)

    def test_comparable_screen_and_factor_model_routes_are_local(self):
        mod = load_module()
        comparable = mod.execute("analysis/valuation/comparable", {
            "target": {"market_cap": 3_000_000, "net_income": 138_750, "total_debt": 550_000,
                       "cash": 150_000, "ebitda": 250_000, "book_value": 1_000_000,
                       "revenue": 1_000_000, "operating_cash_flow": 180_000},
            "peers": [{"market_cap": 2_500_000, "net_income": 125_000, "enterprise_value": 2_900_000,
                       "ebitda": 230_000, "book_value": 950_000, "revenue": 900_000,
                       "operating_cash_flow": 160_000},
                      {"market_cap": 4_200_000, "net_income": 175_000, "enterprise_value": 4_600_000,
                       "ebitda": 330_000, "book_value": 1_300_000, "revenue": 1_350_000,
                       "operating_cash_flow": 240_000},
                      {"market_cap": 1_800_000, "net_income": 110_000, "enterprise_value": 2_300_000,
                       "ebitda": 200_000, "book_value": 800_000, "revenue": 850_000,
                       "operating_cash_flow": 150_000}],
            "metrics": ["pe", "ev_ebitda", "pb", "ps", "ev_revenue", "pcf"]})
        screen = mod.execute("analysis/valuation/screen", {
            "companies": [{"name": "A", "pe": 12, "pb": 1.8, "roe": 0.15, "debt_to_equity": 0.6,
                           "current_ratio": 1.8, "revenue_growth": 0.06, "gross_margin": 0.35},
                          {"name": "B", "pe": 25, "pb": 5.5, "roe": 0.22, "debt_to_equity": 0.4,
                           "current_ratio": 2.2, "revenue_growth": 0.28, "gross_margin": 0.62},
                          {"name": "C", "pe": 9, "pb": 0.72, "roe": 0.08, "debt_to_equity": 1.4,
                           "current_ratio": 1.1, "revenue_growth": 0.02, "gross_margin": 0.22}],
            "screen_type": "value"})
        factors = mod.execute("analysis/valuation/factor-models", {
            "income": {"revenue": 1_000_000, "gross_profit": 400_000, "net_income": 138_750,
                       "dividends_paid": 40_000},
            "balance": {"total_assets": 2_000_000, "total_equity": 1_000_000},
            "market": {"market_cap": 3_000_000, "share_price": 30},
            "cash_flow": {"operating_cash_flow": 180_000},
            "prior_balance": {"total_assets": 1_905_000}})
        for answer in (comparable, screen, factors):
            self.assertTrue(answer["supported"])
            self.assertNotIn("error", answer)
            self.assertEqual(answer["meta"]["fincept_credits"], 0)
        self.assertEqual(len(comparable["data"]["metrics"]), 6)
        self.assertEqual(screen["data"]["ranked_companies"][0]["name"], "C")
        self.assertAlmostEqual(factors["data"]["profitability"], 0.13875)

    def test_predictive_financial_scores_are_local(self):
        mod = load_module()
        company = {
            "income": {"revenue": 1_000_000, "cost_of_goods_sold": 600_000, "gross_profit": 400_000,
                       "sga_expenses": 120_000, "depreciation": 40_000, "net_income": 138_750,
                       "shares_outstanding": 100_000},
            "balance": {"accounts_receivable": 150_000, "total_current_assets": 630_000,
                        "net_ppe": 900_000, "total_assets": 2_000_000,
                        "total_current_liabilities": 300_000, "long_term_debt": 450_000,
                        "total_liabilities": 1_000_000},
            "cash_flow": {"operating_cash_flow": 180_000},
            "prior_income": {"revenue": 925_000, "cost_of_goods_sold": 564_250,
                             "gross_profit": 360_750, "sga_expenses": 112_000,
                             "depreciation": 37_500, "net_income": 118_500,
                             "shares_outstanding": 100_700},
            "prior_balance": {"accounts_receivable": 140_000, "total_current_assets": 575_000,
                              "net_ppe": 870_000, "total_assets": 1_905_000,
                              "total_current_liabilities": 300_000, "long_term_debt": 460_000,
                              "total_liabilities": 983_750},
        }
        endpoints = ["piotroski-f", "beneish-m", "ohlson-o", "zmijewski"]
        answers = {name: mod.execute(f"analysis/valuation/predictive/{name}", company) for name in endpoints}
        for name, answer in answers.items():
            self.assertTrue(answer["supported"], name)
            self.assertNotIn("error", answer, name)
            self.assertTrue(math.isfinite(answer["data"]["score"]), name)
            self.assertEqual(answer["meta"]["fincept_credits"], 0)
        self.assertEqual(answers["piotroski-f"]["data"]["score"], 9)
        self.assertIn("probability", answers["ohlson-o"]["data"])
        self.assertIn("probability", answers["zmijewski"]["data"])

    def test_proforma_adjustments_are_local(self):
        mod = load_module()
        answer = mod.execute("analysis/valuation/proforma/adjustments", {
            "income": {"revenue": 1_000_000, "rd_expenses": 30_000, "net_income": 138_750,
                       "shares_outstanding": 100_000},
            "balance": {"total_assets": 2_000_000, "intangible_assets": 100_000,
                        "total_equity": 1_000_000},
            "rd_expenses_history": [22_000, 24_000, 25_500, 27_000, 30_000], "useful_life_rd": 5,
            "lease_payments": [24_000, 24_000, 22_000, 20_000, 18_000, 15_000, 12_000, 10_000],
            "discount_rate": 0.05, "goodwill": 200_000, "goodwill_approach": "conservative",
            "pension_obligation": 300_000, "pension_plan_assets": 255_000,
            "pension_service_cost": 9_000, "pension_interest_cost": 13_500,
            "pension_expected_return": 16_500, "pension_reported_expense": 10_000,
            "stock_compensation": 25_000, "diluted_shares": 102_000, "stock_price": 30,
            "tax_rate": 0.25})
        self.assertTrue(answer["supported"])
        self.assertNotIn("error", answer)
        self.assertEqual(answer["meta"]["fincept_credits"], 0)
        self.assertGreater(answer["data"]["capitalized_rd"], 0)
        self.assertGreater(answer["data"]["lease_liability"], 0)
        self.assertEqual(answer["data"]["pension_deficit"], 45_000)
        self.assertTrue(math.isfinite(answer["data"]["adjusted_eps"]))

    def test_all_fundamental_analysis_sections_are_local(self):
        mod = load_module()
        income = {"revenue": 1_000_000, "cost_of_goods_sold": 600_000, "gross_profit": 400_000,
                  "operating_expenses": 200_000, "operating_income": 200_000,
                  "interest_expense": 20_000, "pretax_income": 185_000, "income_tax": 46_250,
                  "net_income": 138_750, "shares_outstanding": 100_000, "dividends_paid": 40_000}
        prior_income = {"revenue": 925_000, "cost_of_goods_sold": 564_250, "gross_profit": 360_750,
                        "operating_income": 175_000, "interest_expense": 21_000,
                        "pretax_income": 158_000, "income_tax": 39_500, "net_income": 118_500,
                        "shares_outstanding": 100_700, "dividends_paid": 36_000}
        balance = {"cash": 150_000, "short_term_investments": 50_000,
                   "accounts_receivable": 150_000, "inventory": 250_000,
                   "total_current_assets": 630_000, "net_ppe": 900_000, "total_assets": 2_000_000,
                   "accounts_payable": 120_000, "short_term_debt": 50_000,
                   "current_portion_lt_debt": 50_000, "total_current_liabilities": 300_000,
                   "long_term_debt": 450_000, "total_liabilities": 1_000_000,
                   "total_equity": 1_000_000, "retained_earnings": 500_000}
        prior_balance = {"cash": 140_000, "short_term_investments": 30_000,
                         "accounts_receivable": 140_000, "inventory": 240_000,
                         "total_current_assets": 575_000, "net_ppe": 870_000,
                         "total_assets": 1_905_000, "accounts_payable": 115_000,
                         "short_term_debt": 60_000, "current_portion_lt_debt": 50_000,
                         "total_current_liabilities": 300_000, "long_term_debt": 460_000,
                         "total_liabilities": 983_750, "total_equity": 921_250,
                         "retained_earnings": 401_250}
        cash_flow = {"operating_cash_flow": 180_000, "capital_expenditures": 70_000,
                     "depreciation": 50_000, "change_in_working_capital": -15_000,
                     "investing_cash_flow": -90_000, "financing_cash_flow": -80_000,
                     "dividends_paid": 40_000, "debt_issued": 50_000, "debt_repaid": 70_000,
                     "shares_issued": 0, "shares_repurchased": 20_000, "net_cash_flow": 10_000}
        prior_cash_flow = {"operating_cash_flow": 160_000, "capital_expenditures": 65_000,
                           "depreciation": 46_750, "change_in_working_capital": -10_000,
                           "investing_cash_flow": -75_000, "financing_cash_flow": -75_000,
                           "dividends_paid": 36_000, "debt_issued": 40_000, "debt_repaid": 60_000,
                           "shares_issued": 0, "shares_repurchased": 19_000, "net_cash_flow": 10_000}
        standard = {"income": income, "balance": balance, "cash_flow": cash_flow,
                    "prior_income": prior_income, "prior_balance": prior_balance,
                    "market": {"share_price": 30, "shares_outstanding": 100_000}}
        cases = {
            "profitability": {"income": income, "balance": balance, "prior_balance": prior_balance},
            "liquidity": {"income": income, "balance": balance, "cash_flow": cash_flow,
                          "prior_balance": prior_balance},
            "efficiency": {"income": income, "balance": balance, "cash_flow": cash_flow,
                           "prior_balance": prior_balance},
            "growth": {"current_income": income, "prior_income": prior_income,
                       "current_balance": balance, "prior_balance": prior_balance,
                       "current_cash_flow": cash_flow, "prior_cash_flow": prior_cash_flow},
            "solvency": {"income": income, "balance": balance, "cash_flow": cash_flow},
            "cashflow": {"income": income, "balance": balance, "cash_flow": cash_flow,
                         "prior_balance": prior_balance},
            "comprehensive": standard,
            "dupont": {"income": income, "balance": balance, "prior_balance": prior_balance},
            "quality": standard,
        }
        answers = {name: mod.execute(f"analysis/fundamentals/{name}", body) for name, body in cases.items()}
        for name, answer in answers.items():
            self.assertTrue(answer["supported"], name)
            self.assertNotIn("error", answer, name)
            self.assertEqual(answer["meta"]["fincept_credits"], 0)
        self.assertAlmostEqual(answers["profitability"]["data"]["gross_margin"], 0.4)
        self.assertAlmostEqual(answers["liquidity"]["data"]["current_ratio"], 2.1)
        self.assertEqual(answers["cashflow"]["data"]["free_cash_flow"], 110_000)
        self.assertAlmostEqual(answers["growth"]["data"]["revenue_growth"], 75_000 / 925_000)
        self.assertAlmostEqual(answers["dupont"]["data"]["roe"],
                               answers["dupont"]["data"]["net_margin"] *
                               answers["dupont"]["data"]["asset_turnover"] *
                               answers["dupont"]["data"]["equity_multiplier"])
        self.assertIn("valuation", answers["comprehensive"]["data"])

    def test_fundamental_capital_structure_routes_are_local(self):
        mod = load_module()
        wacc = mod.execute("analysis/fundamentals/capital-structure/wacc",
                           {"debt": 600_000, "equity": 1_000_000})
        optimal = mod.execute("analysis/fundamentals/capital-structure/optimal",
                              {"cost_of_equity": 0.10, "cost_of_debt": 0.05, "tax_rate": 0.25,
                               "ebitda": 250_000, "debt": 600_000, "equity": 1_000_000})
        for answer in (wacc, optimal):
            self.assertTrue(answer["supported"])
            self.assertNotIn("error", answer)
            self.assertEqual(answer["meta"]["fincept_credits"], 0)
        self.assertGreater(wacc["data"]["wacc"], 0)
        self.assertLess(wacc["data"]["wacc"], 0.2)
        self.assertGreaterEqual(optimal["data"]["optimal_debt_ratio"], 0)
        self.assertLessEqual(optimal["data"]["optimal_debt_ratio"], 0.8)

    def test_target_return_and_black_litterman_routes_are_local(self):
        mod = load_module()
        covariance = [[0.04, 0.006, 0.004], [0.006, 0.09, 0.012], [0.004, 0.012, 0.0625]]
        target = mod.execute("portfolio/optimize/target-return",
                             {"expected_returns": [0.08, 0.10, 0.12],
                              "covariance_matrix": covariance, "target_return": 0.10})
        equilibrium = mod.execute("portfolio/black-litterman/equilibrium",
                                  {"covariance_matrix": covariance, "market_caps": [1000, 800, 600]})
        posterior = mod.execute("portfolio/black-litterman/posterior",
                                {"covariance_matrix": covariance, "market_caps": [1000, 800, 600]})
        for answer in (target, equilibrium, posterior):
            self.assertTrue(answer["supported"])
            self.assertNotIn("error", answer)
            self.assertEqual(answer["meta"]["fincept_credits"], 0)
        self.assertAlmostEqual(sum(target["data"]["weights"]), 1.0)
        self.assertAlmostEqual(target["data"]["expected_return"], 0.10, places=8)
        self.assertEqual(len(equilibrium["data"]["equilibrium_returns"]), 3)
        self.assertEqual(len(posterior["data"]["posterior_returns"]), 3)

    def test_advanced_portfolio_risk_routes_are_local(self):
        mod = load_module()
        covariance = [[0.04, 0.006, 0.004], [0.006, 0.09, 0.012], [0.004, 0.012, 0.0625]]
        weights = [0.4, 0.3, 0.3]
        returns_assets = [[0.010, -0.020, 0.015, 0.005], [0.020, 0.010, -0.005, 0.015],
                          [-0.010, 0.005, 0.010, -0.010]]
        incremental = mod.execute("portfolio/risk/incremental-var",
                                  {"weights": weights, "covariance_matrix": covariance,
                                   "asset_index": 0, "weight_change": 0.05, "confidence": 0.95})
        comprehensive = mod.execute("portfolio/risk/comprehensive",
                                    {"weights": weights, "returns": returns_assets,
                                     "covariance_matrix": covariance, "risk_free_rate": 0})
        observations = list(map(list, zip(*returns_assets)))
        portfolio_full = mod.execute("portfolio/risk/portfolio-comprehensive",
                                     {"returns": observations, "weights": weights})
        for answer in (incremental, comprehensive, portfolio_full):
            self.assertTrue(answer["supported"])
            self.assertNotIn("error", answer)
            self.assertEqual(answer["meta"]["fincept_credits"], 0)
        self.assertIn("var_change", incremental["data"])
        self.assertIn("risk_contributions", comprehensive["data"])
        self.assertIn("max_drawdown", portfolio_full["data"])

    def test_var_decomposition_optimization_and_backtest_routes_are_local(self):
        mod = load_module()
        portfolio_returns = [-0.013, 0.004, 0.011, -0.005, -0.014, 0.006, 0.012, -0.008,
                             0.003, -0.019, 0.008, -0.015, 0.002, 0.010, -0.006, 0.014]
        position_returns = [value * 0.8 + 0.001 for value in portfolio_returns]
        parametric = mod.execute("risk/var/parametric", {"portfolio_value": 1_000_000, "volatility": 0.2})
        component = mod.execute("risk/var/component",
                                {"returns_by_factor": {"equity": portfolio_returns,
                                                       "rates": position_returns},
                                 "weights": {"equity": 0.6, "rates": 0.4}})
        incremental = mod.execute("risk/var/incremental",
                                  {"portfolio_returns": portfolio_returns,
                                   "new_position_returns": position_returns, "position_weight": 0.1})
        marginal = mod.execute("risk/var/marginal",
                               {"portfolio_returns": portfolio_returns,
                                "new_position_returns": position_returns, "position_weight": 0.1})
        matrix = [[0.0088, 0.0055, 0.0185], [-0.0037, 0.0065, 0.006],
                  [0.0035, 0.0027, 0.0024], [-0.0062, -0.0108, -0.004],
                  [0.0069, 0.017, 0.0111], [0.012, 0.009, 0.0081]]
        optimized = mod.execute("risk/var/es-optimization", {"returns": matrix})
        backtest = mod.execute("risk/backtest",
                               {"actual_returns": portfolio_returns,
                                "var_estimates": [0.015] * len(portfolio_returns), "confidence": 0.95})
        for answer in (parametric, component, incremental, marginal, optimized, backtest):
            self.assertTrue(answer["supported"])
            self.assertNotIn("error", answer)
            self.assertEqual(answer["meta"]["fincept_credits"], 0)
        self.assertGreater(parametric["data"]["var"], 0)
        self.assertAlmostEqual(sum(component["data"]["percentage_contributions"]), 1.0)
        self.assertAlmostEqual(sum(optimized["data"]["weights"]), 1.0)
        self.assertEqual(backtest["data"]["observations"], len(portfolio_returns))

    def test_stress_and_tail_risk_routes_are_local(self):
        mod = load_module()
        returns = [-0.013, 0.004, 0.011, -0.005, -0.014, 0.006, 0.012, -0.008,
                   0.003, -0.019, 0.008, -0.015, 0.002, 0.010, -0.006, 0.014]
        scenario = mod.execute("risk/stress/scenario",
                               {"name": "scenario", "factor_shocks": {"equity": -0.2, "rates": 0.01}})
        correlation = mod.execute("risk/correlation-stress",
                                  {"correlation_matrix": [[1, 0.5, 0.3], [0.5, 1, 0.4], [0.3, 0.4, 1]]})
        tail = mod.execute("risk/tail-risk/comprehensive",
                           {"returns": returns, "confidence": 0.975, "portfolio_value": 1_000_000})
        for answer in (scenario, correlation, tail):
            self.assertTrue(answer["supported"])
            self.assertNotIn("error", answer)
            self.assertEqual(answer["meta"]["fincept_credits"], 0)
        self.assertEqual(scenario["data"]["name"], "scenario")
        self.assertEqual(len(correlation["data"]["stressed_correlation_matrix"]), 3)
        self.assertGreater(tail["data"]["expected_shortfall"], tail["data"]["var"])

    def test_evt_and_copula_routes_are_local_and_deterministic(self):
        mod = load_module()
        losses = [0.0131, 0.0052, 0.0142, 0.0125, 0.013, 0.0138, 0.0173, 0.0112,
                  0.0287, 0.0121, 0.0193, 0.0152, 0.0158, 0.0367, 0.0142, 0.0107,
                  0.021, 0.0109, 0.0114, 0.0143]
        returns = [-0.0131, 0.0046, 0.0113, -0.0052, -0.0142, -0.0003, 0.0057, 0.0125,
                   -0.013, -0.0138, 0.0082, 0.0173, -0.0026, 0.0287, -0.0193, 0.0367]
        gpd = mod.execute("risk/evt/gpd", {"data": losses, "quantile": 0.8})
        gev = mod.execute("risk/evt/gev", {"data": returns})
        hill = mod.execute("risk/evt/hill", {"data": losses, "k": 8})
        copula1 = mod.execute("risk/copula/sample", {"copula_type": "gaussian", "n_samples": 20})
        copula2 = mod.execute("risk/copula/sample", {"copula_type": "gaussian", "n_samples": 20})
        for answer in (gpd, gev, hill, copula1, copula2):
            self.assertTrue(answer["supported"])
            self.assertNotIn("error", answer)
            self.assertEqual(answer["meta"]["fincept_credits"], 0)
        self.assertGreater(gpd["data"]["scale"], 0)
        self.assertGreater(gev["data"]["scale"], 0)
        self.assertGreater(hill["data"]["tail_index"], 0)
        self.assertEqual(copula1["data"]["samples"], copula2["data"]["samples"])
        self.assertEqual(len(copula1["data"]["samples"]), 20)

    def test_exposure_hedge_and_xva_routes_are_local(self):
        mod = load_module()
        paths = [[0, 1, 2, 1, 0], [0, 2, 3, 2, 0], [0, 0.5, 1, 0.5, 0], [0, -1, -1.5, -1, 0]]
        exposure = mod.execute("risk/portfolio-risk/exposure-profile", {"paths": paths})
        hedge = mod.execute("risk/portfolio-risk/optimal-hedge",
                            {"portfolio_delta": 1000, "hedge_delta": 0.5})
        cva = mod.execute("risk/xva/cva",
                          {"exposure_paths": paths, "counterparty_spread": 100,
                           "own_spread": 60, "funding_spread": 40})
        pfe = mod.execute("risk/xva/pfe",
                          {"exposure_paths": paths, "confidence": 0.95, "counterparty_spread": 100})
        for answer in (exposure, hedge, cva, pfe):
            self.assertTrue(answer["supported"])
            self.assertNotIn("error", answer)
            self.assertEqual(answer["meta"]["fincept_credits"], 0)
        self.assertEqual(hedge["data"]["hedge_units"], -2000)
        self.assertEqual(len(exposure["data"]["expected_exposure"]), 5)
        self.assertGreater(cva["data"]["cva"], 0)
        self.assertEqual(len(pfe["data"]["pfe"]), 5)

    def test_all_sensitivity_routes_are_local(self):
        mod = load_module()
        option = {"S": 100, "K": 100, "T": 1, "r": 0.05, "sigma": 0.2, "q": 0}
        greeks = mod.execute("risk/sensitivities/greeks", option)
        cross = mod.execute("risk/sensitivities/cross-gamma", {**option, "bump": 0.01})
        curve = {"cashflows": [5, 5, 5, 5, 105], "times": [1, 2, 3, 4, 5],
                 "curve_rates": [0.04, 0.042, 0.045, 0.047, 0.05]}
        key_rate = mod.execute("risk/sensitivities/key-rate-duration", {**curve, "bump_size": 0.0001})
        parallel = mod.execute("risk/sensitivities/parallel-shift", curve)
        twist = mod.execute("risk/sensitivities/twist", curve)
        bucket = mod.execute("risk/sensitivities/bucket-delta",
                             {"positions": [{"tenor": 1, "rate": 0.045, "cashflow": 50},
                                            {"tenor": 2, "rate": 0.046, "cashflow": 50},
                                            {"tenor": 5, "rate": 0.047, "cashflow": 1050}],
                              "bump_size": 0.0001})
        for answer in (greeks, cross, key_rate, parallel, twist, bucket):
            self.assertTrue(answer["supported"])
            self.assertNotIn("error", answer)
            self.assertEqual(answer["meta"]["fincept_credits"], 0)
        self.assertGreater(greeks["data"]["gamma"], 0)
        self.assertTrue(math.isfinite(cross["data"]["cross_gamma"]))
        self.assertEqual(len(key_rate["data"]["key_rate_durations"]), 5)
        self.assertEqual(len(bucket["data"]["bucket_deltas"]), 3)

    def test_tail_dependence_is_local(self):
        mod = load_module()
        first = [-0.031, 0.012, -0.008, 0.021, -0.045, 0.007, 0.015, -0.012, 0.004, -0.027,
                 0.018, -0.002, 0.009, -0.019, 0.025, -0.006, 0.011, -0.038, 0.003, 0.014]
        second = [-0.027, 0.009, -0.011, 0.018, -0.039, 0.004, 0.012, -0.009, 0.006, -0.022,
                  0.015, -0.004, 0.007, -0.016, 0.021, -0.003, 0.008, -0.033, 0.001, 0.012]
        answer = mod.execute("risk/tail-risk/tail-dependence",
                             {"returns1": first, "returns2": second, "threshold_quantile": 0.9})
        self.assertTrue(answer["supported"])
        self.assertNotIn("error", answer)
        self.assertGreaterEqual(answer["data"]["lower_tail_dependence"], 0)
        self.assertLessEqual(answer["data"]["lower_tail_dependence"], 1)
        self.assertGreaterEqual(answer["data"]["upper_tail_dependence"], 0)
        self.assertLessEqual(answer["data"]["upper_tail_dependence"], 1)


if __name__ == "__main__":
    unittest.main()
