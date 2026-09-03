"""
Threshold sweep.

Evaluates trigger parameter combinations against the precomputed panel and
reports what each would have detected and what happened next. It does not
choose thresholds; it produces the surface a human chooses from, because the
combination with the best median return is usually the one that fired four
times in thirty years.

THREE THINGS THAT MAKE A SWEEP LIE, AND WHAT IS DONE ABOUT THEM.

  Multiple comparisons. Testing a thousand combinations and keeping the best
  guarantees a good-looking number even from noise. Every combination is
  therefore scored on a training period and re-scored on a later period it
  never saw; a parameter set that only works in-sample is visible as such.

  Small samples. A combination that fires eight times can post any median at
  all. Combinations below `min_events` are reported but never ranked.

  Overlapping events. One shock keeps satisfying a trigger for days, so raw
  firings overcount and inflate every statistic. Firings within `cooldown`
  days in one industry collapse to a single event dated at the first.

Survivorship bias is not fixable here and is not fixed. See panel.py.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from itertools import product
from typing import Dict, Iterable, List, Optional

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class Params:
    window: int              # trading days the fall is measured over
    magnitude: float         # industry median return at or below this
    decline_level: int       # a member "declined" at this % (3, 5 or 10)
    breadth: float           # fraction of members that must have declined
    rel_market: float        # industry must trail the market by at least this

    def label(self) -> str:
        return (f"{self.window}d "
                f"med<={self.magnitude:+.0%} "
                f"{self.breadth:.0%}@-{self.decline_level}% "
                f"rel<={self.rel_market:+.0%}")


def _dedupe(hits: pd.DataFrame, cooldown: int) -> pd.DataFrame:
    """Collapse repeat firings of one shock into a single dated event."""
    if hits.empty:
        return hits
    out = []
    for industry, g in hits.groupby("industry", sort=False):
        g = g.sort_values("date")
        last = None
        for row in g.itertuples(index=False):
            if last is None or (row.date - last).days > cooldown:
                out.append(row)
                last = row.date
    return pd.DataFrame(out)


def evaluate(panel: pd.DataFrame, p: Params, cooldown: int = 60) -> pd.DataFrame:
    """Events this parameter set would have produced, with their outcomes."""
    w, lvl = p.window, p.decline_level
    m = (
        (panel[f"ret_{w}d"] <= p.magnitude)
        & (panel[f"breadth{lvl}_{w}d"] >= p.breadth)
        & ((panel[f"ret_{w}d"] - panel[f"mkt_{w}d"]) <= p.rel_market)
    )
    return _dedupe(panel.loc[m, ["date", "industry", f"ret_{w}d",
                                 f"breadth{lvl}_{w}d", "n_members",
                                 "fwd_1m", "fwd_3m", "fwd_6m", "fwd_12m",
                                 "fwd_min_6m"]], cooldown)


def summarise(events: pd.DataFrame, years: float) -> Dict:
    if events.empty:
        return {"n": 0, "per_year": 0.0}
    out = {"n": len(events), "per_year": len(events) / years}
    for h in ("fwd_1m", "fwd_3m", "fwd_6m", "fwd_12m"):
        v = events[h].dropna()
        out[f"{h}_med"] = float(v.median()) if len(v) else np.nan
        out[f"{h}_hit"] = float((v > 0).mean()) if len(v) else np.nan
    fm = events["fwd_min_6m"].dropna()
    out["further_fall_med"] = float(fm.median()) if len(fm) else np.nan
    return out


def grid(windows: Iterable[int], magnitudes: Iterable[float],
         levels: Iterable[int], breadths: Iterable[float],
         rels: Iterable[float]) -> List[Params]:
    return [Params(w, m, l, b, r)
            for w, m, l, b, r in product(windows, magnitudes, levels,
                                         breadths, rels)]


def run(panel: pd.DataFrame, params: List[Params],
        split: Optional[pd.Timestamp] = None,
        cooldown: int = 60, min_events: int = 20) -> pd.DataFrame:
    """
    Score every combination, in-sample and out-of-sample when `split` is given.

    The out-of-sample columns are the ones worth reading. A combination that
    looks strong before the split and ordinary after it was fitted to noise.
    """
    tr = panel if split is None else panel[panel.date < split]
    te = None if split is None else panel[panel.date >= split]
    yrs_tr = (tr.date.max() - tr.date.min()).days / 365.25
    yrs_te = None if te is None else (te.date.max() - te.date.min()).days / 365.25

    rows = []
    for p in params:
        rec = {**asdict(p), "label": p.label()}
        s = summarise(evaluate(tr, p, cooldown), yrs_tr)
        rec.update({f"tr_{k}": v for k, v in s.items()})
        if te is not None:
            s2 = summarise(evaluate(te, p, cooldown), yrs_te)
            rec.update({f"te_{k}": v for k, v in s2.items()})
        rec["ranked"] = s.get("n", 0) >= min_events
        rows.append(rec)
    return pd.DataFrame(rows)
