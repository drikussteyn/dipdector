"""
Tests for the daily job's failure handling.

This path had no coverage, and it is the one that broke in production. The
scan scored 70 industries correctly, then died constructing an SMTP sender
whose credentials were never added to the repository — and because that
happened before any publishing, a day on which two industries scored
MAJOR_EVENT produced no report page, no stored event and no state write. The
detector worked and left no trace that it had.

So what is asserted here is the separation: the scan's output is the product,
delivery is one channel for it, and a broken channel must cost the email and
nothing else. The run still ends red — silence is this tool's normal output,
so silence cannot also be how a broken mailer looks — but it ends red with
its evidence on disk.
"""

from __future__ import annotations

import datetime as dt
import json
import os

import pytest

from dipdector.daily import (SEARCH_HINDSIGHT_DAYS, build_parser,
                             is_live_alert, run)
from dipdector.publish import load_index

FIXTURE = "dipdector/fixtures/synthetic_semis.csv"
SHOCK_DATE = "2026-06-30"       # two industries score MAJOR_EVENT
QUIET_DATE = "2025-06-30"       # nothing clears WATCH

# Every credential get_sender consults. Absent all of them, --sender smtp
# cannot construct, which is exactly the production failure.
MAIL_ENV = ["SMTP_USERNAME", "SMTP_USER", "SMTP_PASSWORD", "SMTP_HOST",
            "SMTP_SENDER", "SMTP_FROM", "RESEND_API_KEY", "EMAIL_PROVIDER"]


@pytest.fixture
def offline(monkeypatch):
    """No credentials, no API key — so nothing here reaches the network."""
    for name in MAIL_ENV + ["ANTHROPIC_API_KEY", "EODHD_API_KEY"]:
        monkeypatch.delenv(name, raising=False)


def _args(tmp_path, as_of, sender):
    return build_parser().parse_args([
        "--provider", "fixture", "--fixture", FIXTURE,
        "--as-of", as_of, "--to", "owner@example.com",
        "--sender", sender, "--news", "none",
        "--state", str(tmp_path / "state.json"),
        "--db", str(tmp_path / "events.duckdb"),
        "--publish-dir", str(tmp_path / "site"),
    ])


def _state(tmp_path):
    with open(tmp_path / "state.json") as f:
        return json.load(f)


# --- the failure that shipped ------------------------------------------

def test_alert_survives_a_broken_mailer(offline, tmp_path):
    """
    The report must exist even when the email cannot be sent.

    This is the whole point. An alert that cannot be delivered is still an
    alert worth keeping: its page has a permanent URL, so the finding is
    recoverable from the archive once the mail is fixed.
    """
    code = run(_args(tmp_path, SHOCK_DATE, "smtp"))

    assert code == 1, "an undeliverable alert must end the run red"

    pages = sorted(p.name for p in (tmp_path / "site" / "events").iterdir())
    assert pages == [
        "2026-06-30-semiconductor-equipment-and-materials.html",
        "2026-06-30-semiconductors.html",
    ], "the report pages must be written before delivery is attempted"


def test_undelivered_alert_is_not_marked_sent(offline, tmp_path):
    """
    Suppression exists to stop one event mailing five times. An email that
    never left must not consume that budget, or the fix arrives and the alert
    it was meant to deliver stays suppressed for the whole cooldown.
    """
    run(_args(tmp_path, SHOCK_DATE, "smtp"))
    state = _state(tmp_path)

    assert state["industries"] == {}, \
        "nothing was delivered, so nothing may be recorded as delivered"
    assert state["last_run"] == SHOCK_DATE, "the run itself still happened"
    assert "delivery" in state["last_run_status"]


def test_quiet_day_still_records_the_scan(offline, tmp_path):
    """
    A stale archive must mean a quiet market, never a dead job. That promise
    is only kept if the last-scan date is written on days the mailer is down
    — which are the days it is most likely to be read.
    """
    code = run(_args(tmp_path, QUIET_DATE, "smtp"))

    assert code == 1, "an unusable mail channel is still a fault worth failing on"
    assert load_index(str(tmp_path / "site"))["last_run"] == QUIET_DATE
    assert _state(tmp_path)["last_run"] == QUIET_DATE


# --- the healthy paths, so the fix cannot quietly swallow a real send ---

def test_quiet_day_with_a_working_channel_passes(offline, tmp_path):
    assert run(_args(tmp_path, QUIET_DATE, "console")) == 0


def test_delivered_alert_reports_and_records(offline, tmp_path):
    code = run(_args(tmp_path, SHOCK_DATE, "console"))

    assert code == 10, "10 is how the workflow tells 'alerts sent' from a failure"
    industries = _state(tmp_path)["industries"]
    assert set(industries) == {"Semiconductors",
                               "Semiconductor Equipment & Materials"}
    assert all(v["last_level"] == "MAJOR_EVENT" for v in industries.values())


# --- the hindsight guard -----------------------------------------------
#
# This decides whether the cause analysis may search the web, and it is the
# line that keeps the backtester honest: a replay that reads articles written
# after the crash is measuring hindsight, not detection. It used to be an
# inline comparison inside a loop that only executes on a day something fires,
# so it could only be checked by getting lucky with the market.

def test_todays_alert_may_search():
    today = dt.date(2026, 9, 4)
    assert is_live_alert(today, today) is True


def test_alert_from_within_the_window_may_search():
    assert is_live_alert(dt.date(2026, 8, 29), dt.date(2026, 9, 4)) is True


def test_replay_of_an_old_event_may_not_search():
    """The 2026-07-07 event is 59 days old — the case that must stay closed."""
    assert is_live_alert(dt.date(2026, 7, 7), dt.date(2026, 9, 4)) is False


def test_the_window_edge_is_where_it_is_documented_to_be():
    at_edge = dt.date(2026, 9, 4) - dt.timedelta(days=SEARCH_HINDSIGHT_DAYS)
    assert is_live_alert(at_edge, dt.date(2026, 9, 4)) is True
    assert is_live_alert(at_edge - dt.timedelta(days=1),
                         dt.date(2026, 9, 4)) is False
