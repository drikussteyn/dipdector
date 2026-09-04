"""
Prove the cause-analysis path works, without waiting for a crash.

    python -m dipdector.selftest

The problem this solves: cause analysis only runs on a day an industry
actually fires, which is about twice a year. Everything around it is
exercised daily and fails loudly, but the two model calls sit idle for
months — so an expired API key, a blocked egress route, a renamed tool
parameter or a model that stopped accepting a schema would all be
discovered on the one day the tool was built for, in the one hour it
matters, by an email that says "cause not determined".

Both 400s that broke this in development were exactly that shape: invisible
to the tests, invisible to a quiet run, fatal on an alerting day.

So this drives the real `classify_event` — the same function `daily.py`
calls, with search on — against a fixed synthetic shock, and reports which
stage answered. It writes nothing, publishes nothing and stores nothing:
the verdict is thrown away, only the plumbing is under test. That is also
why searching here is not a hindsight problem. Nothing it concludes is ever
recorded, so there is no measurement to contaminate.

Exit codes: 0 both stages answered, 1 something is broken.
"""

from __future__ import annotations

import datetime as dt
import sys

from .config import CONFIG, load_env
from .data.providers import FixtureProvider
from .data.universe import SeedClassificationProvider
from .engine.detection import score_industry
from .engine.metrics import compute_industry_metrics
from .news.engine import MODEL, SEARCH_MODEL, classify_event

FIXTURE = "dipdector/fixtures/synthetic_semis.csv"
AS_OF = dt.date(2026, 6, 30)
INDUSTRY = "Semiconductors"


def main() -> int:
    load_env()
    print(f"Cause-analysis self-test\n  search pass: {SEARCH_MODEL}"
          f"\n  judgement  : {MODEL}\n")

    cp = SeedClassificationProvider()
    frame = FixtureProvider(path=FIXTURE).fetch(
        [c.ticker for c in cp.companies_as_of(AS_OF)] +
        [CONFIG.market_benchmark], AS_OF - dt.timedelta(days=700), AS_OF)
    truncated = frame.as_of(AS_OF)
    metrics = compute_industry_metrics(INDUSTRY, cp.industries(AS_OF)[INDUSTRY],
                                       truncated, AS_OF, CONFIG)
    assessment = score_industry(metrics, CONFIG)
    print(f"  synthetic shock: {INDUSTRY} {metrics.median_return:+.1%}, "
          f"score {assessment.score:.0f}\n")

    # allow_search=True on purpose: the search pass is the half most likely to
    # break, being the one with a server tool and a model that has to support
    # it. No article is supplied, so the model must go and find something —
    # which is what a live alert asks of it.
    event = classify_event(INDUSTRY, assessment, [], allow_search=True)

    if event.is_stub:
        print("FAILED — cause analysis degraded to the stub.")
        print(f"  {event.reasoning}")
        print("\nThis is what a real alert would have delivered instead of an "
              "explanation. Fix it now rather than on the day it fires.")
        return 1

    print(f"OK — both stages answered.")
    print(f"  searched     : {event.searched} ({len(event.sources)} sources)")
    print(f"  title        : {event.title[:120]}")
    print(f"  causes       : {', '.join(event.causes)}")
    print(f"  scores       : temporary {event.temporary_confidence} / "
          f"structural {event.structural_risk} / severity {event.severity} / "
          f"continuation {event.continuation_risk}")
    if not event.sources:
        # Not fatal — the judgement still came back, and an alert with only
        # the supplied feed behind it is a real (if weaker) outcome. But it
        # means the search half contributed nothing, and on a live alert that
        # is the half doing the work.
        print("\nWARNING: the judgement answered but the search pass returned "
              "no sources. Cause analysis would rest on the free feed alone.")
    print("\nNothing was stored, published or emailed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
