"""
Generate a SYNTHETIC price fixture.

This exists so the detection engine can be exercised and unit-tested without
network access. It is NOT market data and must never be presented as such.

Devlog s.44.1 forbids inventing market data. The guard is threefold:
  1. every row carries synthetic=True, and FixtureProvider refuses files without it
  2. tickers are prefixed SYN- by default so they cannot be confused with real ones
  3. the shock is injected at a known date so tests assert on a known answer

The generator produces a quiet regime, then a correlated industry-wide decline
driven by a shared factor, so that breadth, correlation and abnormality all move
the way a real industry shock would. Parameters are chosen to sit just past the
devlog s.6 thresholds, not far past them, so the test is not trivially easy.
"""

from __future__ import annotations

import argparse
import datetime as dt

import numpy as np
import pandas as pd


def make(
    tickers: list[str],
    market_ticker: str = "^GSPC",
    nasdaq_ticker: str = "^NDX",
    n_days: int = 420,
    shock_start_from_end: int = 5,
    shock_total: float = -0.15,
    market_shock_total: float = -0.02,
    seed: int = 7,
) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    end = dt.date(2026, 6, 30)
    dates = pd.bdate_range(end=pd.Timestamp(end), periods=n_days)

    # Shared market factor plus idiosyncratic noise.
    market_daily = rng.normal(0.0004, 0.008, n_days)
    industry_factor = rng.normal(0.0002, 0.009, n_days)

    rows = []
    shock_idx = n_days - shock_start_from_end
    shock_days = np.arange(shock_start_from_end)

    for t in tickers:
        beta_m = rng.uniform(0.9, 1.4)
        beta_i = rng.uniform(0.8, 1.2)
        idio = rng.normal(0.0, 0.011, n_days)
        daily = beta_m * market_daily + beta_i * industry_factor + idio

        # Inject the shock: a shared, front-loaded decline across the window.
        weights = np.array([0.45, 0.25, 0.15, 0.10, 0.05])[:shock_start_from_end]
        weights = weights / weights.sum()
        per_name = shock_total * rng.uniform(0.75, 1.25)
        daily[shock_idx:] += np.log1p(per_name) * weights

        px = 100 * np.exp(np.cumsum(daily))
        base_vol = rng.uniform(8e6, 4e7)
        vol = base_vol * np.exp(rng.normal(0, 0.25, n_days))
        vol[shock_idx:] *= rng.uniform(2.4, 4.0)

        for d, p, v in zip(dates, px, vol):
            rows.append({"date": d, "ticker": t, "close": round(float(p), 4),
                         "volume": int(v), "synthetic": True})

    # The industry ETF. Built from the same industry factor but with a heavier
    # shock, because these ETFs hold smaller operators that get hit harder than
    # the S&P 500 large caps we are measuring.
    etf_daily = industry_factor * 1.05 + rng.normal(0.0002, 0.004, n_days)
    etf_daily[shock_idx:] += np.log1p(shock_total * 1.15) / shock_start_from_end
    etf_px = 200 * np.exp(np.cumsum(etf_daily))
    etf_vol = rng.uniform(2e6, 6e6, n_days)
    etf_vol[shock_idx:] *= 3.0
    for d, p_, v in zip(dates, etf_px, etf_vol):
        rows.append({"date": d, "ticker": "SOXX", "close": round(float(p_), 4),
                     "volume": int(v), "synthetic": True})

    for bench, drift, vol_scale, total in (
        (market_ticker, 0.0004, 0.006, market_shock_total),
        (nasdaq_ticker, 0.0005, 0.008, market_shock_total * 1.4),
    ):
        daily = rng.normal(drift, vol_scale, n_days)
        daily[shock_idx:] += np.log1p(total) / shock_start_from_end
        px = 4000 * np.exp(np.cumsum(daily))
        for d, p in zip(dates, px):
            rows.append({"date": d, "ticker": bench, "close": round(float(p), 4),
                         "volume": 0, "synthetic": True})

    return pd.DataFrame(rows)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="dipdector/fixtures/synthetic_semis.csv")
    args = ap.parse_args()

    from dipdector.data.universe import default_provider
    tickers = [c.ticker for c in default_provider().companies_as_of(dt.date(2026, 6, 30))
               if c.sub_industry.startswith("Semiconductor")]

    df = make(tickers)
    df.to_csv(args.out, index=False)
    print(f"Wrote {len(df):,} SYNTHETIC rows for {df.ticker.nunique()} tickers -> {args.out}")
