"""
Breadth significance.

A flat member floor asks "did enough companies fall?". These pin the
replacement, which asks "was it surprising that they fell?" — the question
that actually separates an industry shock from an ordinary down day, and the
one a headcount cannot express at any threshold.
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd
import pytest

from dipdector.config import CONFIG
from dipdector.data.providers import PriceFrame
from dipdector.engine.metrics import breadth_pvalue, market_decline_rate


def _frame(n_companies: int, n_down: int, window: int = 5,
           down: float = -0.10) -> PriceFrame:
    """A frame where exactly n_down of n_companies fall over the window."""
    dates = pd.bdate_range("2026-01-01", periods=window + 40)
    data = {}
    for i in range(n_companies):
        path = np.full(len(dates), 100.0)
        if i < n_down:
            path[-1] = 100.0 * (1 + down)
        data[f"T{i:03d}"] = path
    close = pd.DataFrame(data, index=dates)
    return PriceFrame(close, close * 0, "test", dt.datetime.now(dt.timezone.utc),
                      synthetic=True)


# --- the statistic --------------------------------------------------------

def test_whole_small_group_falling_is_unremarkable_in_a_down_market():
    """3 of 3 is a 1-in-11 coincidence when 45% of the market is falling."""
    assert breadth_pvalue(3, 3, 0.45) > CONFIG.detection.max_breadth_pvalue


def test_same_small_group_is_notable_in_a_calm_market():
    """The identical 3 of 3 is a 1-in-125 event when only 20% is falling."""
    assert breadth_pvalue(3, 3, 0.20) < CONFIG.detection.max_breadth_pvalue


def test_bigger_groups_clear_the_bar_at_lower_completeness():
    """10 of 12 is stronger evidence than 3 of 3, which a count inverts."""
    assert breadth_pvalue(10, 12, 0.45) < breadth_pvalue(3, 3, 0.45)


def test_pvalue_falls_as_the_group_grows():
    ps = [breadth_pvalue(n, n, 0.45) for n in (3, 4, 5, 8, 12)]
    assert ps == sorted(ps, reverse=True)


def test_two_of_two_never_clears_the_bar_at_any_plausible_base_rate():
    """Which is why min_industry_members still exists as a floor."""
    for rate in (0.20, 0.30, 0.45):
        assert breadth_pvalue(2, 2, rate) > CONFIG.detection.max_breadth_pvalue


def test_degenerate_base_rates_are_clamped_not_propagated():
    assert 0.0 < breadth_pvalue(5, 5, 0.0) < 1.0
    assert 0.0 < breadth_pvalue(5, 5, 1.0) <= 1.0


def test_missing_base_rate_yields_no_pvalue():
    assert breadth_pvalue(5, 5, None) is None


# --- the base rate --------------------------------------------------------

def test_base_rate_measures_the_universe():
    rate = market_decline_rate(_frame(100, 30), window=5, threshold=-0.03)
    assert rate == pytest.approx(0.30)


def test_base_rate_is_none_when_there_is_no_comparison_universe():
    """s.44 — absence of evidence is not evidence. Skip, never assume."""
    assert market_decline_rate(_frame(10, 10), window=5, threshold=-0.03) is None


def test_base_rate_is_none_when_exclusions_empty_the_universe():
    f = _frame(40, 40)
    everything = list(f.close.columns)
    assert market_decline_rate(f, 5, -0.03, exclude=everything) is None


def test_excluded_tickers_do_not_count_toward_the_base_rate():
    """A group must not be compared against a universe containing itself."""
    f = _frame(100, 40)                       # first 40 fall
    fallers = [c for c in f.close.columns][:40]
    rate = market_decline_rate(f, 5, -0.03, exclude=fallers)
    assert rate == pytest.approx(0.0)


# --- scope: the test admits small groups, it does not police large ones ----

def test_a_large_group_is_not_subjected_to_the_significance_test():
    """
    The regression this pins actually happened. Applying the test to every
    industry measurably degraded the detector: shocks cluster on days the
    whole market is falling, and a high base rate then makes even 12-of-12
    look unsurprising, so the strongest events were the ones rejected.

    Whether a decline is market-wide is already answered by the relative
    underperformance and abnormality conditions. This condition exists only
    to decide whether a small group deserves the benefit of the doubt.
    """
    d = CONFIG.detection
    n = d.breadth_significance_below + 7            # comfortably "large"
    crash_rate = 0.75

    # In a crash even a whole large group fails the raw statistic ...
    assert breadth_pvalue(n, n, crash_rate) > d.max_breadth_pvalue
    # ... which is exactly why the condition must not be applied to it.
    assert n >= d.breadth_significance_below


def test_the_floor_and_the_test_meet_without_a_gap():
    """Every group is governed by one rule or the other, never neither."""
    d = CONFIG.detection
    assert d.min_industry_members <= d.breadth_significance_below
    for n in range(d.min_industry_members, d.breadth_significance_below):
        assert breadth_pvalue(n, n, 0.20) is not None
