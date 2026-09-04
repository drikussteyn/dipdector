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
from .config import CONFIG, load_env
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
from .publish import note_quiet_run, publish
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


def _resolve_sender(kind: str):
    """
    Returns (sender, None) or (None, why).

    `get_sender` raises on a misconfiguration, which is right for a caller
    about to send something. This caller is not one: it runs before the scan
    knows whether there is anything to send, and a mail problem must never be
    allowed to destroy a scan result that is already correct.
    """
    try:
        return get_sender(kind), None
    except Exception as exc:            # noqa: BLE001 — reported, not raised
        return None, str(exc)


def _undeliverable(why: str, *, alerting: bool) -> int:
    """
    End the run red, after the scan's own work is safely on disk.

    Silence is this tool's normal output, so silence cannot also be how a
    broken mail channel looks — nobody goes looking for an alert they were
    never told to expect. When the channel is down the only remaining way to
    say so is to fail the run, which is what makes the scheduler complain.
    """
    print(f"\nDELIVERY FAILED: {why}")
    if alerting:
        print("An alert fired today and could NOT be delivered. Its report "
              "page and stored event were written before this failure, so "
              "the evidence is in the archive; only the email is missing. "
              "The alert is deliberately not marked as sent, so the next run "
              "will try again.")
    else:
        print("Nothing fired today, so nothing was missed — but had it, this "
              "run could not have told you. That is why a quiet day still "
              "fails here rather than passing quietly.")
    print("The scan itself succeeded, and everything it produced reached "
          "disk before this failure.")
    print("Fix the mail credentials. Under GitHub Actions they come from the "
          "repository secrets listed in .github/workflows/daily.yml "
          "(Settings -> Secrets and variables -> Actions).")
    return 1


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

    # Resolved here, early, so a broken mail channel is reported on the first
    # quiet run rather than discovered on the one day in six months an alert
    # fires. Resolving is not the same as aborting, though, and this used to
    # abort: a missing SMTP password threw on this line and took the entire
    # scan down with it — on a day two industries scored MAJOR_EVENT, no
    # report page, no stored event and no state write survived, because the
    # mailer could not be built. The scan's output is the product. Delivery
    # is one channel for it, and a channel failing must not erase the goods.
    sender, sender_error = _resolve_sender(args.sender)
    state.last_run = today.isoformat()
    state.last_run_status = ("ok" if sender is not None
                             else "scan ok, delivery unconfigured")
    if sender_error:
        print(f"\n  mail channel unavailable: {sender_error}")

    if not to_send:
        state.runs_since_last_alert += 1
        quiet = state.runs_since_last_alert
        print(f"\nNothing to send. {quiet} runs since the last alert.")
        if args.publish_dir:
            # So a stale-looking archive means a quiet market and not a dead
            # job. Without this the newest entry could be months old with no
            # way to tell the difference.
            note_quiet_run(args.publish_dir, as_of.isoformat(),
                           CONFIG.params_version, len(companies))
        if args.heartbeat_every and quiet % args.heartbeat_every == 0:
            if sender is None:
                print("Heartbeat due, but there is no mail channel to send it "
                      "on — which is the thing a heartbeat exists to prove.")
            else:
                msg = compose_heartbeat(as_of, quiet, state.summary(),
                                        len(companies))
                sender.send(args.to, msg)
                print(f"Heartbeat sent via {sender.name}.")
        state.prune(as_of)
        state.save(args.state)
        if sender is None:
            return _undeliverable(sender_error, alerting=False)
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
        cutoff = dt.datetime.combine(as_of, dt.time(23, 59),
                                     tzinfo=dt.timezone.utc)
        arts = news.fetch(
            keywords_for(a.industry,
                         [c.ticker for c in a.metrics.companies]),
            cutoff, 10)
        # Let the model research the cause itself, but only when this is a
        # live alert. Searching the web about an old decline returns pieces
        # written after it — including ones that say how it resolved — and a
        # replay that reads those is measuring hindsight, not detection.
        live = (dt.date.today() - as_of).days <= 7
        event = classify_event(a.industry, a, arts, allow_search=live)
        if live:
            print(f"    {a.industry}: cause analysis with web search")
        else:
            print(f"    {a.industry}: replay of {as_of} — search disabled "
                  f"to avoid hindsight")
        cands = score_candidates(a.metrics, truncated.close, CONFIG, event)
        store.record_event(a, truncated, event, cands, arts)
        events_for_report.append({
            "assessment": a, "event": event, "candidates": cands[:6],
            "coverage": CONFIG.recovery.coverage, "close": truncated.close,
            "market": CONFIG.market_benchmark, "reason": item["reason"],
        })
    store.close()

    run_meta = {
        "as_of": as_of.isoformat(), "window": CONFIG.detection.primary_window,
        "params_version": CONFIG.params_version, "source": frame.source,
        "synthetic": frame.synthetic,
        "generated_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
    }
    report_html = render(events_for_report, run_meta)
    if args.save_report:
        with open(args.save_report, "w") as f:
            f.write(report_html)
        print(f"Report written to {args.save_report}")

    # --- publish one permanent page per event, then link to it ----------
    links = {}
    if args.publish_dir:
        site_url = args.site_url or os.environ.get("SITE_BASE_URL")
        reports = publish(events_for_report, run_meta, args.publish_dir,
                          site_url, len(companies))
        links = {r.industry: r.link for r in reports}
        for r in reports:
            print(f"  published {r.path}")
        if not site_url:
            print("  note: SITE_BASE_URL unset — the email will carry "
                  "relative paths, not clickable links.")

    message = compose(events_for_report, as_of, frame.synthetic, report_html,
                      links)
    if sender is None:
        # Everything above is already durable — the event is in the store and
        # the report page is on disk with its permanent URL. Bail out without
        # recording the alert as sent, so tomorrow's run retries it.
        state.prune(as_of)
        state.save(args.state)
        return _undeliverable(sender_error, alerting=True)
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


def build_parser() -> argparse.ArgumentParser:
    """
    Separate from main() so tests drive run() through the real defaults
    instead of a hand-built namespace that silently drifts from them.
    """
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
    p.add_argument("--publish-dir", default="docs",
                   help="write the hosted report pages here; '' disables")
    p.add_argument("--site-url", default=None,
                   help="public base URL of the published site; or set "
                        "SITE_BASE_URL. Without it the email has no links.")
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
    return p


def main():
    load_env()
    args = build_parser().parse_args()

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
