"""
Industry Shock Score and alert levels.

DEVLOG s.10: score is 0-100 and measures the strength of the detected market
anomaly, NOT investment attractiveness.

DEVLOG s.41: no unexplained scores. Every component reports its raw input, its
0-100 sub-score, its weight, and its contribution to the total.

DEVLOG s.11 + s.6: the score alone cannot promote an industry to INVESTIGATE.
The hard trigger conditions from s.6 must also be satisfied. This is the guard
against the app degenerating into a generic screener (s.49).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional

import numpy as np

from .metrics import IndustryMetrics


class AlertLevel(str, Enum):
    NONE = "NONE"
    WATCH = "WATCH"
    INVESTIGATE = "INVESTIGATE"
    MAJOR_EVENT = "MAJOR_EVENT"


@dataclass
class ScoreComponent:
    name: str
    raw_value: float
    raw_label: str
    sub_score: float      # 0-100
    weight: float
    explanation: str

    @property
    def contribution(self) -> float:
        return self.sub_score * self.weight


@dataclass
class TriggerCheck:
    """DEVLOG s.6 — the five hard conditions, each pass/fail and stated."""

    label: str
    passed: bool
    detail: str


@dataclass
class ShockAssessment:
    industry: str
    score: float
    level: AlertLevel
    components: List[ScoreComponent]
    triggers: List[TriggerCheck]
    metrics: IndustryMetrics
    params_version: str
    notes: List[str] = field(default_factory=list)

    @property
    def triggered(self) -> bool:
        return all(t.passed for t in self.triggers)

    @property
    def failed_triggers(self) -> List[TriggerCheck]:
        return [t for t in self.triggers if not t.passed]

    def explain(self) -> str:
        lines = [f"Industry Shock Score {self.score:.0f}/100 — {self.level.value}"]
        for c in sorted(self.components, key=lambda x: -x.contribution):
            lines.append(
                f"  {c.name:<24} {c.sub_score:5.1f} x {c.weight:.2f} "
                f"= {c.contribution:5.1f}   ({c.raw_label})"
            )
        return "\n".join(lines)


def _ramp(value: float, lo: float, hi: float) -> float:
    """Linear 0-100 ramp; clamps outside [lo, hi]. lo may exceed hi (inverted)."""
    if hi == lo:
        return 0.0
    x = (value - lo) / (hi - lo)
    return float(np.clip(x, 0.0, 1.0) * 100.0)


def score_industry(m: IndustryMetrics, cfg) -> ShockAssessment:
    d, w = cfg.detection, cfg.weights
    comps: List[ScoreComponent] = []

    # --- Magnitude (s.10) --------------------------------------------------
    comps.append(ScoreComponent(
        "magnitude",
        m.median_return,
        f"median {m.window}d return {m.median_return:+.1%}",
        _ramp(m.median_return, -0.03, d.extreme_decline),
        w.magnitude,
        f"Industry median {m.window}-day return is {m.median_return:+.1%}. "
        f"Scales from 0 at -3% to 100 at {d.extreme_decline:.0%}.",
    ))

    # --- Breadth (s.10) ----------------------------------------------------
    comps.append(ScoreComponent(
        "breadth",
        m.pct_declining,
        f"{m.n_declining}/{m.n_members} ({m.pct_declining:.0%}) declining",
        _ramp(m.pct_declining, 0.55, 1.0),
        w.breadth,
        f"{m.n_declining} of {m.n_members} members ({m.pct_declining:.0%}) are "
        f"down at least {abs(d.material_decline):.0%}. The bar is a "
        f"supermajority: a fixed count like 'three companies' means something "
        f"very different in a 5-member group than a 20-member one.",
    ))

    # --- Relative weakness (s.10) -----------------------------------------
    if m.alpha is not None:
        rel_value, rel_label = m.alpha, f"{m.alpha:+.1%} vs beta-adjusted"
        rel_expl = (
            f"This group normally moves {m.beta_to_market:.2f}x the market "
            f"(R² {m.r_squared:.2f}). With the S&P 500 at "
            f"{m.market_return:+.1%}, a fall of {m.expected_return:+.1%} would "
            f"be ordinary. It actually fell {m.median_return:+.1%}, leaving "
            f"{m.alpha:+.1%} the market cannot account for. That residual, not "
            f"the raw gap, is what distinguishes an industry shock from a "
            f"broad sell-off in a high-beta group.")
    else:
        rel_value, rel_label = m.relative_to_market, f"{m.relative_to_market:+.1%} vs S&P 500"
        rel_expl = (
            f"The industry is {abs(m.relative_to_market):.1%} "
            f"{'behind' if m.relative_to_market < 0 else 'ahead of'} the S&P 500. "
            f"Beta could not be estimated, so this is a raw difference and "
            f"overstates the signal for a naturally volatile group.")
    comps.append(ScoreComponent(
        "relative_weakness", rel_value, rel_label,
        _ramp(rel_value, -0.02, -0.12), w.relative_weakness, rel_expl,
    ))

    # --- Abnormality (s.10) ------------------------------------------------
    comps.append(ScoreComponent(
        "abnormality",
        m.abnormality_z,
        f"{m.abnormality_z:.1f} sigma",
        _ramp(m.abnormality_z, 1.0, 4.0),
        w.abnormality,
        f"The move is {m.abnormality_z:.1f} standard deviations below this "
        f"industry's own distribution of {m.window}-day returns over the past "
        f"year. A volatile industry has to move further to look abnormal.",
    ))

    # --- Correlation (s.10) ------------------------------------------------
    corr = 0.0 if np.isnan(m.mean_pairwise_correlation) else m.mean_pairwise_correlation
    comps.append(ScoreComponent(
        "correlation",
        corr,
        f"mean pairwise {corr:.2f}",
        _ramp(corr, 0.3, 0.85),
        w.correlation,
        f"Mean pairwise correlation of daily returns across members is "
        f"{corr:.2f}. High correlation means they are falling together, which "
        f"points to a shared cause rather than coincidence.",
    ))

    # --- Volume (s.10) -----------------------------------------------------
    comps.append(ScoreComponent(
        "volume",
        m.median_volume_z,
        f"median volume z {m.median_volume_z:+.1f}",
        _ramp(m.median_volume_z, 0.5, 3.0),
        w.volume,
        f"Median volume z-score is {m.median_volume_z:+.1f} against the "
        f"trailing 60-day mean. Conviction selling usually shows up as volume.",
    ))

    # --- Benchmark confirmation (s.10) -------------------------------------
    if m.benchmark_return is None:
        bench_score = 0.0
        bench_label = (f"{m.benchmark_ticker} unavailable" if m.benchmark_ticker
                       else "no industry ETF mapped")
        bench_expl = ("No industry benchmark reading, so this component "
                      "contributes nothing. It is not counted as confirmation "
                      "either way — absence of evidence is not evidence.")
    else:
        bench_score = _ramp(m.benchmark_return, -0.02, -0.12)
        bench_label = f"{m.benchmark_ticker} {m.benchmark_return:+.1%}"
        vs = m.relative_to_benchmark
        harder = ("harder than" if vs is not None and vs < -0.01
                  else "less than" if vs is not None and vs > 0.01 else "in line with")
        bench_expl = (
            f"{m.benchmark_ticker} ({m.benchmark_name}) returned "
            f"{m.benchmark_return:+.1%}, confirming the move extends beyond the "
            f"large-cap names measured here. This group fell {harder} the ETF"
            + (f" ({vs:+.1%})." if vs is not None else ".")
            + (f" Overlap with our universe is {m.benchmark_overlap} — the ETF "
               f"holds many of the same companies, so treat this as "
               f"corroboration rather than an independent second opinion."
               if m.benchmark_overlap else ""))
    comps.append(ScoreComponent(
        "benchmark_confirmation", m.benchmark_return or 0.0,
        bench_label, bench_score, w.benchmark_confirmation, bench_expl,
    ))

    score = sum(c.contribution for c in comps)

    # --- Hard trigger conditions (s.6) -------------------------------------
    triggers = [
        TriggerCheck(
            f"At least {d.min_breadth_pct:.0%} of the industry declining",
            m.pct_declining >= d.min_breadth_pct,
            f"{m.n_declining} of {m.n_members} ({m.pct_declining:.0%}) at or "
            f"below {d.material_decline:.0%} (need {d.min_breadth_pct:.0%}).",
        ),
        TriggerCheck(
            f"At least {d.min_declining_companies} companies declining",
            m.n_declining >= d.min_declining_companies,
            f"{m.n_declining} declining. A backstop only — one or two names "
            f"cannot show anything however improbable the arithmetic looks.",
        ),
        TriggerCheck(
            f"Industry median {m.window}d return at or below {d.industry_median_threshold:.0%}",
            m.median_return <= d.industry_median_threshold,
            f"Median is {m.median_return:+.1%}.",
        ),
        TriggerCheck(
            f"At least {d.min_underperforming_pct:.0%} underperforming the S&P 500 "
            f"by {abs(d.relative_underperformance_threshold):.0%}+",
            (m.n_underperforming / m.n_members >= d.min_underperforming_pct
             and m.n_underperforming >= d.min_underperforming_companies),
            f"{m.n_underperforming} of {m.n_members} "
            f"({m.n_underperforming / m.n_members:.0%}) qualify — need "
            f"{d.min_underperforming_pct:.0%} and at least "
            f"{d.min_underperforming_companies}.",
        ),
        TriggerCheck(
            "Move is abnormal vs the industry's own volatility",
            m.abnormality_z >= d.min_abnormality_z,
            f"{m.abnormality_z:.1f} sigma (need {d.min_abnormality_z:.1f}).",
        ),
    ]
    if (m.n_members < d.breadth_significance_below
            and m.breadth_pvalue is not None):
        triggers.append(TriggerCheck(
            "Breadth is unlikely to be coincidence",
            m.breadth_pvalue <= d.max_breadth_pvalue,
            f"{m.n_declining} of {m.n_members} fell while "
            f"{m.market_decline_rate:.0%} of the rest of the universe did — "
            f"a {m.breadth_pvalue:.1%} chance of happening by coincidence "
            f"(need {d.max_breadth_pvalue:.0%} or less). This is what "
            f"replaces a flat member count: the bar rises when the whole "
            f"market is falling and relaxes when it is calm.",
        ))
    if m.benchmark_return is not None:
        triggers.append(TriggerCheck(
            f"Industry ETF confirms the decline ({m.benchmark_ticker})",
            m.benchmark_return <= d.benchmark_confirm_threshold,
            f"{m.benchmark_ticker} returned {m.benchmark_return:+.1%} "
            f"(need {d.benchmark_confirm_threshold:.0%} or worse).",
        ))
    notes = []
    if (m.n_members < d.breadth_significance_below
            and m.breadth_pvalue is None):
        notes.append(
            "Breadth could not be tested for significance: there was no "
            "usable comparison universe outside this industry. The condition "
            "is skipped rather than passed, so this alert rests on the "
            "proportion of the group falling without knowing how ordinary "
            "that was across the market that day.")
    if m.benchmark_return is None:
        notes.append(
            "Devlog s.6.4 requires industry ETF confirmation. No benchmark "
            "reading was available, so that condition is skipped rather than "
            "passed, and this alert rests on one fewer piece of evidence than "
            "a full trigger would.")

    # --- Alert level (s.11) ------------------------------------------------
    a = cfg.alerts
    triggered = all(t.passed for t in triggers)
    if score >= a.major_event_score and triggered:
        level = AlertLevel.MAJOR_EVENT
    elif score >= a.investigate_score and (triggered or not a.require_trigger_for_investigate):
        level = AlertLevel.INVESTIGATE
    elif score >= a.watch_score:
        level = AlertLevel.WATCH
    else:
        level = AlertLevel.NONE

    if score >= a.investigate_score and not triggered:
        notes.append(
            "Score reached the INVESTIGATE band but the s.6 trigger conditions "
            "were not all met, so the alert is held at WATCH. A high score on "
            "its own is not an event."
        )

    notes.extend(m.data_warnings)

    return ShockAssessment(
        industry=m.industry, score=float(score), level=level,
        components=comps, triggers=triggers, metrics=m,
        params_version=cfg.params_version, notes=notes,
    )


def scan(industry_metrics: List[IndustryMetrics], cfg) -> List[ShockAssessment]:
    out = [score_industry(m, cfg) for m in industry_metrics]
    return sorted(out, key=lambda a: -a.score)
