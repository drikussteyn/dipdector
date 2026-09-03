"""
Tests for the published site.

The email is now a link rather than an attachment, which makes the published
page load-bearing: if publishing silently produces nothing, the alert still
sends and still looks fine, but every link in it is dead. These check that the
page exists, that its URL is stable, and that the archive is rebuildable from
the repository alone.
"""

from __future__ import annotations

import datetime as dt
import json
import os

import pytest

from dipdector.analysis.recovery import score_candidates
from dipdector.config import CONFIG
from dipdector.data.benchmarks import all_tickers
from dipdector.data.providers import FixtureProvider
from dipdector.data.universe import SeedClassificationProvider
# These exercise the engine against the synthetic fixtures, which contain
# prices for the six-industry seed universe only. They therefore pin the seed
# provider explicitly rather than taking whatever default_provider() returns —
# that is now the full S&P 500, whose constituents the fixtures have no prices
# for. Testing the engine, not the universe.
from dipdector.engine.detection import score_industry
from dipdector.engine.metrics import compute_industry_metrics
from dipdector.news.engine import classify_event
from dipdector.publish import (event_path, load_index, note_quiet_run,
                               publish, rebuild_index, slugify)

FIXTURE = "dipdector/fixtures/synthetic_semis.csv"
SHOCK_DATE = dt.date(2026, 6, 30)


@pytest.fixture(scope="module")
def event():
    """One real event dict, shaped exactly as daily.py builds it."""
    cp = SeedClassificationProvider()
    tickers = ([c.ticker for c in cp.companies_as_of(SHOCK_DATE)]
               + [CONFIG.market_benchmark] + all_tickers())
    frame = FixtureProvider(FIXTURE).fetch(
        tickers, dt.date(2024, 1, 1), SHOCK_DATE).as_of(SHOCK_DATE)
    members = cp.industry_members("Semiconductors", SHOCK_DATE)
    m = compute_industry_metrics("Semiconductors", members, frame,
                                 SHOCK_DATE, CONFIG)
    a = score_industry(m, CONFIG)
    # No articles -> the explicit stub, so this never reaches the network.
    ev = classify_event("Semiconductors", a, [])
    return {"assessment": a, "event": ev,
            "candidates": score_candidates(m, frame.close, CONFIG, ev)[:6],
            "coverage": CONFIG.recovery.coverage, "close": frame.close,
            "market": CONFIG.market_benchmark, "reason": "first alert"}


@pytest.fixture
def run_meta():
    return {"as_of": SHOCK_DATE.isoformat(),
            "window": CONFIG.detection.primary_window,
            "params_version": CONFIG.params_version,
            "source": "fixture", "synthetic": True,
            "generated_at": "2026-06-30 22:00 UTC"}


# --- URLs must not move ---------------------------------------------------

def test_slug_is_url_safe_and_stable():
    assert slugify("Oil & Gas Equipment & Services") == \
        "oil-and-gas-equipment-and-services"
    assert slugify("Hotels, Resorts & Cruise Lines") == \
        "hotels-resorts-and-cruise-lines"
    assert slugify("Regional Banks") == "regional-banks"


def test_event_path_is_dated_and_deterministic():
    p1 = event_path("2023-03-14", "Regional Banks")
    p2 = event_path("2023-03-14", "Regional Banks")
    assert p1 == p2 == "events/2023-03-14-regional-banks.html"


# --- publishing -----------------------------------------------------------

def test_publish_writes_page_index_and_record(tmp_path, event, run_meta):
    root = str(tmp_path / "site")
    got = publish([event], run_meta, root, "https://example.test/dipdector", 35)

    assert len(got) == 1
    r = got[0]
    assert os.path.exists(os.path.join(root, r.path))
    assert os.path.exists(os.path.join(root, "index.html"))
    assert os.path.exists(os.path.join(root, ".nojekyll"))
    assert r.url == f"https://example.test/dipdector/{r.path}"
    assert r.link == r.url

    page = open(os.path.join(root, r.path)).read()
    assert "Semiconductors" in page
    assert "DipDector" in page


def test_link_falls_back_to_relative_without_base_url(tmp_path, event, run_meta):
    root = str(tmp_path / "site")
    got = publish([event], run_meta, root, None, 35)
    assert got[0].url is None
    assert got[0].link == got[0].path


def test_republishing_the_same_event_does_not_duplicate(tmp_path, event, run_meta):
    root = str(tmp_path / "site")
    publish([event], run_meta, root, None, 35)
    publish([event], run_meta, root, None, 35)

    record = load_index(root)
    assert len(record["events"]) == 1


def test_index_lists_the_event_and_escapes_ampersands(tmp_path, event, run_meta):
    root = str(tmp_path / "site")
    publish([event], run_meta, root, None, 35)

    record = load_index(root)
    record["events"].append({
        "as_of": "2023-03-14", "industry": "Oil & Gas Equipment & Services",
        "level": "INVESTIGATE", "score": 65.0, "median_return": -0.107,
        "headline": "x", "path": "events/2023-03-14-oil-and-gas.html"})
    from dipdector.publish import save_index
    save_index(root, record)
    html = rebuild_index(root)

    assert "Oil &amp; Gas Equipment &amp; Services" in html
    assert "Oil & Gas Equipment & Services" not in html
    assert "Semiconductors" in html


# --- the archive must survive without a database --------------------------

def test_index_rebuilds_from_json_alone(tmp_path, event, run_meta):
    root = str(tmp_path / "site")
    publish([event], run_meta, root, None, 35)
    os.remove(os.path.join(root, "index.html"))

    rebuild_index(root)

    assert os.path.exists(os.path.join(root, "index.html"))
    assert "Semiconductors" in open(os.path.join(root, "index.html")).read()


def test_load_index_on_empty_root_does_not_raise(tmp_path):
    record = load_index(str(tmp_path / "nothing-here"))
    assert record["events"] == []


def test_quiet_run_updates_last_scan_without_adding_events(tmp_path, event,
                                                           run_meta):
    root = str(tmp_path / "site")
    publish([event], run_meta, root, None, 35)

    note_quiet_run(root, "2026-07-15", CONFIG.params_version, 35)

    record = load_index(root)
    assert record["last_run"] == "2026-07-15"
    assert len(record["events"]) == 1
    assert "2026-07-15" in open(os.path.join(root, "index.html")).read()
