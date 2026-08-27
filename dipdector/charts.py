"""
Charts, drawn as inline SVG.

No charting library and no JavaScript. The report has to stay a single file you
can open offline, email to yourself, or archive next to the event record and
still have it render in five years. A CDN dependency breaks all three.

Two charts, because two questions need answering visually:

  performance_chart  — did this group fall apart from the market, or with it?
                       The whole thesis depends on the answer being "apart".
  distribution_chart — did the whole group fall, or did the median get dragged
                       down by two disasters? A median of -16% means something
                       very different in each case.
"""

from __future__ import annotations

from typing import List, Optional

import numpy as np
import pandas as pd

INK = "#16181B"
MUTED = "#565B62"
RULE = "#D8D8D1"
PAPER = "#F7F7F3"
INDUSTRY = "#9E2B21"
MARKET = "#3B4A5C"
BENCH = "#8A6B3D"
BAND = "#E8E2DF"


def _esc(s: str) -> str:
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def _rebase(s: pd.Series) -> pd.Series:
    s = s.dropna()
    return (s / s.iloc[0] - 1.0) * 100 if len(s) else s


def performance_chart(close: pd.DataFrame, tickers: List[str],
                      market: str, benchmark: Optional[str],
                      window: int, benchmark_name: str = "",
                      lookback: int = 60,
                      width: int = 616, height: int = 230) -> str:
    """
    Cumulative return of the affected companies against the broad market.

    Equal-weighted basket, rebased to zero at the left edge. The detection
    window is shaded so you can see how much of the move happened inside it —
    a fall that was already underway for two months is a different animal from
    one that arrived in five days.
    """
    cols = [t for t in tickers if t in close.columns]
    if not cols or market not in close.columns:
        return ""

    basket_px = close[cols].dropna(how="all").tail(lookback)
    if len(basket_px) < 10:
        return ""
    basket = _rebase((basket_px / basket_px.bfill().iloc[0]).mean(axis=1))
    mkt = _rebase(close[market].reindex(basket.index).ffill())
    bench = (_rebase(close[benchmark].reindex(basket.index).ffill())
             if benchmark and benchmark in close.columns else None)

    # S&P 500 always present; the third line is this industry's own ETF.
    series = [("Affected companies", basket, INDUSTRY, 2.4),
              ("S&P 500", mkt, MARKET, 1.6)]
    if bench is not None:
        series.append((benchmark or "Industry ETF", bench, BENCH, 1.5))

    pad_l, pad_r, pad_t, pad_b = 6, 118, 14, 26
    plot_w, plot_h = width - pad_l - pad_r, height - pad_t - pad_b

    lo = min(float(s.min()) for _, s, _, _ in series)
    hi = max(float(s.max()) for _, s, _, _ in series)
    span = max(hi - lo, 1.0)
    lo, hi = lo - span * 0.10, hi + span * 0.10

    def X(i, n):
        return pad_l + (i / max(n - 1, 1)) * plot_w

    def Y(v):
        return pad_t + (hi - v) / (hi - lo) * plot_h

    parts = [f'<svg viewBox="0 0 {width} {height}" width="100%" '
             f'role="img" aria-label="Cumulative return of the affected '
             f'companies against the market over the last {len(basket)} '
             f'trading days" xmlns="http://www.w3.org/2000/svg" '
             f'style="display:block">']

    # detection window band
    n = len(basket)
    if n > window:
        bx = X(n - window - 1, n)
        parts.append(f'<rect x="{bx:.1f}" y="{pad_t}" width="{plot_w - bx + pad_l:.1f}" '
                     f'height="{plot_h}" fill="{BAND}"/>')
        parts.append(f'<text x="{bx + 4:.1f}" y="{pad_t + 11}" font-size="9.5" '
                     f'fill="{MUTED}" font-family="IBM Plex Mono,monospace">'
                     f'{window}-day window</text>')

    # zero line and gridlines
    for v in (0.0, hi, lo):
        y = Y(v)
        solid = abs(v) < 1e-9
        parts.append(f'<line x1="{pad_l}" y1="{y:.1f}" x2="{pad_l + plot_w}" '
                     f'y2="{y:.1f}" stroke="{INK if solid else RULE}" '
                     f'stroke-width="{1 if solid else 0.7}" '
                     f'{"" if solid else "stroke-dasharray=\'2 3\'"}/>')
        parts.append(f'<text x="{pad_l + plot_w + 6}" y="{y + 3.5:.1f}" '
                     f'font-size="10" fill="{MUTED}" '
                     f'font-family="IBM Plex Mono,monospace">{v:+.0f}%</text>')

    for label, s, colour, sw in series:
        pts = " ".join(f"{X(i, len(s)):.1f},{Y(float(v)):.1f}"
                       for i, v in enumerate(s.values))
        parts.append(f'<polyline points="{pts}" fill="none" stroke="{colour}" '
                     f'stroke-width="{sw}" stroke-linejoin="round" '
                     f'stroke-linecap="round"/>')
        end_y = Y(float(s.iloc[-1]))
        parts.append(f'<circle cx="{X(len(s) - 1, len(s)):.1f}" cy="{end_y:.1f}" '
                     f'r="{2.6 if sw > 2 else 2}" fill="{colour}"/>')

    # legend along the bottom
    lx = pad_l
    for label, s, colour, _ in series:
        parts.append(f'<rect x="{lx}" y="{height - 11}" width="10" height="2.5" '
                     f'fill="{colour}"/>')
        parts.append(f'<text x="{lx + 14}" y="{height - 7}" font-size="10.5" '
                     f'fill="{MUTED}">{_esc(label)} '
                     f'<tspan fill="{colour}">{float(s.iloc[-1]):+.1f}%</tspan></text>')
        lx += 26 + len(label) * 6.1 + 40

    parts.append("</svg>")
    return "".join(parts)


def distribution_chart(companies, window: int, market_return: float,
                       median_return: float, width: int = 616,
                       row_h: int = 21) -> str:
    """
    Every member's return over the window, worst first.

    The median tells you where the middle is; this tells you whether there is a
    middle. A tight cluster is a shared cause. A long tail with a few survivors
    is several different stories being averaged together.
    """
    if not companies:
        return ""
    vals = [(c.ticker, c.returns.get(window)) for c in companies]
    vals = [(t, v) for t, v in vals if v is not None]
    if not vals:
        return ""

    height = len(vals) * row_h + 34
    label_w, pad_r = 46, 54
    plot_w = width - label_w - pad_r

    lo = min(min(v for _, v in vals), market_return, median_return, -0.01)
    hi = max(max(v for _, v in vals), market_return, 0.01)
    span = max(hi - lo, 0.02)
    lo, hi = lo - span * 0.06, hi + span * 0.06

    def X(v):
        return label_w + (v - lo) / (hi - lo) * plot_w

    zero_x = X(0.0)
    parts = [f'<svg viewBox="0 0 {width} {height}" width="100%" role="img" '
             f'aria-label="Return of each company over the {window}-day window" '
             f'xmlns="http://www.w3.org/2000/svg" style="display:block">']

    for i, (ticker, v) in enumerate(vals):
        y = 8 + i * row_h
        x0, x1 = min(X(v), zero_x), max(X(v), zero_x)
        parts.append(f'<rect x="{x0:.1f}" y="{y:.1f}" width="{max(x1 - x0, 1):.1f}" '
                     f'height="{row_h - 7}" fill="{INDUSTRY}" '
                     f'fill-opacity="{0.85 if v < 0 else 0.35}"/>')
        parts.append(f'<text x="0" y="{y + row_h - 11:.1f}" font-size="11" '
                     f'fill="{INK}" font-family="IBM Plex Mono,monospace">'
                     f'{_esc(ticker)}</text>')
        parts.append(f'<text x="{width - pad_r + 6}" y="{y + row_h - 11:.1f}" '
                     f'font-size="11" fill="{MUTED}" '
                     f'font-family="IBM Plex Mono,monospace">{v * 100:+.1f}%</text>')

    bottom = 8 + len(vals) * row_h
    parts.append(f'<line x1="{zero_x:.1f}" y1="4" x2="{zero_x:.1f}" '
                 f'y2="{bottom:.1f}" stroke="{INK}" stroke-width="1"/>')

    for value, colour, label in ((market_return, MARKET, "S&P 500"),
                                 (median_return, INK, "median")):
        x = X(value)
        parts.append(f'<line x1="{x:.1f}" y1="4" x2="{x:.1f}" y2="{bottom:.1f}" '
                     f'stroke="{colour}" stroke-width="1.3" stroke-dasharray="3 3"/>')
        anchor = "end" if x > label_w + plot_w * 0.6 else "start"
        dx = -4 if anchor == "end" else 4
        parts.append(f'<text x="{x + dx:.1f}" y="{bottom + 14:.1f}" font-size="10" '
                     f'fill="{colour}" text-anchor="{anchor}">'
                     f'{_esc(label)} {value * 100:+.1f}%</text>')

    parts.append("</svg>")
    return "".join(parts)
