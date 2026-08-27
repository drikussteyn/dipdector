"""
Portfolio simulator.

Implements devlog s.24-28: rotation, realistic delays, transaction costs,
normal growth between events, and no assumption of perfect timing.

Two rules here are NOT in the devlog. I added them because the strategy as
specified has no answer to being wrong:

  POSITION LIMIT — the devlog rotates the whole position into one industry.
  That is 100% concentration into a group that has just crashed, entered at the
  moment of maximum uncertainty about whether the cause is temporary. The
  simulator supports a cap so this can be measured rather than assumed.

  LOSS EXIT — devlog s.26 sells only when the next opportunity appears. Under a
  structural event that never recovers, that means holding a permanently
  impaired position indefinitely while waiting for an unrelated trigger. A stop
  is not obviously correct — it converts recoverable drawdowns into realised
  losses — so it is configurable and OFF is a valid setting. But it has to be
  measurable both ways.

Both are flagged in the results so no run silently deviates from the spec.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np
import pandas as pd


@dataclass
class ExecutionConfig:
    """Devlog s.25 — the delay scenarios."""

    name: str = "base"
    detection_delay: int = 1        # trading days from firing to you seeing it
    decision_delay: int = 2         # from seeing it to acting
    exit_delay: int = 2
    slippage_bps: float = 15.0
    commission_bps: float = 5.0
    entry_tranches: int = 2         # split the buy, don't assume one clean fill
    tranche_gap_days: int = 3

    @property
    def entry_delay(self) -> int:
        return self.detection_delay + self.decision_delay

    @property
    def cost_bps(self) -> float:
        return self.slippage_bps + self.commission_bps


SCENARIOS = {
    "optimistic":   ExecutionConfig("optimistic", 0, 1, 1, 8, 3, 1, 0),
    "base":         ExecutionConfig("base", 1, 2, 2, 15, 5, 2, 3),
    "conservative": ExecutionConfig("conservative", 2, 4, 4, 30, 8, 3, 5),
}


@dataclass
class StrategyConfig:
    starting_capital: float = 100_000.0
    n_positions: int = 4              # top-N recovery candidates per event
    max_position_pct: float = 1.0     # 1.0 = devlog behaviour, all-in
    min_hold_days: int = 20
    max_hold_days: Optional[int] = 504
    stop_loss: Optional[float] = None  # e.g. -0.25; None = devlog behaviour
    rotate_on_better_score: bool = True
    rotation_score_margin: float = 5.0

    def deviations(self) -> List[str]:
        out = []
        if self.max_position_pct < 1.0:
            out.append(
                f"Position capped at {self.max_position_pct:.0%} of the portfolio. "
                f"Devlog s.26 rotates the full position; this run does not.")
        if self.stop_loss is not None:
            out.append(
                f"Loss exit at {self.stop_loss:.0%}. Devlog s.26 has no exit "
                f"other than rotation; this run adds one.")
        if self.max_hold_days:
            out.append(
                f"Positions force-closed after {self.max_hold_days} trading days.")
        return out


@dataclass
class Trade:
    ticker: str
    industry: str
    side: str
    date: dt.date
    shares: float
    price: float
    cost: float
    reason: str


@dataclass
class Position:
    ticker: str
    industry: str
    shares: float
    cost_basis: float
    opened_on: dt.date
    event_score: float

    def value(self, px: float) -> float:
        return self.shares * px


@dataclass
class BacktestResult:
    equity: pd.Series
    trades: List[Trade]
    strategy: StrategyConfig
    execution: ExecutionConfig
    events_taken: int
    events_skipped: int
    deviations: List[str] = field(default_factory=list)
    synthetic: bool = False


class Simulator:
    def __init__(self, close: pd.DataFrame, strategy: StrategyConfig,
                 execution: ExecutionConfig):
        self.close = close
        self.s = strategy
        self.x = execution

    # -- helpers ---------------------------------------------------------
    def _px(self, ticker: str, day: pd.Timestamp) -> Optional[float]:
        if ticker not in self.close.columns:
            return None
        s = self.close[ticker]
        v = s.loc[:day]
        if v.empty or pd.isna(v.iloc[-1]):
            return None
        return float(v.iloc[-1])

    def _fill(self, price: float, side: str) -> float:
        """Slippage always works against you."""
        adj = self.x.cost_bps / 10_000
        return price * (1 + adj) if side == "buy" else price * (1 - adj)

    # -- main loop -------------------------------------------------------
    def run(self, events, candidates_by_event: Dict[int, List[str]],
            start: dt.date, end: dt.date) -> BacktestResult:
        idx = [d for d in self.close.index if start <= d.date() <= end]
        cash = self.s.starting_capital
        positions: Dict[str, Position] = {}
        trades: List[Trade] = []
        equity = []

        # Schedule entries at detection + delay. Nothing is decided on the day
        # the event fires; that is the point of the delay.
        pending: Dict[pd.Timestamp, list] = {}
        pos_of = {d: i for i, d in enumerate(idx)}
        taken = skipped = 0

        for ev_i, ev in enumerate(events):
            det = pd.Timestamp(ev.detected_on)
            if det not in pos_of:
                nxt = [d for d in idx if d >= det]
                if not nxt:
                    skipped += 1
                    continue
                det = nxt[0]
            for t in range(self.x.entry_tranches):
                offset = self.x.entry_delay + t * self.x.tranche_gap_days
                p = pos_of[det] + offset
                if p < len(idx):
                    pending.setdefault(idx[p], []).append(
                        (ev_i, ev, t, self.x.entry_tranches))

        for day in idx:
            d = day.date()

            # 1. Exits that do not depend on new events (devlog s.26 keeps the
            #    position through recovery; these are the safety valves).
            for tk in list(positions):
                p = positions[tk]
                px = self._px(tk, day)
                if px is None:
                    continue
                held = (d - p.opened_on).days
                ret = px * p.shares / p.cost_basis - 1.0
                reason = None
                if self.s.stop_loss is not None and ret <= self.s.stop_loss:
                    reason = f"loss exit at {ret:.1%}"
                elif self.s.max_hold_days and held >= self.s.max_hold_days * 1.4:
                    reason = f"max holding period ({held} days)"
                if reason:
                    fill = self._fill(px, "sell")
                    cash += p.shares * fill
                    trades.append(Trade(tk, p.industry, "sell", d, p.shares,
                                        fill, 0.0, reason))
                    del positions[tk]

            # 2. Scheduled entries.
            for (ev_i, ev, tranche, n_tr) in pending.get(day, []):
                tickers = candidates_by_event.get(ev_i, ev.tickers)[:self.s.n_positions]
                tickers = [t for t in tickers if self._px(t, day) is not None]
                if not tickers:
                    skipped += 1
                    continue

                held_industries = {p.industry for p in positions.values()}
                if ev.industry in held_industries and tranche == 0:
                    skipped += 1
                    continue

                # Rotation (devlog s.28): only exit the current holding if this
                # event is meaningfully stronger and the minimum hold has passed.
                if positions and tranche == 0 and self.s.rotate_on_better_score:
                    worst = min(positions.values(), key=lambda p: p.event_score)
                    old_enough = (d - worst.opened_on).days >= self.s.min_hold_days
                    better = ev.score > worst.event_score + self.s.rotation_score_margin
                    if old_enough and better:
                        for tk in list(positions):
                            p = positions[tk]
                            px = self._px(tk, day)
                            if px is None:
                                continue
                            fill = self._fill(px, "sell")
                            cash += p.shares * fill
                            trades.append(Trade(tk, p.industry, "sell", d,
                                                p.shares, fill, 0.0,
                                                f"rotating into {ev.industry}"))
                            del positions[tk]
                    elif not old_enough:
                        skipped += 1
                        continue

                equity_now = cash + sum(
                    p.value(self._px(tk, day) or 0) for tk, p in positions.items())
                budget = equity_now * self.s.max_position_pct / n_tr
                budget = min(budget, cash)
                if budget <= 0:
                    skipped += 1
                    continue

                per_name = budget / len(tickers)
                for tk in tickers:
                    px = self._px(tk, day)
                    fill = self._fill(px, "buy")
                    shares = per_name / fill
                    cash -= shares * fill
                    if tk in positions:
                        p = positions[tk]
                        p.shares += shares
                        p.cost_basis += shares * fill
                    else:
                        positions[tk] = Position(tk, ev.industry, shares,
                                                 shares * fill, d, ev.score)
                    trades.append(Trade(tk, ev.industry, "buy", d, shares, fill,
                                        shares * fill * self.x.cost_bps / 10_000,
                                        f"tranche {tranche + 1}/{n_tr}"))
                if tranche == 0:
                    taken += 1

            # 3. Mark to market (devlog s.27 — normal growth between events).
            val = cash + sum(p.value(self._px(tk, day) or 0)
                             for tk, p in positions.items())
            equity.append(val)

        return BacktestResult(
            equity=pd.Series(equity, index=idx),
            trades=trades, strategy=self.s, execution=self.x,
            events_taken=taken, events_skipped=skipped,
            deviations=self.s.deviations(),
        )
