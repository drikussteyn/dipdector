"""
Deterministic market metrics.

DEVLOG s.8 / s.9 / s.30: all of this is arithmetic performed by code. No AI is
allowed anywhere in this module. Nothing here reads news, and nothing here makes
a judgement — it only measures.

DEVLOG s.44.5: every function takes an `as_of` frame that has already been
truncated. Nothing in here may look forward.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from math import comb
from typing import Dict, List, Optional, Sequence

import numpy as np
import pandas as pd

from ..data.providers import PriceFrame
from ..data.benchmarks import resolve as resolve_benchmark
from ..data.universe import Company


@dataclass
class CompanyMetrics:
    """DEVLOG s.8."""

    ticker: str
    name: str
    industry: str
    sub_industry: str
    returns: Dict[int, float] = field(default_factory=dict)   # window -> return
    rel_to_market: Optional[float] = None
    rel_to_benchmark: Optional[float] = None
    rel_to_industry: Optional[float] = None
    dist_from_52w_high: Optional[float] = None
    trailing_vol: Optional[float] = None
    volume_z: Optional[float] = None
    dollar_volume: Optional[float] = None
    data_coverage: float = 1.0

    @property
    def primary_return(self) -> Optional[float]:
        return self.returns.get(5)


@dataclass
class IndustryMetrics:
    """DEVLOG s.9 — the inputs to the Industry Shock Score."""

    industry: str
    as_of: dt.date
    window: int
    n_members: int
    n_declining: int
    pct_declining: float
    median_return: float
    mean_return: float
    worst_return: float
    best_return: float
    dispersion: float
    market_return: float
    relative_to_market: float
    benchmark_ticker: Optional[str]
    benchmark_name: Optional[str]
    benchmark_overlap: Optional[str]
    relative_to_benchmark: Optional[float]
    mean_pairwise_correlation: float
    median_volume_z: float
    abnormality_z: float
    n_underperforming: int
    # Beta-adjusted statistics. See _beta() for why these matter more than the
    # raw difference against the index.
    # Breadth judged against the day, not against a fixed headcount.
    market_decline_rate: Optional[float] = None
    breadth_pvalue: Optional[float] = None
    beta_to_market: Optional[float] = None
    r_squared: Optional[float] = None
    expected_return: Optional[float] = None   # beta x market return
    alpha: Optional[float] = None             # actual - expected
    companies: List[CompanyMetrics] = field(default_factory=list)
    benchmark_return: Optional[float] = None   # the industry ETF's own return
    data_warnings: List[str] = field(default_factory=list)


def _window_return(series: pd.Series, window: int) -> Optional[float]:
    """Simple return over `window` trading days ending at the last bar."""
    s = series.dropna()
    if len(s) < window + 1:
        return None
    start, end = s.iloc[-(window + 1)], s.iloc[-1]
    if start == 0 or pd.isna(start) or pd.isna(end):
        return None
    return float(end / start - 1.0)


def _trailing_vol(series: pd.Series, lookback: int = 60) -> Optional[float]:
    s = series.dropna().pct_change().dropna()
    if len(s) < 20:
        return None
    return float(s.tail(lookback).std() * np.sqrt(252))


def _volume_z(vol: pd.Series, lookback: int = 60) -> Optional[float]:
    v = vol.dropna()
    if len(v) < 25:
        return None
    hist = v.iloc[-(lookback + 1):-1]
    if len(hist) < 20 or hist.std() == 0:
        return None
    return float((v.iloc[-1] - hist.mean()) / hist.std())


def _beta(industry: pd.Series, market: pd.Series, window: int,
          lookback: int) -> tuple:
    """
    Ordinary least squares beta of the industry basket against the market.

        beta = Cov(r_industry, r_market) / Var(r_market)

    Estimated on the `lookback` days ENDING BEFORE the detection window, so the
    shock being measured cannot contaminate the estimate.

    This matters because the raw "industry fell 17.3% while the S&P fell 1.7%"
    comparison silently assumes the industry should move one-for-one with the
    market. Semiconductors do not. A group with a beta of 1.4 is *expected* to
    fall 2.4% when the market falls 1.7%, and the interesting quantity is the
    part that beta does not explain:

        expected = beta x r_market
        alpha    = r_actual - expected

    Returns (beta, r_squared, expected_return, alpha).
    """
    ri = industry.dropna().pct_change().dropna()
    rm = market.dropna().pct_change().dropna()
    aligned = pd.concat([ri, rm], axis=1, join="inner").dropna()
    if len(aligned) < 60:
        return (None, None, None, None)

    # Drop the detection window from the estimation sample.
    est = aligned.iloc[:-window] if len(aligned) > window + 60 else aligned
    est = est.tail(lookback)
    if len(est) < 60:
        return (None, None, None, None)

    y, x = est.iloc[:, 0].values, est.iloc[:, 1].values
    var_x = float(np.var(x))
    if var_x == 0:
        return (None, None, None, None)
    beta = float(np.cov(y, x, ddof=1)[0, 1] / np.var(x, ddof=1))
    corr = float(np.corrcoef(y, x)[0, 1])
    r2 = corr ** 2

    r_ind = _window_return(industry, window)
    r_mkt = _window_return(market, window)
    if r_ind is None or r_mkt is None:
        return (beta, r2, None, None)
    expected = beta * r_mkt
    return (beta, r2, float(expected), float(r_ind - expected))


def _abnormality_z(series: pd.Series, window: int, lookback: int) -> Optional[float]:
    """
    DEVLOG s.6.5 — how unusual is this window's move against the same
    instrument's own history of same-length window moves?

    Negative z means the move is unusually bad. We return the magnitude of the
    downside surprise, so a bigger positive number means more abnormal decline.
    """
    s = series.dropna()
    if len(s) < window + 40:
        return None
    rolling = s.pct_change(window).dropna()
    hist = rolling.iloc[-(lookback + 1):-1]
    if len(hist) < 30 or hist.std() == 0:
        return None
    current = rolling.iloc[-1]
    return float(-(current - hist.mean()) / hist.std())


def compute_company_metrics(
    company: Company,
    frame: PriceFrame,
    windows: List[int],
    market_returns: Dict[int, float],
    benchmark_returns: Dict[int, float],
    primary_window: int,
) -> Optional[CompanyMetrics]:
    if company.ticker not in frame.close.columns:
        return None
    px = frame.close[company.ticker]
    if px.dropna().empty:
        return None

    m = CompanyMetrics(
        ticker=company.ticker,
        name=company.name,
        industry=company.industry,
        sub_industry=company.sub_industry,
    )
    for w in windows:
        r = _window_return(px, w)
        if r is not None:
            m.returns[w] = r

    pr = m.returns.get(primary_window)
    if pr is not None:
        if market_returns.get(primary_window) is not None:
            m.rel_to_market = pr - market_returns[primary_window]
        if benchmark_returns.get(primary_window) is not None:
            m.rel_to_benchmark = pr - benchmark_returns[primary_window]

    hi = px.dropna().tail(252).max()
    if hi and hi > 0:
        m.dist_from_52w_high = float(px.dropna().iloc[-1] / hi - 1.0)

    m.trailing_vol = _trailing_vol(px)
    if company.ticker in frame.volume.columns:
        vol = frame.volume[company.ticker]
        m.volume_z = _volume_z(vol)
        v = vol.dropna()
        if not v.empty:
            m.dollar_volume = float(v.tail(20).mean() * px.dropna().iloc[-1])

    m.data_coverage = float(px.notna().mean())
    return m


def breadth_pvalue(k: int, n: int,
                   base_rate: Optional[float]) -> Optional[float]:
    """
    P(at least k of n declining | each declines independently at base_rate).

    The exact binomial upper tail. Answers "how surprised should I be that
    this many of the group fell?", which is the question a flat member count
    cannot express: 3 of 3 is a 9% coincidence when 45% of the market is
    falling and a 0.8% event when 20% is.

    Independence is not true — companies in one industry are correlated by
    construction, which is the whole premise here — so this OVERSTATES the
    surprise. It is a screening statistic, not an inference, and it is used
    as one: a gate that small groups must clear, not a probability reported
    to the reader as if it were calibrated.
    """
    if n <= 0 or k <= 0 or base_rate is None:
        return None
    k = min(k, n)
    # Degenerate base rates make the tail meaningless in both directions.
    p = min(max(base_rate, 0.01), 0.99)
    return float(sum(comb(n, i) * p ** i * (1.0 - p) ** (n - i)
                     for i in range(k, n + 1)))


def market_decline_rate(frame: PriceFrame, window: int, threshold: float,
                        exclude: Optional[Sequence[str]] = None,
                        min_universe: int = 30) -> Optional[float]:
    """
    Fraction of the rest of the universe declining at or below `threshold`.

    Returns None when there is no usable comparison group, rather than a
    guess. s.44 — absence of evidence is not evidence: a base rate that
    cannot be estimated makes the significance test unavailable, and the
    caller must skip that condition rather than let the industry pass it.

    Two exclusions, both load-bearing. Benchmarks are dropped because an ETF
    is not a company and holds the very constituents being measured. The
    industry's own members are dropped because comparing a group against a
    universe that contains it asks whether it fell more than itself.
    """
    close = frame.close
    if len(close) <= window:
        return None
    skip = set(exclude or ())
    cols = [c for c in close.columns if c not in skip]
    if len(cols) < min_universe:
        return None
    latest, prior = close.iloc[-1], close.iloc[-1 - window]
    rets = (latest[cols] / prior[cols] - 1.0).dropna()
    if len(rets) < min_universe:
        return None
    return float((rets <= threshold).sum() / len(rets))


def compute_industry_metrics(
    industry: str,
    companies: List[Company],
    frame: PriceFrame,
    as_of: dt.date,
    cfg,
    base_rate: Optional[float] = None,
) -> Optional[IndustryMetrics]:
    """
    `base_rate` is the market-wide decline rate for this window. Callers that
    loop over many industries on one date should compute it once with
    market_decline_rate() and pass it in; it is identical for every industry
    on a given day and recomputing it per group is pure waste.
    """
    d = cfg.detection
    windows = [d.primary_window, *d.context_windows]
    w = d.primary_window

    market_px = frame.close.get(cfg.market_benchmark)
    market_returns = ({win: _window_return(market_px, win) for win in windows}
                      if market_px is not None else {})

    # Second comparator: this industry's own ETF, resolved from the registry.
    bm = resolve_benchmark(companies[0].sub_industry, companies[0].industry,
                           companies[0].sector) if companies else None
    bm_px = frame.close.get(bm.ticker) if bm else None
    benchmark_returns = ({win: _window_return(bm_px, win) for win in windows}
                         if bm_px is not None else {})

    warnings: List[str] = []
    if market_px is None:
        warnings.append("S&P 500 series missing — relative metrics unavailable.")
    if bm is None:
        warnings.append(
            f"No industry ETF is mapped for this group, so the s.6.4 benchmark "
            f"confirmation condition is skipped rather than passed.")
    elif bm_px is None:
        warnings.append(
            f"{bm.ticker} ({bm.name}) is mapped as the industry benchmark but "
            f"no price data was returned for it.")
    if frame.synthetic:
        warnings.append("SYNTHETIC DATA — not a real market observation.")

    cms = []
    for c in companies:
        cm = compute_company_metrics(c, frame, windows, market_returns,
                                     benchmark_returns, w)
        if cm is not None and cm.primary_return is not None:
            cms.append(cm)

    if len(cms) < d.min_industry_members:
        return None

    if base_rate is None:
        from ..data.benchmarks import all_tickers
        base_rate = market_decline_rate(
            frame, w, d.material_decline,
            exclude=[cfg.market_benchmark, *all_tickers(),
                     *[cm.ticker for cm in cms]])

    rets = np.array([cm.returns[w] for cm in cms], dtype=float)
    market_r = market_returns.get(w) or 0.0
    bm_r = benchmark_returns.get(w)

    # DEVLOG s.9 — correlation of constituent declines, measured on daily
    # returns across the detection window plus a short lead-in for stability.
    tickers = [cm.ticker for cm in cms]
    sub = frame.close[tickers].tail(max(w * 4, 20)).pct_change().dropna(how="all")
    corr = sub.corr()
    if len(tickers) > 1 and not corr.isna().all().all():
        iu = np.triu_indices_from(corr.values, k=1)
        pair_corrs = corr.values[iu]
        mean_corr = float(np.nanmean(pair_corrs))
    else:
        mean_corr = float("nan")

    # Industry composite: equal-weighted price index of members, used to ask
    # how abnormal the industry's own move is against its own history.
    composite = frame.close[tickers].dropna(how="all")
    composite = (composite / composite.bfill().iloc[0]).mean(axis=1)
    abn = _abnormality_z(composite, w, d.volatility_lookback_days)

    vol_zs = [cm.volume_z for cm in cms if cm.volume_z is not None]
    median_vol_z = float(np.median(vol_zs)) if vol_zs else 0.0

    n_declining = int(sum(1 for r in rets if r <= d.material_decline))
    n_under = int(sum(
        1 for cm in cms
        if cm.rel_to_market is not None
        and cm.rel_to_market <= d.relative_underperformance_threshold
    ))

    poor = [cm.ticker for cm in cms if cm.data_coverage < 0.9]
    if poor:
        warnings.append(f"Sparse price history for: {', '.join(poor)}")

    beta = r2 = expected = alpha = None
    if market_px is not None:
        beta, r2, expected, alpha = _beta(composite, market_px, w,
                                          d.beta_lookback_days)
    if beta is None:
        warnings.append("Beta could not be estimated — relative figures are "
                        "raw differences, not risk-adjusted.")
    elif r2 is not None and r2 < 0.10:
        # A regression that explains 1% of the variance gives a beta that is
        # noise wearing a decimal point. Discard it rather than let a precise-
        # looking number carry weight it has not earned.
        warnings.append(
            f"Beta discarded: the market explains only {r2:.0%} of this group's "
            f"normal movement (R\u00b2 {r2:.2f}), so the estimate is unreliable. "
            f"Relative figures below are raw differences.")
        beta = expected = alpha = None

    median_r = float(np.median(rets))
    return IndustryMetrics(
        industry=industry,
        as_of=as_of,
        window=w,
        n_members=len(cms),
        n_declining=n_declining,
        pct_declining=n_declining / len(cms),
        market_decline_rate=base_rate,
        breadth_pvalue=breadth_pvalue(n_declining, len(cms), base_rate),
        median_return=median_r,
        mean_return=float(np.mean(rets)),
        worst_return=float(np.min(rets)),
        best_return=float(np.max(rets)),
        dispersion=float(np.std(rets)),
        market_return=float(market_r),
        relative_to_market=median_r - float(market_r),
        benchmark_ticker=bm.ticker if bm else None,
        benchmark_name=bm.name if bm else None,
        benchmark_overlap=bm.overlap if bm else None,
        relative_to_benchmark=(median_r - bm_r) if bm_r is not None else None,
        mean_pairwise_correlation=mean_corr,
        median_volume_z=median_vol_z,
        abnormality_z=float(abn) if abn is not None else 0.0,
        n_underperforming=n_under,
        beta_to_market=beta, r_squared=r2,
        expected_return=expected, alpha=alpha,
        companies=sorted(cms, key=lambda c: c.returns[w]),
        benchmark_return=bm_r,
        data_warnings=warnings,
    )
