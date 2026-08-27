"""
Recovery Candidate Score.

DEVLOG s.19: 0-100, higher means better positioned to recover under the
analysed event. Must NOT simply reward the largest company. Must explain itself.

DEVLOG s.7 of the philosophy section: "The strongest candidate is not
necessarily the company that fell the most." So depth of decline is deliberately
NOT a straight positive here — a name that fell far more than its peers is
flagged as potentially idiosyncratic, which counts against it, not for it.

PROTOTYPE HONESTY: devlog s.18 lists ~18 inputs. Five are implemented, because
five are computable from price and volume alone. The rest need a fundamentals
feed. Every score returned carries its own coverage figure so the number is
never mistaken for the finished model.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

import numpy as np
import pandas as pd

from ..engine.metrics import CompanyMetrics, IndustryMetrics


@dataclass
class RecoveryFactor:
    name: str
    score: float          # 0-100
    weight: float
    reason: str

    @property
    def contribution(self) -> float:
        return self.score * self.weight


@dataclass
class RecoveryCandidate:
    ticker: str
    name: str
    score: float
    factors: List[RecoveryFactor]
    coverage: float
    caveats: List[str] = field(default_factory=list)

    def explain(self) -> List[str]:
        return [f"{f.name}: {f.reason}" for f in
                sorted(self.factors, key=lambda x: -x.contribution)]


def _ramp(v, lo, hi):
    if hi == lo:
        return 50.0
    return float(np.clip((v - lo) / (hi - lo), 0, 1) * 100)


def _historical_resilience(close: pd.Series, window: int) -> Optional[float]:
    """
    How did this name behave after its worst prior drawdowns?

    Finds the 3 worst non-overlapping `window`-day declines in the trailing
    history, then measures the forward return over the following 3*window days.
    Uses only data strictly before as_of, and skips any episode whose forward
    window has not fully completed — so no partial future is peeked at.
    """
    s = close.dropna()
    fwd = window * 3
    if len(s) < window + fwd + 60:
        return None
    rolling = s.pct_change(window)
    candidates = rolling.iloc[window:-(fwd + 1)].nsmallest(20)

    picked, used_idx = [], []
    for date, decline in candidates.items():
        pos = s.index.get_loc(date)
        if any(abs(pos - p) < window for p in used_idx):
            continue
        used_idx.append(pos)
        recovery = float(s.iloc[pos + fwd] / s.iloc[pos] - 1.0)
        picked.append(recovery)
        if len(picked) == 3:
            break
    return float(np.mean(picked)) if picked else None


def score_candidates(industry: IndustryMetrics, close: pd.DataFrame,
                     cfg, event=None) -> List[RecoveryCandidate]:
    rc = cfg.recovery
    w = industry.window
    out: List[RecoveryCandidate] = []

    peer_median = industry.median_return
    vols = [c.trailing_vol for c in industry.companies if c.trailing_vol]
    peer_vol = float(np.median(vols)) if vols else None
    dvs = [c.dollar_volume for c in industry.companies if c.dollar_volume]
    peer_dv = float(np.median(dvs)) if dvs else None

    for cm in industry.companies:
        factors: List[RecoveryFactor] = []
        caveats: List[str] = []
        r = cm.returns[w]

        # 1. Event exposure — how far this name moved relative to its peers.
        excess = r - peer_median
        if excess < -0.05:
            exp_score = _ramp(excess, -0.15, -0.05) * 0.5
            reason = (f"Fell {abs(excess):.1%} more than the industry median. "
                      f"That gap may be company-specific rather than event-driven, "
                      f"which is a reason for caution, not a discount to buy.")
            caveats.append("Underperformed peers — check for company-specific news.")
        elif excess > 0.04:
            exp_score = _ramp(excess, 0.0, 0.10) * 0.8 + 20
            reason = (f"Held up {excess:.1%} better than the industry median, "
                      f"suggesting lower exposure to the event.")
        else:
            exp_score = 60.0
            reason = (f"Moved in line with the industry ({excess:+.1%} vs median), "
                      f"consistent with a shared cause rather than a company issue.")
        factors.append(RecoveryFactor("event_exposure", exp_score, 0.28, reason))

        # 2. Drawdown depth — distance from 52-week high.
        if cm.dist_from_52w_high is not None:
            dd = cm.dist_from_52w_high
            score = _ramp(dd, -0.55, -0.12)
            factors.append(RecoveryFactor(
                "drawdown_depth", score, 0.16,
                f"{abs(dd):.0%} below its 52-week high. Deep drawdowns leave more "
                f"room to recover but also signal the market has been repricing "
                f"this name for a while."))

        # 3. Historical resilience.
        hr = _historical_resilience(close[cm.ticker], w) if cm.ticker in close else None
        if hr is not None:
            factors.append(RecoveryFactor(
                "historical_resilience", _ramp(hr, -0.15, 0.30), 0.24,
                f"After its three worst prior {w}-day declines, it returned "
                f"{hr:+.1%} on average over the next {w*3} trading days. "
                f"Small sample — indicative only."))
        else:
            caveats.append("Insufficient history to measure past recovery behaviour.")

        # 4. Liquidity.
        if cm.dollar_volume and peer_dv:
            ratio = cm.dollar_volume / peer_dv
            factors.append(RecoveryFactor(
                "liquidity", _ramp(np.log10(max(ratio, 0.01)), -1, 1), 0.14,
                f"Traded {ratio:.1f}x the industry median dollar volume. Liquid "
                f"names tend to re-rate first when sentiment turns."))

        # 5. Relative stability.
        if cm.trailing_vol and peer_vol:
            rel_vol = cm.trailing_vol / peer_vol
            factors.append(RecoveryFactor(
                "relative_stability", _ramp(-rel_vol, -1.6, -0.7), 0.18,
                f"Trailing volatility is {rel_vol:.2f}x the industry median "
                f"({cm.trailing_vol:.0%} annualised)."))

        total_w = sum(f.weight for f in factors)
        score = sum(f.contribution for f in factors) / total_w if total_w else 0.0

        # DEVLOG s.15/s.19 — a structural event should suppress recovery scores
        # across the board. The event assessment modulates, it does not rank.
        if event and not event.is_stub and event.structural_risk > 60:
            score *= 1 - min((event.structural_risk - 60) / 100, 0.35)
            caveats.append(
                f"Score reduced: structural risk assessed at {event.structural_risk}/100. "
                f"If the event is structural, resilience rankings mean less.")

        out.append(RecoveryCandidate(
            ticker=cm.ticker, name=cm.name, score=round(float(score), 1),
            factors=factors, coverage=rc.coverage, caveats=caveats,
        ))

    return sorted(out, key=lambda c: -c.score)
