"""
Universe and classification guards.

The seed table could only ever surface a shock in one of six industries
somebody picked in advance; anything else in the index was invisible and
looked exactly like a quiet day. These pin the properties that stop that
happening again, and the ones that stop the constituent list being silently
corrupted.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

from dipdector.config import CONFIG
from dipdector.data import sp500
from dipdector.data.universe import (SP500ClassificationProvider,
                                     SeedClassificationProvider,
                                     default_provider)

TODAY = dt.date.today()


def test_constituent_cache_is_the_whole_index():
    df = sp500.load()
    assert len(df) > 450, f"only {len(df)} constituents cached"
    assert df["ticker"].is_unique
    assert not df["ticker"].isna().any()
    assert not df["sub_industry"].isna().any()


def test_class_share_tickers_use_the_yahoo_spelling():
    """BRK.B on Wikipedia is BRK-B at Yahoo. Getting this wrong drops names."""
    df = sp500.load()
    assert not df["ticker"].str.contains(r"\.", regex=True).any()


def test_provider_covers_far_more_than_the_seed_table():
    seed = SeedClassificationProvider()
    full = SP500ClassificationProvider()
    assert len(full.companies_as_of(TODAY)) > 10 * len(seed.companies_as_of(TODAY))


def test_grouping_is_by_sub_industry_not_sector():
    """Sector is too coarse — chip fabs and payroll software are both 'IT'."""
    full = SP500ClassificationProvider()
    groups = full.industries(TODAY)
    assert "Semiconductors" in groups
    assert "Information Technology" not in groups
    assert len(groups) > 100


def test_scoreable_groups_are_a_meaningful_slice_of_the_index():
    full = SP500ClassificationProvider()
    mn = CONFIG.detection.min_industry_members
    scoreable = {k: v for k, v in full.industries(TODAY).items() if len(v) >= mn}
    assert len(scoreable) > 25, f"only {len(scoreable)} scoreable groups"
    covered = sum(len(v) for v in scoreable.values())
    assert covered > 200, f"only {covered} companies inside scoreable groups"


def test_middle_gics_tiers_are_left_empty_not_invented():
    """Wikipedia does not publish them. Guessing would be fabrication."""
    c = SP500ClassificationProvider().companies_as_of(TODAY)[0]
    assert c.sector and c.sub_industry
    assert c.industry == "" and c.industry_group == ""


def test_default_provider_prefers_the_full_index():
    assert default_provider().name == "sp500-gics-current"


def test_refusal_to_cache_a_truncated_list(monkeypatch):
    """A partial parse must fail loudly, not quietly shrink the universe."""
    monkeypatch.setattr(sp500, "fetch_constituents",
                        lambda: (_ for _ in ()).throw(
                            RuntimeError("Only 12 constituents parsed")))
    with pytest.raises(RuntimeError, match="constituents parsed"):
        sp500.refresh("/tmp/should-not-be-written.csv")


def test_missing_columns_are_reported_not_ignored(monkeypatch):
    bad = "<table><tr><th>Symbol</th></tr><tr><td>AAPL</td></tr></table>"

    class R:
        text = bad
        def raise_for_status(self): pass

    monkeypatch.setattr("requests.get", lambda *a, **k: R())
    with pytest.raises(RuntimeError, match="column layout has changed"):
        sp500.fetch_constituents()
