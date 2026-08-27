"""
News investigation and event classification.

This is the ONLY module allowed to use AI (devlog s.30). It receives articles,
never prices, and it never computes a market metric. The shock score, the
returns, the breadth — all of that is already fixed by the time we get here.

DEVLOG s.13 — cluster articles into one underlying event, don't list dozens.
DEVLOG s.14 — classify the cause, allow multiple causes.
DEVLOG s.15 — temporary vs structural, as two separate scores.
DEVLOG s.16 — event severity.
DEVLOG s.44.2 — never invent news. The model is given articles and told to work
only from them; if the evidence does not support a conclusion it must say so and
return low confidence rather than fill the gap.
DEVLOG s.36 — during backtesting, only articles published on or before the
as_of timestamp may be passed in. Enforced in fetch, not left to the model.
"""

from __future__ import annotations

import datetime as dt
import json
import os
from dataclasses import dataclass, field, asdict
from typing import List, Optional, Protocol

# Claude Sonnet 5 is both newer and cheaper than the Sonnet 4.6 this was
# written against ($2/$10 vs $3/$15 per 1M tokens). Override with
# DIPDECTOR_MODEL to move to claude-opus-5 without touching code.
MODEL = os.environ.get("DIPDECTOR_MODEL", "claude-sonnet-5")

CAUSE_TAXONOMY = [
    "geopolitical conflict", "oil/fuel shock", "recession/economic slowdown",
    "interest-rate shock", "inflation", "semiconductor shortage",
    "supply-chain disruption", "trade war/tariffs", "regulatory action",
    "banking/liquidity crisis", "commodity collapse", "housing slowdown",
    "pandemic/public-health event", "transportation disruption",
    "natural disaster", "consumer-demand shock", "technology disruption",
    "industry-specific operational disruption", "other",
]

SOURCE_TIERS = {
    1: "primary government or company announcement",
    2: "major financial news organization",
    3: "established financial publication",
    4: "industry publication",
    5: "other",
}


@dataclass
class Article:
    headline: str
    source: str
    published_at: dt.datetime
    url: str
    summary: str = ""
    source_tier: int = 5

    def to_prompt_line(self, idx: int) -> str:
        return (f"[{idx}] {self.published_at:%Y-%m-%d %H:%M} | {self.source} "
                f"(tier {self.source_tier}) | {self.headline}"
                + (f"\n     {self.summary}" if self.summary else ""))


@dataclass
class EventAssessment:
    """DEVLOG s.13/14/15/16. Every field is a model judgement, not a measurement."""

    title: str
    causes: List[str]
    causal_chain: str
    temporary_confidence: int      # 0-100
    structural_risk: int           # 0-100
    severity: int                  # 0-100
    continuation_risk: int         # 0-100
    reasoning: str
    evidence_article_ids: List[int]
    unresolved_questions: List[str] = field(default_factory=list)
    model: str = MODEL
    n_articles_considered: int = 0
    generated_at: Optional[dt.datetime] = None
    is_stub: bool = False

    def to_dict(self):
        d = asdict(self)
        d["generated_at"] = self.generated_at.isoformat() if self.generated_at else None
        return d


def to_utc(ts: dt.datetime) -> dt.datetime:
    """
    Coerce a datetime to timezone-aware UTC.

    The three feeds disagree about tzinfo: yfinance returns aware timestamps,
    Yahoo's RSS returns RFC-822 strings that may carry an offset or not, and
    EODHD returns ISO strings that sometimes end in Z. The as-of cutoff below
    is load-bearing for s.44.5, and a naive/aware comparison raises TypeError
    at exactly the moment an alert fires — quiet days never reach this code.
    Naive input is read as UTC, which is what every caller here means by it.
    """
    if ts.tzinfo is None:
        return ts.replace(tzinfo=dt.timezone.utc)
    return ts.astimezone(dt.timezone.utc)


class NewsProvider(Protocol):
    name: str

    def fetch(self, keywords: List[str], as_of: dt.datetime,
              lookback_days: int) -> List[Article]:
        ...


class NullNewsProvider:
    """
    Offline placeholder. Returns nothing rather than fabricating headlines.

    Devlog s.44.2 is absolute: the app must never invent news. An empty result
    is the honest output when no feed is connected.
    """

    name = "null"

    def fetch(self, keywords, as_of, lookback_days=7) -> List[Article]:
        return []


class EODHDNewsProvider:
    """Live feed. Included in EODHD's All-In-One subscription."""

    name = "eodhd-news"

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.environ.get("EODHD_API_KEY")
        if not self.api_key:
            raise RuntimeError("EODHD_API_KEY is not set.")

    def fetch(self, keywords: List[str], as_of: dt.datetime,
              lookback_days: int = 7) -> List[Article]:
        import requests

        as_of = to_utc(as_of)
        since = as_of - dt.timedelta(days=lookback_days)
        seen, out = set(), []
        for kw in keywords:
            r = requests.get(
                "https://eodhd.com/api/news",
                params={"api_token": self.api_key, "fmt": "json", "t": kw,
                        "from": since.date().isoformat(),
                        "to": as_of.date().isoformat(), "limit": 50},
                timeout=30,
            )
            r.raise_for_status()
            for item in r.json():
                pub = to_utc(dt.datetime.fromisoformat(
                    item["date"].replace("Z", "+00:00")))
                # DEVLOG s.36 / s.44.5 — hard cutoff, enforced here so the
                # backtester can never see an article before it was published.
                if pub > as_of:
                    continue
                key = item.get("link") or item.get("title")
                if key in seen:
                    continue
                seen.add(key)
                out.append(Article(
                    headline=item.get("title", ""),
                    source=item.get("source", "unknown"),
                    published_at=pub,
                    url=item.get("link", ""),
                    summary=(item.get("content") or "")[:400],
                ))
        return sorted(out, key=lambda a: a.published_at)


SYSTEM_PROMPT = """You are the event-classification component of DipDector, a market research tool.

You have been given a set of news articles and a set of ALREADY-COMPUTED market
statistics. Your job is to explain WHY an industry declined, and to judge whether
the cause looks temporary or structural.

Hard rules:
- Work only from the articles provided. Do not use outside knowledge of what
  happened next, and do not introduce facts that are not in the articles.
- Never recompute or dispute the market statistics. They are measurements; you
  are interpreting them, not checking them.
- If the articles do not explain the decline, say so plainly and return a low
  temporary_confidence AND a low severity, with the gap listed in
  unresolved_questions. A confident-sounding guess is worse than an admission.
- A sharp decline is not evidence that an event is temporary. Do not reason
  backwards from the price move to a benign cause.
- Distinguish confirmed information from unverified reports, and weight primary
  sources (tier 1-2) above the rest.

Return ONLY a JSON object, no markdown fences, no preamble, with keys:
  title                  short neutral description of the event
  causes                 array, one or more from the supplied taxonomy
  causal_chain           the mechanism, as "A -> B -> C"
  temporary_confidence   0-100, how confident that effects fade within ~12 months
  structural_risk        0-100, risk of permanent impairment to industry economics
  severity               0-100, expected magnitude of the earnings/business impact
  continuation_risk      0-100, risk the decline continues from here
  reasoning              3-6 sentences citing article numbers as [n]
  evidence_article_ids   array of the article numbers you actually relied on
  unresolved_questions   array of what you could not determine from the evidence

temporary_confidence and structural_risk are separate judgements, not two ends
of one scale. An event can be both clearly temporary and severely damaging."""


def _build_user_prompt(industry, assessment, articles) -> str:
    m = assessment.metrics
    stats = (
        f"Industry: {industry}\n"
        f"Window: {m.window} trading days ending {m.as_of}\n"
        f"Median return: {m.median_return:+.1%}\n"
        f"S&P 500 over same window: {m.market_return:+.1%}\n"
        f"Relative: {m.relative_to_market:+.1%}\n"
        f"Breadth: {m.n_declining} of {m.n_members} members declining\n"
        f"Abnormality: {m.abnormality_z:.1f} sigma vs the industry's own history\n"
        f"Mean pairwise correlation: {m.mean_pairwise_correlation:.2f}\n"
        f"Median volume z-score: {m.median_volume_z:+.1f}\n"
        f"Industry Shock Score: {assessment.score:.0f}/100 ({assessment.level.value})\n"
        f"Worst member: {m.companies[0].ticker} {m.companies[0].returns[m.window]:+.1%}\n"
        f"Best member: {m.companies[-1].ticker} {m.companies[-1].returns[m.window]:+.1%}"
    )
    if articles:
        arts = "\n".join(a.to_prompt_line(i) for i, a in enumerate(articles, 1))
    else:
        arts = "(no articles retrieved)"
    return (f"MARKET STATISTICS (computed, do not recompute):\n{stats}\n\n"
            f"CAUSE TAXONOMY: {', '.join(CAUSE_TAXONOMY)}\n\n"
            f"ARTICLES ({len(articles)}):\n{arts}")


def _stub(reason: str, n_articles: int) -> EventAssessment:
    """
    An explicit "we did not analyse this" result.

    The zeroes are not findings and the wording says so. s.41 forbids a number
    whose provenance the reader cannot recover, and a silent zero here would
    read as "no structural risk" rather than "no analysis".
    """
    return EventAssessment(
        title="Cause not determined",
        causes=["other"], causal_chain="unknown",
        temporary_confidence=0, structural_risk=0,
        severity=0, continuation_risk=0,
        reasoning=(f"{reason} The decline was detected and measured, but no "
                   f"cause analysis was performed. Do not read the zeroes as "
                   f"findings — they mean the analysis did not run."),
        evidence_article_ids=[], unresolved_questions=["Everything."],
        n_articles_considered=n_articles,
        generated_at=dt.datetime.now(dt.timezone.utc), is_stub=True,
    )


def classify_event(industry: str, assessment, articles: List[Article],
                   api_key: Optional[str] = None) -> EventAssessment:
    """Call Claude to cluster and classify. Falls back to an explicit stub."""

    key = api_key or os.environ.get("ANTHROPIC_API_KEY")
    if not key or not articles:
        reason = ("No ANTHROPIC_API_KEY configured." if not key
                  else "No articles retrieved for this industry and window.")
        return _stub(reason, len(articles))

    import requests

    # Everything from here to the parse is best-effort. The alert does not
    # depend on it: the numbers are already computed by engine/metrics.py, and
    # s.44 keeps judgement separable from measurement. A rate limit, an outage
    # or a reply that isn't the JSON we asked for must degrade to "cause not
    # determined" — it must not take down a run that has something to report.
    try:
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": key, "anthropic-version": "2023-06-01",
                     "content-type": "application/json"},
            json={"model": MODEL, "max_tokens": 2000, "system": SYSTEM_PROMPT,
                  "messages": [{"role": "user",
                                "content": _build_user_prompt(
                                    industry, assessment, articles)}]},
            timeout=90,
        )
        r.raise_for_status()
        text = "".join(b.get("text", "") for b in r.json()["content"]
                       if b["type"] == "text")
        payload = json.loads(
            text.replace("```json", "").replace("```", "").strip())
    except Exception as exc:                 # noqa: BLE001 — degrade, never die
        return _stub(f"Cause analysis did not complete "
                     f"({type(exc).__name__}: {exc}).", len(articles))

    try:
        return _assessment_from(payload, len(articles))
    except Exception as exc:                 # noqa: BLE001
        return _stub(f"Cause analysis returned an unreadable payload "
                     f"({type(exc).__name__}: {exc}).", len(articles))


def _assessment_from(payload: dict, n_articles: int) -> EventAssessment:
    """Map the model's JSON onto the dataclass. Raises if the shape is wrong."""
    return EventAssessment(
        title=payload["title"],
        causes=payload.get("causes", []),
        causal_chain=payload.get("causal_chain", ""),
        temporary_confidence=int(payload.get("temporary_confidence", 0)),
        structural_risk=int(payload.get("structural_risk", 0)),
        severity=int(payload.get("severity", 0)),
        continuation_risk=int(payload.get("continuation_risk", 0)),
        reasoning=payload.get("reasoning", ""),
        evidence_article_ids=payload.get("evidence_article_ids", []),
        unresolved_questions=payload.get("unresolved_questions", []),
        n_articles_considered=n_articles,
        generated_at=dt.datetime.now(dt.timezone.utc),
    )


def keywords_for(industry: str, companies: List[str]) -> List[str]:
    """DEVLOG s.12 — industry terms, macro terms, and company tickers."""
    base = {
        "Semiconductors": ["semiconductor", "chip export controls", "chip demand"],
        "Semiconductor Materials & Equipment": ["semiconductor equipment",
                                                "wafer fab equipment", "export controls"],
    }
    return base.get(industry, [industry.lower()]) + companies[:6]
