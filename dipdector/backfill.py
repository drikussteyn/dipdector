"""
Backfill the archive from history.

  python -m dipdector.backfill --provider yfinance --start 2016-01-01

Runs the same detector over past dates and publishes a page for every event it
would have alerted on. The point is to give the archive a past: a site whose
first entry appears three months after you deploy it tells you nothing about
whether the thing works, whereas an archive that already contains the March
2023 regional-bank collapse and the February 2020 crash is inspectable today.

Two honest limits, both printed on every run:

  - No cause analysis. Free news feeds do not serve articles from 2016, and
    calling a model without them would invent the explanation rather than
    retrieve it. Backfilled pages say "cause not determined", which is what
    actually happened.

  - Survivorship bias. The universe uses current index membership for every
    date, so these are the events among companies that still exist. s.29
    forbids reading this as a backtest result.

Detection itself is honest: every assessment comes from a frame truncated at
its own date, so no page contains a number that was not knowable that day.
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys

from .analysis.recovery import score_candidates
from .config import CONFIG, load_env
from .data.benchmarks import all_tickers as benchmark_tickers
from .data.providers import get_provider
from .data.universe import default_provider
from .engine.detection import AlertLevel
from .news.engine import classify_event
from .publish import note_quiet_run, publish
from .backtest.scan import scan_history


def main() -> int:
    load_env()
    p = argparse.ArgumentParser(description="Backfill the DipDector archive")
    p.add_argument("--provider", default="yfinance",
                   choices=["yfinance", "eodhd", "fixture"])
    p.add_argument("--fixture", default="dipdector/fixtures/synthetic_history.csv")
    p.add_argument("--start", required=True)
    p.add_argument("--end", default=dt.date.today().isoformat())
    p.add_argument("--level", default="INVESTIGATE",
                   choices=["WATCH", "INVESTIGATE", "MAJOR_EVENT"])
    p.add_argument("--step", type=int, default=1,
                   help="scan every Nth trading day; 1 is exact, 5 is ~5x faster")
    p.add_argument("--cooldown", type=int, default=60)
    p.add_argument("--publish-dir", default="docs")
    p.add_argument("--site-url", default=None)
    a = p.parse_args()

    start = dt.date.fromisoformat(a.start)
    end = dt.date.fromisoformat(a.end)

    cp = default_provider()
    companies = cp.companies_as_of(end)
    tickers = ([c.ticker for c in companies] + [CONFIG.market_benchmark]
               + benchmark_tickers())

    print(f"DipDector backfill · {start} → {end} · {len(companies)} companies")
    print("⚠ Survivorship bias: the universe uses current index membership for "
          "all dates.\n  These are the events among companies that still "
          "exist. Not a backtest result.")

    kwargs = {"path": a.fixture} if a.provider == "fixture" else {}
    # One fetch for the whole period; scan_history truncates per day.
    frame = get_provider(a.provider, **kwargs).fetch(
        tickers, start - dt.timedelta(days=400), end)

    events = scan_history(frame, cp, start, end,
                          min_level=AlertLevel[a.level],
                          cooldown_days=a.cooldown, step=a.step)
    print(f"\n{len(events)} event(s) detected.")
    if not events:
        return 0

    published = 0
    for e in events:
        truncated = frame.as_of(e.detected_on)
        m = e.assessment.metrics
        # No articles: free feeds do not reach back this far, so the page says
        # the cause was not determined rather than guessing at one.
        ev = classify_event(e.industry, e.assessment, [])
        item = {
            "assessment": e.assessment, "event": ev,
            "candidates": score_candidates(m, truncated.close, CONFIG, ev)[:6],
            "coverage": CONFIG.recovery.coverage, "close": truncated.close,
            "market": CONFIG.market_benchmark,
            "reason": "backfilled from history",
        }
        run_meta = {
            "as_of": e.detected_on.isoformat(),
            "window": CONFIG.detection.primary_window,
            "params_version": CONFIG.params_version,
            "source": f"{frame.source} (backfill)",
            "synthetic": frame.synthetic,
            "generated_at": dt.datetime.now(dt.timezone.utc)
                              .strftime("%Y-%m-%d %H:%M UTC"),
        }
        for r in publish([item], run_meta, a.publish_dir, a.site_url,
                         len(companies)):
            print(f"  {r.as_of}  {r.industry:<34} {r.score:5.1f}  {r.path}")
            published += 1

    # publish() stamps last_run from each event's own date, so after a
    # chronological backfill the index would claim the last scan was the date
    # of the newest event rather than today. Correct it once at the end.
    note_quiet_run(a.publish_dir, end.isoformat(), CONFIG.params_version,
                   len(companies))

    print(f"\nPublished {published} page(s) to {a.publish_dir}/.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
