"""
Event store.

DEVLOG s.23 / s.32 — schema. DEVLOG s.43 — "every alert should be reproducible
from the data available when it was generated." So each event row records the
params_version, the data source, and the full component breakdown as JSON. If a
threshold changes later, old events still explain themselves under the rules
that were actually in force when they fired.

Recovery/outcome columns are written NULL at detection time and filled in later
by a separate outcome job. They are never populated in the same pass that
detected the event — that would be hindsight (s.29).
"""

from __future__ import annotations

import datetime as dt
import json
import uuid
from dataclasses import asdict
from typing import List, Optional

import duckdb

SCHEMA = """
CREATE TABLE IF NOT EXISTS companies (
    ticker            VARCHAR PRIMARY KEY,
    name              VARCHAR,
    exchange          VARCHAR,
    sector            VARCHAR,
    industry_group    VARCHAR,
    industry          VARCHAR,
    sub_industry      VARCHAR,
    index_membership  VARCHAR
);

CREATE TABLE IF NOT EXISTS industry_events (
    event_id              VARCHAR PRIMARY KEY,
    industry              VARCHAR,
    sector                VARCHAR,
    sub_industry          VARCHAR,
    as_of                 DATE,
    detection_time        TIMESTAMP,
    window_days           INTEGER,
    alert_level           VARCHAR,
    shock_score           DOUBLE,
    score_components      JSON,
    trigger_checks        JSON,
    median_return         DOUBLE,
    market_return         DOUBLE,
    relative_return       DOUBLE,
    n_members             INTEGER,
    n_declining           INTEGER,
    abnormality_z         DOUBLE,
    mean_correlation      DOUBLE,
    -- AI event assessment (nullable: detection can fire without it)
    cause                 VARCHAR,
    causal_chain          VARCHAR,
    severity              INTEGER,
    temporary_confidence  INTEGER,
    structural_risk       INTEGER,
    continuation_risk     INTEGER,
    event_reasoning       VARCHAR,
    event_is_stub         BOOLEAN,
    -- outcome, filled in later, never at detection time
    maximum_drop          DOUBLE,
    recovery_date         DATE,
    recovery_duration     INTEGER,
    -- provenance
    params_version        VARCHAR,
    data_source           VARCHAR,
    data_is_synthetic     BOOLEAN
);

CREATE TABLE IF NOT EXISTS event_companies (
    event_id          VARCHAR,
    ticker            VARCHAR,
    window_return     DOUBLE,
    relative_return   DOUBLE,
    volume_z          DOUBLE,
    dist_52w_high     DOUBLE,
    recovery_score    DOUBLE,
    recovery_factors  JSON,
    recovery_date     DATE,
    recovery_pct      DOUBLE,
    PRIMARY KEY (event_id, ticker)
);

CREATE TABLE IF NOT EXISTS news_articles (
    article_id     VARCHAR PRIMARY KEY,
    event_id       VARCHAR,
    source         VARCHAR,
    source_tier    INTEGER,
    published_at   TIMESTAMP,
    headline       VARCHAR,
    url            VARCHAR,
    used_as_evidence BOOLEAN
);
"""


class EventStore:
    def __init__(self, path: str = "dipdector.duckdb"):
        self.con = duckdb.connect(path)
        self.con.execute(SCHEMA)

    def upsert_companies(self, companies) -> None:
        for c in companies:
            self.con.execute(
                "INSERT OR REPLACE INTO companies VALUES (?,?,?,?,?,?,?,?)",
                [c.ticker, c.name, c.exchange, c.sector, c.industry_group,
                 c.industry, c.sub_industry, ",".join(c.index_membership)],
            )

    def record_event(self, assessment, frame, event=None,
                     candidates=None, articles=None) -> str:
        m = assessment.metrics
        event_id = str(uuid.uuid4())[:8]

        self.con.execute(
            """INSERT INTO industry_events VALUES
               (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            [event_id, m.industry, None, m.industry, m.as_of,
             dt.datetime.now(dt.timezone.utc), m.window,
             assessment.level.value, assessment.score,
             json.dumps([{**asdict(c), "contribution": c.contribution}
                         for c in assessment.components]),
             json.dumps([asdict(t) for t in assessment.triggers]),
             m.median_return, m.market_return, m.relative_to_market,
             m.n_members, m.n_declining, m.abnormality_z,
             None if m.mean_pairwise_correlation != m.mean_pairwise_correlation
                  else m.mean_pairwise_correlation,
             # A stub assessment means the analysis did not run. Storing its
             # placeholder "other" would read as a finding in the events table.
             ", ".join(event.causes) if (event and not event.is_stub) else None,
             event.causal_chain if (event and not event.is_stub) else None,
             event.severity if event else None,
             event.temporary_confidence if event else None,
             event.structural_risk if event else None,
             event.continuation_risk if event else None,
             event.reasoning if event else None,
             event.is_stub if event else None,
             None, None, None,
             assessment.params_version, frame.source, frame.synthetic],
        )

        for cm in m.companies:
            cand = next((c for c in (candidates or []) if c.ticker == cm.ticker), None)
            self.con.execute(
                "INSERT OR REPLACE INTO event_companies VALUES (?,?,?,?,?,?,?,?,?,?)",
                [event_id, cm.ticker, cm.returns.get(m.window), cm.rel_to_market,
                 cm.volume_z, cm.dist_from_52w_high,
                 cand.score if cand else None,
                 json.dumps([asdict(f) for f in cand.factors]) if cand else None,
                 None, None],
            )

        for i, a in enumerate(articles or [], 1):
            self.con.execute(
                "INSERT OR REPLACE INTO news_articles VALUES (?,?,?,?,?,?,?,?)",
                [f"{event_id}-{i}", event_id, a.source, a.source_tier,
                 a.published_at, a.headline, a.url,
                 bool(event and i in event.evidence_article_ids)],
            )

        return event_id

    def events(self):
        return self.con.execute(
            "SELECT event_id, as_of, industry, alert_level, shock_score, "
            "median_return, cause, data_is_synthetic FROM industry_events "
            "ORDER BY as_of DESC, shock_score DESC"
        ).fetchdf()

    def close(self):
        self.con.close()
