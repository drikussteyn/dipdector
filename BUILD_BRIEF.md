# BUILD BRIEF — for the coding agent

Read this before changing anything. Then read `README.md` for what each module
does, and `dipdector/config.py` for every tunable number.

## What this is

A personal research tool that watches for whole industries crashing together,
works out why, and emails the owner. It is **not** a trading system, it does not
place orders, and it must never tell anyone to buy anything.

## State of the code

Working, tested, 26 passing tests. Everything below runs today:

- detection engine, industry shock score, 5 trigger conditions
- per-industry ETF benchmarks, beta-adjusted excess return
- recovery candidate ranking (5 of ~18 intended inputs)
- news retrieval + Claude cause classification
- HTML report with charts
- backtester: event study + portfolio simulator + controls
- daily job with alert de-duplication and email delivery

**Verify this before you start, and again before you finish:**

```bash
python -m pytest dipdector/tests -q                    # expect 26 passed
python -m dipdector.daily --provider fixture \
  --fixture dipdector/fixtures/synthetic_semis.csv \
  --as-of 2026-06-30 --to test@example.com --dry-run \
  --news none --state /tmp/s.json --db /tmp/d.duckdb  # expect one alert printed
```

## What has never been run against real data

Everything. Every number produced so far comes from synthetic fixtures. The
first real run is the actual milestone here.

## Your tasks, in order

### 1. First real run (do this before anything else)

```bash
pip install -r requirements.txt
python -m dipdector.daily --provider yfinance --as-of <recent weekday> \
  --to you@example.com --dry-run --level WATCH
```

Expect breakage. yfinance is scraped, not an official API: column layouts shift
between versions, tickers go missing, rate limits bite. Fix what breaks in
`data/providers.py::YFinanceProvider` only. Do not paper over missing data by
substituting values.

Then report back to the owner:
- how many industries fired, at what level
- whether any ticker in `data/universe.py` failed to resolve
- how long the run took

### 2. Measure the firing rate

The thresholds have never been validated. Before trusting any alert, find out
how often this thing actually fires on real data:

```bash
python -m dipdector.backtest.run --provider yfinance \
  --start 2016-01-01 --end <today> --step 5 --controls
```

Under ~1 event/year the filter is too tight to learn from. Over ~15 it has
become a screener. Report the number; do not silently retune thresholds to hit
a target. If you do change one, bump `PARAMS_VERSION` and add a line to
`CHANGELOG_THRESHOLDS`.

### 3. Wire delivery

`.env.example` → `.env`. Start with `EMAIL_PROVIDER=console`, confirm the text
looks right, then switch to `smtp` with a **Gmail app password** (not the
account password). Send one real email before automating anything.

### 4. Schedule it

`deploy/.github/workflows/daily.yml` is preferred — a laptop cron job only fires
when the lid is open. The state file must persist between runs or the owner gets
emailed about the same event every day; the workflow commits it back to the
repo for exactly this reason.

### 5. Then, and only then, features

Ask the owner before starting any of these:
- point-in-time index membership (removes survivorship bias — highest value)
- fundamentals feed (unlocks 14 of 18 recovery inputs)
- expanding `data/universe.py` beyond 6 sub-industries

## Rules that must not be broken

These are load-bearing. Tests enforce most of them; the tests are not the
reason, they are the alarm.

1. **AI never does arithmetic.** `news/engine.py` is the only module allowed to
   call a model. It receives computed statistics and interprets them. Every
   number in the system comes from `engine/metrics.py`.
2. **No lookahead, anywhere.** Every function takes a frame already truncated at
   the as-of date. `test_no_lookahead` guards this. The backtester is worthless
   without it.
3. **Never invent market data.** `FixtureProvider` refuses files not marked
   synthetic. If a feed fails, fail loudly — do not interpolate, do not
   substitute a similar ticker, do not fall back to a cached value silently.
4. **A high score alone is not an event.** The trigger conditions in
   `engine/detection.py` gate promotion past WATCH.
   `test_single_stock_collapse_does_not_trigger` fails if this is loosened.
5. **Absence of evidence is not evidence.** When the ETF reading or the news
   feed is missing, the condition is *skipped* and said so, never passed.
6. **No unexplained numbers.** Every score component carries prose explaining
   itself. Components must sum to the total — asserted in tests.
7. **Thresholds are versioned.** All in `config.py`, stamped into every stored
   event, so an old alert still explains itself under the rules of its day.
8. **Nothing recommends buying.** Actions are "watch", "investigate", "worth a
   full look". Keep it that way.

## Things that will bite you

- **yfinance is fragile.** Unofficial, rate-limited, and its DataFrame shape
  changes between releases. Pin the version. Expect to fix it periodically.
- **Email clients strip `<details>`.** The report's progressive disclosure does
  not work in Gmail. That is why the email is a plain summary with the full
  report attached. Don't try to inline the report.
- **Survivorship bias is live right now.** `data/universe.py` uses current index
  membership for all dates. Every backtest number is optimistic until this is
  fixed. The runner prints this warning; don't remove it.
- **GitHub's scheduler runs late** under load, sometimes by an hour. Harmless
  here — daily bars don't change — but don't build anything time-critical on it.
- **Secrets.** `.env` is gitignored. Use GitHub Secrets in the workflow. Never
  log an API key, never commit one.

## If you disagree with something here

Say so to the owner rather than working around it. Several of these rules cost
convenience on purpose. The tests exist to make quiet erosion loud, not to be
satisfied by whatever means available — if a test fails, fix the cause, don't
adjust the assertion to match the new behaviour.
