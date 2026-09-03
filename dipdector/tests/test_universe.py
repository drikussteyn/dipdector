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
import pathlib

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


def test_grouping_is_by_industry_not_sector():
    """Sector is too coarse — chip fabs and payroll software are both tech."""
    full = SP500ClassificationProvider()
    groups = full.industries(TODAY)
    assert "Semiconductors" in groups
    assert "Technology" not in groups
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
    assert default_provider().name == "sp500-current"


def test_refusal_to_cache_a_truncated_list(monkeypatch):
    """A partial parse must fail loudly, not quietly shrink the universe."""
    monkeypatch.setattr(sp500, "fetch_holdings",
                        lambda: (_ for _ in ()).throw(
                            RuntimeError("Only 12 holdings parsed")))
    with pytest.raises(RuntimeError, match="holdings parsed"):
        sp500.refresh("/tmp/should-not-be-written.csv")


def test_non_spreadsheet_response_is_refused(monkeypatch):
    """The holdings host serves an HTML wall when it dislikes a client."""
    class R:
        content = b"<!DOCTYPE html><html>bot wall</html>"
        def raise_for_status(self): pass

    monkeypatch.setattr("requests.get", lambda *a, **k: R())
    with pytest.raises(RuntimeError, match="not a spreadsheet"):
        sp500.fetch_holdings()


def test_classification_shortfall_refuses_to_write(monkeypatch):
    """If classification silently loses half the index, do not persist it."""
    import pandas as pd
    monkeypatch.setattr(sp500, "fetch_holdings",
                        lambda: pd.DataFrame({"ticker": [f"T{i}" for i in range(500)],
                                              "name": ["x"] * 500}))
    monkeypatch.setattr(sp500, "classify",
                        lambda tk, **kw: pd.DataFrame(
                            {"ticker": tk[:10], "sector": ["S"] * 10,
                             "sub_industry": ["I"] * 10}))
    with pytest.raises(RuntimeError, match="survived classification"):
        sp500.refresh("/tmp/should-not-be-written.csv")


def test_universe_comes_from_primary_sources_only():
    """
    No crowd-edited data in the universe. Constituents come from the fund's
    own holdings file and classification from the price provider.
    """
    src = (pathlib.Path(sp500.__file__)).read_text().lower()
    assert "en.wikipedia.org" not in src
    assert "ssga.com" in src
