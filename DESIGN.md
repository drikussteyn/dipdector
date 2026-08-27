# DipDector — design notes

The status of each subsystem against the master devlog. For setup and
operation see `README.md`.

An end-to-end vertical slice of the system described in the master devlog. It
runs the whole chain against one as-of date:

```
universe → price frame → deterministic metrics → Industry Shock Score
→ alert level → news retrieval → AI event classification
→ recovery ranking → event store → HTML report
```

Built as a **replay** rather than a live monitor. It takes an `--as-of` date,
truncates every series at that date and never looks past it. That makes it
testable without a live feed, and it makes this the skeleton the Phase 3
backtester will loop over.

## Run it

```bash
pip install -r requirements.txt
python -m dipdector.fixtures.make_fixture              # synthetic test data
python -m dipdector.replay --as-of 2026-06-30 --provider fixture
python -m pytest dipdector/tests -q
```

Against real data:

```bash
python -m dipdector.replay --as-of 2026-06-26 --provider yfinance --level WATCH
export EODHD_API_KEY=...      # switches on the paid feed and the news engine
export ANTHROPIC_API_KEY=...  # switches on cause classification
```

## What actually works

| Devlog section | Status |
|---|---|
| s.4 industry classification | Adapter interface, curated seed table for the semi complex |
| s.8 company metrics | All six windows, relative performance, vol, volume z, 52w distance |
| s.9 industry metrics | Breadth, dispersion, correlation, abnormality, relative return |
| s.10 Industry Shock Score | 7 weighted components, fully decomposed |
| s.11 alert levels | WATCH / INVESTIGATE / MAJOR EVENT |
| s.6 trigger conditions | 4 of 5 enforced as hard gates, each reported pass/fail |
| s.12–16 news + event engine | Written and wired; needs API keys to run |
| s.18–20 recovery ranking | 5 of ~18 inputs implemented, coverage reported on every score |
| s.23/32 event store | DuckDB, full schema, components stored as JSON for audit |
| s.41 explainability | Enforced — every score carries its inputs and reasons |

## What does not work yet, and why

- **Point-in-time index membership.** The seed table uses current membership for
  all dates. That is survivorship bias, and devlog s.29 forbids relying on it.
  Nothing here should be read as a backtest result until this is replaced. EODHD
  exposes S&P 500 constituents with join and leave dates; the adapter is shaped
  to take it.
- **Real GICS.** GICS Direct is a licensed S&P Global / MSCI product at
  enterprise pricing, not a subscribe-online API. The seed table stands in, with
  sub-industry splits done by hand for the industries that matter. Swapping in a
  licensed feed touches one module.
- **Industry ETF confirmation** (s.6.4). Not wired. The condition is *skipped*
  rather than silently passed, and the report says so.
- **Fundamentals.** 14 of the s.18 resilience inputs need a fundamentals feed.
  The Recovery Score runs on the five that are computable from price and volume
  and reports 26% coverage so the number is never mistaken for the real model.
- **Backtester** (Phase 3). Not started. The replay runner is its inner loop.
- **Outcome tracking** (s.47). Schema columns exist and are written NULL.
  Filling them at detection time would be hindsight.

## Design rules held in code, not in comments

- **Nothing invents market data.** `FixtureProvider` refuses to load a file not
  marked synthetic, and every synthetic run is banner-flagged through to the
  report footer.
- **AI never touches arithmetic.** `news/engine.py` is the only module that
  calls a model. It receives already-computed statistics and is instructed not
  to recompute them.
- **A high score alone is not an event.** The s.6 conditions gate promotion past
  WATCH. `test_single_stock_collapse_does_not_trigger` fails loudly if that
  guard is ever removed.
- **No unexplained numbers.** Score components sum to the total by assertion,
  and every component and trigger carries prose.
- **Thresholds are versioned.** All of them live in `config.py` with a
  `PARAMS_VERSION` stamped into every stored event, so an old alert still
  explains itself under the rules in force when it fired.
- **Judgement is visually separate from measurement.** The report runs two
  registers: measured values in mono on the pale ground, AI assessments on a
  tinted offset panel labelled as judgement.

## Benchmarks (v0.3)

Two comparators, doing different jobs.

**The S&P 500 is the primary and never varies.** It answers "is this an industry
problem or is everything down", and it is the quality gate on the universe:
large, established companies that are likely to still exist when the event
resolves. A rebound thesis needs that.

*One caveat, since real money is involved: index membership lowers bankruptcy
risk, it does not remove it. Lehman Brothers, Washington Mutual, General Motors
and Circuit City were all members shortly before filing. The index screens for
size, not for balance-sheet safety, and it contains plenty of heavily indebted
companies. Good filter, bad guarantee — which is why the structural-risk score
exists.*

**The second comparator is the industry's own ETF**, resolved per industry from
`data/benchmarks.py` — never a fixed index. Semiconductors are compared to SOXX,
airlines to JETS, regional banks to KRE, oil services to OIH. Resolution walks
sub-industry → industry → sector SPDR, and returns nothing rather than guessing.

*Second caveat: an industry ETF usually holds the same companies we are
measuring, so "the ETF confirms it" is partly circular. Its real value is
coverage of names outside the S&P 500 — mid-caps, foreign listings, pure-plays.
If the ETF falls harder than our large-cap basket, smaller operators are being
hit worse, which is evidence about severity. Each mapping carries an `overlap`
rating, and the component is weighted at 5% for this reason.*

This activates devlog s.6.4, the fifth trigger condition, which had been skipped
in every run until now. When no ETF reading is available the condition is
dropped rather than passed — absence of evidence is not confirmation.

## Breadth rule (v0.2)

The original trigger was "at least 3 companies declining". That is 60% of a
5-member group and 15% of a 20-member one — the same rule meaning two different
things. It is now proportional:

- **80% of the industry** must be declining at least 3%, **and**
- **at least 4 companies**, because 80% of a tiny group is still a tiny group, and
- **minimum 5 members** before an industry is scored at all.

The same treatment was applied to relative underperformance: 60% of members
plus a floor of 3.

**The synthetic fixture cannot evaluate this change.** Its seeded shocks hit
every member at once, so breadth is near 100% for every event and the rule
barely bites (1.0 → 0.9 events/year). Real shocks hit unevenly — a few names
have different exposure and hold up — so expect 80% to be a much harder filter
on live data. It may prove too hard: allowing only 2 of 10 names to escape is
tight. Treat 0.65–0.80 as the range to test once real prices are wired, and
bump `PARAMS_VERSION` when you settle it.

## Beta-adjusted relative return

"Semiconductors fell 17% while the S&P fell 2%" assumes the group should move
one-for-one with the market. It shouldn't. The engine now estimates

    β = Cov(r_industry, r_market) / Var(r_market)

on the 252 days *ending before* the detection window, so the shock cannot
inflate its own baseline, and reports the residual:

    expected = β × r_market
    α        = r_actual − expected

α is the part the market cannot explain, and it is what the relative-weakness
score now uses. If the regression's R² falls below 0.10 the beta is discarded
rather than displayed — a beta from a regression explaining 1% of variance is
noise with a decimal point — and the report falls back to the raw difference
with a warning saying so.

## Known weakness in the current parameters

The thresholds are the devlog's starting values and have **not** been validated.
Expect the -8% median plus 3-company plus 5pp relative test to fire rarely. The
first real question to answer with live data is the firing rate: run the replay
across a few hundred historical dates and count events per year per industry. If
it is under about two, the filter is probably too tight to learn from; if it is
over about fifteen, it is a screener. Tune from that measurement, not from
intuition, and bump `PARAMS_VERSION` when you do.

## The backtester

```bash
python -m dipdector.fixtures.make_history        # 14 years of synthetic history
python -m dipdector.backtest.run --provider fixture \
    --start 2014-01-01 --end 2026-06-30 --step 5 --controls
```

It runs in two halves, and the order is deliberate.

**The event study runs first.** It asks only whether the bounce exists: for
every detection, what happened over the following 1, 3, 6 and 12 months, entered
three days after the alert rather than at the bottom. Reported as a distribution
— median, hit rate, 10th percentile, worst case — never as an average, because
an average forward return of +8% is equally consistent with "most events worked"
and "one event carried eleven failures." It also reports how far the basket kept
falling *after* entry, which decides whether the position was holdable, and how
long recovery took.

**The portfolio simulator runs second**, across three reaction-speed scenarios,
with slippage, commission, staged entry, minimum holds and rotation. If the
event study is weak, this half isn't worth reading.

Controls matter more than the headline number. The random-timing control reuses
the same capital, delays, costs and sizing but picks entry dates at random. If
the strategy can't beat it convincingly, the detector isn't contributing and the
returns are just equity exposure.

### Two rules added that aren't in the devlog

The spec has no answer to being wrong, so the simulator supports both and
declares them in the output whenever they're active:

- `--max-position` caps how much of the portfolio goes into one event. The
  devlog rotates the whole position into an industry that has just crashed.
- `--stop-loss` gives a way out of a structural event. Devlog s.26 sells only
  when the next opportunity appears, which means a permanently impaired position
  is held indefinitely waiting for an unrelated trigger.

Neither is obviously correct — a stop converts recoverable drawdowns into
realised losses — but both need to be measurable.

### Warnings the runner will give you

- **Non-monotonic scenarios.** If reacting *more slowly* produces a better
  result, that isn't an effect, it's the sample being too small. The runner says
  so explicitly rather than letting you read the CAGR.
- **Survivorship bias.** Printed at the top of every run while the universe uses
  current index membership.

## Layout

```
dipdector/
  config.py              all thresholds and weights, versioned
  narrative.py           deterministic plain-English summaries
  data/universe.py       classification adapter + seed table
  data/providers.py      yfinance / EODHD / synthetic fixture
  engine/metrics.py      deterministic company + industry metrics
  engine/detection.py    shock score, components, trigger gates, alert levels
  news/engine.py         article retrieval + Claude event classification
  analysis/recovery.py   recovery candidate scoring
  backtest/scan.py       day-by-day event scan with de-duplication
  backtest/event_study.py  forward returns and recovery profile
  backtest/simulator.py  portfolio simulation with delays and rotation
  backtest/metrics.py    performance stats and control benchmarks
  backtest/run.py        backtest CLI
  store/db.py            DuckDB event store
  report.py              HTML report
  replay.py              single-date CLI runner
  tests/                 non-negotiables as executable assertions
```
