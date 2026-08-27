"""
Daily job.

  python -m dipdector.daily --to you@example.com

What it does, in order:

  1. Fetch prices for the universe plus the S&P 500 and every industry ETF
  2. Check the data is actually fresh — a stale feed produces a silent
     false negative, which is the worst failure mode this thing has
  3. Score every industry as of the last completed trading day
  4. Drop anything already alerted on (notify/state.py)
  5. Email what remains, with the full report attached
  6. Write state back so tomorrow's run knows what today sent

Exit codes: 0 nothing to report, 10 alerts sent, 1 failure. A scheduler can
act on those.
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import sys
import traceback

import pandas as pd

from .analysis.recovery import score_candidates
from .config import CONFIG
from .data.benchmarks import all_tickers as benchmark_tickers
from .data.providers import get_provider
from .data.universe import default_provider
from .engine.detection import AlertLevel, score_industry
from .engine.metrics import compute_industry_metrics
from .news.free import FreeNewsProvider
from .news.engine import (EODHDNewsProvider, NullNewsProvider, classify_event,
                          keywords_for)
from .notify.email import compose, compose_heartbeat, get_sender
from .notify.state import AlertState
from .report import render
from .store.db import EventStore

LEVEL_ORDER = [AlertLevel.NONE, AlertLevel.WATCH, AlertLevel.INVESTIGATE,
               AlertLevel.MAJOR_EVENT]


def _fetch(provider_kind: str, fixture: str, tickers, as_of, history_days):
    kwargs = {"path": fixture} if provider_kind == "fixture" else {}
    last_error = None
    for attempt in range(3):
        try:
            frame = get_provider(provider_kind, **kwargs).fetch(
                tickers, as_of - dt.timedelta(days=history_days), as_of)
            return frame, None
        except Exception as exc:            # noqa: BLE001 — retry anything
            last_error = exc
            if attempt < 2:
                import time
                time.sleep(5 * (attempt + 1))
    return None, last_error


def run(args) -> int:
    today = (dt.date.fromisoformat(args.as_of) if args.as_of
             else dt.date.today())
    state = AlertState.load(args.state)
    cp = default_provider()
    companies = cp.companies_as_of(today)
    tickers = ([c.ticker for c in companies] + [CONFIG.market_benchmark]
               + benchmark_tickers())

    print(f"DipDector daily · {today} · {len(companies)} companies")

    frame, err = _fetch(args.provider, args.fixture, tickers, today,
                        args.history_days)
    if frame is None:
        print(f"FAILED to fetch prices after 3 attempts: {err}")
        state.last_run = today.isoformat()
        state.last_run_status = f"fetch failed: {err}"
        state.save(args.state)
        return 1

    # --- freshness. A stale feed silently produces "nothing happened". ---
    last_bar = frame.close.index.max().date()
    staleness = (today - last_bar).days
    print(f"Latest bar: {last_bar} ({staleness} days old), source {frame.source}")
    if staleness > args.max_staleness and not frame.synthetic:
        print(f"ABORT: price data is {staleness} days old, beyond the "
              f"{args.max_staleness}-day limit. Refusing to report 'all quiet' "
              f"on data this old — that would be a false negative dressed as "
              f"good news.")
        state.last_run = today.isoformat()
        state.last_run_status = f"stale data ({staleness}d)"
        state.save(args.state)
        return 1

    as_of = last_bar
    truncated = frame.as_of(as_of)

    # --- score every industry ------------------------------------------
    assessments = []
    for industry, members in cp.industries(as_of).items():
        m = compute_industry_metrics(industry, members, truncated, as_of, CONFIG)
        if m is None:
            continue
        assessments.append(score_industry(m, CONFIG))
    assessments.sort(key=lambda a: -a.score)

    threshold = LEVEL_ORDER.index(AlertLevel[args.level])
    firing = [a for a in assessments if LEVEL_ORDER.index(a.level) >= threshold]

    for a in assessments:
        mark = "*" if a in firing else " "
        print(f"  {mark} {a.industry:<40} {a.score:5.1f}  {a.level.value}")

    # --- suppress what has already been sent ----------------------------
    to_send = []
    for a in firing:
        send, reason = state.should_notify(a.industry, a.level.value, a.score,
                                           as_of, args.cooldown)
        print(f"    {a.industry}: {'SEND' if send else 'skip'} — {reason}")
        if send:
            to_send.append({"assessment": a, "reason": reason})

    sender = get_sender(args.sender)
    state.last_run = today.isoformat()
    state.last_run_status = "ok"

    if not to_send:
        state.runs_since_last_alert += 1
        quiet = state.runs_since_last_alert
        print(f"\nNothing to send. {quiet} runs since the last alert.")
        if args.heartbeat_every and quiet % args.heartbeat_every == 0:
            msg = compose_heartbeat(as_of, quiet, state.summary(), len(companies))
            sender.send(args.to, msg)
            print(f"Heartbeat sent via {sender.name}.")
        state.prune(as_of)
        state.save(args.state)
        return 0

    # --- enrich only what is actually being sent ------------------------
    # Free by default. EODHD only if a key happens to be present.
    if os.environ.get("EODHD_API_KEY"):
        news = EODHDNewsProvider()
    elif args.news == "free":
        news = FreeNewsProvider()
    else:
        news = NullNewsProvider()
    print(f"  news source: {news.name}")

    store = EventStore(args.db)
    store.upsert_companies(companies)
    events_for_report = []

    for item in to_send:
        a = item["assessment"]
        arts = news.fetch(keywords_for(a.industry,
                                       [c.ticker for c in a.metrics.companies]),
                          dt.datetime.combine(as_of, dt.time(23, 59)), 10)
        event = classify_event(a.industry, a, arts)
        cands = score_candidates(a.metrics, truncated.close, CONFIG, event)
        store.record_event(a, truncated, event, cands, arts)
        events_for_report.append({
            "assessment": a, "event": event, "candidates": cands[:6],
            "coverage": CONFIG.recovery.coverage, "close": truncated.close,
            "market": CONFIG.market_benchmark, "reason": item["reason"],
        })
    store.close()

    report_html = render(events_for_report, {
        "as_of": as_of.isoformat(), "window": CONFIG.detection.primary_window,
        "params_version": CONFIG.params_version, "source": frame.source,
        "synthetic": frame.synthetic,
        "generated_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
    })
    if args.save_report:
        with open(args.save_report, "w") as f:
            f.write(report_html)
        print(f"Report written to {args.save_report}")

    message = compose(events_for_report, as_of, frame.synthetic, report_html)
    sender.send(args.to, message)
    print(f"\nSent {len(to_send)} alert(s) to {args.to} via {sender.name}.")

    if not args.dry_run:
        for item in to_send:
            a = item["assessment"]
            state.record(a.industry, a.level.value, a.score, as_of)
    else:
        print("Dry run — state not updated, so this will send again next time.")

    state.prune(as_of)
    state.save(args.state)
    return 10


def main():
    p = argparse.ArgumentParser(description="DipDector daily check")
    p.add_argument("--to", default=os.environ.get("ALERT_EMAIL"),
                   help="recipient; or set ALERT_EMAIL")
    p.add_argument("--provider", default="yfinance",
                   choices=["yfinance", "eodhd", "fixture"])
    p.add_argument("--fixture", default="dipdector/fixtures/synthetic_semis.csv")
    p.add_argument("--sender", default="auto",
                   choices=["auto", "smtp", "resend", "console"])
    p.add_argument("--level", default="INVESTIGATE",
                   choices=["WATCH", "INVESTIGATE", "MAJOR_EVENT"])
    p.add_argument("--state", default="state/alert_state.json")
    p.add_argument("--db", default="state/dipdector.duckdb")
    p.add_argument("--save-report", default=None)
    p.add_argument("--cooldown", type=int, default=45,
                   help="days an industry stays suppressed after an alert")
    p.add_argument("--heartbeat-every", type=int, default=20,
                   help="send an 'all quiet' note every N quiet runs; 0 to disable")
    p.add_argument("--max-staleness", type=int, default=5,
                   help="abort if the newest bar is older than this many days")
    p.add_argument("--news", default="free", choices=["free", "none"],
                   help="'free' uses Yahoo (no key). EODHD_API_KEY overrides both.")
    p.add_argument("--history-days", type=int, default=700)
    p.add_argument("--as-of", default=None, help="override today, for testing")
    p.add_argument("--dry-run", action="store_true",
                   help="send nothing permanent and do not update state")
    args = p.parse_args()

    if not args.to:
        sys.exit("No recipient. Pass --to or set ALERT_EMAIL.")
    if args.dry_run and args.sender == "auto":
        args.sender = "console"

    try:
        sys.exit(run(args))
    except Exception:                        # noqa: BLE001
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
