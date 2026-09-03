"""
End-to-end replay runner.

Runs the full DipDector chain against a single as-of date:

  universe -> price frame -> deterministic metrics -> Industry Shock Score
  -> alert level -> news retrieval -> AI event classification
  -> recovery ranking -> event store -> HTML report

Deliberately built as a replay rather than a live monitor. Same code path as
live operation would use, but it takes an --as-of date, truncates every series
at that date, and never looks past it. That makes it testable today and makes it
the skeleton the Phase 3 backtester can loop over.

Usage:
  python -m dipdector.replay --as-of 2026-06-30 --provider fixture \\
      --fixture dipdector/fixtures/synthetic_semis.csv
  python -m dipdector.replay --as-of 2026-06-30 --provider yfinance
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import sys
from typing import List

from .config import CONFIG, load_env
from .analysis.recovery import score_candidates
from .data.benchmarks import all_tickers as benchmark_tickers
from .data.providers import get_provider
from .data.universe import default_provider
from .engine.detection import AlertLevel, scan
from .engine.metrics import compute_industry_metrics
from .news.engine import (EODHDNewsProvider, NullNewsProvider,
                          classify_event, keywords_for)
from .report import render
from .store.db import EventStore


def run(as_of: dt.date, provider_kind: str, fixture: str, out: str,
        db_path: str, level_filter: str, history_days: int) -> int:
    classification = default_provider()
    companies = classification.companies_as_of(as_of)
    # S&P 500 always, plus every industry ETF the registry might resolve to.
    tickers = ([c.ticker for c in companies] + [CONFIG.market_benchmark]
               + benchmark_tickers())

    kwargs = {"path": fixture} if provider_kind == "fixture" else {}
    price_provider = get_provider(provider_kind, **kwargs)
    start = as_of - dt.timedelta(days=history_days)

    print(f"[1/6] Fetching {len(tickers)} tickers from {price_provider.name} "
          f"({start} → {as_of})")
    frame = get_provider(provider_kind, **kwargs).fetch(tickers, start, as_of)
    frame = frame.as_of(as_of)
    if frame.synthetic:
        print("      ⚠ SYNTHETIC DATA — pipeline test only, not a market observation.")

    thin = [t for t, cov in frame.coverage_report().items() if cov < 0.9]
    if thin:
        print(f"      ⚠ Sparse history: {', '.join(thin[:8])}")

    print(f"[2/6] Computing metrics at sub-industry level")
    industry_metrics = []
    for industry, members in classification.industries(as_of).items():
        m = compute_industry_metrics(industry, members, frame, as_of, CONFIG)
        if m is None:
            print(f"      skip {industry}: fewer than "
                  f"{CONFIG.detection.min_industry_members} usable members")
            continue
        industry_metrics.append(m)

    print(f"[3/6] Scoring {len(industry_metrics)} industries")
    assessments = scan(industry_metrics, CONFIG)
    for a in assessments:
        print(f"      {a.industry:<40} {a.score:5.1f}  {a.level.value}")

    threshold = AlertLevel[level_filter]
    order = [AlertLevel.NONE, AlertLevel.WATCH, AlertLevel.INVESTIGATE,
             AlertLevel.MAJOR_EVENT]
    actionable = [a for a in assessments
                  if order.index(a.level) >= order.index(threshold)]

    if not actionable:
        print(f"\nNo industry reached {level_filter} on {as_of}. "
              f"This is the expected result on most days.")
        return 0

    news_provider = NullNewsProvider()
    if os.environ.get("EODHD_API_KEY"):
        news_provider = EODHDNewsProvider()

    store = EventStore(db_path)
    store.upsert_companies(companies)
    events_for_report = []

    for a in actionable:
        print(f"\n[4/6] {a.industry}: investigating cause "
              f"(news provider: {news_provider.name})")
        kws = keywords_for(a.industry, [c.ticker for c in a.metrics.companies])
        cutoff = dt.datetime.combine(as_of, dt.time(23, 59),
                                     tzinfo=dt.timezone.utc)
        articles = news_provider.fetch(kws, cutoff, lookback_days=10)
        print(f"      {len(articles)} articles within the as-of cutoff")
        event = classify_event(a.industry, a, articles)
        if event.is_stub:
            print(f"      ⚠ Cause analysis did not run: {event.reasoning[:80]}…")
        else:
            print(f"      → {event.title}")
            print(f"        severity {event.severity} | temporary "
                  f"{event.temporary_confidence} | structural "
                  f"{event.structural_risk}")

        print(f"[5/6] Ranking recovery candidates")
        candidates = score_candidates(a.metrics, frame.close, CONFIG, event)
        for i, c in enumerate(candidates[:5], 1):
            print(f"      {i}. {c.ticker:<6} {c.score:5.1f}")

        event_id = store.record_event(a, frame, event, candidates, articles)
        print(f"      stored as event {event_id}")

        events_for_report.append({
            "assessment": a, "event": event,
            "candidates": candidates[:6],
            "coverage": CONFIG.recovery.coverage,
            "close": frame.close,
            "market": CONFIG.market_benchmark,
        })

    print(f"\n[6/6] Writing report")
    html = render(events_for_report, {
        "as_of": as_of.isoformat(),
        "window": CONFIG.detection.primary_window,
        "params_version": CONFIG.params_version,
        "source": frame.source,
        "synthetic": frame.synthetic,
        "generated_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
    })
    with open(out, "w") as f:
        f.write(html)
    print(f"      {out}")
    store.close()
    return len(actionable)


def main():
    load_env()
    p = argparse.ArgumentParser(description="DipDector industry shock replay")
    p.add_argument("--as-of", required=True, help="YYYY-MM-DD")
    p.add_argument("--provider", default="fixture",
                   choices=["fixture", "yfinance", "eodhd"])
    p.add_argument("--fixture", default="dipdector/fixtures/synthetic_semis.csv")
    p.add_argument("--out", default="dipdector_report.html")
    p.add_argument("--db", default="dipdector.duckdb")
    p.add_argument("--level", default="WATCH",
                   choices=["WATCH", "INVESTIGATE", "MAJOR_EVENT"],
                   help="minimum alert level to investigate and report")
    p.add_argument("--history-days", type=int, default=700,
                   help="calendar days of price history to load before as-of")
    args = p.parse_args()

    as_of = dt.date.fromisoformat(args.as_of)
    if as_of > dt.date.today():
        sys.exit(f"as-of {as_of} is in the future.")

    n = run(as_of, args.provider, args.fixture, args.out, args.db,
            args.level, args.history_days)
    print(f"\nDone. {n} industry event(s) at or above {args.level}.")


if __name__ == "__main__":
    main()
