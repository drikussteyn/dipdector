"""
Event study — the direct test of the premise.

The strategy rests on one empirical claim: when an industry crashes together,
it bounces, and it bounces fast enough to be worth the trade. This module tests
that claim and nothing else. It is deliberately separate from the portfolio
simulator, because the two answer different questions:

  event study  — does the pattern exist at all?
  simulator    — could you actually have captured it, after delays and costs?

If the event study comes back weak, the simulator is not worth reading.

Reported as a DISTRIBUTION, never as an average. An average forward return of
+8% is consistent with "eight events at +12% and four at nothing", and it is
also consistent with "eleven events flat and one at +96%". Those are completely
different strategies and only one of them is investable. So every horizon
reports median, hit rate, worst case, and the bottom decile.

Entry is measured from detection + reaction delay, not from the bottom. Devlog
s.29 forbids knowing future bottoms.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

HORIZONS = {"1m": 21, "3m": 63, "6m": 126, "12m": 252}


@dataclass
class EventOutcome:
    industry: str
    detected_on: dt.date
    entry_on: Optional[dt.date]
    score: float
    decline_at_detection: float
    forward: Dict[str, Optional[float]] = field(default_factory=dict)
    days_to_recover_preshock: Optional[int] = None
    max_further_drawdown: Optional[float] = None
    censored: bool = False          # history ran out before the last horizon


def _basket(close: pd.DataFrame, tickers: List[str]) -> pd.Series:
    """Equal-weighted basket of the affected names, rebased."""
    cols = [t for t in tickers if t in close.columns]
    sub = close[cols].dropna(how="all")
    if sub.empty:
        return pd.Series(dtype=float)
    return (sub / sub.bfill().iloc[0]).mean(axis=1)


def study_event(event, close: pd.DataFrame, entry_delay_days: int = 3,
                preshock_lookback: int = 10) -> EventOutcome:
    basket = _basket(close, event.tickers)
    idx = basket.index
    det_ts = pd.Timestamp(event.detected_on)

    pos_arr = idx.searchsorted(det_ts)
    if pos_arr >= len(idx):
        return EventOutcome(event.industry, event.detected_on, None, event.score,
                            event.median_return, censored=True)
    det_pos = int(pos_arr)
    entry_pos = det_pos + entry_delay_days
    if entry_pos >= len(idx):
        return EventOutcome(event.industry, event.detected_on, None, event.score,
                            event.median_return, censored=True)

    entry_px = float(basket.iloc[entry_pos])
    out = EventOutcome(
        industry=event.industry, detected_on=event.detected_on,
        entry_on=idx[entry_pos].date(), score=event.score,
        decline_at_detection=event.median_return,
    )

    for label, h in HORIZONS.items():
        tgt = entry_pos + h
        out.forward[label] = (float(basket.iloc[tgt] / entry_px - 1.0)
                              if tgt < len(idx) else None)
    out.censored = out.forward["12m"] is None

    # How far it kept falling after you bought. This is the number that decides
    # whether you could have held the position.
    tail = basket.iloc[entry_pos:entry_pos + HORIZONS["12m"]]
    if len(tail) > 1:
        out.max_further_drawdown = float(tail.min() / entry_px - 1.0)

    # Time to regain the pre-shock level. Uncapped recovery is meaningless, so
    # anything beyond two years is recorded as "did not recover".
    pre_pos = max(det_pos - preshock_lookback, 0)
    pre_px = float(basket.iloc[pre_pos])
    fwd = basket.iloc[entry_pos:entry_pos + 504]
    hit = np.where(fwd.values >= pre_px)[0]
    out.days_to_recover_preshock = int(hit[0]) if len(hit) else None

    return out


def run_study(events, close: pd.DataFrame, entry_delay_days: int = 3) -> List[EventOutcome]:
    return [study_event(e, close, entry_delay_days) for e in events]


def summarise(outcomes: List[EventOutcome]) -> pd.DataFrame:
    """Distribution per horizon. Medians, not means — one outlier shouldn't carry it."""
    rows = []
    for label in HORIZONS:
        vals = np.array([o.forward[label] for o in outcomes
                         if o.forward.get(label) is not None], dtype=float)
        if len(vals) == 0:
            continue
        rows.append({
            "horizon": label,
            "n": len(vals),
            "median": np.median(vals),
            "mean": vals.mean(),
            "hit_rate": float((vals > 0).mean()),
            "p10": np.percentile(vals, 10),
            "p90": np.percentile(vals, 90),
            "worst": vals.min(),
            "best": vals.max(),
        })
    return pd.DataFrame(rows)


def recovery_profile(outcomes: List[EventOutcome]) -> Dict[str, float]:
    """How often, and how fast, the bounce actually arrived."""
    resolved = [o for o in outcomes if not o.censored]
    recovered = [o for o in resolved if o.days_to_recover_preshock is not None]
    days = [o.days_to_recover_preshock for o in recovered]
    dd = [o.max_further_drawdown for o in outcomes
          if o.max_further_drawdown is not None]
    return {
        "events_resolved": len(resolved),
        "recovered_within_2y": len(recovered),
        "recovery_rate": len(recovered) / len(resolved) if resolved else float("nan"),
        "median_days_to_recover": float(np.median(days)) if days else float("nan"),
        "p90_days_to_recover": float(np.percentile(days, 90)) if days else float("nan"),
        "median_further_drawdown": float(np.median(dd)) if dd else float("nan"),
        "worst_further_drawdown": float(np.min(dd)) if dd else float("nan"),
    }


def by_bucket(outcomes: List[EventOutcome], horizon: str = "6m",
              field_name: str = "score", bins: int = 3) -> pd.DataFrame:
    """
    Does a higher shock score actually predict a better bounce?

    If it doesn't, the score is measuring the size of the crash and nothing
    more — which is worth knowing before any weight is put on it.
    """
    rows = [{"x": getattr(o, field_name), "y": o.forward.get(horizon)}
            for o in outcomes if o.forward.get(horizon) is not None]
    if len(rows) < bins * 2:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["bucket"] = pd.qcut(df["x"], bins, duplicates="drop")
    g = df.groupby("bucket", observed=True)["y"]
    return pd.DataFrame({
        "n": g.size(), "median": g.median(),
        "hit_rate": g.apply(lambda s: (s > 0).mean()),
    }).reset_index()
