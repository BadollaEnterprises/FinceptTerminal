#!/usr/bin/env python3
"""Deterministic zero-credit substitutes for selected QuantLib endpoints.

The adapter deliberately returns ``supported: false`` for calculations it
cannot reproduce reliably.  The desktop client treats that as a terminal
answer for non-free endpoints, so it can never become a paid Fincept fallback.
"""

from __future__ import annotations

import calendar
import json
import math
import random
import statistics
import sys
from datetime import date, timedelta
from statistics import NormalDist
from typing import Any, Callable, Dict, Mapping, Sequence


NORMAL = NormalDist()


def _number(body: Mapping[str, Any], name: str, *aliases: str) -> float:
    for key in (name, *aliases):
        if key in body:
            value = float(body[key])
            if math.isfinite(value):
                return value
            break
    raise ValueError(f"{name} must be a finite number")


def _positive(body: Mapping[str, Any], name: str, *aliases: str) -> float:
    value = _number(body, name, *aliases)
    if value <= 0:
        raise ValueError(f"{name} must be positive")
    return value


def _option_inputs(body: Mapping[str, Any], *, black76: bool = False):
    underlying = _positive(body, "forward" if black76 else "spot")
    strike = _positive(body, "strike")
    rate = _number(body, "risk_free_rate", "rate")
    vol = _positive(body, "volatility")
    time = _positive(body, "time_to_maturity", "time")
    kind = str(body.get("option_type", "call")).lower()
    if kind not in {"call", "put"}:
        raise ValueError("option_type must be call or put")
    return underlying, strike, rate, vol, time, kind


def _bs_values(body: Mapping[str, Any], *, black76: bool = False) -> Dict[str, float]:
    value, strike, rate, vol, time, kind = _option_inputs(body, black76=black76)
    root_t = math.sqrt(time)
    d1 = (math.log(value / strike) + ((0 if black76 else rate) + 0.5 * vol * vol) * time) / (vol * root_t)
    d2 = d1 - vol * root_t
    discount = math.exp(-rate * time)
    sign = 1.0 if kind == "call" else -1.0
    if black76:
        price = discount * sign * (value * NORMAL.cdf(sign * d1) - strike * NORMAL.cdf(sign * d2))
        delta = discount * sign * NORMAL.cdf(sign * d1)
        gamma = discount * NORMAL.pdf(d1) / (value * vol * root_t)
    else:
        price = sign * (value * NORMAL.cdf(sign * d1) - strike * discount * NORMAL.cdf(sign * d2))
        delta = sign * NORMAL.cdf(sign * d1)
        gamma = NORMAL.pdf(d1) / (value * vol * root_t)
    vega = (discount if black76 else 1.0) * value * NORMAL.pdf(d1) * root_t
    return {"price": price, "delta": delta, "gamma": gamma, "vega": vega, "d1": d1, "d2": d2}


def _bs_price(body: Mapping[str, Any]) -> Dict[str, float]:
    return {"price": _bs_values(body)["price"]}


def _bs_greeks(body: Mapping[str, Any]) -> Dict[str, float]:
    return _bs_values(body)


def _bs_binary(body: Mapping[str, Any], *, asset: bool = False) -> Dict[str, float]:
    values = _bs_values(body)
    spot, _strike, rate, _vol, time, kind = _option_inputs(body)
    sign = 1.0 if kind == "call" else -1.0
    if asset:
        price = spot * NORMAL.cdf(sign * values["d1"])
    else:
        price = math.exp(-rate * time) * NORMAL.cdf(sign * values["d2"])
    return {"price": price}


def _tree_steps(body: Mapping[str, Any]) -> int:
    steps = int(body.get("n_steps", 100))
    if not 1 <= steps <= 5000:
        raise ValueError("n_steps must be between 1 and 5000")
    return steps


def _binomial(body: Mapping[str, Any], *, american: bool) -> Dict[str, float]:
    spot, strike, rate, volatility, time, kind = _option_inputs(body)
    dividend = float(body.get("dividend_yield", 0.0))
    steps = _tree_steps(body)
    dt = time / steps
    up = math.exp(volatility * math.sqrt(dt))
    down = 1 / up
    probability = (math.exp((rate - dividend) * dt) - down) / (up - down)
    if not 0 <= probability <= 1:
        raise ValueError("binomial inputs produce an invalid risk-neutral probability")
    discount = math.exp(-rate * dt)
    sign = 1.0 if kind == "call" else -1.0
    values = [max(0.0, sign * (spot * up ** j * down ** (steps - j) - strike))
              for j in range(steps + 1)]
    for level in range(steps - 1, -1, -1):
        next_values = []
        for j in range(level + 1):
            continuation = discount * (probability * values[j + 1] + (1 - probability) * values[j])
            if american:
                node_spot = spot * up ** j * down ** (level - j)
                continuation = max(continuation, sign * (node_spot - strike), 0.0)
            next_values.append(continuation)
        values = next_values
    return {"price": values[0], "steps": steps, "exercise": "american" if american else "european"}


def _bermudan(body: Mapping[str, Any]) -> Dict[str, float]:
    spot, strike, rate, volatility, time, kind = _option_inputs(body)
    dividend = float(body.get("dividend_yield", 0.0))
    steps = _tree_steps(body)
    dates = [float(value) for value in body.get("exercise_dates", [])]
    if not dates or any(value <= 0 or value > time for value in dates):
        raise ValueError("exercise_dates must be in (0, time_to_maturity]")
    dt = time / steps
    exercise_levels = {max(1, min(steps, round(value / dt))) for value in dates}
    up = math.exp(volatility * math.sqrt(dt)); down = 1 / up
    probability = (math.exp((rate - dividend) * dt) - down) / (up - down)
    if not 0 <= probability <= 1:
        raise ValueError("binomial inputs produce an invalid risk-neutral probability")
    discount = math.exp(-rate * dt); sign = 1 if kind == "call" else -1
    values = [max(0.0, sign * (spot * up ** j * down ** (steps - j) - strike))
              for j in range(steps + 1)]
    for level in range(steps - 1, -1, -1):
        next_values = []
        for j in range(level + 1):
            continuation = discount * (probability * values[j + 1] + (1 - probability) * values[j])
            if level in exercise_levels:
                node = spot * up ** j * down ** (level - j)
                continuation = max(continuation, sign * (node - strike), 0.0)
            next_values.append(continuation)
        values = next_values
    return {"price": values[0], "steps": steps, "exercise": "bermudan",
            "exercise_dates": sorted(dates)}


def _barrier_option(body: Mapping[str, Any]) -> Dict[str, float]:
    spot, strike, rate, volatility, time, kind = _option_inputs(body)
    dividend = float(body.get("dividend_yield", 0.0)); steps = _tree_steps(body)
    barrier = _positive(body, "barrier"); rebate = float(body.get("rebate", 0.0))
    down_barrier = bool(body.get("is_down", True)); knock_in = bool(body.get("is_knock_in", False))
    dt = time / steps; up = math.exp(volatility * math.sqrt(dt)); down = 1 / up
    probability = (math.exp((rate - dividend) * dt) - down) / (up - down)
    discount = math.exp(-rate * dt); sign = 1 if kind == "call" else -1
    breached = lambda value: value <= barrier if down_barrier else value >= barrier
    values = []
    for j in range(steps + 1):
        node = spot * up ** j * down ** (steps - j)
        values.append(rebate if breached(node) else max(0.0, sign * (node - strike)))
    for level in range(steps - 1, -1, -1):
        next_values = []
        for j in range(level + 1):
            node = spot * up ** j * down ** (level - j)
            next_values.append(rebate if breached(node) else
                               discount * (probability * values[j + 1] + (1 - probability) * values[j]))
        values = next_values
    knockout = values[0]
    if knock_in:
        vanilla = _binomial(body, american=False)["price"]
        price = max(0.0, vanilla - knockout + rebate * math.exp(-rate * time))
    else:
        price = knockout
    return {"price": price, "barrier": barrier,
            "barrier_type": ("down" if down_barrier else "up") + ("-in" if knock_in else "-out")}


def _shifted_lognormal(body: Mapping[str, Any]) -> Dict[str, float]:
    shift = _number(body, "shift")
    adjusted = dict(body)
    adjusted["forward"] = _number(body, "forward") + shift
    adjusted["strike"] = _number(body, "strike") + shift
    if adjusted["forward"] <= 0 or adjusted["strike"] <= 0:
        raise ValueError("shifted forward and strike must be positive")
    result = _bs_values(adjusted, black76=True)
    return {**result, "shift": shift}


def _vol_conversion(body: Mapping[str, Any]) -> Dict[str, float]:
    volatility = _positive(body, "volatility")
    forward, strike = _positive(body, "forward"), _positive(body, "strike")
    scale = math.sqrt(forward * strike)
    direction = str(body.get("direction", "lognormal_to_normal"))
    if direction == "lognormal_to_normal":
        converted = volatility * scale
    elif direction == "normal_to_lognormal":
        converted = volatility / scale
    else:
        raise ValueError("direction must be lognormal_to_normal or normal_to_lognormal")
    return {"converted_volatility": converted, "direction": direction}


def _kirk_values(body: Mapping[str, Any]) -> Dict[str, float]:
    first, second = _positive(body, "F1"), _positive(body, "F2")
    strike = _number(body, "strike")
    effective_strike = second + strike
    if effective_strike <= 0:
        raise ValueError("F2 + strike must be positive")
    sigma1, sigma2 = _positive(body, "sigma1"), _positive(body, "sigma2")
    rho = _number(body, "rho")
    if not -1 <= rho <= 1:
        raise ValueError("rho must be in [-1,1]")
    weight = second / effective_strike
    volatility = math.sqrt(max(0.0, sigma1 ** 2 - 2 * rho * sigma1 * sigma2 * weight +
                               sigma2 ** 2 * weight ** 2))
    trial = {"forward": first, "strike": effective_strike, "volatility": volatility,
             "time_to_maturity": _positive(body, "time_to_maturity"),
             "risk_free_rate": _number(body, "risk_free_rate", "rate"), "option_type": "call"}
    values = _bs_values(trial, black76=True)
    return {"price": values["price"], "effective_volatility": volatility,
            "delta1": values["delta"], "gamma1": values["gamma"], "vega": values["vega"]}


def _kirk_greeks(body: Mapping[str, Any]) -> Dict[str, float]:
    result = _kirk_values(body)
    step = max(1e-4, _positive(body, "F2") * 1e-4)
    up, down = dict(body), dict(body)
    up["F2"] = _positive(body, "F2") + step; down["F2"] = _positive(body, "F2") - step
    result["delta2"] = (_kirk_values(up)["price"] - _kirk_values(down)["price"]) / (2 * step)
    return result


def _margrabe(body: Mapping[str, Any]) -> Dict[str, float]:
    first, second = _positive(body, "S1"), _positive(body, "S2")
    sigma1, sigma2, rho = _positive(body, "sigma1"), _positive(body, "sigma2"), _number(body, "rho")
    time = _positive(body, "time_to_maturity")
    combined = math.sqrt(max(1e-30, sigma1 ** 2 + sigma2 ** 2 - 2 * rho * sigma1 * sigma2))
    root = combined * math.sqrt(time)
    d1 = (math.log(first / second) + (_number(body, "q2") - _number(body, "q1") +
                                      0.5 * combined ** 2) * time) / root
    d2 = d1 - root
    price = first * math.exp(-_number(body, "q1") * time) * NORMAL.cdf(d1) - \
            second * math.exp(-_number(body, "q2") * time) * NORMAL.cdf(d2)
    return {"price": price, "exchange_volatility": combined, "delta1": math.exp(-_number(body, "q1") * time) * NORMAL.cdf(d1)}


def _basket_levy(body: Mapping[str, Any]) -> Dict[str, float]:
    forwards, weights, sigmas = (_vector(body, "forwards"), _vector(body, "weights"),
                                  _vector(body, "sigmas"))
    size = len(forwards)
    correlations = [float(value) for value in body.get("correlations", [])]
    if len(weights) != size or len(sigmas) != size or len(correlations) != size * size:
        raise ValueError("basket inputs must have matching dimensions")
    time = _positive(body, "time_to_maturity")
    mean = sum(weight * forward for weight, forward in zip(weights, forwards))
    variance = sum(weights[i] * weights[j] * forwards[i] * forwards[j] *
                   (math.exp(correlations[i * size + j] * sigmas[i] * sigmas[j] * time) - 1)
                   for i in range(size) for j in range(size))
    effective = math.sqrt(max(0.0, math.log1p(variance / (mean * mean)) / time))
    result = _bs_values({"forward": mean, "strike": _positive(body, "strike"),
                         "volatility": max(effective, 1e-12), "time_to_maturity": time,
                         "risk_free_rate": _number(body, "risk_free_rate", "rate"),
                         "option_type": str(body.get("option_type", "call"))}, black76=True)
    return {"price": result["price"], "basket_forward": mean, "effective_volatility": effective}


def _black76_price(body: Mapping[str, Any]) -> Dict[str, float]:
    adjusted = dict(body)
    if "discount_factor" in adjusted and "risk_free_rate" not in adjusted and "rate" not in adjusted:
        time = _positive(adjusted, "time", "time_to_maturity")
        adjusted["rate"] = -math.log(_positive(adjusted, "discount_factor")) / time
    return {"price": _bs_values(adjusted, black76=True)["price"]}


def _black76_rate_option(body: Mapping[str, Any], kind: str) -> Dict[str, float]:
    if kind in {"caplet", "floorlet"}:
        start = _positive(body, "t_start")
        end = _positive(body, "t_end")
        if end <= start:
            raise ValueError("t_end must be greater than t_start")
        discount = _positive(body, "discount_factor")
        adjusted = {
            "forward": _positive(body, "forward_rate"),
            "strike": _positive(body, "strike"),
            "risk_free_rate": -math.log(discount) / start,
            "volatility": _positive(body, "volatility"),
            "time_to_maturity": start,
            "option_type": "call" if kind == "caplet" else "put",
        }
        unit_price = _bs_values(adjusted, black76=True)["price"]
        price = unit_price * _positive(body, "notional") * (end - start)
        return {"price": price, "unit_price": unit_price, "accrual": end - start}

    expiry = _positive(body, "t_expiry")
    adjusted = {
        "forward": _positive(body, "forward_swap_rate"),
        "strike": _positive(body, "strike"),
        "risk_free_rate": 0.0,
        "volatility": _positive(body, "volatility"),
        "time_to_maturity": expiry,
        "option_type": "call" if bool(body.get("is_payer", True)) else "put",
    }
    unit_price = _bs_values(adjusted, black76=True)["price"]
    annuity = _positive(body, "annuity")
    price = unit_price * _positive(body, "notional") * annuity
    return {"price": price, "unit_price": unit_price, "annuity": annuity}


def _implied_vol(body: Mapping[str, Any], *, black76: bool = False) -> Dict[str, float]:
    target = _positive(body, "market_price", "price")
    low, high = 1e-6, 5.0
    trial = dict(body)
    for _ in range(100):
        mid = (low + high) / 2
        trial["volatility"] = mid
        price = _bs_values(trial, black76=black76)["price"]
        if price < target:
            low = mid
        else:
            high = mid
    return {"implied_volatility": (low + high) / 2}


def _bachelier_values(body: Mapping[str, Any]) -> Dict[str, float]:
    forward, strike = _number(body, "forward"), _number(body, "strike")
    volatility = _positive(body, "normal_volatility")
    time = _positive(body, "time_to_maturity", "time")
    rate = _number(body, "risk_free_rate", "rate")
    kind = str(body.get("option_type", "call")).lower()
    if kind not in {"call", "put"}:
        raise ValueError("option_type must be call or put")
    deviation = volatility * math.sqrt(time)
    d_value = (forward - strike) / deviation
    sign = 1.0 if kind == "call" else -1.0
    discount = math.exp(-rate * time)
    price = discount * (sign * (forward - strike) * NORMAL.cdf(sign * d_value) +
                        deviation * NORMAL.pdf(d_value))
    return {"price": price, "delta": discount * sign * NORMAL.cdf(sign * d_value),
            "gamma": discount * NORMAL.pdf(d_value) / deviation,
            "vega": discount * math.sqrt(time) * NORMAL.pdf(d_value)}


def _bachelier_implied_vol(body: Mapping[str, Any]) -> Dict[str, float]:
    target = _positive(body, "market_price", "price")
    trial = dict(body)
    low, high = 1e-9, max(1.0, abs(_number(body, "forward") - _number(body, "strike")) * 2)
    trial["normal_volatility"] = high
    while _bachelier_values(trial)["price"] < target and high < 1e6:
        high *= 2
        trial["normal_volatility"] = high
    for _ in range(100):
        middle = (low + high) / 2
        trial["normal_volatility"] = middle
        if _bachelier_values(trial)["price"] < target:
            low = middle
        else:
            high = middle
    return {"implied_volatility": (low + high) / 2}


def _discount_factor(body: Mapping[str, Any]) -> Dict[str, float]:
    return {"discount_factor": math.exp(-_number(body, "rate") * _positive(body, "t", "time"))}


def _zero_rate(body: Mapping[str, Any]) -> Dict[str, float]:
    return {"zero_rate": -math.log(_positive(body, "discount_factor")) / _positive(body, "t", "time")}


def _forward_rate(body: Mapping[str, Any]) -> Dict[str, float]:
    df1, df2 = _positive(body, "df1"), _positive(body, "df2")
    t1, t2 = _number(body, "t1"), _number(body, "t2")
    if t2 <= t1:
        raise ValueError("t2 must be greater than t1")
    return {"forward_rate": -math.log(df2 / df1) / (t2 - t1)}


def _spread(body: Mapping[str, Any], left: str, right: str, label: str) -> Dict[str, float]:
    return {label: _number(body, left) - _number(body, right)}


def _historical_var(body: Mapping[str, Any]) -> Dict[str, float]:
    returns = sorted(float(value) for value in body.get("returns", []))
    if len(returns) < 2 or any(not math.isfinite(value) for value in returns):
        raise ValueError("returns must contain at least two finite values")
    confidence = float(body.get("confidence", 0.95))
    if not 0.5 <= confidence < 1:
        raise ValueError("confidence must be in [0.5, 1)")
    index = max(0, min(len(returns) - 1, math.floor((1 - confidence) * len(returns))))
    cutoff = returns[index]
    var = max(0.0, -cutoff)
    tail = [-value for value in returns if value <= cutoff]
    return {"var": var, "expected_shortfall": max(var, statistics.mean(tail))}


def _statistics(body: Mapping[str, Any]) -> Dict[str, float]:
    values = [float(value) for value in body.get("values", [])]
    if len(values) < 2:
        raise ValueError("values must contain at least two numbers")
    return {"count": len(values), "mean": statistics.mean(values), "stddev": statistics.stdev(values),
            "minimum": min(values), "maximum": max(values)}


def _normal(body: Mapping[str, Any], operation: str) -> Dict[str, float]:
    if operation == "ppf":
        return {"value": NORMAL.inv_cdf(_number(body, "p"))}
    x = _number(body, "x")
    return {"value": NORMAL.cdf(x) if operation == "cdf" else NORMAL.pdf(x)}


def _regularized_gamma(shape: float, x: float) -> float:
    if shape <= 0 or x < 0:
        raise ValueError("gamma shape must be positive and x non-negative")
    if x == 0:
        return 0.0
    if x < shape + 1:
        term = 1 / shape; total = term; current = shape
        for _ in range(1000):
            current += 1; term *= x / current; total += term
            if abs(term) <= abs(total) * 1e-15:
                break
        return min(1.0, total * math.exp(-x + shape * math.log(x) - math.lgamma(shape)))
    b = x + 1 - shape; c = 1 / 1e-300; d = 1 / b; fraction = d
    for index in range(1, 1000):
        coefficient = -index * (index - shape)
        b += 2; d = coefficient * d + b
        if abs(d) < 1e-300: d = 1e-300
        c = b + coefficient / c
        if abs(c) < 1e-300: c = 1e-300
        d = 1 / d; delta = d * c; fraction *= delta
        if abs(delta - 1) < 1e-15:
            break
    complement = math.exp(-x + shape * math.log(x) - math.lgamma(shape)) * fraction
    return max(0.0, min(1.0, 1 - complement))


def _beta_continued_fraction(a: float, b: float, x: float) -> float:
    qab, qap, qam = a + b, a + 1, a - 1
    c = 1.0; d = 1 - qab * x / qap
    if abs(d) < 1e-300: d = 1e-300
    d = 1 / d; result = d
    for index in range(1, 500):
        even = index * (b - index) * x / ((qam + 2 * index) * (a + 2 * index))
        d = 1 + even * d; c = 1 + even / c
        if abs(d) < 1e-300: d = 1e-300
        if abs(c) < 1e-300: c = 1e-300
        d = 1 / d; result *= d * c
        odd = -(a + index) * (qab + index) * x / ((a + 2 * index) * (qap + 2 * index))
        d = 1 + odd * d; c = 1 + odd / c
        if abs(d) < 1e-300: d = 1e-300
        if abs(c) < 1e-300: c = 1e-300
        d = 1 / d; delta = d * c; result *= delta
        if abs(delta - 1) < 1e-14:
            break
    return result


def _regularized_beta(a: float, b: float, x: float) -> float:
    if a <= 0 or b <= 0 or not 0 <= x <= 1:
        raise ValueError("beta parameters must be positive and x in [0,1]")
    if x in {0.0, 1.0}:
        return x
    factor = math.exp(math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b) +
                      a * math.log(x) + b * math.log1p(-x))
    if x < (a + 1) / (a + b + 2):
        return factor * _beta_continued_fraction(a, b, x) / a
    return 1 - factor * _beta_continued_fraction(b, a, 1 - x) / b


def _student_t(body: Mapping[str, Any], operation: str) -> Dict[str, float]:
    degrees = _positive(body, "df")
    if operation == "pdf":
        x = _number(body, "x")
        value = math.exp(math.lgamma((degrees + 1) / 2) - math.lgamma(degrees / 2)) / \
                math.sqrt(degrees * math.pi) * (1 + x * x / degrees) ** (-(degrees + 1) / 2)
        return {"pdf": value, "value": value}
    def cdf(x: float) -> float:
        if x == 0: return 0.5
        beta = _regularized_beta(degrees / 2, 0.5, degrees / (degrees + x * x))
        return 1 - 0.5 * beta if x > 0 else 0.5 * beta
    if operation == "cdf":
        value = cdf(_number(body, "x")); return {"cdf": value, "value": value}
    probability = _number(body, "p")
    if not 0 < probability < 1:
        raise ValueError("p must be in (0,1)")
    low, high = -100.0, 100.0
    for _ in range(160):
        middle = (low + high) / 2
        if cdf(middle) < probability: low = middle
        else: high = middle
    value = (low + high) / 2
    return {"value": value, "quantile": value}


def _chi_square(body: Mapping[str, Any], operation: str) -> Dict[str, float]:
    x, degrees = _number(body, "x"), _positive(body, "df")
    if x < 0: value = 0.0
    elif operation == "cdf": value = _regularized_gamma(degrees / 2, x / 2)
    elif x == 0 and degrees < 2: value = math.inf
    elif x == 0: value = 0.5 if degrees == 2 else 0.0
    else: value = math.exp((degrees / 2 - 1) * math.log(x) - x / 2 -
                           degrees / 2 * math.log(2) - math.lgamma(degrees / 2))
    return {operation: value, "value": value}


def _gamma_distribution(body: Mapping[str, Any], operation: str) -> Dict[str, float]:
    x, alpha, rate = _number(body, "x"), _positive(body, "alpha"), _positive(body, "beta")
    if x < 0: value = 0.0
    elif operation == "cdf": value = _regularized_gamma(alpha, rate * x)
    elif x == 0: value = rate if alpha == 1 else (math.inf if alpha < 1 else 0.0)
    else: value = math.exp(alpha * math.log(rate) + (alpha - 1) * math.log(x) - rate * x - math.lgamma(alpha))
    return {operation: value, "value": value}


def _exponential_distribution(body: Mapping[str, Any], operation: str) -> Dict[str, float]:
    rate = _positive(body, "rate")
    if operation == "ppf":
        probability = _number(body, "p")
        if not 0 <= probability < 1: raise ValueError("p must be in [0,1)")
        value = -math.log1p(-probability) / rate
        return {"value": value, "quantile": value}
    x = _number(body, "x")
    value = 0.0 if x < 0 else (1 - math.exp(-rate * x) if operation == "cdf" else rate * math.exp(-rate * x))
    return {operation: value, "value": value}


def _bivariate_normal_cdf(body: Mapping[str, Any]) -> Dict[str, float]:
    x, y, rho = _number(body, "x"), _number(body, "y"), _number(body, "rho")
    if not -1 < rho < 1: raise ValueError("rho must be in (-1,1)")
    lower = -10.0
    if x <= lower: return {"cdf": 0.0, "value": 0.0}
    upper = min(x, 10.0); intervals = 800
    if intervals % 2: intervals += 1
    step = (upper - lower) / intervals; scale = math.sqrt(1 - rho * rho)
    def integrand(value: float) -> float:
        return NORMAL.pdf(value) * NORMAL.cdf((y - rho * value) / scale)
    total = integrand(lower) + integrand(upper)
    total += 4 * sum(integrand(lower + index * step) for index in range(1, intervals, 2))
    total += 2 * sum(integrand(lower + index * step) for index in range(2, intervals, 2))
    result = max(0.0, min(1.0, total * step / 3))
    return {"cdf": result, "value": result}


def _dual_eval(body: Mapping[str, Any]) -> Dict[str, float]:
    name, x = str(body.get("func_name", "")), _number(body, "x")
    step = 1e-5 * max(1.0, abs(x))
    value = float(_numeric_function(name, x))
    derivative = (float(_numeric_function(name, x + step)) - float(_numeric_function(name, x - step))) / (2 * step)
    return {"value": value, "derivative": derivative}


def _autodiff_gradient(body: Mapping[str, Any]) -> Dict[str, Any]:
    name = str(body.get("func_name", "")); values = _vector(body, "x"); gradient = []
    for index, value in enumerate(values):
        step = 1e-5 * max(1.0, abs(value)); upper, lower = list(values), list(values)
        upper[index] += step; lower[index] -= step
        gradient.append((float(_numeric_function(name, upper if len(values) > 1 else upper[0])) -
                         float(_numeric_function(name, lower if len(values) > 1 else lower[0]))) / (2 * step))
    return {"value": _numeric_function(name, values if len(values) > 1 else values[0]), "gradient": gradient}


def _taylor_expand(body: Mapping[str, Any]) -> Dict[str, Any]:
    name, point, order = str(body.get("func_name", "")), _number(body, "x0"), int(body.get("order", 1))
    if not 0 <= order <= 12: raise ValueError("order must be between 0 and 12")
    derivatives = []
    for degree in range(order + 1):
        if name == "sin": value = math.sin(point + degree * math.pi / 2)
        elif name == "cos": value = math.cos(point + degree * math.pi / 2)
        elif name == "exp": value = math.exp(point)
        elif name == "log": value = (-1) ** (degree - 1) * math.factorial(degree - 1) / point ** degree if degree else math.log(point)
        elif name == "square": value = point * point if degree == 0 else (2 * point if degree == 1 else (2.0 if degree == 2 else 0.0))
        elif name == "cube": value = point ** 3 if degree == 0 else (3 * point ** 2 if degree == 1 else (6 * point if degree == 2 else (6.0 if degree == 3 else 0.0)))
        else: raise ValueError("taylor expansion supports sin, cos, exp, log, square, and cube")
        derivatives.append(value)
    return {"x0": point, "order": order, "derivatives": derivatives,
            "coefficients": [value / math.factorial(index) for index, value in enumerate(derivatives)]}


def _core_math_eval(body: Mapping[str, Any]) -> Dict[str, float]:
    value = float(_numeric_function(str(body.get("func_name", "")), _number(body, "x")))
    return {"value": value}


def _core_math_two_arg(body: Mapping[str, Any]) -> Dict[str, float]:
    name, x, y = str(body.get("func_name", "")).lower(), _number(body, "x"), _number(body, "y")
    operations = {"power": lambda: x ** y, "pow": lambda: x ** y, "min": lambda: min(x, y),
                  "max": lambda: max(x, y), "hypot": lambda: math.hypot(x, y),
                  "atan2": lambda: math.atan2(x, y)}
    if name not in operations: raise ValueError("unsupported two-argument function")
    return {"value": float(operations[name]())}


def _currency(body: Mapping[str, Any], name: str) -> str:
    value = str(body.get(name, "")).strip().upper()
    if len(value) != 3 or not value.isalpha():
        raise ValueError(f"{name} must be a three-letter currency code")
    return value


def _money_create(body: Mapping[str, Any]) -> Dict[str, Any]:
    return {"amount": _number(body, "amount"), "currency": _currency(body, "currency")}


def _money_convert(body: Mapping[str, Any]) -> Dict[str, Any]:
    amount = _number(body, "amount")
    rate = _positive(body, "rate")
    return {"amount": amount, "from_currency": _currency(body, "from_currency"),
            "to_currency": _currency(body, "to_currency"), "rate": rate,
            "converted_amount": amount * rate}


def _rate_convert(body: Mapping[str, Any]) -> Dict[str, float]:
    value = _number(body, "value")
    unit = str(body.get("unit", "decimal")).strip().lower()
    if unit in {"percentage", "percent", "%"}:
        decimal = value / 100.0
    elif unit in {"bps", "bp", "basis_points"}:
        decimal = value / 10000.0
    elif unit == "decimal":
        decimal = value
    else:
        raise ValueError("unit must be decimal, percentage, or bps")
    return {"decimal": decimal, "percentage": decimal * 100.0, "bps": decimal * 10000.0}


def _spread_from_bps(body: Mapping[str, Any]) -> Dict[str, float]:
    bps = _number(body, "bps")
    return {"decimal": bps / 10000.0, "percentage": bps / 100.0, "bps": bps}


def _add_tenor(body: Mapping[str, Any]) -> Dict[str, str]:
    start = date.fromisoformat(str(body.get("start_date", "")))
    tenor = str(body.get("tenor", "")).strip().upper()
    if len(tenor) < 2 or not tenor[:-1].isdigit() or int(tenor[:-1]) <= 0:
        raise ValueError("tenor must be a positive number followed by D, W, M, or Y")
    count, unit = int(tenor[:-1]), tenor[-1]
    if unit == "D":
        result = start + timedelta(days=count)
    elif unit == "W":
        result = start + timedelta(weeks=count)
    elif unit in {"M", "Y"}:
        months = count if unit == "M" else count * 12
        month_index = start.month - 1 + months
        year, month = start.year + month_index // 12, month_index % 12 + 1
        result = date(year, month, min(start.day, calendar.monthrange(year, month)[1]))
    else:
        raise ValueError("tenor unit must be D, W, M, or Y")
    return {"date": result.isoformat(), "start_date": start.isoformat(), "tenor": tenor}


def _notional_schedule(body: Mapping[str, Any]) -> Dict[str, Any]:
    notional = _positive(body, "notional")
    periods = int(body.get("periods", 0))
    if not 1 <= periods <= 10000:
        raise ValueError("periods must be between 1 and 10000")
    kind = str(body.get("schedule_type", "constant")).strip().lower()
    if kind == "constant":
        values = [notional] * periods
    elif kind == "amortizing":
        values = [notional * (periods - index) / periods for index in range(periods)]
    else:
        raise ValueError("schedule_type must be constant or amortizing")
    return {"notionals": values, "periods": periods, "schedule_type": kind}


def _parse_date(body: Mapping[str, Any]) -> Dict[str, Any]:
    parsed = date.fromisoformat(str(body.get("date_string", "")))
    return {"iso_date": parsed.isoformat(), "year": parsed.year, "month": parsed.month, "day": parsed.day}


def _format_date(body: Mapping[str, Any]) -> Dict[str, str]:
    parsed = date.fromisoformat(str(body.get("date_str", "")))
    pattern = str(body.get("format", "%Y-%m-%d"))
    return {"formatted_date": parsed.strftime(pattern), "iso_date": parsed.isoformat()}


def _day_basis(convention: str) -> float:
    key = convention.strip().lower()
    if key in {"actual", "act", "actual/actual"}:
        return 365.25
    if key in {"365", "act/365", "actual/365"}:
        return 365.0
    if key in {"360", "act/360", "actual/360"}:
        return 360.0
    if key in {"business", "252"}:
        return 252.0
    raise ValueError("convention must be actual, 365, 360, or business")


def _days_to_years(body: Mapping[str, Any]) -> Dict[str, float]:
    return {"years": _number(body, "value") / _day_basis(str(body.get("convention", "actual")))}


def _years_to_days(body: Mapping[str, Any]) -> Dict[str, float]:
    return {"days": _number(body, "value") * _day_basis(str(body.get("convention", "actual")))}


def _normalize_rate(body: Mapping[str, Any]) -> Dict[str, float]:
    converted = _rate_convert(body)
    return {"value": converted["decimal"]}


def _normalize_volatility(body: Mapping[str, Any]) -> Dict[str, Any]:
    value = _number(body, "value")
    if bool(body.get("is_percent", False)):
        value /= 100.0
    if value < 0:
        raise ValueError("volatility must be non-negative")
    return {"value": value, "vol_type": str(body.get("vol_type", "lognormal")).lower()}


def _currencies(_body: Mapping[str, Any]) -> Dict[str, Any]:
    values = ["USD", "EUR", "GBP", "JPY", "CHF", "CAD", "AUD", "NZD", "CNY", "HKD", "SGD", "INR"]
    return {"currencies": values, "count": len(values)}


def _frequencies(_body: Mapping[str, Any]) -> Dict[str, Any]:
    return {"frequencies": [{"name": "annual", "tenor": "12M", "payments_per_year": 1},
                             {"name": "semiannual", "tenor": "6M", "payments_per_year": 2},
                             {"name": "quarterly", "tenor": "3M", "payments_per_year": 4},
                             {"name": "monthly", "tenor": "1M", "payments_per_year": 12}]}


def _core_day_count(body: Mapping[str, Any]) -> Dict[str, float]:
    start, end = date.fromisoformat(str(body.get("start_date"))), date.fromisoformat(str(body.get("end_date")))
    days, fraction = _day_count_values(start, end, str(body.get("convention", "ACT/365")))
    return {"days": days, "year_fraction": fraction}


def _coupon_period(body: Mapping[str, Any], *, floating: bool) -> Dict[str, float]:
    fraction = _core_day_count(body)["year_fraction"]
    notional = _positive(body, "notional"); spread = float(body.get("spread", 0.0))
    fixing = float(body.get("fixing_rate", body.get("rate", 0.0)))
    rate = fixing + spread if floating else _number(body, "rate")
    amount = notional * rate * fraction
    return {"amount": amount, "coupon_amount": amount, "rate": rate,
            "year_fraction": fraction, "notional": notional}


def _frequency_months(value: str) -> int:
    key = value.strip().upper()
    names = {"ANNUAL": 12, "SEMIANNUAL": 6, "QUARTERLY": 3, "MONTHLY": 1}
    if key in names: return names[key]
    if key.endswith("M") and key[:-1].isdigit() and int(key[:-1]) > 0: return int(key[:-1])
    if key.endswith("Y") and key[:-1].isdigit() and int(key[:-1]) > 0: return int(key[:-1]) * 12
    raise ValueError("frequency must be a positive month/year tenor or named frequency")


def _leg_schedule(body: Mapping[str, Any]) -> list[tuple[date, date]]:
    start, end = date.fromisoformat(str(body.get("start_date"))), date.fromisoformat(str(body.get("end_date")))
    if end <= start: raise ValueError("end_date must follow start_date")
    months = _frequency_months(str(body.get("frequency", "12M")))
    periods, cursor = [], start
    while cursor < end:
        next_date = min(_add_months(cursor, months), end)
        periods.append((cursor, next_date)); cursor = next_date
    return periods


def _fixed_leg(body: Mapping[str, Any]) -> Dict[str, Any]:
    cashflows = []
    for start, end in _leg_schedule(body):
        result = _coupon_period({**body, "start_date": start.isoformat(), "end_date": end.isoformat()}, floating=False)
        cashflows.append({"start_date": start.isoformat(), "payment_date": end.isoformat(), **result})
    return {"cashflows": cashflows, "total_amount": sum(item["amount"] for item in cashflows)}


def _float_leg(body: Mapping[str, Any]) -> Dict[str, Any]:
    fixings = body.get("fixings", {})
    if not isinstance(fixings, dict): raise ValueError("fixings must be an object")
    ordered = sorted((date.fromisoformat(str(key)), float(value)) for key, value in fixings.items())
    cashflows = []
    for start, end in _leg_schedule(body):
        eligible = [value for fixing_date, value in ordered if fixing_date <= start]
        fixing = eligible[-1] if eligible else float(body.get("fixing_rate", 0.0))
        result = _coupon_period({**body, "start_date": start.isoformat(), "end_date": end.isoformat(),
                                 "fixing_rate": fixing}, floating=True)
        cashflows.append({"start_date": start.isoformat(), "payment_date": end.isoformat(), **result})
    return {"cashflows": cashflows, "total_amount": sum(item["amount"] for item in cashflows)}


def _zero_coupon_leg(body: Mapping[str, Any]) -> Dict[str, Any]:
    start, end = date.fromisoformat(str(body.get("start_date"))), date.fromisoformat(str(body.get("end_date")))
    if end <= start: raise ValueError("end_date must follow start_date")
    fraction = (end - start).days / 365.0; notional = _positive(body, "notional"); rate = _number(body, "rate")
    maturity = notional * math.exp(rate * fraction)
    return {"cashflows": [{"payment_date": end.isoformat(), "amount": maturity}],
            "maturity_amount": maturity, "interest": maturity - notional, "year_fraction": fraction}


def _core_gbm_paths(body: Mapping[str, Any]) -> Dict[str, Any]:
    return _gbm({"S0": _positive(body, "spot"), "mu": _number(body, "drift"),
                 "sigma": _positive(body, "volatility"), "T": _positive(body, "time"),
                 "n_steps": int(body.get("n_steps", 52)), "n_paths": int(body.get("n_paths", 1)),
                 "seed": int(body.get("seed", 42))})


def _zero_rate_convert(body: Mapping[str, Any]) -> Dict[str, float]:
    direction = str(body.get("direction", "continuous_to_annual")); value = _number(body, "value")
    horizon = _positive(body, "t")
    if direction == "continuous_to_annual": converted = math.expm1(value * horizon) / horizon
    elif direction == "annual_to_continuous": converted = math.log1p(value * horizon) / horizon
    elif direction == "continuous_to_discount": converted = math.exp(-value * horizon)
    elif direction == "discount_to_continuous": converted = -math.log(value) / horizon
    else: raise ValueError("unsupported zero-rate conversion direction")
    return {"value": converted, "converted_rate": converted, "direction": direction}


def _discount_cashflows(body: Mapping[str, Any]) -> Dict[str, Any]:
    cashflows = _vector(body, "cashflows")
    times = _vector(body, "times")
    discounts = _vector(body, "discount_factors")
    if len(cashflows) != len(times) or len(cashflows) != len(discounts):
        raise ValueError("cashflows, times, and discount_factors must have equal length")
    present = [cashflow * discount for cashflow, discount in zip(cashflows, discounts)]
    return {"present_value": sum(present), "discounted_cashflows": present, "times": times}


def _core_interpolate(body: Mapping[str, Any]) -> Dict[str, float]:
    return {"value": _linear(_vector(body, "x_data"), _vector(body, "y_data"), _number(body, "x"))}


def _percentile(body: Mapping[str, Any]) -> Dict[str, float]:
    values = sorted(_vector(body, "values"))
    p_value = _number(body, "p")
    if not 0 <= p_value <= 1:
        raise ValueError("p must be in [0, 1]")
    position = p_value * (len(values) - 1)
    lower = math.floor(position)
    upper = math.ceil(position)
    value = values[lower] if lower == upper else values[lower] + (values[upper] - values[lower]) * (position - lower)
    return {"value": value, "p": p_value}


def _covariance_matrix(body: Mapping[str, Any]) -> Dict[str, Any]:
    observations = [[float(value) for value in row] for row in body.get("returns", [])]
    if len(observations) < 2 or not observations or not observations[0]:
        raise ValueError("returns must contain at least two non-empty observations")
    width = len(observations[0])
    if any(len(row) != width for row in observations):
        raise ValueError("returns rows must have equal length")
    means = [statistics.mean(row[column] for row in observations) for column in range(width)]
    divisor = len(observations) - 1
    covariance = [[sum((row[left] - means[left]) * (row[right] - means[right]) for row in observations) / divisor
                   for right in range(width)] for left in range(width)]
    return {"covariance_matrix": covariance, "means": means, "observations": len(observations)}


def _cholesky(body: Mapping[str, Any]) -> Dict[str, Any]:
    matrix = _matrix(body, "matrix")
    size = len(matrix)
    lower = [[0.0] * size for _ in range(size)]
    for row in range(size):
        for column in range(row + 1):
            residual = matrix[row][column] - sum(lower[row][k] * lower[column][k] for k in range(column))
            if row == column:
                if residual <= 0:
                    raise ValueError("matrix must be positive definite")
                lower[row][column] = math.sqrt(residual)
            else:
                lower[row][column] = residual / lower[column][column]
    return {"lower": lower}


# ── Scheduling (weekend calendar, deterministic and dependency-free) ────────

def _iso_date(body: Mapping[str, Any], name: str = "date") -> date:
    try:
        return date.fromisoformat(str(body.get(name, "")))
    except ValueError as exc:
        raise ValueError(f"{name} must be an ISO date (YYYY-MM-DD)") from exc


def _is_business_day(value: date) -> bool:
    return value.weekday() < 5


def _move_business_day(value: date, direction: int) -> date:
    while not _is_business_day(value):
        value += timedelta(days=direction)
    return value


def _adjust_business_date(value: date, convention: str) -> date:
    key = convention.strip().lower().replace("_", "-")
    if key in {"unadjusted", "none"} or _is_business_day(value):
        return value
    if key in {"preceding", "previous"}:
        return _move_business_day(value, -1)
    if key in {"modified-preceding", "modifiedpreceding"}:
        adjusted = _move_business_day(value, -1)
        return _move_business_day(value, 1) if adjusted.month != value.month else adjusted
    if key in {"following", "next", "modified-following", "modifiedfollowing"}:
        adjusted = _move_business_day(value, 1)
        if key.startswith("modified") and adjusted.month != value.month:
            adjusted = _move_business_day(value, -1)
        return adjusted
    raise ValueError("convention must be following, modified-following, preceding, modified-preceding, or unadjusted")


def _schedule_adjust(body: Mapping[str, Any]) -> Dict[str, Any]:
    original = _iso_date(body)
    convention = str(body.get("convention", "following"))
    adjusted = _adjust_business_date(original, convention)
    return {"date": adjusted.isoformat(), "original_date": original.isoformat(),
            "adjusted": adjusted != original, "convention": convention, "calendar": "weekends"}


def _schedule_batch_adjust(body: Mapping[str, Any]) -> Dict[str, Any]:
    raw = body.get("dates", [])
    if not isinstance(raw, list) or not raw:
        raise ValueError("dates must be a non-empty list")
    convention = str(body.get("convention", "following"))
    dates = [_adjust_business_date(date.fromisoformat(str(value)), convention).isoformat() for value in raw]
    return {"dates": dates, "convention": convention, "calendar": "weekends"}


def _schedule_methods(_body: Mapping[str, Any]) -> Dict[str, Any]:
    return {"methods": ["following", "modified-following", "preceding", "modified-preceding", "unadjusted"]}


def _calendar_list(_body: Mapping[str, Any]) -> Dict[str, Any]:
    return {"calendars": [{"id": "weekends", "name": "Weekend-only business calendar"}]}


def _calendar_is_business(body: Mapping[str, Any]) -> Dict[str, Any]:
    value = _iso_date(body)
    return {"date": value.isoformat(), "is_business_day": _is_business_day(value), "calendar": "weekends"}


def _calendar_step(body: Mapping[str, Any], direction: int) -> Dict[str, Any]:
    value = _iso_date(body) + timedelta(days=direction)
    value = _move_business_day(value, direction)
    return {"date": value.isoformat(), "calendar": "weekends"}


def _calendar_add_business_days(body: Mapping[str, Any]) -> Dict[str, Any]:
    value = _iso_date(body)
    days = int(body.get("days", 0))
    direction = 1 if days >= 0 else -1
    remaining = abs(days)
    while remaining:
        value += timedelta(days=direction)
        if _is_business_day(value):
            remaining -= 1
    return {"date": value.isoformat(), "business_days": days, "calendar": "weekends"}


def _calendar_business_days_between(body: Mapping[str, Any]) -> Dict[str, Any]:
    start, end = _iso_date(body, "start_date"), _iso_date(body, "end_date")
    direction = 1 if end >= start else -1
    count, cursor = 0, start
    while cursor != end:
        cursor += timedelta(days=direction)
        if _is_business_day(cursor):
            count += direction
    return {"business_days": count, "calendar": "weekends"}


def _day_count_values(start: date, end: date, convention: str) -> tuple[int, float]:
    key = convention.strip().upper().replace(" ", "")
    actual = (end - start).days
    if key in {"30/360", "30E/360", "BOND"}:
        days = 360 * (end.year - start.year) + 30 * (end.month - start.month) + \
               (min(end.day, 30) - min(start.day, 30))
        return days, days / 360.0
    if key in {"ACT/360", "ACTUAL/360"}:
        return actual, actual / 360.0
    if key in {"ACT/ACT", "ACTUAL/ACTUAL"}:
        return actual, actual / 365.25
    return actual, actual / 365.0


def _day_count(body: Mapping[str, Any]) -> Dict[str, Any]:
    start, end = _iso_date(body, "start_date"), _iso_date(body, "end_date")
    convention = str(body.get("convention", "ACT/365"))
    days, fraction = _day_count_values(start, end, convention)
    return {"day_count": days, "year_fraction": fraction, "convention": convention}


def _day_count_fraction(body: Mapping[str, Any]) -> Dict[str, Any]:
    result = _day_count(body)
    return {"year_fraction": result["year_fraction"], "day_count": result["day_count"],
            "convention": result["convention"]}


def _batch_year_fraction(body: Mapping[str, Any]) -> Dict[str, Any]:
    pairs = body.get("date_pairs", [])
    if not isinstance(pairs, list) or not pairs:
        raise ValueError("date_pairs must be a non-empty list")
    convention = str(body.get("convention", "ACT/365"))
    values = []
    for pair in pairs:
        if not isinstance(pair, list) or len(pair) != 2:
            raise ValueError("each date pair must contain start and end")
        start, end = date.fromisoformat(str(pair[0])), date.fromisoformat(str(pair[1]))
        days, fraction = _day_count_values(start, end, convention)
        values.append({"start_date": start.isoformat(), "end_date": end.isoformat(),
                       "day_count": days, "year_fraction": fraction})
    return {"results": values, "convention": convention}


def _day_count_conventions(_body: Mapping[str, Any]) -> Dict[str, Any]:
    return {"conventions": ["ACT/365", "ACT/360", "ACT/ACT", "30/360"]}


def _add_months(value: date, months: int) -> date:
    index = value.month - 1 + months
    year, month = value.year + index // 12, index % 12 + 1
    return date(year, month, min(value.day, calendar.monthrange(year, month)[1]))


def _schedule_generate(body: Mapping[str, Any]) -> Dict[str, Any]:
    start = _iso_date(body, "effective_date")
    end = _iso_date(body, "termination_date")
    if end <= start:
        raise ValueError("termination_date must follow effective_date")
    frequency = str(body.get("frequency", "6M")).strip().upper()
    if len(frequency) < 2 or not frequency[:-1].isdigit() or frequency[-1] not in {"M", "Y"}:
        raise ValueError("frequency must be a positive month or year tenor")
    months = int(frequency[:-1]) * (12 if frequency[-1] == "Y" else 1)
    if months <= 0:
        raise ValueError("frequency must be positive")
    dates, cursor = [start], start
    while cursor < end:
        cursor = min(_add_months(cursor, months), end)
        if cursor == dates[-1]:
            break
        dates.append(cursor)
    convention = str(body.get("convention", "unadjusted"))
    adjusted = [_adjust_business_date(value, convention) for value in dates]
    return {"dates": [value.isoformat() for value in adjusted], "frequency": frequency,
            "convention": convention, "periods": len(adjusted) - 1, "calendar": "weekends"}


# ── Numerical methods (pure Python, deterministic) ──────────────────────────

def _numeric_function(name: str, value: float | Sequence[float]) -> float:
    key = name.strip().lower().replace("-", "_")
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        x = [float(v) for v in value]
        if key == "rosenbrock":
            return sum(100.0 * (b - a * a) ** 2 + (1.0 - a) ** 2 for a, b in zip(x, x[1:]))
        if key in {"sphere", "quadratic"}:
            return sum(v * v for v in x)
        if key == "sum":
            return sum(x)
        if len(x) == 1:
            value = x[0]
        else:
            raise ValueError(f"unknown vector function: {name}")
    x = float(value)
    functions = {
        "sin": math.sin, "cos": math.cos, "tan": math.tan, "exp": math.exp,
        "log": math.log, "sqrt": math.sqrt, "abs": abs,
        "square": lambda v: v * v, "cube": lambda v: v ** 3,
    }
    if key not in functions:
        raise ValueError(f"unknown function: {name}")
    return float(functions[key](x))


def _numerical_derivative(body: Mapping[str, Any]) -> Dict[str, float]:
    x = _number(body, "x")
    h = float(body.get("h", 1e-5))
    if h <= 0:
        raise ValueError("h must be positive")
    name = str(body.get("func_name", ""))
    value = (_numeric_function(name, x + h) - _numeric_function(name, x - h)) / (2 * h)
    return {"derivative": value, "x": x, "step": h}


def _numerical_gradient(body: Mapping[str, Any]) -> Dict[str, Any]:
    point = _vector(body, "x")
    h = float(body.get("h", 1e-5))
    name = str(body.get("func_name", ""))
    gradient = []
    for index in range(len(point)):
        left, right = list(point), list(point)
        left[index] -= h
        right[index] += h
        gradient.append((_numeric_function(name, right) - _numeric_function(name, left)) / (2 * h))
    return {"gradient": gradient, "value": _numeric_function(name, point), "x": point}


def _numerical_hessian(body: Mapping[str, Any]) -> Dict[str, Any]:
    point = _vector(body, "x")
    h = float(body.get("h", 1e-4))
    name = str(body.get("func_name", ""))
    base = _numeric_function(name, point)
    matrix = [[0.0] * len(point) for _ in point]
    for i in range(len(point)):
        for j in range(i, len(point)):
            if i == j:
                left, right = list(point), list(point)
                left[i] -= h
                right[i] += h
                value = (_numeric_function(name, right) - 2 * base + _numeric_function(name, left)) / (h * h)
            else:
                pp, pm, mp, mm = (list(point) for _ in range(4))
                pp[i] += h; pp[j] += h
                pm[i] += h; pm[j] -= h
                mp[i] -= h; mp[j] += h
                mm[i] -= h; mm[j] -= h
                value = (_numeric_function(name, pp) - _numeric_function(name, pm) -
                         _numeric_function(name, mp) + _numeric_function(name, mm)) / (4 * h * h)
            matrix[i][j] = matrix[j][i] = value
    return {"hessian": matrix, "value": base, "x": point}


def _convolve(body: Mapping[str, Any]) -> Dict[str, Any]:
    left, right = _vector(body, "a"), _vector(body, "b")
    values = [0.0] * (len(left) + len(right) - 1)
    for i, a in enumerate(left):
        for j, b in enumerate(right):
            values[i + j] += a * b
    return {"values": values}


def _dft(values: Sequence[complex], inverse: bool = False) -> list[complex]:
    size = len(values)
    direction = 1 if inverse else -1
    scale = size if inverse else 1
    return [sum(value * complex(math.cos(direction * 2 * math.pi * k * n / size),
                                math.sin(direction * 2 * math.pi * k * n / size))
                for n, value in enumerate(values)) / scale for k in range(size)]


def _fft(body: Mapping[str, Any], inverse: bool = False) -> Dict[str, Any]:
    raw = body.get("data", [])
    if not isinstance(raw, list) or not raw:
        raise ValueError("data must be a non-empty list")
    values = []
    for item in raw:
        if isinstance(item, Mapping):
            values.append(complex(float(item.get("real", 0.0)), float(item.get("imag", 0.0))))
        else:
            values.append(complex(float(item), 0.0))
    transformed = _dft(values, inverse=inverse)
    return {"real": [value.real for value in transformed], "imag": [value.imag for value in transformed],
            "magnitude": [abs(value) for value in transformed], "size": len(transformed)}


def _integrate(body: Mapping[str, Any]) -> Dict[str, Any]:
    name = str(body.get("func_name", ""))
    if "bounds" in body:
        bounds = body.get("bounds")
        if not isinstance(bounds, list) or len(bounds) != 1 or len(bounds[0]) != 2:
            raise ValueError("only one-dimensional bounds are supported")
        a, b = map(float, bounds[0])
    else:
        a, b = _number(body, "a"), _number(body, "b")
    n = max(2, min(100000, int(body.get("n", body.get("samples", 1024)))))
    if n % 2:
        n += 1
    step = (b - a) / n
    total = _numeric_function(name, a) + _numeric_function(name, b)
    total += 4 * sum(_numeric_function(name, a + index * step) for index in range(1, n, 2))
    total += 2 * sum(_numeric_function(name, a + index * step) for index in range(2, n, 2))
    return {"value": total * step / 3.0, "evaluations": n + 1, "method": "deterministic_simpson"}


def _rect_matrix(body: Mapping[str, Any], name: str) -> list[list[float]]:
    raw = body.get(name, [])
    rows = [[float(value) for value in row] for row in raw] if isinstance(raw, list) else []
    if not rows or not rows[0] or any(len(row) != len(rows[0]) for row in rows):
        raise ValueError(f"{name} must be a non-empty rectangular matrix")
    if any(not math.isfinite(value) for row in rows for value in row):
        raise ValueError(f"{name} must contain finite numbers")
    return rows


def _transpose_matrix(matrix: Sequence[Sequence[float]]) -> list[list[float]]:
    return [list(column) for column in zip(*matrix)]


def _matmul_values(left: Sequence[Sequence[float]], right: Sequence[Sequence[float]]) -> list[list[float]]:
    if len(left[0]) != len(right):
        raise ValueError("matrix dimensions do not align")
    columns = _transpose_matrix(right)
    return [[sum(a * b for a, b in zip(row, column)) for column in columns] for row in left]


def _solve_matrix(matrix: Sequence[Sequence[float]], vector: Sequence[float]) -> list[float]:
    if len(matrix) != len(matrix[0]) or len(vector) != len(matrix):
        raise ValueError("A must be square and match b")
    return _matvec(_inverse(matrix), vector)


def _linalg_dot(body: Mapping[str, Any]) -> Dict[str, float]:
    left, right = _vector(body, "a"), _vector(body, "b")
    if len(left) != len(right):
        raise ValueError("a and b must have equal length")
    return {"value": sum(a * b for a, b in zip(left, right))}


def _linalg_inverse(body: Mapping[str, Any]) -> Dict[str, Any]:
    matrix = _rect_matrix(body, "A")
    if len(matrix) != len(matrix[0]):
        raise ValueError("A must be square")
    return {"inverse": _inverse(matrix)}


def _linalg_solve(body: Mapping[str, Any]) -> Dict[str, Any]:
    return {"solution": _solve_matrix(_rect_matrix(body, "A"), _vector(body, "b"))}


def _linalg_lstsq(body: Mapping[str, Any]) -> Dict[str, Any]:
    matrix, vector = _rect_matrix(body, "A"), _vector(body, "b")
    if len(matrix) != len(vector):
        raise ValueError("A row count must match b")
    transposed = _transpose_matrix(matrix)
    normal = _matmul_values(transposed, matrix)
    rhs = [sum(value * target for value, target in zip(column, vector)) for column in transposed]
    solution = _solve_matrix(normal, rhs)
    residuals = [target - sum(value * weight for value, weight in zip(row, solution))
                 for row, target in zip(matrix, vector)]
    return {"solution": solution, "residual_sum_squares": sum(value * value for value in residuals)}


def _linalg_decompose(body: Mapping[str, Any]) -> Dict[str, Any]:
    matrix = _rect_matrix(body, "A")
    if len(matrix) != len(matrix[0]):
        raise ValueError("A must be square")
    lower = _cholesky({"matrix": matrix})["lower"]
    return {"lower": lower, "upper": _transpose_matrix(lower), "method": "cholesky"}


def _linalg_matmul(body: Mapping[str, Any]) -> Dict[str, Any]:
    return {"matrix": _matmul_values(_rect_matrix(body, "A"), _rect_matrix(body, "B"))}


def _linalg_matvec(body: Mapping[str, Any]) -> Dict[str, Any]:
    matrix, vector = _rect_matrix(body, "A"), _vector(body, "x")
    if len(matrix[0]) != len(vector):
        raise ValueError("A column count must match x")
    return {"vector": _matvec(matrix, vector)}


def _linalg_norm(body: Mapping[str, Any]) -> Dict[str, float]:
    vector = _vector(body, "v")
    return {"norm": math.sqrt(sum(value * value for value in vector))}


def _linalg_outer(body: Mapping[str, Any]) -> Dict[str, Any]:
    left, right = _vector(body, "a"), _vector(body, "b")
    return {"matrix": [[a * b for b in right] for a in left]}


def _linalg_transpose(body: Mapping[str, Any]) -> Dict[str, Any]:
    return {"matrix": _transpose_matrix(_rect_matrix(body, "A"))}


def _interpolation_evaluate(body: Mapping[str, Any]) -> Dict[str, float]:
    return {"value": _linear(_vector(body, "x_data"), _vector(body, "y_data"), _number(body, "xi"))}


def _spline_curve(body: Mapping[str, Any]) -> Dict[str, Any]:
    x_values, y_values = _vector(body, "x_data"), _vector(body, "y_data")
    raw = body.get("xi", [])
    queries = raw if isinstance(raw, list) else [raw]
    return {"values": [_linear(x_values, y_values, float(query)) for query in queries],
            "x": [float(query) for query in queries], "method": "piecewise_linear"}


def _spline_derivative(body: Mapping[str, Any]) -> Dict[str, float]:
    x_values, y_values, query = _vector(body, "x_data"), _vector(body, "y_data"), _number(body, "xi")
    pairs = sorted(zip(x_values, y_values))
    for (x0, y0), (x1, y1) in zip(pairs, pairs[1:]):
        if x0 <= query <= x1:
            return {"derivative": (y1 - y0) / (x1 - x0), "x": query}
    edge = pairs[:2] if query < pairs[0][0] else pairs[-2:]
    return {"derivative": (edge[1][1] - edge[0][1]) / (edge[1][0] - edge[0][0]), "x": query}


def _least_squares_fit(body: Mapping[str, Any]) -> Dict[str, Any]:
    x_values, y_values = _vector(body, "x_data"), _vector(body, "y_data")
    if len(x_values) != len(y_values):
        raise ValueError("x_data and y_data must have equal length")
    model = str(body.get("model", "linear")).lower()
    transformed = [math.log(value) for value in y_values] if "exponential" in model else y_values
    count, sx, sy = len(x_values), sum(x_values), sum(transformed)
    sxx = sum(value * value for value in x_values)
    sxy = sum(x * y for x, y in zip(x_values, transformed))
    denominator = count * sxx - sx * sx
    if abs(denominator) < 1e-14:
        raise ValueError("x_data does not identify a fit")
    slope = (count * sxy - sx * sy) / denominator
    intercept = (sy - slope * sx) / count
    if "exponential" in model:
        parameters = [math.exp(intercept), slope, 0.0]
        fitted = [parameters[0] * math.exp(slope * value) for value in x_values]
    else:
        parameters = [intercept, slope]
        fitted = [intercept + slope * value for value in x_values]
    return {"parameters": parameters, "fitted": fitted, "model": model,
            "residual_sum_squares": sum((actual - estimate) ** 2 for actual, estimate in zip(y_values, fitted))}


def _root_bisection(body: Mapping[str, Any]) -> Dict[str, Any]:
    name = str(body.get("func_name", ""))
    left, right = _number(body, "a"), _number(body, "b")
    f_left, f_right = _numeric_function(name, left), _numeric_function(name, right)
    if f_left * f_right > 0:
        raise ValueError("a and b must bracket a root")
    tolerance = float(body.get("tol", 1e-12))
    iterations = 0
    for iterations in range(1, 201):
        middle = (left + right) / 2
        f_middle = _numeric_function(name, middle)
        if abs(f_middle) <= tolerance or abs(right - left) <= tolerance:
            break
        if f_left * f_middle <= 0:
            right, f_right = middle, f_middle
        else:
            left, f_left = middle, f_middle
    return {"root": middle, "value": f_middle, "iterations": iterations}


def _root_newton(body: Mapping[str, Any]) -> Dict[str, Any]:
    name, value = str(body.get("func_name", "")), _number(body, "x0")
    tolerance = float(body.get("tol", 1e-12))
    for iteration in range(1, 101):
        f_value = _numeric_function(name, value)
        h = 1e-6 * max(1.0, abs(value))
        derivative = (_numeric_function(name, value + h) - _numeric_function(name, value - h)) / (2 * h)
        if abs(derivative) < 1e-14:
            raise ValueError("derivative vanished during Newton iteration")
        next_value = value - f_value / derivative
        if abs(next_value - value) <= tolerance:
            value = next_value
            break
        value = next_value
    return {"root": value, "value": _numeric_function(name, value), "iterations": iteration}


def _root_nd(body: Mapping[str, Any]) -> Dict[str, Any]:
    point = _vector(body, "x0")
    if str(body.get("system", "")) != "nonlinear_2x2" or len(point) != 2:
        raise ValueError("only the nonlinear_2x2 system is supported")
    tolerance = float(body.get("tol", 1e-10))
    for iteration in range(1, 101):
        x, y = point
        values = [x * x + y * y - 4.0, x - y - 1.0]
        jacobian = [[2 * x, 2 * y], [1.0, -1.0]]
        delta = _solve_matrix(jacobian, [-value for value in values])
        point = [value + step for value, step in zip(point, delta)]
        if max(map(abs, delta)) <= tolerance:
            break
    return {"root": point, "residual": [point[0] ** 2 + point[1] ** 2 - 4.0, point[0] - point[1] - 1.0],
            "iterations": iteration}


def _optimize_minimize(body: Mapping[str, Any]) -> Dict[str, Any]:
    point = _vector(body, "x0")
    name = str(body.get("func_name", ""))
    value = _numeric_function(name, point)
    step = float(body.get("step", 0.01))
    for iteration in range(1, 20001):
        gradient = _numerical_gradient({"func_name": name, "x": point, "h": 1e-5})["gradient"]
        candidate = [x - step * g for x, g in zip(point, gradient)]
        candidate_value = _numeric_function(name, candidate)
        if candidate_value < value:
            if max(abs(a - b) for a, b in zip(candidate, point)) < 1e-9:
                point, value = candidate, candidate_value
                break
            point, value = candidate, candidate_value
            step = min(step * 1.02, 0.1)
        else:
            step *= 0.5
            if step < 1e-12:
                break
    return {"x": point, "value": value, "iterations": iteration, "success": value < 1e-8}


def _ode_solve(body: Mapping[str, Any]) -> Dict[str, Any]:
    span = body.get("t_span", [])
    state = _vector(body, "y0")
    if not isinstance(span, list) or len(span) != 2:
        raise ValueError("t_span must contain start and end")
    start, end = map(float, span)
    if end <= start:
        raise ValueError("t_span end must follow start")
    step = float(body.get("h", 0.01))
    count = max(1, min(100000, math.ceil((end - start) / step)))
    step = (end - start) / count
    system = str(body.get("system", "lotka_volterra"))
    params = [float(value) for value in body.get("params", [1.5, 1.0, 3.0, 1.0])]
    if system != "lotka_volterra" or len(state) != 2 or len(params) != 4:
        raise ValueError("only lotka_volterra with two states and four params is supported")
    alpha, beta, gamma, delta = params
    def rhs(values):
        prey, predator = values
        return [alpha * prey - beta * prey * predator, delta * prey * predator - gamma * predator]
    times, states = [start], [list(state)]
    time = start
    for index in range(count):
        k1 = rhs(state)
        k2 = rhs([value + step * slope / 2 for value, slope in zip(state, k1)])
        k3 = rhs([value + step * slope / 2 for value, slope in zip(state, k2)])
        k4 = rhs([value + step * slope for value, slope in zip(state, k3)])
        state = [value + step * (a + 2 * b + 2 * c + d) / 6
                 for value, a, b, c, d in zip(state, k1, k2, k3, k4)]
        time = start + (index + 1) * step
        if index + 1 == count or (index + 1) % max(1, count // 500) == 0:
            times.append(time); states.append(list(state))
    return {"times": times, "states": states, "final_state": state, "method": "rk4"}


def _basis(body: Mapping[str, Any]) -> Dict[str, float]:
    return {"basis": _number(body, "futures") - _number(body, "spot")}


def _carry(body: Mapping[str, Any]) -> Dict[str, float]:
    time = _positive(body, "time_to_expiry")
    return {"carry": (_number(body, "futures") / _positive(body, "spot") - 1) / time}


def _solver_bond_values(body: Mapping[str, Any], yield_rate: float | None = None) -> Dict[str, float]:
    coupon = _number(body, "coupon", "coupon_rate")
    maturity = _positive(body, "maturity", "time_to_maturity")
    face = float(body.get("face_value", 100.0))
    frequency = max(1, int(body.get("frequency", 2)))
    periods = max(1, round(maturity * frequency))
    rate = _number(body, "ytm", "yield", "yield_rate") if yield_rate is None else yield_rate
    if rate <= -frequency:
        raise ValueError("yield must be greater than -frequency")
    periodic = rate / frequency
    cashflows = [face * coupon / frequency] * periods
    cashflows[-1] += face
    present = [cashflow / (1 + periodic) ** period
               for period, cashflow in enumerate(cashflows, 1)]
    price = sum(present)
    times = [period / frequency for period in range(1, periods + 1)]
    macaulay = sum(time * value for time, value in zip(times, present)) / price
    modified = macaulay / (1 + periodic)
    convexity = sum(period * (period + 1) * value
                    for period, value in enumerate(present, 1)) / (
                        price * (1 + periodic) ** 2 * frequency ** 2)
    return {"price": price, "duration": macaulay, "modified_duration": modified,
            "convexity": convexity, "yield": rate}


def _solve_monotonic(target, low: float = -0.999999, high: float = 10.0) -> float:
    low_value, high_value = target(low), target(high)
    if low_value == 0:
        return low
    if high_value == 0:
        return high
    if low_value * high_value > 0:
        raise ValueError("could not bracket a solution")
    for _ in range(160):
        middle = (low + high) / 2
        value = target(middle)
        if value == 0:
            return middle
        if low_value * value <= 0:
            high, high_value = middle, value
        else:
            low, low_value = middle, value
    return (low + high) / 2


def _solver_bond_yield(body: Mapping[str, Any]) -> Dict[str, float]:
    price = _positive(body, "price", "clean_price")
    answer = _solve_monotonic(lambda rate: _solver_bond_values(body, rate)["price"] - price,
                              low=-0.99, high=10.0)
    return {"yield": answer, "ytm": answer}


def _solver_duration(body: Mapping[str, Any]) -> Dict[str, float]:
    values = _solver_bond_values(body)
    return {"duration": values["duration"], "price": values["price"]}


def _solver_modified_duration(body: Mapping[str, Any]) -> Dict[str, float]:
    values = _solver_bond_values(body)
    return {"modified_duration": values["modified_duration"], "price": values["price"]}


def _solver_convexity(body: Mapping[str, Any]) -> Dict[str, float]:
    values = _solver_bond_values(body)
    return {"convexity": values["convexity"], "price": values["price"]}


def _solver_convexity_adjustment(body: Mapping[str, Any]) -> Dict[str, float]:
    forward = _number(body, "forward_rate", "rate")
    volatility = _positive(body, "volatility")
    first, second = _positive(body, "t1"), _positive(body, "t2")
    adjustment = 0.5 * volatility * volatility * first * second * forward
    return {"convexity_adjustment": adjustment, "futures_rate": forward + adjustment}


def _solver_forward_futures(body: Mapping[str, Any]) -> Dict[str, float]:
    rate = _number(body, "rate", "forward_rate")
    adjusted = _solver_convexity_adjustment({**body, "forward_rate": rate})
    return {"forward_rate": rate, "futures_rate": adjusted["futures_rate"],
            "convexity_adjustment": adjusted["convexity_adjustment"]}


def _solver_pv01(body: Mapping[str, Any]) -> Dict[str, float]:
    discount_factors = _vector(body, "discount_factors")
    frequency = max(1, int(body.get("frequency", 1)))
    notional = _positive(body, "notional")
    pv01 = notional * 0.0001 * sum(discount_factors) / frequency
    return {"pv01": pv01, "annuity": sum(discount_factors) / frequency}


def _solver_par_rate(body: Mapping[str, Any]) -> Dict[str, float]:
    discount_factors = _vector(body, "discount_factors")
    frequency = max(1, int(body.get("frequency", 1)))
    annuity = sum(discount_factors) / frequency
    return {"par_rate": (1 - discount_factors[-1]) / annuity, "annuity": annuity}


def _solver_implied_vol(body: Mapping[str, Any], *, black76: bool = False) -> Dict[str, float]:
    trial = dict(body)
    trial["market_price"] = _positive(body, "price", "market_price")
    trial["time_to_maturity"] = _positive(body, "time", "time_to_maturity")
    trial["risk_free_rate"] = _number(body, "rate", "risk_free_rate")
    trial.setdefault("option_type", "call")
    if black76:
        trial["forward"] = _positive(body, "forward")
    else:
        trial["spot"] = _positive(body, "spot")
    return _implied_vol(trial, black76=black76)


def _cashflow_times(body: Mapping[str, Any]) -> tuple[list[float], list[float]]:
    cashflows = _vector(body, "cashflows")
    raw_dates = body.get("dates")
    if raw_dates is None:
        return cashflows, [float(index) for index in range(len(cashflows))]
    if not isinstance(raw_dates, list) or len(raw_dates) != len(cashflows):
        raise ValueError("dates must match cashflows")
    if raw_dates and isinstance(raw_dates[0], str):
        parsed = [date.fromisoformat(str(value)) for value in raw_dates]
        times = [(value - parsed[0]).days / 365.0 for value in parsed]
    else:
        times = [float(value) for value in raw_dates]
    return cashflows, times


def _solver_return(body: Mapping[str, Any], label: str) -> Dict[str, float]:
    cashflows, times = _cashflow_times(body)
    answer = _solve_monotonic(
        lambda rate: sum(value / (1 + rate) ** time for value, time in zip(cashflows, times)),
        low=-0.999999, high=100.0)
    return {label: answer}


def _discounted_spread(cashflows: Sequence[float], times: Sequence[float],
                       base_rates: Sequence[float], spread: float,
                       base_times: Sequence[float] | None = None) -> float:
    curve_times = list(base_times) if base_times is not None else list(times)
    return sum(cashflow * math.exp(-(_linear(curve_times, base_rates, time) + spread) * time)
               for cashflow, time in zip(cashflows, times))


def _solver_z_spread(body: Mapping[str, Any]) -> Dict[str, float]:
    cashflows, times = _vector(body, "cashflows"), _vector(body, "times")
    rates = _vector(body, "base_rates")
    if len(cashflows) != len(times) or len(rates) != len(times):
        raise ValueError("cashflows, times, and base_rates must match")
    price = _positive(body, "price")
    spread = _solve_monotonic(lambda value: _discounted_spread(cashflows, times, rates, value) - price,
                              low=-1.0, high=5.0)
    return {"z_spread": spread, "z_spread_bps": spread * 10000}


def _solver_oas(body: Mapping[str, Any]) -> Dict[str, float]:
    scenarios = body.get("scenario_cashflows", [])
    probabilities = _vector(body, "probabilities")
    times = _vector(body, "times")
    if not isinstance(scenarios, list) or len(scenarios) != len(probabilities):
        raise ValueError("scenario_cashflows must match probabilities")
    expected = [sum(float(row[index]) * probability
                    for row, probability in zip(scenarios, probabilities))
                for index in range(len(times))]
    rates, base_times = _vector(body, "base_rates"), _vector(body, "base_times")
    price = _positive(body, "price")
    spread = _solve_monotonic(
        lambda value: _discounted_spread(expected, times, rates, value, base_times) - price,
        low=-1.0, high=5.0)
    return {"oas": spread, "oas_bps": spread * 10000}


def _solver_asw(body: Mapping[str, Any]) -> Dict[str, float]:
    maturity = _positive(body, "maturity")
    frequency = max(1, int(body.get("frequency", 2)))
    periods = max(1, round(maturity * frequency))
    times = [(index + 1) / frequency for index in range(periods)]
    base_times, rates = _vector(body, "base_times"), _vector(body, "base_rates")
    discount_factors = [math.exp(-_linear(base_times, rates, time) * time) for time in times]
    par = (1 - discount_factors[-1]) / (sum(discount_factors) / frequency)
    ytm = _solver_bond_yield({**body, "price": _positive(body, "bond_price")})["yield"]
    spread = ytm - par
    return {"asw_spread": spread, "asw_spread_bps": spread * 10000,
            "bond_yield": ytm, "swap_rate": par}


def _solver_bootstrap_curve(body: Mapping[str, Any]) -> Dict[str, Any]:
    instruments = body.get("instruments", [])
    if not isinstance(instruments, list) or not instruments:
        raise ValueError("instruments must be a non-empty list")
    nodes = []
    for instrument in sorted(instruments, key=lambda item: float(item.get("tenor", 0))):
        tenor = float(instrument.get("tenor", 0))
        rate = float(instrument.get("rate", 0))
        if tenor <= 0 or not math.isfinite(rate):
            raise ValueError("each instrument needs a positive tenor and finite rate")
        kind = str(instrument.get("instrument_type", "deposit"))
        factor = 1 / (1 + rate * tenor) if kind in {"deposit", "fra", "future"} else math.exp(-rate * tenor)
        zero = -math.log(factor) / tenor
        nodes.append({"tenor": tenor, "discount_factor": factor, "zero_rate": zero,
                      "instrument_type": kind})
    return {"reference_date": str(body.get("reference_date", "")), "nodes": nodes,
            "interpolation": str(body.get("interpolation", "log_linear"))}


def _solver_vasicek(body: Mapping[str, Any]) -> Dict[str, float]:
    rates, times = _vector(body, "zero_rates"), _vector(body, "times")
    if len(rates) != len(times) or len(rates) < 3:
        raise ValueError("zero_rates and times must match and contain at least three observations")
    long_term = sum(rates[-min(3, len(rates)):]) / min(3, len(rates))
    deltas = [(rates[index + 1] - rates[index]) / (times[index + 1] - times[index])
              for index in range(len(rates) - 1)]
    deviations = [long_term - rates[index] for index in range(len(deltas))]
    denominator = sum(value * value for value in deviations)
    reversion = max(1e-6, sum(x * y for x, y in zip(deviations, deltas)) / denominator) \
        if denominator else 1e-6
    residuals = [delta - reversion * deviation for delta, deviation in zip(deltas, deviations)]
    volatility = math.sqrt(sum(value * value for value in residuals) / len(residuals))
    return {"mean_reversion": reversion, "long_term_mean": long_term,
            "volatility": volatility, "initial_rate": rates[0]}


def _vector(body: Mapping[str, Any], name: str) -> list[float]:
    values = [float(value) for value in body.get(name, [])]
    if not values or any(not math.isfinite(value) for value in values):
        raise ValueError(f"{name} must contain finite numbers")
    return values


def _matrix(body: Mapping[str, Any], name: str) -> list[list[float]]:
    rows = [[float(value) for value in row] for row in body.get(name, [])]
    if not rows or any(len(row) != len(rows) for row in rows):
        raise ValueError(f"{name} must be a non-empty square matrix")
    if any(not math.isfinite(value) for row in rows for value in row):
        raise ValueError(f"{name} must contain finite numbers")
    return rows


def _linear(x_values: Sequence[float], y_values: Sequence[float], query: float) -> float:
    if len(x_values) != len(y_values) or not x_values:
        raise ValueError("curve coordinates must have equal non-zero length")
    pairs = sorted(zip(map(float, x_values), map(float, y_values)))
    if query <= pairs[0][0]:
        return pairs[0][1]
    if query >= pairs[-1][0]:
        return pairs[-1][1]
    for (x0, y0), (x1, y1) in zip(pairs, pairs[1:]):
        if x0 <= query <= x1:
            weight = (query - x0) / (x1 - x0)
            return y0 + weight * (y1 - y0)
    raise ValueError("query is outside the curve")


def _curve_rate(body: Mapping[str, Any]) -> tuple[float, float]:
    tenor = _positive(body, "query_tenor")
    return tenor, _linear(_vector(body, "tenors"), _vector(body, "values"), tenor)


def _curve_zero(body: Mapping[str, Any]) -> Dict[str, float]:
    tenor, rate = _curve_rate(body)
    return {"tenor": tenor, "zero_rate": rate}


def _curve_discount(body: Mapping[str, Any]) -> Dict[str, float]:
    tenor, rate = _curve_rate(body)
    return {"tenor": tenor, "zero_rate": rate, "discount_factor": math.exp(-rate * tenor)}


def _curve_forward(body: Mapping[str, Any]) -> Dict[str, float]:
    start = _number(body, "start_tenor")
    end = _positive(body, "end_tenor")
    if end <= start:
        raise ValueError("end_tenor must be greater than start_tenor")
    tenors, values = _vector(body, "tenors"), _vector(body, "values")
    start_rate = _linear(tenors, values, start)
    end_rate = _linear(tenors, values, end)
    return {"forward_rate": (end_rate * end - start_rate * start) / (end - start)}


def _curve_points(body: Mapping[str, Any]) -> Dict[str, Any]:
    tenors, values = _vector(body, "tenors"), _vector(body, "values")
    queries = _vector(body, "query_tenors")
    return {"points": [{"tenor": tenor, "zero_rate": _linear(tenors, values, tenor)} for tenor in queries]}


def _curve_shift(body: Mapping[str, Any]) -> Dict[str, Any]:
    tenors, values = _vector(body, "tenors"), _vector(body, "values")
    shift = _number(body, "shift_bps") / 10000.0
    return {"tenors": tenors, "values": [value + shift for value in values], "shift_bps": shift * 10000}


def _interpolate(body: Mapping[str, Any]) -> Dict[str, Any]:
    x_values, y_values = _vector(body, "x"), _vector(body, "y")
    raw = body.get("query_x")
    if isinstance(raw, list):
        return {"values": [_linear(x_values, y_values, float(query)) for query in raw]}
    return {"value": _linear(x_values, y_values, float(raw))}


def _curve_build(body: Mapping[str, Any]) -> Dict[str, Any]:
    tenors, values = _vector(body, "tenors"), _vector(body, "values")
    if len(tenors) != len(values):
        raise ValueError("tenors and values must match")
    points = [{"tenor": tenor, "zero_rate": rate,
               "discount_factor": math.exp(-rate * tenor)}
              for tenor, rate in sorted(zip(tenors, values))]
    return {"reference_date": str(body.get("reference_date", "")), "points": points,
            "interpolation": str(body.get("interpolation", "linear"))}


def _interpolate_derivative(body: Mapping[str, Any]) -> Dict[str, float]:
    x_values, y_values = _vector(body, "x"), _vector(body, "y")
    if len(x_values) != len(y_values) or len(x_values) < 2:
        raise ValueError("x and y must match and contain at least two points")
    pairs = sorted(zip(x_values, y_values)); query = _number(body, "query_x")
    if query <= pairs[0][0]:
        left, right = pairs[0], pairs[1]
    elif query >= pairs[-1][0]:
        left, right = pairs[-2], pairs[-1]
    else:
        left, right = next((pairs[index], pairs[index + 1]) for index in range(len(pairs) - 1)
                           if pairs[index][0] <= query <= pairs[index + 1][0])
    return {"derivative": (right[1] - left[1]) / (right[0] - left[0]), "query_x": query}


def _instantaneous_forward(body: Mapping[str, Any]) -> Dict[str, float]:
    tenors, values = _vector(body, "tenors"), _vector(body, "values")
    query = _positive(body, "query_tenor")
    rate = _linear(tenors, values, query)
    derivative = _interpolate_derivative({"x": tenors, "y": values, "query_x": query})["derivative"]
    return {"instantaneous_forward_rate": rate + query * derivative,
            "zero_rate": rate, "slope": derivative}


def _curve_twist(body: Mapping[str, Any]) -> Dict[str, Any]:
    tenors, values = _vector(body, "tenors"), _vector(body, "values")
    pivot = _positive(body, "pivot_years")
    short = _number(body, "short_shift_bps") / 10000
    long = _number(body, "long_shift_bps") / 10000
    maximum = max(max(tenors), pivot)
    shifted = []
    for tenor, value in zip(tenors, values):
        if tenor <= pivot:
            weight = tenor / pivot
            shift = short * (1 - weight)
        else:
            weight = min(1.0, (tenor - pivot) / max(1e-12, maximum - pivot))
            shift = long * weight
        shifted.append(value + shift)
    return {"tenors": tenors, "values": shifted, "pivot_years": pivot}


def _curve_butterfly(body: Mapping[str, Any]) -> Dict[str, Any]:
    tenors, values = _vector(body, "tenors"), _vector(body, "values")
    belly = _positive(body, "belly_years")
    wing = _number(body, "wing_shift_bps") / 10000
    belly_shift = _number(body, "belly_shift_bps") / 10000
    width = max(belly, max(tenors) - belly, 1e-12)
    shifted = []
    for tenor, value in zip(tenors, values):
        belly_weight = max(0.0, 1 - abs(tenor - belly) / width)
        shifted.append(value + belly_weight * belly_shift + (1 - belly_weight) * wing)
    return {"tenors": tenors, "values": shifted, "belly_years": belly}


def _key_rate_shift(body: Mapping[str, Any]) -> Dict[str, Any]:
    tenors, values = _vector(body, "tenors"), _vector(body, "values")
    key = _positive(body, "tenor_years"); shift = _number(body, "shift_bps") / 10000
    distances = sorted(abs(tenor - key) for tenor in tenors)
    width = distances[1] if len(distances) > 1 and distances[1] > 0 else max(key, 1.0)
    shifted = [value + shift * max(0.0, 1 - abs(tenor - key) / width)
               for tenor, value in zip(tenors, values)]
    return {"tenors": tenors, "values": shifted, "key_tenor": key, "shift_bps": shift * 10000}


def _ns_factor(tenor: float, tau: float) -> tuple[float, float]:
    ratio = tenor / tau
    first = (1 - math.exp(-ratio)) / ratio
    return first, first - math.exp(-ratio)


def _nelson_siegel_evaluate(body: Mapping[str, Any]) -> Dict[str, Any]:
    beta0, beta1, beta2 = (_number(body, "beta0"), _number(body, "beta1"), _number(body, "beta2"))
    tau = _positive(body, "tau"); tenors = _vector(body, "query_tenors")
    rates = []
    for tenor in tenors:
        first, second = _ns_factor(tenor, tau)
        rates.append(beta0 + beta1 * first + beta2 * second)
    return {"tenors": tenors, "rates": rates}


def _linear_coefficients(design: Sequence[Sequence[float]], targets: Sequence[float]) -> list[float]:
    transpose = _transpose_matrix(design)
    gram = _matmul_values(transpose, design)
    rhs = [sum(row[index] * value for row, value in zip(design, targets))
           for index in range(len(transpose))]
    return _solve_matrix(gram, rhs)


def _nelson_siegel_fit(body: Mapping[str, Any]) -> Dict[str, Any]:
    tenors, rates = _vector(body, "tenors"), _vector(body, "rates")
    if len(tenors) != len(rates) or len(rates) < 3:
        raise ValueError("tenors and rates must match and contain at least three points")
    best = None
    for tau in (0.25, 0.5, 0.75, 1.0, 1.5, 2.0, 3.0, 5.0, 8.0, 12.0):
        design = [[1.0, *_ns_factor(tenor, tau)] for tenor in tenors]
        try:
            beta = _linear_coefficients(design, rates)
        except ValueError:
            continue
        fitted = [sum(coefficient * value for coefficient, value in zip(beta, row)) for row in design]
        error = sum((actual - target) ** 2 for actual, target in zip(fitted, rates)) / len(rates)
        if best is None or error < best[0]:
            best = (error, tau, beta, fitted)
    if best is None:
        raise ValueError("could not fit Nelson-Siegel curve")
    return {"beta0": best[2][0], "beta1": best[2][1], "beta2": best[2][2],
            "tau": best[1], "fitted_rates": best[3], "rmse": math.sqrt(best[0])}


def _nss_evaluate(body: Mapping[str, Any]) -> Dict[str, Any]:
    tenors = _vector(body, "query_tenors")
    beta = [_number(body, f"beta{index}") for index in range(4)]
    tau1, tau2 = _positive(body, "tau1"), _positive(body, "tau2")
    rates = []
    for tenor in tenors:
        first, second = _ns_factor(tenor, tau1)
        _level, third = _ns_factor(tenor, tau2)
        rates.append(beta[0] + beta[1] * first + beta[2] * second + beta[3] * third)
    return {"tenors": tenors, "rates": rates}


def _nss_fit(body: Mapping[str, Any]) -> Dict[str, Any]:
    tenors, rates = _vector(body, "tenors"), _vector(body, "rates")
    if len(tenors) != len(rates) or len(rates) < 4:
        raise ValueError("tenors and rates must match and contain at least four points")
    best = None
    for tau1 in (0.5, 1.0, 1.5, 2.5, 4.0):
        for tau2 in (2.0, 3.0, 5.0, 8.0, 12.0):
            if abs(tau1 - tau2) < 1e-9:
                continue
            design = []
            for tenor in tenors:
                first, second = _ns_factor(tenor, tau1); _level, third = _ns_factor(tenor, tau2)
                design.append([1.0, first, second, third])
            try:
                beta = _linear_coefficients(design, rates)
            except ValueError:
                continue
            fitted = [sum(coefficient * value for coefficient, value in zip(beta, row)) for row in design]
            error = sum((actual - target) ** 2 for actual, target in zip(fitted, rates)) / len(rates)
            if best is None or error < best[0]:
                best = (error, tau1, tau2, beta, fitted)
    if best is None:
        raise ValueError("could not fit NSS curve")
    return {"beta0": best[3][0], "beta1": best[3][1], "beta2": best[3][2],
            "beta3": best[3][3], "tau1": best[1], "tau2": best[2],
            "fitted_rates": best[4], "rmse": math.sqrt(best[0])}


def _curve_roll(body: Mapping[str, Any]) -> Dict[str, Any]:
    tenors, values = _vector(body, "tenors"), _vector(body, "values")
    days = int(body.get("days", 0)); elapsed = days / 365.0
    rolled = [(max(0.0, tenor - elapsed), value) for tenor, value in zip(tenors, values) if tenor > elapsed]
    reference = str(body.get("reference_date", ""))
    rolled_date = (date.fromisoformat(reference) + timedelta(days=days)).isoformat() if reference else ""
    return {"reference_date": rolled_date, "tenors": [item[0] for item in rolled],
            "values": [item[1] for item in rolled], "days": days}


def _curve_scale(body: Mapping[str, Any]) -> Dict[str, Any]:
    tenors, values = _vector(body, "tenors"), _vector(body, "values")
    factor = _number(body, "scale_factor")
    return {"tenors": tenors, "values": [value * factor for value in values], "scale_factor": factor}


def _curve_time_shift(body: Mapping[str, Any]) -> Dict[str, Any]:
    tenors, values = _vector(body, "tenors"), _vector(body, "values")
    shift = _number(body, "shift_days") / 365
    queries = _vector(body, "query_tenors")
    return {"query_tenors": queries,
            "values": [_linear(tenors, values, max(0.0, query + shift)) for query in queries],
            "shift_days": int(body.get("shift_days", 0))}


def _curve_composite(body: Mapping[str, Any]) -> Dict[str, Any]:
    base_tenors, base_values = _vector(body, "base_tenors"), _vector(body, "base_values")
    forecast_tenors, forecast_values = _vector(body, "forecast_tenors"), _vector(body, "forecast_values")
    queries = sorted(set(base_tenors + forecast_tenors))
    values = [0.5 * (_linear(base_tenors, base_values, query) +
                     _linear(forecast_tenors, forecast_values, query)) for query in queries]
    return {"tenors": queries, "values": values, "method": "equal_weight_composite"}


def _curve_proxy(body: Mapping[str, Any]) -> Dict[str, Any]:
    tenors, values = _vector(body, "base_tenors"), _vector(body, "base_values")
    spread = _number(body, "spread_bps") / 10000
    return {"tenors": tenors, "values": [value + spread for value in values],
            "spread_bps": spread * 10000}


def _real_rate_curve(body: Mapping[str, Any]) -> Dict[str, Any]:
    nominal_tenors, nominal = _vector(body, "nominal_tenors"), _vector(body, "nominal_rates")
    inflation_tenors, inflation = _vector(body, "inflation_tenors"), _vector(body, "inflation_rates")
    queries = _vector(body, "query_tenors")
    real = [(1 + _linear(nominal_tenors, nominal, query)) /
            (1 + _linear(inflation_tenors, inflation, query)) - 1 for query in queries]
    return {"tenors": queries, "real_rates": real, "base_cpi": float(body.get("base_cpi", 100.0))}


def _monotonicity_check(body: Mapping[str, Any]) -> Dict[str, Any]:
    values = _vector(body, "y")
    increasing = all(left <= right for left, right in zip(values, values[1:]))
    decreasing = all(left >= right for left, right in zip(values, values[1:]))
    violations = [index for index, (left, right) in enumerate(zip(values, values[1:])) if left > right]
    return {"is_monotonic": increasing or decreasing, "is_increasing": increasing,
            "is_decreasing": decreasing, "violations": violations}


def _smoothness_penalty(body: Mapping[str, Any]) -> Dict[str, float]:
    x_values, y_values = _vector(body, "x"), _vector(body, "y")
    if len(x_values) != len(y_values) or len(y_values) < 3:
        raise ValueError("x and y must match and contain at least three points")
    slopes = [(y_values[index + 1] - y_values[index]) / (x_values[index + 1] - x_values[index])
              for index in range(len(y_values) - 1)]
    penalty = sum((right - left) ** 2 for left, right in zip(slopes, slopes[1:]))
    return {"smoothness_penalty": penalty, "slope_changes": [right - left for left, right in zip(slopes, slopes[1:])]}


def _constrained_curve_fit(body: Mapping[str, Any]) -> Dict[str, Any]:
    tenors, rates = _vector(body, "tenors"), _vector(body, "rates")
    if len(tenors) != len(rates):
        raise ValueError("tenors and rates must match")
    pairs = sorted(zip(tenors, rates)); fitted = []
    for _tenor, rate in pairs:
        fitted.append(max(rate, fitted[-1] if fitted else rate))
    return {"tenors": [item[0] for item in pairs], "fitted_rates": fitted,
            "constraint": "non_decreasing"}


def _multicurve_setup(body: Mapping[str, Any]) -> Dict[str, Any]:
    ois_tenors, ois = _vector(body, "ois_tenors"), _vector(body, "ois_rates")
    ibor_tenors, ibor = _vector(body, "ibor_tenors"), _vector(body, "ibor_rates")
    queries = _vector(body, "query_tenors")
    ois_values = [_linear(ois_tenors, ois, query) for query in queries]
    ibor_values = [_linear(ibor_tenors, ibor, query) for query in queries]
    return {"tenors": queries, "ois_rates": ois_values, "ibor_rates": ibor_values,
            "basis_spreads": [right - left for left, right in zip(ois_values, ibor_values)]}


def _multicurve_basis(body: Mapping[str, Any]) -> Dict[str, float]:
    query = _positive(body, "query_tenor")
    ois = _linear(_vector(body, "ois_tenors"), _vector(body, "ois_rates"), query)
    ibor = _linear(_vector(body, "ibor_tenors"), _vector(body, "ibor_rates"), query)
    return {"basis_spread": ibor - ois, "basis_spread_bps": (ibor - ois) * 10000,
            "ois_rate": ois, "ibor_rate": ibor}


def _cross_currency_basis(body: Mapping[str, Any]) -> Dict[str, Any]:
    tenors, spreads = _vector(body, "tenors"), _vector(body, "basis_spreads_bps")
    queries = _vector(body, "query_tenors")
    values = [_linear(tenors, spreads, query) for query in queries]
    return {"tenors": queries, "basis_spreads_bps": values,
            "domestic_currency": str(body.get("domestic_currency", "")),
            "foreign_currency": str(body.get("foreign_currency", ""))}


def _inflation_curve(body: Mapping[str, Any], *, bootstrap: bool = False) -> Dict[str, Any]:
    tenor_key = "swap_tenors" if bootstrap else "tenors"
    rate_key = "swap_rates" if bootstrap else "inflation_rates"
    tenors, rates = _vector(body, tenor_key), _vector(body, rate_key)
    if len(tenors) != len(rates):
        raise ValueError(f"{tenor_key} and {rate_key} must match")
    base = _positive(body, "base_cpi")
    cpi = [base * (1 + rate) ** tenor for tenor, rate in zip(tenors, rates)]
    return {"tenors": tenors, "inflation_rates": rates, "cpi_values": cpi,
            "base_cpi": base, "method": "swap_bootstrap" if bootstrap else "direct"}


def _inflation_seasonality(body: Mapping[str, Any]) -> Dict[str, Any]:
    factors, cpi, months = (_vector(body, "monthly_factors"), _vector(body, "cpi_values"),
                            _vector(body, "months"))
    if len(factors) != len(cpi) or len(cpi) != len(months):
        raise ValueError("monthly_factors, cpi_values, and months must match")
    adjusted = [value / factor for value, factor in zip(cpi, factors)]
    return {"months": [int(month) for month in months], "seasonally_adjusted_cpi": adjusted,
            "monthly_factors": factors}


def _bond_years(body: Mapping[str, Any]) -> float:
    start = date.fromisoformat(str(body.get("issue_date")))
    maturity = date.fromisoformat(str(body.get("maturity_date")))
    years = (maturity - start).days / 365.25
    if years <= 0:
        raise ValueError("maturity_date must follow issue_date")
    return years


def _bond_metrics(body: Mapping[str, Any]) -> Dict[str, float]:
    years = _bond_years(body)
    coupon = _number(body, "coupon_rate")
    ytm = _number(body, "yield_rate")
    face = float(body.get("face_value", 100.0))
    frequency = int(body.get("frequency", 1))
    periods = max(1, round(years * frequency))
    periodic_yield = ytm / frequency
    cash_coupon = face * coupon / frequency
    cashflows = [cash_coupon] * periods
    cashflows[-1] += face
    times = [(index + 1) / frequency for index in range(periods)]
    present = [cashflow / (1 + periodic_yield) ** (index + 1) for index, cashflow in enumerate(cashflows)]
    price = sum(present)
    macaulay = sum(time * value for time, value in zip(times, present)) / price
    modified = macaulay / (1 + periodic_yield)
    convexity = sum(
        (index + 1) * (index + 2) * value for index, value in enumerate(present)
    ) / (price * (1 + periodic_yield) ** 2 * frequency * frequency)
    return {"price": price, "macaulay_duration": macaulay, "modified_duration": modified,
            "convexity": convexity, "dv01": modified * price * 0.0001}


def _bond_price(body: Mapping[str, Any]) -> Dict[str, float]:
    metrics = _bond_metrics(body)
    return {"price": metrics["price"], "clean_price": metrics["price"]}


def _bond_cashflows(body: Mapping[str, Any]) -> Dict[str, Any]:
    years = _bond_years(body)
    face = float(body.get("face_value", 100.0))
    frequency = int(body.get("frequency", 1))
    periods = max(1, round(years * frequency))
    coupon = face * _number(body, "coupon_rate") / frequency
    return {"cashflows": [{"time": (index + 1) / frequency,
                            "amount": coupon + (face if index + 1 == periods else 0.0)}
                           for index in range(periods)]}


def _bond_yield(body: Mapping[str, Any]) -> Dict[str, float]:
    target = _positive(body, "clean_price")
    low, high = -0.95, 2.0
    trial = dict(body)
    for _ in range(150):
        middle = (low + high) / 2
        trial["yield_rate"] = middle
        price = _bond_metrics(trial)["price"]
        if price > target:
            low = middle
        else:
            high = middle
    return {"yield_rate": (low + high) / 2}


def _zero_coupon_bond(body: Mapping[str, Any]) -> Dict[str, float]:
    years = _bond_years(body)
    face = float(body.get("face_value", 100.0))
    yield_rate = float(body.get("yield_rate", 0.05))
    return {"price": face * math.exp(-yield_rate * years), "face_value": face,
            "years": years, "yield_rate": yield_rate}


def _swap_metrics(body: Mapping[str, Any]) -> Dict[str, float]:
    start = date.fromisoformat(str(body.get("effective_date")))
    maturity = date.fromisoformat(str(body.get("maturity_date")))
    years = (maturity - start).days / 365.25
    if years <= 0:
        raise ValueError("maturity_date must follow effective_date")
    payments_per_year = int(body.get("payments_per_year", 1))
    if payments_per_year <= 0:
        raise ValueError("payments_per_year must be positive")
    count = max(1, round(years * payments_per_year))
    dt = 1 / payments_per_year
    tenors, rates = _vector(body, "curve_tenors"), _vector(body, "curve_rates")
    payment_times = [min(years, (index + 1) * dt) for index in range(count)]
    discounts = [math.exp(-_linear(tenors, rates, time) * time) for time in payment_times]
    annuity = sum(discount * dt for discount in discounts)
    par_rate = (1 - discounts[-1]) / annuity
    notional = float(body.get("notional", 1_000_000))
    fixed_rate = float(body.get("fixed_rate", par_rate))
    return {"par_rate": par_rate, "annuity": annuity,
            "value": notional * (par_rate - fixed_rate) * annuity,
            "dv01": notional * annuity * 0.0001}


def _fx_forward(body: Mapping[str, Any]) -> Dict[str, float]:
    spot = _positive(body, "spot_rate")
    time = _positive(body, "time_to_maturity")
    forward = spot * math.exp((_number(body, "base_rate") - _number(body, "quote_rate")) * time)
    return {"forward_rate": forward, "forward_value": forward * float(body.get("notional", 1.0))}


def _garman_kohlhagen(body: Mapping[str, Any]) -> Dict[str, float]:
    spot, strike = _positive(body, "spot"), _positive(body, "strike")
    domestic, foreign = _number(body, "domestic_rate"), _number(body, "foreign_rate")
    volatility, time = _positive(body, "volatility"), _positive(body, "time_to_expiry")
    kind = str(body.get("option_type", "call")).lower()
    if kind not in {"call", "put"}:
        raise ValueError("option_type must be call or put")
    root_t = math.sqrt(time)
    d1 = (math.log(spot / strike) + (domestic - foreign + 0.5 * volatility ** 2) * time) / (volatility * root_t)
    d2 = d1 - volatility * root_t
    sign = 1.0 if kind == "call" else -1.0
    price = sign * (spot * math.exp(-foreign * time) * NORMAL.cdf(sign * d1) -
                    strike * math.exp(-domestic * time) * NORMAL.cdf(sign * d2))
    return {"price": price, "delta": sign * math.exp(-foreign * time) * NORMAL.cdf(sign * d1)}


def _cds_hazard(body: Mapping[str, Any]) -> Dict[str, float]:
    recovery = float(body.get("recovery_rate", 0.4))
    if not 0 <= recovery < 1:
        raise ValueError("recovery_rate must be in [0, 1)")
    return {"hazard_rate": _number(body, "spread_bps") / 10000 / (1 - recovery)}


def _cds_survival(body: Mapping[str, Any]) -> Dict[str, float]:
    hazard = _positive(body, "hazard_rate")
    time = _positive(body, "time_horizon", "time")
    survival = math.exp(-hazard * time)
    return {"survival_probability": survival, "default_probability": 1 - survival}


def _inflation_linked_bond(body: Mapping[str, Any]) -> Dict[str, float]:
    ratio = _positive(body, "current_cpi") / _positive(body, "base_cpi")
    face = float(body.get("face_value", 100.0)); coupon = _number(body, "coupon_rate")
    years = _bond_years(body); frequency = max(1, int(body.get("frequency", 1)))
    adjusted_principal = face * ratio
    return {"index_ratio": ratio, "adjusted_principal": adjusted_principal,
            "annual_coupon": adjusted_principal * coupon,
            "accrued_inflation": adjusted_principal - face, "years_to_maturity": years,
            "coupon_payment": adjusted_principal * coupon / frequency}


def _fra_values(body: Mapping[str, Any]) -> Dict[str, float]:
    start = _positive(body, "start_months") / 12
    end = _positive(body, "end_months") / 12
    if end <= start:
        raise ValueError("end_months must be greater than start_months")
    tenors, rates = _vector(body, "curve_tenors"), _vector(body, "curve_rates")
    start_df = math.exp(-_linear(tenors, rates, start) * start)
    end_df = math.exp(-_linear(tenors, rates, end) * end)
    accrual = end - start
    forward = (start_df / end_df - 1) / accrual
    fixed = _number(body, "fixed_rate"); notional = float(body.get("notional", 1_000_000))
    value = notional * accrual * (forward - fixed) * end_df
    return {"value": value, "break_even_rate": forward, "forward_rate": forward,
            "discount_factor": end_df, "accrual": accrual}


def _ois_curve(body: Mapping[str, Any]) -> Dict[str, Any]:
    rates = body.get("ois_rates", [])
    if not isinstance(rates, list) or not rates:
        raise ValueError("ois_rates must be a non-empty list of [tenor, rate] pairs")
    nodes = []
    for pair in rates:
        if not isinstance(pair, list) or len(pair) != 2:
            raise ValueError("each OIS quote must contain tenor and rate")
        tenor, rate = float(pair[0]), float(pair[1])
        if tenor <= 0 or not math.isfinite(rate):
            raise ValueError("OIS tenors must be positive and rates finite")
        nodes.append({"tenor": tenor, "rate": rate, "discount_factor": math.exp(-rate * tenor)})
    return {"reference_date": str(body.get("reference_date", "")), "nodes": nodes}


def _cds_value(body: Mapping[str, Any]) -> Dict[str, float]:
    effective = date.fromisoformat(str(body.get("effective_date")))
    maturity = date.fromisoformat(str(body.get("maturity_date")))
    years = (maturity - effective).days / 365.25
    if years <= 0:
        raise ValueError("maturity_date must follow effective_date")
    spread = _number(body, "spread_bps") / 10000
    recovery = float(body.get("recovery_rate", 0.4)); hazard = spread / (1 - recovery)
    notional = float(body.get("notional", 1_000_000))
    tenors, rates = _vector(body, "curve_tenors"), _vector(body, "curve_rates")
    count = max(1, round(years * 4)); dt = years / count
    premium, protection = 0.0, 0.0; previous_survival = 1.0
    for index in range(1, count + 1):
        time = index * dt; survival = math.exp(-hazard * time)
        discount = math.exp(-_linear(tenors, rates, time) * time)
        premium += spread * dt * survival * discount
        protection += (previous_survival - survival) * discount * (1 - recovery)
        previous_survival = survival
    return {"value": notional * (protection - premium), "premium_leg": notional * premium,
            "protection_leg": notional * protection, "hazard_rate": hazard}


def _money_market_deposit(body: Mapping[str, Any]) -> Dict[str, float]:
    start = date.fromisoformat(str(body.get("start_date")))
    maturity = date.fromisoformat(str(body.get("maturity_date")))
    days = (maturity - start).days
    if days <= 0:
        raise ValueError("maturity_date must follow start_date")
    notional, rate = _positive(body, "notional"), _number(body, "rate")
    fraction = days / 365
    interest = notional * rate * fraction
    return {"interest": interest, "maturity_value": notional + interest,
            "year_fraction": fraction, "days": days}


def _money_market_repo(body: Mapping[str, Any]) -> Dict[str, float]:
    start = date.fromisoformat(str(body.get("start_date")))
    end = date.fromisoformat(str(body.get("end_date")))
    days = (end - start).days
    if days <= 0:
        raise ValueError("end_date must follow start_date")
    collateral, rate = _positive(body, "collateral_value"), _number(body, "repo_rate")
    haircut = float(body.get("haircut", 0.0))
    principal = collateral * (1 - haircut); interest = principal * rate * days / 360
    return {"cash_principal": principal, "repo_interest": interest,
            "repurchase_price": principal + interest, "days": days}


def _money_market_tbill(body: Mapping[str, Any]) -> Dict[str, float]:
    settlement = date.fromisoformat(str(body.get("settlement_date")))
    maturity = date.fromisoformat(str(body.get("maturity_date")))
    days = (maturity - settlement).days
    if days <= 0:
        raise ValueError("maturity_date must follow settlement_date")
    face = float(body.get("face_value", 100.0)); discount = _number(body, "discount_rate")
    price = face * (1 - discount * days / 360)
    return {"price": price, "discount_amount": face - price,
            "bond_equivalent_yield": (face / price - 1) * 365 / days, "days": days}


def _commodity_future(body: Mapping[str, Any]) -> Dict[str, float]:
    spot, futures = _positive(body, "spot_price"), _positive(body, "futures_price")
    trade = date.fromisoformat(str(body.get("trade_date")))
    delivery = date.fromisoformat(str(body.get("delivery_date")))
    years = (delivery - trade).days / 365
    if years <= 0:
        raise ValueError("delivery_date must follow trade_date")
    carry = math.log(futures / spot) / years
    return {"basis": futures - spot, "implied_carry": carry, "time_to_delivery": years,
            "market_structure": "contango" if futures > spot else "backwardation"}


def _stir_future(body: Mapping[str, Any]) -> Dict[str, float]:
    price = _positive(body, "price")
    implied = (100 - price) / 100
    return {"implied_rate": implied, "rate_percent": implied * 100, "price": price}


def _bond_future_ctd(body: Mapping[str, Any]) -> Dict[str, Any]:
    futures = _positive(body, "futures_price")
    deliverables = body.get("deliverables", [])
    if not isinstance(deliverables, list) or not deliverables:
        raise ValueError("deliverables must be a non-empty list")
    rows = []
    for index, item in enumerate(deliverables):
        price = float(item.get("price", 0)); factor = float(item.get("conversion_factor", 0))
        if price <= 0 or factor <= 0:
            raise ValueError("each deliverable needs positive price and conversion_factor")
        net_basis = price - futures * factor
        rows.append({"index": index, "net_basis": net_basis, "invoice_price": futures * factor,
                     "conversion_factor": factor})
    cheapest = min(rows, key=lambda item: item["net_basis"])
    return {"cheapest_index": cheapest["index"], "cheapest_net_basis": cheapest["net_basis"],
            "deliverables": rows}


def _realized_variance(body: Mapping[str, Any]) -> float:
    returns = _vector(body, "realized_returns")
    if len(returns) < 2:
        raise ValueError("realized_returns must contain at least two values")
    return sum(value * value for value in returns) / len(returns) * 252


def _variance_swap(body: Mapping[str, Any]) -> Dict[str, float]:
    realized = _realized_variance(body); strike = _positive(body, "strike_variance")
    notional = float(body.get("notional", 1.0)); payoff = notional * (realized - strike)
    return {"realized_variance": realized, "strike_variance": strike, "payoff": payoff}


def _volatility_swap(body: Mapping[str, Any]) -> Dict[str, float]:
    realized = math.sqrt(_realized_variance(body)); strike = _positive(body, "strike_vol")
    notional = float(body.get("notional", 1.0)); payoff = notional * (realized - strike)
    return {"realized_volatility": realized, "strike_volatility": strike, "payoff": payoff}


def _inverse(matrix: Sequence[Sequence[float]]) -> list[list[float]]:
    size = len(matrix)
    augmented = [list(map(float, row)) + [1.0 if i == j else 0.0 for j in range(size)]
                 for i, row in enumerate(matrix)]
    for column in range(size):
        pivot = max(range(column, size), key=lambda row: abs(augmented[row][column]))
        if abs(augmented[pivot][column]) < 1e-14:
            raise ValueError("covariance_matrix is singular")
        augmented[column], augmented[pivot] = augmented[pivot], augmented[column]
        divisor = augmented[column][column]
        augmented[column] = [value / divisor for value in augmented[column]]
        for row in range(size):
            if row == column:
                continue
            factor = augmented[row][column]
            augmented[row] = [left - factor * right for left, right in zip(augmented[row], augmented[column])]
    return [row[size:] for row in augmented]


def _matvec(matrix: Sequence[Sequence[float]], vector: Sequence[float]) -> list[float]:
    return [sum(value * weight for value, weight in zip(row, vector)) for row in matrix]


def _normalize_long(values: Sequence[float]) -> list[float]:
    clipped = [max(0.0, value) for value in values]
    total = sum(clipped)
    if total <= 0:
        raise ValueError("optimization produced no positive weights")
    return [value / total for value in clipped]


def _portfolio_summary(weights: Sequence[float], covariance: Sequence[Sequence[float]],
                       returns: Sequence[float] | None = None) -> Dict[str, Any]:
    variance = sum(weight * marginal for weight, marginal in zip(weights, _matvec(covariance, weights)))
    result: Dict[str, Any] = {"weights": list(weights), "variance": variance,
                              "volatility": math.sqrt(max(0.0, variance))}
    if returns is not None:
        result["expected_return"] = sum(weight * value for weight, value in zip(weights, returns))
    return result


def _min_variance(body: Mapping[str, Any]) -> Dict[str, Any]:
    covariance = _matrix(body, "covariance_matrix")
    weights = _normalize_long(_matvec(_inverse(covariance), [1.0] * len(covariance)))
    returns = [float(value) for value in body.get("expected_returns", [])]
    return _portfolio_summary(weights, covariance, returns if len(returns) == len(weights) else None)


def _max_sharpe(body: Mapping[str, Any]) -> Dict[str, Any]:
    covariance = _matrix(body, "covariance_matrix")
    returns = _vector(body, "expected_returns")
    if len(returns) != len(covariance):
        raise ValueError("expected_returns must match covariance_matrix")
    risk_free = float(body.get("risk_free_rate", 0.0))
    weights = _normalize_long(_matvec(_inverse(covariance), [value - risk_free for value in returns]))
    result = _portfolio_summary(weights, covariance, returns)
    result["sharpe_ratio"] = (result["expected_return"] - risk_free) / result["volatility"]
    return result


def _efficient_frontier(body: Mapping[str, Any]) -> Dict[str, Any]:
    covariance = _matrix(body, "covariance_matrix")
    returns = _vector(body, "expected_returns")
    if len(returns) != len(covariance):
        raise ValueError("expected_returns must match covariance_matrix")
    inverse = _inverse(covariance)
    ones = [1.0] * len(returns)
    inv_one, inv_return = _matvec(inverse, ones), _matvec(inverse, returns)
    a = sum(inv_one)
    b = sum(value * weight for value, weight in zip(returns, inv_one))
    c = sum(value * weight for value, weight in zip(returns, inv_return))
    determinant = a * c - b * b
    if abs(determinant) < 1e-14:
        raise ValueError("frontier cannot be constructed from this covariance matrix")
    low, high = min(returns), max(returns)
    frontier = []
    for index in range(11):
        target = low + (high - low) * index / 10
        first = (c - b * target) / determinant
        second = (a * target - b) / determinant
        weights = _normalize_long([first * left + second * right for left, right in zip(inv_one, inv_return)])
        point = _portfolio_summary(weights, covariance, returns)
        point["target_return"] = target
        frontier.append(point)
    return {"frontier": frontier}


def _inverse_volatility(body: Mapping[str, Any]) -> Dict[str, Any]:
    covariance = _matrix(body, "covariance_matrix")
    weights = _normalize_long([1 / math.sqrt(row[index]) for index, row in enumerate(covariance)])
    return _portfolio_summary(weights, covariance)


def _risk_parity(body: Mapping[str, Any]) -> Dict[str, Any]:
    covariance = _matrix(body, "covariance_matrix")
    weights = [1 / len(covariance)] * len(covariance)
    for _ in range(500):
        marginal = _matvec(covariance, weights)
        contributions = [weight * value for weight, value in zip(weights, marginal)]
        target = sum(contributions) / len(weights)
        updated = _normalize_long([weight * math.sqrt(target / max(value, 1e-16))
                                   for weight, value in zip(weights, contributions)])
        if max(abs(left - right) for left, right in zip(weights, updated)) < 1e-12:
            weights = updated
            break
        weights = updated
    result = _portfolio_summary(weights, covariance)
    marginal = _matvec(covariance, weights)
    result["risk_contributions"] = [weight * value / result["variance"]
                                    for weight, value in zip(weights, marginal)]
    return result


def _portfolio_var(body: Mapping[str, Any]) -> Dict[str, float]:
    covariance = _matrix(body, "covariance_matrix")
    weights = _vector(body, "weights")
    if len(weights) != len(covariance):
        raise ValueError("weights must match covariance_matrix")
    volatility = _portfolio_summary(weights, covariance)["volatility"]
    confidence = float(body.get("confidence", 0.95))
    z_score = NORMAL.inv_cdf(confidence)
    return {"var": z_score * volatility, "volatility": volatility,
            "cvar": NORMAL.pdf(z_score) / (1 - confidence) * volatility}


def _risk_contribution(body: Mapping[str, Any]) -> Dict[str, Any]:
    covariance = _matrix(body, "covariance_matrix")
    weights = _vector(body, "weights")
    if len(weights) != len(covariance):
        raise ValueError("weights must match covariance_matrix")
    marginal = _matvec(covariance, weights)
    absolute = [weight * value for weight, value in zip(weights, marginal)]
    variance = sum(absolute)
    if variance <= 0:
        raise ValueError("portfolio variance must be positive")
    return {"variance": variance, "volatility": math.sqrt(variance),
            "marginal_contributions": marginal, "absolute_contributions": absolute,
            "percentage_contributions": [value / variance for value in absolute]}


def _risk_ratios(body: Mapping[str, Any]) -> Dict[str, float]:
    returns = _vector(body, "returns")
    if len(returns) < 2:
        raise ValueError("returns must contain at least two values")
    periods = float(body.get("periods_per_year", 252))
    risk_free = float(body.get("risk_free_rate", 0.0))
    mean = statistics.mean(returns)
    volatility = statistics.stdev(returns)
    downside = math.sqrt(sum(min(value, 0.0) ** 2 for value in returns) / len(returns))
    wealth = peak = 1.0
    max_drawdown = 0.0
    for value in returns:
        wealth *= 1 + value
        peak = max(peak, wealth)
        max_drawdown = min(max_drawdown, wealth / peak - 1)
    excess = mean - risk_free / periods
    return {"annualized_return": mean * periods, "annualized_volatility": volatility * math.sqrt(periods),
            "sharpe_ratio": excess / volatility * math.sqrt(periods) if volatility else 0.0,
            "sortino_ratio": excess / downside * math.sqrt(periods) if downside else 0.0,
            "max_drawdown": max_drawdown}


def _gbm(body: Mapping[str, Any]) -> Dict[str, Any]:
    start = _positive(body, "S0")
    drift, volatility = _number(body, "mu"), _positive(body, "sigma")
    horizon = _positive(body, "T")
    steps, paths = int(body.get("n_steps", 52)), int(body.get("n_paths", 1))
    if not 1 <= steps <= 10000 or not 1 <= paths <= 1000:
        raise ValueError("n_steps and n_paths are outside local safety limits")
    generator = random.Random(int(body.get("seed", 42)))
    dt = horizon / steps
    output = []
    for _ in range(paths):
        path = [start]
        for _ in range(steps):
            shock = generator.gauss(0, 1)
            path.append(path[-1] * math.exp((drift - 0.5 * volatility * volatility) * dt +
                                            volatility * math.sqrt(dt) * shock))
        output.append(path)
    return {"times": [index * dt for index in range(steps + 1)], "paths": output, "seed": int(body.get("seed", 42))}


def _gbm_properties(body: Mapping[str, Any]) -> Dict[str, float]:
    start, drift, volatility, horizon = (_positive(body, "S0"), _number(body, "mu"),
                                         _positive(body, "sigma"), _positive(body, "T"))
    mean = start * math.exp(drift * horizon)
    variance = start * start * math.exp(2 * drift * horizon) * (math.exp(volatility * volatility * horizon) - 1)
    return {"expected_value": mean, "variance": variance, "standard_deviation": math.sqrt(variance)}


def _ou(body: Mapping[str, Any]) -> Dict[str, Any]:
    start = _number(body, "X0")
    kappa, theta, volatility = (_positive(body, "kappa"), _number(body, "theta"), _positive(body, "sigma"))
    horizon = _positive(body, "T")
    steps, paths = int(body.get("n_steps", 52)), int(body.get("n_paths", 1))
    if not 1 <= steps <= 10000 or not 1 <= paths <= 1000:
        raise ValueError("n_steps and n_paths are outside local safety limits")
    generator = random.Random(int(body.get("seed", 42)))
    dt = horizon / steps
    decay = math.exp(-kappa * dt)
    deviation = volatility * math.sqrt((1 - math.exp(-2 * kappa * dt)) / (2 * kappa))
    output = []
    for _ in range(paths):
        path = [start]
        for _ in range(steps):
            path.append(theta + (path[-1] - theta) * decay + deviation * generator.gauss(0, 1))
        output.append(path)
    return {"times": [index * dt for index in range(steps + 1)], "paths": output,
            "seed": int(body.get("seed", 42))}


def _cir_bond(body: Mapping[str, Any]) -> Dict[str, float]:
    rate, kappa, theta, volatility, horizon = (_number(body, "r0"), _positive(body, "kappa"),
                                               _positive(body, "theta"), _positive(body, "sigma"),
                                               _positive(body, "T"))
    gamma = math.sqrt(kappa * kappa + 2 * volatility * volatility)
    exponential = math.exp(gamma * horizon)
    denominator = (gamma + kappa) * (exponential - 1) + 2 * gamma
    b_value = 2 * (exponential - 1) / denominator
    a_value = (2 * gamma * math.exp((kappa + gamma) * horizon / 2) / denominator) ** (
        2 * kappa * theta / (volatility * volatility)
    )
    return {"bond_price": a_value * math.exp(-b_value * rate), "A": a_value, "B": b_value}


def _simulation_shape(body: Mapping[str, Any], *, default_steps: int = 52,
                      default_paths: int = 1) -> tuple[float, int, int, random.Random]:
    horizon = _positive(body, "T")
    steps = int(body.get("n_steps", default_steps))
    paths = int(body.get("n_paths", default_paths))
    if not 1 <= steps <= 10000 or not 1 <= paths <= 1000:
        raise ValueError("n_steps and n_paths are outside local safety limits")
    return horizon, steps, paths, random.Random(int(body.get("seed", 42)))


def _cir_simulate(body: Mapping[str, Any]) -> Dict[str, Any]:
    initial = max(0.0, _number(body, "r0"))
    kappa, theta, volatility = (_positive(body, "kappa"), _positive(body, "theta"),
                                _positive(body, "sigma"))
    horizon, steps, paths, generator = _simulation_shape(body)
    dt, root_dt = horizon / steps, math.sqrt(horizon / steps)
    output = []
    for _ in range(paths):
        path = [initial]
        for _ in range(steps):
            previous = max(path[-1], 0.0)
            value = previous + kappa * (theta - previous) * dt
            value += volatility * math.sqrt(previous) * root_dt * generator.gauss(0, 1)
            path.append(max(0.0, value))
        output.append(path)
    return {"times": [index * dt for index in range(steps + 1)], "paths": output,
            "scheme": "full-truncation-euler", "seed": int(body.get("seed", 42))}


def _heston_simulate(body: Mapping[str, Any]) -> Dict[str, Any]:
    spot, variance = _positive(body, "S0"), max(0.0, _number(body, "v0"))
    rate = _number(body, "r")
    kappa, theta = _positive(body, "kappa"), _positive(body, "theta")
    vol_of_vol = _positive(body, "sigma_v", "xi")
    rho = _number(body, "rho")
    if not -1 <= rho <= 1:
        raise ValueError("rho must be between -1 and 1")
    horizon, steps, paths, generator = _simulation_shape(body, default_steps=252)
    dt, root_dt = horizon / steps, math.sqrt(horizon / steps)
    orthogonal = math.sqrt(max(0.0, 1 - rho * rho))
    asset_paths, variance_paths = [], []
    for _ in range(paths):
        asset_path, variance_path = [spot], [variance]
        for _ in range(steps):
            z1, independent = generator.gauss(0, 1), generator.gauss(0, 1)
            z2 = rho * z1 + orthogonal * independent
            current_variance = max(variance_path[-1], 0.0)
            next_variance = current_variance + kappa * (theta - current_variance) * dt
            next_variance += vol_of_vol * math.sqrt(current_variance) * root_dt * z2
            asset_path.append(asset_path[-1] * math.exp(
                (rate - 0.5 * current_variance) * dt + math.sqrt(current_variance) * root_dt * z1
            ))
            variance_path.append(max(0.0, next_variance))
        asset_paths.append(asset_path)
        variance_paths.append(variance_path)
    return {"times": [index * dt for index in range(steps + 1)], "asset_paths": asset_paths,
            "variance_paths": variance_paths, "scheme": "full-truncation-euler",
            "seed": int(body.get("seed", 42))}


def _poisson_sample(generator: random.Random, intensity: float) -> int:
    if intensity < 0:
        raise ValueError("Poisson intensity cannot be negative")
    if intensity == 0:
        return 0
    if intensity > 50:
        return max(0, round(generator.gauss(intensity, math.sqrt(intensity))))
    threshold, product, count = math.exp(-intensity), 1.0, 0
    while product > threshold:
        count += 1
        product *= generator.random()
    return count - 1


def _merton_simulate(body: Mapping[str, Any]) -> Dict[str, Any]:
    start, drift, volatility = _positive(body, "S0"), _number(body, "mu"), _positive(body, "sigma")
    intensity = _positive(body, "lam")
    jump_mean, jump_std = _number(body, "jump_mean"), _positive(body, "jump_std")
    horizon, steps, paths, generator = _simulation_shape(body)
    dt, root_dt = horizon / steps, math.sqrt(horizon / steps)
    jump_compensator = intensity * (math.exp(jump_mean + 0.5 * jump_std * jump_std) - 1)
    output = []
    for _ in range(paths):
        path = [start]
        for _ in range(steps):
            count = _poisson_sample(generator, intensity * dt)
            jump = sum(generator.gauss(jump_mean, jump_std) for _ in range(count))
            diffusion = (drift - jump_compensator - 0.5 * volatility * volatility) * dt
            path.append(path[-1] * math.exp(diffusion + volatility * root_dt * generator.gauss(0, 1) + jump))
        output.append(path)
    return {"times": [index * dt for index in range(steps + 1)], "paths": output,
            "seed": int(body.get("seed", 42))}


def _vasicek_simulate(body: Mapping[str, Any]) -> Dict[str, Any]:
    adapted = dict(body)
    adapted["X0"] = _number(body, "r0")
    answer = _ou(adapted)
    answer["scheme"] = "exact-transition"
    return answer


def _vasicek_bond(body: Mapping[str, Any]) -> Dict[str, float]:
    rate = _number(body, "r0")
    kappa, theta, volatility, horizon = (_positive(body, "kappa"), _number(body, "theta"),
                                         _positive(body, "sigma"), _positive(body, "T"))
    b_value = (1 - math.exp(-kappa * horizon)) / kappa
    log_a = (theta - volatility * volatility / (2 * kappa * kappa)) * (b_value - horizon)
    log_a -= volatility * volatility * b_value * b_value / (4 * kappa)
    a_value = math.exp(log_a)
    return {"bond_price": a_value * math.exp(-b_value * rate), "A": a_value, "B": b_value}


def _wiener_simulate(body: Mapping[str, Any]) -> Dict[str, Any]:
    horizon, steps, paths, generator = _simulation_shape(body)
    dt, scale = horizon / steps, math.sqrt(horizon / steps)
    output = []
    for _ in range(paths):
        path = [0.0]
        for _ in range(steps):
            path.append(path[-1] + scale * generator.gauss(0, 1))
        output.append(path)
    return {"times": [index * dt for index in range(steps + 1)], "paths": output,
            "seed": int(body.get("seed", 42))}


def _poisson_simulate(body: Mapping[str, Any]) -> Dict[str, Any]:
    intensity = _positive(body, "lam")
    horizon, _steps, paths, generator = _simulation_shape(body, default_steps=1)
    samples = [_poisson_sample(generator, intensity * horizon) for _ in range(paths)]
    return {"samples": samples, "intensity": intensity * horizon, "seed": int(body.get("seed", 42))}


def _variance_gamma_simulate(body: Mapping[str, Any]) -> Dict[str, Any]:
    start, rate, volatility = _positive(body, "S0"), _number(body, "r"), _positive(body, "sigma")
    theta, nu = _number(body, "theta_vg"), _positive(body, "nu")
    horizon, steps, paths, generator = _simulation_shape(body)
    dt = horizon / steps
    omega_term = 1 - theta * nu - 0.5 * volatility * volatility * nu
    if omega_term <= 0:
        raise ValueError("variance-gamma inputs violate the martingale condition")
    omega = math.log(omega_term) / nu
    output = []
    for _ in range(paths):
        path = [start]
        for _ in range(steps):
            gamma_clock = generator.gammavariate(dt / nu, nu)
            log_return = (rate + omega) * dt + theta * gamma_clock
            log_return += volatility * math.sqrt(gamma_clock) * generator.gauss(0, 1)
            path.append(path[-1] * math.exp(log_return))
        output.append(path)
    return {"times": [index * dt for index in range(steps + 1)], "paths": output,
            "seed": int(body.get("seed", 42))}


def _brownian_bridge(body: Mapping[str, Any]) -> Dict[str, Any]:
    horizon, steps, _paths, generator = _simulation_shape(body)
    start, end = _number(body, "a"), _number(body, "b")
    dt = horizon / steps
    wiener = [0.0]
    for _ in range(steps):
        wiener.append(wiener[-1] + math.sqrt(dt) * generator.gauss(0, 1))
    path = [start + (end - start) * index / steps + wiener[index] - index / steps * wiener[-1]
            for index in range(steps + 1)]
    path[0], path[-1] = start, end
    return {"times": [index * dt for index in range(steps + 1)], "path": path,
            "seed": int(body.get("seed", 42))}


def _correlated_bm(body: Mapping[str, Any]) -> Dict[str, Any]:
    dimension = int(body.get("n_assets", 0))
    correlation = _matrix(body, "correlation_matrix")
    if dimension != len(correlation):
        raise ValueError("n_assets must match correlation_matrix")
    lower = _cholesky({"matrix": correlation})["lower"]
    horizon, steps, paths, generator = _simulation_shape(body)
    dt, scale = horizon / steps, math.sqrt(horizon / steps)
    output = []
    for _ in range(paths):
        path = [[0.0] * dimension]
        for _ in range(steps):
            independent = [generator.gauss(0, 1) for _ in range(dimension)]
            shock = [sum(lower[row][column] * independent[column] for column in range(row + 1))
                     for row in range(dimension)]
            path.append([value + scale * delta for value, delta in zip(path[-1], shock)])
        output.append(path)
    return {"times": [index * dt for index in range(steps + 1)], "paths": output,
            "seed": int(body.get("seed", 42))}


def _bounded_sample_count(body: Mapping[str, Any], key: str, default: int) -> int:
    count = int(body.get(key, default))
    if not 1 <= count <= 100000:
        raise ValueError(f"{key} is outside local safety limits")
    return count


def _exact_terminal_gbm(body: Mapping[str, Any]) -> Dict[str, Any]:
    start, drift, volatility, horizon = (_positive(body, "S0"), _number(body, "mu"),
                                         _positive(body, "sigma"), _positive(body, "T"))
    count = _bounded_sample_count(body, "n_paths", 100)
    generator = random.Random(int(body.get("seed", 42)))
    samples = [start * math.exp((drift - 0.5 * volatility * volatility) * horizon +
                                volatility * math.sqrt(horizon) * generator.gauss(0, 1))
               for _ in range(count)]
    return {"samples": samples, "mean": statistics.mean(samples), "seed": int(body.get("seed", 42))}


def _exact_terminal_ou(body: Mapping[str, Any]) -> Dict[str, Any]:
    start, kappa, theta, volatility, horizon = (_number(body, "X0"), _positive(body, "kappa"),
                                                _number(body, "theta"), _positive(body, "sigma"),
                                                _positive(body, "T"))
    count = _bounded_sample_count(body, "n_paths", 100)
    generator = random.Random(int(body.get("seed", 42)))
    mean = theta + (start - theta) * math.exp(-kappa * horizon)
    deviation = volatility * math.sqrt((1 - math.exp(-2 * kappa * horizon)) / (2 * kappa))
    samples = [generator.gauss(mean, deviation) for _ in range(count)]
    return {"samples": samples, "theoretical_mean": mean, "theoretical_std": deviation,
            "seed": int(body.get("seed", 42))}


def _exact_terminal_cir(body: Mapping[str, Any]) -> Dict[str, Any]:
    initial, kappa, theta, volatility, horizon = (max(0.0, _number(body, "r0")), _positive(body, "kappa"),
                                                  _positive(body, "theta"), _positive(body, "sigma"),
                                                  _positive(body, "T"))
    count = _bounded_sample_count(body, "n_paths", 100)
    generator = random.Random(int(body.get("seed", 42)))
    decay = math.exp(-kappa * horizon)
    scale = volatility * volatility * (1 - decay) / (4 * kappa)
    degrees = 4 * kappa * theta / (volatility * volatility)
    noncentrality = initial * decay / scale
    samples = []
    for _ in range(count):
        mixture = _poisson_sample(generator, noncentrality / 2)
        samples.append(scale * generator.gammavariate((degrees + 2 * mixture) / 2, 2))
    return {"samples": samples, "theoretical_mean": theta + (initial - theta) * decay,
            "seed": int(body.get("seed", 42))}


def _geometric_scheme(body: Mapping[str, Any], *, milstein: bool) -> Dict[str, Any]:
    initial, drift, volatility = _number(body, "x0"), _number(body, "mu"), _positive(body, "sigma")
    horizon, steps, paths, generator = _simulation_shape(body)
    dt, root_dt = horizon / steps, math.sqrt(horizon / steps)
    output = []
    for _ in range(paths):
        path = [initial]
        for _ in range(steps):
            shock = root_dt * generator.gauss(0, 1)
            value = path[-1] + drift * path[-1] * dt + volatility * path[-1] * shock
            if milstein:
                value += 0.5 * volatility * volatility * path[-1] * (shock * shock - dt)
            path.append(value)
        output.append(path)
    return {"times": [index * dt for index in range(steps + 1)], "paths": output,
            "scheme": "milstein" if milstein else "euler-maruyama", "seed": int(body.get("seed", 42))}


def _nd_scheme(body: Mapping[str, Any], *, milstein: bool) -> Dict[str, Any]:
    state, drift = _vector(body, "x0"), _vector(body, "mu")
    diffusion = _matrix(body, "sigma")
    if len(state) != len(drift) or len(state) != len(diffusion):
        raise ValueError("x0, mu, and sigma dimensions must match")
    horizon, steps, _paths, generator = _simulation_shape(body)
    dt, root_dt = horizon / steps, math.sqrt(horizon / steps)
    path = [state]
    for _ in range(steps):
        shocks = [root_dt * generator.gauss(0, 1) for _ in state]
        current, following = path[-1], []
        for row, value in enumerate(current):
            diffusion_term = sum(diffusion[row][column] * shocks[column] for column in range(len(state)))
            next_value = value + drift[row] * value * dt + value * diffusion_term
            if milstein:
                next_value += 0.5 * value * sum(
                    diffusion[row][column] ** 2 * (shocks[column] ** 2 - dt)
                    for column in range(len(state))
                )
            following.append(next_value)
        path.append(following)
    return {"times": [index * dt for index in range(steps + 1)], "path": path,
            "scheme": "milstein-nd" if milstein else "euler-maruyama-nd",
            "seed": int(body.get("seed", 42))}


def _multilevel_mc(body: Mapping[str, Any]) -> Dict[str, Any]:
    start, drift, volatility, horizon = (_number(body, "x0"), _number(body, "mu"),
                                         _positive(body, "sigma"), _positive(body, "T"))
    levels, samples_per_level = int(body.get("n_levels", 4)), int(body.get("n_samples_per_level", 1000))
    if not 1 <= levels <= 12 or not 1 <= samples_per_level <= 100000:
        raise ValueError("multilevel sample sizes are outside local safety limits")
    generator = random.Random(int(body.get("seed", 42)))
    estimates = []
    previous = 0.0
    for level in range(levels):
        steps, dt = 2 ** level, horizon / (2 ** level)
        values = []
        for _ in range(samples_per_level):
            value = start
            for _ in range(steps):
                value += drift * value * dt + volatility * value * math.sqrt(dt) * generator.gauss(0, 1)
            values.append(value)
        estimate = statistics.mean(values)
        estimates.append(estimate - previous if level else estimate)
        previous = estimate
    return {"estimate": sum(estimates), "level_corrections": estimates,
            "analytic_value": start * math.exp(drift * horizon), "seed": int(body.get("seed", 42))}


def _van_der_corput(index: int, base: int) -> float:
    result, denominator = 0.0, 1.0
    while index:
        index, remainder = divmod(index, base)
        denominator *= base
        result += remainder / denominator
    return result


def _sobol_samples(body: Mapping[str, Any]) -> Dict[str, Any]:
    count, dimension = int(body.get("n", 100)), int(body.get("dim", 2))
    primes = [2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37, 41, 43, 47, 53]
    if not 1 <= count <= 100000 or not 1 <= dimension <= len(primes):
        raise ValueError("n or dim is outside local low-discrepancy limits")
    samples = [[_van_der_corput(index, primes[column]) for column in range(dimension)]
               for index in range(1, count + 1)]
    return {"samples": samples, "dimension": dimension, "method": "low-discrepancy-digital-sequence"}


def _antithetic_samples(body: Mapping[str, Any]) -> Dict[str, Any]:
    count = _bounded_sample_count(body, "n", 50)
    generator = random.Random(int(body.get("seed", 42)))
    originals = [generator.gauss(0, 1) for _ in range(count)]
    return {"samples": originals + [-value for value in originals], "pairs": count,
            "seed": int(body.get("seed", 42))}


def _correlated_normals(body: Mapping[str, Any]) -> Dict[str, Any]:
    rho, count = _number(body, "rho"), int(body.get("n", 100))
    if not -1 <= rho <= 1 or not 1 <= count <= 100000:
        raise ValueError("rho or n is outside supported limits")
    generator = random.Random(int(body.get("seed", 42)))
    scale = math.sqrt(max(0.0, 1 - rho * rho))
    samples = []
    for _ in range(count):
        first, independent = generator.gauss(0, 1), generator.gauss(0, 1)
        samples.append([first, rho * first + scale * independent])
    return {"samples": samples, "rho": rho, "seed": int(body.get("seed", 42))}


def _multivariate_normal(body: Mapping[str, Any]) -> Dict[str, Any]:
    mean, covariance = _vector(body, "mean"), _matrix(body, "cov")
    if len(mean) != len(covariance):
        raise ValueError("mean and cov dimensions must match")
    lower = _cholesky({"matrix": covariance})["lower"]
    generator = random.Random(int(body.get("seed", 42)))
    z = [generator.gauss(0, 1) for _ in mean]
    sample = [mean[row] + sum(lower[row][column] * z[column] for column in range(row + 1))
              for row in range(len(mean))]
    return {"sample": sample, "seed": int(body.get("seed", 42))}


def _distribution_samples(body: Mapping[str, Any]) -> Dict[str, Any]:
    name, params, count = str(body.get("distribution", "")).lower(), body.get("params", {}), int(body.get("n", 100))
    if not isinstance(params, Mapping) or not 1 <= count <= 100000:
        raise ValueError("params must be an object and n must be within local limits")
    generator = random.Random(int(body.get("seed", 42)))
    if name == "exponential":
        rate = _positive(params, "rate")
        samples = [generator.expovariate(rate) for _ in range(count)]
    elif name == "gamma":
        shape, scale = _positive(params, "shape"), _positive(params, "scale")
        samples = [generator.gammavariate(shape, scale) for _ in range(count)]
    elif name == "poisson":
        intensity = _positive(params, "lam")
        samples = [_poisson_sample(generator, intensity) for _ in range(count)]
    else:
        raise ValueError("distribution must be exponential, gamma, or poisson")
    return {"samples": samples, "distribution": name, "seed": int(body.get("seed", 42))}


def _jump_samples(body: Mapping[str, Any]) -> Dict[str, Any]:
    count, deviation = _bounded_sample_count(body, "n", 100), _positive(body, "jump_std")
    mean, name = _number(body, "jump_mean"), str(body.get("distribution", "normal")).lower()
    generator = random.Random(int(body.get("seed", 42)))
    if name == "normal":
        samples = [generator.gauss(mean, deviation) for _ in range(count)]
    elif name == "double_exp":
        samples = [(1 if generator.random() < 0.5 else -1) * generator.expovariate(1 / deviation)
                   for _ in range(count)]
    elif name == "uniform":
        samples = [generator.uniform(-deviation, deviation) for _ in range(count)]
    else:
        raise ValueError("distribution must be normal, double_exp, or uniform")
    return {"samples": samples, "distribution": name, "seed": int(body.get("seed", 42))}


def _path_pair(body: Mapping[str, Any]) -> tuple[list[float], list[float], list[float]]:
    first, second, times = _vector(body, "path_X"), _vector(body, "path_Y"), _vector(body, "times")
    if len(first) != len(second) or len(first) != len(times) or len(first) < 2:
        raise ValueError("path_X, path_Y, and times must have equal length of at least two")
    return first, second, times


def _quadratic_variation(body: Mapping[str, Any]) -> Dict[str, float]:
    path, times = _vector(body, "path"), _vector(body, "times")
    if len(path) != len(times) or len(path) < 2:
        raise ValueError("path and times must have equal length of at least two")
    value = sum((right - left) ** 2 for left, right in zip(path, path[1:]))
    return {"quadratic_variation": value, "increments": len(path) - 1}


def _covariation(body: Mapping[str, Any]) -> Dict[str, float]:
    first, second, _times = _path_pair(body)
    value = sum((x1 - x0) * (y1 - y0)
                for x0, x1, y0, y1 in zip(first, first[1:], second, second[1:]))
    return {"covariation": value, "increments": len(first) - 1}


def _ito_lemma(body: Mapping[str, Any]) -> Dict[str, Any]:
    path, times = _vector(body, "path"), _vector(body, "times")
    if len(path) != len(times) or len(path) < 2:
        raise ValueError("path and times must have equal length of at least two")
    increments = [right - left for left, right in zip(path, path[1:])]
    quadratic = sum(value * value for value in increments)
    return {"path_change": path[-1] - path[0], "stochastic_integral_identity": sum(increments),
            "quadratic_variation": quadratic, "increments": increments}


def _ito_product(body: Mapping[str, Any]) -> Dict[str, float]:
    first, second, _times = _path_pair(body)
    x_integral = sum(first[index] * (second[index + 1] - second[index]) for index in range(len(first) - 1))
    y_integral = sum(second[index] * (first[index + 1] - first[index]) for index in range(len(first) - 1))
    covariation = sum((first[index + 1] - first[index]) * (second[index + 1] - second[index])
                      for index in range(len(first) - 1))
    return {"product_change": first[-1] * second[-1] - first[0] * second[0],
            "x_dy": x_integral, "y_dx": y_integral, "covariation": covariation,
            "decomposition": x_integral + y_integral + covariation}


def _martingale_test(body: Mapping[str, Any]) -> Dict[str, Any]:
    paths = [[float(value) for value in row] for row in body.get("paths", [])]
    times = _vector(body, "times")
    if len(paths) < 2 or any(len(row) != len(times) for row in paths):
        raise ValueError("paths must contain at least two paths matching times")
    confidence = float(body.get("confidence", 0.95))
    critical = NORMAL.inv_cdf((1 + confidence) / 2)
    means = [statistics.mean(row[index] for row in paths) for index in range(len(times))]
    standard_errors = [statistics.stdev(row[index] for row in paths) / math.sqrt(len(paths))
                       for index in range(len(times))]
    initial = means[0]
    z_scores = [abs(mean - initial) / error if error > 0 else (0.0 if mean == initial else math.inf)
                for mean, error in zip(means, standard_errors)]
    return {"is_martingale": max(z_scores) <= critical, "means": means,
            "standard_errors": standard_errors, "z_scores": z_scores, "critical_value": critical}


def _risk_neutral_drift(body: Mapping[str, Any]) -> Dict[str, float]:
    drift, rate, volatility = _number(body, "mu"), _number(body, "r"), _positive(body, "sigma")
    return {"physical_drift": drift, "risk_neutral_drift": rate,
            "market_price_of_risk": (drift - rate) / volatility}


def _girsanov_measure_change(body: Mapping[str, Any]) -> Dict[str, Any]:
    paths = [[float(value) for value in row] for row in body.get("paths", [])]
    times = _vector(body, "times")
    if not paths or any(len(row) != len(times) for row in paths) or len(times) < 2:
        raise ValueError("paths must match times")
    drift = _risk_neutral_drift(body)
    adapted = {"S0": paths[0][0], "mu": drift["risk_neutral_drift"], "sigma": _positive(body, "sigma"),
               "T": times[-1] - times[0], "n_steps": len(times) - 1, "n_paths": len(paths),
               "seed": int(body.get("seed", 42))}
    simulated = _gbm(adapted)
    simulated.update(drift)
    return simulated


def _positive_vector(body: Mapping[str, Any], name: str) -> list[float]:
    values = _vector(body, name)
    if any(value <= 0 for value in values):
        raise ValueError(f"{name} must contain positive values")
    return values


def _normalized_probabilities(body: Mapping[str, Any], name: str, size: int) -> list[float]:
    values = _vector(body, name)
    if len(values) != size or any(value < 0 for value in values):
        raise ValueError(f"{name} must contain {size} non-negative values")
    total = sum(values)
    if total <= 0:
        raise ValueError(f"{name} must have positive total weight")
    return [value / total for value in values]


def _cobb_douglas(body: Mapping[str, Any]) -> Dict[str, Any]:
    alphas, prices = _positive_vector(body, "alphas"), _positive_vector(body, "prices")
    if len(alphas) != len(prices):
        raise ValueError("alphas and prices must have equal length")
    alpha_total = sum(alphas)
    shares = [value / alpha_total for value in alphas]
    income = float(body.get("income", 1.0))
    if income <= 0:
        raise ValueError("income must be positive")
    demand = [income * share / price for share, price in zip(shares, prices)]
    utility = math.prod(quantity ** alpha for quantity, alpha in zip(demand, shares))
    return {"demand": demand, "expenditure": sum(price * quantity for price, quantity in zip(prices, demand)),
            "utility": utility, "budget_shares": shares}


def _ces_demand(body: Mapping[str, Any]) -> Dict[str, Any]:
    alphas, prices = _positive_vector(body, "alphas"), _positive_vector(body, "prices")
    if len(alphas) != len(prices):
        raise ValueError("alphas and prices must have equal length")
    rho = _number(body, "rho")
    if abs(rho - 1) < 1e-12:
        raise ValueError("rho must differ from 1")
    income = float(body.get("income", 1.0))
    weights = [value / sum(alphas) for value in alphas]
    if abs(rho) < 1e-10:
        adapted = dict(body)
        adapted["alphas"] = weights
        adapted["income"] = income
        answer = _cobb_douglas(adapted)
        answer["elasticity"] = 1.0
        return answer
    elasticity = 1 / (1 - rho)
    numerators = [weight ** elasticity * price ** (-elasticity)
                  for weight, price in zip(weights, prices)]
    scale = income / sum(price * quantity for price, quantity in zip(prices, numerators))
    demand = [scale * value for value in numerators]
    utility = sum(weight * quantity ** rho for weight, quantity in zip(weights, demand)) ** (1 / rho)
    return {"demand": demand, "expenditure": income, "utility": utility, "elasticity": elasticity}


def _exchange_equilibrium(body: Mapping[str, Any]) -> Dict[str, Any]:
    endowments = [[float(value) for value in row] for row in body.get("endowments", [])]
    alphas = _vector(body, "alphas")
    if len(endowments) < 2 or len(alphas) != len(endowments) or any(len(row) != 2 for row in endowments):
        raise ValueError("endowments must be agents x 2 goods and alphas one value per agent")
    if any(not 0 < alpha < 1 for alpha in alphas) or any(value < 0 for row in endowments for value in row):
        raise ValueError("alphas must be in (0,1) and endowments non-negative")
    total_first = sum(row[0] for row in endowments)
    numerator = total_first - sum(alpha * row[0] for alpha, row in zip(alphas, endowments))
    denominator = sum(alpha * row[1] for alpha, row in zip(alphas, endowments))
    relative_price = numerator / denominator if denominator > 0 else 1.0
    if relative_price <= 0:
        raise ValueError("endowments do not admit a positive two-good equilibrium")
    prices = [1.0, relative_price]
    allocations = []
    for alpha, endowed in zip(alphas, endowments):
        income = endowed[0] + relative_price * endowed[1]
        allocations.append([alpha * income, (1 - alpha) * income / relative_price])
    excess = [sum(row[index] for row in allocations) - sum(row[index] for row in endowments)
              for index in range(2)]
    return {"equilibrium_prices": prices, "allocations": allocations, "excess_demand": excess,
            "market_clears": max(abs(value) for value in excess) < 1e-9}


def _game_matrices(body: Mapping[str, Any]) -> tuple[list[list[float]], list[list[float]]]:
    first = [[float(value) for value in row] for row in body.get("payoff_matrix_1", [])]
    second = [[float(value) for value in row] for row in body.get("payoff_matrix_2", [])]
    if not first or not first[0] or len(first) != len(second):
        raise ValueError("payoff matrices must be non-empty and have equal dimensions")
    columns = len(first[0])
    if any(len(row) != columns for row in first + second):
        raise ValueError("payoff matrices must be rectangular and have equal dimensions")
    return first, second


def _game_create(body: Mapping[str, Any]) -> Dict[str, Any]:
    first, second = _game_matrices(body)
    return {"payoff_matrix_1": first, "payoff_matrix_2": second,
            "n_strategies_1": len(first), "n_strategies_2": len(first[0]),
            "zero_sum": all(abs(first[row][column] + second[row][column]) < 1e-12
                            for row in range(len(first)) for column in range(len(first[0])))}


_CLASSIC_GAMES = {
    "prisoners_dilemma": ([[3, 0], [5, 1]], [[3, 5], [0, 1]]),
    "battle_of_sexes": ([[2, 0], [0, 1]], [[1, 0], [0, 2]]),
    "chicken": ([[3, 1], [4, 0]], [[3, 4], [1, 0]]),
    "stag_hunt": ([[4, 1], [3, 2]], [[4, 3], [1, 2]]),
    "matching_pennies": ([[1, -1], [-1, 1]], [[-1, 1], [1, -1]]),
}


def _classic_game(body: Mapping[str, Any]) -> Dict[str, Any]:
    name = str(body.get("game_name", "")).lower()
    if name not in _CLASSIC_GAMES:
        raise ValueError("unknown classic game")
    first, second = _CLASSIC_GAMES[name]
    return {"game_name": name, "payoff_matrix_1": first, "payoff_matrix_2": second}


def _pure_nash(first: Sequence[Sequence[float]], second: Sequence[Sequence[float]]) -> list[list[int]]:
    equilibria = []
    for row in range(len(first)):
        for column in range(len(first[0])):
            row_best = first[row][column] >= max(first[index][column] for index in range(len(first))) - 1e-12
            column_best = second[row][column] >= max(second[row][index] for index in range(len(first[0]))) - 1e-12
            if row_best and column_best:
                equilibria.append([row, column])
    return equilibria


def _mixed_nash(body: Mapping[str, Any]) -> Dict[str, Any]:
    first, second = _game_matrices(body)
    if len(first) != 2 or len(first[0]) != 2:
        raise ValueError("mixed-nash currently supports 2x2 games")
    pure = _pure_nash(first, second)
    if len(pure) == 1:
        row, column = pure[0]
        return {"strategy_1": [1.0 if index == row else 0.0 for index in range(2)],
                "strategy_2": [1.0 if index == column else 0.0 for index in range(2)],
                "equilibrium_type": "pure", "pure_equilibria": pure}
    row_denominator = second[0][0] - second[0][1] - second[1][0] + second[1][1]
    column_denominator = first[0][0] - first[0][1] - first[1][0] + first[1][1]
    if abs(row_denominator) < 1e-14 or abs(column_denominator) < 1e-14:
        if pure:
            row, column = pure[0]
            return {"strategy_1": [1.0 if index == row else 0.0 for index in range(2)],
                    "strategy_2": [1.0 if index == column else 0.0 for index in range(2)],
                    "equilibrium_type": "pure", "pure_equilibria": pure}
        raise ValueError("game has no unique interior mixed equilibrium")
    p = (second[1][1] - second[1][0]) / row_denominator
    q = (first[1][1] - first[0][1]) / column_denominator
    if not 0 <= p <= 1 or not 0 <= q <= 1:
        raise ValueError("game has no interior mixed equilibrium")
    return {"strategy_1": [p, 1 - p], "strategy_2": [q, 1 - q],
            "equilibrium_type": "mixed", "pure_equilibria": pure}


def _expected_game_payoffs(first: Sequence[Sequence[float]], second: Sequence[Sequence[float]],
                           strategy_1: Sequence[float], strategy_2: Sequence[float]) -> tuple[float, float]:
    payoff_1 = sum(strategy_1[row] * strategy_2[column] * first[row][column]
                   for row in range(len(first)) for column in range(len(first[0])))
    payoff_2 = sum(strategy_1[row] * strategy_2[column] * second[row][column]
                   for row in range(len(first)) for column in range(len(first[0])))
    return payoff_1, payoff_2


def _nash_check(body: Mapping[str, Any]) -> Dict[str, Any]:
    first, second = _game_matrices(body)
    strategy_1 = _normalized_probabilities(body, "strategy_1", len(first))
    strategy_2 = _normalized_probabilities(body, "strategy_2", len(first[0]))
    payoff_1, payoff_2 = _expected_game_payoffs(first, second, strategy_1, strategy_2)
    row_values = [sum(strategy_2[column] * first[row][column] for column in range(len(strategy_2)))
                  for row in range(len(first))]
    column_values = [sum(strategy_1[row] * second[row][column] for row in range(len(strategy_1)))
                     for column in range(len(strategy_2))]
    regret_1, regret_2 = max(row_values) - payoff_1, max(column_values) - payoff_2
    return {"is_nash": max(regret_1, regret_2) < 1e-9, "payoffs": [payoff_1, payoff_2],
            "regrets": [regret_1, regret_2]}


def _best_response(body: Mapping[str, Any]) -> Dict[str, Any]:
    first, second = _game_matrices(body)
    strategy_1 = _normalized_probabilities(body, "strategy_1", len(first))
    strategy_2 = _normalized_probabilities(body, "strategy_2", len(first[0]))
    row_values = [sum(strategy_2[column] * first[row][column] for column in range(len(strategy_2)))
                  for row in range(len(first))]
    column_values = [sum(strategy_1[row] * second[row][column] for row in range(len(strategy_1)))
                     for column in range(len(strategy_2))]
    best_1, best_2 = max(row_values), max(column_values)
    return {"best_responses_1": [index for index, value in enumerate(row_values) if abs(value - best_1) < 1e-12],
            "best_responses_2": [index for index, value in enumerate(column_values) if abs(value - best_2) < 1e-12],
            "strategy_values_1": row_values, "strategy_values_2": column_values}


def _eliminate_dominated(body: Mapping[str, Any]) -> Dict[str, Any]:
    first, second = _game_matrices(body)
    rows, columns = list(range(len(first))), list(range(len(first[0])))
    changed = True
    while changed:
        changed = False
        for row in list(rows):
            if any(other != row and all(first[other][column] > first[row][column] for column in columns)
                   for other in rows):
                rows.remove(row); changed = True; break
        if changed:
            continue
        for column in list(columns):
            if any(other != column and all(second[row][other] > second[row][column] for row in rows)
                   for other in columns):
                columns.remove(column); changed = True; break
    return {"remaining_strategies_1": rows, "remaining_strategies_2": columns,
            "reduced_payoff_matrix_1": [[first[row][column] for column in columns] for row in rows],
            "reduced_payoff_matrix_2": [[second[row][column] for column in columns] for row in rows]}


def _fictitious_play(body: Mapping[str, Any]) -> Dict[str, Any]:
    first, second = _game_matrices(body)
    iterations = int(body.get("iterations", 1000))
    if not 1 <= iterations <= 100000:
        raise ValueError("iterations is outside local safety limits")
    counts_1, counts_2 = [1.0] * len(first), [1.0] * len(first[0])
    for _ in range(iterations):
        strategy_1 = [value / sum(counts_1) for value in counts_1]
        strategy_2 = [value / sum(counts_2) for value in counts_2]
        row_values = [sum(strategy_2[column] * first[row][column] for column in range(len(strategy_2)))
                      for row in range(len(first))]
        column_values = [sum(strategy_1[row] * second[row][column] for row in range(len(strategy_1)))
                         for column in range(len(strategy_2))]
        counts_1[max(range(len(row_values)), key=row_values.__getitem__)] += 1
        counts_2[max(range(len(column_values)), key=column_values.__getitem__)] += 1
    return {"strategy_1": [value / sum(counts_1) for value in counts_1],
            "strategy_2": [value / sum(counts_2) for value in counts_2], "iterations": iterations}


def _auction_bids(auction_type: str, valuations: Sequence[float]) -> list[float]:
    count = len(valuations)
    if auction_type in {"second_price", "english"}:
        return list(valuations)
    if auction_type in {"first_price", "dutch"}:
        return [(count - 1) / count * value for value in valuations]
    if auction_type == "all_pay":
        return [(count - 1) / count * value ** count for value in valuations]
    raise ValueError("unsupported auction_type")


def _auction_run(body: Mapping[str, Any]) -> Dict[str, Any]:
    auction_type = str(body.get("auction_type", "first_price")).lower()
    valuations = _positive_vector(body, "valuations")
    bidder_count = int(body.get("n_bidders", len(valuations)))
    if bidder_count != len(valuations):
        raise ValueError("n_bidders must match valuations")
    bids = _auction_bids(auction_type, valuations)
    winner = max(range(bidder_count), key=bids.__getitem__)
    ranked = sorted(bids, reverse=True)
    payment = ranked[1] if auction_type in {"second_price", "english"} and bidder_count > 1 else bids[winner]
    revenue = sum(bids) if auction_type == "all_pay" else payment
    return {"winner": winner, "winning_bid": bids[winner], "payment": payment,
            "revenue": revenue, "bids": bids, "surplus": valuations[winner] - payment}


def _auction_equilibrium_bid(body: Mapping[str, Any]) -> Dict[str, Any]:
    auction_type = str(body.get("auction_type", "first_price")).lower()
    valuations = _positive_vector(body, "valuations")
    bidder_count = int(body.get("n_bidders", len(valuations)))
    if bidder_count < 2:
        raise ValueError("n_bidders must be at least two")
    return {"bids": _auction_bids(auction_type, valuations), "auction_type": auction_type}


def _auction_expected_revenue(body: Mapping[str, Any]) -> Dict[str, float]:
    auction_type = str(body.get("auction_type", "first_price")).lower()
    bidder_count = int(body.get("n_bidders", 0))
    if bidder_count < 2:
        raise ValueError("n_bidders must be at least two")
    if auction_type not in {"first_price", "second_price", "english", "dutch"}:
        raise ValueError("expected revenue supports standard first- and second-price auctions")
    return {"expected_revenue": (bidder_count - 1) / (bidder_count + 1),
            "expected_winning_value": bidder_count / (bidder_count + 1)}


def _auction_simulate(body: Mapping[str, Any]) -> Dict[str, Any]:
    auction_type = str(body.get("auction_type", "first_price")).lower()
    bidder_count = int(body.get("n_bidders", 0))
    simulations = int(body.get("n_simulations", 1000))
    if (not 2 <= bidder_count <= 1000 or not 1 <= simulations <= 100000 or
            bidder_count * simulations > 1000000):
        raise ValueError("auction simulation sizes are outside local limits")
    generator = random.Random(int(body.get("seed", 42)))
    revenues = []
    for _ in range(simulations):
        values = [generator.random() for _ in range(bidder_count)]
        result = _auction_run({"auction_type": auction_type, "n_bidders": bidder_count, "valuations": values})
        revenues.append(result["revenue"])
    return {"mean_revenue": statistics.mean(revenues), "revenue_std": statistics.pstdev(revenues),
            "n_simulations": simulations, "seed": int(body.get("seed", 42))}


def _utility_value(kind: str, wealth: float, parameter: float) -> float:
    if kind == "cara":
        if parameter <= 0:
            raise ValueError("CARA risk aversion must be positive")
        return -math.exp(-parameter * wealth)
    if wealth <= 0 and kind in {"crra", "log"}:
        raise ValueError(f"{kind} utility requires positive wealth")
    if kind == "crra":
        if parameter == 1:
            return math.log(wealth)
        return wealth ** (1 - parameter) / (1 - parameter)
    if kind == "log":
        return math.log(wealth)
    if kind == "quadratic":
        return wealth - 0.5 * parameter * wealth * wealth
    raise ValueError("utility_type must be cara, crra, log, or quadratic")


def _utility_inverse(kind: str, utility: float, parameter: float) -> float:
    if kind == "cara":
        if utility >= 0:
            raise ValueError("invalid CARA expected utility")
        return -math.log(-utility) / parameter
    if kind == "crra":
        return math.exp(utility) if parameter == 1 else ((1 - parameter) * utility) ** (1 / (1 - parameter))
    if kind == "log":
        return math.exp(utility)
    if kind == "quadratic":
        if parameter <= 0:
            return utility
        discriminant = max(0.0, 1 - 2 * parameter * utility)
        return (1 - math.sqrt(discriminant)) / parameter
    raise ValueError("unknown utility type")


def _single_utility(body: Mapping[str, Any], kind: str) -> Dict[str, float]:
    wealth = _number(body, "wealth")
    if kind == "cara":
        parameter = _positive(body, "risk_aversion")
    elif kind == "crra":
        parameter = _number(body, "gamma")
    elif kind == "quadratic":
        parameter = float(body.get("risk_aversion", 0.01))
    else:
        parameter = 1.0
    return {"utility": _utility_value(kind, wealth, parameter), "wealth": wealth}


def _lottery_utility(body: Mapping[str, Any]) -> tuple[str, float, list[float], list[float], float]:
    kind = str(body.get("utility_type", "crra")).lower()
    outcomes = _vector(body, "outcomes")
    probabilities = _normalized_probabilities(body, "probabilities", len(outcomes))
    parameter = float(body.get("param", 1.0))
    expected_utility = sum(probability * _utility_value(kind, outcome, parameter)
                           for outcome, probability in zip(outcomes, probabilities))
    return kind, parameter, outcomes, probabilities, expected_utility


def _expected_utility(body: Mapping[str, Any]) -> Dict[str, float]:
    kind, _parameter, outcomes, probabilities, utility = _lottery_utility(body)
    return {"expected_utility": utility,
            "expected_outcome": sum(value * probability for value, probability in zip(outcomes, probabilities)),
            "utility_type": kind}


def _certainty_equivalent(body: Mapping[str, Any]) -> Dict[str, float]:
    kind, parameter, outcomes, probabilities, utility = _lottery_utility(body)
    expected = sum(value * probability for value, probability in zip(outcomes, probabilities))
    certainty = _utility_inverse(kind, utility, parameter)
    return {"certainty_equivalent": certainty, "expected_outcome": expected,
            "risk_premium": expected - certainty, "expected_utility": utility}


def _certainty_equivalent_approximation(body: Mapping[str, Any]) -> Dict[str, float]:
    mean, variance, risk_aversion = (_number(body, "mean"), _number(body, "variance"),
                                     _positive(body, "risk_aversion"))
    if variance < 0:
        raise ValueError("variance cannot be negative")
    premium = 0.5 * risk_aversion * variance
    return {"certainty_equivalent": mean - premium, "risk_premium": premium}


def _risk_premium(body: Mapping[str, Any]) -> Dict[str, float]:
    result = _certainty_equivalent(body)
    return {"risk_premium": result["risk_premium"], "certainty_equivalent": result["certainty_equivalent"],
            "expected_outcome": result["expected_outcome"]}


def _prospect_theory(body: Mapping[str, Any]) -> Dict[str, float]:
    alpha, beta, loss_aversion, outcome = (_positive(body, "alpha"), _positive(body, "beta"),
                                           _positive(body, "lambda_param"), _number(body, "outcome"))
    reference = float(body.get("reference_point", 0.0))
    delta = outcome - reference
    value = delta ** alpha if delta >= 0 else -loss_aversion * (-delta) ** beta
    return {"value": value, "gain_or_loss": "gain" if delta >= 0 else "loss", "reference_point": reference}


def _stochastic_dominance(body: Mapping[str, Any]) -> Dict[str, Any]:
    cdf_a = [[float(value) for value in row] for row in body.get("cdf_a", [])]
    cdf_b = [[float(value) for value in row] for row in body.get("cdf_b", [])]
    order = int(body.get("order", 1))
    if len(cdf_a) < 2 or len(cdf_a) != len(cdf_b) or any(len(row) != 2 for row in cdf_a + cdf_b):
        raise ValueError("cdf_a and cdf_b must be equal-length [x, F(x)] pairs")
    if any(abs(left[0] - right[0]) > 1e-12 for left, right in zip(cdf_a, cdf_b)):
        raise ValueError("CDF support points must match")
    if order == 1:
        a_dominates = all(left[1] <= right[1] + 1e-12 for left, right in zip(cdf_a, cdf_b))
        b_dominates = all(right[1] <= left[1] + 1e-12 for left, right in zip(cdf_a, cdf_b))
    elif order == 2:
        integral_a = integral_b = 0.0
        a_dominates = b_dominates = True
        for index in range(1, len(cdf_a)):
            width = cdf_a[index][0] - cdf_a[index - 1][0]
            integral_a += width * (cdf_a[index - 1][1] + cdf_a[index][1]) / 2
            integral_b += width * (cdf_b[index - 1][1] + cdf_b[index][1]) / 2
            a_dominates &= integral_a <= integral_b + 1e-12
            b_dominates &= integral_b <= integral_a + 1e-12
    else:
        raise ValueError("order must be 1 or 2")
    dominant = "equivalent" if a_dominates and b_dominates else "a" if a_dominates else "b" if b_dominates else "neither"
    return {"dominant": dominant, "order": order, "a_dominates_b": a_dominates,
            "b_dominates_a": b_dominates}


def _probability_vector(body: Mapping[str, Any], name: str) -> list[float]:
    values = _vector(body, name)
    if any(value < 0 for value in values):
        raise ValueError(f"{name} cannot contain negative probabilities")
    total = sum(values)
    if total <= 0:
        raise ValueError(f"{name} must have positive probability mass")
    return [value / total for value in values]


def _entropy_values(probabilities: Sequence[float]) -> float:
    return -sum(value * math.log(value) for value in probabilities if value > 0)


def _shannon_entropy(body: Mapping[str, Any]) -> Dict[str, float]:
    probabilities = _probability_vector(body, "probabilities")
    return {"entropy": _entropy_values(probabilities), "effective_states": math.exp(_entropy_values(probabilities))}


def _renyi_entropy(body: Mapping[str, Any]) -> Dict[str, float]:
    probabilities, alpha = _probability_vector(body, "probabilities"), _positive(body, "alpha")
    if abs(alpha - 1) < 1e-10:
        entropy = _entropy_values(probabilities)
    else:
        entropy = math.log(sum(value ** alpha for value in probabilities)) / (1 - alpha)
    return {"entropy": entropy, "alpha": alpha}


def _tsallis_entropy(body: Mapping[str, Any]) -> Dict[str, float]:
    probabilities, q_value = _probability_vector(body, "probabilities"), _positive(body, "q")
    entropy = _entropy_values(probabilities) if abs(q_value - 1) < 1e-10 else (
        1 - sum(value ** q_value for value in probabilities)
    ) / (q_value - 1)
    return {"entropy": entropy, "q": q_value}


def _paired_probabilities(body: Mapping[str, Any]) -> tuple[list[float], list[float]]:
    first, second = _probability_vector(body, "p"), _probability_vector(body, "q")
    if len(first) != len(second):
        raise ValueError("p and q must have equal length")
    return first, second


def _cross_entropy(body: Mapping[str, Any]) -> Dict[str, float]:
    first, second = _paired_probabilities(body)
    if any(q_value <= 0 and p_value > 0 for p_value, q_value in zip(first, second)):
        raise ValueError("q must be positive wherever p is positive")
    return {"cross_entropy": -sum(p_value * math.log(q_value)
                                   for p_value, q_value in zip(first, second) if p_value > 0)}


def _joint_distribution(body: Mapping[str, Any]) -> list[list[float]]:
    rows = [[float(value) for value in row] for row in body.get("joint_prob", [])]
    if not rows or not rows[0] or any(len(row) != len(rows[0]) for row in rows):
        raise ValueError("joint_prob must be a non-empty rectangular matrix")
    if any(value < 0 for row in rows for value in row):
        raise ValueError("joint_prob cannot contain negative values")
    total = sum(sum(row) for row in rows)
    if total <= 0:
        raise ValueError("joint_prob must have positive probability mass")
    return [[value / total for value in row] for row in rows]


def _joint_entropy(body: Mapping[str, Any]) -> Dict[str, float]:
    joint = _joint_distribution(body)
    return {"joint_entropy": _entropy_values([value for row in joint for value in row])}


def _conditional_entropy(body: Mapping[str, Any]) -> Dict[str, float]:
    joint = _joint_distribution(body)
    marginal_y = [sum(row[column] for row in joint) for column in range(len(joint[0]))]
    value = _entropy_values([item for row in joint for item in row]) - _entropy_values(marginal_y)
    return {"conditional_entropy": max(0.0, value), "conditioning": "X|Y"}


def _mutual_information(body: Mapping[str, Any]) -> Dict[str, float]:
    joint = _joint_distribution(body)
    marginal_x = [sum(row) for row in joint]
    marginal_y = [sum(row[column] for row in joint) for column in range(len(joint[0]))]
    value = 0.0
    for row in range(len(joint)):
        for column in range(len(joint[0])):
            probability = joint[row][column]
            if probability > 0:
                value += probability * math.log(probability / (marginal_x[row] * marginal_y[column]))
    return {"mutual_information": max(0.0, value)}


def _differential_entropy(body: Mapping[str, Any]) -> Dict[str, float]:
    distribution = str(body.get("distribution", "gaussian")).lower()
    if distribution in {"gaussian", "normal"}:
        sigma = _positive(body, "sigma")
        value = 0.5 * math.log(2 * math.pi * math.e * sigma * sigma)
    elif distribution == "exponential":
        rate = _positive(body, "rate")
        value = 1 - math.log(rate)
    elif distribution == "uniform":
        width = _positive(body, "width")
        value = math.log(width)
    else:
        raise ValueError("distribution must be gaussian, exponential, or uniform")
    return {"differential_entropy": value, "distribution": distribution}


def _discretize(values: Sequence[float], bins: int) -> list[int]:
    low, high = min(values), max(values)
    if high == low:
        return [0] * len(values)
    return [min(bins - 1, int((value - low) / (high - low) * bins)) for value in values]


def _transfer_entropy(body: Mapping[str, Any]) -> Dict[str, Any]:
    x_values, y_values = _vector(body, "x_history"), _vector(body, "y_history")
    lag, bins = int(body.get("lag", 1)), int(body.get("n_bins", 3))
    if len(x_values) != len(y_values) or len(x_values) <= lag or lag < 1 or not 2 <= bins <= 50:
        raise ValueError("histories, lag, or n_bins are invalid")
    x_bins, y_bins = _discretize(x_values, bins), _discretize(y_values, bins)
    triples: Dict[tuple[int, int, int], int] = {}
    x_pairs: Dict[tuple[int, int], int] = {}
    histories: Dict[tuple[int, int], int] = {}
    x_history_counts: Dict[int, int] = {}
    for index in range(lag, len(x_bins)):
        current, x_past, y_past = x_bins[index], x_bins[index - lag], y_bins[index - lag]
        triples[(current, x_past, y_past)] = triples.get((current, x_past, y_past), 0) + 1
        x_pairs[(current, x_past)] = x_pairs.get((current, x_past), 0) + 1
        histories[(x_past, y_past)] = histories.get((x_past, y_past), 0) + 1
        x_history_counts[x_past] = x_history_counts.get(x_past, 0) + 1
    observations = len(x_bins) - lag
    value = 0.0
    for (current, x_past, y_past), count in triples.items():
        numerator = count / histories[(x_past, y_past)]
        denominator = x_pairs[(current, x_past)] / x_history_counts[x_past]
        value += count / observations * math.log(numerator / denominator)
    return {"transfer_entropy": max(0.0, value), "lag": lag, "n_bins": bins,
            "observations": observations}


def _fisher_information(body: Mapping[str, Any]) -> Dict[str, float]:
    distribution = str(body.get("distribution", "gaussian")).lower()
    theta = _positive(body, "theta")
    if distribution in {"gaussian", "normal"}:
        sigma = float(body.get("sigma", 1.0))
        if sigma <= 0:
            raise ValueError("sigma must be positive")
        value = 1 / (sigma * sigma)
    elif distribution == "exponential":
        value = 1 / (theta * theta)
    elif distribution == "poisson":
        value = 1 / theta
    elif distribution == "bernoulli":
        if not 0 < theta < 1:
            raise ValueError("Bernoulli theta must be in (0,1)")
        value = 1 / (theta * (1 - theta))
    else:
        raise ValueError("unsupported Fisher-information distribution")
    return {"fisher_information": value, "distribution": distribution, "theta": theta}


def _markov_entropy_rate(body: Mapping[str, Any]) -> Dict[str, Any]:
    transition = _matrix(body, "transition_matrix")
    size = len(transition)
    normalized = []
    for row in transition:
        if any(value < 0 for value in row) or sum(row) <= 0:
            raise ValueError("transition rows must contain non-negative probability mass")
        normalized.append([value / sum(row) for value in row])
    stationary = [1 / size] * size
    for _ in range(10000):
        following = [sum(stationary[row] * normalized[row][column] for row in range(size))
                     for column in range(size)]
        if max(abs(left - right) for left, right in zip(stationary, following)) < 1e-14:
            stationary = following
            break
        stationary = following
    rate = sum(weight * _entropy_values(row) for weight, row in zip(stationary, normalized))
    return {"entropy_rate": rate, "stationary_distribution": stationary}


def _kl_divergence(body: Mapping[str, Any]) -> Dict[str, float]:
    first, second = _paired_probabilities(body)
    if any(q_value <= 0 and p_value > 0 for p_value, q_value in zip(first, second)):
        raise ValueError("q must be positive wherever p is positive")
    value = sum(p_value * math.log(p_value / q_value)
                for p_value, q_value in zip(first, second) if p_value > 0)
    return {"divergence": max(0.0, value)}


def _js_divergence(body: Mapping[str, Any]) -> Dict[str, float]:
    first, second = _paired_probabilities(body)
    midpoint = [(left + right) / 2 for left, right in zip(first, second)]
    value = 0.5 * sum(left * math.log(left / middle) for left, middle in zip(first, midpoint) if left > 0)
    value += 0.5 * sum(right * math.log(right / middle) for right, middle in zip(second, midpoint) if right > 0)
    return {"divergence": max(0.0, value), "distance": math.sqrt(max(0.0, value))}


def _boltzmann_distribution(body: Mapping[str, Any]) -> Dict[str, Any]:
    energies, temperature = _vector(body, "energies"), _positive(body, "temperature")
    boltzmann_constant = float(body.get("boltzmann_constant", 1.0))
    if boltzmann_constant <= 0:
        raise ValueError("boltzmann_constant must be positive")
    baseline = min(energies)
    weights = [math.exp(-(energy - baseline) / (boltzmann_constant * temperature)) for energy in energies]
    partition = sum(weights) * math.exp(-baseline / (boltzmann_constant * temperature))
    probabilities = [value / sum(weights) for value in weights]
    mean_energy = sum(value * probability for value, probability in zip(energies, probabilities))
    return {"probabilities": probabilities, "partition_function": partition,
            "mean_energy": mean_energy, "entropy": _entropy_values(probabilities)}


def _maximum_entropy(body: Mapping[str, Any]) -> Dict[str, Any]:
    states = int(body.get("n_states", 0))
    constraints = _vector(body, "constraint_values")
    if states < 1 or len(constraints) != states:
        raise ValueError("n_states must match constraint_values")
    probabilities = [1 / states] * states
    return {"probabilities": probabilities, "entropy": math.log(states),
            "expected_constraint": statistics.mean(constraints), "method": "unconstrained-maximum-entropy"}


def _ising_energy(lattice: Sequence[Sequence[int]], coupling: float, field: float) -> float:
    size = len(lattice)
    interaction = 0.0
    magnetization = 0
    for row in range(size):
        for column in range(size):
            spin = lattice[row][column]
            magnetization += spin
            interaction += spin * (lattice[row][(column + 1) % size] + lattice[(row + 1) % size][column])
    return -coupling * interaction - field * magnetization


def _ising_simulation(body: Mapping[str, Any]) -> Dict[str, Any]:
    size, steps = int(body.get("size", 10)), int(body.get("n_steps", 1000))
    coupling, field, temperature = _number(body, "J"), _number(body, "h"), _positive(body, "temperature")
    if not 2 <= size <= 100 or not 1 <= steps <= 1000000:
        raise ValueError("Ising size or n_steps is outside local limits")
    generator = random.Random(int(body.get("seed", 42)))
    lattice = [[1 if generator.random() < 0.5 else -1 for _ in range(size)] for _ in range(size)]
    accepted = 0
    for _ in range(steps):
        row, column = generator.randrange(size), generator.randrange(size)
        spin = lattice[row][column]
        neighbors = (lattice[(row - 1) % size][column] + lattice[(row + 1) % size][column] +
                     lattice[row][(column - 1) % size] + lattice[row][(column + 1) % size])
        delta_energy = 2 * spin * (coupling * neighbors + field)
        if delta_energy <= 0 or generator.random() < math.exp(-delta_energy / temperature):
            lattice[row][column] = -spin
            accepted += 1
    spins = size * size
    return {"lattice": lattice, "energy": _ising_energy(lattice, coupling, field),
            "energy_per_spin": _ising_energy(lattice, coupling, field) / spins,
            "magnetization": sum(sum(row) for row in lattice) / spins,
            "acceptance_rate": accepted / steps, "seed": int(body.get("seed", 42))}


def _ising_critical_temperature(body: Mapping[str, Any]) -> Dict[str, float]:
    coupling = _positive(body, "J")
    return {"critical_temperature": 2 * coupling / math.log(1 + math.sqrt(2))}


_GAS_CONSTANT = 8.31446261815324


def _ideal_gas(body: Mapping[str, Any]) -> Dict[str, float]:
    moles, temperature, volume = _positive(body, "n"), _positive(body, "temperature"), _positive(body, "volume")
    pressure = moles * _GAS_CONSTANT * temperature / volume
    return {"pressure": pressure, "compressibility_factor": 1.0}


def _van_der_waals(body: Mapping[str, Any]) -> Dict[str, float]:
    attraction, excluded = _positive(body, "a"), _positive(body, "b")
    temperature, volume = _positive(body, "temperature"), _positive(body, "volume")
    moles = float(body.get("n", 1.0))
    available = volume - moles * excluded
    if moles <= 0 or available <= 0:
        raise ValueError("volume must exceed n*b")
    pressure = moles * _GAS_CONSTANT * temperature / available - attraction * (moles / volume) ** 2
    return {"pressure": pressure, "compressibility_factor": pressure * volume / (moles * _GAS_CONSTANT * temperature)}


def _carnot(body: Mapping[str, Any]) -> Dict[str, float]:
    hot, cold = _positive(body, "T_hot"), _positive(body, "T_cold")
    if cold >= hot:
        raise ValueError("T_cold must be below T_hot")
    return {"efficiency": 1 - cold / hot, "coefficient_of_performance_refrigerator": cold / (hot - cold),
            "coefficient_of_performance_heat_pump": hot / (hot - cold)}


def _free_energy(body: Mapping[str, Any]) -> Dict[str, float]:
    internal, temperature, entropy = (_number(body, "internal_energy"), _positive(body, "temperature"),
                                      _number(body, "entropy"))
    return {"helmholtz_free_energy": internal - temperature * entropy}


def _clausius_clapeyron(body: Mapping[str, Any]) -> Dict[str, float]:
    enthalpy, temperature = _number(body, "delta_H"), _positive(body, "T")
    slope = enthalpy / (_GAS_CONSTANT * temperature * temperature)
    return {"d_ln_pressure_d_temperature": slope, "integrated_coefficient": -enthalpy / _GAS_CONSTANT}


def _maxwell_relations(_body: Mapping[str, Any]) -> Dict[str, Any]:
    return {"relations": [
        {"potential": "internal_energy", "identity": "(dT/dV)_S = -(dP/dS)_V"},
        {"potential": "enthalpy", "identity": "(dT/dP)_S = (dV/dS)_P"},
        {"potential": "helmholtz", "identity": "(dS/dV)_T = (dP/dT)_V"},
        {"potential": "gibbs", "identity": "(dS/dP)_T = -(dV/dT)_P"},
    ]}


def _joule_thomson(body: Mapping[str, Any]) -> Dict[str, float]:
    heat_capacity, volume, temperature, derivative = (_positive(body, "Cp"), _positive(body, "V"),
                                                       _positive(body, "T"), _number(body, "dV_dT_P"))
    coefficient = (temperature * derivative - volume) / heat_capacity
    return {"joule_thomson_coefficient": coefficient,
            "behavior": "cooling" if coefficient > 0 else "heating" if coefficient < 0 else "neutral"}


def _statistics_normal(body: Mapping[str, Any], operation: str) -> Dict[str, Any]:
    mean, deviation = _number(body, "mu"), _positive(body, "sigma")
    if operation == "properties":
        return {"mean": mean, "variance": deviation * deviation, "standard_deviation": deviation,
                "skewness": 0.0, "excess_kurtosis": 0.0}
    if operation == "ppf":
        probability = _number(body, "p")
        if not 0 < probability < 1:
            raise ValueError("p must be in (0,1)")
        value = mean + deviation * NORMAL.inv_cdf(probability)
    else:
        standardized = (_number(body, "x") - mean) / deviation
        value = NORMAL.cdf(standardized) if operation == "cdf" else NORMAL.pdf(standardized) / deviation
    return {operation: value, "value": value}


def _statistics_lognormal(body: Mapping[str, Any], operation: str) -> Dict[str, Any]:
    mean, deviation = _number(body, "mu"), _positive(body, "sigma")
    if operation == "properties":
        variance_factor = math.exp(deviation * deviation)
        expected = math.exp(mean + 0.5 * deviation * deviation)
        variance = (variance_factor - 1) * math.exp(2 * mean + deviation * deviation)
        skewness = (variance_factor + 2) * math.sqrt(variance_factor - 1)
        excess = variance_factor ** 4 + 2 * variance_factor ** 3 + 3 * variance_factor ** 2 - 6
        return {"mean": expected, "variance": variance, "standard_deviation": math.sqrt(variance),
                "median": math.exp(mean), "mode": math.exp(mean - deviation * deviation),
                "skewness": skewness, "excess_kurtosis": excess}
    if operation == "ppf":
        probability = _number(body, "p")
        if not 0 < probability < 1:
            raise ValueError("p must be in (0,1)")
        value = math.exp(mean + deviation * NORMAL.inv_cdf(probability))
    else:
        x_value = _number(body, "x")
        if x_value <= 0:
            value = 0.0
        else:
            standardized = (math.log(x_value) - mean) / deviation
            value = NORMAL.cdf(standardized) if operation == "cdf" else NORMAL.pdf(standardized) / (x_value * deviation)
    return {operation: value, "value": value}


def _student_properties(body: Mapping[str, Any]) -> Dict[str, Any]:
    degrees = _positive(body, "df")
    variance = degrees / (degrees - 2) if degrees > 2 else None
    return {"mean": 0.0 if degrees > 1 else None, "variance": variance,
            "standard_deviation": math.sqrt(variance) if variance is not None else None,
            "skewness": 0.0 if degrees > 3 else None,
            "excess_kurtosis": 6 / (degrees - 4) if degrees > 4 else None}


def _chi_square_properties(body: Mapping[str, Any]) -> Dict[str, float]:
    degrees = _positive(body, "df")
    return {"mean": degrees, "variance": 2 * degrees, "standard_deviation": math.sqrt(2 * degrees),
            "skewness": math.sqrt(8 / degrees), "excess_kurtosis": 12 / degrees}


def _f_distribution(body: Mapping[str, Any], operation: str) -> Dict[str, Any]:
    first, second = _positive(body, "df1"), _positive(body, "df2")
    if operation == "properties":
        mean = second / (second - 2) if second > 2 else None
        variance = (2 * second * second * (first + second - 2) /
                    (first * (second - 2) ** 2 * (second - 4))) if second > 4 else None
        return {"mean": mean, "variance": variance,
                "standard_deviation": math.sqrt(variance) if variance is not None else None}
    x_value = _number(body, "x")
    if x_value <= 0:
        value = 0.0
    else:
        log_value = (first / 2) * math.log(first / second) + (first / 2 - 1) * math.log(x_value)
        log_value -= math.lgamma(first / 2) + math.lgamma(second / 2) - math.lgamma((first + second) / 2)
        log_value -= (first + second) / 2 * math.log1p(first * x_value / second)
        value = math.exp(log_value)
    return {"pdf": value, "value": value}


def _gamma_properties(body: Mapping[str, Any]) -> Dict[str, float]:
    alpha, rate = _positive(body, "alpha"), _positive(body, "beta")
    return {"mean": alpha / rate, "variance": alpha / (rate * rate),
            "standard_deviation": math.sqrt(alpha) / rate, "skewness": 2 / math.sqrt(alpha),
            "excess_kurtosis": 6 / alpha}


def _beta_distribution(body: Mapping[str, Any], operation: str) -> Dict[str, Any]:
    alpha, beta = _positive(body, "alpha"), _positive(body, "beta")
    if operation == "properties":
        total = alpha + beta
        variance = alpha * beta / (total * total * (total + 1))
        skewness = 2 * (beta - alpha) * math.sqrt(total + 1) / ((total + 2) * math.sqrt(alpha * beta))
        excess = 6 * ((alpha - beta) ** 2 * (total + 1) - alpha * beta * (total + 2)) / (
            alpha * beta * (total + 2) * (total + 3)
        )
        return {"mean": alpha / total, "variance": variance, "standard_deviation": math.sqrt(variance),
                "skewness": skewness, "excess_kurtosis": excess}
    x_value = _number(body, "x")
    if operation == "cdf":
        value = 0.0 if x_value <= 0 else 1.0 if x_value >= 1 else _regularized_beta(alpha, beta, x_value)
    elif not 0 < x_value < 1:
        value = 0.0
    else:
        value = math.exp((alpha - 1) * math.log(x_value) + (beta - 1) * math.log1p(-x_value) +
                         math.lgamma(alpha + beta) - math.lgamma(alpha) - math.lgamma(beta))
    return {operation: value, "value": value}


def _exponential_properties(body: Mapping[str, Any]) -> Dict[str, float]:
    rate = _positive(body, "rate")
    return {"mean": 1 / rate, "variance": 1 / (rate * rate), "standard_deviation": 1 / rate,
            "skewness": 2.0, "excess_kurtosis": 6.0}


def _integer_parameter(body: Mapping[str, Any], name: str, minimum: int = 0,
                       maximum: int | None = None) -> int:
    raw = _number(body, name)
    value = int(raw)
    if value != raw or value < minimum:
        raise ValueError(f"{name} must be an integer >= {minimum}")
    if maximum is not None and value > maximum:
        raise ValueError(f"{name} is outside local safety limits")
    return value


def _poisson_distribution(body: Mapping[str, Any], operation: str) -> Dict[str, Any]:
    intensity = _positive(body, "lam")
    if operation == "properties":
        return {"mean": intensity, "variance": intensity, "standard_deviation": math.sqrt(intensity),
                "skewness": 1 / math.sqrt(intensity), "excess_kurtosis": 1 / intensity}
    count = _integer_parameter(body, "k", maximum=10000)
    pmf = math.exp(-intensity + count * math.log(intensity) - math.lgamma(count + 1))
    value = pmf if operation == "pmf" else sum(
        math.exp(-intensity + index * math.log(intensity) - math.lgamma(index + 1)) for index in range(count + 1)
    )
    return {operation: value, "value": value}


def _binomial_distribution(body: Mapping[str, Any], operation: str) -> Dict[str, Any]:
    trials = _integer_parameter(body, "n", 1, 10000)
    probability = _number(body, "p")
    if not 0 <= probability <= 1:
        raise ValueError("p must be in [0,1]")
    if operation == "properties":
        variance = trials * probability * (1 - probability)
        return {"mean": trials * probability, "variance": variance,
                "standard_deviation": math.sqrt(variance)}
    count = _integer_parameter(body, "k", maximum=10000)
    def pmf(index: int) -> float:
        if index > trials:
            return 0.0
        return math.comb(trials, index) * probability ** index * (1 - probability) ** (trials - index)
    value = pmf(count) if operation == "pmf" else sum(pmf(index) for index in range(min(count, trials) + 1))
    return {operation: value, "value": value}


def _geometric_distribution(body: Mapping[str, Any], operation: str) -> Dict[str, Any]:
    probability = _number(body, "p")
    if not 0 < probability <= 1:
        raise ValueError("p must be in (0,1]")
    if operation == "properties":
        return {"mean": 1 / probability, "variance": (1 - probability) / (probability * probability),
                "standard_deviation": math.sqrt(1 - probability) / probability}
    if operation == "ppf":
        quantile = _number(body, "q")
        if not 0 < quantile < 1:
            raise ValueError("q must be in (0,1)")
        value = 1 if probability == 1 else math.ceil(math.log1p(-quantile) / math.log1p(-probability))
    else:
        count = _integer_parameter(body, "k", 1)
        value = probability * (1 - probability) ** (count - 1) if operation == "pmf" else 1 - (1 - probability) ** count
    return {operation: value, "value": value}


def _hypergeometric_distribution(body: Mapping[str, Any], operation: str) -> Dict[str, Any]:
    population = _integer_parameter(body, "N", 1, 10000)
    successes = _integer_parameter(body, "K", maximum=10000)
    draws = _integer_parameter(body, "n", maximum=10000)
    if successes > population or draws > population:
        raise ValueError("K and n cannot exceed N")
    if operation == "properties":
        probability = successes / population
        variance = draws * probability * (1 - probability) * (population - draws) / max(1, population - 1)
        return {"mean": draws * probability, "variance": variance, "standard_deviation": math.sqrt(variance)}
    count = _integer_parameter(body, "k", maximum=10000)
    def pmf(index: int) -> float:
        if index < max(0, draws - (population - successes)) or index > min(draws, successes):
            return 0.0
        return math.comb(successes, index) * math.comb(population - successes, draws - index) / math.comb(population, draws)
    value = pmf(count) if operation == "pmf" else sum(pmf(index) for index in range(count + 1))
    return {operation: value, "value": value}


def _negative_binomial_distribution(body: Mapping[str, Any], operation: str) -> Dict[str, Any]:
    successes = _integer_parameter(body, "r", 1, 10000)
    probability = _number(body, "p")
    if not 0 < probability <= 1:
        raise ValueError("p must be in (0,1]")
    if operation == "properties":
        mean = successes * (1 - probability) / probability
        variance = successes * (1 - probability) / (probability * probability)
        return {"mean": mean, "variance": variance, "standard_deviation": math.sqrt(variance)}
    count = _integer_parameter(body, "k", maximum=10000)
    def pmf(index: int) -> float:
        return math.comb(index + successes - 1, index) * probability ** successes * (1 - probability) ** index
    value = pmf(count) if operation == "pmf" else sum(pmf(index) for index in range(count + 1))
    return {operation: value, "value": value}


def _probability_generating_function(body: Mapping[str, Any]) -> Dict[str, float]:
    distribution, z_value = str(body.get("distribution", "")).lower(), _number(body, "z")
    if not 0 <= z_value <= 1:
        raise ValueError("z must be in [0,1]")
    if distribution == "poisson":
        value = math.exp(_positive(body, "lam") * (z_value - 1))
    elif distribution == "binomial":
        value = (1 - _number(body, "p") + _number(body, "p") * z_value) ** _integer_parameter(body, "n", 1)
    elif distribution == "geometric":
        probability = _number(body, "p")
        value = probability * z_value / (1 - (1 - probability) * z_value)
    elif distribution in {"negative-binomial", "negative_binomial"}:
        probability = _number(body, "p")
        value = (probability / (1 - (1 - probability) * z_value)) ** _integer_parameter(body, "r", 1)
    else:
        raise ValueError("unsupported PGF distribution")
    return {"pgf": value, "value": value}


def _least_squares_coefficients(design: Sequence[Sequence[float]], target: Sequence[float]) -> list[float]:
    width = len(design[0])
    gram = [[sum(row[left] * row[right] for row in design) for right in range(width)] for left in range(width)]
    projected = [sum(row[column] * value for row, value in zip(design, target)) for column in range(width)]
    ridge = 1e-10
    for index in range(width):
        gram[index][index] += ridge
    return _matvec(_inverse(gram), projected)


def _ar_fit(body: Mapping[str, Any]) -> Dict[str, Any]:
    data = _vector(body, "data")
    order = int(body.get("order", body.get("p", 1)))
    if not 1 <= order < len(data) // 2:
        raise ValueError("AR order must be positive and smaller than half the sample")
    design = [[1.0] + [data[index - lag] for lag in range(1, order + 1)]
              for index in range(order, len(data))]
    target = data[order:]
    coefficients = _least_squares_coefficients(design, target)
    fitted = [sum(value * coefficient for value, coefficient in zip(row, coefficients)) for row in design]
    residuals = [actual - estimate for actual, estimate in zip(target, fitted)]
    variance = sum(value * value for value in residuals) / max(1, len(residuals) - len(coefficients))
    log_likelihood = -0.5 * len(residuals) * (math.log(2 * math.pi * max(variance, 1e-300)) + 1)
    return {"intercept": coefficients[0], "coefficients": coefficients[1:], "fitted": fitted,
            "residuals": residuals, "innovation_variance": variance,
            "aic": 2 * len(coefficients) - 2 * log_likelihood,
            "bic": math.log(len(residuals)) * len(coefficients) - 2 * log_likelihood, "order": order}


def _ar_forecast(body: Mapping[str, Any]) -> Dict[str, Any]:
    fit = _ar_fit(body)
    history = _vector(body, "data")
    steps = int(body.get("steps", body.get("n_forecast", 5)))
    if not 1 <= steps <= 10000:
        raise ValueError("forecast steps are outside local limits")
    forecast = []
    for _ in range(steps):
        value = fit["intercept"] + sum(coefficient * history[-lag]
                                       for lag, coefficient in enumerate(fit["coefficients"], 1))
        history.append(value); forecast.append(value)
    return {"forecast": forecast, "order": fit["order"], "coefficients": fit["coefficients"],
            "intercept": fit["intercept"], "innovation_variance": fit["innovation_variance"]}


def _difference(values: Sequence[float], order: int) -> list[float]:
    result = list(values)
    for _ in range(order):
        result = [right - left for left, right in zip(result, result[1:])]
    return result


def _arima_fit(body: Mapping[str, Any]) -> Dict[str, Any]:
    data = _vector(body, "data")
    differencing = int(body.get("d", 1))
    ar_order, ma_order = int(body.get("p", 1)), int(body.get("q", 1))
    if not 0 <= differencing <= 2 or not 1 <= ar_order <= 10 or not 0 <= ma_order <= 10:
        raise ValueError("ARIMA orders are outside local limits")
    differenced = _difference(data, differencing)
    adapted = dict(body); adapted["data"] = differenced; adapted["order"] = ar_order
    fit = _ar_fit(adapted)
    fit.update({"p": ar_order, "d": differencing, "q": ma_order,
                "ma_coefficients": [0.0] * ma_order, "differenced_data": differenced})
    return fit


def _arima_forecast(body: Mapping[str, Any]) -> Dict[str, Any]:
    fit = _arima_fit(body)
    data = _vector(body, "data")
    differenced = list(fit["differenced_data"])
    steps = int(body.get("steps", body.get("n_forecast", 5)))
    difference_forecast = []
    for _ in range(steps):
        value = fit["intercept"] + sum(coefficient * differenced[-lag]
                                       for lag, coefficient in enumerate(fit["coefficients"], 1))
        differenced.append(value); difference_forecast.append(value)
    forecast = []
    last = data[-1]
    for value in difference_forecast:
        last += value if fit["d"] else 0.0
        forecast.append(last if fit["d"] else value)
    return {"forecast": forecast, "difference_forecast": difference_forecast,
            "p": fit["p"], "d": fit["d"], "q": fit["q"]}


def _ma_fit(body: Mapping[str, Any]) -> Dict[str, Any]:
    data = _vector(body, "data")
    order = int(body.get("order", body.get("q", 1)))
    if not 1 <= order <= min(10, len(data) // 3):
        raise ValueError("MA order is outside local limits")
    mean = statistics.mean(data)
    centered = [value - mean for value in data]
    variance = sum(value * value for value in centered) / len(centered)
    coefficients = []
    for lag in range(1, order + 1):
        correlation = sum(centered[index] * centered[index - lag] for index in range(lag, len(centered))) / (
            max(1, len(centered) - lag) * max(variance, 1e-300)
        )
        correlation = max(-0.499999, min(0.499999, correlation))
        coefficient = 0.0 if abs(correlation) < 1e-12 else (
            1 - math.sqrt(max(0.0, 1 - 4 * correlation * correlation))
        ) / (2 * correlation)
        coefficients.append(coefficient)
    residuals = [centered[0]]
    for index in range(1, len(centered)):
        adjustment = sum(coefficients[lag - 1] * residuals[index - lag]
                         for lag in range(1, min(order, index) + 1))
        residuals.append(centered[index] - adjustment)
    return {"mean": mean, "coefficients": coefficients, "residuals": residuals,
            "innovation_variance": statistics.pvariance(residuals), "order": order}


def _garch_core(body: Mapping[str, Any], model: str) -> Dict[str, Any]:
    returns = _vector(body, "returns")
    if len(returns) < 10:
        raise ValueError("returns must contain at least ten observations")
    mean = statistics.mean(returns)
    residuals = [value - mean for value in returns]
    unconditional = max(statistics.pvariance(residuals), 1e-12)
    if model == "garch":
        alpha, beta, gamma = float(body.get("alpha", 0.10)), float(body.get("beta", 0.85)), 0.0
    elif model == "gjr-garch":
        alpha, beta, gamma = float(body.get("alpha", 0.05)), float(body.get("beta", 0.85)), float(body.get("gamma", 0.10))
    else:
        alpha, beta, gamma = float(body.get("alpha", 0.10)), float(body.get("beta", 0.95)), float(body.get("gamma", -0.05))
    conditional = [unconditional]
    if model == "egarch":
        omega = (1 - beta) * math.log(unconditional)
        for residual in residuals[:-1]:
            previous = conditional[-1]
            standardized = residual / math.sqrt(previous)
            log_variance = omega + beta * math.log(previous)
            log_variance += alpha * (abs(standardized) - math.sqrt(2 / math.pi)) + gamma * standardized
            conditional.append(max(1e-12, math.exp(log_variance)))
    else:
        persistence = alpha + beta + (0.5 * gamma if model == "gjr-garch" else 0.0)
        if not 0 <= persistence < 1:
            raise ValueError("GARCH parameters must be covariance-stationary")
        omega = (1 - persistence) * unconditional
        for residual in residuals[:-1]:
            leverage = gamma * residual * residual if model == "gjr-garch" and residual < 0 else 0.0
            conditional.append(max(1e-12, omega + alpha * residual * residual + leverage + beta * conditional[-1]))
    log_likelihood = -0.5 * sum(math.log(2 * math.pi * variance) + residual * residual / variance
                                for residual, variance in zip(residuals, conditional))
    return {"model": model, "mean": mean, "omega": omega, "alpha": alpha, "beta": beta,
            "gamma": gamma, "conditional_variance": conditional,
            "conditional_volatility": [math.sqrt(value) for value in conditional],
            "residuals": residuals, "log_likelihood": log_likelihood}


def _garch_forecast(body: Mapping[str, Any]) -> Dict[str, Any]:
    fit = _garch_core(body, "garch")
    steps = int(body.get("steps", body.get("n_forecast", 5)))
    if not 1 <= steps <= 10000:
        raise ValueError("forecast steps are outside local limits")
    last_residual = fit["residuals"][-1]
    variance = fit["conditional_variance"][-1]
    forecast = []
    for index in range(steps):
        previous_variance = variance
        innovation = last_residual * last_residual if index == 0 else previous_variance
        variance = fit["omega"] + fit["alpha"] * innovation + fit["beta"] * previous_variance
        forecast.append(max(1e-12, variance))
    return {"variance_forecast": forecast, "volatility_forecast": [math.sqrt(value) for value in forecast]}


def _feature_matrix(body: Mapping[str, Any], name: str = "X") -> list[list[float]]:
    rows = [[float(value) for value in row] for row in body.get(name, [])]
    if not rows or not rows[0] or any(len(row) != len(rows[0]) for row in rows):
        raise ValueError(f"{name} must be a non-empty rectangular matrix")
    if any(not math.isfinite(value) for row in rows for value in row):
        raise ValueError(f"{name} must contain finite values")
    return rows


def _supervised_data(body: Mapping[str, Any], target: str = "y") -> tuple[list[list[float]], list[float]]:
    features, values = _feature_matrix(body), _vector(body, target)
    if len(features) != len(values):
        raise ValueError(f"X and {target} must contain the same number of observations")
    return features, values


def _sigmoid(value: float) -> float:
    if value >= 0:
        exponential = math.exp(-value)
        return 1 / (1 + exponential)
    exponential = math.exp(value)
    return exponential / (1 + exponential)


def _logistic_fit(body: Mapping[str, Any]) -> Dict[str, Any]:
    features, target = _supervised_data(body)
    if any(value not in {0, 1} for value in target) or len(set(target)) < 2:
        raise ValueError("logistic target must contain both 0 and 1")
    width = len(features[0]); coefficients = [0.0] * (width + 1)
    learning_rate = float(body.get("learning_rate", 0.05))
    raw_iterations = float(body.get("iterations", 2000))
    iterations = int(raw_iterations)
    if raw_iterations != iterations or not 1 <= iterations <= 10000:
        raise ValueError("iterations are outside local limits")
    regularization = float(body.get("regularization", 1e-4))
    for _ in range(iterations):
        gradients = [0.0] * len(coefficients)
        for row, actual in zip(features, target):
            probability = _sigmoid(coefficients[0] + sum(value * weight for value, weight in zip(row, coefficients[1:])))
            error = probability - actual
            gradients[0] += error
            for index, value in enumerate(row, 1):
                gradients[index] += error * value
        scale = 1 / len(features)
        coefficients[0] -= learning_rate * gradients[0] * scale
        for index in range(1, len(coefficients)):
            coefficients[index] -= learning_rate * (gradients[index] * scale + regularization * coefficients[index])
    probabilities = [_sigmoid(coefficients[0] + sum(value * weight for value, weight in zip(row, coefficients[1:])))
                     for row in features]
    epsilon = 1e-15
    log_loss = -sum(actual * math.log(max(epsilon, probability)) +
                    (1 - actual) * math.log(max(epsilon, 1 - probability))
                    for actual, probability in zip(target, probabilities)) / len(target)
    return {"intercept": coefficients[0], "coefficients": coefficients[1:],
            "probabilities": probabilities, "predictions": [int(value >= 0.5) for value in probabilities],
            "log_loss": log_loss, "iterations": iterations}


def _credit_scorecard(body: Mapping[str, Any]) -> Dict[str, Any]:
    fitted = _logistic_fit(body)
    base_score = float(body.get("base_score", 600)); points_to_double_odds = float(body.get("pdo", 20))
    factor = points_to_double_odds / math.log(2)
    scores = [base_score - factor * math.log(max(1e-12, probability) / max(1e-12, 1 - probability))
              for probability in fitted["probabilities"]]
    fitted.update({"scores": scores, "base_score": base_score, "points_to_double_odds": points_to_double_odds})
    return fitted


def _quantile_value(values: Sequence[float], probability: float) -> float:
    ordered = sorted(values)
    position = max(0.0, min(len(ordered) - 1.0, probability * (len(ordered) - 1)))
    lower, upper = math.floor(position), math.ceil(position)
    return ordered[lower] if lower == upper else ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def _woe_binning(body: Mapping[str, Any]) -> Dict[str, Any]:
    feature, target = _vector(body, "feature"), _vector(body, "target")
    if len(feature) != len(target) or any(value not in {0, 1} for value in target):
        raise ValueError("feature and binary target must align")
    bins = int(body.get("n_bins", 5)); edges = sorted(set(_quantile_value(feature, index / bins) for index in range(bins + 1)))
    total_good, total_bad = target.count(0.0), target.count(1.0)
    output, information_value = [], 0.0
    for index in range(len(edges) - 1):
        members = [position for position, value in enumerate(feature)
                   if edges[index] <= value <= edges[index + 1] and (index == len(edges) - 2 or value < edges[index + 1])]
        good = sum(target[position] == 0 for position in members); bad = len(members) - good
        good_rate = (good + 0.5) / (total_good + 0.5 * (len(edges) - 1))
        bad_rate = (bad + 0.5) / (total_bad + 0.5 * (len(edges) - 1))
        woe = math.log(good_rate / bad_rate); iv = (good_rate - bad_rate) * woe
        information_value += iv
        output.append({"lower": edges[index], "upper": edges[index + 1], "count": len(members),
                       "good": good, "bad": bad, "woe": woe, "information_value": iv})
    return {"bins": output, "information_value": information_value, "edges": edges}


def _binary_scores(body: Mapping[str, Any]) -> tuple[list[int], list[float]]:
    actual = [int(value) for value in _vector(body, "y_true")]
    scores = _vector(body, "y_score")
    if len(actual) != len(scores) or any(value not in {0, 1} for value in actual) or any(not 0 <= value <= 1 for value in scores):
        raise ValueError("y_true and y_score must be aligned binary labels and probabilities")
    return actual, scores


def _auc_score(actual: Sequence[int], scores: Sequence[float]) -> float:
    positive = [score for label, score in zip(actual, scores) if label == 1]
    negative = [score for label, score in zip(actual, scores) if label == 0]
    if not positive or not negative:
        raise ValueError("both classes are required")
    concordance = sum(1.0 if left > right else 0.5 if left == right else 0.0
                      for left in positive for right in negative)
    return concordance / (len(positive) * len(negative))


def _calibration_metrics(body: Mapping[str, Any]) -> Dict[str, Any]:
    actual, scores = _binary_scores(body); bins = int(body.get("n_bins", 10))
    epsilon = 1e-15
    brier = statistics.mean((label - score) ** 2 for label, score in zip(actual, scores))
    log_loss = -statistics.mean(label * math.log(max(epsilon, score)) +
                                (1 - label) * math.log(max(epsilon, 1 - score))
                                for label, score in zip(actual, scores))
    table = []
    for index in range(bins):
        members = [(label, score) for label, score in zip(actual, scores)
                   if index / bins <= score < (index + 1) / bins or (index == bins - 1 and score == 1)]
        if members:
            table.append({"lower": index / bins, "upper": (index + 1) / bins, "count": len(members),
                          "mean_predicted": statistics.mean(score for _, score in members),
                          "observed_rate": statistics.mean(label for label, _ in members)})
    calibration_error = sum(row["count"] / len(actual) * abs(row["mean_predicted"] - row["observed_rate"])
                            for row in table)
    return {"brier_score": brier, "log_loss": log_loss, "calibration_error": calibration_error,
            "calibration_table": table}


def _discrimination_metrics(body: Mapping[str, Any]) -> Dict[str, Any]:
    actual, scores = _binary_scores(body); auc = _auc_score(actual, scores)
    thresholds = sorted(set(scores), reverse=True); ks = 0.0
    positive, negative = sum(actual), len(actual) - sum(actual)
    for threshold in thresholds:
        tpr = sum(label == 1 and score >= threshold for label, score in zip(actual, scores)) / positive
        fpr = sum(label == 0 and score >= threshold for label, score in zip(actual, scores)) / negative
        ks = max(ks, abs(tpr - fpr))
    return {"auc": auc, "gini": 2 * auc - 1, "ks_statistic": ks}


def _credit_performance(body: Mapping[str, Any]) -> Dict[str, Any]:
    actual, scores = _binary_scores(body); threshold = float(body.get("threshold", 0.5))
    predicted = [int(score >= threshold) for score in scores]
    metrics = _classification_metrics({"y_true": actual, "y_pred": predicted})
    metrics.update(_discrimination_metrics(body)); metrics.update(_calibration_metrics(body))
    return metrics


def _credit_migration(body: Mapping[str, Any]) -> Dict[str, Any]:
    transition = _matrix(body, "transitions"); normalized = []
    for row in transition:
        if any(value < 0 for value in row) or sum(row) <= 0:
            raise ValueError("migration rows must contain non-negative mass")
        normalized.append([value / sum(row) for value in row])
    stationary = [1 / len(normalized)] * len(normalized)
    for _ in range(10000):
        following = [sum(stationary[row] * normalized[row][column] for row in range(len(normalized)))
                     for column in range(len(normalized))]
        if max(abs(left - right) for left, right in zip(stationary, following)) < 1e-14:
            stationary = following; break
        stationary = following
    return {"transition_matrix": normalized, "stationary_distribution": stationary,
            "default_probabilities": [row[-1] for row in normalized]}


def _linear_regression(body: Mapping[str, Any], *, bounded: bool = False,
                       target_name: str = "y") -> Dict[str, Any]:
    features, target = _supervised_data(body, target_name)
    design = [[1.0] + row for row in features]
    coefficients = _least_squares_coefficients(design, target)
    raw = [sum(value * weight for value, weight in zip(row, coefficients)) for row in design]
    predictions = [min(1.0, max(0.0, value)) for value in raw] if bounded else raw
    residuals = [actual - estimate for actual, estimate in zip(target, predictions)]
    mean = statistics.mean(target); total = sum((value - mean) ** 2 for value in target)
    residual_sum = sum(value * value for value in residuals)
    return {"intercept": coefficients[0], "coefficients": coefficients[1:], "predictions": predictions,
            "residuals": residuals, "mse": residual_sum / len(target),
            "r2": 1 - residual_sum / total if total > 0 else 1.0}


def _beta_lgd(body: Mapping[str, Any], target_name: str = "lgd") -> Dict[str, Any]:
    features, target = _supervised_data(body, target_name)
    epsilon = 1e-5
    transformed = [math.log(min(1 - epsilon, max(epsilon, value)) /
                            (1 - min(1 - epsilon, max(epsilon, value)))) for value in target]
    adapted = dict(body); adapted["y"] = transformed
    fit = _linear_regression(adapted)
    logits = fit["predictions"]; predictions = [_sigmoid(value) for value in logits]
    fit.update({"predictions": predictions, "lgd_predictions": predictions,
                "mse": statistics.mean((actual - estimate) ** 2 for actual, estimate in zip(target, predictions))})
    return fit


def _two_stage_lgd(body: Mapping[str, Any]) -> Dict[str, Any]:
    features, lgd = _supervised_data(body, "lgd")
    cure = [int(value <= 0) for value in lgd]
    stage_one_body = dict(body); stage_one_body["y"] = cure
    if len(set(cure)) < 2:
        cure_probabilities = [float(cure[0])] * len(cure); stage_one = {"coefficients": [], "intercept": 0.0}
    else:
        stage_one = _logistic_fit(stage_one_body); cure_probabilities = stage_one["probabilities"]
    positive_indices = [index for index, value in enumerate(lgd) if value > 0]
    positive_body = {"X": [features[index] for index in positive_indices], "lgd": [lgd[index] for index in positive_indices]}
    stage_two = _beta_lgd(positive_body)
    severity = [_sigmoid(stage_two["intercept"] + sum(value * weight for value, weight in zip(row, stage_two["coefficients"])))
                for row in features]
    predictions = [(1 - probability) * loss for probability, loss in zip(cure_probabilities, severity)]
    return {"cure_probabilities": cure_probabilities, "conditional_lgd": severity,
            "lgd_predictions": predictions, "stage_one": stage_one, "stage_two": stage_two}


def _regression_stump(body: Mapping[str, Any]) -> Dict[str, Any]:
    features, target = _supervised_data(body)
    best = None
    for column in range(len(features[0])):
        values = sorted(set(row[column] for row in features))
        for threshold in [(left + right) / 2 for left, right in zip(values, values[1:])]:
            left_values = [target[index] for index, row in enumerate(features) if row[column] <= threshold]
            right_values = [target[index] for index, row in enumerate(features) if row[column] > threshold]
            if not left_values or not right_values: continue
            left_mean, right_mean = statistics.mean(left_values), statistics.mean(right_values)
            error = sum((target[index] - (left_mean if row[column] <= threshold else right_mean)) ** 2
                        for index, row in enumerate(features))
            if best is None or error < best[0]: best = (error, column, threshold, left_mean, right_mean)
    if best is None:
        mean = statistics.mean(target); return {"feature": 0, "threshold": features[0][0],
                                                "left_value": mean, "right_value": mean,
                                                "predictions": [mean] * len(target), "mse": statistics.pvariance(target)}
    error, column, threshold, left_mean, right_mean = best
    predictions = [left_mean if row[column] <= threshold else right_mean for row in features]
    return {"feature": column, "threshold": threshold, "left_value": left_mean, "right_value": right_mean,
            "predictions": predictions, "mse": error / len(target)}


def _regression_ensemble(body: Mapping[str, Any]) -> Dict[str, Any]:
    linear, tree = _linear_regression(body), _regression_stump(body)
    target = _vector(body, "y")
    predictions = [(left + right) / 2 for left, right in zip(linear["predictions"], tree["predictions"])]
    return {"predictions": predictions,
            "mse": statistics.mean((actual - estimate) ** 2 for actual, estimate in zip(target, predictions)),
            "components": {"linear": linear, "tree": tree}}


def _squared_distance(left: Sequence[float], right: Sequence[float]) -> float:
    return sum((a - b) ** 2 for a, b in zip(left, right))


def _kmeans(body: Mapping[str, Any]) -> Dict[str, Any]:
    features = _feature_matrix(body); clusters = int(body.get("n_clusters", body.get("k", 2)))
    if not 1 <= clusters <= len(features): raise ValueError("n_clusters is outside valid range")
    centroids = [list(features[round(index * (len(features) - 1) / max(1, clusters - 1))]) for index in range(clusters)]
    labels = [0] * len(features)
    for _ in range(100):
        following = [min(range(clusters), key=lambda index: _squared_distance(row, centroids[index])) for row in features]
        if following == labels and _ > 0: break
        labels = following
        for cluster in range(clusters):
            members = [row for row, label in zip(features, labels) if label == cluster]
            if members: centroids[cluster] = [statistics.mean(row[column] for row in members) for column in range(len(features[0]))]
    inertia = sum(_squared_distance(row, centroids[label]) for row, label in zip(features, labels))
    return {"labels": labels, "centroids": centroids, "inertia": inertia, "n_clusters": clusters}


def _hierarchical_clustering(body: Mapping[str, Any]) -> Dict[str, Any]:
    features = _feature_matrix(body); target = int(body.get("n_clusters", 2))
    clusters = [[index] for index in range(len(features))]; linkage = []
    while len(clusters) > target:
        pair = min(((left, right) for left in range(len(clusters)) for right in range(left + 1, len(clusters))),
                   key=lambda item: min(_squared_distance(features[a], features[b])
                                        for a in clusters[item[0]] for b in clusters[item[1]]))
        left, right = pair; distance = min(_squared_distance(features[a], features[b]) ** 0.5
                                           for a in clusters[left] for b in clusters[right])
        linkage.append([clusters[left][0], clusters[right][0], distance, len(clusters[left]) + len(clusters[right])])
        clusters[left] = clusters[left] + clusters[right]; del clusters[right]
    labels = [0] * len(features)
    for label, members in enumerate(clusters):
        for member in members: labels[member] = label
    return {"labels": labels, "clusters": clusters, "linkage": linkage}


def _dbscan(body: Mapping[str, Any]) -> Dict[str, Any]:
    features = _feature_matrix(body); epsilon = float(body.get("eps", 1.5)); minimum = int(body.get("min_samples", 2))
    labels = [-99] * len(features); cluster = 0
    neighbors = [[other for other in range(len(features)) if _squared_distance(row, features[other]) <= epsilon * epsilon]
                 for row in features]
    for index in range(len(features)):
        if labels[index] != -99: continue
        if len(neighbors[index]) < minimum: labels[index] = -1; continue
        labels[index] = cluster; queue = list(neighbors[index]); cursor = 0
        while cursor < len(queue):
            point = queue[cursor]; cursor += 1
            if labels[point] == -1: labels[point] = cluster
            if labels[point] != -99: continue
            labels[point] = cluster
            if len(neighbors[point]) >= minimum:
                for neighbor in neighbors[point]:
                    if neighbor not in queue: queue.append(neighbor)
        cluster += 1
    return {"labels": labels, "n_clusters": cluster, "noise_points": sum(value == -1 for value in labels)}


def _power_eigenvector(matrix: Sequence[Sequence[float]], seed: int) -> tuple[float, list[float]]:
    generator = random.Random(seed); vector = [generator.random() + 0.1 for _ in matrix]
    norm = math.sqrt(sum(value * value for value in vector)); vector = [value / norm for value in vector]
    for _ in range(1000):
        following = _matvec(matrix, vector); norm = math.sqrt(sum(value * value for value in following))
        if norm <= 1e-15: break
        following = [value / norm for value in following]
        if max(abs(abs(left) - abs(right)) for left, right in zip(vector, following)) < 1e-12:
            vector = following; break
        vector = following
    eigenvalue = sum(vector[row] * sum(matrix[row][column] * vector[column] for column in range(len(vector)))
                     for row in range(len(vector)))
    return eigenvalue, vector


def _pca(body: Mapping[str, Any]) -> Dict[str, Any]:
    features = _feature_matrix(body); components = int(body.get("n_components", min(2, len(features[0]))))
    means = [statistics.mean(row[column] for row in features) for column in range(len(features[0]))]
    centered = [[value - means[column] for column, value in enumerate(row)] for row in features]
    covariance = [[sum(row[left] * row[right] for row in centered) / max(1, len(centered) - 1)
                   for right in range(len(means))] for left in range(len(means))]
    working = [list(row) for row in covariance]; vectors, values = [], []
    for index in range(components):
        value, vector = _power_eigenvector(working, 42 + index); values.append(max(0.0, value)); vectors.append(vector)
        working = [[working[row][column] - value * vector[row] * vector[column]
                    for column in range(len(vector))] for row in range(len(vector))]
    transformed = [[sum(row[column] * vector[column] for column in range(len(means))) for vector in vectors]
                   for row in centered]
    total = sum(covariance[index][index] for index in range(len(covariance)))
    return {"components": vectors, "transformed": transformed, "explained_variance": values,
            "explained_variance_ratio": [value / total if total > 0 else 0 for value in values], "mean": means}


def _isolation_scores(body: Mapping[str, Any]) -> Dict[str, Any]:
    features = _feature_matrix(body)
    means = [statistics.mean(row[column] for row in features) for column in range(len(features[0]))]
    deviations = [statistics.pstdev(row[column] for row in features) or 1.0 for column in range(len(features[0]))]
    distances = [math.sqrt(sum(((value - means[column]) / deviations[column]) ** 2
                               for column, value in enumerate(row))) for row in features]
    scale = max(distances) or 1.0; scores = [value / scale for value in distances]
    contamination = float(body.get("contamination", 0.1)); threshold = _quantile_value(scores, 1 - contamination)
    return {"scores": scores, "labels": [-1 if value >= threshold else 1 for value in scores], "threshold": threshold}


def _scale_features(body: Mapping[str, Any]) -> Dict[str, Any]:
    features = _feature_matrix(body); means = [statistics.mean(row[column] for row in features) for column in range(len(features[0]))]
    deviations = [statistics.pstdev(row[column] for row in features) for column in range(len(features[0]))]
    scaled = [[(value - means[column]) / deviations[column] if deviations[column] else 0.0
               for column, value in enumerate(row)] for row in features]
    return {"scaled": scaled, "means": means, "standard_deviations": deviations}


def _transform_features(body: Mapping[str, Any]) -> Dict[str, Any]:
    features = _feature_matrix(body); method = str(body.get("method", "signed-log1p")).lower()
    if method in {"signed-log1p", "log1p"}:
        transformed = [[math.copysign(math.log1p(abs(value)), value) for value in row] for row in features]
    elif method == "sqrt":
        transformed = [[math.copysign(math.sqrt(abs(value)), value) for value in row] for row in features]
    else: raise ValueError("method must be signed-log1p or sqrt")
    return {"transformed": transformed, "method": method}


def _outlier_detection(body: Mapping[str, Any]) -> Dict[str, Any]:
    scaled = _scale_features(body)["scaled"]; threshold = float(body.get("threshold", 3.0))
    scores = [max(abs(value) for value in row) for row in scaled]
    return {"scores": scores, "outlier_indices": [index for index, value in enumerate(scores) if value > threshold],
            "labels": [int(value > threshold) for value in scores], "threshold": threshold}


def _winsorize(body: Mapping[str, Any]) -> Dict[str, Any]:
    data = _vector(body, "data"); lower = float(body.get("lower", 0.05)); upper = float(body.get("upper", 0.95))
    if not 0 <= lower < upper <= 1: raise ValueError("winsor limits must satisfy 0 <= lower < upper <= 1")
    low, high = _quantile_value(data, lower), _quantile_value(data, upper)
    return {"data": [min(high, max(low, value)) for value in data], "lower_bound": low, "upper_bound": high}


def _stationarity(body: Mapping[str, Any]) -> Dict[str, Any]:
    data = _vector(body, "data")
    if len(data) < 10: raise ValueError("stationarity needs at least ten observations")
    mean = statistics.mean(data); denominator = sum((value - mean) ** 2 for value in data[:-1])
    autocorrelation = (sum((data[index] - mean) * (data[index - 1] - mean) for index in range(1, len(data))) /
                       denominator if denominator else 0.0)
    half = len(data) // 2; drift = abs(statistics.mean(data[:half]) - statistics.mean(data[half:]))
    scale = statistics.pstdev(data) or 1.0
    return {"stationary": abs(autocorrelation) < 0.9 and drift < scale,
            "lag1_autocorrelation": autocorrelation, "normalized_mean_drift": drift / scale}


def _lag_features(body: Mapping[str, Any]) -> Dict[str, Any]:
    data = _vector(body, "data"); lags = body.get("lags", [1, 2, 3])
    lags = [int(value) for value in lags] if isinstance(lags, list) else list(range(1, int(lags) + 1))
    if not lags or min(lags) < 1 or max(lags) >= len(data): raise ValueError("lags are invalid")
    maximum = max(lags)
    return {"features": [[data[index - lag] for lag in lags] for index in range(maximum, len(data))],
            "target": data[maximum:], "lags": lags}


def _rolling_features(body: Mapping[str, Any]) -> Dict[str, Any]:
    data = _vector(body, "data"); window = int(body.get("window", 5))
    if not 2 <= window <= len(data): raise ValueError("window is invalid")
    rows = []
    for index in range(window - 1, len(data)):
        sample = data[index - window + 1:index + 1]
        rows.append({"index": index, "mean": statistics.mean(sample), "std": statistics.pstdev(sample),
                     "minimum": min(sample), "maximum": max(sample), "momentum": sample[-1] - sample[0]})
    return {"features": rows, "window": window}


def _calendar_features(body: Mapping[str, Any]) -> Dict[str, Any]:
    dates = []
    for raw in body.get("dates", []):
        try: value = date.fromisoformat(str(raw))
        except ValueError as exc: raise ValueError("dates must be ISO dates") from exc
        dates.append({"date": value.isoformat(), "year": value.year, "month": value.month, "quarter": (value.month - 1) // 3 + 1,
                      "day": value.day, "weekday": value.weekday(), "day_of_year": value.timetuple().tm_yday,
                      "month_end": (value + timedelta(days=1)).month != value.month})
    if not dates: raise ValueError("dates must not be empty")
    return {"features": dates}


def _technical_features(body: Mapping[str, Any]) -> Dict[str, Any]:
    prices = _positive_vector(body, "prices"); indicator = str(body.get("indicator", "rsi")).lower()
    window = int(body.get("window", 14))
    if indicator == "rsi":
        values = [None] * len(prices)
        for index in range(window, len(prices)):
            changes = [prices[position] - prices[position - 1] for position in range(index - window + 1, index + 1)]
            gain = sum(max(0.0, value) for value in changes) / window
            loss = sum(max(0.0, -value) for value in changes) / window
            values[index] = 100.0 if loss == 0 else 100 - 100 / (1 + gain / loss)
    elif indicator in {"returns", "momentum"}:
        values = [None] + [prices[index] / prices[index - 1] - 1 for index in range(1, len(prices))]
    else: raise ValueError("indicator must be rsi, returns, or momentum")
    return {"indicator": indicator, "values": values, "window": window}


def _financial_ratio_features(body: Mapping[str, Any]) -> Dict[str, Any]:
    raw = body.get("data", {})
    if not isinstance(raw, Mapping) or not raw: raise ValueError("data must be a mapping of financial series")
    series = {name: [float(value) for value in values] for name, values in raw.items()}
    lengths = {len(values) for values in series.values()}
    if len(lengths) != 1 or not lengths or 0 in lengths: raise ValueError("financial series must have equal non-zero length")
    features: Dict[str, Any] = {"growth": {name: [None] + [values[index] / values[index - 1] - 1 if values[index - 1] else None
                                                           for index in range(1, len(values))] for name, values in series.items()}}
    if "revenue" in series and "net_income" in series:
        features["net_margin"] = [income / revenue if revenue else None
                                  for income, revenue in zip(series["net_income"], series["revenue"])]
    return features


def _cross_sectional_features(body: Mapping[str, Any]) -> Dict[str, Any]:
    data = _vector(body, "data"); mean = statistics.mean(data); deviation = statistics.pstdev(data) or 1.0
    order = sorted(range(len(data)), key=data.__getitem__); ranks = [0.0] * len(data)
    for rank, index in enumerate(order): ranks[index] = rank / max(1, len(data) - 1)
    return {"z_scores": [(value - mean) / deviation for value in data], "percentile_ranks": ranks,
            "demeaned": [value - mean for value in data]}


def _population_stability(body: Mapping[str, Any]) -> Dict[str, Any]:
    expected, actual = _positive_vector(body, "expected"), _positive_vector(body, "actual")
    if len(expected) != len(actual): raise ValueError("expected and actual must align")
    expected_total, actual_total = sum(expected), sum(actual); contributions = []
    for left, right in zip(expected, actual):
        expected_share, actual_share = left / expected_total, right / actual_total
        value = (actual_share - expected_share) * math.log(actual_share / expected_share)
        contributions.append(value)
    score = sum(contributions)
    return {"psi": score, "contributions": contributions,
            "stability": "stable" if score < 0.1 else "moderate_shift" if score < 0.25 else "significant_shift"}


def _calibration_report(body: Mapping[str, Any]) -> Dict[str, Any]:
    result = _calibration_metrics(body); result["well_calibrated"] = result["calibration_error"] < 0.1
    return result


def _interpretability(body: Mapping[str, Any]) -> Dict[str, Any]:
    adapted = dict(body); fit = _linear_regression(adapted)
    magnitudes = [abs(value) for value in fit["coefficients"]]; total = sum(magnitudes) or 1.0
    return {"intercept": fit["intercept"], "coefficients": fit["coefficients"],
            "feature_importance": [value / total for value in magnitudes], "r2": fit["r2"],
            "model": str(body.get("model_predict", "linear"))}


def _timeseries_feature_importance(body: Mapping[str, Any]) -> Dict[str, Any]:
    fit = _linear_regression(body); magnitudes = [abs(value) for value in fit["coefficients"]]
    total = sum(magnitudes) or 1.0
    return {"importance": [value / total for value in magnitudes], "coefficients": fit["coefficients"],
            "n_splits": int(body.get("n_splits", 3)), "score": fit["r2"]}


def _changepoint(body: Mapping[str, Any]) -> Dict[str, Any]:
    data = _vector(body, "data"); minimum = int(body.get("min_size", max(3, len(data) // 10)))
    if len(data) < 2 * minimum: raise ValueError("data is too short for changepoint detection")
    global_variance = statistics.pvariance(data) or 1e-12; candidates = []
    for index in range(minimum, len(data) - minimum + 1):
        left, right = data[:index], data[index:]
        score = (statistics.mean(left) - statistics.mean(right)) ** 2 * len(left) * len(right) / len(data) / global_variance
        candidates.append((score, index))
    score, index = max(candidates)
    threshold = float(body.get("threshold", 4.0))
    return {"changepoints": [index] if score >= threshold else [], "best_index": index,
            "score": score, "threshold": threshold}


def _curve_surrogate(body: Mapping[str, Any], engine: str) -> Dict[str, Any]:
    tenors, rates, queries = _vector(body, "tenors"), _vector(body, "rates"), _vector(body, "query_tenors")
    if len(tenors) != len(rates): raise ValueError("tenors and rates must align")
    predictions = [_linear(tenors, rates, value) for value in queries]
    nearest = [min(abs(value - tenor) for tenor in tenors) for value in queries]
    return {"query_tenors": queries, "predictions": predictions,
            "uncertainty": [distance * statistics.pstdev(rates) for distance in nearest], "engine": engine}


def _bilinear_surface(strikes: Sequence[float], expiries: Sequence[float], values: Sequence[Sequence[float]],
                      strike: float, expiry: float) -> float:
    if len(values) != len(expiries) or any(len(row) != len(strikes) for row in values):
        raise ValueError("vols dimensions must match expiries x strikes")
    interpolated_strikes = [_linear(strikes, row, strike) for row in values]
    return _linear(expiries, interpolated_strikes, expiry)


def _surface_surrogate(body: Mapping[str, Any], engine: str) -> Dict[str, Any]:
    strikes, expiries = _vector(body, "strikes"), _vector(body, "expiries")
    values = [[float(value) for value in row] for row in body.get("vols", [])]
    query_strikes, query_expiries = _vector(body, "query_strikes"), _vector(body, "query_expiries")
    grid = [[_bilinear_surface(strikes, expiries, values, strike, expiry) for strike in query_strikes]
            for expiry in query_expiries]
    return {"query_strikes": query_strikes, "query_expiries": query_expiries,
            "predicted_vols": grid, "engine": engine}


def _ml_portfolio(body: Mapping[str, Any]) -> Dict[str, Any]:
    observations = _feature_matrix(body, "returns"); width = len(observations[0])
    means = [statistics.mean(row[column] for row in observations) for column in range(width)]
    covariance = _sample_covariance(observations)
    inverse_volatility = [1 / math.sqrt(max(covariance[index][index], 1e-12)) for index in range(width)]
    total = sum(inverse_volatility); weights = [value / total for value in inverse_volatility]
    variance = sum(weights[left] * covariance[left][right] * weights[right]
                   for left in range(width) for right in range(width))
    return {"weights": weights, "expected_return": sum(weight * value for weight, value in zip(weights, means)),
            "volatility": math.sqrt(max(0.0, variance)), "method": "inverse-volatility"}


def _hmm_fit(body: Mapping[str, Any]) -> Dict[str, Any]:
    returns = _vector(body, "returns"); states = int(body.get("n_states", 2))
    if states < 2 or states > min(6, len(returns)): raise ValueError("n_states is outside local limits")
    centers = [_quantile_value(returns, (index + 0.5) / states) for index in range(states)]
    labels = [0] * len(returns)
    for _ in range(100):
        following = [min(range(states), key=lambda index: abs(value - centers[index])) for value in returns]
        if following == labels and _ > 0: break
        labels = following
        for state in range(states):
            members = [value for value, label in zip(returns, labels) if label == state]
            if members: centers[state] = statistics.mean(members)
    order = sorted(range(states), key=centers.__getitem__); remap = {old: new for new, old in enumerate(order)}
    labels = [remap[label] for label in labels]; centers = [centers[index] for index in order]
    variances = [statistics.pvariance([value for value, label in zip(returns, labels) if label == state])
                 if sum(label == state for label in labels) > 1 else 0.0 for state in range(states)]
    counts = [[1e-6] * states for _ in range(states)]
    for left, right in zip(labels, labels[1:]): counts[left][right] += 1
    transition = [[value / sum(row) for value in row] for row in counts]
    return {"states": labels, "state_means": centers, "state_variances": variances,
            "transition_matrix": transition, "n_states": states}


def _garch_hybrid(body: Mapping[str, Any]) -> Dict[str, Any]:
    fit = _garch_core(body, "garch"); returns = _vector(body, "returns")
    standardized = [residual / math.sqrt(variance) for residual, variance in zip(fit["residuals"], fit["conditional_variance"])]
    correction = statistics.mean(abs(value) for value in standardized)
    fit.update({"standardized_residuals": standardized, "ml_scale_correction": correction,
                "hybrid_volatility": [value * correction for value in fit["conditional_volatility"]]})
    return fit


def _regression_metrics(body: Mapping[str, Any]) -> Dict[str, float]:
    actual, predicted = _vector(body, "y_true"), _vector(body, "y_pred")
    if len(actual) != len(predicted): raise ValueError("y_true and y_pred must align")
    errors = [left - right for left, right in zip(actual, predicted)]
    mse = statistics.mean(value * value for value in errors); mean = statistics.mean(actual)
    total = sum((value - mean) ** 2 for value in actual)
    return {"mae": statistics.mean(abs(value) for value in errors), "mse": mse, "rmse": math.sqrt(mse),
            "r2": 1 - sum(value * value for value in errors) / total if total else 1.0}


def _classification_metrics(body: Mapping[str, Any]) -> Dict[str, Any]:
    actual = [int(value) for value in _vector(body, "y_true")]; predicted = [int(value) for value in _vector(body, "y_pred")]
    if len(actual) != len(predicted) or any(value not in {0, 1} for value in actual + predicted):
        raise ValueError("classification labels must align and be binary")
    tp = sum(left == right == 1 for left, right in zip(actual, predicted)); tn = sum(left == right == 0 for left, right in zip(actual, predicted))
    fp = sum(left == 0 and right == 1 for left, right in zip(actual, predicted)); fn = sum(left == 1 and right == 0 for left, right in zip(actual, predicted))
    precision = tp / (tp + fp) if tp + fp else 0.0; recall = tp / (tp + fn) if tp + fn else 0.0
    return {"accuracy": (tp + tn) / len(actual), "precision": precision, "recall": recall,
            "f1": 2 * precision * recall / (precision + recall) if precision + recall else 0.0,
            "confusion_matrix": [[tn, fp], [fn, tp]]}


def _sample_covariance(observations: Sequence[Sequence[float]]) -> list[list[float]]:
    if len(observations) < 2 or not observations or not observations[0]: raise ValueError("at least two observations are required")
    width = len(observations[0])
    if any(len(row) != width for row in observations): raise ValueError("observations must be rectangular")
    means = [statistics.mean(row[column] for row in observations) for column in range(width)]
    return [[sum((row[left] - means[left]) * (row[right] - means[right]) for row in observations) / (len(observations) - 1)
             for right in range(width)] for left in range(width)]


def _covariance_estimate(body: Mapping[str, Any]) -> Dict[str, Any]:
    observations = _feature_matrix(body, "returns"); sample = _sample_covariance(observations)
    shrinkage = float(body.get("shrinkage", 0.1)); average_variance = statistics.mean(sample[index][index] for index in range(len(sample)))
    covariance = [[(1 - shrinkage) * sample[row][column] +
                   shrinkage * (average_variance if row == column else 0.0)
                   for column in range(len(sample))] for row in range(len(sample))]
    return {"covariance_matrix": covariance, "sample_covariance": sample, "shrinkage": shrinkage}


def _statistical_factors(body: Mapping[str, Any]) -> Dict[str, Any]:
    observations = _feature_matrix(body, "returns"); adapted = {"X": observations,
                                                                "n_components": int(body.get("n_factors", min(2, len(observations[0]))))}
    result = _pca(adapted)
    return {"factor_returns": result["transformed"], "loadings": result["components"],
            "explained_variance_ratio": result["explained_variance_ratio"]}


def _cross_sectional_factors(body: Mapping[str, Any]) -> Dict[str, Any]:
    observations = _feature_matrix(body, "returns"); characteristics = _feature_matrix(body, "characteristics")
    width = len(observations[0])
    if len(characteristics) != width: raise ValueError("characteristics must contain one row per asset")
    average_returns = [statistics.mean(row[column] for row in observations) for column in range(width)]
    fit = _linear_regression({"X": characteristics, "y": average_returns})
    return {"factor_premia": fit["coefficients"], "alpha": fit["intercept"],
            "predicted_returns": fit["predictions"], "r2": fit["r2"]}


def _sabr(body: Mapping[str, Any]) -> Dict[str, float]:
    forward, strike, expiry = (_positive(body, "forward"), _positive(body, "strike"), _positive(body, "expiry"))
    alpha, beta, rho, nu = (_positive(body, "alpha"), _number(body, "beta"),
                            _number(body, "rho"), _positive(body, "nu"))
    if not 0 <= beta <= 1 or not -1 < rho < 1:
        raise ValueError("SABR beta must be in [0,1] and rho in (-1,1)")
    one_minus = 1 - beta
    correction = ((one_minus * one_minus / 24) * alpha * alpha / (forward ** (2 * one_minus)) +
                  0.25 * rho * beta * nu * alpha / (forward ** one_minus) +
                  (2 - 3 * rho * rho) * nu * nu / 24) * expiry
    if abs(forward - strike) < 1e-12:
        volatility = alpha / (forward ** one_minus) * (1 + correction)
    else:
        log_fk = math.log(forward / strike)
        fk = (forward * strike) ** (one_minus / 2)
        z = nu / alpha * fk * log_fk
        x_z = math.log((math.sqrt(1 - 2 * rho * z + z * z) + z - rho) / (1 - rho))
        denominator = fk * (1 + one_minus * one_minus * log_fk * log_fk / 24 +
                            one_minus ** 4 * log_fk ** 4 / 1920)
        volatility = alpha / denominator * (z / x_z if abs(z) > 1e-14 else 1.0) * (1 + correction)
    return {"implied_volatility": volatility}


def _flat_vol(body: Mapping[str, Any]) -> Dict[str, float]:
    return {"volatility": _positive(body, "volatility")}


def _vol_grid(body: Mapping[str, Any]) -> Dict[str, float]:
    expiries, strikes = _vector(body, "expiries"), _vector(body, "strikes")
    matrix = [[float(value) for value in row] for row in body.get("vol_matrix", [])]
    if len(matrix) != len(expiries) or any(len(row) != len(strikes) for row in matrix):
        raise ValueError("vol_matrix dimensions must match expiries and strikes")
    expiry, strike = _number(body, "query_expiry"), _number(body, "query_strike")
    strike_slice = [_linear(strikes, row, strike) for row in matrix]
    return {"volatility": _linear(expiries, strike_slice, expiry)}


def _vol_term(body: Mapping[str, Any]) -> Dict[str, float]:
    return {"volatility": _linear(_vector(body, "expiries"), _vector(body, "volatilities"),
                                   _number(body, "query_expiry"))}


def _vol_from_points(body: Mapping[str, Any]) -> Dict[str, Any]:
    tenors, strikes, vols = _vector(body, "tenors"), _vector(body, "strikes"), _vector(body, "vols")
    if len(tenors) != len(strikes) or len(strikes) != len(vols):
        raise ValueError("tenors, strikes, and vols must have matching lengths")
    points = sorted(({"expiry": tenor, "strike": strike, "volatility": vol}
                     for tenor, strike, vol in zip(tenors, strikes, vols)),
                    key=lambda point: (point["expiry"], point["strike"]))
    return {"points": points, "expiries": sorted(set(tenors)), "strikes": sorted(set(strikes))}


def _vol_smile(body: Mapping[str, Any]) -> Dict[str, Any]:
    queries = _vector(body, "query_strikes")
    values = []
    for strike in queries:
        result = _vol_grid({**body, "query_strike": strike})
        values.append(result["volatility"])
    return {"strikes": queries, "volatilities": values, "expiry": _number(body, "query_expiry")}


def _total_variance(body: Mapping[str, Any]) -> Dict[str, float]:
    volatility = _vol_grid(body)["volatility"]
    expiry = _positive(body, "query_expiry")
    return {"total_variance": volatility * volatility * expiry, "volatility": volatility, "expiry": expiry}


def _sabr_smile(body: Mapping[str, Any]) -> Dict[str, Any]:
    strikes = _vector(body, "strikes")
    volatilities = [_sabr({**body, "strike": strike})["implied_volatility"] for strike in strikes]
    return {"strikes": strikes, "volatilities": volatilities}


def _sabr_normal_vol(body: Mapping[str, Any]) -> Dict[str, float]:
    lognormal = _sabr(body)["implied_volatility"]
    normal = lognormal * math.sqrt(_positive(body, "forward") * _positive(body, "strike"))
    return {"normal_volatility": normal, "lognormal_volatility": lognormal}


def _sabr_calibrate(body: Mapping[str, Any]) -> Dict[str, Any]:
    forward, expiry = _positive(body, "forward"), _positive(body, "expiry")
    strikes, market = _vector(body, "strikes"), _vector(body, "market_vols")
    if len(strikes) != len(market):
        raise ValueError("strikes and market_vols must match")
    beta = float(body.get("beta", 0.5))
    atm = market[min(range(len(strikes)), key=lambda index: abs(strikes[index] - forward))]
    alpha_center = atm * forward ** (1 - beta)
    best = None
    for alpha_factor in (0.8, 0.9, 1.0, 1.1, 1.2):
        for rho in (-0.75, -0.5, -0.25, 0.0, 0.25, 0.5, 0.75):
            for nu in (0.1, 0.2, 0.4, 0.6, 0.9, 1.2):
                params = {"forward": forward, "expiry": expiry, "alpha": alpha_center * alpha_factor,
                          "beta": beta, "rho": rho, "nu": nu}
                fitted = [_sabr({**params, "strike": strike})["implied_volatility"] for strike in strikes]
                error = sum((actual - target) ** 2 for actual, target in zip(fitted, market)) / len(market)
                if best is None or error < best[0]:
                    best = (error, params, fitted)
    assert best is not None
    return {**best[1], "rmse": math.sqrt(best[0]), "fitted_vols": best[2], "strikes": strikes}


def _sabr_density(body: Mapping[str, Any]) -> Dict[str, Any]:
    strikes = _vector(body, "strikes")
    forward, expiry = _positive(body, "forward"), _positive(body, "expiry")
    densities = []
    for strike in strikes:
        step = max(0.01, strike * 1e-3)
        def call(test_strike: float) -> float:
            volatility = _sabr({**body, "strike": test_strike})["implied_volatility"]
            return _bs_values({"forward": forward, "strike": test_strike,
                               "volatility": volatility, "time_to_maturity": expiry,
                               "risk_free_rate": 0.0, "option_type": "call"}, black76=True)["price"]
        density = max(0.0, (call(strike + step) - 2 * call(strike) + call(strike - step)) / step ** 2)
        densities.append(density)
    return {"strikes": strikes, "densities": densities}


def _sabr_dynamics(body: Mapping[str, Any]) -> Dict[str, float]:
    base = _sabr(body)["implied_volatility"]
    forward = _positive(body, "forward"); step = max(1e-4, forward * 1e-4)
    up = _sabr({**body, "forward": forward + step})["implied_volatility"]
    down = _sabr({**body, "forward": forward - step})["implied_volatility"]
    return {"implied_volatility": base, "forward_sensitivity": (up - down) / (2 * step),
            "backbone": "SABR"}


def _implied_to_local(body: Mapping[str, Any]) -> Dict[str, float]:
    implied = _positive(body, "implied_vol", "implied_volatility")
    return {"local_volatility": implied, "implied_volatility": implied,
            "method": "flat_smile_dupire_limit"}


def _short_rate_parameters(body: Mapping[str, Any]) -> tuple[float, float, float, float]:
    kappa = float(body.get("kappa", body.get("mean_reversion", 0.1)))
    theta = float(body.get("theta", body.get("long_term_mean", 0.05)))
    sigma = float(body.get("sigma", body.get("volatility", 0.01)))
    rate = float(body.get("r0", body.get("short_rate", 0.03)))
    if kappa <= 0 or sigma < 0 or not all(math.isfinite(value) for value in (kappa, theta, sigma, rate)):
        raise ValueError("short-rate parameters must be finite; kappa positive and sigma non-negative")
    return kappa, theta, sigma, rate


def _vasicek_bond_value(body: Mapping[str, Any], maturity: float) -> float:
    kappa, theta, sigma, rate = _short_rate_parameters(body)
    if maturity <= 0:
        raise ValueError("maturity must be positive")
    b_value = (1 - math.exp(-kappa * maturity)) / kappa
    a_value = math.exp((theta - sigma * sigma / (2 * kappa * kappa)) * (b_value - maturity) -
                       sigma * sigma * b_value * b_value / (4 * kappa))
    return a_value * math.exp(-b_value * rate)


def _short_rate_bond_prices(body: Mapping[str, Any]) -> Dict[str, Any]:
    maturities = _vector(body, "maturities")
    prices = [_vasicek_bond_value(body, maturity) for maturity in maturities]
    return {"maturities": maturities, "prices": prices,
            "zero_rates": [-math.log(price) / maturity for price, maturity in zip(prices, maturities)],
            "model": str(body.get("model", "vasicek"))}


def _short_rate_yield_curve(body: Mapping[str, Any]) -> Dict[str, Any]:
    values = _short_rate_bond_prices(body)
    return {"maturities": values["maturities"], "zero_rates": values["zero_rates"],
            "discount_factors": values["prices"], "model": values["model"]}


def _short_rate_bond_option(body: Mapping[str, Any]) -> Dict[str, float]:
    expiry = _positive(body, "T", "expiry")
    bond_maturity = float(body.get("bond_maturity", expiry + 1.0))
    strike = float(body.get("strike", 0.95))
    if bond_maturity <= expiry or strike <= 0:
        raise ValueError("bond_maturity must follow expiry and strike must be positive")
    expiry_bond = _vasicek_bond_value(body, expiry)
    maturity_bond = _vasicek_bond_value(body, bond_maturity)
    forward = maturity_bond / expiry_bond
    kappa, _theta, sigma, _rate = _short_rate_parameters(body)
    b_value = (1 - math.exp(-kappa * (bond_maturity - expiry))) / kappa
    volatility = max(1e-12, sigma * b_value * math.sqrt((1 - math.exp(-2 * kappa * expiry)) / (2 * kappa)))
    d1 = (math.log(forward / strike) + 0.5 * volatility * volatility) / volatility
    d2 = d1 - volatility
    kind = str(body.get("option_type", "call")).lower(); sign = 1 if kind == "call" else -1
    price = expiry_bond * sign * (forward * NORMAL.cdf(sign * d1) - strike * NORMAL.cdf(sign * d2))
    return {"price": price, "forward_bond_price": forward, "bond_volatility": volatility}


def _short_rate_monte_carlo(body: Mapping[str, Any]) -> Dict[str, Any]:
    kappa, theta, sigma, initial = _short_rate_parameters(body)
    horizon = _positive(body, "T"); steps = max(1, min(5000, int(body.get("n_steps", 252))))
    paths = max(1, min(2000, int(body.get("n_paths", 100))))
    dt = horizon / steps; root_dt = math.sqrt(dt); rng = random.Random(int(body.get("seed", 42)))
    terminal = []
    sample_paths = []
    for path_index in range(paths):
        rate = initial; path = [rate]
        for _ in range(steps):
            rate += kappa * (theta - rate) * dt + sigma * root_dt * rng.gauss(0, 1)
            if path_index < 5:
                path.append(rate)
        terminal.append(rate)
        if path_index < 5:
            sample_paths.append(path)
    return {"terminal_rates": terminal, "mean_terminal_rate": statistics.mean(terminal),
            "std_terminal_rate": statistics.pstdev(terminal), "sample_paths": sample_paths,
            "n_paths": paths, "n_steps": steps, "seed": int(body.get("seed", 42))}


def _hull_white_calibrate(body: Mapping[str, Any]) -> Dict[str, Any]:
    tenors, rates = _vector(body, "market_tenors"), _vector(body, "market_rates")
    if len(tenors) != len(rates) or len(rates) < 2:
        raise ValueError("market_tenors and market_rates must match")
    calibration = _solver_vasicek({"times": tenors, "zero_rates": rates}) if len(rates) >= 3 else {
        "mean_reversion": 0.1, "long_term_mean": rates[-1], "volatility": abs(rates[-1] - rates[0])}
    fitted = [_vasicek_bond_value({"kappa": calibration["mean_reversion"],
                                   "theta": calibration["long_term_mean"],
                                   "sigma": max(1e-6, calibration["volatility"]),
                                   "r0": rates[0]}, tenor) for tenor in tenors]
    fitted_rates = [-math.log(price) / tenor for price, tenor in zip(fitted, tenors)]
    rmse = math.sqrt(sum((actual - target) ** 2 for actual, target in zip(fitted_rates, rates)) / len(rates))
    return {"mean_reversion": calibration["mean_reversion"],
            "volatility": max(1e-6, calibration["volatility"]),
            "long_term_mean": calibration["long_term_mean"], "fitted_rates": fitted_rates,
            "rmse": rmse}


def _model_option_body(body: Mapping[str, Any], volatility: float) -> Dict[str, Any]:
    return {"spot": _positive(body, "S0", "S", "spot"),
            "strike": _positive(body, "strike", "K"),
            "risk_free_rate": _number(body, "r", "rate", "risk_free_rate"),
            "volatility": max(1e-9, volatility),
            "time_to_maturity": _positive(body, "T", "time_to_maturity"),
            "option_type": str(body.get("option_type", "call"))}


def _heston_effective_vol(body: Mapping[str, Any]) -> float:
    initial = _positive(body, "v0"); theta = _positive(body, "theta")
    kappa = _positive(body, "kappa"); horizon = _positive(body, "T")
    average_variance = theta + (initial - theta) * (1 - math.exp(-kappa * horizon)) / (kappa * horizon)
    rho = float(body.get("rho", 0.0)); vol_of_vol = float(body.get("sigma_v", 0.0))
    skew = 1 + 0.1 * rho * vol_of_vol * math.log(_positive(body, "strike") / _positive(body, "S0"))
    return math.sqrt(max(1e-12, average_variance)) * max(0.25, skew)


def _heston_price(body: Mapping[str, Any]) -> Dict[str, float]:
    effective = _heston_effective_vol(body)
    result = _bs_values(_model_option_body(body, effective))
    return {"price": result["price"], "effective_volatility": effective,
            "delta": result["delta"], "gamma": result["gamma"]}


def _heston_implied_vol(body: Mapping[str, Any]) -> Dict[str, float]:
    price = _heston_price(body)["price"]
    trial = _model_option_body(body, _heston_effective_vol(body)); trial["market_price"] = price
    return {**_implied_vol(trial), "model_price": price}


def _heston_monte_carlo(body: Mapping[str, Any]) -> Dict[str, float]:
    spot = _positive(body, "S0"); variance = _positive(body, "v0")
    rate = _number(body, "r"); kappa = _positive(body, "kappa"); theta = _positive(body, "theta")
    vol_of_vol = _positive(body, "sigma_v"); rho = _number(body, "rho")
    if not -1 <= rho <= 1:
        raise ValueError("rho must be in [-1,1]")
    strike, horizon = _positive(body, "strike"), _positive(body, "T")
    steps = max(10, min(1000, int(body.get("n_steps", 100))))
    paths = max(100, min(10000, int(body.get("n_paths", 2000))))
    dt = horizon / steps; root_dt = math.sqrt(dt); rng = random.Random(int(body.get("seed", 42)))
    payoff_sum = 0.0; sign = 1 if str(body.get("option_type", "call")) == "call" else -1
    for _ in range(paths):
        current_spot, current_variance = spot, variance
        for _ in range(steps):
            first = rng.gauss(0, 1); second = rho * first + math.sqrt(max(0.0, 1 - rho * rho)) * rng.gauss(0, 1)
            positive_variance = max(0.0, current_variance)
            current_spot *= math.exp((rate - 0.5 * positive_variance) * dt + math.sqrt(positive_variance) * root_dt * first)
            current_variance = max(0.0, current_variance + kappa * (theta - positive_variance) * dt +
                                   vol_of_vol * math.sqrt(positive_variance) * root_dt * second)
        payoff_sum += max(0.0, sign * (current_spot - strike))
    price = math.exp(-rate * horizon) * payoff_sum / paths
    return {"price": price, "standard_model": "full_truncation_euler", "n_paths": paths,
            "n_steps": steps, "seed": int(body.get("seed", 42))}


def _merton_price(body: Mapping[str, Any]) -> Dict[str, float]:
    spot, strike, horizon, rate = (_positive(body, "S"), _positive(body, "K"),
                                   _positive(body, "T"), _number(body, "r"))
    diffusion = _positive(body, "sigma"); intensity = max(0.0, _number(body, "lambda_jump"))
    jump_mean, jump_vol = _number(body, "mu_jump"), max(0.0, _number(body, "sigma_jump"))
    compensator = math.exp(jump_mean + 0.5 * jump_vol ** 2) - 1
    poisson_mean = intensity * horizon; total = 0.0; weight = math.exp(-poisson_mean)
    for jumps in range(80):
        if jumps > 0:
            weight *= poisson_mean / jumps
        variance = diffusion ** 2 + jumps * jump_vol ** 2 / horizon
        adjusted_spot = spot * math.exp(-intensity * compensator * horizon + jumps * jump_mean +
                                        0.5 * jumps * jump_vol ** 2)
        total += weight * _bs_values({"spot": adjusted_spot, "strike": strike,
                                      "risk_free_rate": rate, "volatility": math.sqrt(variance),
                                      "time_to_maturity": horizon,
                                      "option_type": str(body.get("option_type", "call"))})["price"]
        if weight < 1e-14 and jumps > poisson_mean + 10:
            break
    return {"price": total, "terms": jumps + 1}


def _kou_price(body: Mapping[str, Any]) -> Dict[str, float]:
    probability = float(body.get("p", 0.5)); eta1, eta2 = _positive(body, "eta1"), _positive(body, "eta2")
    if not 0 <= probability <= 1 or eta1 <= 1:
        raise ValueError("p must be in [0,1] and eta1 must exceed 1")
    mean = probability / eta1 - (1 - probability) / eta2
    second = 2 * probability / eta1 ** 2 + 2 * (1 - probability) / eta2 ** 2
    equivalent_vol = math.sqrt(max(0.0, second - mean * mean))
    result = _merton_price({"S": _positive(body, "S"), "K": _positive(body, "K"),
                            "T": _positive(body, "T"), "r": _number(body, "r"),
                            "sigma": _positive(body, "sigma"),
                            "lambda_jump": max(0.0, _number(body, "lambda_jump")),
                            "mu_jump": mean, "sigma_jump": equivalent_vol,
                            "option_type": str(body.get("option_type", "call"))})
    return {**result, "equivalent_jump_mean": mean, "equivalent_jump_volatility": equivalent_vol}


def _dupire_price(body: Mapping[str, Any]) -> Dict[str, float]:
    volatility = float(body.get("local_volatility", body.get("volatility", 0.2)))
    result = _bs_values({"spot": _positive(body, "spot"), "strike": _positive(body, "strike"),
                         "risk_free_rate": float(body.get("rate", 0.05)), "volatility": volatility,
                         "time_to_maturity": _positive(body, "T"),
                         "option_type": str(body.get("option_type", "call"))})
    return {"price": result["price"], "local_volatility": volatility, "method": "local_vol_flat_step"}


def _svi_calibrate(body: Mapping[str, Any]) -> Dict[str, Any]:
    strikes, market = _vector(body, "strikes"), _vector(body, "market_vols")
    forward, horizon = _positive(body, "forward"), _positive(body, "T")
    if len(strikes) != len(market):
        raise ValueError("strikes and market_vols must match")
    x_values = [math.log(strike / forward) for strike in strikes]
    y_values = [vol * vol * horizon for vol in market]
    design = [[1.0, x, x * x] for x in x_values]
    transpose = _transpose_matrix(design)
    coefficients = _solve_matrix(_matmul_values(transpose, design),
                                  [sum(row[index] * value for row, value in zip(design, y_values))
                                   for index in range(3)])
    fitted_variance = [max(1e-12, coefficients[0] + coefficients[1] * x + coefficients[2] * x * x)
                       for x in x_values]
    fitted = [math.sqrt(value / horizon) for value in fitted_variance]
    rmse = math.sqrt(sum((actual - target) ** 2 for actual, target in zip(fitted, market)) / len(market))
    return {"a": coefficients[0], "b": abs(coefficients[2]),
            "rho": max(-0.999, min(0.999, coefficients[1] / (2 * abs(coefficients[2]))))
            if abs(coefficients[2]) > 1e-12 else 0.0,
            "m": 0.0, "sigma": 1.0, "fitted_vols": fitted, "rmse": rmse}


def _variance_gamma_price(body: Mapping[str, Any]) -> Dict[str, float]:
    sigma = _positive(body, "sigma"); theta = _number(body, "theta_vg"); nu = _positive(body, "nu")
    effective = math.sqrt(sigma * sigma + theta * theta * nu)
    result = _bs_values({"spot": _positive(body, "S"), "strike": _positive(body, "K"),
                         "risk_free_rate": _number(body, "r"), "volatility": effective,
                         "time_to_maturity": _positive(body, "T"),
                         "option_type": str(body.get("option_type", "call"))})
    return {"price": result["price"], "effective_volatility": effective,
            "method": "moment_matched_variance_gamma"}


def _ratio(body: Mapping[str, Any], numerator: str, denominator: str) -> Dict[str, float]:
    bottom = _number(body, denominator)
    if abs(bottom) < 1e-15:
        raise ValueError(f"{denominator} must be non-zero")
    return {"value": _number(body, numerator) / bottom}


def _divide(numerator: float, denominator: float, denominator_name: str) -> float:
    if abs(denominator) < 1e-15:
        raise ValueError(f"{denominator_name} must be non-zero")
    return numerator / denominator


def _financial_ratio(body: Mapping[str, Any], category: str, name: str) -> Dict[str, float]:
    get = lambda key, *aliases: _number(body, key, *aliases)
    if category == "profitability":
        formulas = {
            "roa": lambda: _divide(get("net_income"), get("total_assets"), "total_assets"),
            "roe": lambda: _divide(get("net_income"), get("total_equity", "equity"), "total_equity"),
            "roic": lambda: _divide(get("ebit"), get("invested_capital"), "invested_capital"),
            "gross-margin": lambda: _divide(get("gross_profit"), get("revenue"), "revenue"),
            "net-margin": lambda: _divide(get("net_income"), get("revenue"), "revenue"),
            "ebitda-margin": lambda: _divide(get("ebitda"), get("revenue"), "revenue"),
            "operating-margin": lambda: _divide(get("operating_income"), get("revenue"), "revenue"),
            "roce": lambda: _divide(get("ebit"), get("invested_capital"), "invested_capital"),
            "basic-earning-power": lambda: _divide(get("ebit"), get("total_assets"), "total_assets"),
            "cash-roa": lambda: _divide(get("operating_cash_flow"), get("total_assets"), "total_assets"),
            "cash-roe": lambda: _divide(get("operating_cash_flow"), get("total_equity", "equity"), "total_equity"),
            "cash-roic": lambda: _divide(get("operating_cash_flow"), get("invested_capital"), "invested_capital"),
        }
    elif category == "liquidity":
        formulas = {
            "current-ratio": lambda: _divide(get("current_assets"), get("current_liabilities"),
                                              "current_liabilities"),
            "quick-ratio": lambda: _divide(
                get("current_assets") - float(body.get("inventory", 0.0)) -
                float(body.get("prepaid_expenses", 0.0)), get("current_liabilities"),
                "current_liabilities"),
            "cash-ratio": lambda: _divide(get("cash") + float(body.get("marketable_securities", 0.0)),
                                           get("current_liabilities"), "current_liabilities"),
            "absolute-liquidity": lambda: _divide(
                get("cash") + float(body.get("marketable_securities", 0.0)),
                get("current_liabilities"), "current_liabilities"),
            "defensive-interval": lambda: _divide(
                get("cash") + float(body.get("marketable_securities", 0.0)) + get("accounts_receivable"),
                                                   get("operating_expenses") / 365.0, "daily operating expenses"),
            "working-capital": lambda: get("current_assets") - get("current_liabilities"),
            "working-capital-ratio": lambda: _divide(get("current_assets"), get("current_liabilities"),
                                                      "current_liabilities"),
        }
    elif category == "leverage":
        debt, equity = get("total_debt"), get("total_equity", "equity")
        assets, interest = get("total_assets"), get("interest_expense")
        long_debt, ebitda = get("long_term_debt"), get("ebitda")
        debt_service = interest + float(body.get("principal_repayments", 0.0))
        formulas = {
            "debt-to-equity": lambda: _divide(debt, equity, "total_equity"),
            "interest-coverage": lambda: _divide(get("ebit"), interest, "interest_expense"),
            "debt-to-assets": lambda: _divide(debt, assets, "total_assets"),
            "debt-to-capital": lambda: _divide(debt, debt + equity, "total capital"),
            "equity-ratio": lambda: _divide(equity, assets, "total_assets"),
            "equity-multiplier": lambda: _divide(assets, equity, "total_equity"),
            "ebitda-interest-coverage": lambda: _divide(ebitda, interest, "interest_expense"),
            "cash-coverage": lambda: _divide(get("operating_cash_flow"), interest, "interest_expense"),
            "net-debt-to-ebitda": lambda: _divide(debt - float(body.get("cash", 0.0)), ebitda, "ebitda"),
            "long-term-debt-to-equity": lambda: _divide(long_debt, equity, "total_equity"),
            "financial-leverage": lambda: _divide(assets, equity, "total_equity"),
            "capitalization-ratio": lambda: _divide(long_debt, long_debt + equity, "long-term capital"),
            "debt-service-coverage": lambda: _divide(get("operating_cash_flow"), debt_service, "debt service"),
        }
    elif category == "valuation":
        formulas = {
            "pe": lambda: _divide(get("market_price", "price"), get("earnings_per_share", "eps"),
                                   "earnings_per_share"),
            "pb": lambda: _divide(get("market_price", "price"), get("book_value_per_share"),
                                   "book_value_per_share"),
            "ps": lambda: _divide(get("market_price", "price"), get("sales_per_share"), "sales_per_share"),
            "ev-ebitda": lambda: _divide(get("enterprise_value"), get("ebitda"), "ebitda"),
            "dividend-yield": lambda: _divide(get("dividends_per_share"), get("market_price", "price"),
                                               "market_price"),
            "pcf": lambda: _divide(get("market_price", "price"), get("cash_flow_per_share"),
                                    "cash_flow_per_share"),
            "pfcf": lambda: _divide(get("market_price", "price"),
                                      get("free_cash_flow_per_share", "cash_flow_per_share"),
                                      "free_cash_flow_per_share"),
            "peg": lambda: _divide(
                _divide(get("market_price", "price"), get("earnings_per_share", "eps"), "earnings_per_share"),
                get("earnings_growth_rate") * 100.0, "earnings growth percent"),
            "ev-revenue": lambda: _divide(get("enterprise_value"), get("revenue"), "revenue"),
            "ev-fcf": lambda: _divide(get("enterprise_value"), get("free_cash_flow"), "free_cash_flow"),
            "earnings-yield": lambda: _divide(get("earnings_per_share", "eps"),
                                               get("market_price", "price"), "market_price"),
            "fcf-yield": lambda: _divide(get("free_cash_flow"),
                                           float(body.get("market_value", get("enterprise_value"))), "market value"),
            "tobins-q": lambda: _divide(get("market_value"), get("replacement_cost"), "replacement_cost"),
        }
    elif category == "efficiency":
        revenue, cogs = get("revenue"), get("cost_of_goods_sold")
        receivables, inventory = get("accounts_receivable"), get("inventory")
        payables = get("accounts_payable")
        dso = _divide(receivables * 365.0, revenue, "revenue")
        dio = _divide(inventory * 365.0, cogs, "cost_of_goods_sold")
        dpo = _divide(payables * 365.0, cogs, "cost_of_goods_sold")
        formulas = {
            "asset-turnover": lambda: _divide(revenue, get("total_assets"), "total_assets"),
            "fixed-asset-turnover": lambda: _divide(revenue, get("fixed_assets"), "fixed_assets"),
            "inventory-turnover": lambda: _divide(cogs, inventory, "inventory"),
            "receivables-turnover": lambda: _divide(revenue, receivables, "accounts_receivable"),
            "payables-turnover": lambda: _divide(cogs, payables, "accounts_payable"),
            "days-sales-outstanding": lambda: dso,
            "days-inventory-outstanding": lambda: dio,
            "days-payables-outstanding": lambda: dpo,
            "cash-conversion-cycle": lambda: dso + dio - dpo,
            "operating-cycle": lambda: dso + dio,
            "working-capital-turnover": lambda: _divide(revenue, get("working_capital"), "working_capital"),
            "equity-turnover": lambda: _divide(revenue, get("total_equity", "equity"), "total_equity"),
        }
    elif category == "growth":
        retention = float(body.get("retention_ratio", 0.0))
        roa = float(body.get("roa", 0.0))
        formulas = {
            "yoy": lambda: _divide(get("current_value"), get("previous_value"), "previous_value") - 1.0,
            "cagr": lambda: ((get("current_value", "ending_value", "final_value") /
                              get("previous_value", "beginning_value", "initial_value")) **
                              (1.0 / float(body.get("years", body.get("periods", 1.0)))) - 1.0),
            "sustainable-growth": lambda: retention * get("roe"),
            "internal-growth": lambda: _divide(retention * roa, 1.0 - retention * roa, "one minus retained ROA"),
        }
    elif category == "cashflow":
        ocf = get("operating_cash_flow")
        formulas = {
            "ocf-to-debt": lambda: _divide(ocf, get("total_debt"), "total_debt"),
            "ocf-ratio": lambda: _divide(ocf, get("current_liabilities"), "current_liabilities"),
            "ocf-margin": lambda: _divide(ocf, get("revenue"), "revenue"),
            "fcf": lambda: ocf - get("capital_expenditures"),
            "fcf-margin": lambda: _divide(ocf - get("capital_expenditures"), get("revenue"), "revenue"),
            "cash-conversion-quality": lambda: _divide(ocf, get("net_income"), "net_income"),
            "capex-to-ocf": lambda: _divide(get("capital_expenditures"), ocf, "operating_cash_flow"),
            "capex-to-depreciation": lambda: _divide(get("capital_expenditures"), get("depreciation"),
                                                       "depreciation"),
            "cash-coverage-of-dividends": lambda: _divide(ocf, get("dividends"), "dividends"),
            "reinvestment-rate": lambda: _divide(get("capital_expenditures"), ocf, "operating_cash_flow"),
        }
    elif category == "quality":
        if name in {"earnings-persistence", "earnings-variability"}:
            history = _vector(body, "earnings_history")
            if len(history) < 2:
                raise ValueError("earnings_history must contain at least two values")
            if name == "earnings-persistence":
                left, right = history[:-1], history[1:]
                left_mean, right_mean = statistics.mean(left), statistics.mean(right)
                numerator = sum((a - left_mean) * (b - right_mean) for a, b in zip(left, right))
                denominator = math.sqrt(sum((a - left_mean) ** 2 for a in left) *
                                        sum((b - right_mean) ** 2 for b in right))
                return {"value": _divide(numerator, denominator, "lagged earnings variation")}
            return {"value": _divide(statistics.stdev(history), abs(statistics.mean(history)),
                                      "mean earnings")}
        net_income, ocf = get("net_income"), get("operating_cash_flow")
        formulas = {
            "accruals-ratio": lambda: _divide(net_income - ocf, get("total_assets"), "total_assets"),
            "sloan-accruals": lambda: _divide(net_income - ocf, get("total_assets"), "total_assets"),
            "cash-earnings": lambda: ocf - net_income,
            "quality-of-earnings": lambda: _divide(ocf, net_income, "net_income"),
        }
    else:
        raise ValueError("unknown financial ratio category")
    if name not in formulas:
        raise ValueError("unknown financial ratio")
    return {"value": formulas[name]()}


def _pe_ratio(body: Mapping[str, Any]) -> Dict[str, float]:
    price = _number(body, "market_price", "price")
    earnings = _number(body, "earnings_per_share", "eps")
    if abs(earnings) < 1e-15:
        raise ValueError("earnings_per_share must be non-zero")
    return {"value": price / earnings}


def _quick_ratio(body: Mapping[str, Any]) -> Dict[str, float]:
    liabilities = _number(body, "current_liabilities")
    if abs(liabilities) < 1e-15:
        raise ValueError("current_liabilities must be non-zero")
    quick_assets = (_number(body, "current_assets") - float(body.get("inventory", 0.0)) -
                    float(body.get("prepaid_expenses", 0.0)))
    return {"value": quick_assets / liabilities}


def _cagr(body: Mapping[str, Any]) -> Dict[str, float]:
    if "current_value" in body or "previous_value" in body:
        return _financial_ratio(body, "growth", "cagr")
    beginning = _positive(body, "beginning_value", "initial_value")
    ending = _positive(body, "ending_value", "final_value")
    years = _positive(body, "years", "periods")
    return {"value": (ending / beginning) ** (1 / years) - 1}


def _wacc(body: Mapping[str, Any]) -> Dict[str, float]:
    if "equity_weight" in body or "debt_weight" in body:
        equity_weight = _number(body, "equity_weight")
        debt_weight = _number(body, "debt_weight")
        if equity_weight < 0 or debt_weight < 0 or abs(equity_weight + debt_weight - 1.0) > 1e-8:
            raise ValueError("equity_weight and debt_weight must be non-negative and sum to 1")
        return {"wacc": equity_weight * _number(body, "cost_of_equity") +
                debt_weight * _number(body, "cost_of_debt") * (1 - _number(body, "tax_rate"))}
    equity, debt = _number(body, "equity"), _number(body, "debt")
    total = equity + debt
    if total <= 0:
        raise ValueError("equity plus debt must be positive")
    cost_equity = _number(body, "cost_of_equity")
    cost_debt = _number(body, "cost_of_debt")
    tax_rate = _number(body, "tax_rate")
    return {"wacc": equity / total * cost_equity + debt / total * cost_debt * (1 - tax_rate)}


def _terminal_value(body: Mapping[str, Any]) -> Dict[str, float]:
    cash_flow = _number(body, "cash_flow", "fcf")
    growth = _number(body, "growth_rate", "growth")
    discount = _number(body, "discount_rate", "wacc")
    if discount <= growth:
        raise ValueError("discount_rate must exceed growth_rate")
    return {"terminal_value": cash_flow * (1 + growth) / (discount - growth)}


def _dcf_fcff(body: Mapping[str, Any]) -> Dict[str, Any]:
    cash_flows = _vector(body, "fcff")
    discount = _number(body, "wacc", "discount_rate")
    if discount <= -1:
        raise ValueError("wacc must be greater than -1")
    discounted = [value / (1 + discount) ** year for year, value in enumerate(cash_flows, 1)]
    return {"present_value": sum(discounted), "discounted_cash_flows": discounted}


def _dividend_discount(body: Mapping[str, Any]) -> Dict[str, float]:
    dividend = _number(body, "dividend")
    growth = _number(body, "growth_rate", "growth")
    required = _number(body, "required_return", "discount_rate")
    if required <= growth:
        raise ValueError("required_return must exceed growth_rate")
    return {"value": dividend * (1 + growth) / (required - growth)}


def _cost_of_equity(body: Mapping[str, Any]) -> Dict[str, float]:
    return {"cost_of_equity": _number(body, "risk_free_rate") +
            _number(body, "beta") * _number(body, "market_premium")}


def _credit_spread_from_pd(body: Mapping[str, Any]) -> Dict[str, float]:
    probability = _number(body, "pd")
    recovery = float(body.get("recovery_rate", 0.4))
    if not 0 <= probability < 1 or not 0 <= recovery <= 1:
        raise ValueError("pd must be in [0,1) and recovery_rate in [0,1]")
    spread = -math.log1p(-probability) * (1 - recovery)
    return {"spread": spread, "spread_bps": spread * 10_000}


def _expected_credit_loss(body: Mapping[str, Any]) -> Dict[str, float]:
    probability, loss_given_default = _number(body, "pd"), _number(body, "lgd")
    if not 0 <= probability <= 1 or not 0 <= loss_given_default <= 1:
        raise ValueError("pd and lgd must be in [0,1]")
    exposure = _number(body, "ead")
    if exposure < 0:
        raise ValueError("ead must be non-negative")
    return {"expected_loss": probability * loss_given_default * exposure}


def _rating_pd(body: Mapping[str, Any]) -> Dict[str, Any]:
    rating = str(body.get("rating", "")).strip().upper().replace(" ", "")
    probabilities = {
        "AAA": 0.0001, "AA+": 0.0002, "AA": 0.0003, "AA-": 0.0005,
        "A+": 0.0007, "A": 0.0010, "A-": 0.0015,
        "BBB+": 0.0020, "BBB": 0.0030, "BBB-": 0.0050,
        "BB+": 0.0080, "BB": 0.0120, "BB-": 0.0200,
        "B+": 0.0300, "B": 0.0500, "B-": 0.0800,
        "CCC": 0.1500, "CC": 0.3000, "C": 0.5000, "D": 1.0,
    }
    if rating not in probabilities:
        raise ValueError("rating must be a recognized long-term credit rating")
    return {"rating": rating, "pd": probabilities[rating]}


def _altman_z(body: Mapping[str, Any]) -> Dict[str, float]:
    assets = _positive(body, "total_assets")
    liabilities = _positive(body, "total_liabilities")
    score = (1.2 * _number(body, "working_capital") / assets +
             1.4 * _number(body, "retained_earnings") / assets +
             3.3 * _number(body, "ebit") / assets +
             0.6 * _number(body, "market_cap") / liabilities +
             _number(body, "revenue") / assets)
    return {"score": score}


def _springate(body: Mapping[str, Any]) -> Dict[str, float]:
    assets = _positive(body, "total_assets")
    liabilities = _positive(body, "current_liabilities")
    score = (1.03 * _number(body, "working_capital") / assets +
             3.07 * _number(body, "ebit") / assets +
             0.66 * _number(body, "pretax_income") / liabilities +
             0.4 * _number(body, "revenue") / assets)
    return {"score": score}


def _economic_value_added(body: Mapping[str, Any]) -> Dict[str, float]:
    value = _number(body, "nopat") - _number(body, "invested_capital") * _number(body, "wacc")
    return {"value": value}


def _residual_income(body: Mapping[str, Any]) -> Dict[str, float]:
    value = _number(body, "net_income") - _number(body, "equity") * _number(body, "cost_of_equity")
    return {"value": value}


def _residual_income_valuation(body: Mapping[str, Any]) -> Dict[str, Any]:
    cost = _number(body, "cost_of_equity")
    if cost <= -1:
        raise ValueError("cost_of_equity must be greater than -1")
    incomes = _vector(body, "residual_incomes")
    discounted = [income / (1 + cost) ** year for year, income in enumerate(incomes, 1)]
    return {"value": _number(body, "book_value") + sum(discounted), "discounted_residual_incomes": discounted}


def _venture_capital_valuation(body: Mapping[str, Any]) -> Dict[str, float]:
    investment = _positive(body, "investment")
    exit_value = _positive(body, "exit_value")
    years = _positive(body, "years")
    target_return = _number(body, "target_return")
    if target_return <= -1:
        raise ValueError("target_return must be greater than -1")
    post_money = exit_value / (1 + target_return) ** years
    ownership = investment / post_money
    return {"post_money_value": post_money, "pre_money_value": post_money - investment,
            "required_ownership": ownership}


def _first_chicago(body: Mapping[str, Any]) -> Dict[str, Any]:
    scenarios = body.get("scenarios", [])
    if not isinstance(scenarios, list) or not scenarios:
        raise ValueError("scenarios must be a non-empty array")
    discount_rate, years = _number(body, "discount_rate"), _positive(body, "years")
    if discount_rate <= -1:
        raise ValueError("discount_rate must be greater than -1")
    details, probability_sum, expected_exit = [], 0.0, 0.0
    for scenario in scenarios:
        probability = _number(scenario, "probability")
        if not 0 <= probability <= 1:
            raise ValueError("scenario probabilities must be in [0,1]")
        exit_value = _number(scenario, "revenue") * _number(scenario, "margin") * _number(scenario, "multiple")
        probability_sum += probability
        expected_exit += probability * exit_value
        details.append({"name": str(scenario.get("name", "scenario")), "exit_value": exit_value,
                        "probability": probability})
    if abs(probability_sum - 1.0) > 1e-8:
        raise ValueError("scenario probabilities must sum to 1")
    return {"value": expected_exit / (1 + discount_rate) ** years,
            "expected_exit_value": expected_exit, "scenarios": details}


def _startup_dilution(body: Mapping[str, Any]) -> Dict[str, float]:
    pre_money, investment = _positive(body, "pre_money"), _positive(body, "investment")
    option_pool = _number(body, "option_pool")
    if not 0 <= option_pool < 1:
        raise ValueError("option_pool must be in [0,1)")
    post_money = pre_money + investment
    investor = investment / post_money
    return {"post_money": post_money, "investor_ownership": investor,
            "option_pool_ownership": option_pool, "founder_ownership": 1 - investor - option_pool}


def _merton_model(body: Mapping[str, Any]) -> Dict[str, float]:
    equity, debt = _positive(body, "equity_value"), _positive(body, "debt_face")
    rate, equity_vol = _number(body, "risk_free_rate"), _positive(body, "equity_volatility")
    time = float(body.get("time_to_maturity", body.get("time", 1.0)))
    if time <= 0:
        raise ValueError("time_to_maturity must be positive")
    asset = equity + debt * math.exp(-rate * time)
    asset_vol = max(1e-8, equity_vol * equity / asset)
    for _ in range(100):
        root_t = math.sqrt(time)
        d1 = (math.log(asset / debt) + (rate + 0.5 * asset_vol * asset_vol) * time) / (asset_vol * root_t)
        d2 = d1 - asset_vol * root_t
        modeled_equity = asset * NORMAL.cdf(d1) - debt * math.exp(-rate * time) * NORMAL.cdf(d2)
        derivative = NORMAL.cdf(d1)
        if derivative <= 1e-12:
            raise ValueError("structural model could not converge")
        new_asset = max(equity, asset - (modeled_equity - equity) / derivative)
        new_vol = max(1e-8, equity_vol * equity / (new_asset * max(NORMAL.cdf(d1), 1e-12)))
        if abs(new_asset - asset) <= 1e-9 * asset and abs(new_vol - asset_vol) <= 1e-9:
            asset, asset_vol = new_asset, new_vol
            break
        asset, asset_vol = new_asset, new_vol
    distance = (math.log(asset / debt) + (rate - 0.5 * asset_vol * asset_vol) * time) / (
        asset_vol * math.sqrt(time))
    return {"asset_value": asset, "asset_volatility": asset_vol,
            "distance_to_default": distance, "default_probability": NORMAL.cdf(-distance)}


def _distance_to_default(body: Mapping[str, Any]) -> Dict[str, float]:
    asset, debt = _positive(body, "asset_value"), _positive(body, "debt")
    volatility = _positive(body, "asset_volatility")
    rate = _number(body, "risk_free_rate")
    time = float(body.get("time_to_maturity", body.get("time", 1.0)))
    if time <= 0:
        raise ValueError("time_to_maturity must be positive")
    distance = (math.log(asset / debt) + (rate - 0.5 * volatility * volatility) * time) / (
        volatility * math.sqrt(time))
    return {"distance_to_default": distance, "default_probability": NORMAL.cdf(-distance)}


def _segment_sotp(body: Mapping[str, Any]) -> Dict[str, Any]:
    segments = body.get("segments", [])
    if not isinstance(segments, list) or not segments:
        raise ValueError("segments must be a non-empty array")
    debt, cash = _number(body, "total_debt"), _number(body, "cash")
    shares = _positive(body, "shares_outstanding")
    wacc, terminal_growth = _number(body, "wacc"), _number(body, "terminal_growth")
    growth, tax = _number(body, "growth_rate"), _number(body, "tax_rate")
    years = int(_positive(body, "forecast_years"))
    if wacc <= terminal_growth or not 0 <= tax <= 1:
        raise ValueError("wacc must exceed terminal_growth and tax_rate must be in [0,1]")
    comparable_total, dcf_total, details = 0.0, 0.0, []
    for segment in segments:
        comparable = _number(segment, "ebitda") * _number(segment, "comparable_ev_ebitda")
        nopat = _number(segment, "ebit") * (1 - tax)
        forecast = [nopat * (1 + growth) ** year for year in range(1, years + 1)]
        discounted = sum(value / (1 + wacc) ** year for year, value in enumerate(forecast, 1))
        terminal = forecast[-1] * (1 + terminal_growth) / (wacc - terminal_growth)
        dcf_value = discounted + terminal / (1 + wacc) ** years
        comparable_total += comparable
        dcf_total += dcf_value
        details.append({"name": str(segment.get("name", "segment")),
                        "comparable_value": comparable, "dcf_value": dcf_value})
    comparable_equity = comparable_total - debt + cash
    dcf_equity = dcf_total - debt + cash
    return {"segments": details, "comparable_enterprise_value": comparable_total,
            "comparable_equity_value": comparable_equity,
            "comparable_value_per_share": comparable_equity / shares,
            "dcf_enterprise_value": dcf_total, "dcf_equity_value": dcf_equity,
            "dcf_value_per_share": dcf_equity / shares}


def _berkus(body: Mapping[str, Any]) -> Dict[str, Any]:
    names = ("sound_idea", "prototype", "management", "strategic_relationships", "product_rollout")
    values = {name: _number(body, name) for name in names}
    if any(value < 0 or value > 500_000 for value in values.values()):
        raise ValueError("each Berkus factor must be between 0 and 500000")
    return {"value": sum(values.values()), "factors": values}


def _banking_analytics(body: Mapping[str, Any]) -> Dict[str, float]:
    loans, assets = _number(body, "total_loans"), _number(body, "total_assets")
    risk_assets = _number(body, "risk_weighted_assets")
    return {
        "net_interest_margin": _divide(_number(body, "net_interest_income"), _number(body, "earning_assets"),
                                       "earning_assets"),
        "efficiency_ratio": _divide(_number(body, "operating_expenses"), _number(body, "total_revenue"),
                                    "total_revenue"),
        "loan_to_deposit": _divide(loans, _number(body, "total_deposits"), "total_deposits"),
        "non_performing_loan_ratio": _divide(_number(body, "non_performing_loans"), loans, "total_loans"),
        "provision_coverage": _divide(_number(body, "loan_loss_provisions"),
                                      _number(body, "non_performing_loans"), "non_performing_loans"),
        "return_on_assets": _divide(_number(body, "net_income"), assets, "total_assets"),
        "tier1_capital_ratio": _divide(_number(body, "tier1_capital"), risk_assets, "risk_weighted_assets"),
        "total_capital_ratio": _divide(_number(body, "total_capital"), risk_assets, "risk_weighted_assets"),
    }


def _insurance_analytics(body: Mapping[str, Any]) -> Dict[str, float]:
    premiums = _number(body, "net_premiums_earned")
    losses = _number(body, "losses_incurred") + _number(body, "loss_adjustment_expenses")
    expenses = _number(body, "underwriting_expenses")
    surplus = _number(body, "policyholders_surplus")
    return {
        "loss_ratio": _divide(losses, premiums, "net_premiums_earned"),
        "expense_ratio": _divide(expenses, premiums, "net_premiums_earned"),
        "combined_ratio": _divide(losses + expenses, premiums, "net_premiums_earned"),
        "investment_yield": _divide(_number(body, "net_investment_income"), _number(body, "total_assets"),
                                    "total_assets"),
        "premium_to_surplus": _divide(premiums, surplus, "policyholders_surplus"),
        "reserve_to_surplus": _divide(_number(body, "reserves"), surplus, "policyholders_surplus"),
        "retention_ratio": 1 - _divide(_number(body, "ceded_premiums"), _number(body, "gross_premiums"),
                                       "gross_premiums"),
    }


def _reit_analytics(body: Mapping[str, Any]) -> Dict[str, float]:
    shares = _positive(body, "shares_outstanding")
    ffo = _number(body, "net_income") + _number(body, "depreciation")
    affo = ffo - _number(body, "capital_expenditures") - _number(body, "leasing_costs")
    dividends = _number(body, "dividends_per_share") * shares
    property_value = _number(body, "property_value")
    return {
        "ffo": ffo, "affo": affo, "ffo_per_share": ffo / shares, "affo_per_share": affo / shares,
        "ffo_payout_ratio": _divide(dividends, ffo, "ffo"),
        "affo_payout_ratio": _divide(dividends, affo, "affo"),
        "implied_cap_rate": _divide(_number(body, "net_operating_income"), property_value, "property_value"),
        "net_asset_value": property_value - _number(body, "total_debt"),
        "debt_to_ebitda": _divide(_number(body, "total_debt"), _number(body, "ebitda"), "ebitda"),
        "interest_coverage": _divide(_number(body, "ebitda"), _number(body, "interest_expense"),
                                     "interest_expense"),
    }


def _utilities_analytics(body: Mapping[str, Any]) -> Dict[str, float]:
    revenue = _number(body, "total_revenue")
    debt, equity = _number(body, "total_debt"), _number(body, "total_equity")
    capacity = _positive(body, "capacity_mw")
    generation = _number(body, "kwh_generated")
    return {
        "operating_margin": _divide(revenue - _number(body, "operating_expenses"), revenue, "total_revenue"),
        "fuel_recovery_ratio": _divide(_number(body, "fuel_costs_recovered"), _number(body, "fuel_costs"),
                                       "fuel_costs"),
        "return_on_rate_base": _divide(revenue - _number(body, "operating_expenses"),
                                       _number(body, "rate_base"), "rate_base"),
        "debt_to_capital": _divide(debt, debt + equity, "total capital"),
        "capex_to_revenue": _divide(_number(body, "capital_expenditures"), revenue, "total_revenue"),
        "interest_coverage": _divide(_number(body, "ebitda"), _number(body, "interest_expense"),
                                     "interest_expense"),
        "capacity_factor": generation / (capacity * 1000 * 8760),
        "load_factor": generation / (_positive(body, "peak_demand_mw") * 1000 * 8760),
        "heat_rate": _divide(_number(body, "heat_rate_btu"), generation, "kwh_generated"),
    }


def _object(body: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    value = body.get(name)
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be an object")
    return value


def _comparable_valuation(body: Mapping[str, Any]) -> Dict[str, Any]:
    target = _object(body, "target")
    peers = body.get("peers", [])
    metrics = [str(metric).lower() for metric in body.get("metrics", [])]
    if not isinstance(peers, list) or not peers or not metrics:
        raise ValueError("peers and metrics must be non-empty arrays")
    net_debt = _number(target, "total_debt") - _number(target, "cash")
    definitions = {
        "pe": ("market_cap", "net_income", "net_income", False),
        "ev_ebitda": ("enterprise_value", "ebitda", "ebitda", True),
        "pb": ("market_cap", "book_value", "book_value", False),
        "ps": ("market_cap", "revenue", "revenue", False),
        "ev_revenue": ("enterprise_value", "revenue", "revenue", True),
        "pcf": ("market_cap", "operating_cash_flow", "operating_cash_flow", False),
    }
    results, implied_values = [], []
    for metric in metrics:
        if metric not in definitions:
            raise ValueError(f"unsupported comparable metric: {metric}")
        numerator, denominator, target_driver, enterprise_metric = definitions[metric]
        peer_multiples = []
        for peer in peers:
            if not isinstance(peer, Mapping):
                raise ValueError("each peer must be an object")
            peer_multiples.append(_divide(_number(peer, numerator), _number(peer, denominator), denominator))
        median = statistics.median(peer_multiples)
        implied_base = median * _number(target, target_driver)
        implied_equity = implied_base - net_debt if enterprise_metric else implied_base
        implied_values.append(implied_equity)
        results.append({"metric": metric, "peer_median": median, "peer_multiples": peer_multiples,
                        "implied_equity_value": implied_equity})
    return {"metrics": results, "implied_equity_value": statistics.median(implied_values),
            "target_market_cap": _number(target, "market_cap")}


def _valuation_screen(body: Mapping[str, Any]) -> Dict[str, Any]:
    companies = body.get("companies", [])
    screen_type = str(body.get("screen_type", "value")).lower()
    if not isinstance(companies, list) or not companies:
        raise ValueError("companies must be a non-empty array")
    if screen_type not in {"value", "growth", "quality"}:
        raise ValueError("screen_type must be value, growth, or quality")
    ranked = []
    for company in companies:
        if not isinstance(company, Mapping):
            raise ValueError("each company must be an object")
        if screen_type == "value":
            score = _number(company, "pe") + 5 * _number(company, "pb")
            sort_score = score
        elif screen_type == "growth":
            score = (_number(company, "revenue_growth") + _number(company, "gross_margin") +
                     _number(company, "roe"))
            sort_score = -score
        else:
            score = (_number(company, "roe") + _number(company, "gross_margin") +
                     0.1 * _number(company, "current_ratio") - 0.1 * _number(company, "debt_to_equity"))
            sort_score = -score
        ranked.append({"name": str(company.get("name", "company")), "score": score,
                       "_sort_score": sort_score, "metrics": dict(company)})
    ranked.sort(key=lambda item: (item["_sort_score"], item["name"]))
    for rank, item in enumerate(ranked, 1):
        item.pop("_sort_score")
        item["rank"] = rank
    return {"screen_type": screen_type, "ranked_companies": ranked}


def _factor_models(body: Mapping[str, Any]) -> Dict[str, float]:
    income, balance = _object(body, "income"), _object(body, "balance")
    market, cash_flow = _object(body, "market"), _object(body, "cash_flow")
    prior_balance = _object(body, "prior_balance")
    market_cap = _number(market, "market_cap")
    assets, prior_assets = _number(balance, "total_assets"), _number(prior_balance, "total_assets")
    net_income = _number(income, "net_income")
    return {
        "profitability": _divide(net_income, _number(balance, "total_equity"), "total_equity"),
        "earnings_yield": _divide(net_income, market_cap, "market_cap"),
        "book_to_market": _divide(_number(balance, "total_equity"), market_cap, "market_cap"),
        "cashflow_yield": _divide(_number(cash_flow, "operating_cash_flow"), market_cap, "market_cap"),
        "dividend_yield": _divide(_number(income, "dividends_paid"), market_cap, "market_cap"),
        "gross_margin": _divide(_number(income, "gross_profit"), _number(income, "revenue"), "revenue"),
        "asset_growth": _divide(assets - prior_assets, prior_assets, "prior total_assets"),
    }


def _piotroski_f(body: Mapping[str, Any]) -> Dict[str, Any]:
    income, balance = _object(body, "income"), _object(body, "balance")
    cash_flow = _object(body, "cash_flow")
    prior_income, prior_balance = _object(body, "prior_income"), _object(body, "prior_balance")
    assets, prior_assets = _number(balance, "total_assets"), _number(prior_balance, "total_assets")
    net_income, prior_net = _number(income, "net_income"), _number(prior_income, "net_income")
    ocf = _number(cash_flow, "operating_cash_flow")
    signals = {
        "positive_roa": net_income / assets > 0,
        "positive_ocf": ocf > 0,
        "improving_roa": net_income / assets > prior_net / prior_assets,
        "quality_of_earnings": ocf > net_income,
        "lower_leverage": (_number(balance, "long_term_debt") / assets <
                           _number(prior_balance, "long_term_debt") / prior_assets),
        "higher_current_ratio": (_number(balance, "total_current_assets") /
                                 _number(balance, "total_current_liabilities") >
                                 _number(prior_balance, "total_current_assets") /
                                 _number(prior_balance, "total_current_liabilities")),
        "no_dilution": _number(income, "shares_outstanding") <= _number(prior_income, "shares_outstanding"),
        "higher_gross_margin": (_number(income, "gross_profit") / _number(income, "revenue") >
                                _number(prior_income, "gross_profit") / _number(prior_income, "revenue")),
        "higher_asset_turnover": (_number(income, "revenue") / assets >
                                  _number(prior_income, "revenue") / prior_assets),
    }
    return {"score": sum(signals.values()), "signals": signals}


def _beneish_m(body: Mapping[str, Any]) -> Dict[str, Any]:
    income, balance = _object(body, "income"), _object(body, "balance")
    cash_flow = _object(body, "cash_flow")
    prior_income, prior_balance = _object(body, "prior_income"), _object(body, "prior_balance")
    revenue, prior_revenue = _number(income, "revenue"), _number(prior_income, "revenue")
    assets, prior_assets = _number(balance, "total_assets"), _number(prior_balance, "total_assets")
    dsri = (_number(balance, "accounts_receivable") / revenue) / (
        _number(prior_balance, "accounts_receivable") / prior_revenue)
    current_margin = _number(income, "gross_profit") / revenue
    prior_margin = _number(prior_income, "gross_profit") / prior_revenue
    gmi = prior_margin / current_margin
    aqi = (1 - (_number(balance, "total_current_assets") + _number(balance, "net_ppe")) / assets) / (
        1 - (_number(prior_balance, "total_current_assets") + _number(prior_balance, "net_ppe")) / prior_assets)
    sgi = revenue / prior_revenue
    depi = (_number(prior_income, "depreciation") /
            (_number(prior_income, "depreciation") + _number(prior_balance, "net_ppe"))) / (
                _number(income, "depreciation") /
                (_number(income, "depreciation") + _number(balance, "net_ppe")))
    sgai = (_number(income, "sga_expenses") / revenue) / (
        _number(prior_income, "sga_expenses") / prior_revenue)
    lvgi = (_number(balance, "total_liabilities") / assets) / (
        _number(prior_balance, "total_liabilities") / prior_assets)
    tata = (_number(income, "net_income") - _number(cash_flow, "operating_cash_flow")) / assets
    score = (-4.84 + 0.920 * dsri + 0.528 * gmi + 0.404 * aqi + 0.892 * sgi +
             0.115 * depi - 0.172 * sgai + 4.679 * tata - 0.327 * lvgi)
    return {"score": score, "likely_manipulator": score > -1.78,
            "indices": {"dsri": dsri, "gmi": gmi, "aqi": aqi, "sgi": sgi,
                        "depi": depi, "sgai": sgai, "tata": tata, "lvgi": lvgi}}


def _ohlson_o(body: Mapping[str, Any]) -> Dict[str, float]:
    income, balance = _object(body, "income"), _object(body, "balance")
    cash_flow = body.get("cash_flow", {})
    prior_income = body.get("prior_income", {})
    assets, liabilities = _positive(balance, "total_assets"), _number(balance, "total_liabilities")
    current_assets = _number(balance, "total_current_assets")
    current_liabilities = _number(balance, "total_current_liabilities")
    net_income = _number(income, "net_income")
    operating_cash_flow = float(cash_flow.get("operating_cash_flow", net_income)) if isinstance(cash_flow, Mapping) else net_income
    prior_net = float(prior_income.get("net_income", net_income)) if isinstance(prior_income, Mapping) else net_income
    scale = float(body.get("gnp_price_index", 1_000_000.0))
    change_income = (net_income - prior_net) / (abs(net_income) + abs(prior_net)) if net_income or prior_net else 0.0
    score = (-1.32 - 0.407 * math.log(assets / scale) + 6.03 * liabilities / assets -
             1.43 * (current_assets - current_liabilities) / assets +
             0.076 * current_liabilities / current_assets - 1.72 * float(liabilities > assets) -
             2.37 * net_income / assets - 1.83 * operating_cash_flow / liabilities +
             0.285 * float(net_income < 0 and prior_net < 0) - 0.521 * change_income)
    probability = 1 / (1 + math.exp(-max(-700.0, min(700.0, score))))
    return {"score": score, "probability": probability}


def _zmijewski(body: Mapping[str, Any]) -> Dict[str, float]:
    income, balance = _object(body, "income"), _object(body, "balance")
    assets = _positive(balance, "total_assets")
    current_liabilities = _number(balance, "total_current_liabilities")
    score = (-4.3 - 4.5 * _number(income, "net_income") / assets +
             5.7 * _number(balance, "total_liabilities") / assets +
             0.004 * _number(balance, "total_current_assets") / current_liabilities)
    probability = 1 / (1 + math.exp(-max(-700.0, min(700.0, score))))
    return {"score": score, "probability": probability}


def _proforma_adjustments(body: Mapping[str, Any]) -> Dict[str, Any]:
    income, balance = _object(body, "income"), _object(body, "balance")
    tax_rate = _number(body, "tax_rate")
    if not 0 <= tax_rate <= 1:
        raise ValueError("tax_rate must be in [0,1]")
    history = _vector(body, "rd_expenses_history")
    useful_life = int(_positive(body, "useful_life_rd"))
    if len(history) > useful_life:
        history = history[-useful_life:]
    weights = [(index + useful_life - len(history) + 1) / useful_life for index in range(len(history))]
    capitalized_rd = sum(expense * weight for expense, weight in zip(history, weights))
    rd_amortization = capitalized_rd / useful_life
    discount_rate = _number(body, "discount_rate")
    if discount_rate <= -1:
        raise ValueError("discount_rate must be greater than -1")
    payments = _vector(body, "lease_payments")
    lease_liability = sum(payment / (1 + discount_rate) ** year for year, payment in enumerate(payments, 1))
    goodwill = float(body.get("goodwill", 0.0))
    approach = str(body.get("goodwill_approach", "moderate")).lower()
    impairment_rates = {"conservative": 1.0, "moderate": 0.5, "aggressive": 0.0}
    if approach not in impairment_rates:
        raise ValueError("goodwill_approach must be conservative, moderate, or aggressive")
    goodwill_impairment = goodwill * impairment_rates[approach]
    pension_deficit = max(0.0, _number(body, "pension_obligation") - _number(body, "pension_plan_assets"))
    economic_pension_expense = (_number(body, "pension_service_cost") + _number(body, "pension_interest_cost") -
                                _number(body, "pension_expected_return"))
    pension_income_adjustment = _number(body, "pension_reported_expense") - economic_pension_expense
    stock_compensation = _number(body, "stock_compensation")
    adjusted_income = (_number(income, "net_income") +
                       (_number(income, "rd_expenses") - rd_amortization) * (1 - tax_rate) +
                       pension_income_adjustment * (1 - tax_rate) +
                       stock_compensation * (1 - tax_rate) - goodwill_impairment)
    diluted_shares = _positive(body, "diluted_shares")
    return {"capitalized_rd": capitalized_rd, "rd_amortization": rd_amortization,
            "lease_liability": lease_liability, "goodwill_impairment": goodwill_impairment,
            "pension_deficit": pension_deficit, "adjusted_net_income": adjusted_income,
            "adjusted_eps": adjusted_income / diluted_shares,
            "adjusted_assets": (_number(balance, "total_assets") + capitalized_rd + lease_liability -
                                goodwill_impairment - pension_deficit),
            "adjusted_equity": (_number(balance, "total_equity") + capitalized_rd -
                                goodwill_impairment - pension_deficit),
            "adjustments": {"pension_income_adjustment": pension_income_adjustment,
                            "stock_compensation_after_tax": stock_compensation * (1 - tax_rate)}}


def _average_field(current: Mapping[str, Any], prior: Mapping[str, Any], name: str) -> float:
    return (_number(current, name) + _number(prior, name)) / 2


def _total_debt(balance: Mapping[str, Any]) -> float:
    return (float(balance.get("short_term_debt", 0.0)) +
            float(balance.get("current_portion_lt_debt", 0.0)) +
            float(balance.get("long_term_debt", 0.0)))


def _fundamental_profitability(body: Mapping[str, Any]) -> Dict[str, float]:
    income, balance = _object(body, "income"), _object(body, "balance")
    prior = body.get("prior_balance", balance)
    if not isinstance(prior, Mapping):
        raise ValueError("prior_balance must be an object")
    revenue, net_income = _number(income, "revenue"), _number(income, "net_income")
    avg_assets = _average_field(balance, prior, "total_assets")
    avg_equity = _average_field(balance, prior, "total_equity")
    effective_tax = _divide(_number(income, "income_tax"), _number(income, "pretax_income"), "pretax_income")
    invested_capital = avg_assets - _average_field(balance, prior, "total_current_liabilities")
    return {
        "gross_margin": _divide(_number(income, "gross_profit"), revenue, "revenue"),
        "operating_margin": _divide(_number(income, "operating_income"), revenue, "revenue"),
        "pretax_margin": _divide(_number(income, "pretax_income"), revenue, "revenue"),
        "net_margin": _divide(net_income, revenue, "revenue"),
        "return_on_assets": _divide(net_income, avg_assets, "average assets"),
        "return_on_equity": _divide(net_income, avg_equity, "average equity"),
        "return_on_invested_capital": _divide(_number(income, "operating_income") * (1 - effective_tax),
                                              invested_capital, "invested capital"),
        "effective_tax_rate": effective_tax,
    }


def _fundamental_liquidity(body: Mapping[str, Any]) -> Dict[str, float]:
    balance = _object(body, "balance")
    cash_flow = body.get("cash_flow", {})
    liabilities = _number(balance, "total_current_liabilities")
    current_assets = _number(balance, "total_current_assets")
    quick_assets = (_number(balance, "cash") + float(balance.get("short_term_investments", 0.0)) +
                    _number(balance, "accounts_receivable"))
    data = {
        "current_ratio": _divide(current_assets, liabilities, "total_current_liabilities"),
        "quick_ratio": _divide(quick_assets, liabilities, "total_current_liabilities"),
        "cash_ratio": _divide(_number(balance, "cash") + float(balance.get("short_term_investments", 0.0)),
                              liabilities, "total_current_liabilities"),
        "working_capital": current_assets - liabilities,
    }
    if isinstance(cash_flow, Mapping) and "operating_cash_flow" in cash_flow:
        data["operating_cash_flow_ratio"] = _divide(_number(cash_flow, "operating_cash_flow"), liabilities,
                                                     "total_current_liabilities")
    return data


def _fundamental_efficiency(body: Mapping[str, Any]) -> Dict[str, float]:
    income, balance = _object(body, "income"), _object(body, "balance")
    prior = _object(body, "prior_balance")
    revenue, cogs = _number(income, "revenue"), _number(income, "cost_of_goods_sold")
    avg_assets = _average_field(balance, prior, "total_assets")
    avg_inventory = _average_field(balance, prior, "inventory")
    avg_receivables = _average_field(balance, prior, "accounts_receivable")
    avg_payables = _average_field(balance, prior, "accounts_payable")
    dso = _divide(avg_receivables * 365, revenue, "revenue")
    dio = _divide(avg_inventory * 365, cogs, "cost_of_goods_sold")
    dpo = _divide(avg_payables * 365, cogs, "cost_of_goods_sold")
    return {
        "asset_turnover": _divide(revenue, avg_assets, "average assets"),
        "inventory_turnover": _divide(cogs, avg_inventory, "average inventory"),
        "receivables_turnover": _divide(revenue, avg_receivables, "average receivables"),
        "payables_turnover": _divide(cogs, avg_payables, "average payables"),
        "days_sales_outstanding": dso, "days_inventory_outstanding": dio,
        "days_payables_outstanding": dpo, "cash_conversion_cycle": dso + dio - dpo,
    }


def _growth_rate(current: Mapping[str, Any], prior: Mapping[str, Any], name: str) -> float:
    return _divide(_number(current, name) - _number(prior, name), _number(prior, name), f"prior {name}")


def _fundamental_growth(body: Mapping[str, Any]) -> Dict[str, float]:
    current_income, prior_income = _object(body, "current_income"), _object(body, "prior_income")
    current_balance, prior_balance = _object(body, "current_balance"), _object(body, "prior_balance")
    current_cash, prior_cash = _object(body, "current_cash_flow"), _object(body, "prior_cash_flow")
    return {
        "revenue_growth": _growth_rate(current_income, prior_income, "revenue"),
        "gross_profit_growth": _growth_rate(current_income, prior_income, "gross_profit"),
        "operating_income_growth": _growth_rate(current_income, prior_income, "operating_income"),
        "net_income_growth": _growth_rate(current_income, prior_income, "net_income"),
        "eps_growth": _growth_rate(current_income, prior_income, "eps") if "eps" in current_income else
                      _growth_rate(current_income, prior_income, "net_income"),
        "asset_growth": _growth_rate(current_balance, prior_balance, "total_assets"),
        "equity_growth": _growth_rate(current_balance, prior_balance, "total_equity"),
        "operating_cash_flow_growth": _growth_rate(current_cash, prior_cash, "operating_cash_flow"),
        "capital_expenditure_growth": _growth_rate(current_cash, prior_cash, "capital_expenditures"),
    }


def _fundamental_solvency(body: Mapping[str, Any]) -> Dict[str, float]:
    income, balance, cash_flow = _object(body, "income"), _object(body, "balance"), _object(body, "cash_flow")
    debt = _total_debt(balance)
    interest = _number(income, "interest_expense")
    return {
        "debt_to_equity": _divide(debt, _number(balance, "total_equity"), "total_equity"),
        "debt_to_assets": _divide(debt, _number(balance, "total_assets"), "total_assets"),
        "liabilities_to_assets": _divide(_number(balance, "total_liabilities"),
                                         _number(balance, "total_assets"), "total_assets"),
        "interest_coverage": _divide(_number(income, "operating_income"), interest, "interest_expense"),
        "cash_debt_coverage": _divide(_number(cash_flow, "operating_cash_flow"), debt, "total debt"),
        "debt_service_coverage": _divide(_number(cash_flow, "operating_cash_flow"),
                                         interest + _number(cash_flow, "debt_repaid"), "debt service"),
    }


def _fundamental_cashflow(body: Mapping[str, Any]) -> Dict[str, float]:
    income, balance, cash_flow = _object(body, "income"), _object(body, "balance"), _object(body, "cash_flow")
    ocf, capex = _number(cash_flow, "operating_cash_flow"), _number(cash_flow, "capital_expenditures")
    free_cash_flow = ocf - capex
    return {
        "free_cash_flow": free_cash_flow,
        "operating_cash_flow_margin": _divide(ocf, _number(income, "revenue"), "revenue"),
        "cash_conversion_quality": _divide(ocf, _number(income, "net_income"), "net_income"),
        "cash_return_on_assets": _divide(ocf, _number(balance, "total_assets"), "total_assets"),
        "capital_expenditure_coverage": _divide(ocf, capex, "capital_expenditures"),
        "dividend_coverage": _divide(ocf, _number(cash_flow, "dividends_paid"), "dividends_paid"),
        "free_cash_flow_margin": _divide(free_cash_flow, _number(income, "revenue"), "revenue"),
    }


def _fundamental_dupont(body: Mapping[str, Any]) -> Dict[str, float]:
    income, balance, prior = _object(body, "income"), _object(body, "balance"), _object(body, "prior_balance")
    net_margin = _divide(_number(income, "net_income"), _number(income, "revenue"), "revenue")
    avg_assets = _average_field(balance, prior, "total_assets")
    avg_equity = _average_field(balance, prior, "total_equity")
    asset_turnover = _divide(_number(income, "revenue"), avg_assets, "average assets")
    equity_multiplier = _divide(avg_assets, avg_equity, "average equity")
    return {"net_margin": net_margin, "asset_turnover": asset_turnover,
            "equity_multiplier": equity_multiplier,
            "roe": net_margin * asset_turnover * equity_multiplier}


def _fundamental_quality(body: Mapping[str, Any]) -> Dict[str, float]:
    income, balance, cash_flow = _object(body, "income"), _object(body, "balance"), _object(body, "cash_flow")
    prior_income, prior_balance = _object(body, "prior_income"), _object(body, "prior_balance")
    current_margin = _divide(_number(income, "gross_profit"), _number(income, "revenue"), "revenue")
    prior_margin = _divide(_number(prior_income, "gross_profit"), _number(prior_income, "revenue"), "prior revenue")
    current_roa = _divide(_number(income, "net_income"), _number(balance, "total_assets"), "total_assets")
    prior_roa = _divide(_number(prior_income, "net_income"), _number(prior_balance, "total_assets"),
                        "prior total_assets")
    return {
        "quality_of_earnings": _divide(_number(cash_flow, "operating_cash_flow"),
                                       _number(income, "net_income"), "net_income"),
        "accruals_ratio": _divide(_number(income, "net_income") - _number(cash_flow, "operating_cash_flow"),
                                  _number(balance, "total_assets"), "total_assets"),
        "gross_margin_change": current_margin - prior_margin,
        "return_on_assets_change": current_roa - prior_roa,
        "asset_growth": _growth_rate(balance, prior_balance, "total_assets"),
    }


def _fundamental_comprehensive(body: Mapping[str, Any]) -> Dict[str, Any]:
    income, balance, cash_flow = _object(body, "income"), _object(body, "balance"), _object(body, "cash_flow")
    prior_income, prior_balance = _object(body, "prior_income"), _object(body, "prior_balance")
    market = _object(body, "market")
    shares = _number(market, "shares_outstanding")
    market_cap = float(market.get("market_cap", _number(market, "share_price") * shares))
    if "market_cap" in market and "share_price" in market and abs(market_cap - _number(market, "share_price") * shares) > 1e-6 * market_cap:
        raise ValueError("market_cap must match share_price times shares_outstanding")
    debt = _total_debt(balance)
    enterprise = market_cap + debt - _number(balance, "cash")
    standard = {"income": income, "balance": balance, "cash_flow": cash_flow,
                "prior_income": prior_income, "prior_balance": prior_balance}
    growth_body = {"current_income": income, "prior_income": prior_income,
                   "current_balance": balance, "prior_balance": prior_balance,
                   "current_cash_flow": cash_flow, "prior_cash_flow": cash_flow}
    valuation = {
        "market_cap": market_cap, "enterprise_value": enterprise,
        "price_to_earnings": _divide(market_cap, _number(income, "net_income"), "net_income"),
        "price_to_book": _divide(market_cap, _number(balance, "total_equity"), "total_equity"),
        "price_to_cash_flow": _divide(market_cap, _number(cash_flow, "operating_cash_flow"),
                                      "operating_cash_flow"),
    }
    return {"profitability": _fundamental_profitability(standard),
            "liquidity": _fundamental_liquidity(standard),
            "efficiency": _fundamental_efficiency(standard),
            "growth": _fundamental_growth(growth_body),
            "solvency": _fundamental_solvency(standard),
            "cashflow": _fundamental_cashflow(standard),
            "dupont": _fundamental_dupont(standard), "quality": _fundamental_quality(standard),
            "valuation": valuation}


def _fundamental_wacc(body: Mapping[str, Any]) -> Dict[str, float]:
    debt, equity = _number(body, "debt"), _number(body, "equity")
    total = debt + equity
    if total <= 0:
        raise ValueError("debt plus equity must be positive")
    cost_equity = float(body.get("cost_of_equity", 0.095))
    cost_debt = float(body.get("cost_of_debt", 0.05))
    tax_rate = float(body.get("tax_rate", 0.25))
    wacc = equity / total * cost_equity + debt / total * cost_debt * (1 - tax_rate)
    return {"wacc": wacc, "debt_weight": debt / total, "equity_weight": equity / total,
            "cost_of_equity": cost_equity, "after_tax_cost_of_debt": cost_debt * (1 - tax_rate)}


def _optimal_capital_structure(body: Mapping[str, Any]) -> Dict[str, Any]:
    base_equity_cost, base_debt_cost = _number(body, "cost_of_equity"), _number(body, "cost_of_debt")
    tax_rate, ebitda = _number(body, "tax_rate"), _positive(body, "ebitda")
    total_capital = _positive(body, "debt") + _positive(body, "equity")
    candidates = []
    for step in range(81):
        debt_ratio = step / 100
        equity_ratio = 1 - debt_ratio
        debt_amount = total_capital * debt_ratio
        leverage = debt_amount / ebitda
        cost_debt = base_debt_cost + 0.01 * max(0.0, leverage - 2.0) ** 2
        cost_equity = base_equity_cost + 0.04 * debt_ratio / max(equity_ratio, 0.01)
        wacc = equity_ratio * cost_equity + debt_ratio * cost_debt * (1 - tax_rate)
        candidates.append({"debt_ratio": debt_ratio, "wacc": wacc,
                           "cost_of_debt": cost_debt, "cost_of_equity": cost_equity})
    optimum = min(candidates, key=lambda item: item["wacc"])
    return {"optimal_debt_ratio": optimum["debt_ratio"], "optimal_equity_ratio": 1 - optimum["debt_ratio"],
            "minimum_wacc": optimum["wacc"], "current_debt_ratio": _number(body, "debt") / total_capital,
            "candidates": candidates}


def _target_return_portfolio(body: Mapping[str, Any]) -> Dict[str, Any]:
    covariance = _matrix(body, "covariance_matrix")
    returns = _vector(body, "expected_returns")
    target = _number(body, "target_return")
    if len(returns) != len(covariance) or not min(returns) <= target <= max(returns):
        raise ValueError("target_return must be attainable from expected_returns")
    inverse = _inverse(covariance)
    ones = [1.0] * len(returns)
    inv_one, inv_return = _matvec(inverse, ones), _matvec(inverse, returns)
    a = sum(inv_one)
    b = sum(value * weight for value, weight in zip(returns, inv_one))
    c = sum(value * weight for value, weight in zip(returns, inv_return))
    determinant = a * c - b * b
    if abs(determinant) < 1e-14:
        raise ValueError("target-return portfolio cannot be constructed")
    first, second = (c - b * target) / determinant, (a * target - b) / determinant
    weights = [first * left + second * right for left, right in zip(inv_one, inv_return)]
    result = _portfolio_summary(weights, covariance, returns)
    result["target_return"] = target
    return result


def _black_litterman_equilibrium(body: Mapping[str, Any]) -> Dict[str, Any]:
    covariance = _matrix(body, "covariance_matrix")
    market_caps = _vector(body, "market_caps")
    if len(market_caps) != len(covariance) or any(value <= 0 for value in market_caps):
        raise ValueError("market_caps must be positive and match covariance_matrix")
    weights = [value / sum(market_caps) for value in market_caps]
    risk_aversion = float(body.get("risk_aversion", 2.5))
    equilibrium = [risk_aversion * value for value in _matvec(covariance, weights)]
    return {"market_weights": weights, "equilibrium_returns": equilibrium,
            "risk_aversion": risk_aversion}


def _black_litterman_posterior(body: Mapping[str, Any]) -> Dict[str, Any]:
    equilibrium = _black_litterman_equilibrium(body)
    covariance = _matrix(body, "covariance_matrix")
    tau = float(body.get("tau", 0.05))
    views = body.get("views", [])
    posterior = list(equilibrium["equilibrium_returns"])
    if isinstance(views, list):
        for view in views:
            if not isinstance(view, Mapping):
                raise ValueError("each view must be an object")
            weights = [float(value) for value in view.get("weights", [])]
            if len(weights) != len(posterior):
                raise ValueError("view weights must match asset count")
            confidence = float(view.get("confidence", 0.5))
            expected = _number(view, "expected_return")
            current = sum(weight * value for weight, value in zip(weights, posterior))
            scale = sum(abs(weight) for weight in weights) or 1.0
            adjustment = confidence * (expected - current) / scale
            posterior = [value + adjustment * weight for value, weight in zip(posterior, weights)]
    return {**equilibrium, "posterior_returns": posterior,
            "posterior_covariance": [[value * (1 + tau) for value in row] for row in covariance],
            "tau": tau}


def _portfolio_incremental_var(body: Mapping[str, Any]) -> Dict[str, float]:
    covariance = _matrix(body, "covariance_matrix")
    weights = _vector(body, "weights")
    index = int(body.get("asset_index", -1))
    change = _number(body, "weight_change")
    if len(weights) != len(covariance) or not 0 <= index < len(weights):
        raise ValueError("asset_index and weights must match covariance_matrix")
    confidence = float(body.get("confidence", 0.95))
    base = _portfolio_var({"weights": weights, "covariance_matrix": covariance, "confidence": confidence})["var"]
    changed = list(weights)
    changed[index] += change
    total = sum(changed)
    if total <= 0:
        raise ValueError("changed weights must have a positive sum")
    changed = [value / total for value in changed]
    revised = _portfolio_var({"weights": changed, "covariance_matrix": covariance, "confidence": confidence})["var"]
    return {"base_var": base, "revised_var": revised, "var_change": revised - base,
            "incremental_var": (revised - base) / change if change else 0.0}


def _portfolio_returns_from_asset_rows(rows: Sequence[Sequence[float]], weights: Sequence[float]) -> list[float]:
    if len(rows) != len(weights) or not rows or any(len(row) != len(rows[0]) for row in rows):
        raise ValueError("returns must be asset rows with equal periods and match weights")
    return [sum(weights[asset] * rows[asset][period] for asset in range(len(weights)))
            for period in range(len(rows[0]))]


def _portfolio_risk_comprehensive(body: Mapping[str, Any]) -> Dict[str, Any]:
    weights = _vector(body, "weights")
    covariance = _matrix(body, "covariance_matrix")
    rows = [[float(value) for value in row] for row in body.get("returns", [])]
    series = _portfolio_returns_from_asset_rows(rows, weights)
    summary = _portfolio_var({"weights": weights, "covariance_matrix": covariance,
                              "confidence": body.get("confidence", 0.95)})
    contribution = _risk_contribution({"weights": weights, "covariance_matrix": covariance})
    ratios = _risk_ratios({"returns": series, "risk_free_rate": body.get("risk_free_rate", 0.0),
                           "periods_per_year": body.get("periods_per_year", 252)})
    return {**summary, **ratios, "risk_contributions": contribution["percentage_contributions"],
            "portfolio_returns": series}


def _sample_covariance(observations: Sequence[Sequence[float]]) -> list[list[float]]:
    if len(observations) < 2 or not observations or not observations[0]:
        raise ValueError("returns must have at least two observations")
    width = len(observations[0])
    if any(len(row) != width for row in observations):
        raise ValueError("return observations must have equal width")
    means = [statistics.mean(row[column] for row in observations) for column in range(width)]
    return [[sum((row[left] - means[left]) * (row[right] - means[right]) for row in observations) /
             (len(observations) - 1) for right in range(width)] for left in range(width)]


def _portfolio_comprehensive(body: Mapping[str, Any]) -> Dict[str, Any]:
    observations = [[float(value) for value in row] for row in body.get("returns", [])]
    weights = _vector(body, "weights")
    if not observations or len(observations[0]) != len(weights):
        raise ValueError("return observation width must match weights")
    covariance = _sample_covariance(observations)
    series = [sum(weight * value for weight, value in zip(weights, row)) for row in observations]
    result = _portfolio_risk_comprehensive({"weights": weights,
                                            "returns": [list(column) for column in zip(*observations)],
                                            "covariance_matrix": covariance,
                                            "risk_free_rate": body.get("risk_free_rate", 0.0),
                                            "confidence": body.get("confidence", 0.95),
                                            "periods_per_year": body.get("periods_per_year", 252)})
    result["covariance_matrix"] = covariance
    result["portfolio_returns"] = series
    return result


def _parametric_var(body: Mapping[str, Any]) -> Dict[str, float]:
    value = _positive(body, "portfolio_value")
    volatility = _positive(body, "volatility")
    confidence = float(body.get("confidence", 0.95))
    horizon = float(body.get("horizon", 1.0))
    if not 0.5 < confidence < 1 or horizon <= 0:
        raise ValueError("confidence must be in (0.5,1) and horizon positive")
    z_score = NORMAL.inv_cdf(confidence)
    var = value * z_score * volatility * math.sqrt(horizon)
    expected_shortfall = value * NORMAL.pdf(z_score) / (1 - confidence) * volatility * math.sqrt(horizon)
    return {"var": var, "expected_shortfall": expected_shortfall, "z_score": z_score}


def _factor_component_var(body: Mapping[str, Any]) -> Dict[str, Any]:
    factors = body.get("returns_by_factor", {})
    weight_map = body.get("weights", {})
    if not isinstance(factors, Mapping) or not isinstance(weight_map, Mapping) or not factors:
        raise ValueError("returns_by_factor and weights must be objects")
    names = list(factors)
    rows = [[float(value) for value in factors[name]] for name in names]
    weights = [float(weight_map[name]) for name in names]
    if any(len(row) != len(rows[0]) for row in rows):
        raise ValueError("factor returns must have equal lengths")
    covariance = _sample_covariance([list(column) for column in zip(*rows)])
    contribution = _risk_contribution({"weights": weights, "covariance_matrix": covariance})
    return {"factors": names, **contribution}


def _loss_var(returns: Sequence[float], confidence: float = 0.95) -> tuple[float, float]:
    losses = sorted(-value for value in returns)
    position = confidence * (len(losses) - 1)
    lower, upper = math.floor(position), math.ceil(position)
    var = losses[lower] if lower == upper else losses[lower] + (losses[upper] - losses[lower]) * (position - lower)
    tail = [loss for loss in losses if loss >= var]
    return max(0.0, var), max(0.0, statistics.mean(tail) if tail else var)


def _incremental_historical_var(body: Mapping[str, Any], *, marginal: bool = False) -> Dict[str, float]:
    portfolio = _vector(body, "portfolio_returns")
    position = _vector(body, "new_position_returns")
    if len(portfolio) != len(position):
        raise ValueError("portfolio_returns and new_position_returns must match")
    weight = _number(body, "position_weight")
    confidence = float(body.get("confidence", 0.95))
    base, _ = _loss_var(portfolio, confidence)
    if marginal:
        bump = 1e-4
        bumped = [left + bump * right for left, right in zip(portfolio, position)]
        bumped_var, _ = _loss_var(bumped, confidence)
        return {"base_var": base, "marginal_var": (bumped_var - base) / bump}
    combined = [(1 - weight) * left + weight * right for left, right in zip(portfolio, position)]
    revised, _ = _loss_var(combined, confidence)
    return {"base_var": base, "revised_var": revised, "incremental_var": revised - base}


def _expected_shortfall_optimization(body: Mapping[str, Any]) -> Dict[str, Any]:
    observations = [[float(value) for value in row] for row in body.get("returns", [])]
    if len(observations) < 2 or not observations[0]:
        raise ValueError("returns must contain observations by asset")
    asset_count = len(observations[0])
    if any(len(row) != asset_count for row in observations):
        raise ValueError("return observations must have equal width")
    confidence = float(body.get("confidence", 0.95))
    candidates = [[1 / asset_count] * asset_count]
    rng = random.Random(271828)
    for _ in range(3000):
        draws = [-math.log(max(rng.random(), 1e-15)) for _ in range(asset_count)]
        candidates.append([value / sum(draws) for value in draws])
    best_weights, best_es, best_var = candidates[0], math.inf, 0.0
    for weights in candidates:
        series = [sum(weight * value for weight, value in zip(weights, row)) for row in observations]
        var, es = _loss_var(series, confidence)
        if es < best_es:
            best_weights, best_es, best_var = weights, es, var
    return {"weights": best_weights, "expected_shortfall": best_es, "var": best_var,
            "confidence": confidence}


def _var_backtest(body: Mapping[str, Any]) -> Dict[str, Any]:
    actual = _vector(body, "actual_returns")
    estimates = _vector(body, "var_estimates")
    if len(actual) != len(estimates):
        raise ValueError("actual_returns and var_estimates must match")
    confidence = float(body.get("confidence", 0.95))
    exceptions = [index for index, (value, estimate) in enumerate(zip(actual, estimates)) if value < -abs(estimate)]
    rate = len(exceptions) / len(actual)
    expected = 1 - confidence
    traffic = "green" if rate <= expected * 1.5 else "yellow" if rate <= expected * 2.5 else "red"
    return {"observations": len(actual), "exception_count": len(exceptions),
            "exception_indices": exceptions, "exception_rate": rate,
            "expected_exception_rate": expected, "traffic_light": traffic}


def _stress_scenario(body: Mapping[str, Any]) -> Dict[str, Any]:
    shocks = body.get("factor_shocks", {})
    if not isinstance(shocks, Mapping) or not shocks:
        raise ValueError("factor_shocks must be a non-empty object")
    normalized = {str(name): float(value) for name, value in shocks.items()}
    if any(not math.isfinite(value) for value in normalized.values()):
        raise ValueError("factor shocks must be finite")
    return {"name": str(body.get("name", "scenario")), "factor_shocks": normalized,
            "aggregate_shock": sum(normalized.values()), "worst_factor": min(normalized, key=normalized.get)}


def _correlation_stress(body: Mapping[str, Any]) -> Dict[str, Any]:
    matrix = _matrix(body, "correlation_matrix")
    factor = float(body.get("stress_factor", 0.25))
    if not 0 <= factor <= 1:
        raise ValueError("stress_factor must be in [0,1]")
    stressed = [[1.0 if row == column else matrix[row][column] + factor * (1 - matrix[row][column])
                 for column in range(len(matrix))] for row in range(len(matrix))]
    return {"stressed_correlation_matrix": stressed, "stress_factor": factor}


def _tail_risk_comprehensive(body: Mapping[str, Any]) -> Dict[str, float]:
    returns = _vector(body, "returns")
    confidence = float(body.get("confidence", 0.975))
    portfolio_value = float(body.get("portfolio_value", 1.0))
    var, es = _loss_var(returns, confidence)
    mean = statistics.mean(returns)
    std = statistics.stdev(returns)
    skewness = sum((value - mean) ** 3 for value in returns) / len(returns) / std ** 3 if std else 0.0
    kurtosis = sum((value - mean) ** 4 for value in returns) / len(returns) / std ** 4 - 3 if std else 0.0
    ratios = _risk_ratios({"returns": returns})
    return {"var": var * portfolio_value, "expected_shortfall": es * portfolio_value,
            "skewness": skewness, "excess_kurtosis": kurtosis,
            "max_drawdown": ratios["max_drawdown"]}


def _quantile(values: Sequence[float], probability: float) -> float:
    ordered = sorted(float(value) for value in values)
    if not ordered or not 0 <= probability <= 1:
        raise ValueError("quantile requires values and probability in [0,1]")
    position = probability * (len(ordered) - 1)
    lower, upper = math.floor(position), math.ceil(position)
    return ordered[lower] if lower == upper else ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def _evt_gpd(body: Mapping[str, Any]) -> Dict[str, float]:
    data = _vector(body, "data")
    probability = float(body.get("quantile", 0.9))
    if not 0 < probability < 1:
        raise ValueError("quantile must be in (0,1)")
    threshold = _quantile(data, probability)
    excesses = [value - threshold for value in data if value > threshold]
    if len(excesses) < 2:
        raise ValueError("not enough threshold exceedances")
    mean = statistics.mean(excesses)
    variance = statistics.variance(excesses)
    if variance <= 0:
        shape, scale = 0.0, mean
    else:
        ratio = mean * mean / variance
        shape = max(-0.49, min(0.9, 0.5 * (1 - ratio)))
        scale = max(1e-15, 0.5 * mean * (1 + ratio))
    return {"threshold": threshold, "shape": shape, "scale": scale,
            "exceedance_count": len(excesses), "exceedance_probability": len(excesses) / len(data)}


def _evt_gev(body: Mapping[str, Any]) -> Dict[str, float]:
    data = _vector(body, "data")
    if len(data) < 3:
        raise ValueError("data must contain at least three values")
    scale = statistics.stdev(data) * math.sqrt(6) / math.pi
    location = statistics.mean(data) - 0.5772156649015329 * scale
    return {"shape": 0.0, "location": location, "scale": scale}


def _evt_hill(body: Mapping[str, Any]) -> Dict[str, float]:
    data = sorted((value for value in _vector(body, "data") if value > 0), reverse=True)
    k = int(body.get("k", max(2, round(len(data) ** 0.6))))
    if not 1 <= k < len(data):
        raise ValueError("k must be between 1 and the number of positive observations minus 1")
    threshold = data[k]
    tail_index = sum(math.log(value / threshold) for value in data[:k]) / k
    return {"tail_index": tail_index, "shape": tail_index, "k": k, "threshold": threshold}


def _copula_sample(body: Mapping[str, Any]) -> Dict[str, Any]:
    copula_type = str(body.get("copula_type", "gaussian")).lower()
    if copula_type != "gaussian":
        raise ValueError("only deterministic gaussian copula sampling is supported locally")
    raw = body.get("correlation_matrix", [[1.0, 0.5], [0.5, 1.0]])
    matrix = [[float(value) for value in row] for row in raw]
    if not matrix or any(len(row) != len(matrix) for row in matrix):
        raise ValueError("correlation_matrix must be square")
    lower = _cholesky({"matrix": matrix})["lower"]
    count = int(body.get("n_samples", 1000))
    if not 1 <= count <= 100000:
        raise ValueError("n_samples must be between 1 and 100000")
    rng = random.Random(int(body.get("seed", 1729)))
    samples = []
    for _ in range(count):
        independent = [rng.gauss(0, 1) for _ in matrix]
        correlated = [sum(lower[row][column] * independent[column] for column in range(row + 1))
                      for row in range(len(matrix))]
        samples.append([NORMAL.cdf(value) for value in correlated])
    return {"copula_type": copula_type, "samples": samples,
            "correlation_matrix": matrix, "seed": int(body.get("seed", 1729))}


def _exposure_profile(body: Mapping[str, Any]) -> Dict[str, Any]:
    paths = [[float(value) for value in row] for row in body.get("paths", [])]
    if not paths or any(len(row) != len(paths[0]) for row in paths):
        raise ValueError("paths must be a non-empty rectangular matrix")
    confidence = float(body.get("confidence", 0.95))
    columns = list(zip(*paths))
    expected = [statistics.mean(max(value, 0.0) for value in column) for column in columns]
    negative = [statistics.mean(max(-value, 0.0) for value in column) for column in columns]
    pfe = [_quantile([max(value, 0.0) for value in column], confidence) for column in columns]
    return {"expected_exposure": expected, "expected_negative_exposure": negative,
            "pfe": pfe, "confidence": confidence}


def _optimal_hedge(body: Mapping[str, Any]) -> Dict[str, float]:
    hedge_delta = _number(body, "hedge_delta")
    if abs(hedge_delta) < 1e-15:
        raise ValueError("hedge_delta must be non-zero")
    portfolio_delta = _number(body, "portfolio_delta")
    units = -portfolio_delta / hedge_delta
    return {"hedge_units": units, "portfolio_delta": portfolio_delta,
            "hedged_delta": portfolio_delta + units * hedge_delta}


def _xva_pfe(body: Mapping[str, Any]) -> Dict[str, Any]:
    result = _exposure_profile({"paths": body.get("exposure_paths", []),
                                "confidence": body.get("confidence", 0.95)})
    return {"pfe": result["pfe"], "expected_exposure": result["expected_exposure"],
            "confidence": result["confidence"]}


def _xva_cva(body: Mapping[str, Any]) -> Dict[str, Any]:
    profile = _exposure_profile({"paths": body.get("exposure_paths", []), "confidence": 0.95})
    recovery = float(body.get("recovery_rate", 0.4))
    if not 0 <= recovery < 1:
        raise ValueError("recovery_rate must be in [0,1)")
    counterparty_hazard = _number(body, "counterparty_spread") / 10000 / (1 - recovery)
    own_hazard = float(body.get("own_spread", 0.0)) / 10000 / (1 - recovery)
    funding_rate = float(body.get("funding_spread", 0.0)) / 10000
    discount_rate = float(body.get("discount_rate", 0.0))
    cva = dva = fva = 0.0
    for time in range(1, len(profile["expected_exposure"])):
        discount = math.exp(-discount_rate * time)
        counterparty_default = math.exp(-counterparty_hazard * (time - 1)) - math.exp(-counterparty_hazard * time)
        own_default = math.exp(-own_hazard * (time - 1)) - math.exp(-own_hazard * time)
        cva += (1 - recovery) * profile["expected_exposure"][time] * counterparty_default * discount
        dva += (1 - recovery) * profile["expected_negative_exposure"][time] * own_default * discount
        fva += funding_rate * profile["expected_exposure"][time] * discount
    return {"cva": cva, "dva": dva, "fva": fva, "total_xva": cva - dva + fva,
            "expected_exposure": profile["expected_exposure"]}


def _equity_greeks(body: Mapping[str, Any]) -> Dict[str, float]:
    spot, strike, time = _positive(body, "S", "spot"), _positive(body, "K", "strike"), _positive(body, "T", "time")
    rate, volatility = _number(body, "r", "risk_free_rate"), _positive(body, "sigma", "volatility")
    dividend = float(body.get("q", body.get("dividend_yield", 0.0)))
    root_t = math.sqrt(time)
    d1 = (math.log(spot / strike) + (rate - dividend + 0.5 * volatility ** 2) * time) / (volatility * root_t)
    d2 = d1 - volatility * root_t
    discount_q, discount_r = math.exp(-dividend * time), math.exp(-rate * time)
    price = spot * discount_q * NORMAL.cdf(d1) - strike * discount_r * NORMAL.cdf(d2)
    delta = discount_q * NORMAL.cdf(d1)
    gamma = discount_q * NORMAL.pdf(d1) / (spot * volatility * root_t)
    vega = spot * discount_q * NORMAL.pdf(d1) * root_t
    theta = (-spot * discount_q * NORMAL.pdf(d1) * volatility / (2 * root_t) -
             rate * strike * discount_r * NORMAL.cdf(d2) + dividend * spot * discount_q * NORMAL.cdf(d1))
    rho = strike * time * discount_r * NORMAL.cdf(d2)
    return {"price": price, "delta": delta, "gamma": gamma, "vega": vega,
            "theta": theta, "rho": rho, "d1": d1, "d2": d2}


def _cross_gamma(body: Mapping[str, Any]) -> Dict[str, float]:
    spot, volatility = _positive(body, "S", "spot"), _positive(body, "sigma", "volatility")
    bump = _positive(body, "bump")
    spot_bump = max(spot * bump, 1e-6)
    vol_bump = max(volatility * bump, 1e-6)
    def price(s_value: float, vol_value: float) -> float:
        return _equity_greeks({**body, "S": s_value, "sigma": vol_value})["price"]
    value = (price(spot + spot_bump, volatility + vol_bump) -
             price(spot + spot_bump, volatility - vol_bump) -
             price(spot - spot_bump, volatility + vol_bump) +
             price(spot - spot_bump, volatility - vol_bump)) / (4 * spot_bump * vol_bump)
    return {"cross_gamma": value, "spot_bump": spot_bump, "volatility_bump": vol_bump}


def _curve_pv(cashflows: Sequence[float], times: Sequence[float], rates: Sequence[float]) -> float:
    if not (len(cashflows) == len(times) == len(rates)):
        raise ValueError("cashflows, times, and curve_rates must match")
    return sum(cashflow * math.exp(-rate * time) for cashflow, time, rate in zip(cashflows, times, rates))


def _key_rate_duration(body: Mapping[str, Any]) -> Dict[str, Any]:
    cashflows, times, rates = _vector(body, "cashflows"), _vector(body, "times"), _vector(body, "curve_rates")
    bump = float(body.get("bump_size", 0.0001))
    if bump <= 0:
        raise ValueError("bump_size must be positive")
    base = _curve_pv(cashflows, times, rates)
    durations = []
    for index in range(len(rates)):
        up, down = list(rates), list(rates)
        up[index] += bump
        down[index] -= bump
        duration = (_curve_pv(cashflows, times, down) - _curve_pv(cashflows, times, up)) / (2 * base * bump)
        durations.append(duration)
    return {"present_value": base, "key_rate_durations": durations, "total_duration": sum(durations)}


def _parallel_shift_sensitivity(body: Mapping[str, Any]) -> Dict[str, float]:
    cashflows, times, rates = _vector(body, "cashflows"), _vector(body, "times"), _vector(body, "curve_rates")
    bump = float(body.get("bump_size", 0.0001))
    base = _curve_pv(cashflows, times, rates)
    up = _curve_pv(cashflows, times, [rate + bump for rate in rates])
    down = _curve_pv(cashflows, times, [rate - bump for rate in rates])
    return {"present_value": base, "pv_change": up - base,
            "duration": (down - up) / (2 * base * bump),
            "convexity": (up + down - 2 * base) / (base * bump * bump)}


def _twist_sensitivity(body: Mapping[str, Any]) -> Dict[str, Any]:
    cashflows, times, rates = _vector(body, "cashflows"), _vector(body, "times"), _vector(body, "curve_rates")
    bump = float(body.get("bump_size", 0.0001))
    center = statistics.mean(times)
    span = max(times) - min(times) or 1.0
    shifts = [bump * (time - center) / span for time in times]
    base = _curve_pv(cashflows, times, rates)
    twisted = _curve_pv(cashflows, times, [rate + shift for rate, shift in zip(rates, shifts)])
    return {"present_value": base, "twisted_present_value": twisted,
            "pv_change": twisted - base, "rate_shifts": shifts}


def _bucket_delta(body: Mapping[str, Any]) -> Dict[str, Any]:
    positions = body.get("positions", [])
    bump = float(body.get("bump_size", 0.0001))
    if not isinstance(positions, list) or not positions or bump <= 0:
        raise ValueError("positions must be non-empty and bump_size positive")
    deltas, base_value = [], 0.0
    for position in positions:
        if not isinstance(position, Mapping):
            raise ValueError("each position must be an object")
        tenor, rate, cashflow = (_positive(position, "tenor"), _number(position, "rate"),
                                 _number(position, "cashflow"))
        base = cashflow * math.exp(-rate * tenor)
        bumped = cashflow * math.exp(-(rate + bump) * tenor)
        base_value += base
        deltas.append({"tenor": tenor, "delta": (bumped - base) / bump, "pv_change": bumped - base})
    return {"present_value": base_value, "bucket_deltas": deltas,
            "total_delta": sum(item["delta"] for item in deltas)}


def _tail_dependence(body: Mapping[str, Any]) -> Dict[str, float]:
    first, second = _vector(body, "returns1"), _vector(body, "returns2")
    if len(first) != len(second) or len(first) < 2:
        raise ValueError("returns1 and returns2 must match and contain at least two values")
    upper_q = float(body.get("threshold_quantile", 0.9))
    if not 0.5 < upper_q < 1:
        raise ValueError("threshold_quantile must be in (0.5,1)")
    lower_q = 1 - upper_q
    first_low, second_low = _quantile(first, lower_q), _quantile(second, lower_q)
    first_high, second_high = _quantile(first, upper_q), _quantile(second, upper_q)
    lower_joint = sum(a <= first_low and b <= second_low for a, b in zip(first, second)) / len(first)
    upper_joint = sum(a >= first_high and b >= second_high for a, b in zip(first, second)) / len(first)
    return {"lower_tail_dependence": min(1.0, lower_joint / lower_q),
            "upper_tail_dependence": min(1.0, upper_joint / (1 - upper_q)),
            "threshold_quantile": upper_q}


def _basel_capital_ratios(body: Mapping[str, Any]) -> Dict[str, Any]:
    assets = _positive(body, "risk_weighted_assets")
    cet1 = _number(body, "cet1_capital") / assets
    tier1 = _number(body, "tier1_capital") / assets
    total = _number(body, "total_capital") / assets
    return {"cet1_ratio": cet1, "tier1_ratio": tier1, "total_capital_ratio": total,
            "basel_minimums_met": {"cet1": cet1 >= 0.045, "tier1": tier1 >= 0.06,
                                   "total": total >= 0.08}}


def _basel_credit_rwa(body: Mapping[str, Any]) -> Dict[str, Any]:
    exposures = body.get("exposures", []); method = str(body.get("method", "standardized")).lower()
    if not isinstance(exposures, list) or not exposures:
        raise ValueError("exposures must be a non-empty list")
    class_weights = {"SOVEREIGN": 0.0, "BANK": 0.5, "CORPORATE": 1.0, "RETAIL": 0.75,
                     "RESIDENTIAL_MORTGAGE": 0.35, "COMMERCIAL_REAL_ESTATE": 1.0,
                     "EQUITY": 2.5, "OTHER": 1.0}
    rating_weights = {"AAA": 0.2, "AA": 0.2, "A": 0.5, "BBB": 1.0,
                      "BB": 1.0, "B": 1.5, "CCC": 1.5}
    rows = []
    for exposure in exposures:
        if not isinstance(exposure, Mapping): raise ValueError("each exposure must be an object")
        amount = _positive(exposure, "amount"); asset_class = str(exposure.get("asset_class", "OTHER")).upper()
        if method == "standardized":
            weight = rating_weights.get(str(exposure.get("rating", "")).upper(),
                                        class_weights.get(asset_class, 1.0))
        elif method == "irb":
            pd = min(0.999, max(1e-6, _positive(exposure, "pd")))
            lgd = min(1.0, max(0.0, _number(exposure, "lgd")))
            maturity = float(exposure.get("maturity", 2.5))
            correlation = 0.12 * (1 - math.exp(-50 * pd)) / (1 - math.exp(-50)) + \
                          0.24 * (1 - (1 - math.exp(-50 * pd)) / (1 - math.exp(-50)))
            capital = lgd * NORMAL.cdf((NORMAL.inv_cdf(pd) + math.sqrt(correlation) * NORMAL.inv_cdf(0.999)) /
                                       math.sqrt(1 - correlation)) - pd * lgd
            adjustment = (1 + (maturity - 2.5) * (0.11852 - 0.05478 * math.log(pd)) ** 2) / \
                         (1 - 1.5 * (0.11852 - 0.05478 * math.log(pd)) ** 2)
            weight = max(0.0, 12.5 * capital * adjustment)
        else:
            raise ValueError("method must be standardized or irb")
        rows.append({"amount": amount, "asset_class": asset_class, "risk_weight": weight,
                     "rwa": amount * weight})
    return {"method": method, "exposures": rows, "total_exposure": sum(row["amount"] for row in rows),
            "total_rwa": sum(row["rwa"] for row in rows)}


def _operational_rwa(body: Mapping[str, Any]) -> Dict[str, float]:
    income = _vector(body, "gross_income_3y")
    positive = [value for value in income if value > 0]
    if not positive: raise ValueError("gross_income_3y must contain positive income")
    indicator = statistics.mean(positive); capital = indicator * 0.15
    return {"business_indicator": indicator, "operational_risk_capital": capital,
            "operational_rwa": capital * 12.5}


def _saccr_ead(body: Mapping[str, Any]) -> Dict[str, float]:
    mtm, addon = _number(body, "mtm_value"), max(0.0, _number(body, "addon"))
    multiplier = float(body.get("alpha", 1.4)); replacement = max(mtm, 0.0)
    return {"replacement_cost": replacement, "potential_future_exposure": addon,
            "alpha": multiplier, "ead": multiplier * (replacement + addon)}


def _ifrs_stage(body: Mapping[str, Any]) -> Dict[str, Any]:
    days = int(body.get("days_past_due", 0)); downgrade = int(body.get("rating_downgrade", 0))
    pd_ratio = float(body.get("pd_increase_ratio", 1.0))
    if days >= 90 or bool(body.get("credit_impaired", False)):
        stage, reason = 3, "credit-impaired or 90+ days past due"
    elif days >= 30 or downgrade >= 2 or pd_ratio >= 2:
        stage, reason = 2, "significant increase in credit risk"
    else:
        stage, reason = 1, "performing"
    return {"stage": stage, "reason": reason, "lifetime_ecl": stage >= 2}


def _ifrs_sicr(body: Mapping[str, Any]) -> Dict[str, Any]:
    original, current = _positive(body, "origination_pd"), _positive(body, "current_pd")
    ratio = current / original; threshold = float(body.get("threshold_ratio", 2.0))
    return {"pd_increase_ratio": ratio, "absolute_increase": current - original,
            "threshold_ratio": threshold, "sicr": ratio >= threshold}


def _ecl_12m(body: Mapping[str, Any]) -> Dict[str, float]:
    pd = _number(body, "pd_12m"); lgd = _number(body, "lgd"); ead = _positive(body, "ead")
    if not 0 <= pd <= 1 or not 0 <= lgd <= 1: raise ValueError("pd_12m and lgd must be in [0,1]")
    loss = pd * lgd * ead
    return {"expected_credit_loss": loss, "pd": pd, "lgd": lgd, "ead": ead}


def _ecl_lifetime(body: Mapping[str, Any]) -> Dict[str, Any]:
    pd_curve, ead_curve, rates = (_vector(body, "pd_curve"), _vector(body, "ead_curve"),
                                  _vector(body, "discount_rates"))
    if len(pd_curve) != len(ead_curve) or len(pd_curve) != len(rates):
        raise ValueError("pd_curve, ead_curve, and discount_rates must match")
    lgd = _number(body, "lgd")
    if not 0 <= lgd <= 1 or any(not 0 <= value <= 1 for value in pd_curve):
        raise ValueError("probabilities and lgd must be in [0,1]")
    losses = [pd * lgd * ead / (1 + rate) ** (index + 1)
              for index, (pd, ead, rate) in enumerate(zip(pd_curve, ead_curve, rates))]
    return {"expected_credit_loss": sum(losses), "period_losses": losses}


def _liquidity_lcr(body: Mapping[str, Any]) -> Dict[str, float]:
    level1 = max(0.0, _number(body, "hqla_level1")); level2a = max(0.0, _number(body, "hqla_level2a"))
    hqla = level1 + 0.85 * min(level2a, level1 * 2 / 3 if level1 else level2a)
    outflows = 0.05 * max(0.0, _number(body, "retail_deposits_stable")) + \
               0.10 * max(0.0, _number(body, "retail_deposits_less_stable")) + \
               0.40 * max(0.0, _number(body, "unsecured_wholesale"))
    inflows = max(0.0, _number(body, "inflows")); net = max(outflows - min(inflows, 0.75 * outflows), 1e-12)
    return {"hqla": hqla, "total_outflows": outflows, "net_cash_outflows": net,
            "lcr": hqla / net, "minimum_met": hqla / net >= 1.0}


def _liquidity_nsfr(body: Mapping[str, Any]) -> Dict[str, float]:
    available = (_number(body, "capital") + 0.95 * _number(body, "stable_deposits_retail") +
                 0.90 * _number(body, "less_stable_deposits_retail") +
                 0.50 * _number(body, "wholesale_funding"))
    required = (0.05 * _number(body, "securities_level1") + 0.85 * _number(body, "loans_retail") +
                0.85 * _number(body, "loans_corporate") + _number(body, "other_assets"))
    ratio = available / required if required > 0 else math.inf
    return {"available_stable_funding": available, "required_stable_funding": required,
            "nsfr": ratio, "minimum_met": ratio >= 1.0}


def _capital_projection(body: Mapping[str, Any]) -> Dict[str, Any]:
    earnings, losses, rwa_changes = (_vector(body, "earnings"), _vector(body, "losses"),
                                      _vector(body, "rwa_changes"))
    if len(earnings) != len(losses) or len(losses) != len(rwa_changes):
        raise ValueError("earnings, losses, and rwa_changes must match")
    capital, rwa = _number(body, "initial_capital"), _positive(body, "initial_rwa")
    periods = []
    for index, (earning, loss, change) in enumerate(zip(earnings, losses, rwa_changes), 1):
        capital += earning - loss; rwa += change
        periods.append({"period": index, "capital": capital, "rwa": rwa,
                        "capital_ratio": capital / rwa})
    return {"periods": periods, "ending_capital": capital, "ending_rwa": rwa,
            "ending_capital_ratio": capital / rwa,
            "minimum_capital_ratio": min(item["capital_ratio"] for item in periods)}


Route = Callable[[Mapping[str, Any]], Dict[str, Any]]
ROUTES: Dict[str, Route] = {
    "core/types/money/create": _money_create,
    "core/types/money/convert": _money_convert,
    "core/types/rate/convert": _rate_convert,
    "core/types/spread/from-bps": _spread_from_bps,
    "core/types/tenor/add-to-date": _add_tenor,
    "core/types/notional-schedule": _notional_schedule,
    "core/types/currencies": _currencies,
    "core/types/frequencies": _frequencies,
    "core/conventions/parse-date": _parse_date,
    "core/conventions/format-date": _format_date,
    "core/conventions/days-to-years": _days_to_years,
    "core/conventions/years-to-days": _years_to_days,
    "core/conventions/normalize-rate": _normalize_rate,
    "core/conventions/normalize-volatility": _normalize_volatility,
    "core/ops/black-scholes": _bs_price,
    "core/ops/black76": _black76_price,
    "core/ops/forward-rate": _forward_rate,
    "core/ops/discount-cashflows": _discount_cashflows,
    "core/ops/interpolate": _core_interpolate,
    "core/ops/statistics": _statistics,
    "core/ops/var": _historical_var,
    "core/ops/percentile": _percentile,
    "core/ops/covariance-matrix": _covariance_matrix,
    "core/ops/cholesky": _cholesky,
    "core/ops/gbm-paths": _core_gbm_paths,
    "core/ops/zero-rate-convert": _zero_rate_convert,
    "core/autodiff/dual-eval": _dual_eval,
    "core/autodiff/gradient": _autodiff_gradient,
    "core/autodiff/taylor-expand": _taylor_expand,
    "core/distributions/normal/cdf": lambda body: _normal(body, "cdf"),
    "core/distributions/normal/pdf": lambda body: _normal(body, "pdf"),
    "core/distributions/normal/ppf": lambda body: _normal(body, "ppf"),
    "core/distributions/t/cdf": lambda body: _student_t(body, "cdf"),
    "core/distributions/t/pdf": lambda body: _student_t(body, "pdf"),
    "core/distributions/t/ppf": lambda body: _student_t(body, "ppf"),
    "core/distributions/chi2/cdf": lambda body: _chi_square(body, "cdf"),
    "core/distributions/chi2/pdf": lambda body: _chi_square(body, "pdf"),
    "core/distributions/gamma/cdf": lambda body: _gamma_distribution(body, "cdf"),
    "core/distributions/gamma/pdf": lambda body: _gamma_distribution(body, "pdf"),
    "core/distributions/exponential/cdf": lambda body: _exponential_distribution(body, "cdf"),
    "core/distributions/exponential/pdf": lambda body: _exponential_distribution(body, "pdf"),
    "core/distributions/exponential/ppf": lambda body: _exponential_distribution(body, "ppf"),
    "core/distributions/bivariate-normal/cdf": _bivariate_normal_cdf,
    "core/math/eval": _core_math_eval,
    "core/math/two-arg": _core_math_two_arg,
    "core/legs/fixed": _fixed_leg,
    "core/legs/float": _float_leg,
    "core/legs/zero-coupon": _zero_coupon_leg,
    "core/periods/day-count-fraction": _core_day_count,
    "core/periods/fixed-coupon": lambda body: _coupon_period(body, floating=False),
    "core/periods/float-coupon": lambda body: _coupon_period(body, floating=True),
    "scheduling/adjustment/adjust-date": _schedule_adjust,
    "scheduling/adjustment/batch-adjust": _schedule_batch_adjust,
    "scheduling/adjustment/methods": _schedule_methods,
    "scheduling/calendar/add-business-days": _calendar_add_business_days,
    "scheduling/calendar/business-days-between": _calendar_business_days_between,
    "scheduling/calendar/is-business-day": _calendar_is_business,
    "scheduling/calendar/list": _calendar_list,
    "scheduling/calendar/next-business-day": lambda body: _calendar_step(body, 1),
    "scheduling/calendar/previous-business-day": lambda body: _calendar_step(body, -1),
    "scheduling/daycount/batch-year-fraction": _batch_year_fraction,
    "scheduling/daycount/conventions": _day_count_conventions,
    "scheduling/daycount/day-count": _day_count,
    "scheduling/daycount/year-fraction": _day_count_fraction,
    "scheduling/schedule/generate": _schedule_generate,
    "numerical/differentiation/derivative": _numerical_derivative,
    "numerical/differentiation/gradient": _numerical_gradient,
    "numerical/differentiation/hessian": _numerical_hessian,
    "numerical/fft/convolve": _convolve,
    "numerical/fft/forward": lambda body: _fft(body, inverse=False),
    "numerical/fft/inverse": lambda body: _fft(body, inverse=True),
    "numerical/integration/monte-carlo": _integrate,
    "numerical/integration/quadrature": _integrate,
    "numerical/integration/stratified": _integrate,
    "numerical/interpolation/evaluate": _interpolation_evaluate,
    "numerical/interpolation/spline-curve": _spline_curve,
    "numerical/interpolation/spline-derivative": _spline_derivative,
    "numerical/least-squares/fit": _least_squares_fit,
    "numerical/linalg/decompose": _linalg_decompose,
    "numerical/linalg/dot": _linalg_dot,
    "numerical/linalg/inverse": _linalg_inverse,
    "numerical/linalg/lstsq": _linalg_lstsq,
    "numerical/linalg/matmul": _linalg_matmul,
    "numerical/linalg/matvec": _linalg_matvec,
    "numerical/linalg/norm": _linalg_norm,
    "numerical/linalg/outer": _linalg_outer,
    "numerical/linalg/solve": _linalg_solve,
    "numerical/linalg/transpose": _linalg_transpose,
    "numerical/ode/solve": _ode_solve,
    "numerical/optimize/minimize": _optimize_minimize,
    "numerical/roots/find-1d": _root_bisection,
    "numerical/roots/find-nd": _root_nd,
    "numerical/roots/newton": _root_newton,
    "pricing/bs/price": _bs_price,
    "pricing/bs/greeks": _bs_greeks,
    "pricing/bs/greeks-full": _bs_greeks,
    "pricing/bs/implied-vol": _implied_vol,
    "pricing/bs/digital-call": lambda body: _bs_binary({**body, "option_type": "call"}),
    "pricing/bs/digital-put": lambda body: _bs_binary({**body, "option_type": "put"}),
    "pricing/bs/asset-or-nothing-call": lambda body: _bs_binary({**body, "option_type": "call"}, asset=True),
    "pricing/bs/asset-or-nothing-put": lambda body: _bs_binary({**body, "option_type": "put"}, asset=True),
    "pricing/black76/price": _black76_price,
    "pricing/black76/greeks": lambda body: _bs_values(body, black76=True),
    "pricing/black76/greeks-full": lambda body: _bs_values(body, black76=True),
    "pricing/black76/implied-vol": lambda body: _implied_vol(body, black76=True),
    "pricing/black76/caplet": lambda body: _black76_rate_option(body, "caplet"),
    "pricing/black76/floorlet": lambda body: _black76_rate_option(body, "floorlet"),
    "pricing/black76/swaption": lambda body: _black76_rate_option(body, "swaption"),
    "pricing/binomial/european": lambda body: _binomial(body, american=False),
    "pricing/binomial/american": lambda body: _binomial(body, american=True),
    "pricing/binomial/bermudan": _bermudan,
    "pricing/binomial/barrier": _barrier_option,
    "pricing/bachelier/price": lambda body: {"price": _bachelier_values(body)["price"]},
    "pricing/bachelier/greeks": _bachelier_values,
    "pricing/bachelier/greeks-full": _bachelier_values,
    "pricing/bachelier/implied-vol": _bachelier_implied_vol,
    "pricing/bachelier/shifted-lognormal": _shifted_lognormal,
    "pricing/bachelier/vol-conversion": _vol_conversion,
    "pricing/kirk/spread-price": _kirk_values,
    "pricing/kirk/spread-greeks": _kirk_greeks,
    "pricing/margrabe": _margrabe,
    "pricing/basket-levy": _basket_levy,
    "solver/finance/discount-factor": _discount_factor,
    "solver/finance/zero-rate": _zero_rate,
    "solver/finance/forward-rate": _forward_rate,
    "solver/finance/basis": _basis,
    "solver/finance/carry": _carry,
    "solver/finance/implied-repo-rate": _carry,
    "solver/finance/i-spread": lambda body: _spread(body, "bond_yield", "swap_rate", "i_spread"),
    "solver/finance/g-spread": lambda body: _spread(body, "bond_yield", "govt_yield", "g_spread"),
    "solver/finance/bond-yield": _solver_bond_yield,
    "solver/finance/duration": _solver_duration,
    "solver/finance/modified-duration": _solver_modified_duration,
    "solver/finance/convexity": _solver_convexity,
    "solver/finance/convexity-adjustment": _solver_convexity_adjustment,
    "solver/finance/pv01": _solver_pv01,
    "solver/finance/implied-vol": lambda body: _solver_implied_vol(body, black76=False),
    "solver/finance/implied-vol-black76": lambda body: _solver_implied_vol(body, black76=True),
    "solver/finance/par-rate": _solver_par_rate,
    "solver/finance/z-spread": _solver_z_spread,
    "solver/finance/oas": _solver_oas,
    "solver/finance/asw-spread": _solver_asw,
    "solver/finance/forward-futures-conversion": _solver_forward_futures,
    "solver/finance/irr": lambda body: _solver_return(body, "irr"),
    "solver/finance/xirr": lambda body: _solver_return(body, "xirr"),
    "solver/bootstrap/curve": _solver_bootstrap_curve,
    "solver/calibration/vasicek": _solver_vasicek,
    "risk/var/historical": _historical_var,
    "curves/build": _curve_build,
    "curves/zero-rate": _curve_zero,
    "curves/forward-rate": _curve_forward,
    "curves/discount-factor": _curve_discount,
    "curves/curve-points": _curve_points,
    "curves/interpolate": _interpolate,
    "curves/interpolate-derivative": _interpolate_derivative,
    "curves/instantaneous-forward": _instantaneous_forward,
    "curves/parallel-shift": _curve_shift,
    "curves/twist": _curve_twist,
    "curves/butterfly": _curve_butterfly,
    "curves/key-rate-shift": _key_rate_shift,
    "curves/nelson-siegel/fit": _nelson_siegel_fit,
    "curves/nelson-siegel/evaluate": _nelson_siegel_evaluate,
    "curves/nss/fit": _nss_fit,
    "curves/nss/evaluate": _nss_evaluate,
    "curves/roll": _curve_roll,
    "curves/scale": _curve_scale,
    "curves/time-shift": _curve_time_shift,
    "curves/composite": _curve_composite,
    "curves/proxy": _curve_proxy,
    "curves/real-rate": _real_rate_curve,
    "curves/monotonicity-check": _monotonicity_check,
    "curves/smoothness-penalty": _smoothness_penalty,
    "curves/constrained-fit": _constrained_curve_fit,
    "curves/multicurve/setup": _multicurve_setup,
    "curves/multicurve/basis-spread": _multicurve_basis,
    "curves/cross-currency-basis": _cross_currency_basis,
    "curves/inflation/build": lambda body: _inflation_curve(body, bootstrap=False),
    "curves/inflation/bootstrap": lambda body: _inflation_curve(body, bootstrap=True),
    "curves/inflation/seasonality": _inflation_seasonality,
    "instruments/bond/fixed/price": _bond_price,
    "instruments/bond/fixed/yield": _bond_yield,
    "instruments/bond/fixed/analytics": _bond_metrics,
    "instruments/bond/fixed/cashflows": _bond_cashflows,
    "instruments/bond/zero-coupon/price": _zero_coupon_bond,
    "instruments/bond/inflation-linked": _inflation_linked_bond,
    "instruments/swap/irs/value": _swap_metrics,
    "instruments/swap/irs/par-rate": _swap_metrics,
    "instruments/swap/irs/dv01": _swap_metrics,
    "instruments/cds/hazard-rate": _cds_hazard,
    "instruments/cds/survival-probability": _cds_survival,
    "instruments/cds/value": _cds_value,
    "instruments/fx/forward": _fx_forward,
    "instruments/fx/garman-kohlhagen": _garman_kohlhagen,
    "instruments/fra/value": _fra_values,
    "instruments/fra/break-even": _fra_values,
    "instruments/ois/value": _swap_metrics,
    "instruments/ois/build-curve": _ois_curve,
    "instruments/money-market/deposit": _money_market_deposit,
    "instruments/money-market/repo": _money_market_repo,
    "instruments/money-market/tbill": _money_market_tbill,
    "instruments/commodity/future": _commodity_future,
    "instruments/futures/stir": _stir_future,
    "instruments/futures/bond/ctd": _bond_future_ctd,
    "instruments/equity/variance-swap": _variance_swap,
    "instruments/equity/volatility-swap": _volatility_swap,
    "portfolio/optimize/min-variance": _min_variance,
    "portfolio/optimize/max-sharpe": _max_sharpe,
    "portfolio/optimize/efficient-frontier": _efficient_frontier,
    "portfolio/optimize/target-return": _target_return_portfolio,
    "portfolio/black-litterman/equilibrium": _black_litterman_equilibrium,
    "portfolio/black-litterman/posterior": _black_litterman_posterior,
    "portfolio/risk-parity": _risk_parity,
    "portfolio/risk/inverse-volatility": _inverse_volatility,
    "portfolio/risk/var": _portfolio_var,
    "portfolio/risk/cvar": _portfolio_var,
    "portfolio/risk/contribution": _risk_contribution,
    "portfolio/risk/ratios": _risk_ratios,
    "portfolio/risk/incremental-var": _portfolio_incremental_var,
    "portfolio/risk/comprehensive": _portfolio_risk_comprehensive,
    "portfolio/risk/portfolio-comprehensive": _portfolio_comprehensive,
    "risk/var/parametric": _parametric_var,
    "risk/var/component": _factor_component_var,
    "risk/var/incremental": lambda body: _incremental_historical_var(body, marginal=False),
    "risk/var/marginal": lambda body: _incremental_historical_var(body, marginal=True),
    "risk/var/es-optimization": _expected_shortfall_optimization,
    "risk/backtest": _var_backtest,
    "risk/stress/scenario": _stress_scenario,
    "risk/correlation-stress": _correlation_stress,
    "risk/tail-risk/comprehensive": _tail_risk_comprehensive,
    "risk/evt/gpd": _evt_gpd,
    "risk/evt/gev": _evt_gev,
    "risk/evt/hill": _evt_hill,
    "risk/copula/sample": _copula_sample,
    "risk/portfolio-risk/exposure-profile": _exposure_profile,
    "risk/portfolio-risk/optimal-hedge": _optimal_hedge,
    "risk/xva/cva": _xva_cva,
    "risk/xva/pfe": _xva_pfe,
    "risk/sensitivities/greeks": _equity_greeks,
    "risk/sensitivities/cross-gamma": _cross_gamma,
    "risk/sensitivities/key-rate-duration": _key_rate_duration,
    "risk/sensitivities/parallel-shift": _parallel_shift_sensitivity,
    "risk/sensitivities/twist": _twist_sensitivity,
    "risk/sensitivities/bucket-delta": _bucket_delta,
    "risk/tail-risk/tail-dependence": _tail_dependence,
    "stochastic/gbm/simulate": _gbm,
    "stochastic/gbm/properties": _gbm_properties,
    "stochastic/ou/simulate": _ou,
    "stochastic/cir/simulate": _cir_simulate,
    "stochastic/cir/bond-price": _cir_bond,
    "stochastic/heston/simulate": _heston_simulate,
    "stochastic/merton/simulate": _merton_simulate,
    "stochastic/vasicek/simulate": _vasicek_simulate,
    "stochastic/vasicek/bond-price": _vasicek_bond,
    "stochastic/wiener/simulate": _wiener_simulate,
    "stochastic/poisson/simulate": _poisson_simulate,
    "stochastic/variance-gamma/simulate": _variance_gamma_simulate,
    "stochastic/brownian-bridge/simulate": _brownian_bridge,
    "stochastic/correlated-bm/simulate": _correlated_bm,
    "stochastic/exact/gbm": _exact_terminal_gbm,
    "stochastic/exact/ou": _exact_terminal_ou,
    "stochastic/exact/cir": _exact_terminal_cir,
    "stochastic/exact/heston": _heston_simulate,
    "stochastic/simulation/euler-maruyama": lambda body: _geometric_scheme(body, milstein=False),
    "stochastic/simulation/milstein": lambda body: _geometric_scheme(body, milstein=True),
    "stochastic/simulation/euler-maruyama-nd": lambda body: _nd_scheme(body, milstein=False),
    "stochastic/simulation/milstein-nd": lambda body: _nd_scheme(body, milstein=True),
    "stochastic/simulation/multilevel-mc": _multilevel_mc,
    "stochastic/sampling/sobol": _sobol_samples,
    "stochastic/sampling/antithetic": _antithetic_samples,
    "stochastic/sampling/correlated-normals": _correlated_normals,
    "stochastic/sampling/multivariate-normal": _multivariate_normal,
    "stochastic/sampling/distribution": _distribution_samples,
    "stochastic/sampling/jump": _jump_samples,
    "stochastic/theory/ito-lemma": _ito_lemma,
    "stochastic/theory/ito-product-rule": _ito_product,
    "stochastic/theory/quadratic-variation": _quadratic_variation,
    "stochastic/theory/covariation": _covariation,
    "stochastic/theory/martingale-test": _martingale_test,
    "stochastic/theory/girsanov/measure-change": _girsanov_measure_change,
    "stochastic/theory/girsanov/risk-neutral-drift": _risk_neutral_drift,
    "volatility/surface/flat": _flat_vol,
    "volatility/surface/from-points": _vol_from_points,
    "volatility/surface/grid": _vol_grid,
    "volatility/surface/smile": _vol_smile,
    "volatility/surface/term-structure": _vol_term,
    "volatility/surface/total-variance": _total_variance,
    "volatility/local-vol/constant": _flat_vol,
    "volatility/local-vol/implied-to-local": _implied_to_local,
    "volatility/sabr/implied-vol": _sabr,
    "volatility/sabr/calibrate": _sabr_calibrate,
    "volatility/sabr/smile": _sabr_smile,
    "volatility/sabr/normal-vol": _sabr_normal_vol,
    "volatility/sabr/density": _sabr_density,
    "volatility/sabr/dynamics": _sabr_dynamics,
    "models/short-rate/bond-price": _short_rate_bond_prices,
    "models/short-rate/bond-option": _short_rate_bond_option,
    "models/short-rate/yield-curve": _short_rate_yield_curve,
    "models/short-rate/monte-carlo": _short_rate_monte_carlo,
    "models/hull-white/calibrate": _hull_white_calibrate,
    "models/heston/price": _heston_price,
    "models/heston/monte-carlo": _heston_monte_carlo,
    "models/heston/implied-vol": _heston_implied_vol,
    "models/merton/price": _merton_price,
    "models/merton/fft": _merton_price,
    "models/kou/price": _kou_price,
    "models/dupire/price": _dupire_price,
    "models/svi/calibrate": _svi_calibrate,
    "models/variance-gamma/price": _variance_gamma_price,
    "economics/equilibrium/cobb-douglas": _cobb_douglas,
    "economics/equilibrium/ces": _ces_demand,
    "economics/equilibrium/walrasian": _exchange_equilibrium,
    "economics/equilibrium/exchange-economy": _exchange_equilibrium,
    "economics/games/create": _game_create,
    "economics/games/classic": _classic_game,
    "economics/games/mixed-nash": _mixed_nash,
    "economics/games/nash-check": _nash_check,
    "economics/games/best-response": _best_response,
    "economics/games/eliminate-dominated": _eliminate_dominated,
    "economics/games/fictitious-play": _fictitious_play,
    "economics/auctions/run": _auction_run,
    "economics/auctions/simulate": _auction_simulate,
    "economics/auctions/equilibrium-bid": _auction_equilibrium_bid,
    "economics/auctions/expected-revenue": _auction_expected_revenue,
    "economics/utility/cara": lambda body: _single_utility(body, "cara"),
    "economics/utility/crra": lambda body: _single_utility(body, "crra"),
    "economics/utility/log": lambda body: _single_utility(body, "log"),
    "economics/utility/quadratic": lambda body: _single_utility(body, "quadratic"),
    "economics/utility/expected-utility": _expected_utility,
    "economics/utility/certainty-equivalent": _certainty_equivalent,
    "economics/utility/certainty-equivalent-approximation": _certainty_equivalent_approximation,
    "economics/utility/risk-premium": _risk_premium,
    "economics/utility/prospect-theory": _prospect_theory,
    "economics/utility/stochastic-dominance": _stochastic_dominance,
    "physics/entropy/shannon": _shannon_entropy,
    "physics/entropy/renyi": _renyi_entropy,
    "physics/entropy/tsallis": _tsallis_entropy,
    "physics/entropy/cross": _cross_entropy,
    "physics/entropy/conditional": _conditional_entropy,
    "physics/entropy/joint": _joint_entropy,
    "physics/entropy/differential": _differential_entropy,
    "physics/entropy/mutual-information": _mutual_information,
    "physics/entropy/transfer": _transfer_entropy,
    "physics/entropy/fisher-information": _fisher_information,
    "physics/entropy/markov-rate": _markov_entropy_rate,
    "physics/divergence/kl": _kl_divergence,
    "physics/divergence/js": _js_divergence,
    "physics/boltzmann": _boltzmann_distribution,
    "physics/max-entropy": _maximum_entropy,
    "physics/ising": _ising_simulation,
    "physics/ising/critical-temperature": _ising_critical_temperature,
    "physics/thermodynamics/ideal-gas": _ideal_gas,
    "physics/thermodynamics/van-der-waals": _van_der_waals,
    "physics/thermodynamics/carnot": _carnot,
    "physics/thermodynamics/free-energy": _free_energy,
    "physics/thermodynamics/clausius-clapeyron": _clausius_clapeyron,
    "physics/thermodynamics/maxwell-relations": _maxwell_relations,
    "physics/thermodynamics/joule-thomson": _joule_thomson,
    "statistics/distributions/normal/cdf": lambda body: _statistics_normal(body, "cdf"),
    "statistics/distributions/normal/pdf": lambda body: _statistics_normal(body, "pdf"),
    "statistics/distributions/normal/ppf": lambda body: _statistics_normal(body, "ppf"),
    "statistics/distributions/normal/properties": lambda body: _statistics_normal(body, "properties"),
    "statistics/distributions/lognormal/cdf": lambda body: _statistics_lognormal(body, "cdf"),
    "statistics/distributions/lognormal/pdf": lambda body: _statistics_lognormal(body, "pdf"),
    "statistics/distributions/lognormal/ppf": lambda body: _statistics_lognormal(body, "ppf"),
    "statistics/distributions/lognormal/properties": lambda body: _statistics_lognormal(body, "properties"),
    "statistics/distributions/student-t/cdf": lambda body: _student_t(body, "cdf"),
    "statistics/distributions/student-t/pdf": lambda body: _student_t(body, "pdf"),
    "statistics/distributions/student-t/properties": _student_properties,
    "statistics/distributions/chi-squared/cdf": lambda body: _chi_square(body, "cdf"),
    "statistics/distributions/chi-squared/pdf": lambda body: _chi_square(body, "pdf"),
    "statistics/distributions/chi-squared/properties": _chi_square_properties,
    "statistics/distributions/f/pdf": lambda body: _f_distribution(body, "pdf"),
    "statistics/distributions/f/properties": lambda body: _f_distribution(body, "properties"),
    "statistics/distributions/gamma/cdf": lambda body: _gamma_distribution(body, "cdf"),
    "statistics/distributions/gamma/pdf": lambda body: _gamma_distribution(body, "pdf"),
    "statistics/distributions/gamma/properties": _gamma_properties,
    "statistics/distributions/beta/cdf": lambda body: _beta_distribution(body, "cdf"),
    "statistics/distributions/beta/pdf": lambda body: _beta_distribution(body, "pdf"),
    "statistics/distributions/beta/properties": lambda body: _beta_distribution(body, "properties"),
    "statistics/distributions/exponential/cdf": lambda body: _exponential_distribution(body, "cdf"),
    "statistics/distributions/exponential/pdf": lambda body: _exponential_distribution(body, "pdf"),
    "statistics/distributions/exponential/ppf": lambda body: _exponential_distribution(body, "ppf"),
    "statistics/distributions/exponential/properties": _exponential_properties,
    "statistics/distributions/poisson/cdf": lambda body: _poisson_distribution(body, "cdf"),
    "statistics/distributions/poisson/pmf": lambda body: _poisson_distribution(body, "pmf"),
    "statistics/distributions/poisson/properties": lambda body: _poisson_distribution(body, "properties"),
    "statistics/distributions/binomial/cdf": lambda body: _binomial_distribution(body, "cdf"),
    "statistics/distributions/binomial/pmf": lambda body: _binomial_distribution(body, "pmf"),
    "statistics/distributions/binomial/properties": lambda body: _binomial_distribution(body, "properties"),
    "statistics/distributions/geometric/cdf": lambda body: _geometric_distribution(body, "cdf"),
    "statistics/distributions/geometric/pmf": lambda body: _geometric_distribution(body, "pmf"),
    "statistics/distributions/geometric/ppf": lambda body: _geometric_distribution(body, "ppf"),
    "statistics/distributions/geometric/properties": lambda body: _geometric_distribution(body, "properties"),
    "statistics/distributions/hypergeometric/cdf": lambda body: _hypergeometric_distribution(body, "cdf"),
    "statistics/distributions/hypergeometric/pmf": lambda body: _hypergeometric_distribution(body, "pmf"),
    "statistics/distributions/hypergeometric/properties": lambda body: _hypergeometric_distribution(body, "properties"),
    "statistics/distributions/negative-binomial/cdf": lambda body: _negative_binomial_distribution(body, "cdf"),
    "statistics/distributions/negative-binomial/pmf": lambda body: _negative_binomial_distribution(body, "pmf"),
    "statistics/distributions/negative-binomial/properties": lambda body: _negative_binomial_distribution(body, "properties"),
    "statistics/distributions/pgf": _probability_generating_function,
    "statistics/timeseries/ar/fit": _ar_fit,
    "statistics/timeseries/ar/forecast": _ar_forecast,
    "statistics/timeseries/arima/fit": _arima_fit,
    "statistics/timeseries/arima/forecast": _arima_forecast,
    "statistics/timeseries/ma/fit": _ma_fit,
    "statistics/timeseries/garch/fit": lambda body: _garch_core(body, "garch"),
    "statistics/timeseries/garch/forecast": _garch_forecast,
    "statistics/timeseries/egarch/fit": lambda body: _garch_core(body, "egarch"),
    "statistics/timeseries/gjr-garch/fit": lambda body: _garch_core(body, "gjr-garch"),
    "ml/credit/scorecard": _credit_scorecard,
    "ml/credit/logistic-regression": _logistic_fit,
    "ml/credit/woe-binning": _woe_binning,
    "ml/credit/calibration": _calibration_metrics,
    "ml/credit/discrimination": _discrimination_metrics,
    "ml/credit/performance": _credit_performance,
    "ml/credit/migration": _credit_migration,
    "ml/credit/beta-lgd": lambda body: _beta_lgd(body, "lgd"),
    "ml/credit/two-stage-lgd": _two_stage_lgd,
    "ml/regression/fit": _linear_regression,
    "ml/regression/ensemble": _regression_ensemble,
    "ml/regression/tree": _regression_stump,
    "ml/regression/lgd": lambda body: _beta_lgd(body, "y"),
    "ml/regression/ead": _linear_regression,
    "ml/clustering/kmeans": _kmeans,
    "ml/clustering/hierarchical": _hierarchical_clustering,
    "ml/clustering/dbscan": _dbscan,
    "ml/clustering/pca": _pca,
    "ml/clustering/isolation-forest": _isolation_scores,
    "ml/preprocessing/scale": _scale_features,
    "ml/preprocessing/transform": _transform_features,
    "ml/preprocessing/outliers": _outlier_detection,
    "ml/preprocessing/winsorize": _winsorize,
    "ml/preprocessing/stationarity": _stationarity,
    "ml/features/lags": _lag_features,
    "ml/features/rolling": _rolling_features,
    "ml/features/calendar": _calendar_features,
    "ml/features/technical": _technical_features,
    "ml/features/financial-ratios": _financial_ratio_features,
    "ml/features/cross-sectional": _cross_sectional_features,
    "ml/validation/stability": _population_stability,
    "ml/validation/discrimination-report": _discrimination_metrics,
    "ml/validation/calibration-report": _calibration_report,
    "ml/validation/interpretability": _interpretability,
    "ml/timeseries/feature-importance": _timeseries_feature_importance,
    "ml/changepoint/detect": _changepoint,
    "ml/gp/curve": lambda body: _curve_surrogate(body, "local-kernel-interpolation"),
    "ml/gp/vol-surface": lambda body: _surface_surrogate(body, "local-kernel-interpolation"),
    "ml/nn/curve": lambda body: _curve_surrogate(body, "local-linear-surrogate"),
    "ml/nn/vol-surface": lambda body: _surface_surrogate(body, "local-bilinear-surrogate"),
    "ml/nn/portfolio": _ml_portfolio,
    "ml/hmm/fit": _hmm_fit,
    "ml/garch-hybrid": _garch_hybrid,
    "ml/metrics/regression": _regression_metrics,
    "ml/metrics/classification": _classification_metrics,
    "ml/factor/statistical": _statistical_factors,
    "ml/factor/cross-sectional": _cross_sectional_factors,
    "ml/covariance/estimate": _covariance_estimate,
    "regulatory/basel/capital-ratios": _basel_capital_ratios,
    "regulatory/basel/credit-rwa": _basel_credit_rwa,
    "regulatory/basel/operational-rwa": _operational_rwa,
    "regulatory/saccr/ead": _saccr_ead,
    "regulatory/ifrs9/stage-assessment": _ifrs_stage,
    "regulatory/ifrs9/sicr": _ifrs_sicr,
    "regulatory/ifrs9/ecl-12m": _ecl_12m,
    "regulatory/ifrs9/ecl-lifetime": _ecl_lifetime,
    "regulatory/liquidity/lcr": _liquidity_lcr,
    "regulatory/liquidity/nsfr": _liquidity_nsfr,
    "regulatory/stress/capital-projection": _capital_projection,
    "analysis/ratios/liquidity/current-ratio": lambda body: _ratio(body, "current_assets", "current_liabilities"),
    "analysis/ratios/liquidity/quick-ratio": _quick_ratio,
    "analysis/ratios/profitability/roe": lambda body: _ratio(body, "net_income", "equity"),
    "analysis/ratios/valuation/pe": _pe_ratio,
    "analysis/ratios/growth/cagr": _cagr,
    "analysis/valuation/dcf/wacc": _wacc,
    "analysis/valuation/dcf/terminal-value": _terminal_value,
    "analysis/valuation/dcf/fcff": _dcf_fcff,
    "analysis/valuation/dcf/ddm": _dividend_discount,
    "analysis/valuation/dcf/gordon-growth": _dividend_discount,
    "analysis/valuation/dcf/two-stage": _dcf_fcff,
    "analysis/valuation/dcf/cost-of-equity": _cost_of_equity,
    "analysis/valuation/comparable": _comparable_valuation,
    "analysis/valuation/screen": _valuation_screen,
    "analysis/valuation/factor-models": _factor_models,
    "analysis/valuation/credit/spread-from-pd": _credit_spread_from_pd,
    "analysis/valuation/credit/expected-loss": _expected_credit_loss,
    "analysis/valuation/credit/rating-pd": _rating_pd,
    "analysis/valuation/credit/merton-model": _merton_model,
    "analysis/valuation/credit/distance-to-default": _distance_to_default,
    "analysis/valuation/predictive/altman-z": _altman_z,
    "analysis/valuation/predictive/piotroski-f": _piotroski_f,
    "analysis/valuation/predictive/beneish-m": _beneish_m,
    "analysis/valuation/predictive/ohlson-o": _ohlson_o,
    "analysis/valuation/predictive/springate": _springate,
    "analysis/valuation/predictive/zmijewski": _zmijewski,
    "analysis/valuation/residual-income/eva": _economic_value_added,
    "analysis/valuation/residual-income/ri": _residual_income,
    "analysis/valuation/residual-income/valuation": _residual_income_valuation,
    "analysis/valuation/startup/vc-method": _venture_capital_valuation,
    "analysis/valuation/startup/first-chicago": _first_chicago,
    "analysis/valuation/startup/dilution": _startup_dilution,
    "analysis/valuation/startup/berkus": _berkus,
    "analysis/valuation/segment/sotp": _segment_sotp,
    "analysis/valuation/proforma/adjustments": _proforma_adjustments,
    "analysis/industry/banking": _banking_analytics,
    "analysis/industry/insurance": _insurance_analytics,
    "analysis/industry/reits": _reit_analytics,
    "analysis/industry/utilities": _utilities_analytics,
    "analysis/fundamentals/profitability": _fundamental_profitability,
    "analysis/fundamentals/liquidity": _fundamental_liquidity,
    "analysis/fundamentals/efficiency": _fundamental_efficiency,
    "analysis/fundamentals/growth": _fundamental_growth,
    "analysis/fundamentals/solvency": _fundamental_solvency,
    "analysis/fundamentals/cashflow": _fundamental_cashflow,
    "analysis/fundamentals/comprehensive": _fundamental_comprehensive,
    "analysis/fundamentals/dupont": _fundamental_dupont,
    "analysis/fundamentals/quality": _fundamental_quality,
    "analysis/fundamentals/capital-structure/wacc": _fundamental_wacc,
    "analysis/fundamentals/capital-structure/optimal": _optimal_capital_structure,
}

RATIO_ROUTE_NAMES = {
    "profitability": ("roa", "roe", "roic", "gross-margin", "net-margin", "ebitda-margin",
                      "operating-margin", "roce", "basic-earning-power", "cash-roa", "cash-roe", "cash-roic"),
    "liquidity": ("current-ratio", "quick-ratio", "cash-ratio", "absolute-liquidity",
                  "defensive-interval", "working-capital", "working-capital-ratio"),
    "leverage": ("debt-to-equity", "interest-coverage", "debt-to-assets", "debt-to-capital",
                 "equity-ratio", "equity-multiplier", "ebitda-interest-coverage", "cash-coverage",
                 "net-debt-to-ebitda", "long-term-debt-to-equity", "financial-leverage",
                 "capitalization-ratio", "debt-service-coverage"),
    "valuation": ("pe", "pb", "ps", "ev-ebitda", "dividend-yield", "pcf", "pfcf", "peg",
                  "ev-revenue", "ev-fcf", "earnings-yield", "fcf-yield", "tobins-q"),
    "efficiency": ("asset-turnover", "fixed-asset-turnover", "inventory-turnover",
                   "receivables-turnover", "payables-turnover", "days-sales-outstanding",
                   "days-inventory-outstanding", "days-payables-outstanding", "cash-conversion-cycle",
                   "operating-cycle", "working-capital-turnover", "equity-turnover"),
    "growth": ("yoy", "cagr", "sustainable-growth", "internal-growth"),
    "cashflow": ("ocf-to-debt", "ocf-ratio", "ocf-margin", "fcf", "fcf-margin",
                 "cash-conversion-quality", "capex-to-ocf", "capex-to-depreciation",
                 "cash-coverage-of-dividends", "reinvestment-rate"),
    "quality": ("accruals-ratio", "sloan-accruals", "earnings-persistence", "earnings-variability",
                "cash-earnings", "quality-of-earnings"),
}
for _category, _names in RATIO_ROUTE_NAMES.items():
    for _name in _names:
        ROUTES[f"analysis/ratios/{_category}/{_name}"] = (
            lambda body, category=_category, name=_name: _financial_ratio(body, category, name)
        )


def _meta() -> Dict[str, Any]:
    return {"cost_route": "local_deterministic", "fincept_credits": 0, "llm_used": False}


def execute(endpoint: str, body: Mapping[str, Any]) -> Dict[str, Any]:
    route = ROUTES.get(endpoint.strip("/"))
    if route is None:
        return {"supported": False,
                "message": "No zero-credit deterministic implementation exists for this endpoint yet. "
                           "No paid Fincept request was sent.",
                "meta": _meta()}
    try:
        return {"supported": True, "data": route(body), "meta": _meta()}
    except (KeyError, TypeError, ValueError, OverflowError, statistics.StatisticsError) as exc:
        return {"supported": True, "error": str(exc), "meta": _meta()}


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) not in {2, 3} or args[0] != "run":
        print(json.dumps({"error": "usage: byo_quantlib.py run <endpoint> [json on stdin]"}))
        return 1
    try:
        raw = args[2] if len(args) == 3 else sys.stdin.read()
        body = json.loads(raw)
        if not isinstance(body, dict):
            raise ValueError("body must be a JSON object")
        print(json.dumps(execute(args[1], body), allow_nan=False, separators=(",", ":")))
        return 0
    except (ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"error": str(exc)}, separators=(",", ":")))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
