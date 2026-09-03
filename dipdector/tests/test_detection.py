"""
Tests for the detection engine.

These are written against the devlog's non-negotiables (s.49) rather than
against the implementation, so that if someone later loosens the engine into a
generic screener, these fail.

Run: python -m pytest dipdector/tests -q
"""

import datetime as dt

import numpy as np
import pandas as pd
import pytest

from dipdector.config import CONFIG
from dipdector.data.providers import FixtureProvider, PriceFrame
from dipdector.data.benchmarks import all_tickers
from dipdector.data.universe import SeedClassificationProvider
# These exercise the engine against the synthetic fixtures, which contain
# prices for the six-industry seed universe only. They therefore pin the seed
# provider explicitly rather than taking whatever default_provider() returns —
# that is now the full S&P 500, whose constituents the fixtures have no prices
# for. Testing the engine, not the universe.
from dipdector.engine.detection import AlertLevel, score_industry
from dipdector.engine.metrics import compute_industry_metrics

FIXTURE = "dipdector/fixtures/synthetic_semis.csv"
SHOCK_DATE = dt.date(2026, 6, 30)
QUIET_DATE = dt.date(2026, 5, 15)


@pytest.fixture(scope="module")
def frame():
    cp = SeedClassificationProvider()
    tickers = ([c.ticker for c in cp.companies_as_of(SHOCK_DATE)]
               + [CONFIG.market_benchmark] + all_tickers())
    return FixtureProvider(FIXTURE).fetch(tickers, dt.date(2024, 1, 1), SHOCK_DATE)


def _assess(frame, as_of, industry="Semiconductors"):
    cp = SeedClassificationProvider()
    members = cp.industry_members(industry, as_of)
    m = compute_industry_metrics(industry, members, frame.as_of(as_of), as_of, CONFIG)
    return score_industry(m, CONFIG) if m else None


def test_quiet_period_produces_no_alert(frame):
    """The default state must be silence. Devlog s.51: few, high-quality events."""
    a = _assess(frame, QUIET_DATE)
    assert a.level == AlertLevel.NONE
    assert not a.triggered


def test_correlated_shock_is_detected(frame):
    a = _assess(frame, SHOCK_DATE)
    assert a.level == AlertLevel.MAJOR_EVENT
    assert a.triggered
    assert a.metrics.n_declining >= CONFIG.detection.min_declining_companies


def test_score_is_fully_decomposed(frame):
    """Devlog s.41 — no unexplained scores."""
    a = _assess(frame, SHOCK_DATE)
    assert abs(sum(c.contribution for c in a.components) - a.score) < 1e-6
    assert all(c.explanation for c in a.components)
    assert all(t.detail for t in a.triggers)


def test_no_lookahead(frame):
    """
    Devlog s.29 / s.44.5 — the assessment on a date must not change when future
    bars are appended. This is the property the whole backtester rests on.
    """
    early = frame.as_of(QUIET_DATE)
    a_truncated = _assess(early, QUIET_DATE)
    a_full = _assess(frame, QUIET_DATE)
    assert a_truncated.score == pytest.approx(a_full.score)
    assert a_truncated.metrics.median_return == pytest.approx(a_full.metrics.median_return)


def test_single_stock_collapse_does_not_trigger(frame):
    """
    Devlog s.49 — "do not rely on a single-stock percentage decline".
    Crater one name by 40% and leave the rest alone. Nothing should fire.
    """
    close = frame.close.copy()
    close.iloc[-6:, close.columns.get_loc("NVDA")] *= np.linspace(1.0, 0.60, 6)
    doctored = PriceFrame(close, frame.volume, frame.source,
                          frame.retrieved_at, synthetic=True)
    a = _assess(doctored, QUIET_DATE)
    assert a.level == AlertLevel.NONE, (
        f"A single-stock collapse produced {a.level.value}. The industry-wide "
        f"requirement has been broken.")


def test_broad_market_selloff_is_discounted(frame):
    """
    Devlog s.1 — a broad market sell-off is not an industry shock. If the S&P
    falls as hard as the industry, relative weakness must collapse.
    """
    close = frame.close.copy()
    col = close.columns.get_loc(CONFIG.market_benchmark)
    close.iloc[-6:, col] *= np.linspace(1.0, 0.84, 6)
    doctored = PriceFrame(close, frame.volume, frame.source,
                          frame.retrieved_at, synthetic=True)
    a = _assess(doctored, SHOCK_DATE)
    rel = next(c for c in a.components if c.name == "relative_weakness")
    baseline = next(c for c in _assess(frame, SHOCK_DATE).components
                    if c.name == "relative_weakness")
    assert rel.sub_score < baseline.sub_score
    assert a.metrics.n_underperforming < a.metrics.n_members


def test_fixture_provider_refuses_unmarked_data(tmp_path):
    """Devlog s.44.1 — synthetic data must never masquerade as real."""
    p = tmp_path / "unmarked.csv"
    pd.DataFrame([{"date": "2026-01-02", "ticker": "AAA", "close": 10.0,
                   "volume": 1000, "synthetic": False}]).to_csv(p, index=False)
    with pytest.raises(RuntimeError, match="not marked synthetic"):
        FixtureProvider(str(p)).fetch(["AAA"], dt.date(2026, 1, 1), dt.date(2026, 1, 3))


def test_weights_sum_to_one():
    CONFIG.weights.validate()


def test_breadth_is_proportional_not_a_fixed_count(frame):
    """
    Three companies falling is 60% of a 5-member group and 15% of a 20-member
    one. The trigger must scale with the group, not sit at a fixed count.
    """
    a = _assess(frame, SHOCK_DATE)
    breadth_check = next(t for t in a.triggers if "%" in t.label and "declining" in t.label)
    assert f"{CONFIG.detection.min_breadth_pct:.0%}" in breadth_check.label
    assert a.metrics.pct_declining >= CONFIG.detection.min_breadth_pct


def test_partial_breadth_does_not_trigger(frame):
    """
    Half the group falling hard is not an industry event. Build exactly that
    case and confirm the supermajority rule catches it.
    """
    from dipdector.data.providers import PriceFrame
    cp = SeedClassificationProvider()
    members = [c.ticker for c in cp.industry_members("Semiconductors", QUIET_DATE)]
    close = frame.close.copy()
    half = members[:len(members) // 2]
    for tk in half:
        close.iloc[-6:, close.columns.get_loc(tk)] *= np.linspace(1.0, 0.80, 6)
    doctored = PriceFrame(close, frame.volume, frame.source,
                          frame.retrieved_at, synthetic=True)
    a = _assess(doctored, QUIET_DATE)
    breadth_ok = a.metrics.pct_declining >= CONFIG.detection.min_breadth_pct
    assert not breadth_ok, "Half the group falling passed the supermajority test."
    assert a.level != AlertLevel.MAJOR_EVENT


def test_beta_is_discarded_when_regression_is_meaningless(frame):
    """
    A beta from a regression with near-zero R-squared is noise with a decimal
    point. It must be dropped, not displayed.
    """
    a = _assess(frame, SHOCK_DATE)
    m = a.metrics
    if m.r_squared is not None and m.r_squared < 0.10:
        assert m.beta_to_market is None and m.alpha is None
        assert any("Beta discarded" in w for w in m.data_warnings)
    if m.beta_to_market is not None:
        assert m.alpha == pytest.approx(m.median_return - m.expected_return, abs=1e-9)


def test_sp500_is_the_invariant_primary_benchmark():
    """
    The S&P 500 comparison must not vary by industry. Only the second
    comparator does.
    """
    assert CONFIG.market_benchmark == "^GSPC"
    assert not hasattr(CONFIG, "nasdaq_benchmark"), (
        "A fixed secondary index is back in the config. The second comparator "
        "must be resolved per industry.")


def test_benchmark_resolves_to_the_right_industry_etf():
    """Airlines get an airline ETF. Nothing gets the Nasdaq by default."""
    from dipdector.data.benchmarks import resolve
    assert resolve("Semiconductors", "Semiconductors",
                   "Information Technology").ticker == "SOXX"
    assert resolve("Passenger Airlines", "Passenger Airlines",
                   "Industrials").ticker == "JETS"
    assert resolve("Regional Banks", "Banks", "Financials").ticker == "KRE"
    # Unknown sub-industry falls back to the sector, never to a tech index.
    fb = resolve("Something Unmapped", "Also Unmapped", "Health Care")
    assert fb is not None and fb.ticker == "XLV"
    assert resolve("Unmapped", "Unmapped", "Unmapped Sector") is None


def test_etf_confirmation_trigger_is_evaluated_not_skipped(frame):
    """Devlog s.6.4 was previously always skipped. It must now be checked."""
    a = _assess(frame, SHOCK_DATE)
    assert a.metrics.benchmark_ticker == "SOXX"
    assert a.metrics.benchmark_return is not None
    labels = [t.label for t in a.triggers]
    assert any("ETF confirms" in l for l in labels), (
        "The benchmark-confirmation condition is missing from the trigger set.")


def test_missing_etf_is_skipped_not_treated_as_confirmation(frame):
    """
    Absence of a benchmark reading must never count as evidence. It should drop
    the trigger and score zero, not pass by default.
    """
    from dipdector.data.providers import PriceFrame
    close = frame.close.drop(columns=["SOXX"])
    stripped = PriceFrame(close, frame.volume, frame.source,
                          frame.retrieved_at, synthetic=True)
    a = _assess(stripped, SHOCK_DATE)
    assert a.metrics.benchmark_return is None
    assert not any("ETF confirms" in t.label for t in a.triggers)
    comp = next(c for c in a.components if c.name == "benchmark_confirmation")
    assert comp.sub_score == 0.0
    assert any("skipped rather than" in n for n in a.notes)
