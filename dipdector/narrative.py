"""
Plain-English summaries.

The top of the report has to answer three questions in ordinary language before
any number appears: what happened, how unusual is it, and what should I do next.

Everything here is generated deterministically from the metrics. No AI. That
matters for the same reason as devlog s.30 — the headline sentence is a
restatement of a measurement, so it must not be able to drift from it. The AI's
judgement appears separately, clearly labelled, and never in the headline.

Devlog s.11 supplies the actions: monitor, investigate, full analysis. None of
them are ever "buy".
"""

from __future__ import annotations

from .engine.detection import AlertLevel


def _count_phrase(n: int, total: int) -> str:
    if n == total:
        return f"all {total}"
    return f"{n} of {total}"


def headline(assessment) -> str:
    """One sentence. The single most important measured fact."""
    m = assessment.metrics
    direction = "fell" if m.median_return < 0 else "rose"
    return (f"{m.industry} {direction} "
            f"{abs(m.median_return):.1%} in {m.window} trading days")


def subhead(assessment) -> str:
    """Two clauses: what the market did, and how broad the move was."""
    m = assessment.metrics
    mkt = (f"the S&P 500 fell {abs(m.market_return):.1%}"
           if m.market_return < 0 else
           f"the S&P 500 rose {m.market_return:.1%}")
    breadth = _count_phrase(m.n_declining, m.n_members)
    return (f"Over the same window {mkt}. "
            f"{breadth.capitalize()} companies in the group declined together.")


def unusualness(assessment) -> str:
    """How rare is this, in words rather than sigma."""
    z = assessment.metrics.abnormality_z
    if z >= 4:
        return "This is far outside anything this group has done in the past year."
    if z >= 3:
        return "A move this size is rare for this group — roughly a once-a-year event."
    if z >= 2:
        return "This is a larger move than this group usually makes."
    if z >= 1.5:
        return "Slightly larger than this group's normal range of movement."
    return "This is within the range this group normally moves in."


def suggestion(assessment) -> tuple[str, str]:
    """
    Devlog s.11 — the action, and why. Returns (action, rationale).

    The action is always about investigation. There is no action here that says
    to buy anything, and there should never be one.
    """
    m = assessment.metrics
    level = assessment.level

    if level == AlertLevel.MAJOR_EVENT:
        return (
            "Worth a full look",
            "Several independent signals agree: the size of the fall, how many "
            "companies are involved, how far they've fallen behind the wider "
            "market, and how unusual this is for them. That combination is what "
            "this tool is built to find. It is not a reason to buy anything — "
            "it's a reason to find out what happened.",
        )
    if level == AlertLevel.INVESTIGATE:
        return (
            "Find out what caused this",
            "The group is falling together and falling behind the market, which "
            "points to a shared cause rather than coincidence. The next step is "
            "to identify the cause and judge whether it looks temporary.",
        )
    if level == AlertLevel.WATCH:
        if not assessment.triggered:
            missing = assessment.failed_triggers[0].label.lower()
            return (
                "Keep an eye on it",
                f"Something unusual has started, but it doesn't yet meet the bar "
                f"for a real event — {missing} hasn't been met. Held at watch "
                f"deliberately. A big number on its own isn't an event.",
            )
        return (
            "Keep an eye on it",
            "An unusual move has begun but hasn't reached the strength this tool "
            "requires before it's worth investigating.",
        )
    return (
        "Nothing to do",
        "This group is moving within its normal range.",
    )


def key_figures(assessment) -> list[dict]:
    """The three numbers that carry the most information. Everything else hides."""
    m = assessment.metrics
    return [
        {"value": f"{m.median_return:+.1%}",
         "label": f"typical fall over {m.window} days",
         "note": "The middle company in the group. Half fell more, half fell less."},
        {"value": f"{m.n_declining}/{m.n_members}",
         "label": "companies falling",
         "note": "How widespread the decline is. One or two weak names would not "
                 "count as an industry event."},
        {"value": f"{m.relative_to_market:+.1%}",
         "label": "behind the S&P 500",
         "note": "The gap between this group and the wider market. This is what "
                 "separates an industry problem from a bad week for everything."},
    ]


def candidate_summary(candidate, industry_median: float, window: int) -> str:
    """One plain sentence per recovery candidate, above the detailed factors."""
    factors = sorted(candidate.factors, key=lambda f: -f.contribution)
    top = factors[0].name.replace("_", " ") if factors else "limited data"
    readable = {
        "event exposure": "how much of the industry's fall it took on",
        "historical resilience": "how it behaved after past falls of this size",
        "relative stability": "how steady it usually is compared to its peers",
        "liquidity": "how heavily it trades",
        "drawdown depth": "how far it sits below its high",
    }.get(top, top)
    return f"Scored mainly on {readable}."
