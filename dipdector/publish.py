"""
Static site builder for the hosted reports.

The email is a summary and a link; this is what the link points at. Email
clients strip <details>, external stylesheets and web fonts, so the report's
progressive disclosure cannot survive in an inbox — but it survives perfectly
in a browser. Publishing the report and mailing its URL is what makes the
design work as intended, instead of degrading to an attachment the reader has
to download before they can read it.

Layout produced under the publish root (`docs/` by default, which is what
GitHub Pages serves):

    index.html                              archive, newest first
    events/2023-03-14-regional-banks.html   one permanent page per event
    data/events.json                        the index's source of truth
    .nojekyll                               stop Pages running Jekyll on it

One page per event, keyed by date and industry, so a URL mailed today still
resolves years later and still says what it said at the time. The index is
rebuilt from events.json rather than from the event store, so the site can be
regenerated from the repo alone with no database present.

Nothing here writes anything reader-identifying: the pages are public when the
repository is, and the recipient's address lives only in the email envelope.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
from dataclasses import asdict, dataclass
from typing import Dict, List, Optional

from jinja2 import Template

from .report import render

LEVEL_COLOR = {"MAJOR_EVENT": "#9E2B21", "INVESTIGATE": "#86580A",
               "WATCH": "#1E5C4F", "NONE": "#565B62"}
LEVEL_WORD = {"MAJOR_EVENT": "Major event", "INVESTIGATE": "Investigate",
              "WATCH": "Watch", "NONE": "Normal"}


@dataclass(frozen=True)
class PublishedReport:
    """One event's page, and where it can be reached."""

    industry: str
    as_of: str
    level: str
    score: float
    median_return: float
    headline: str
    path: str                      # relative to the publish root
    url: Optional[str] = None      # absolute, when a site base URL is known

    @property
    def link(self) -> str:
        """What the email should point at. Relative if no base URL is set."""
        return self.url or self.path


def slugify(text: str) -> str:
    """Industry name -> URL fragment. Stable, because URLs must not move."""
    s = text.lower().replace("&", " and ")
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return re.sub(r"-+", "-", s).strip("-")


def event_path(as_of: str, industry: str) -> str:
    return f"events/{as_of}-{slugify(industry)}.html"


def _abs_url(base_url: Optional[str], path: str) -> Optional[str]:
    if not base_url:
        return None
    return f"{base_url.rstrip('/')}/{path.lstrip('/')}"


# --------------------------------------------------------------------------
# The archive index
# --------------------------------------------------------------------------

# autoescape, because industry names carry ampersands ("Oil & Gas Equipment &
# Services") and every value this template interpolates is text, never markup.
INDEX = Template(autoescape=True, source="""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light only">
<title>DipDector — industry shock archive</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500&family=Newsreader:opsz,wght@6..72,300;6..72,400;6..72,500&display=swap" rel="stylesheet">
<style>
:root{color-scheme:light only;
  --paper:#F7F7F3; --panel:#ECECE6; --ink:#16181B; --muted:#565B62;
  --rule:#D8D8D1; --rule-soft:#E4E4DD;}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%;background:var(--paper)}
body{margin:0;background:var(--paper);color:var(--ink);
  font-family:ui-sans-serif,system-ui,-apple-system,'Segoe UI',Helvetica,sans-serif;
  font-size:16px;line-height:1.6;-webkit-font-smoothing:antialiased}
.wrap{max-width:720px;margin:0 auto;padding:44px 22px 100px}
.mono{font-family:'IBM Plex Mono',ui-monospace,monospace}
.brand{font-family:'IBM Plex Mono',monospace;font-size:12px;letter-spacing:.18em;
  text-transform:uppercase;color:var(--muted)}
h1{font-family:'Newsreader',Georgia,serif;font-weight:400;font-size:34px;
  line-height:1.25;letter-spacing:-.012em;margin:12px 0 0}
p.lede{color:var(--muted);font-size:16px;margin:14px 0 0;max-width:58ch}
.meta{margin:34px 0 0;padding:16px 18px;background:var(--panel);
  font-family:'IBM Plex Mono',monospace;font-size:12.5px;color:var(--muted);
  line-height:1.7}
.meta b{color:var(--ink);font-weight:500}
h2.year{font-family:'IBM Plex Mono',monospace;font-size:12px;letter-spacing:.16em;
  color:var(--muted);margin:46px 0 0;padding-bottom:10px;
  border-bottom:1px solid var(--rule)}
ul.events{list-style:none;margin:0;padding:0}
li.ev{border-bottom:1px solid var(--rule-soft)}
li.ev a{display:flex;align-items:baseline;gap:14px;flex-wrap:wrap;
  padding:16px 2px;text-decoration:none;color:inherit}
li.ev a:hover{background:var(--panel)}
li.ev a:focus-visible{outline:2px solid var(--ink);outline-offset:-2px}
.date{font-family:'IBM Plex Mono',monospace;font-size:13px;color:var(--muted);
  flex:0 0 92px}
.name{font-family:'Newsreader',Georgia,serif;font-size:20px;flex:1 1 210px;
  line-height:1.3}
.dot{width:8px;height:8px;border-radius:50%;display:inline-block;
  vertical-align:middle;margin-right:6px}
.lvl{font-family:'IBM Plex Mono',monospace;font-size:11px;letter-spacing:.1em;
  text-transform:uppercase;flex:0 0 auto}
.num{font-family:'IBM Plex Mono',monospace;font-size:13px;color:var(--muted);
  flex:0 0 auto;margin-left:auto;text-align:right}
.empty{margin:44px 0 0;padding:26px;background:var(--panel);color:var(--muted);
  font-size:15px}
footer{margin-top:64px;padding-top:20px;border-top:1px solid var(--rule);
  font-size:12.5px;color:var(--muted);line-height:1.65}
@media (max-width:520px){
  .num{margin-left:0;flex-basis:100%;text-align:left}
  .date{flex-basis:100%}
}
</style></head>
<body><div class="wrap">

<header>
  <div class="brand">DipDector</div>
  <h1>Industry shock archive</h1>
  <p class="lede">Every industry-wide decline this detector has flagged, newest
  first. Each entry opens the full report as it was written on the day —
  the measurements, the score breakdown, and what was known about the cause.</p>
</header>

<div class="meta">
  <b>{{ total }}</b> event{{ '' if total == 1 else 's' }} recorded ·
  last scan <b>{{ last_run }}</b>{% if universe %} ·
  <b>{{ universe }}</b> companies monitored{% endif %}<br>
  Thresholds <b>{{ params_version }}</b>
</div>

{% if not years %}
<div class="empty">No events recorded yet. The detector runs daily and writes
here the first time an industry meets every trigger condition.</div>
{% endif %}

{% for year, rows in years %}
<h2 class="year">{{ year }}</h2>
<ul class="events">
  {% for r in rows %}
  <li class="ev"><a href="{{ r.path }}">
    <span class="date">{{ r.as_of }}</span>
    <span class="name">{{ r.industry }}</span>
    <span class="lvl" style="color:{{ r.color }}">
      <span class="dot" style="background:{{ r.color }}"></span>{{ r.word }}</span>
    <span class="num">{{ '%.0f'|format(r.score) }}/100 &middot;
      {{ '%+.1f'|format(r.median_return * 100) }}%</span>
  </a></li>
  {% endfor %}
</ul>
{% endfor %}

<footer>
  Research output. Scores are model estimates, not predictions. A detected
  shock is not a reason to buy anything.<br>
  Generated {{ generated_at }}.
</footer>

</div></body></html>""")


# --------------------------------------------------------------------------
# 404
# --------------------------------------------------------------------------

NOT_FOUND = Template(autoescape=True, source="""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light only">
<title>DipDector — report not found</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500&family=Newsreader:opsz,wght@6..72,300;6..72,400&display=swap" rel="stylesheet">
<style>
:root{color-scheme:light only;--paper:#F7F7F3;--panel:#ECECE6;--ink:#16181B;
  --muted:#565B62;--rule:#D8D8D1}
*{box-sizing:border-box}
html{background:var(--paper);-webkit-text-size-adjust:100%}
body{margin:0;background:var(--paper);color:var(--ink);font-size:16px;line-height:1.6;
  font-family:ui-sans-serif,system-ui,-apple-system,'Segoe UI',Helvetica,sans-serif}
.wrap{max-width:560px;margin:0 auto;padding:64px 22px 100px}
.brand{font-family:'IBM Plex Mono',monospace;font-size:12px;letter-spacing:.18em;
  text-transform:uppercase;color:var(--muted)}
h1{font-family:'Newsreader',Georgia,serif;font-weight:400;font-size:31px;
  line-height:1.25;margin:12px 0 0}
p{color:var(--muted);margin:16px 0 0;max-width:52ch}
.box{margin:30px 0 0;padding:18px 20px;background:var(--panel);font-size:15px;
  color:var(--ink)}
a.btn{display:inline-block;margin-top:26px;padding:12px 22px;background:var(--ink);
  color:var(--paper);text-decoration:none;border-radius:4px;font-size:15px}
a.btn:hover{opacity:.88}
footer{margin-top:52px;padding-top:18px;border-top:1px solid var(--rule);
  font-size:12.5px;color:var(--muted)}
</style></head>
<body><div class="wrap">
<div class="brand">DipDector</div>
<h1>That report isn't here any more</h1>
<p>The link is valid but the page it pointed at has moved. This happens when
the industry classification changes and a report's address changes with it —
an early alert about "Regional Banks" now lives under "Banks - Regional".</p>
<div class="box">Every event this detector has found is in the archive, newest
first. The one you were looking for is almost certainly there under a slightly
different name.</div>
<a class="btn" href="/dipdector/">Open the archive &rarr;</a>
<footer>Research output. A detected shock is not a reason to buy anything.</footer>
</div></body></html>""")


def write_not_found(root: str) -> None:
    """
    GitHub Pages serves this for any unmatched path.

    publish.py promises that a URL mailed today still resolves years later,
    and reclassifying the universe broke that promise once already: renaming
    an industry renames its slug, and every link already sent goes dead. The
    archive is the durable entry point, so a stale link should land somewhere
    that explains itself and points there, not on a bare 404.
    """
    with open(os.path.join(root, "404.html"), "w") as f:
        f.write(NOT_FOUND.render())

# --------------------------------------------------------------------------
# Reading and writing the index record
# --------------------------------------------------------------------------

def _index_file(root: str) -> str:
    return os.path.join(root, "data", "events.json")


def load_index(root: str) -> Dict:
    """Existing record, or an empty one. Never raises on a fresh checkout."""
    path = _index_file(root)
    if not os.path.exists(path):
        return {"events": [], "last_run": None, "universe": None,
                "params_version": None}
    with open(path) as f:
        return json.load(f)


def save_index(root: str, record: Dict) -> None:
    path = _index_file(root)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(record, f, indent=2, sort_keys=True)
        f.write("\n")


def rebuild_index(root: str) -> str:
    """Regenerate index.html from events.json alone."""
    record = load_index(root)
    rows = sorted(record.get("events", []),
                  key=lambda e: (e["as_of"], e["industry"]), reverse=True)
    for r in rows:
        r["color"] = LEVEL_COLOR.get(r["level"], "#565B62")
        r["word"] = LEVEL_WORD.get(r["level"], r["level"])

    years: List = []
    for r in rows:
        year = r["as_of"][:4]
        if not years or years[-1][0] != year:
            years.append((year, []))
        years[-1][1].append(r)

    html = INDEX.render(
        years=years, total=len(rows),
        last_run=record.get("last_run") or "—",
        universe=record.get("universe"),
        params_version=record.get("params_version") or "—",
        generated_at=dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
    )
    os.makedirs(root, exist_ok=True)
    with open(os.path.join(root, "index.html"), "w") as f:
        f.write(html)
    # Pages runs Jekyll by default, which would skip anything underscore-prefixed.
    open(os.path.join(root, ".nojekyll"), "a").close()
    write_not_found(root)
    return html


# --------------------------------------------------------------------------
# Publishing a run
# --------------------------------------------------------------------------

def publish(events: List[dict], run: dict, root: str = "docs",
            base_url: Optional[str] = None,
            universe_size: Optional[int] = None) -> List[PublishedReport]:
    """
    Write one page per event, refresh the archive, and return the links.

    `events` is the same list `report.render` takes, so the published page is
    the report — not a reduced copy of it that can drift.
    """
    published: List[PublishedReport] = []
    os.makedirs(os.path.join(root, "events"), exist_ok=True)

    for e in events:
        a = e["assessment"]
        path = event_path(run["as_of"], a.industry)
        # render() takes a list; one event per page keeps the URL permanent.
        html = render([e], {**run, "permalink": _abs_url(base_url, path)})
        with open(os.path.join(root, path), "w") as f:
            f.write(html)

        published.append(PublishedReport(
            industry=a.industry, as_of=run["as_of"], level=a.level.value,
            score=a.score, median_return=a.metrics.median_return,
            headline=e.get("headline") or a.industry,
            path=path, url=_abs_url(base_url, path),
        ))

    record = load_index(root)
    known = {(e["as_of"], e["industry"]): e for e in record.get("events", [])}
    for p in published:
        # Re-publishing the same date and industry overwrites in place rather
        # than adding a second row for one event.
        known[(p.as_of, p.industry)] = {
            k: v for k, v in asdict(p).items() if k != "url"
        }
    record["events"] = list(known.values())
    record["last_run"] = run["as_of"]
    record["params_version"] = run.get("params_version")
    if universe_size:
        record["universe"] = universe_size
    save_index(root, record)
    rebuild_index(root)

    return published


def note_quiet_run(root: str, as_of: str, params_version: Optional[str] = None,
                   universe_size: Optional[int] = None) -> None:
    """
    Record that a scan ran and found nothing.

    Worth writing. An archive whose newest entry is four months old is
    ambiguous between a quiet market and a job that died in March; the
    last-scan date on the index resolves it.
    """
    record = load_index(root)
    record["last_run"] = as_of
    if params_version:
        record["params_version"] = params_version
    if universe_size:
        record["universe"] = universe_size
    save_index(root, record)
    rebuild_index(root)
