# DipDector

Watches for whole industries crashing together, works out why, and emails you a
link to the report.

It is **not** a trading system. It places no orders, and nothing it produces
recommends buying anything. It answers one question — *did an entire industry
just fall together, and is that unusual?* — and hands you the evidence.

---

## What you get

**A daily scan.** Every weekday after the US close, GitHub runs the detector
over the whole S&P 500 — 503 companies, grouped into their GICS sub-industries.
Most days nothing fires and you hear nothing.

**An email when something does.** Short and plain: what fell, how far, how
unusual, and a link.

**A permanent report page per event.** The link opens the full report — the
measurements, the seven score components and what each contributed, all five
trigger conditions with pass/fail, two charts, and whatever the cause analysis
could establish from the news. Every page stays at its URL forever, so an
email from two years ago still opens the report as it was written that day.

**An archive.** [`docs/index.html`](docs/index.html) lists every event ever
detected, newest first.

The archive ships already populated with **41 real events from 2016–2026**,
backfilled from live market data — including the March 2023 regional banking
collapse (score 100/100), the February–March 2020 crash across eleven separate
industries, and the August 2024 unwind.

---

## Setting it up

You need a GitHub account and a Gmail account. Budget fifteen minutes.

### 1. Push this to a new GitHub repository

```bash
git remote add origin https://github.com/<you>/dipdector.git
git push -u origin main
```

A **public** repo is free and gives you free Pages hosting. The published
pages contain market analysis of public companies and nothing about you — your
email address exists only in the email envelope, never on a page. If you would
rather they were private, GitHub Pro ($4/mo) serves Pages from a private repo
and nothing in the code changes.

### 2. Turn on Pages

Repository **Settings → Pages**. Set **Source** to *Deploy from a branch*,
branch **main**, folder **/docs**. Save.

A minute later your archive is live at
`https://<you>.github.io/dipdector/`. The workflow works out this URL by
itself, so there is nothing to configure.

### 3. Create a Resend account

Resend is a transactional email service — the kind that sends order
confirmations and one-time codes. It means alerts arrive from the tool's own
identity rather than your personal address, and setup is a single API key: no
app password, no SMTP ports, no two-factor dance.

Sign up at [resend.com](https://resend.com) **using the same address you want
alerts sent to**, then create an API key.

That last part matters. Without verifying a domain of your own you send from
`onboarding@resend.dev`, which will only deliver to the address registered on
the Resend account. For a tool that emails exactly one person this costs
nothing — but the two addresses have to match, and the app will tell you so
in plain English if they don't.

The free tier is 3,000 emails a month. This sends a handful a year.

If you would rather use Gmail, `EMAIL_PROVIDER=smtp` with the `SMTP_*` secrets
still works — see `.env.example`.

### 4. Add the secrets

Repository **Settings → Secrets and variables → Actions → New repository
secret**. Add these:

| Secret | Value |
|---|---|
| `ALERT_EMAIL` | where alerts go — the same address as your Resend account |
| `RESEND_API_KEY` | the key from step 3 |
| `ANTHROPIC_API_KEY` | your Claude Console API key (optional) |

Without `ANTHROPIC_API_KEY` everything still works; reports say "cause not
determined" instead of explaining why. It is only called on days something
fires — a few cents a month, not a few cents a day.

### 5. Run it once by hand

**Actions** tab → **DipDector daily scan** → **Run workflow**.

Watch it finish. On a quiet day it prints the scores and exits; the archive's
"last scan" date updates. If something fired, you get the email.

That is the whole setup. It now runs at 21:45 UTC on weekdays.

---

## Running it locally

```bash
python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env          # then fill it in
```

**Python 3.12 or newer is required**, not preferred: `charts.py` uses a
backslash inside an f-string expression, which is a `SyntaxError` on 3.11 and
below.

```bash
# a safe dry run against real prices — prints the email, sends nothing
.venv/bin/python -m dipdector.daily --as-of 2023-03-14 --dry-run \
    --to you@example.com --publish-dir /tmp/preview

# the tests
.venv/bin/python -m pytest dipdector/tests -q          # 44 passing

# rebuild the archive from history
.venv/bin/python -m dipdector.backfill --start 2016-01-01 --step 5

# measure how often it fires, with controls
.venv/bin/python -m dipdector.backtest.run --provider yfinance \
    --start 2016-01-01 --end 2026-08-26 --step 5 --controls
```

`--dry-run` sends nothing and does not update state, so it will alert on the
same event again next time. It still writes report pages, which is how you
preview one.

---

## What the numbers actually say

Measured over the full index, with thresholds derived from a 36-year study
rather than assumed (see *Where the thresholds come from* below):

**It fires 3.8 times a year.** Under ~1 would be too tight to learn from; over
~15 would make it a screener.

**The bounce is real.** Entering three days after each alert: 6-month median
+18.6%, 12-month median +35.5% (92% of the time positive). Every one of the 38
events with a two-year window recovered to its pre-shock level, median 50
trading days. At 12 months even the 10th-percentile outcome was positive
(+3.2%) — the worst decile still made money.

**The median event keeps falling another 6.3% after the alert**, worst case
45%. Detection is not timing, and this is the number that decides whether a
position is holdable.

**Survivorship bias is live, and it inflates everything above.** The universe
is today's S&P 500 applied to every historical date, so a company that fell and
never recovered left the index and is absent by construction. Fixing it needs
point-in-time membership data. Treat these figures as directionally right and
numerically optimistic.

## Where the thresholds come from

The original values came from a design document and had never been fitted to
anything. They now come from evidence, and `dipdector/research/` reproduces the
whole study.

A panel of **103,994 industry-days from 1990 to 2026** was precomputed, and
**1,350 parameter combinations** were scored on 1990–2012 and then re-scored on
2013–2026, which they had never seen. Two results ranked *identically* in both
eras:

- **Deeper falls recover better** — a −20% industry decline beat −15%, which
  beat −10%, −8% and −5%, in that exact order, twice.
- **Requiring members to have fallen further selects better events** — counting
  a company as "declining" at −10% beat −5%, which beat −3%, twice.

Breadth agreed at +0.77 and saturates by 0.70–0.80, so 80% stays.

**The detection window did not transfer at all** (rank correlation −0.10).
2013–2026 says a 2-day window is best, on a +63% 12-month median; 1990–2012
ranks 2 days *last of five*. Fitting on recent data alone would have moved the
window on the strength of noise, so it is deliberately left at 5 days. The
depth of a fall is a real signal; its speed is not.

Only the two replicated findings were changed. Validated afterwards on the live
engine — not on the panel — over 2016–2026: 4.9 → 3.8 events/year, 12-month
median +31.7% → +35.5%, hit rate 82% → 92%, recovery 98% → 100%, and further
fall after entry −11.0% → −6.3%.

The thresholds rest on those rankings, which survivorship bias does not
reorder, rather than on the absolute returns, which it inflates. Hence
`PARAMS_VERSION = "0.5.0-fitted"` — fitted, not validated.

## Design rules held in code, not comments

- **AI never does arithmetic.** `news/engine.py` is the only module that calls
  a model. It receives computed statistics and interprets them. Every number
  comes from `engine/metrics.py`.
- **No lookahead, anywhere.** Every function takes a frame already truncated at
  the as-of date. `test_no_lookahead` guards it; the news cutoff is enforced in
  `fetch`, not left to the caller.
- **Never invent market data.** `FixtureProvider` refuses files not marked
  synthetic. A failed feed fails loudly rather than interpolating.
- **A high score alone is not an event.** The trigger conditions gate promotion
  past WATCH. `test_single_stock_collapse_does_not_trigger` fails if loosened.
- **Absence of evidence is not evidence.** A missing ETF reading or news feed
  makes a condition *skipped* and says so, never passed.
- **Nothing recommends buying.** Actions are "watch", "investigate", "worth a
  full look".
- **Thresholds are versioned.** All in `config.py`, stamped into every stored
  event, so an old alert still explains itself under the rules of its day.

---

## Layout

```
dipdector/
  config.py              all thresholds and weights, versioned
  daily.py               the daily job
  publish.py             builds the report site under docs/
  backfill.py            populates the archive from history
  report.py              the event report page
  charts.py              inline SVG, no JS
  narrative.py           deterministic plain-English summaries
  replay.py              single-date runner
  research/panel.py      36-year statistics panel for threshold work
  research/sweep.py      parameter sweep with train/test split
  data/sp500.py          S&P 500 constituents + GICS, cached to CSV
  data/universe.py       classification adapter over that list
  data/                  price providers, ETF benchmarks
  engine/                deterministic metrics, shock score, triggers
  news/                  article retrieval + Claude cause classification
  analysis/recovery.py   recovery candidate ranking
  backtest/              event study, portfolio simulator, controls
  notify/                email delivery and alert de-duplication
  store/db.py            DuckDB event store
  tests/                 the non-negotiables, as executable assertions
docs/                    the published site (GitHub Pages serves this)
```

The DuckDB store is deliberately not committed — a binary that churns daily
would bloat the repo. `docs/data/events.json` is the durable record, and the
archive rebuilds from it with no database present.

---

Research output only. A detected shock is not a reason to buy anything.
