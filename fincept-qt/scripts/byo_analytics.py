#!/usr/bin/env python3
"""Local-first analytics that never calls a metered Fincept endpoint.

Commands:
  portfolio_risk       Multi-asset volatility, VaR/ES, drawdown and risk contribution.
  technical_rating     Composite trend and momentum rating from closing prices.
  market_pulse         Weighted risk-on/risk-off score from normalized signals.
  options_positioning  Put/call ratios, OI walls, max pain and optional gamma exposure.
  fixed_income         Plain fixed-rate bond price, duration, convexity and DV01.
  quantlib_routes      Local script routes for all 18 QuantLib screen modules.
  bundle               Run several engines from one payload and one tool call.
  capabilities         Machine-readable feature and data-quality manifest.

Each analytics command accepts one JSON object as argv[2], or on stdin.  The
module intentionally uses only the Python standard library; DeepSeek or another
BYO model can orchestrate it through Fincept's Python tool without consuming
Fincept AI credits.
"""

from __future__ import annotations

import json
import math
import statistics
import sys
from typing import Any, Dict, Iterable, List, Mapping, Sequence


def _finite(value: Any, name: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be numeric") from exc
    if not math.isfinite(number):
        raise ValueError(f"{name} must be finite")
    return number


def _numbers(values: Iterable[Any], name: str) -> List[float]:
    result = [_finite(value, name) for value in values]
    if not result:
        raise ValueError(f"{name} must not be empty")
    return result


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values)


def _quantile(values: Sequence[float], probability: float) -> float:
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = probability * (len(ordered) - 1)
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def _normalize_weights(names: Sequence[str], supplied: Mapping[str, Any] | None) -> Dict[str, float]:
    if supplied:
        weights = {name: _finite(supplied.get(name, 0), f"weight[{name}]") for name in names}
    else:
        weights = {name: 1.0 for name in names}
    total = sum(weights.values())
    if total <= 0:
        raise ValueError("weights must sum to a positive value")
    return {name: value / total for name, value in weights.items()}


def portfolio_risk(payload: Mapping[str, Any]) -> Dict[str, Any]:
    raw = payload.get("returns") or payload.get("returns_data")
    if not isinstance(raw, Mapping) or not raw:
        raise ValueError("returns must be a non-empty object of asset return arrays")
    series = {str(name): _numbers(values, f"returns[{name}]") for name, values in raw.items()}
    for name, values in series.items():
        if any(value < -1.0 for value in values):
            raise ValueError(f"returns[{name}] cannot be below -100% for a price return series")
    lengths = {len(values) for values in series.values()}
    if len(lengths) != 1 or next(iter(lengths)) < 2:
        raise ValueError("all return arrays must have the same length of at least two")

    names = list(series)
    weights = _normalize_weights(names, payload.get("weights"))
    count = next(iter(lengths))
    returns = [sum(weights[name] * series[name][i] for name in names) for i in range(count)]
    daily_vol = statistics.stdev(returns)
    periods_per_year = _finite(payload.get("periods_per_year", 252), "periods_per_year")
    annualized_vol = daily_vol * math.sqrt(periods_per_year)

    confidence = _finite(payload.get("confidence", 0.95), "confidence")
    if not 0.5 <= confidence < 1:
        raise ValueError("confidence must be in [0.5, 1)")
    losses = [-value for value in returns]
    var = max(0.0, _quantile(losses, confidence))
    tail = [loss for loss in losses if loss >= var - 1e-15]
    expected_shortfall = max(var, _mean(tail) if tail else var)

    wealth = 1.0
    peak = 1.0
    max_drawdown = 0.0
    for value in returns:
        wealth *= 1 + value
        peak = max(peak, wealth)
        max_drawdown = min(max_drawdown, wealth / peak - 1)

    means = {name: _mean(series[name]) for name in names}
    covariance: Dict[str, Dict[str, float]] = {name: {} for name in names}
    for left in names:
        for right in names:
            covariance[left][right] = sum(
                (series[left][i] - means[left]) * (series[right][i] - means[right])
                for i in range(count)
            ) / (count - 1)
    marginal = {
        name: sum(covariance[name][other] * weights[other] for other in names)
        for name in names
    }
    variance = sum(weights[name] * marginal[name] for name in names)
    if abs(variance) < 1e-18:
        contribution = {name: weights[name] for name in names}
    else:
        contribution = {name: weights[name] * marginal[name] / variance for name in names}

    asset_volatility = {
        name: statistics.stdev(series[name]) * math.sqrt(periods_per_year)
        for name in names
    }
    weighted_standalone_volatility = sum(weights[name] * asset_volatility[name] for name in names)
    diversification_ratio = weighted_standalone_volatility / annualized_vol if annualized_vol > 1e-15 else 1.0

    correlation_matrix: Dict[str, Dict[str, float]] = {name: {} for name in names}
    for left in names:
        left_vol = math.sqrt(max(0.0, covariance[left][left]))
        for right in names:
            right_vol = math.sqrt(max(0.0, covariance[right][right]))
            denominator = left_vol * right_vol
            correlation_matrix[left][right] = (
                covariance[left][right] / denominator if denominator > 1e-15 else (1.0 if left == right else 0.0)
            )

    benchmark = None
    raw_benchmark = payload.get("benchmark_returns")
    if raw_benchmark is not None:
        benchmark_returns = _numbers(raw_benchmark, "benchmark_returns")
        if len(benchmark_returns) != count:
            raise ValueError("benchmark_returns must have the same length as asset returns")
        portfolio_mean = _mean(returns)
        benchmark_mean = _mean(benchmark_returns)
        benchmark_variance = sum((value - benchmark_mean) ** 2 for value in benchmark_returns) / (count - 1)
        portfolio_benchmark_covariance = sum(
            (returns[index] - portfolio_mean) * (benchmark_returns[index] - benchmark_mean)
            for index in range(count)
        ) / (count - 1)
        beta = portfolio_benchmark_covariance / benchmark_variance if benchmark_variance > 1e-18 else 0.0
        benchmark_daily_vol = statistics.stdev(benchmark_returns)
        correlation_denominator = daily_vol * benchmark_daily_vol
        benchmark = {
            "beta": beta,
            "annualized_alpha": (portfolio_mean - beta * benchmark_mean) * periods_per_year,
            "correlation": (
                portfolio_benchmark_covariance / correlation_denominator
                if correlation_denominator > 1e-15 else 0.0
            ),
        }

    result = {
        "kind": "portfolio_risk",
        "observations": count,
        "weights": weights,
        "annualized_volatility": annualized_vol,
        "historical_var": var,
        "expected_shortfall": expected_shortfall,
        "max_drawdown": max_drawdown,
        "risk_contribution": contribution,
        "asset_volatility": asset_volatility,
        "correlation_matrix": correlation_matrix,
        "diversification_ratio": diversification_ratio,
        "notes": ["Return inputs are decimal fractions.", "VaR and expected shortfall are one-period loss fractions."],
    }
    if benchmark is not None:
        result["benchmark"] = benchmark
    return result


def _sma(values: Sequence[float], period: int) -> float:
    if len(values) < period:
        raise ValueError(f"at least {period} closes are required")
    return _mean(values[-period:])


def _ema_series(values: Sequence[float], period: int) -> List[float]:
    alpha = 2.0 / (period + 1)
    output = [values[0]]
    for value in values[1:]:
        output.append(alpha * value + (1 - alpha) * output[-1])
    return output


def _rsi(values: Sequence[float], period: int = 14) -> float:
    if len(values) <= period:
        raise ValueError(f"at least {period + 1} closes are required")
    changes = [values[i] - values[i - 1] for i in range(len(values) - period, len(values))]
    gain = _mean([max(change, 0) for change in changes])
    loss = _mean([max(-change, 0) for change in changes])
    if gain == 0 and loss == 0:
        return 50.0
    if loss == 0:
        return 100.0
    return 100 - 100 / (1 + gain / loss)


def technical_rating(payload: Mapping[str, Any]) -> Dict[str, Any]:
    closes = _numbers(payload.get("closes", []), "closes")
    if len(closes) < 200:
        raise ValueError("technical_rating requires at least 200 closing prices")
    if any(value <= 0 for value in closes):
        raise ValueError("closing prices must be positive")
    last = closes[-1]

    moving_averages = []
    for kind, period in (("SMA", 10), ("SMA", 20), ("SMA", 30), ("SMA", 50), ("SMA", 100),
                         ("SMA", 200), ("EMA", 10), ("EMA", 20), ("EMA", 50), ("EMA", 100)):
        value = _sma(closes, period) if kind == "SMA" else _ema_series(closes, period)[-1]
        moving_averages.append({"name": f"{kind} {period}", "value": value,
                                "signal": "BUY" if last > value else "SELL" if last < value else "NEUTRAL"})

    ema12 = _ema_series(closes, 12)
    ema26 = _ema_series(closes, 26)
    macd_series = [fast - slow for fast, slow in zip(ema12, ema26)]
    macd = macd_series[-1]
    macd_signal = _ema_series(macd_series, 9)[-1]
    rsi = _rsi(closes)
    momentum10 = closes[-1] / closes[-11] - 1
    momentum20 = closes[-1] / closes[-21] - 1
    window14 = closes[-14:]
    range14 = max(window14) - min(window14)
    stochastic14 = 50.0 if range14 <= 1e-15 else (last - min(window14)) / range14 * 100.0
    mean20 = _mean(closes[-20:])
    std20 = statistics.stdev(closes[-20:])
    zscore20 = (last - mean20) / std20 if std20 > 1e-15 else 0.0

    def signal(value: float, buy: float, sell: float) -> str:
        return "BUY" if value > buy else "SELL" if value < sell else "NEUTRAL"

    oscillators = [
        {"name": "RSI 14", "value": rsi, "signal": signal(rsi, 55.0, 45.0)},
        {"name": "MACD", "value": macd - macd_signal,
         "signal": signal(macd - macd_signal, 0.0, 0.0)},
        {"name": "Momentum 10", "value": momentum10, "signal": signal(momentum10, 0.0, 0.0)},
        {"name": "Rate of change 20", "value": momentum20, "signal": signal(momentum20, 0.0, 0.0)},
        {"name": "Stochastic 14", "value": stochastic14,
         "signal": signal(stochastic14, 60.0, 40.0)},
        {"name": "Price z-score 20", "value": zscore20, "signal": signal(zscore20, 0.25, -0.25)},
    ]

    def vote_summary(rows: Sequence[Mapping[str, Any]]) -> Dict[str, int]:
        counts = {"buy": 0, "sell": 0, "neutral": 0, "total": len(rows)}
        for row in rows:
            counts[str(row["signal"]).lower()] += 1
        return counts

    ma_votes = vote_summary(moving_averages)
    oscillator_votes = vote_summary(oscillators)
    buy_votes = ma_votes["buy"] + oscillator_votes["buy"]
    sell_votes = ma_votes["sell"] + oscillator_votes["sell"]
    score = (buy_votes - sell_votes) / (ma_votes["total"] + oscillator_votes["total"]) * 100.0
    if score >= 60:
        rating = "STRONG_BUY"
    elif score >= 25:
        rating = "BUY"
    elif score <= -60:
        rating = "STRONG_SELL"
    elif score <= -25:
        rating = "SELL"
    else:
        rating = "NEUTRAL"

    daily_returns = [closes[index] / closes[index - 1] - 1.0 for index in range(1, len(closes))]
    trailing = closes[-252:]
    range_low, range_high = min(trailing), max(trailing)
    range_position = 0.5 if range_high - range_low <= 1e-15 else (last - range_low) / (range_high - range_low)

    performance = {}
    for label, days in (("one_week", 5), ("one_month", 21), ("three_month", 63),
                        ("six_month", 126), ("one_year", 252)):
        if len(closes) > days:
            performance[label] = last / closes[-days - 1] - 1.0
    performance.update({
        "annualized_volatility": statistics.stdev(daily_returns) * math.sqrt(252),
        "range_52_week_low": range_low,
        "range_52_week_high": range_high,
        "range_52_week_position": range_position,
    })

    benchmark_closes = payload.get("benchmark_closes")
    if benchmark_closes is not None:
        benchmark = _numbers(benchmark_closes, "benchmark_closes")
        relative_days = min(63, len(closes) - 1, len(benchmark) - 1)
        if relative_days > 0:
            performance["relative_strength_3m"] = (
                last / closes[-relative_days - 1] - benchmark[-1] / benchmark[-relative_days - 1]
            )

    volume = None
    if payload.get("volumes") is not None:
        volumes = _numbers(payload.get("volumes", []), "volumes")
        if len(volumes) != len(closes):
            raise ValueError("volumes must have the same length as closes")
        if any(value < 0 for value in volumes):
            raise ValueError("volumes cannot be negative")
        average20 = _mean(volumes[-20:])
        volume = {"latest": volumes[-1], "average_20": average20,
                  "relative_to_average": volumes[-1] / average20 if average20 > 1e-15 else 0.0}

    return {
        "kind": "technical_rating",
        "score": score,
        "rating": rating,
        "votes": {"moving_averages": ma_votes, "oscillators": oscillator_votes},
        "moving_averages": moving_averages,
        "oscillators": oscillators,
        "performance": performance,
        "volume": volume,
        "indicators": {
            "close": last,
            "sma20": next(row["value"] for row in moving_averages if row["name"] == "SMA 20"),
            "sma50": next(row["value"] for row in moving_averages if row["name"] == "SMA 50"),
            "sma200": next(row["value"] for row in moving_averages if row["name"] == "SMA 200"),
            "rsi14": rsi,
            "macd": macd,
            "macd_signal": macd_signal,
            "momentum20": momentum20,
        },
    }


def market_pulse(payload: Mapping[str, Any]) -> Dict[str, Any]:
    raw_signals = payload.get("signals")
    if not isinstance(raw_signals, Mapping) or not raw_signals:
        raise ValueError("signals must be a non-empty object of normalized values")
    weights = payload.get("weights") if isinstance(payload.get("weights"), Mapping) else {}
    signals: Dict[str, float] = {}
    contributions: Dict[str, float] = {}
    total_weight = 0.0
    weighted_sum = 0.0
    for name, raw_value in raw_signals.items():
        value = max(-1.0, min(1.0, _finite(raw_value, f"signal[{name}]")))
        weight = max(0.0, _finite(weights.get(name, 1.0), f"weight[{name}]"))
        signals[str(name)] = value
        contributions[str(name)] = value * weight
        weighted_sum += value * weight
        total_weight += weight
    if total_weight <= 0:
        raise ValueError("signal weights must include a positive value")
    normalized = weighted_sum / total_weight
    score = 50 + 50 * normalized
    regime = "RISK_ON" if score >= 60 else "RISK_OFF" if score <= 40 else "MIXED"
    expected = max(len(signals), int(payload.get("expected_signals", len(signals))))
    return {
        "kind": "market_pulse",
        "score": score,
        "regime": regime,
        "coverage": len(signals) / expected,
        "signals": signals,
        "weighted_contribution": contributions,
    }


def _row_value(row: Mapping[str, Any], *names: str) -> float:
    for name in names:
        if name in row and row[name] is not None:
            return _finite(row[name], name)
    return 0.0


def _normal_cdf(value: float) -> float:
    return 0.5 * (1.0 + math.erf(value / math.sqrt(2.0)))


def _black_scholes_price(spot: float, strike: float, years: float, rate: float, volatility: float,
                         is_call: bool) -> float:
    if years <= 0 or volatility <= 0 or spot <= 0 or strike <= 0:
        return max(0.0, spot - strike) if is_call else max(0.0, strike - spot)
    root_time = math.sqrt(years)
    d1 = (math.log(spot / strike) + (rate + 0.5 * volatility**2) * years) / (volatility * root_time)
    d2 = d1 - volatility * root_time
    discounted_strike = strike * math.exp(-rate * years)
    if is_call:
        return spot * _normal_cdf(d1) - discounted_strike * _normal_cdf(d2)
    return discounted_strike * _normal_cdf(-d2) - spot * _normal_cdf(-d1)


def _implied_volatility(price: float, spot: float, strike: float, years: float, rate: float,
                        is_call: bool) -> float | None:
    discounted_strike = strike * math.exp(-rate * years)
    intrinsic = max(0.0, spot - discounted_strike) if is_call else max(
        0.0, discounted_strike - spot
    )
    theoretical_maximum = spot if is_call else discounted_strike
    if price <= intrinsic + 1e-8 or price >= theoretical_maximum:
        return None
    low, high = 1e-4, 5.0
    if _black_scholes_price(spot, strike, years, rate, high, is_call) < price:
        return None
    for _ in range(80):
        middle = (low + high) / 2.0
        if _black_scholes_price(spot, strike, years, rate, middle, is_call) < price:
            low = middle
        else:
            high = middle
    return (low + high) / 2.0


def _option_gamma(spot: float, strike: float, years: float, rate: float, volatility: float | None) -> float:
    if not volatility or volatility <= 0 or years <= 0 or spot <= 0 or strike <= 0:
        return 0.0
    root_time = math.sqrt(years)
    d1 = (math.log(spot / strike) + (rate + 0.5 * volatility**2) * years) / (volatility * root_time)
    density = math.exp(-0.5 * d1**2) / math.sqrt(2.0 * math.pi)
    return density / (spot * volatility * root_time)


def options_positioning(payload: Mapping[str, Any]) -> Dict[str, Any]:
    spot = _finite(payload.get("spot"), "spot")
    if spot <= 0:
        raise ValueError("spot must be positive")
    years = _finite(payload.get("time_to_expiry", 30 / 365), "time_to_expiry")
    if years <= 0:
        raise ValueError("time_to_expiry must be positive")
    rate = _finite(payload.get("risk_free_rate", 0.04), "risk_free_rate")
    raw_chain = payload.get("chain")
    if not isinstance(raw_chain, Sequence) or not raw_chain:
        raise ValueError("chain must be a non-empty array")
    chain = []
    for raw in raw_chain:
        if not isinstance(raw, Mapping):
            raise ValueError("each chain row must be an object")
        row = {
            "strike": _finite(raw.get("strike"), "strike"),
            "call_oi": _row_value(raw, "call_oi", "ce_oi"),
            "put_oi": _row_value(raw, "put_oi", "pe_oi"),
            "call_volume": _row_value(raw, "call_volume", "ce_volume"),
            "put_volume": _row_value(raw, "put_volume", "pe_volume"),
            "call_gamma": _row_value(raw, "call_gamma", "ce_gamma"),
            "put_gamma": _row_value(raw, "put_gamma", "pe_gamma"),
            "call_bid": _row_value(raw, "call_bid", "ce_bid"),
            "call_ask": _row_value(raw, "call_ask", "ce_ask"),
            "put_bid": _row_value(raw, "put_bid", "pe_bid"),
            "put_ask": _row_value(raw, "put_ask", "pe_ask"),
        }
        if row["strike"] <= 0:
            raise ValueError("strike must be positive")
        for field, value in row.items():
            if field != "strike" and value < 0:
                raise ValueError(f"{field} must be non-negative")
        chain.append(row)
    call_oi = sum(row["call_oi"] for row in chain)
    put_oi = sum(row["put_oi"] for row in chain)
    call_volume = sum(row["call_volume"] for row in chain)
    put_volume = sum(row["put_volume"] for row in chain)
    call_wall = max(chain, key=lambda row: row["call_oi"])["strike"]
    put_wall = max(chain, key=lambda row: row["put_oi"])["strike"]

    pain_by_strike: Dict[float, float] = {}
    for settlement in sorted({row["strike"] for row in chain}):
        pain_by_strike[settlement] = sum(
            max(0.0, settlement - row["strike"]) * row["call_oi"]
            + max(0.0, row["strike"] - settlement) * row["put_oi"]
            for row in chain
        )
    max_pain = min(pain_by_strike, key=lambda strike: (pain_by_strike[strike], abs(strike - spot)))
    for row in chain:
        call_mid = (row["call_bid"] + row["call_ask"]) / 2.0 if row["call_bid"] and row["call_ask"] else 0.0
        put_mid = (row["put_bid"] + row["put_ask"]) / 2.0 if row["put_bid"] and row["put_ask"] else 0.0
        row["call_mid"] = call_mid
        row["put_mid"] = put_mid
        row["call_iv"] = _implied_volatility(call_mid, spot, row["strike"], years, rate, True) if call_mid else None
        row["put_iv"] = _implied_volatility(put_mid, spot, row["strike"], years, rate, False) if put_mid else None
        if row["call_gamma"] <= 0:
            row["call_gamma"] = _option_gamma(spot, row["strike"], years, rate, row["call_iv"])
        if row["put_gamma"] <= 0:
            row["put_gamma"] = _option_gamma(spot, row["strike"], years, rate, row["put_iv"])

    atm = min(chain, key=lambda row: abs(row["strike"] - spot))
    expected_move = atm["call_mid"] + atm["put_mid"] if atm["call_mid"] and atm["put_mid"] else None
    atm_ivs = [value for value in (atm["call_iv"], atm["put_iv"]) if value is not None]
    atm_iv = _mean(atm_ivs) if atm_ivs else None
    put_skew_row = min(chain, key=lambda row: abs(row["strike"] - 0.9 * spot))
    call_skew_row = min(chain, key=lambda row: abs(row["strike"] - 1.1 * spot))
    volatility_skew = (
        put_skew_row["put_iv"] - call_skew_row["call_iv"]
        if put_skew_row["put_iv"] is not None and call_skew_row["call_iv"] is not None else None
    )

    gamma_by_strike = []
    for row in chain:
        call_exposure = row["call_gamma"] * row["call_oi"] * 100.0 * spot**2
        put_exposure = -row["put_gamma"] * row["put_oi"] * 100.0 * spot**2
        gamma_by_strike.append({"strike": row["strike"], "call_gamma": call_exposure,
                                "put_gamma": put_exposure, "net_gamma": call_exposure + put_exposure})
    net_gamma = sum(row["net_gamma"] for row in gamma_by_strike)

    unusual_activity = []
    for row in chain:
        for side in ("CALL", "PUT"):
            key = side.lower()
            volume = row[f"{key}_volume"]
            oi = row[f"{key}_oi"]
            if volume > 0:
                unusual_activity.append({"strike": row["strike"], "side": side, "volume": volume,
                                         "open_interest": oi, "volume_oi_ratio": volume / max(oi, 1.0)})
    unusual_activity.sort(key=lambda row: row["volume_oi_ratio"], reverse=True)
    return {
        "kind": "options_positioning",
        "spot": spot,
        "put_call_oi_ratio": put_oi / call_oi if call_oi else None,
        "put_call_volume_ratio": put_volume / call_volume if call_volume else None,
        "call_wall": call_wall,
        "put_wall": put_wall,
        "max_pain": max_pain,
        "max_pain_distance_pct": (max_pain / spot - 1) if spot else None,
        "expected_move": expected_move,
        "expected_move_pct": expected_move / spot if expected_move is not None and spot else None,
        "atm_implied_volatility": atm_iv,
        "volatility_skew": volatility_skew,
        "net_gamma_exposure": net_gamma,
        "gamma_regime": "POSITIVE" if net_gamma > 0 else "NEGATIVE" if net_gamma < 0 else "UNKNOWN",
        "gamma_by_strike": gamma_by_strike,
        "unusual_activity": unusual_activity[:5],
        "data_quality": "input_dependent",
        "delayed_data_ok": bool(payload.get("delayed_data_ok", True)),
    }


def fixed_income(payload: Mapping[str, Any]) -> Dict[str, Any]:
    face = _finite(payload.get("face_value", 1000), "face_value")
    coupon_rate = _finite(payload.get("coupon_rate", 0), "coupon_rate")
    years = _finite(payload.get("years"), "years")
    yield_rate = _finite(payload.get("yield_rate"), "yield_rate")
    frequency_value = _finite(payload.get("frequency", 2), "frequency")
    if frequency_value <= 0 or not frequency_value.is_integer():
        raise ValueError("frequency must be a positive integer")
    frequency = int(frequency_value)
    if face <= 0 or years <= 0 or yield_rate <= -frequency:
        raise ValueError("face_value, years and frequency must be positive and yield must be valid")
    periods_float = years * frequency
    periods = round(periods_float)
    if not math.isclose(periods_float, periods, rel_tol=0, abs_tol=1e-9):
        raise ValueError("years times frequency must be a whole number of periods")
    if periods < 1:
        raise ValueError("fixed-income schedule must contain at least one period")
    if periods > 10_000:
        raise ValueError("fixed-income schedule supports at most 10000 periods")
    periodic_yield = yield_rate / frequency
    coupon = face * coupon_rate / frequency
    cashflows = [coupon] * periods
    cashflows[-1] += face
    present_values = [cashflow / (1 + periodic_yield) ** period for period, cashflow in enumerate(cashflows, 1)]
    price = sum(present_values)
    macaulay = sum((period / frequency) * pv for period, pv in enumerate(present_values, 1)) / price
    modified = macaulay / (1 + periodic_yield)
    convexity = sum(
        cashflow * period * (period + 1) / (1 + periodic_yield) ** (period + 2)
        for period, cashflow in enumerate(cashflows, 1)
    ) / (price * frequency**2)
    return {
        "kind": "fixed_income",
        "clean_price": price,
        "macaulay_duration": macaulay,
        "modified_duration": modified,
        "convexity": convexity,
        "dv01": modified * price * 0.0001,
        "cashflows": cashflows,
    }


def quantlib_routes(payload: Mapping[str, Any]) -> Dict[str, Any]:
    """Return honest local substitutes for every QuantLib screen module.

    This is a routing catalogue, not a claim that a language model or the
    bundled scripts reproduce all cloud endpoints.  DeepSeek can use the
    listed script names with Fincept's Python tool and keep calculations local.
    """
    catalog = {
        "core": {
            "coverage": "partial",
            "scripts": ["byo_analytics", "Analytics/quant/quant_modules_3042"],
            "use_for": ["rates", "cashflows", "statistics", "basic numerical operations"],
        },
        "analysis": {
            "coverage": "strong",
            "scripts": ["Analytics/financial_analysis_cli", "Analytics/finanicalanalysis/specialized_analysis/financial_modeling"],
            "use_for": ["statements", "ratios", "DCF", "credit and company analysis"],
        },
        "curves": {
            "coverage": "strong",
            "scripts": ["Analytics/fixedIncome/yield_curve"],
            "use_for": ["yield curves", "interpolation", "spreads", "curve risk"],
        },
        "economics": {
            "coverage": "partial",
            "scripts": ["Analytics/portfolioManagement/economics_markets"],
            "use_for": ["economic and market scenario calculations"],
        },
        "instruments": {
            "coverage": "strong",
            "scripts": ["Analytics/fixedIncome/bond_pricing", "Analytics/fixedIncome/bond_features"],
            "use_for": ["bonds", "floating-rate instruments", "structured products"],
        },
        "ml": {
            "coverage": "strong",
            "scripts": ["ai_quant_lab/qlib_feature_engineering", "ai_quant_lab/qlib_advanced_models"],
            "use_for": ["regression", "classification", "clustering", "time-series features"],
        },
        "models": {
            "coverage": "partial",
            "scripts": ["Analytics/py_vollib_wrapper/vollib_service", "Analytics/options/iv_surface"],
            "use_for": ["option models", "implied volatility", "surface analysis"],
        },
        "numerical": {
            "coverage": "partial",
            "scripts": ["Analytics/quant/quant_modules_3042", "Analytics/statsmodels_wrapper/statsmodels_service"],
            "use_for": ["optimization", "linear algebra", "statistical numerical work"],
        },
        "physics": {
            "coverage": "limited",
            "scripts": ["Analytics/quant/quant_modules_3042"],
            "use_for": ["entropy-style measures only"],
        },
        "portfolio": {
            "coverage": "strong",
            "scripts": ["byo_analytics", "Analytics/portfolioManagement/risk_management", "optimize_portfolio_weights"],
            "use_for": ["optimization", "VaR", "expected shortfall", "drawdown", "risk contribution"],
        },
        "pricing": {
            "coverage": "strong",
            "scripts": ["Analytics/py_vollib_wrapper/vollib_service", "Analytics/fixedIncome/bond_pricing"],
            "use_for": ["Black-Scholes", "Black 76", "option Greeks", "bond pricing"],
        },
        "regulatory": {
            "coverage": "limited",
            "scripts": ["Analytics/fixedIncome/credit_analysis", "Analytics/portfolioManagement/risk_management"],
            "use_for": ["credit risk and stress metrics; not regulatory reporting"],
        },
        "risk": {
            "coverage": "strong",
            "scripts": ["byo_analytics", "Analytics/portfolioManagement/risk_management", "portfolio_metrics"],
            "use_for": ["VaR", "expected shortfall", "stress", "sensitivity", "drawdown"],
        },
        "scheduling": {
            "coverage": "partial",
            "scripts": ["Analytics/fixedIncome/bond_features", "Analytics/fixedIncome/fi_common"],
            "use_for": ["coupon schedules", "day-count inputs", "instrument conventions"],
        },
        "solver": {
            "coverage": "strong",
            "scripts": ["byo_analytics", "Analytics/fixedIncome/bond_pricing", "Analytics/fixedIncome/duration_convexity"],
            "use_for": ["yield", "duration", "convexity", "cashflows"],
        },
        "statistics": {
            "coverage": "strong",
            "scripts": ["Analytics/statsmodels_wrapper/statsmodels_service", "Analytics/ffn_wrapper/ffn_service"],
            "use_for": ["distributions", "tests", "regression", "time series"],
        },
        "stochastic": {
            "coverage": "partial",
            "scripts": ["Analytics/quant/quant_modules_3042"],
            "use_for": ["simulation and quantitative process utilities"],
        },
        "volatility": {
            "coverage": "strong",
            "scripts": ["Analytics/options/iv_smile", "Analytics/options/iv_surface", "Analytics/options/gex_calculator"],
            "use_for": ["IV smile", "IV surface", "gamma exposure"],
        },
    }
    requested = payload.get("module")
    selected = catalog
    if requested is not None:
        name = str(requested).strip().lower()
        if name not in catalog:
            raise ValueError(f"unknown QuantLib module: {name}")
        selected = {name: catalog[name]}
    return {
        "kind": "quantlib_routes",
        "uses_fincept_credits": False,
        "modules": selected,
        "warning": "Local routes are substitutes, not full parity with the roughly 590 cloud endpoints.",
        "model_role": "DeepSeek selects a route and explains deterministic script output; it must not invent missing market data.",
    }


def bundle(payload: Mapping[str, Any]) -> Dict[str, Any]:
    engines = {
        "portfolio_risk": portfolio_risk,
        "technical_rating": technical_rating,
        "market_pulse": market_pulse,
        "options_positioning": options_positioning,
        "fixed_income": fixed_income,
    }
    requested = [name for name in payload if name in engines]
    if not requested:
        raise ValueError("bundle requires at least one analytics command payload")
    return {
        "kind": "bundle",
        "requested": requested,
        "results": {name: engines[name](payload[name]) for name in requested},
    }


def capabilities() -> Dict[str, Any]:
    return {
        "engine": "byo_analytics",
        "version": 1,
        "uses_fincept_credits": False,
        "commands": ["portfolio_risk", "technical_rating", "market_pulse", "options_positioning", "fixed_income", "quantlib_routes", "bundle"],
        "portfolio_risk": {"data_quality": "input_dependent", "network_required": False},
        "technical_rating": {"data_quality": "input_dependent", "network_required": False},
        "market_pulse": {"data_quality": "input_dependent", "network_required": False},
        "options_positioning": {
            "data_quality": "input_dependent",
            "network_required": False,
            "limitation": "A model cannot replace a reliable option-chain and open-interest feed.",
        },
        "fixed_income": {"data_quality": "deterministic", "network_required": False},
        "orchestration": {
            "recommended_provider": "user_api",
            "model_role": "select commands, prepare inputs, and explain outputs",
            "calculation_role": "this deterministic local engine",
        },
    }


COMMANDS = {
    "portfolio_risk": portfolio_risk,
    "technical_rating": technical_rating,
    "market_pulse": market_pulse,
    "options_positioning": options_positioning,
    "fixed_income": fixed_income,
    "quantlib_routes": quantlib_routes,
    "bundle": bundle,
}


def _read_payload(argv: Sequence[str]) -> Dict[str, Any]:
    raw = argv[0] if argv else sys.stdin.read()
    if not raw.strip():
        return {}
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise ValueError("payload must be a JSON object")
    return parsed


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        print(json.dumps({"error": "usage: byo_analytics.py <command> [json]"}))
        return 1
    command = args.pop(0)
    try:
        result = capabilities() if command == "capabilities" else COMMANDS[command](_read_payload(args))
        print(json.dumps(result, allow_nan=False, separators=(",", ":")))
        return 0
    except KeyError:
        print(json.dumps({"error": f"unknown command: {command}", "commands": sorted([*COMMANDS, "capabilities"])}))
        return 1
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"error": str(exc), "command": command}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
