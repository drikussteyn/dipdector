# DipDector

Watches for whole industries crashing together, works out why, and emails you a
link to the report.

It is **not** a trading system. It places no orders, and nothing it produces
recommends buying anything. It answers one question — *did an entire industry
just fall together, and is that unusual?* — and hands you the evidence.

---

## What you get

**A daily scan.** Every weekday after the US close, GitHub runs the detector
over 35 large-cap companies in 6 sub-industries. Most days nothing fires and
you hear nothing.

**An email when something does.** Short and plain: what fell, how far, how
unusual, and a link.

**A permanent report page per event.** The link opens the full report — the
measurements, the seven score components and what each contributed, all five
trigger conditions with pass/fail, two charts, and whatever the cause analysis
could establish from the news. Every page stays at its URL forever, so an
email from two years ago still opens the report as it was written that day.

**An archive.** [`docs/index.html`](docs/index.html) lists every event ever
detected, newest first.

The archive ships already populated with **35 real events from 2016–2026**,
backfilled from live market data — including the March 2023 regional banking
collapse (score 100/100), the February 2020 crash across airlines, hotels,
banks and oil services, and the August 2024 unwind.

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

### 3. Create a Gmail App Password

A normal Gmail password will not work here, and should never go in a config
file anyway.

Google Account → **Security** → turn on **2-Step Verification** → **App
passwords** → create one for "Mail". You get a 16-character string.

### 4. Add the secrets

Repository **Settings → Secrets and variables → Actions → New repository
secret**. Add these:

| Secret | Value |
|---|---|
| `ALERT_EMAIL` | where alerts go |
| `SMTP_HOST` | `smtp.gmail.com` |
| `SMTP_PORT` | `587` |
| `SMTP_USERNAME` | your Gmail address |
| `SMTP_PASSWORD` | the 16-character App Password from step 3 |
| `SMTP_SENDER` | your Gmail address |
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

Measured over 10.7 years of real prices, not fixtures:

**It fires 3.3 times a year.** Under ~1 would be too tight to learn from; over
~15 would make it a screener. The starting thresholds landed inside the useful
band without tuning, so they have not been changed.

**The bounce is real.** Entering three days after each alert: 3-month median
+8.6% (68% of the time positive), 12-month median +15.5% (76%). 91% of events
recovered to their pre-shock level within two years, median 44 trading days.

**Three things you should not skip:**

- **The median event keeps falling 19% after the alert**, worst case 65%.
  Detection is not timing. This is the number that decides whether a position
  is holdable, and it is large.
- **The shock score does not predict the size of the bounce.** Bucketed by
  score, 6-month returns come out flat and out of order (+11.3% / +14.9% /
  +12.5%). A 100/100 is not a better opportunity than a 60/100 — it is a
  bigger fall.
- **Survivorship bias is live.** The universe uses today's index membership
  for every historical date, so every figure above is optimistic. Fixing this
  needs point-in-time membership data.

As a portfolio strategy the simulator does not beat buying and holding the
S&P 500 once risk is counted (Sharpe 0.52 vs 0.68, drawdown −64% vs −34%), and
naive single-stock dip buying beat both. It does beat 90% of random-timing
runs, so the detector is contributing something — but the event study is the
half worth reading, and this is a research tool, not a strategy.

---

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
  data/                  universe, price providers, ETF benchmarks
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
