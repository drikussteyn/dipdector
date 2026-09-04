# BUILD BRIEF — for the coding agent

Read this before changing anything. Then read `README.md` for what each module
does, and `dipdector/config.py` for every tunable number.

## What this is

A personal research tool that watches for whole industries crashing together,
works out why, and emails the owner. It is **not** a trading system, it does not
place orders, and it must never tell anyone to buy anything.

## State of the code

Working, tested, 75 passing tests, deployed, and running against real market data.

- detection engine, industry shock score, 5 trigger conditions
- per-industry ETF benchmarks, beta-adjusted excess return
- recovery candidate ranking (5 of ~18 intended inputs)
- news retrieval + Claude cause classification
- HTML report with charts, published as a linked page per event
- static site builder + archive index (`publish.py`)
- history backfill (`backfill.py`)
- backtester: event study + portfolio simulator + controls
- daily job with alert de-duplication and email delivery
- GitHub Actions schedule, Pages hosting, state committed back

**Verify this before you start, and again before you finish:**

```bash
python -m pytest dipdector/tests -q                    # expect 75 passed
python -m dipdector.daily --provider fixture \
  --fixture dipdector/fixtures/synthetic_semis.csv \
  --as-of 2026-06-30 --to test@example.com --dry-run \
  --news none --state /tmp/s.json --db /tmp/d.duckdb \
  --publish-dir /tmp/site                              # expect one alert printed
```

## What real data showed

The first real run happened. Everything below is measured, not assumed.

- **yfinance did not break.** Column layout matches what `providers.py`
  expects, all 35 tickers resolve at 100% coverage, a scan takes ~6 seconds.
  The prediction that this would be the fragile part was wrong — so far.
- **Firing rate: 3.3 events/year** over 2016–2026 (35 events in 10.7 years).
  Inside the useful band, so the thresholds were NOT retuned and
  `PARAMS_VERSION` is unchanged.
- **The event study is positive**: 3m median +8.6% / 68% hit, 12m +15.5% /
  76%, 91% recovered within two years.
- **The score does not predict bounce size.** Bucketed 6m returns are flat and
  non-monotonic. Do not treat a 100 as a better opportunity than a 60.
- **Median further fall after entry is −19%**, worst −65%.
- **The portfolio simulator is noise** — its own non-monotonicity warning
  fires, 12 of 56 events were taken, and it loses to buy-and-hold on
  risk-adjusted terms. Read the event study, not the CAGR.

Three bugs were found and fixed that only ever surface on a day an alert
fires, which is why 26 tests passed over a broken production path:

1. `news/free.py` compared a naive `as_of` against aware article timestamps →
   `TypeError` on every alerting day. Both providers now normalise via
   `news/engine.py::to_utc`.
2. `notify/email.py` read `SMTP_USER`/`SMTP_FROM` while the docs and workflow
   set `SMTP_USERNAME`/`SMTP_SENDER` → SMTP could never authenticate.
3. `classify_event` let an API failure propagate and kill the run. It now
   degrades to the explicit stub.

Plus two silent traps: `EMAIL_PROVIDER` was documented but read by nothing
(so "start on console" was unsafe), and the workflow treated exit code 10
("alerts sent") as a job failure.

## What deployment showed

It is live, and the first *scheduled* run failed. Two manual runs had passed
an hour earlier, which is the part worth understanding: the workflow was
switched from Resend to SMTP between them, and the README was not. So the
setup guide told you to add `RESEND_API_KEY` while the workflow read
`SMTP_USERNAME` / `SMTP_PASSWORD`, and no one adds a secret no document asks
for. Docs drifting from a workflow is not a documentation problem here; it is
an outage.

There was a second copy of the same mistake in the workflow itself: it set
`EMAIL_PROVIDER: smtp` *and* passed `--sender smtp`, and the flag silently
wins over the variable. Two settings that must agree is one too many, so the
flag is gone and `EMAIL_PROVIDER` is now the only switch.

Delivery has since been put back on Resend, deliberately and with its cost
accepted: the free tier sends from `onboarding@resend.dev`, shared by every
unverified account on the platform, and Gmail files it as spam. **The sender
has to be whitelisted** — README step 4 — or the alert arrives somewhere
nobody looks, which for a detector that fires twice a year is barely
distinguishable from not sending it. Verifying a domain and setting
`RESEND_FROM` is the real fix; SMTP stays wired up as the alternative.

What the outage exposed is the bug worth remembering. `get_sender()` was
called before the code knew whether there was anything to send, so a missing
SMTP password raised on a day **two industries scored MAJOR_EVENT** and took
the whole run with it — no report page, no stored event, no state write. The
detection worked perfectly and left no trace that it had. Worse, the failed
step skipped the commit step after it, so the archive's last-scan date froze
at the last good run and the site became indistinguishable from a dead job —
the exact ambiguity `note_quiet_run` exists to prevent.

Fixed by separating the product from the channel: the scan publishes its
pages, stores its event and writes its state, and only then is delivery
allowed to fail. It still fails — silence is this tool's normal output, so
silence cannot also be how a broken mailer looks, and the red run is the only
remaining way to say so — but it fails with the evidence on disk and the
alert deliberately not marked as sent, so the next run retries it.
`tests/test_daily.py` is the alarm; it had no coverage before, which is why
this shipped.

## Your tasks, in order

Tasks 1–4 of the original brief are done: the first real run, the firing-rate
measurement, delivery, and scheduling. What remains:

### 1. Confirm delivery actually arrives

Pages is live, the schedule runs, and `RESEND_API_KEY` is set, so the next run
should end green. What is NOT confirmed is that the mail reaches an inbox
rather than a spam folder — the whole point of the sender warning above. Run
the workflow by hand, then go and look. If it landed in spam, whitelist
`onboarding@resend.dev` before doing anything else; a detector nobody reads is
not a working detector. No code changes needed for any of this.

### 2. Watch the first month

The thresholds are validated against history, not against the live feed. Check
that the firing rate in practice resembles 3.3/year and that yfinance keeps
working — it is unofficial and will eventually break.

### 3. Then, and only then, features

Ask the owner before starting any of these:
- point-in-time index membership (removes survivorship bias — highest value)
- fundamentals feed (unlocks 14 of 18 recovery inputs)
- 59 companies sit in industry groups of one or two members and so are never
  scored: two names cannot demonstrate breadth and no p-value rescues that.
  Measured on the live universe today — 70 of 111 groups clear the 3-member
  floor, covering 443 of 502 companies (88%). This bullet previously said
  *206 companies* against a *5-member* floor; that was true until 0.4.0
  replaced the flat floor with the breadth significance test, and was left
  uncorrected. Grouping the remainder by sector would mix refiners with coal
  miners and manufacture false breadth — worse than the gap.

**Done since the original brief:** the universe is no longer six hand-picked
industries. `data/sp500.py` loads all 503 constituents with their GICS
classification, and 36 sub-industries clear the 5-member floor. This also
resolved a dead spot — Semiconductor Materials & Equipment had 4 members
against a minimum of 5 and could never fire; on the full index it has enough
members, and it scored highest of all 36 groups on the first live scan.

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
