"""Shared local-work limits for Alternative Investments screen analyzers."""

from __future__ import annotations

import math
from typing import Any, Dict


MAX_ANALYSIS_YEARS = 100.0

# These fields reach loops, date arithmetic, or Decimal exponentiation in one
# or more screen analyzers. The UI already exposes smaller ranges; this guard
# protects the same entry point when it is called by an AI tool or directly.
YEAR_FIELDS = frozenset({
    "fund_life",
    "hold_period_years",
    "holding_years",
    "investment_horizon_years",
    "life_years",
    "loan_term",
    "maturity_years",
    "payout_years",
    "projection_years",
    "surrender_charge_years",
    "surrender_period",
    "surrender_years",
    "term_years",
    "time_period_years",
    "years",
    "years_held",
    "years_to_call",
    "years_to_maturity",
})


def validate_year_fields(data: Dict[str, Any]) -> Dict[str, Any]:
    """Copy *data* and reject unsafe year durations without changing values."""
    out = dict(data)
    for field in YEAR_FIELDS:
        if field not in out or out[field] is None:
            continue
        raw = out[field]
        if isinstance(raw, bool):
            raise ValueError(f"{field} must be a finite number of years")
        try:
            years = float(raw)
        except (TypeError, ValueError, OverflowError):
            raise ValueError(f"{field} must be a finite number of years") from None
        if not math.isfinite(years) or years < 0 or years > MAX_ANALYSIS_YEARS:
            raise ValueError(
                f"{field} must be between 0 and {MAX_ANALYSIS_YEARS:g} years"
            )
    return out

