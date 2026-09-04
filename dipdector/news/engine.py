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
import re
from dataclasses import dataclass, field, asdict, replace
from typing import List, Optional, Protocol

# TWO MODELS, because retrieval and judgement are different jobs.
#
# One call used to do both, and it was measured doing it: six searches, 166k
# input tokens, 10k output, 138 seconds. Nearly all of that input is search
# results being re-read on every iteration — bulk text that needs skimming,
# not a frontier model's attention. Meanwhile the part that actually matters,
# deciding what the evidence supports, was getting whatever budget survived
# the reading.
#
# So SEARCH_MODEL does the looking and writes down what it found; MODEL reads
# that digest and makes the call. The point is NOT saving money — this fires
# about twice a year and cost either way is pennies. The point is that
# judging a 3k digest with Opus costs about two cents, while judging the raw
# 166k of search results with it would cost the better part of a dollar and
# be slower. Shrinking what reaches the judge is what makes the good judge
# affordable.
#
# `or` rather than a get() default: an unset GitHub Actions variable is
# passed through as an empty string, not as an absent key.
MODEL = os.environ.get("DIPDECTOR_MODEL") or "claude-opus-5"
SEARCH_MODEL = os.environ.get("DIPDECTOR_SEARCH_MODEL") or "claude-haiku-4-5"

SEARCH_INSTRUCTION = """
Search is available to you, and you should use it. The supplied articles come
from one free feed, they are thin, and they skew towards low-tier aggregators.

Run two or three searches aimed at the cause of this specific decline — the
industry name with words like "stocks fall", "selloff", "why", the date, and
the names of the largest companies involved. Prefer wire services, national
papers and trade press over aggregators and commentary.

These are US-listed S&P 500 companies. Searching an industry name alone will
surface other markets — a query about railroads returns Indian rail stocks,
which have nothing to do with these — so anchor every search on the tickers
or company names given to you, and disregard results about other exchanges.
Ignore "best stocks to buy" listicles entirely; they are never evidence about
a specific day's move.

If your searches contradict the supplied articles, say so in the reasoning and
weight the better-sourced account. If they turn up nothing that explains the
decline, that is a real finding: report it as unexplained rather than
stretching a weak story to fit. An industry can fall hard for reasons that are
not yet public, and saying so is more useful than a confident guess.
"""

CAUSE_TAXONOMY = [
    "geopolitical conflict", "oil/fuel shock", "recession/economic slowdown",
    "interest-rate shock", "inflation", "semiconductor shortage",
    "supply-chain disruption", "trade war/tariffs", "regulatory action",
    "banking/liquidity crisis", "commodity collapse", "housing slowdown",
    "pandemic/public-health event", "transportation disruption",
    "natural disaster", "consumer-demand shock", "technology disruption",
    "industry-specific operational disruption", "other",
]

RESEARCH_PROMPT = """
You are the research half of a market-anomaly tool. An industry has fallen
hard and someone needs to know why. Your job is to find out what was being
reported at the time and write it down. It is NOT to score the event, rate its
severity, or decide whether the fall is temporary — a second pass does that,
and it needs evidence from you rather than conclusions.

Write plain prose, at most 600 words:

  - what you found, most load-bearing first, each claim attributed to who
    reported it ("Reuters reported...", "the company's own filing said...")
  - dates, so the reader can see whether a story actually precedes the fall
  - where accounts disagree, both accounts
  - what you looked for and could NOT find

Never state as fact anything no source said. If the searches turn up nothing
that explains the decline, say exactly that — an industry can fall for reasons
that are not yet public, and "no confirmed catalyst" is a real and useful
finding. Do not reach for a plausible-sounding story to fill the gap.
"""

# Constrains stage two's reply. A model that reasons well but wraps its answer
# in prose, or emits almost-JSON, used to degrade a genuine alert to "cause
# not determined"; a schema makes that unrepresentable rather than unlikely.
JUDGE_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "causes": {"type": "array",
                   "items": {"type": "string", "enum": CAUSE_TAXONOMY}},
        "causal_chain": {"type": "string"},
        "temporary_confidence": {"type": "integer"},
        "structural_risk": {"type": "integer"},
        "severity": {"type": "integer"},
        "continuation_risk": {"type": "integer"},
        "reasoning": {"type": "string"},
        "evidence_article_ids": {"type": "array", "items": {"type": "integer"}},
        "unresolved_questions": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["title", "causes", "causal_chain", "temporary_confidence",
                 "structural_risk", "severity", "continuation_risk",
                 "reasoning", "evidence_article_ids", "unresolved_questions"],
    "additionalProperties": False,
}
# The schema subset structured outputs accepts has no minimum/maximum for
# integers, so the 0-100 range is asked for in the prompt and enforced here.
# A score outside the range would be rendered as-is on the report otherwise.

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
    searched: bool = False
    sources: List[dict] = field(default_factory=list)   # {title, url}

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
- Work only from the evidence in front of you: the articles supplied, plus
  anything you retrieve if search is available to you. Do not introduce facts
  from memory, and do not use knowledge of what happened after this date.
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


def stub(reason: str, n_articles: int = 0) -> EventAssessment:
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


def _strip_citation_tags(text: str) -> str:
    """
    Remove the <cite index="..."> markup search returns inside prose.

    The citation itself is worth keeping — the sentence it wraps is the
    claim — but the tag is machine syntax and would reach the reader as
    literal angle brackets in the report.
    """
    if not text:
        return text
    return re.sub(r"</?cite[^>]*>", "", text)


def _research_payload(industry, assessment, articles) -> dict:
    """
    Stage one: go and find out what happened. Search is a server tool, so
    there is no client-side loop.

    This model is explicitly NOT asked to judge. It reports what it found and
    who said it; scoring the event is stage two's job, on a stronger model.
    Keeping the roles apart is also what stops a cheap model's paraphrase
    from quietly becoming the finding.
    """
    return {
        "model": SEARCH_MODEL, "max_tokens": 8000,
        "system": RESEARCH_PROMPT + SEARCH_INSTRUCTION,
        "messages": [{"role": "user",
                      "content": _build_user_prompt(industry, assessment,
                                                    articles)}],
        # allowed_callers=["direct"] is required, not decoration. This search
        # tool does its dynamic filtering by calling itself from inside code
        # execution, and Haiku does not support programmatic tool calling —
        # without this the request is rejected outright. Direct calling is
        # all the research pass needs: it wants the results, not a filtering
        # pipeline it would then have to reason about.
        "tools": [{"type": "web_search_20260209", "name": "web_search",
                   "max_uses": 6, "allowed_callers": ["direct"]}],
    }


def _judge_payload(industry, assessment, articles, digest: str) -> dict:
    """
    Stage two: decide what the evidence supports, and return it as JSON that
    is guaranteed to parse.

    `output_config.format` constrains the response to JUDGE_SCHEMA, which
    removes a whole failure mode — a reply that was perfectly sensible prose
    but not JSON used to degrade a real alert to "cause not determined".
    It also has no search tool: structured output and search citations do not
    coexist, and by this point the searching is already done.
    """
    prompt = _build_user_prompt(industry, assessment, articles)
    if digest:
        prompt += (f"\n\nRESEARCH FINDINGS (retrieved by a search pass; treat "
                   f"as reported evidence, not as established fact):\n{digest}")
    return {
        "model": MODEL, "max_tokens": 8000, "system": SYSTEM_PROMPT,
        "messages": [{"role": "user", "content": prompt}],
        "output_config": {"format": {"type": "json_schema",
                                     "schema": JUDGE_SCHEMA}},
    }


def _post(key: str, body: dict, timeout: int) -> dict:
    import requests      # kept local, as it always was, so importing the
                         # package stays cheap for callers that never classify
    r = requests.post("https://api.anthropic.com/v1/messages",
                      headers={"x-api-key": key,
                               "anthropic-version": "2023-06-01",
                               "content-type": "application/json"},
                      json=body, timeout=timeout)
    if r.status_code >= 300:
        # raise_for_status() gives "400 Client Error: Bad Request" and nothing
        # else. That string ends up in the report as the reason there is no
        # cause analysis, where it tells the reader nothing and tells whoever
        # has to fix it even less — the API says exactly what was wrong with
        # the request, so carry it.
        raise RuntimeError(f"{r.status_code} from {body.get('model')}: "
                           f"{r.text[:400]}")
    return r.json()


def _text_of(body: dict) -> str:
    return "".join(b.get("text", "") for b in body.get("content", [])
                   if b.get("type") == "text")


def _research(industry, assessment, articles, key: str):
    """
    Returns (digest, sources). Failure is survivable: stage two can still
    judge the supplied articles, and says that is all it had.
    """
    body = _post(key, _research_payload(industry, assessment, articles), 240)
    digest = _strip_citation_tags(_text_of(body)).strip()
    if not digest:
        raise RuntimeError(
            f"the search pass returned no findings "
            f"(stop_reason={body.get('stop_reason')})")
    return digest, _collect_sources(body.get("content", []))


def _collect_sources(content: List[dict]) -> List[dict]:
    """Titles and URLs of whatever the model actually retrieved."""
    out, seen = [], set()
    for block in content:
        if block.get("type") != "web_search_tool_result":
            continue
        results = block.get("content")
        # An error comes back as an object, a success as a list. Server tools
        # do not raise — they return HTTP 200 with an error payload — so this
        # has to branch on the shape rather than trust it.
        if not isinstance(results, list):
            continue
        for r in results:
            url = r.get("url")
            if url and url not in seen:
                seen.add(url)
                out.append({"title": (r.get("title") or url)[:200], "url": url})
    return out


def classify_event(industry: str, assessment, articles: List[Article],
                   api_key: Optional[str] = None,
                   allow_search: bool = False) -> EventAssessment:
    """
    Call Claude to cluster and classify. Falls back to an explicit stub.

    `allow_search` lets the model research the decline itself rather than
    depending on one thin free feed. It MUST stay off for historical replays:
    searching the web about a 2020 crash returns pieces written afterwards,
    including ones that say how it resolved, and a backtest that reads those
    is measuring hindsight. It is safe for a live alert because there is no
    "afterwards" yet.
    """

    key = api_key or os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        return stub("No ANTHROPIC_API_KEY configured.", len(articles))
    if not articles and not allow_search:
        # With search available an empty feed is not a dead end — the model
        # can go and find the cause itself, which is the whole point of
        # giving it the tool. Without search there is nothing to reason from.
        return stub("No articles retrieved for this industry and window, and "
                    "web search was not available for this run.", 0)

    # STAGE ONE — go and look. Survivable on its own: if the search pass
    # fails, stage two still judges the supplied articles and the report says
    # that is all it had. Losing the research is worse than losing nothing,
    # but it is not worth losing the alert over.
    digest, sources, research_note = "", [], ""
    if allow_search:
        try:
            digest, sources = _research(industry, assessment, articles, key)
        except Exception as exc:             # noqa: BLE001 — degrade, never die
            research_note = (f" The search pass failed "
                             f"({type(exc).__name__}: {exc}), so this rests "
                             f"only on the supplied articles.")
            if not articles:
                return stub("Web search was available but the search pass "
                            f"failed ({type(exc).__name__}: {exc}), and the "
                            f"news feed returned nothing to fall back on.", 0)

    # STAGE TWO — decide. Best-effort, like everything here: the numbers are
    # already computed by engine/metrics.py and s.44 keeps judgement separable
    # from measurement, so a rate limit or an outage must degrade to "cause
    # not determined" rather than take down a run that has something to say.
    try:
        body = _post(key, _judge_payload(industry, assessment, articles,
                                         digest), 120)
        text = _text_of(body)
        if not text.strip():
            # Worth naming separately from malformed JSON: the model reasoned
            # up to the ceiling without ever answering.
            raise RuntimeError(
                f"the model returned reasoning but no answer "
                f"(stop_reason={body.get('stop_reason')}); max_tokens may be "
                f"too low")
        payload = json.loads(
            text.replace("```json", "").replace("```", "").strip())
    except Exception as exc:                 # noqa: BLE001 — degrade, never die
        return stub(f"Cause analysis did not complete "
                     f"({type(exc).__name__}: {exc}).", len(articles))

    try:
        ev = _assessment_from(payload, len(articles))
        if research_note:
            ev = replace(ev, reasoning=ev.reasoning + research_note)
        return replace(ev, searched=bool(sources), sources=sources)
    except Exception as exc:                 # noqa: BLE001
        return stub(f"Cause analysis returned an unreadable payload "
                     f"({type(exc).__name__}: {exc}).", len(articles))


def _score(value) -> int:
    """Clamp a model-supplied 0-100 score, since the schema cannot."""
    try:
        return max(0, min(100, int(value)))
    except (TypeError, ValueError):
        return 0


def _assessment_from(payload: dict, n_articles: int) -> EventAssessment:
    """Map the model's JSON onto the dataclass. Raises if the shape is wrong."""
    return EventAssessment(
        title=_strip_citation_tags(payload["title"]),
        causes=payload.get("causes", []),
        causal_chain=_strip_citation_tags(payload.get("causal_chain", "")),
        temporary_confidence=_score(payload.get("temporary_confidence")),
        structural_risk=_score(payload.get("structural_risk")),
        severity=_score(payload.get("severity")),
        continuation_risk=_score(payload.get("continuation_risk")),
        reasoning=_strip_citation_tags(payload.get("reasoning", "")),
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
