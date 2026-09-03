"""
DipDector configuration.

DEVLOG s.49 / s.44.9: thresholds must never be changed silently. Every tunable
number lives in this file, carries a provenance note, and is stamped into any
event record written to the store. If you change a value here, bump
PARAMS_VERSION and record the reason in CHANGELOG_THRESHOLDS.

DEVLOG s.50: these are STARTING parameters, not permanently fixed values. None
of them have been validated by backtesting yet.
"""

from dataclasses import dataclass, field, asdict
from typing import Dict, List

def load_env() -> None:
    """
    Load `.env` into the environment, if present.

    Called from the CLI entry points rather than on import, so importing the
    package never has a side effect. python-dotenv does not override variables
    that are already set, which is the behaviour we want: in CI the real
    secrets win, and a stale `.env` on a laptop cannot quietly shadow them.

    A no-op if python-dotenv is not installed, so the package still imports
    with only the core dependencies present.
    """
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv()


PARAMS_VERSION = "0.6.0-fitted"

CHANGELOG_THRESHOLDS = [
    ("0.1.0-unvalidated", "Initial values taken verbatim from devlog s.6 and s.50. "
                          "No backtesting performed. Do not treat as tuned."),
    ("0.2.0-unvalidated", "Breadth changed from an absolute count (3 companies) to "
                          "80% of the industry plus a floor of 4, on the grounds "
                          "that 3 of 10 is ordinary noise. min_industry_members "
                          "raised 4 -> 5. Added beta-adjusted excess return. "
                          "Effect on event frequency measured, not assumed — see "
                          "README."),
    ("0.6.0-fitted", "Re-derived on the primary-source universe, because "
                     "0.5.0 was fitted on a Wikipedia GICS grouping and then "
                     "left running on a different one - the parameters and "
                     "the data they governed had come apart. Same method, "
                     "clean sources: SPDR holdings for constituents, the "
                     "price provider for classification, 104,544 "
                     "industry-days 1990-2026, 1,206 combinations, fitted on "
                     "1990-2012 and verified on 2013-2026. Depth confirmed "
                     "STABLE at +0.90 and member-decline at +1.00, both in "
                     "the same order as before, so the conclusions survived "
                     "the change of source. Window is only +0.60 and at -15% "
                     "its rank correlation is negative: 5 days ranks 1st then "
                     "2nd across the eras while 2 days ranks 5th then 1st, so "
                     "5 is kept as the most CONSISTENT window rather than a "
                     "proven optimum. Depth analysed jointly with window "
                     "rather than marginally - a fall only means something "
                     "paired with the time it took. industry_median_threshold "
                     "-0.10 -> -0.12. Engine-validated 2016-2026: 4.1 -> 2.3 "
                     "events/year, 12-month median +33.0% -> +48.9%, 23 of 23 "
                     "recovered. -0.15 tested better still (+60.5%, 100%) but "
                     "on 13 events in 10.7 years, too thin to trust and too "
                     "rare to learn from."),
    ("0.5.0-fitted", "First thresholds derived from evidence rather than "
                     "taken from the devlog. 1,350 combinations swept over a "
                     "36-year panel (1990-2026, 104k industry-days), scored "
                     "on 1990-2012 and re-scored on 2013-2026, which it never "
                     "saw. Two findings ranked IDENTICALLY in both eras: "
                     "deeper falls recover better (-20% > -15% > -10% > -8% > "
                     "-5%), and requiring members to have fallen further "
                     "selects better events (-10% > -5% > -3%). Breadth "
                     "agreed at +0.77 and saturates by 0.7-0.8, so 0.80 "
                     "stays. The detection window did NOT transfer (rank "
                     "correlation -0.10: 1990-2012 preferred 5 days, "
                     "2013-2026 preferred 2) so it is deliberately left at 5. "
                     "industry_median_threshold -0.08 -> -0.10, "
                     "material_decline -0.03 -> -0.05. Validated on the live "
                     "engine 2016-2026: 4.9 -> 3.8 events/year, 12-month "
                     "median +31.7% -> +35.5%, hit rate 82% -> 92%, recovery "
                     "98% -> 100%, and further fall after entry -11.0% -> "
                     "-6.3%. Absolute returns are inflated by survivorship "
                     "bias and by a test period of unusually sharp "
                     "recoveries; the RANKINGS are what these rest on."),
    ("0.4.0-unvalidated", "Replaced the flat 5-member floor with a "
                          "significance test. The floor was doing two jobs "
                          "badly: it blocked small groups that were genuinely "
                          "anomalous on a calm day, and it drew an arbitrary "
                          "line between 4-of-4 and 5-of-5 whose real "
                          "improbability differs by a factor of two. Breadth "
                          "is now scored against the market-wide decline rate "
                          "that day, so the bar rises automatically when "
                          "everything is falling and relaxes when nothing is. "
                          "min_industry_members 5 -> 3, "
                          "min_declining_companies 4 -> 3, both now backstops "
                          "rather than the test. Applied ONLY to groups "
                          "below 5 members: a first attempt gated every "
                          "industry on it and measurably degraded the "
                          "detector, because shocks cluster on days the whole "
                          "market falls and a high base rate then makes even "
                          "12-of-12 look unsurprising. Measured over "
                          "2016-2026: 3.7 -> 4.9 events/year, 13 of the 52 "
                          "from groups the old floor excluded outright, with "
                          "12-month median +32.3% -> +31.7% and recovery "
                          "97% -> 98%. Coverage 297 -> 422 of 503 companies."),
    ("0.3.0-unvalidated", "Second comparator changed from a fixed Nasdaq-100 "
                          "to a per-industry ETF resolved from a registry. "
                          "S&P 500 remains the invariant primary. Activates "
                          "the devlog s.6.4 benchmark-confirmation trigger, "
                          "which was previously always skipped."),
]


@dataclass(frozen=True)
class DetectionConfig:
    """DEVLOG s.6 — recommended initial trigger. DEVLOG s.7 — timeframes."""

    # Primary detection window, in trading days (devlog s.7: "primary initial
    # signal should use 5 trading days").
    primary_window: int = 5

    # Context windows. Never used to trigger on their own.
    context_windows: tuple = (1, 3, 10, 20, 60)

    # s.6.1 — BREADTH. Superseded the original "at least 3 companies" rule.
    #
    # Three names is not an industry, it is a small sample. In a 10-member
    # group, 3 declining is 30% — which is roughly what you would see on an
    # ordinary Tuesday. The requirement is now proportional: a supermajority of
    # the group must be falling before this counts as industry-wide.
    #
    # The absolute floor stays, because a percentage of a tiny group is also
    # meaningless: 80% of 4 members is 3 stocks wearing a percentage as a
    # disguise. Both conditions must hold.
    min_breadth_pct: float = 0.80
    # A backstop now, not the test. One or two names cannot demonstrate
    # anything regardless of how improbable the arithmetic says it is.
    min_declining_companies: int = 3

    # s.6.1 — a company counts as "materially declining" at this return or worse
    # over the primary window. The devlog does not pin this number; it is
    # inferred so that "materially" is not left undefined in code.
    material_decline: float = -0.05

    # s.6.2 — industry median return over primary window.
    industry_median_threshold: float = -0.12

    # s.6.3 — number of companies underperforming S&P 500 by the relative
    # threshold below.
    # Same logic applied to relative underperformance: proportional, with a floor.
    min_underperforming_pct: float = 0.60
    min_underperforming_companies: int = 3
    relative_underperformance_threshold: float = -0.05

    # s.6.5 — the move must be abnormal vs the industry's own volatility.
    # Expressed as a z-score of the industry's window return against its
    # trailing distribution of same-length window returns.
    min_abnormality_z: float = 1.5
    volatility_lookback_days: int = 252

    # s.6 — high-severity band, used for alert escalation, not as the trigger.
    high_severity_decline: float = -0.10
    extreme_decline: float = -0.15

    # An industry needs at least this many members before breadth statistics
    # mean anything. Raised from 4 alongside the percentage rule: in a 4-member
    # group every breadth figure is a multiple of 25% and the supermajority test
    # collapses into "all of them".
    # Three is the floor at which the significance test below can say
    # anything at all: 2 of 2 falling is a 1-in-5 coincidence even in a
    # fairly calm market, which no p-value can rescue.
    min_industry_members: int = 3

    # s.6.1 — BREADTH SIGNIFICANCE. The real gate on small groups.
    #
    # A flat member floor asks the wrong question. "Did five companies fall?"
    # is not the same as "was it surprising that they fell?" — and the second
    # is what distinguishes an industry shock from an ordinary down day. So
    # breadth is tested against the fraction of the whole universe declining
    # over the same window: P(at least this many of n, given that base rate).
    #
    # 3 of 3 falling is a 9% coincidence when 45% of the market is down, and a
    # 0.8% event when only 20% is. The old floor called both of them nothing.
    max_breadth_pvalue: float = 0.02

    # Applied ONLY to groups smaller than this. A first attempt made it an
    # extra gate on every industry and measurably made things worse: shocks
    # cluster on days the whole market is falling, and on those days a high
    # base rate makes even 12-of-12 look unsurprising (p=0.03 in a crash), so
    # the strongest events were the ones being rejected. Whether a move is
    # market-wide is already answered by the relative-underperformance and
    # abnormality conditions; re-answering it here double-counted and fought
    # them. A large group demonstrates breadth by its size. A small one has
    # to earn the benefit of the doubt, and this is how.
    breadth_significance_below: int = 5

    # Beta is estimated on this many trading days ENDING BEFORE the detection
    # window, so the shock itself cannot inflate the estimate of how much
    # market exposure the industry normally carries.
    beta_lookback_days: int = 252

    # s.5 — abnormal volume, as a z-score against trailing daily volume.
    abnormal_volume_z: float = 2.0

    # s.6.4 — the industry ETF must itself be meaningfully down. Set well below
    # the industry median threshold because the ETF is usually broader and
    # includes names outside this universe, so it moves less.
    benchmark_confirm_threshold: float = -0.05


@dataclass(frozen=True)
class ShockScoreWeights:
    """
    DEVLOG s.10 — Industry Shock Score, 0-100, configurable, weighting "must be
    validated through backtesting".

    These weights are a starting guess. They are equal-ish by design so that no
    single component silently dominates before we have evidence. Every component
    is reported alongside the total so the score is decomposable (s.41).
    """

    magnitude: float = 0.22
    breadth: float = 0.18
    relative_weakness: float = 0.20
    abnormality: float = 0.18
    correlation: float = 0.10
    volume: float = 0.07
    benchmark_confirmation: float = 0.05

    def as_dict(self) -> Dict[str, float]:
        return asdict(self)

    def validate(self) -> None:
        total = sum(self.as_dict().values())
        if abs(total - 1.0) > 1e-9:
            raise ValueError(f"Shock score weights must sum to 1.0, got {total}")


@dataclass(frozen=True)
class AlertConfig:
    """DEVLOG s.11 — WATCH / INVESTIGATE / MAJOR EVENT."""

    watch_score: float = 35.0
    investigate_score: float = 55.0
    major_event_score: float = 75.0

    # An INVESTIGATE or MAJOR alert additionally requires the hard trigger
    # conditions in s.6 to be met, not just a score. The score alone cannot
    # promote an industry past WATCH.
    require_trigger_for_investigate: bool = True


@dataclass(frozen=True)
class RecoveryConfig:
    """
    DEVLOG s.18/s.19 — company resilience and Recovery Candidate Score.

    PROTOTYPE LIMITATION: the full input list in s.18 needs a fundamentals feed
    that is not wired yet. This prototype scores only the inputs it can compute
    deterministically from price/volume, and reports coverage so the user can
    see how much of the intended model is actually running.
    """

    implemented_inputs: tuple = (
        "event_exposure",       # relative decline vs industry median
        "drawdown_depth",       # distance from 52w high
        "historical_resilience",  # behaviour in prior comparable drawdowns
        "liquidity",            # dollar volume
        "relative_stability",   # trailing volatility vs industry
    )
    pending_inputs: tuple = (
        "balance_sheet_strength", "cash", "debt", "free_cash_flow",
        "profitability", "operating_margin", "market_share",
        "competitive_advantage", "geographic_diversification",
        "revenue_diversification", "dividend_capacity", "valuation",
        "earnings_revisions", "industry_position",
    )

    @property
    def coverage(self) -> float:
        n_done = len(self.implemented_inputs)
        return n_done / (n_done + len(self.pending_inputs))


@dataclass(frozen=True)
class Config:
    detection: DetectionConfig = field(default_factory=DetectionConfig)
    weights: ShockScoreWeights = field(default_factory=ShockScoreWeights)
    alerts: AlertConfig = field(default_factory=AlertConfig)
    recovery: RecoveryConfig = field(default_factory=RecoveryConfig)

    # DEVLOG s.5 — benchmarks.
    #
    # The S&P 500 is the PRIMARY comparator and never varies. It answers "is
    # this an industry problem or is everything down", and it is the quality
    # gate for the universe.
    market_benchmark: str = "^GSPC"
    # The SECOND comparator is resolved per industry from data/benchmarks.py —
    # an airline group is compared to an airline ETF, not to the Nasdaq. There
    # is deliberately no fixed secondary index here.

    params_version: str = PARAMS_VERSION

    def validate(self) -> None:
        self.weights.validate()


CONFIG = Config()
CONFIG.validate()
