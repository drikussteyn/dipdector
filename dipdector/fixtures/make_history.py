"""
Generate a long SYNTHETIC price history for backtester development.

Same rules as the short fixture: every row is flagged synthetic, and
FixtureProvider refuses anything that isn't.

What makes this one useful is that it contains BOTH kinds of event, seeded at
known dates with known outcomes:

  TEMPORARY  — sharp correlated fall, then full recovery over 3-9 months.
               This is the pattern the strategy is betting on.
  STRUCTURAL — the same sharp correlated fall, indistinguishable at the moment
               of detection, followed by permanent impairment. The industry
               drifts down for years and never regains its level.

That second category is the whole point. If the backtester only ever sees
recoveries, it will report a wonderful strategy and teach us nothing. A detector
looking at day 5 of a structural event sees roughly what it sees on day 5 of a
temporary one — that ambiguity is real, and the simulation has to contain it or
the results are meaningless.

The ground truth is written to a sidecar CSV so tests can check whether the
event study correctly separates the two populations. The backtester itself must
never read that file.
"""

from __future__ import annotations

import argparse
import datetime as dt

import numpy as np
import pandas as pd

# (industry, offset_in_years, kind, severity)
SEEDED_EVENTS = [
    ("Semiconductors", 1.6, "temporary", -0.19),
    ("Passenger Airlines", 2.4, "temporary", -0.22),
    ("Oil & Gas Equipment & Services", 3.1, "structural", -0.26),
    ("Regional Banks", 4.3, "temporary", -0.17),
    ("Hotels, Resorts & Cruise Lines", 5.0, "structural", -0.31),
    ("Semiconductor Materials & Equipment", 6.2, "temporary", -0.21),
    ("Passenger Airlines", 7.1, "structural", -0.28),
    ("Regional Banks", 8.4, "temporary", -0.20),
    ("Semiconductors", 9.3, "temporary", -0.24),
    ("Hotels, Resorts & Cruise Lines", 10.6, "temporary", -0.18),
    ("Oil & Gas Equipment & Services", 11.2, "temporary", -0.16),
    ("Regional Banks", 12.5, "structural", -0.23),
]

SHOCK_DAYS = 5


def build(years: int = 14, seed: int = 11, end: dt.date = dt.date(2026, 6, 30)):
    from dipdector.data.universe import default_provider

    rng = np.random.default_rng(seed)
    cp = default_provider()
    industries = cp.industries(end)
    dates = pd.bdate_range(end=pd.Timestamp(end), periods=int(years * 252))
    n = len(dates)

    # One market factor everything loads on.
    market_daily = rng.normal(0.00035, 0.0085, n)
    # Two mild market-wide drawdowns, so the detector has chances to produce the
    # false positive it should be producing: broad selloffs that are not
    # industry shocks.
    for frac in (0.35, 0.72):
        i = int(n * frac)
        market_daily[i:i + 12] += np.log1p(-0.09) / 12

    truth = []
    industry_paths = {}

    for industry, members in industries.items():
        ind_daily = rng.normal(0.0001, 0.0075, n)
        events_here = [e for e in SEEDED_EVENTS if e[0] == industry]

        for _, yr_off, kind, severity in events_here:
            start = int(yr_off * 252)
            if start + 300 >= n:
                continue
            # The fall itself: identical shape for both kinds.
            w = np.array([0.42, 0.24, 0.16, 0.11, 0.07])
            ind_daily[start:start + SHOCK_DAYS] += np.log1p(severity) * w

            if kind == "temporary":
                # Recovery over a randomised 3-9 months, back through the fall.
                rec_len = int(rng.uniform(63, 189))
                if start + SHOCK_DAYS + rec_len < n:
                    ind_daily[start + SHOCK_DAYS:start + SHOCK_DAYS + rec_len] += (
                        -np.log1p(severity) * 1.02 / rec_len)
            else:
                # Permanent impairment: no bounce, plus a long slow bleed.
                bleed_len = min(int(252 * 2.5), n - start - SHOCK_DAYS)
                ind_daily[start + SHOCK_DAYS:start + SHOCK_DAYS + bleed_len] += (
                    np.log1p(-0.30) / bleed_len)

            truth.append({
                "industry": industry,
                "shock_start": dates[start].date(),
                "kind": kind,
                "severity": severity,
            })

        industry_paths[industry] = ind_daily

    rows = []
    for industry, members in industries.items():
        ind_daily = industry_paths[industry]
        for c in members:
            beta_m = rng.uniform(0.8, 1.45)
            beta_i = rng.uniform(0.75, 1.25)
            idio = rng.normal(0.00005, 0.0115, n)
            daily = beta_m * market_daily + beta_i * ind_daily + idio
            px = rng.uniform(25, 140) * np.exp(np.cumsum(daily))

            base_vol = rng.uniform(5e6, 5e7)
            vol = base_vol * np.exp(rng.normal(0, 0.28, n))
            # Volume spikes on the shock windows for this industry.
            for _, yr_off, _, _ in [e for e in SEEDED_EVENTS if e[0] == industry]:
                s = int(yr_off * 252)
                if s + SHOCK_DAYS < n:
                    vol[s:s + SHOCK_DAYS] *= rng.uniform(2.2, 4.2)

            for d, p, v in zip(dates, px, vol):
                rows.append({"date": d, "ticker": c.ticker, "close": round(float(p), 4),
                             "volume": int(v), "synthetic": True})

    # Industry ETFs, one per group, resolved from the same registry the engine
    # uses. Each carries a heavier shock than the large-cap basket it shadows.
    from dipdector.data.benchmarks import resolve as resolve_benchmark

    emitted = set()
    for industry, members in industries.items():
        bm = resolve_benchmark(members[0].sub_industry, members[0].industry,
                               members[0].sector)
        if bm is None or bm.ticker in emitted:
            continue
        emitted.add(bm.ticker)
        etf_daily = industry_paths[industry] * 1.08 + rng.normal(0.0002, 0.0035, n)
        for _, yr_off, kind, sev in [e for e in SEEDED_EVENTS if e[0] == industry]:
            st = int(yr_off * 252)
            if st + SHOCK_DAYS < n:
                etf_daily[st:st + SHOCK_DAYS] += np.log1p(sev * 0.18) / SHOCK_DAYS
        etf_px = rng.uniform(40, 180) * np.exp(np.cumsum(etf_daily))
        for d, p_ in zip(dates, etf_px):
            rows.append({"date": d, "ticker": bm.ticker, "close": round(float(p_), 4),
                         "volume": int(rng.uniform(2e6, 9e6)), "synthetic": True})

    for bench, drift, sc in (("^GSPC", 0.00035, 0.9), ("^NDX", 0.00045, 1.25)):
        daily = market_daily * sc + rng.normal(drift * 0.2, 0.002, n)
        px = 1200 * np.exp(np.cumsum(daily))
        for d, p in zip(dates, px):
            rows.append({"date": d, "ticker": bench, "close": round(float(p), 4),
                         "volume": 0, "synthetic": True})

    return pd.DataFrame(rows), pd.DataFrame(truth)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="dipdector/fixtures/synthetic_history.csv")
    ap.add_argument("--truth", default="dipdector/fixtures/synthetic_truth.csv")
    ap.add_argument("--years", type=int, default=14)
    a = ap.parse_args()

    df, truth = build(years=a.years)
    df.to_csv(a.out, index=False)
    truth.to_csv(a.truth, index=False)
    print(f"Wrote {len(df):,} SYNTHETIC rows, {df.ticker.nunique()} tickers, "
          f"{df.date.min().date()} → {df.date.max().date()} -> {a.out}")
    print(f"Ground truth: {len(truth)} seeded events "
          f"({(truth.kind == 'temporary').sum()} temporary, "
          f"{(truth.kind == 'structural').sum()} structural) -> {a.truth}")
