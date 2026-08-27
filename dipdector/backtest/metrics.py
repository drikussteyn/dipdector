"""
Performance metrics and control benchmarks.

Devlog Phase 7 requires comparison against buy-and-hold, random event selection
and simpler dip-buying. The random-event control is the important one and the
one people skip: it takes the same capital, the same delays, the same costs and
the same position sizing, but picks its entry dates at random. If the strategy
cannot beat that, the detector is contributing nothing and the returns are
coming from being invested in equities, which you could have had for free.
"""

from __future__ import annotations

import datetime as dt
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

TRADING_DAYS = 252


def performance(equity: pd.Series, rf: float = 0.02) -> Dict[str, float]:
    eq = equity.dropna()
    if len(eq) < 2:
        return {}
    rets = eq.pct_change().dropna()
    years = len(eq) / TRADING_DAYS
    total = float(eq.iloc[-1] / eq.iloc[0] - 1.0)
    cagr = float((eq.iloc[-1] / eq.iloc[0]) ** (1 / years) - 1.0) if years > 0 else np.nan

    running_max = eq.cummax()
    dd = eq / running_max - 1.0
    max_dd = float(dd.min())

    ann_vol = float(rets.std() * np.sqrt(TRADING_DAYS))
    excess = rets.mean() * TRADING_DAYS - rf
    sharpe = float(excess / ann_vol) if ann_vol > 0 else np.nan

    downside = rets[rets < 0]
    dvol = float(downside.std() * np.sqrt(TRADING_DAYS)) if len(downside) > 1 else np.nan
    sortino = float(excess / dvol) if dvol and dvol > 0 else np.nan

    # How long the worst drawdown lasted. Often more decisive than its depth
    # for whether a person actually sticks with a strategy.
    underwater, longest, cur = dd < -0.01, 0, 0
    for u in underwater:
        cur = cur + 1 if u else 0
        longest = max(longest, cur)

    return {
        "total_return": total, "cagr": cagr, "max_drawdown": max_dd,
        "longest_drawdown_days": longest, "ann_volatility": ann_vol,
        "sharpe": sharpe, "sortino": sortino, "years": years,
        "final_value": float(eq.iloc[-1]),
    }


def trade_stats(trades) -> Dict[str, float]:
    """Round-trip stats. Matches buys to sells per ticker, FIFO."""
    opens: Dict[str, list] = {}
    closed = []
    for t in sorted(trades, key=lambda x: x.date):
        if t.side == "buy":
            opens.setdefault(t.ticker, []).append(t)
        else:
            lots = opens.get(t.ticker, [])
            if not lots:
                continue
            cost = sum(l.shares * l.price for l in lots)
            shares = sum(l.shares for l in lots)
            if shares <= 0:
                continue
            closed.append({
                "ticker": t.ticker, "industry": t.industry,
                "ret": (t.price * shares) / cost - 1.0,
                "days": (t.date - lots[0].date).days,
                "reason": t.reason,
            })
            opens[t.ticker] = []

    if not closed:
        return {"n_round_trips": 0}
    df = pd.DataFrame(closed)
    return {
        "n_round_trips": len(df),
        "win_rate": float((df["ret"] > 0).mean()),
        "median_return": float(df["ret"].median()),
        "mean_return": float(df["ret"].mean()),
        "worst_trade": float(df["ret"].min()),
        "best_trade": float(df["ret"].max()),
        "median_hold_days": float(df["days"].median()),
        "still_open": sum(len(v) for v in opens.values()),
    }


def buy_and_hold(close: pd.DataFrame, ticker: str, start: dt.date,
                 end: dt.date, capital: float = 100_000.0) -> pd.Series:
    if ticker not in close.columns:
        return pd.Series(dtype=float)
    s = close[ticker]
    s = s[(s.index.date >= start) & (s.index.date <= end)].dropna()
    if s.empty:
        return pd.Series(dtype=float)
    return capital * s / s.iloc[0]


def random_event_control(events, close, classification, strategy, execution,
                         start: dt.date, end: dt.date, n_runs: int = 40,
                         seed: int = 3) -> pd.DataFrame:
    """
    Same machinery, random dates.

    Keeps the number of events, the industries touched and the holding
    behaviour identical — only the timing is randomised. This isolates the
    question "is the detector picking good moments?" from "does being invested
    in equities make money?", which are very easy to confuse.
    """
    from .simulator import Simulator

    rng = np.random.default_rng(seed)
    dates = [d for d in close.index if start <= d.date() <= end]
    if len(dates) < 300 or not events:
        return pd.DataFrame()

    template = list(events)
    rows = []
    for run in range(n_runs):
        shuffled = []
        for ev in template:
            pick = dates[int(rng.integers(0, len(dates) - 260))]
            fake = type(ev)(
                industry=ev.industry, detected_on=pick.date(), peak_on=pick.date(),
                level=ev.level, score=ev.score, peak_score=ev.peak_score,
                median_return=ev.median_return, relative_return=ev.relative_return,
                n_declining=ev.n_declining, n_members=ev.n_members,
                abnormality_z=ev.abnormality_z, tickers=ev.tickers,
            )
            shuffled.append(fake)
        shuffled.sort(key=lambda e: e.detected_on)
        res = Simulator(close, strategy, execution).run(shuffled, {}, start, end)
        p = performance(res.equity)
        if p:
            rows.append({"run": run, "cagr": p["cagr"],
                         "max_drawdown": p["max_drawdown"],
                         "total_return": p["total_return"]})
    return pd.DataFrame(rows)


def naive_dip_control(close: pd.DataFrame, tickers: List[str], start: dt.date,
                      end: dt.date, threshold: float = -0.10,
                      window: int = 5, hold_days: int = 126,
                      capital: float = 100_000.0) -> pd.Series:
    """
    The strategy this app is supposed to beat: buy any single stock that fell
    10% in a week, hold six months, repeat. If DipDector does not beat this, the
    industry-wide machinery is not earning its complexity.
    """
    dates = [d for d in close.index if start <= d.date() <= end]
    if not dates:
        return pd.Series(dtype=float)

    cols = [t for t in tickers if t in close.columns]
    px = close[cols].reindex(dates).ffill()
    window_ret = px / px.shift(window) - 1.0

    cash, holdings, equity = capital, [], []
    for i, day in enumerate(dates):
        row = px.loc[day]

        # Sell anything whose holding period has expired. The proceeds go back
        # to cash — the previous version dropped them, which is why this control
        # reported a total loss.
        still_open = []
        for tk, sh, exp_i in holdings:
            if i >= exp_i:
                p = row.get(tk)
                if p is not None and not np.isnan(p):
                    cash += sh * p
                    continue
            still_open.append((tk, sh, exp_i))
        holdings = still_open

        hits = window_ret.loc[day]
        held = {h[0] for h in holdings}
        new = [tk for tk in cols
               if tk not in held and not np.isnan(hits.get(tk, np.nan))
               and hits[tk] <= threshold][:3]

        if new and cash > 0:
            per = cash * 0.5 / len(new)
            exp_i = min(i + hold_days, len(dates) - 1)
            for tk in new:
                p = row.get(tk)
                if p is None or np.isnan(p) or p <= 0:
                    continue
                holdings.append((tk, per / p, exp_i))
                cash -= per

        val = cash + sum(sh * row[tk] for tk, sh, _ in holdings
                         if not np.isnan(row.get(tk, np.nan)))
        equity.append(val)

    return pd.Series(equity, index=dates)
