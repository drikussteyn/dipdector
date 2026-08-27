"""
Backtest runner.

  python -m dipdector.backtest.run --provider fixture \\
      --fixture dipdector/fixtures/synthetic_history.csv \\
      --start 2014-01-01 --end 2026-06-30

Order of operations matters. The event study runs FIRST and prints first,
because if the pattern isn't there, no amount of portfolio simulation will
create it, and a good-looking equity curve on a weak event study means the
returns came from something other than the thesis.
"""

from __future__ import annotations

import argparse
import datetime as dt
import warnings

import numpy as np
import pandas as pd

from ..config import CONFIG
from ..analysis.recovery import score_candidates
from ..data.benchmarks import all_tickers as benchmark_tickers
from ..data.providers import get_provider
from ..data.universe import default_provider
from ..engine.detection import AlertLevel
from . import event_study as es
from . import metrics as mx
from .scan import events_frame, scan_history
from .simulator import SCENARIOS, Simulator, StrategyConfig

warnings.filterwarnings("ignore", category=FutureWarning)
pd.set_option("display.width", 200)


def pct(x, dp=1):
    return "—" if x is None or (isinstance(x, float) and np.isnan(x)) else f"{x*100:+.{dp}f}%"


def rule(title=""):
    print(f"\n{'─' * 72}")
    if title:
        print(title)
        print("─" * 72)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--provider", default="fixture")
    p.add_argument("--fixture", default="dipdector/fixtures/synthetic_history.csv")
    p.add_argument("--start", required=True)
    p.add_argument("--end", required=True)
    p.add_argument("--level", default="INVESTIGATE",
                   choices=["WATCH", "INVESTIGATE", "MAJOR_EVENT"])
    p.add_argument("--cooldown", type=int, default=60)
    p.add_argument("--step", type=int, default=1)
    p.add_argument("--capital", type=float, default=100_000)
    p.add_argument("--max-position", type=float, default=1.0)
    p.add_argument("--stop-loss", type=float, default=None)
    p.add_argument("--controls", action="store_true", help="run random + naive controls")
    a = p.parse_args()

    start, end = dt.date.fromisoformat(a.start), dt.date.fromisoformat(a.end)
    cp = default_provider()
    tickers = ([c.ticker for c in cp.companies_as_of(end)]
               + [CONFIG.market_benchmark] + benchmark_tickers())

    kwargs = {"path": a.fixture} if a.provider == "fixture" else {}
    frame = get_provider(a.provider, **kwargs).fetch(
        tickers, start - dt.timedelta(days=800), end)

    rule("SETUP")
    print(f"Source          {frame.source}"
          + ("   ⚠ SYNTHETIC — pipeline test only" if frame.synthetic else ""))
    print(f"Period          {start} → {end}")
    print(f"Universe        {len(tickers)-2} companies, "
          f"{len(cp.industries(end))} sub-industries")
    print(f"Parameters      {CONFIG.params_version}")
    print(f"Alert level     {a.level}, {a.cooldown}-day cooldown")
    print("\n⚠ Survivorship bias: the universe uses current membership for all "
          "dates.\n  Devlog s.29 forbids relying on this. Treat everything below "
          "as indicative.")

    rule("SCANNING")
    events = scan_history(frame, cp, start, end,
                          min_level=AlertLevel[a.level],
                          cooldown_days=a.cooldown, step=a.step)
    years = (end - start).days / 365.25
    print(f"\n{len(events)} distinct events in {years:.1f} years "
          f"({len(events)/years:.1f} per year)")
    if events:
        print()
        print(events_frame(events).to_string(index=False))

    if not events:
        print("\nNothing fired. Either the thresholds are too tight or the "
              "period is too quiet. Try --level WATCH.")
        return

    # ---- EVENT STUDY: does the bounce exist? ---------------------------
    exec_base = SCENARIOS["base"]
    outcomes = es.run_study(events, frame.close, exec_base.entry_delay)

    rule("EVENT STUDY — what happened after each detection")
    print(f"Entry assumed {exec_base.entry_delay} trading days after the alert. "
          f"No bottom-picking.\n")
    summ = es.summarise(outcomes)
    if not summ.empty:
        disp = summ.copy()
        for c in ("median", "mean", "p10", "p90", "worst", "best"):
            disp[c] = disp[c].map(lambda v: pct(v))
        disp["hit_rate"] = disp["hit_rate"].map(lambda v: f"{v:.0%}")
        print(disp.to_string(index=False))

    prof = es.recovery_profile(outcomes)
    print(f"\nRecovered to pre-shock level within 2 years:  "
          f"{prof['recovered_within_2y']}/{prof['events_resolved']} "
          f"({prof['recovery_rate']:.0%})")
    print(f"Median time to recover:                       "
          f"{prof['median_days_to_recover']:.0f} trading days")
    print(f"Slowest decile:                               "
          f"{prof['p90_days_to_recover']:.0f} trading days")
    print(f"Median further fall AFTER buying:             "
          f"{pct(prof['median_further_drawdown'])}")
    print(f"Worst further fall after buying:              "
          f"{pct(prof['worst_further_drawdown'])}")

    bucket = es.by_bucket(outcomes, "6m", "score")
    if not bucket.empty:
        print("\nDoes a higher shock score predict a better bounce? (6m)")
        b = bucket.copy()
        b["median"] = b["median"].map(pct)
        b["hit_rate"] = b["hit_rate"].map(lambda v: f"{v:.0%}")
        print(b.to_string(index=False))

    # ---- PORTFOLIO SIMULATION -----------------------------------------
    cands = {}
    for i, ev in enumerate(events):
        if ev.assessment is not None:
            ranked = score_candidates(ev.assessment.metrics, frame.close.loc[
                frame.close.index <= pd.Timestamp(ev.detected_on)], CONFIG)
            cands[i] = [c.ticker for c in ranked]

    strat = StrategyConfig(starting_capital=a.capital,
                           max_position_pct=a.max_position,
                           stop_loss=a.stop_loss)

    rule("PORTFOLIO SIMULATION — could you have captured it?")
    if strat.deviations():
        for d in strat.deviations():
            print(f"  · {d}")
        print()

    rows = []
    results = {}
    for name, xc in SCENARIOS.items():
        res = Simulator(frame.close, strat, xc).run(events, cands, start, end)
        results[name] = res
        perf = mx.performance(res.equity)
        ts = mx.trade_stats(res.trades)
        rows.append({
            "scenario": name,
            "entry lag": f"{xc.entry_delay}d",
            "costs": f"{xc.cost_bps:.0f}bp",
            "CAGR": pct(perf.get("cagr")),
            "total": pct(perf.get("total_return"), 0),
            "max DD": pct(perf.get("max_drawdown"), 0),
            "Sharpe": f"{perf.get('sharpe', float('nan')):.2f}",
            "Sortino": f"{perf.get('sortino', float('nan')):.2f}",
            "trades": ts.get("n_round_trips", 0),
            "win rate": f"{ts.get('win_rate', float('nan')):.0%}"
                        if ts.get("n_round_trips") else "—",
            "med hold": f"{ts.get('median_hold_days', 0):.0f}d"
                        if ts.get("n_round_trips") else "—",
        })
    print(pd.DataFrame(rows).to_string(index=False))
    print(f"\nEvents taken {results['base'].events_taken}, "
          f"skipped {results['base'].events_skipped} "
          f"(already holding, too soon to rotate, or no cash)")

    # Slower reaction should not beat faster reaction. If it does, the result is
    # being driven by which specific events happened to be caught, not by the
    # strategy — i.e. the sample is too small to conclude anything from.
    cagrs = {n: mx.performance(r.equity).get("cagr", float("nan"))
             for n, r in results.items()}
    if cagrs["conservative"] > cagrs["base"] or cagrs["base"] > cagrs["optimistic"]:
        print("\n⚠ Reacting more slowly produced a BETTER result here. That is "
              "not a real\n  effect — it means the outcome depends on which "
              "handful of events got caught,\n  not on the strategy. Treat the "
              "returns above as noise until the event count\n  is much higher.")

    # ---- CONTROLS ------------------------------------------------------
    rule("BENCHMARKS")
    base_perf = mx.performance(results["base"].equity)
    bench_rows = [{
        "strategy": "DipDector (base case)",
        "CAGR": pct(base_perf.get("cagr")),
        "max DD": pct(base_perf.get("max_drawdown"), 0),
        "Sharpe": f"{base_perf.get('sharpe', float('nan')):.2f}",
    }]
    for label, tk in (("S&P 500 buy & hold", CONFIG.market_benchmark),):
        bh = mx.buy_and_hold(frame.close, tk, start, end, a.capital)
        pf = mx.performance(bh)
        if pf:
            bench_rows.append({"strategy": label, "CAGR": pct(pf["cagr"]),
                               "max DD": pct(pf["max_drawdown"], 0),
                               "Sharpe": f"{pf['sharpe']:.2f}"})

    if a.controls:
        print("  running controls…", flush=True)
        naive = mx.naive_dip_control(
            frame.close, [c.ticker for c in cp.companies_as_of(end)],
            start, end, capital=a.capital)
        pf = mx.performance(naive)
        if pf:
            bench_rows.append({"strategy": "Naive single-stock dip buying",
                               "CAGR": pct(pf["cagr"]),
                               "max DD": pct(pf["max_drawdown"], 0),
                               "Sharpe": f"{pf['sharpe']:.2f}"})
        rnd = mx.random_event_control(events, frame.close, cp, strat,
                                      exec_base, start, end, n_runs=30)
        if not rnd.empty:
            bench_rows.append({
                "strategy": f"Random dates (median of {len(rnd)})",
                "CAGR": pct(rnd["cagr"].median()),
                "max DD": pct(rnd["max_drawdown"].median(), 0), "Sharpe": "—"})
            beat = (rnd["cagr"] < base_perf["cagr"]).mean()
            print(pd.DataFrame(bench_rows).to_string(index=False))
            print(f"\nDipDector beat {beat:.0%} of {len(rnd)} random-timing runs.")
            if beat < 0.8:
                print("  ⚠ That is not a convincing margin over random entry "
                      "timing.\n    The detector may not be adding much.")
            return
    print(pd.DataFrame(bench_rows).to_string(index=False))
    print("\nRe-run with --controls for the random-timing and naive-dip controls. "
          "\nThose are the comparisons that decide whether the detector earns "
          "its keep.")


if __name__ == "__main__":
    main()
