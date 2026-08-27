"""
News retrieval guards.

These exist because the news path had no coverage at all, and the gap was not
theoretical: `as_of` reached the providers naive while every feed returned
aware timestamps, so the comparison that enforces the s.44.5 cutoff raised
TypeError. Articles are only fetched for industries that actually fire, so the
job ran green on every quiet day and would have died on the first real alert —
the one run where it mattered.

The cutoff itself is the other thing under test here. It is what stops a
backtest reading an article written after the date it is replaying.
"""

from __future__ import annotations

import datetime as dt

import pytest

from ..news.engine import Article, to_utc
from ..news.free import FreeNewsProvider


def _article(headline: str, published_at: dt.datetime) -> Article:
    return Article(headline=headline, source="reuters",
                   published_at=published_at, url="https://example.test/x")


def _provider(articles):
    """A FreeNewsProvider with both feeds stubbed and no polite delay."""
    p = FreeNewsProvider(polite_delay=0.0)
    p._yfinance_news = lambda ticker: list(articles)
    p._rss = lambda kw: []
    return p


# --- to_utc ---------------------------------------------------------------

def test_to_utc_reads_naive_as_utc():
    naive = dt.datetime(2023, 3, 14, 12, 0)
    assert to_utc(naive) == dt.datetime(2023, 3, 14, 12, 0, tzinfo=dt.timezone.utc)


def test_to_utc_converts_other_offsets():
    plus_two = dt.timezone(dt.timedelta(hours=2))
    aware = dt.datetime(2023, 3, 14, 14, 0, tzinfo=plus_two)
    assert to_utc(aware) == dt.datetime(2023, 3, 14, 12, 0, tzinfo=dt.timezone.utc)


def test_to_utc_is_idempotent():
    utc = dt.datetime(2023, 3, 14, 12, 0, tzinfo=dt.timezone.utc)
    assert to_utc(to_utc(utc)) == utc


# --- the bug that took the daily job down ---------------------------------

def test_naive_as_of_against_aware_articles_does_not_raise():
    """The exact production failure: naive cutoff, aware feed timestamps."""
    published = dt.datetime(2023, 3, 13, 9, 0, tzinfo=dt.timezone.utc)
    provider = _provider([_article("SVB fails", published)])

    got = provider.fetch(["KRE"], dt.datetime(2023, 3, 14, 23, 59), 10)

    assert [a.headline for a in got] == ["SVB fails"]


def test_mixed_awareness_in_one_batch_sorts_cleanly():
    """A feed returning both kinds must not blow up the final sort."""
    provider = _provider([
        _article("aware",  dt.datetime(2023, 3, 13, 9, 0, tzinfo=dt.timezone.utc)),
        _article("naive",  dt.datetime(2023, 3, 12, 9, 0)),
    ])

    got = provider.fetch(["KRE"], dt.datetime(2023, 3, 14, 23, 59), 10)

    assert [a.headline for a in got] == ["naive", "aware"]
    assert all(a.published_at.tzinfo is not None for a in got)


# --- s.44.5: no lookahead -------------------------------------------------

def test_article_published_after_as_of_is_excluded():
    """Load-bearing. Without this a backtest reads tomorrow's news."""
    provider = _provider([
        _article("before", dt.datetime(2023, 3, 13, 9, 0, tzinfo=dt.timezone.utc)),
        _article("after",  dt.datetime(2023, 3, 15, 9, 0, tzinfo=dt.timezone.utc)),
    ])

    got = provider.fetch(["KRE"],
                         dt.datetime(2023, 3, 14, 23, 59, tzinfo=dt.timezone.utc), 10)

    assert [a.headline for a in got] == ["before"]


def test_cutoff_holds_when_as_of_is_naive():
    """A naive cutoff must not silently widen the window."""
    provider = _provider([
        _article("after", dt.datetime(2023, 3, 15, 9, 0, tzinfo=dt.timezone.utc)),
    ])

    assert provider.fetch(["KRE"], dt.datetime(2023, 3, 14, 23, 59), 10) == []


def test_article_older_than_lookback_is_excluded():
    provider = _provider([
        _article("stale", dt.datetime(2023, 2, 1, 9, 0, tzinfo=dt.timezone.utc)),
    ])

    assert provider.fetch(["KRE"], dt.datetime(2023, 3, 14, 23, 59), 10) == []


def test_duplicate_headlines_are_collapsed():
    published = dt.datetime(2023, 3, 13, 9, 0, tzinfo=dt.timezone.utc)
    provider = _provider([
        _article("SVB fails", published),
        _article("  svb FAILS  ", published),
    ])

    assert len(provider.fetch(["KRE"], dt.datetime(2023, 3, 14, 23, 59), 10)) == 1
