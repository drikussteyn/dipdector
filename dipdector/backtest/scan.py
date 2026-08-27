"""
Historical event scan.

Walks a date range one trading day at a time and records every industry that
fired. This is the input to both the event study and the portfolio simulator.

Two things matter here and nothing else does:

1. NO LOOKAHEAD. Each day's assessment is computed from a frame truncated at
   that day. The scan cannot be vectorised across the whole history without
   breaking this, so it isn't. Slow and correct beats fast and wrong.

2. DE-DUPLICATION. A five-day shock keeps satisfying the trigger for days
   afterwards, so a naive scan reports one event ten times and every downstream
   statistic is inflated. Consecutive firings in the same industry inside a
   cooldown window are collapsed into a single event, dated at the FIRST firing
   — the day you would actually have been alerted, not the day the score peaked.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import pandas as pd

from ..config import CONFIG
from ..data.providers import PriceFrame
from ..engine.detection import AlertLevel, ShockAssessment, score_industry
from ..engine.metrics import compute_industry_metrics

LEVEL_ORDER = [AlertLevel.NONE, AlertLevel.WATCH,
               AlertLevel.INVESTIGATE, AlertLevel.MAJOR_EVENT]


@dataclass
class DetectedEvent:
    industry: str
    detected_on: dt.date          # first day the trigger fired
    peak_on: dt.date              # day the score was highest
    level: AlertLevel
    score: float                  # score at first firing, not at peak
    peak_score: float
    median_return: float
    relative_return: float
    n_declining: int
    n_members: int
    abnormality_z: float
    tickers: List[str]
    n_firings: int = 1
    assessment: Optional[ShockAssessment] = None

    @property
    def entry_universe(self) -> List[str]:
        return self.tickers


def scan_history(
    frame: PriceFrame,
    classification,
    start: dt.date,
    end: dt.date,
    min_level: AlertLevel = AlertLevel.INVESTIGATE,
    cooldown_days: int = 60,
    step: int = 1,
    progress: bool = True,
) -> List[DetectedEvent]:
    """
    Returns de-duplicated events between start and end.

    cooldown_days: a fresh firing in the same industry within this many calendar
    days of the previous one is treated as the same event continuing, not a new
    opportunity. 60 days is a guess and should itself be tuned.
    """
    all_dates = [d.date() for d in frame.close.index
                 if start <= d.date() <= end]
    dates = all_dates[::step]

    open_events: Dict[str, DetectedEvent] = {}
    closed: List[DetectedEvent] = []
    threshold = LEVEL_ORDER.index(min_level)

    for i, day in enumerate(dates):
        if progress and i % 250 == 0:
            print(f"      scanning {day} ({i}/{len(dates)})", flush=True)

        truncated = frame.as_of(day)
        for industry, members in classification.industries(day).items():
            m = compute_industry_metrics(industry, members, truncated, day, CONFIG)
            if m is None:
                continue
            a = score_industry(m, CONFIG)

            fired = LEVEL_ORDER.index(a.level) >= threshold
            prev = open_events.get(industry)

            if prev and (day - prev.peak_on).days > cooldown_days:
                closed.append(prev)
                prev = None
                open_events.pop(industry, None)

            if not fired:
                continue

            if prev is None:
                open_events[industry] = DetectedEvent(
                    industry=industry, detected_on=day, peak_on=day,
                    level=a.level, score=a.score, peak_score=a.score,
                    median_return=m.median_return,
                    relative_return=m.relative_to_market,
                    n_declining=m.n_declining, n_members=m.n_members,
                    abnormality_z=m.abnormality_z,
                    tickers=[c.ticker for c in m.companies],
                    assessment=a,
                )
            else:
                prev.n_firings += 1
                if a.score > prev.peak_score:
                    prev.peak_score = a.score
                    prev.peak_on = day
                    if LEVEL_ORDER.index(a.level) > LEVEL_ORDER.index(prev.level):
                        prev.level = a.level

    closed.extend(open_events.values())
    return sorted(closed, key=lambda e: e.detected_on)


def events_frame(events: List[DetectedEvent]) -> pd.DataFrame:
    return pd.DataFrame([{
        "detected_on": e.detected_on, "industry": e.industry,
        "level": e.level.value, "score": round(e.score, 1),
        "peak_score": round(e.peak_score, 1),
        "median_return": e.median_return, "relative": e.relative_return,
        "breadth": f"{e.n_declining}/{e.n_members}",
        "firings": e.n_firings,
    } for e in events])
