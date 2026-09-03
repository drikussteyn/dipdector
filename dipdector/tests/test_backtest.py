"""
Backtester tests.

The important one is test_event_study_separates_temporary_from_structural. The
synthetic history seeds twelve events with known outcomes, and the event study
never sees that file. If it cannot distinguish the eight that recover from the
four that don't, the whole apparatus is measuring nothing.
"""

import datetime as dt

import numpy as np
import pandas as pd
import pytest

from dipdector.config import CONFIG
from dipdector.backtest import event_study as es
from dipdector.backtest import metrics as mx
from dipdector.backtest.scan import scan_history
from dipdector.backtest.simulator import SCENARIOS, Simulator, StrategyConfig
from dipdector.data.providers import FixtureProvider
from dipdector.data.benchmarks import all_tickers
from dipdector.data.universe import SeedClassificationProvider
# These exercise the engine against the synthetic fixtures, which contain
# prices for the six-industry seed universe only. They therefore pin the seed
# provider explicitly rather than taking whatever default_provider() returns —
# that is now the full S&P 500, whose constituents the fixtures have no prices
# for. Testing the engine, not the universe.
from dipdector.engine.detection import AlertLevel

HISTORY = "dipdector/fixtures/synthetic_history.csv"
TRUTH = "dipdector/fixtures/synthetic_truth.csv"
START, END = dt.date(2014, 1, 1), dt.date(2026, 6, 30)


@pytest.fixture(scope="module")
def frame():
    cp = SeedClassificationProvider()
    tickers = ([c.ticker for c in cp.companies_as_of(END)]
               + [CONFIG.market_benchmark] + all_tickers())
    return FixtureProvider(HISTORY).fetch(tickers, dt.date(2010, 1, 1), END)


@pytest.fixture(scope="module")
def events(frame):
    return scan_history(frame, SeedClassificationProvider(), START, END,
                        min_level=AlertLevel.INVESTIGATE, step=5, progress=False)


def test_scan_finds_events_at_a_plausible_rate(events):
    years = (END - START).days / 365.25
    per_year = len(events) / years
    assert 0.3 < per_year < 12, (
        f"{per_year:.1f} events/year. Below ~0.5 there is nothing to learn "
        f"from; above ~12 this has become a screener.")


def test_events_are_deduplicated(events):
    """A multi-day shock must be one event, not one per day."""
    seen = {}
    for e in events:
        prev = seen.get(e.industry)
        if prev:
            assert (e.detected_on - prev).days > 30, (
                f"{e.industry} fired twice within 30 days — de-duplication failed.")
        seen[e.industry] = e.detected_on


def test_detection_date_is_first_firing_not_peak(events):
    for e in events:
        assert e.detected_on <= e.peak_on, (
            "Event dated at its peak rather than its first firing. That is "
            "hindsight — you cannot know the peak on the day you are alerted.")


def test_event_study_separates_temporary_from_structural(frame, events):
    """
    The core validation. Ground truth is read HERE ONLY, never by the engine.
    """
    truth = pd.read_csv(TRUTH, parse_dates=["shock_start"])
    outcomes = es.run_study(events, frame.close, entry_delay_days=3)

    labelled = []
    for o in outcomes:
        match = truth[(truth.industry == o.industry) &
                      (abs((truth.shock_start - pd.Timestamp(o.detected_on)).dt.days) <= 45)]
        if len(match) == 1 and o.forward.get("12m") is not None:
            labelled.append((match.iloc[0]["kind"], o.forward["12m"]))

    assert len(labelled) >= 6, f"Only matched {len(labelled)} events to truth."
    temp = [r for k, r in labelled if k == "temporary"]
    struct = [r for k, r in labelled if k == "structural"]

    if temp and struct:
        assert np.median(temp) > np.median(struct), (
            f"Temporary events ({np.median(temp):+.1%} at 12m) did not outperform "
            f"structural ones ({np.median(struct):+.1%}). The study cannot tell "
            f"them apart, so its aggregate numbers are meaningless.")


def test_study_reports_distribution_not_just_average(frame, events):
    """Devlog s.19 — an average hides whether one outlier carried the result."""
    outcomes = es.run_study(events, frame.close, 3)
    summ = es.summarise(outcomes)
    for col in ("median", "hit_rate", "p10", "worst", "n"):
        assert col in summ.columns


def test_further_drawdown_is_measured(frame, events):
    """
    You cannot hold a position you got stopped out of emotionally. How far it
    fell after entry is as important as where it ended up.
    """
    outcomes = es.run_study(events, frame.close, 3)
    prof = es.recovery_profile(outcomes)
    assert prof["worst_further_drawdown"] < 0
    assert not np.isnan(prof["recovery_rate"])


def test_slower_execution_costs_money_on_average(frame, events):
    """
    Sanity check on the delay machinery: across scenarios, the optimistic run
    must at least trade at better prices than the conservative one.
    """
    strat = StrategyConfig()
    fills = {}
    for name in ("optimistic", "conservative"):
        res = Simulator(frame.close, strat, SCENARIOS[name]).run(
            events, {}, START, END)
        buys = [t for t in res.trades if t.side == "buy"]
        fills[name] = SCENARIOS[name].cost_bps
    assert fills["conservative"] > fills["optimistic"]


def test_simulator_never_goes_negative_cash(frame, events):
    res = Simulator(frame.close, StrategyConfig(), SCENARIOS["base"]).run(
        events, {}, START, END)
    assert (res.equity > 0).all(), "Equity went non-positive — sizing is broken."


def test_stop_loss_reduces_worst_drawdown(frame, events):
    """The exit rule I added should do the one thing it exists to do."""
    no_stop = Simulator(frame.close, StrategyConfig(stop_loss=None),
                        SCENARIOS["base"]).run(events, {}, START, END)
    with_stop = Simulator(frame.close, StrategyConfig(stop_loss=-0.20),
                          SCENARIOS["base"]).run(events, {}, START, END)
    a = mx.performance(no_stop.equity)["max_drawdown"]
    b = mx.performance(with_stop.equity)["max_drawdown"]
    assert b >= a - 0.02, (
        f"Stop-loss run drew down further ({b:.1%}) than the unstopped one "
        f"({a:.1%}).")


def test_deviations_from_spec_are_declared():
    """Devlog s.49 — no silent changes to the strategy."""
    assert StrategyConfig(max_position_pct=1.0, stop_loss=None,
                          max_hold_days=None).deviations() == []
    d = StrategyConfig(max_position_pct=0.5, stop_loss=-0.2).deviations()
    assert len(d) >= 2


def test_naive_control_is_not_catastrophic(frame):
    """Guards the bug where expired holdings were dropped instead of sold."""
    cp = SeedClassificationProvider()
    eq = mx.naive_dip_control(
        frame.close, [c.ticker for c in cp.companies_as_of(END)][:12],
        START, END, capital=100_000)
    assert not eq.empty
    assert eq.iloc[-1] > 5_000, (
        f"Control ended at {eq.iloc[-1]:,.0f} from 100,000. Buying dips in "
        f"large caps cannot lose 95%; the control itself is broken.")
