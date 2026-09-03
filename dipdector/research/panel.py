"""
Precomputed statistics panel for threshold research.

The detector's thresholds were taken from the devlog and never derived from
anything. This builds the evidence to derive them: for every (date, industry)
pair over the available history, the raw statistics a trigger might test, and
the forward returns that say whether it was worth acting on.

The architecture matters. Scoring a parameter combination by re-running the
whole detector takes minutes, so a sweep of a few hundred combinations is not
feasible that way. Measurement is therefore separated from thresholding: the
statistics are computed once, and a parameter set becomes a cheap filter over
the resulting table. Nothing in here decides what a good threshold is.

TWO LIMITS THAT BOUND EVERY CONCLUSION DRAWN FROM THIS PANEL.

1. Survivorship bias, and it is far worse here than in the daily scan. The
   universe is today's S&P 500 applied to every historical date, so a company
   that fell and never recovered is absent by construction — it left the index
   before today. Measuring "which dips recovered" on a survivors-only universe
   systematically overstates recovery and biases any fitted threshold toward
   buying more dips. The further back the window reaches, the worse it gets:
   42% of today's members have prices in 1986, 96% by 2020.

2. Forward returns are outcome data. They are used only to evaluate a
   parameter set after the fact and never enter a detection statistic, which
   is what keeps the panel free of lookahead. The detection columns at date t
   use closes at or before t; the forward columns deliberately do not.
"""

from __future__ import annotations

import datetime as dt
from typing import Dict, List, Optional, Sequence

import numpy as np
import pandas as pd

# Windows a trigger might measure the fall over, in trading days.
WINDOWS = (2, 3, 5, 10, 20)
# "Materially declining" cut-offs to test breadth at.
DECLINE_LEVELS = (-0.03, -0.05, -0.10)
# Forward horizons, in trading days: ~1, 3, 6, 12 months.
HORIZONS = {"fwd_1m": 21, "fwd_3m": 63, "fwd_6m": 126, "fwd_12m": 252}


def _basket(close: pd.DataFrame, members: Sequence[str]) -> pd.DataFrame:
    cols = [m for m in members if m in close.columns]
    return close[cols] if cols else pd.DataFrame(index=close.index)


def build_panel(close: pd.DataFrame,
                industries: Dict[str, List[str]],
                market: pd.Series,
                step: int = 5,
                min_members: int = 3,
                start: Optional[dt.date] = None) -> pd.DataFrame:
    """
    One row per (date, industry) with enough columns to evaluate any trigger.

    `close` is adjusted closes, `industries` maps name -> tickers, `market` is
    the index level used as the market comparator.
    """
    # --- trailing returns, one frame per window --------------------------
    rets = {w: close / close.shift(w) - 1.0 for w in WINDOWS}
    mkt = {w: market / market.shift(w) - 1.0 for w in WINDOWS}

    # --- market-wide breadth, the base rate an industry is judged against -
    mkt_breadth = {
        (w, lvl): (rets[w] <= lvl).sum(axis=1) / rets[w].notna().sum(axis=1)
        for w in WINDOWS for lvl in DECLINE_LEVELS
    }

    # --- forward returns of an equally weighted basket -------------------
    fwd = {name: close.shift(-h) / close - 1.0 for name, h in HORIZONS.items()}
    # Deepest additional fall after entry, which decides holdability.
    fwd_min = close.rolling(HORIZONS["fwd_6m"]).min().shift(-HORIZONS["fwd_6m"]) / close - 1.0

    dates = close.index
    if start is not None:
        dates = dates[dates >= pd.Timestamp(start)]
    dates = dates[::step]

    rows = []
    for name, members in industries.items():
        b = _basket(close, members)
        if b.shape[1] < min_members:
            continue
        n_live = b.notna().sum(axis=1)

        per_w = {}
        for w in WINDOWS:
            r = rets[w][b.columns]
            per_w[w] = {
                "median": r.median(axis=1),
                "worst": r.min(axis=1),
                "disp": r.std(axis=1),
                **{f"breadth{lvl}": (r <= lvl).sum(axis=1) / n_live
                   for lvl in DECLINE_LEVELS},
            }

        # Abnormality: the window return against this industry's own trailing
        # distribution of same-length window returns. Without it the panel
        # fires roughly five times more than the engine does, and the sweep's
        # event counts cannot be read against the live configuration.
        abn = {}
        for w in WINDOWS:
            r = per_w[w]["median"]
            mu = r.rolling(252, min_periods=60).mean()
            sd = r.rolling(252, min_periods=60).std()
            abn[w] = -(r - mu) / sd.replace(0.0, np.nan)

        fwd_b = {k: v[b.columns].mean(axis=1) for k, v in fwd.items()}
        fwd_min_b = fwd_min[b.columns].mean(axis=1)

        sel = dates[dates.isin(b.index)]
        frame = {"date": sel, "industry": name,
                 "n_members": n_live.reindex(sel).values}
        for w in WINDOWS:
            frame[f"ret_{w}d"] = per_w[w]["median"].reindex(sel).values
            frame[f"worst_{w}d"] = per_w[w]["worst"].reindex(sel).values
            frame[f"disp_{w}d"] = per_w[w]["disp"].reindex(sel).values
            frame[f"mkt_{w}d"] = mkt[w].reindex(sel).values
            frame[f"abn_{w}d"] = abn[w].reindex(sel).values
            for lvl in DECLINE_LEVELS:
                tag = f"{abs(int(lvl * 100))}"
                frame[f"breadth{tag}_{w}d"] = \
                    per_w[w][f"breadth{lvl}"].reindex(sel).values
                frame[f"mktbreadth{tag}_{w}d"] = \
                    mkt_breadth[(w, lvl)].reindex(sel).values
        for k in HORIZONS:
            frame[k] = fwd_b[k].reindex(sel).values
        frame["fwd_min_6m"] = fwd_min_b.reindex(sel).values
        rows.append(pd.DataFrame(frame))

    panel = pd.concat(rows, ignore_index=True)
    panel = panel[panel["n_members"] >= min_members].reset_index(drop=True)
    return panel
