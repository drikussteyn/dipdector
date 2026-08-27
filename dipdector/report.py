"""
HTML event report.

Rebuilt around progressive disclosure. The surface of the page answers three
questions in plain English — what happened, how unusual is it, what should I do
next — and nothing else. Every number, every component breakdown, every table
sits inside a closed panel the reader opens if they want it.

Two rules kept from the first version because devlog s.35 requires them:
  - measured facts and AI judgement stay visually distinct, so you never have to
    wonder which kind of claim you are reading
  - the score decomposition is always reachable, just no longer in the way

Palette contrast was checked against WCAG AA for every text/background pair used
(lowest ratio in use is 5.27:1, against a 4.5 requirement). Panels use native
<details>, so keyboard and screen-reader behaviour is free and correct.
"""

from __future__ import annotations

from typing import List

from jinja2 import Template

from . import charts, narrative

LEVEL = {
    "MAJOR_EVENT": {"word": "Major event", "color": "#9E2B21"},
    "INVESTIGATE": {"word": "Investigate", "color": "#86580A"},
    "WATCH":       {"word": "Watch",       "color": "#1E5C4F"},
    "NONE":        {"word": "Normal",      "color": "#565B62"},
}

COMPONENT_COLORS = {
    "magnitude": "#22384F", "relative_weakness": "#33556E",
    "breadth": "#43738A", "abnormality": "#57909B",
    "correlation": "#7BAFAC", "volume": "#A5C9BE",
    "benchmark_confirmation": "#C9DCD1",
}

# autoescape, because industry names carry ampersands ("Oil & Gas Equipment &
# Services") and so does the prose around them ("S&P 500"). Without it those
# reach the page as bare & and the markup is invalid. The two chart fields are
# the only values that are deliberately markup; charts.py escapes its own text
# before embedding it, so |safe is correct for them and nothing else.
TEMPLATE = Template(autoescape=True, source="""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light only">
<title>DipDector — {{ run.as_of }}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500&family=Newsreader:opsz,wght@6..72,300;6..72,400;6..72,500&display=swap" rel="stylesheet">
<style>
:root{
  color-scheme:light only;
  --paper:#F7F7F3; --panel:#ECECE6; --ink:#16181B; --muted:#565B62;
  --rule:#D8D8D1; --rule-soft:#E4E4DD;
  --judge-bg:#EAE7F0; --judge-ink:#2E2A3D; --judge-mark:#584C7A;
}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%;background:var(--paper)}
body{margin:0;background:var(--paper)!important;color:var(--ink)!important;
  font-family:ui-sans-serif,system-ui,-apple-system,'Segoe UI',Helvetica,sans-serif;
  font-size:16px;line-height:1.6;-webkit-font-smoothing:antialiased}
.wrap{max-width:660px;margin:0 auto;padding:44px 22px 100px;background:var(--paper)}
.mono{font-family:'IBM Plex Mono',ui-monospace,monospace}

/* header */
header.top{margin-bottom:52px}
.brand{font-family:'IBM Plex Mono',monospace;font-size:12px;letter-spacing:.18em;
  text-transform:uppercase;color:var(--muted)}
.status{font-family:'Newsreader',Georgia,serif;font-size:23px;font-weight:400;
  line-height:1.35;margin:12px 0 0}

/* event */
.event{padding-top:34px;border-top:1px solid var(--rule);margin-top:44px;
  background:var(--paper)}
.event:first-of-type{border-top:none;margin-top:0;padding-top:0}
.tag{display:inline-flex;align-items:center;gap:7px;font-family:'IBM Plex Mono',monospace;
  font-size:11.5px;letter-spacing:.13em;text-transform:uppercase;font-weight:500}
.dot{width:8px;height:8px;border-radius:50%;display:inline-block}
h2.head{font-family:'Newsreader',Georgia,serif;font-weight:400;font-size:33px;
  line-height:1.22;letter-spacing:-.012em;margin:14px 0 0}
p.sub{color:var(--muted);font-size:16px;margin:12px 0 0;max-width:52ch}

/* the suggestion — the one thing that should catch the eye */
.suggest{margin:28px 0 0;padding:20px 22px;background:var(--panel);
  border-left:3px solid var(--accent)}
.suggest .act{font-family:'Newsreader',Georgia,serif;font-size:21px;
  color:var(--accent);margin:0 0 8px;line-height:1.3}
.suggest p{margin:0;font-size:15px;color:var(--ink);max-width:54ch}

/* three figures */
.figs{display:flex;flex-wrap:wrap;gap:30px;margin:30px 0 6px}
.fig{min-width:104px}
.fig b{display:block;font-family:'IBM Plex Mono',monospace;font-size:25px;
  font-weight:500;letter-spacing:-.02em;line-height:1.1}
.fig span{display:block;font-size:12.5px;color:var(--muted);margin-top:5px;
  max-width:15ch;line-height:1.4}

/* disclosure panels */
details{border-top:1px solid var(--rule-soft);background:var(--paper)}
.body{background:var(--paper)}
details:last-of-type{border-bottom:1px solid var(--rule-soft)}
summary{cursor:pointer;list-style:none;padding:15px 2px;font-size:15px;
  display:flex;align-items:center;gap:11px;color:var(--ink)}
summary::-webkit-details-marker{display:none}
summary:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
summary:hover{color:var(--accent)}
.chev{flex:0 0 9px;height:9px;border-right:1.5px solid var(--muted);
  border-bottom:1.5px solid var(--muted);transform:rotate(-45deg);
  transition:transform .18s ease;margin-left:2px}
details[open] .chev{transform:rotate(45deg)}
summary .n{margin-left:auto;font-family:'IBM Plex Mono',monospace;font-size:12px;
  color:var(--muted)}
.body{padding:2px 2px 26px;font-size:14.5px}
.panels{margin-top:24px}
figure.chart{margin:26px 0 4px;padding:0}
figure.chart.tight{margin:22px 0 0}
figure.chart figcaption{font-size:12.5px;color:var(--muted);margin-top:9px;
  line-height:1.5;max-width:56ch}
table.stats{margin-bottom:4px}
table.stats td{vertical-align:top;padding:10px 8px 10px 0}
table.stats td.k{font-weight:600;font-size:13.5px;white-space:nowrap}
table.stats td.k .f{display:block;font-family:'IBM Plex Mono',monospace;
  font-size:11.5px;color:var(--muted);font-weight:400;margin-top:3px}
table.stats td.v{font-family:'IBM Plex Mono',monospace;font-size:15px;
  text-align:right;white-space:nowrap;padding-right:14px}
table.stats td.m{font-size:13px;color:var(--muted);line-height:1.5}

/* score bar, now inside a panel */
.bar{display:flex;height:26px;border:1px solid var(--rule);margin:4px 0 12px}
.seg{min-width:0}
.unearned{flex:1;background-image:repeating-linear-gradient(135deg,
  transparent 0 5px,var(--rule) 5px 6px)}
.comp{display:flex;align-items:baseline;gap:10px;padding:9px 0;
  border-bottom:1px solid var(--rule-soft)}
.comp .sw{flex:0 0 9px;height:9px;margin-top:5px;border-radius:2px}
.comp .txt{flex:1}
.comp .nm{font-size:13.5px;font-weight:600}
.comp .ex{color:var(--muted);font-size:13px;margin-top:2px}
.comp .pts{font-family:'IBM Plex Mono',monospace;font-size:13px;color:var(--muted)}

.chk{display:flex;gap:11px;padding:9px 0;border-bottom:1px solid var(--rule-soft);
  align-items:baseline}
.chk .m{font-family:'IBM Plex Mono',monospace;font-size:11px;letter-spacing:.09em;
  flex:0 0 40px;font-weight:500}
.chk.ok .m{color:#1E5C4F}.chk.no .m{color:#9E2B21}
.chk .d{display:block;color:var(--muted);font-size:13px;margin-top:2px}

table{width:100%;border-collapse:collapse;font-size:14px}
th{font-size:11.5px;letter-spacing:.07em;text-transform:uppercase;color:var(--muted);
  text-align:left;font-weight:600;padding:0 6px 7px;border-bottom:1px solid var(--rule)}
td{padding:8px 6px;border-bottom:1px solid var(--rule-soft)}
td.num,th.num{text-align:right;font-family:'IBM Plex Mono',monospace;font-size:13px}
th.num{font-family:inherit}
.tk{font-family:'IBM Plex Mono',monospace;font-weight:500}
.dn{color:#9E2B21}

/* judgement — different surface, always labelled */
.judge{background:var(--judge-bg);color:var(--judge-ink);padding:20px 22px;
  border-left:3px solid var(--judge-mark);margin:4px 0 12px}
.judge .mark{font-family:'IBM Plex Mono',monospace;font-size:11px;letter-spacing:.13em;
  text-transform:uppercase;color:var(--judge-mark);margin-bottom:10px}
.judge h3{font-family:'Newsreader',Georgia,serif;font-weight:400;font-size:20px;
  margin:0 0 8px;line-height:1.3}
.judge p{margin:0 0 12px;font-size:14.5px}
.judge .chain{font-family:'IBM Plex Mono',monospace;font-size:13px;margin:0 0 14px}
.jrow{display:flex;flex-wrap:wrap;gap:24px;margin-top:6px}
.jrow div{font-family:'IBM Plex Mono',monospace}
.jrow b{display:block;font-size:19px;font-weight:500}
.jrow span{font-family:inherit;font-size:11.5px;letter-spacing:.05em;opacity:.85}
.judge.empty{background:var(--panel);border-left-color:var(--rule);color:var(--muted)}
.judge.empty .mark{color:var(--muted)}

.cand{padding:13px 0;border-bottom:1px solid var(--rule-soft)}
.cand .r{display:flex;align-items:baseline;gap:11px}
.cand .r .i{font-family:'IBM Plex Mono',monospace;font-size:12.5px;color:var(--muted);
  flex:0 0 16px}
.cand .r .t{font-family:'IBM Plex Mono',monospace;font-weight:500;font-size:15px}
.cand .r .nm{color:var(--muted);font-size:14px;flex:1}
.cand .r .s{font-family:'IBM Plex Mono',monospace;font-size:17px;font-weight:500}
.cand .why{margin:5px 0 0 27px;font-size:13.5px;color:var(--muted)}
.cand ul{margin:7px 0 0 27px;padding:0 0 0 15px;font-size:13px;color:var(--muted)}
.cand li{margin-bottom:4px}
.cand .cav{margin:6px 0 0 27px;font-size:13px;color:#9E2B21}

ul.plain{margin:0;padding-left:17px;font-size:14px;color:var(--muted)}
ul.plain li{margin-bottom:7px}
.note{font-size:13.5px;color:var(--muted);margin:0 0 14px}

.banner{margin:0 0 34px;padding:11px 14px;border:1px solid #9E2B21;color:#9E2B21;
  font-size:13.5px}
footer{margin-top:64px;padding-top:18px;border-top:1px solid var(--rule);
  font-size:12.5px;color:var(--muted);line-height:1.75}
@media (max-width:520px){
  .wrap{padding:30px 16px 70px}h2.head{font-size:26px}.status{font-size:19px}
  .figs{gap:22px}.fig b{font-size:21px}
}
@media (prefers-color-scheme:dark){
  html,body,.wrap,.event,details,.body,summary{
    background:var(--paper)!important;color:var(--ink)!important}
  h2.head,.status{color:var(--ink)!important}
  p.sub,.fig span,.note,summary .n,.comp .ex,.chk .d,ul.plain,footer,
  .cand .why,.cand ul,.cand .r .nm,td,th{color:var(--muted)!important}
  td,th,.comp .nm,.fig b{color:var(--ink)!important}
  .judge{background:var(--judge-bg)!important;color:var(--judge-ink)!important}
  .judge p,.judge h3,.judge .chain,.jrow{color:var(--judge-ink)!important}
  .suggest{background:var(--panel)!important}
  .suggest p{color:var(--ink)!important}
}
@media (prefers-reduced-motion:reduce){*{transition:none!important}}
</style></head><body><div class="wrap">

<header class="top">
  <div class="brand">DipDector &middot; {{ run.as_of }}</div>
  <p class="status">{{ run.status_line }}</p>
</header>

{% if run.synthetic %}
<div class="banner"><b>Synthetic data.</b> These numbers were generated to test the
pipeline. Nothing on this page is a real market observation.</div>
{% endif %}

{% for e in events %}
{% set a = e.assessment %}{% set m = a.metrics %}
<section class="event" style="--accent:{{ e.level.color }}">

  <span class="tag" style="color:{{ e.level.color }}">
    <span class="dot" style="background:{{ e.level.color }}"></span>{{ e.level.word }}</span>

  <h2 class="head">{{ e.headline }}</h2>
  <p class="sub">{{ e.subhead }} {{ e.unusualness }}</p>

  <div class="suggest">
    <p class="act">{{ e.action }}</p>
    <p>{{ e.rationale }}</p>
  </div>

  <div class="figs">
    {% for f in e.figures %}
    <div class="fig" title="{{ f.note }}">
      <b{% if '-' in f.value %} style="color:#9E2B21"{% endif %}>{{ f.value }}</b>
      <span>{{ f.label }}</span></div>
    {% endfor %}
  </div>

  {% if e.perf_chart %}
  <figure class="chart">
    {{ e.perf_chart|safe }}
    <figcaption>How the affected companies moved against the market over the
    last {{ e.chart_days }} trading days, rebased to zero. Shaded band is the
    {{ m.window }}-day detection window.</figcaption>
  </figure>
  {% endif %}

  <div class="panels">

  <details>
    <summary><span class="chev"></span>Why this fired
      <span class="n">{{ '%.0f'|format(a.score) }}/100</span></summary>
    <div class="body">
      <p class="note">The score adds up seven separate signals. The hatched part
      of the bar is what this event did <em>not</em> earn.</p>
      <div class="bar">
        {% for c in e.components %}{% if c.contribution > 0.05 %}
        <div class="seg" style="flex:0 0 {{ c.contribution }}%;background:{{ c.color }}"
             title="{{ c.name.replace('_',' ') }} — {{ '%.1f'|format(c.contribution) }}"></div>
        {% endif %}{% endfor %}
        <div class="unearned"></div>
      </div>
      {% for c in a.components|sort(attribute='contribution', reverse=true) %}
      <div class="comp">
        <span class="sw" style="background:{{ e.color_of[c.name] }}"></span>
        <span class="txt"><span class="nm">{{ c.name.replace('_',' ')|capitalize }}</span>
          <span class="ex">{{ c.explanation }}</span></span>
        <span class="pts">{{ '%.1f'|format(c.contribution) }}</span>
      </div>
      {% endfor %}
    </div>
  </details>

  <details>
    <summary><span class="chev"></span>Conditions this had to meet
      <span class="n">{{ e.n_passed }}/{{ a.triggers|length }}</span></summary>
    <div class="body">
      <p class="note">A high score alone is not an event. These conditions gate
      anything stronger than a watch.</p>
      {% for t in a.triggers %}
      <div class="chk {{ 'ok' if t.passed else 'no' }}">
        <span class="m">{{ 'MET' if t.passed else 'NOT MET' }}</span>
        <span>{{ t.label }}<span class="d">{{ t.detail }}</span></span>
      </div>
      {% endfor %}
    </div>
  </details>

  <details>
    <summary><span class="chev"></span>What caused it
      <span class="n">{{ 'not run' if e.event.is_stub else e.event.causes|join(', ') }}</span></summary>
    <div class="body">
      <div class="judge{{ ' empty' if e.event.is_stub }}">
        <div class="mark">{{ 'Not available' if e.event.is_stub else 'AI judgement — not a measurement' }}</div>
        {% if e.event.is_stub %}
          <p style="margin:0">{{ e.event.reasoning }}</p>
        {% else %}
          <h3>{{ e.event.title }}</h3>
          <div class="chain">{{ e.event.causal_chain }}</div>
          <p>{{ e.event.reasoning }}</p>
          <div class="jrow">
            <div><b>{{ e.event.severity }}</b><span>Severity</span></div>
            <div><b>{{ e.event.temporary_confidence }}</b><span>Looks temporary</span></div>
            <div><b>{{ e.event.structural_risk }}</b><span>Structural risk</span></div>
            <div><b>{{ e.event.continuation_risk }}</b><span>Could fall further</span></div>
          </div>
          {% if e.event.unresolved_questions %}
          <p style="margin:16px 0 0"><b>Couldn't determine:</b>
            {{ e.event.unresolved_questions|join('; ') }}</p>
          {% endif %}
        {% endif %}
      </div>
    </div>
  </details>

  <details>
    <summary><span class="chev"></span>Market statistics
      <span class="n">{{ 'β ' ~ '%.2f'|format(m.beta_to_market) if m.beta_to_market else 'raw' }}</span></summary>
    <div class="body">
      <table class="stats"><tbody>
      {% for st in e.stats %}
      <tr><td class="k">{{ st.term }}<span class="f">{{ st.formula }}</span></td>
          <td class="v">{{ st.value }}</td>
          <td class="m">{{ st.meaning }}</td></tr>
      {% endfor %}
      </tbody></table>
      {% if e.dist_chart %}
      <figure class="chart tight">{{ e.dist_chart|safe }}
        <figcaption>Every member's return over the window. A tight cluster
        points to one shared cause; a long tail means several different stories
        are being averaged together.</figcaption></figure>
      {% endif %}
    </div>
  </details>

  <details>
    <summary><span class="chev"></span>The companies
      <span class="n">{{ m.n_members }}</span></summary>
    <div class="body">
      <table><thead><tr><th>Ticker</th><th>Company</th>
        <th class="num">{{ m.window }}d</th><th class="num">vs S&amp;P</th>
        <th class="num">Volume</th><th class="num">Off high</th></tr></thead><tbody>
      {% for c in m.companies %}
      <tr><td class="tk">{{ c.ticker }}</td><td>{{ c.name }}</td>
        <td class="num dn">{{ '%+.1f'|format(c.returns[m.window]*100) }}%</td>
        <td class="num">{{ ('%+.1f'|format(c.rel_to_market*100)) ~ '%' if c.rel_to_market is not none else '—' }}</td>
        <td class="num">{{ ('%+.1f'|format(c.volume_z)) ~ 'σ' if c.volume_z is not none else '—' }}</td>
        <td class="num">{{ ('%.0f'|format(c.dist_from_52w_high*100)) ~ '%' if c.dist_from_52w_high is not none else '—' }}</td>
      </tr>{% endfor %}
      </tbody></table>
    </div>
  </details>

  {% if e.candidates %}
  <details>
    <summary><span class="chev"></span>If this turns out to be temporary
      <span class="n">{{ e.candidates|length }} ranked</span></summary>
    <div class="body">
      <p class="note">Which of these companies looks best placed to recover — <em>if</em>
      the cause turns out to be temporary. That condition is doing a lot of work,
      and this ranking cannot tell you whether it holds. Only
      {{ '%.0f'|format(e.coverage*100) }}% of the intended inputs are running;
      the rest need a fundamentals feed.</p>
      {% for c in e.candidates %}
      <div class="cand">
        <div class="r"><span class="i">{{ loop.index }}</span>
          <span class="t">{{ c.ticker }}</span><span class="nm">{{ c.name }}</span>
          <span class="s">{{ '%.0f'|format(c.score) }}</span></div>
        <div class="why">{{ c.summary }}</div>
        <ul>{% for f in c.factors|sort(attribute='contribution', reverse=true) %}
          <li>{{ f.reason }}</li>{% endfor %}</ul>
        {% for cv in c.caveats %}<div class="cav">{{ cv }}</div>{% endfor %}
      </div>
      {% endfor %}
    </div>
  </details>
  {% endif %}

  {% if a.notes %}
  <details>
    <summary><span class="chev"></span>Limits of this reading
      <span class="n">{{ a.notes|length }}</span></summary>
    <div class="body"><ul class="plain">
      {% for n in a.notes %}<li>{{ n }}</li>{% endfor %}</ul></div>
  </details>
  {% endif %}

  </div>
</section>
{% endfor %}

<footer>
Source: {{ run.source }}{% if run.synthetic %} — synthetic{% endif %} &middot;
parameters {{ run.params_version }}, not yet validated by backtesting &middot;
generated {{ run.generated_at }}<br>
Research output. Scores are model estimates, not predictions. A detected shock is
not a reason to buy anything.
</footer>
</div></body></html>""")


def status_line(events: List[dict], as_of: str) -> str:
    """The first sentence a reader sees. Plain, and honest when nothing happened."""
    if not events:
        return "Nothing unusual across the groups being monitored today."
    strong = [e for e in events
              if e["assessment"].level.value in ("MAJOR_EVENT", "INVESTIGATE")]
    if not strong:
        n = len(events)
        return (f"{n} group{'s' if n > 1 else ''} moving unusually, "
                f"{'none' if n > 1 else 'not'} strong enough to investigate yet.")
    names = [e["assessment"].industry for e in strong]
    if len(names) == 1:
        subject = names[0]
    elif len(names) == 2:
        subject = f"{names[0]} and {names[1]}"
    else:
        subject = f"{names[0]}, {names[1]} and {len(names) - 2} more"
    verb = "moved" if len(names) == 1 else "both moved" if len(names) == 2 else "moved"
    return f"{subject} {verb} far enough together to be worth a look."


def market_stats(m) -> List[dict]:
    """
    The statistics behind the score, with their definitions.

    Each row is term, formula, value, and what it means here. The formula is
    there so the number can be checked rather than trusted — every one of these
    is computed in engine/metrics.py and none of them come from a model.
    """
    rows = []
    if m.beta_to_market is not None:
        rows += [
            {"term": "Beta", "formula": "\u03b2 = Cov(r_i, r_m) / Var(r_m)",
             "value": f"{m.beta_to_market:.2f}",
             "meaning": (f"How much this group normally moves for a given market "
                         f"move, measured over the year before this window. "
                         f"{'Above 1 means it amplifies the market.' if m.beta_to_market > 1 else 'Below 1 means it is less market-sensitive than average.'}")},
            {"term": "R-squared", "formula": "R\u00b2 = \u03c1\u00b2(r_i, r_m)",
             "value": f"{m.r_squared:.2f}",
             "meaning": (f"The market explains {m.r_squared:.0%} of this group's "
                         f"normal day-to-day movement. The rest is industry-specific.")},
            {"term": "Expected return", "formula": "E[r_i] = \u03b2 \u00d7 r_m",
             "value": f"{m.expected_return:+.1%}",
             "meaning": (f"Given the S&P 500 at {m.market_return:+.1%}, this is "
                         f"the fall that would have been unremarkable.")},
            {"term": "Alpha", "formula": "\u03b1 = r_i \u2212 \u03b2 \u00d7 r_m",
             "value": f"{m.alpha:+.1%}",
             "meaning": ("The part of the fall the market cannot account for. "
                         "This is the actual signal — the raw gap against the "
                         "index flatters any high-beta group.")},
        ]
    else:
        rows.append(
            {"term": "Relative return", "formula": "r_i \u2212 r_m",
             "value": f"{m.relative_to_market:+.1%}",
             "meaning": ("Raw difference against the S&P 500. Beta was not "
                         "usable here, so this is not risk-adjusted and will "
                         "overstate the signal for a volatile group.")})

    if m.benchmark_return is not None:
        rows.append(
            {"term": f"Industry ETF ({m.benchmark_ticker})",
             "formula": "r_etf, and r_i \u2212 r_etf",
             "value": f"{m.benchmark_return:+.1%}",
             "meaning": (
                 f"{m.benchmark_name} over the same window"
                 + (f"; this group is {m.relative_to_benchmark:+.1%} against it. "
                    if m.relative_to_benchmark is not None else ". ")
                 + f"Overlap with our own universe is {m.benchmark_overlap}, so "
                   f"this corroborates rather than independently confirms.")})
    rows += [
        {"term": "Abnormality", "formula": "z = (r \u2212 \u03bc) / \u03c3",
         "value": f"{m.abnormality_z:.1f}\u03c3",
         "meaning": (f"Standard deviations below this group's own average "
                     f"{m.window}-day return over the past year. Scales the move "
                     f"by how volatile the group already was.")},
        {"term": "Mean correlation",
         "formula": "\u03c1\u0304 = mean \u03c1(r_a, r_b), all pairs",
         "value": (f"{m.mean_pairwise_correlation:.2f}"
                   if m.mean_pairwise_correlation == m.mean_pairwise_correlation else "\u2014"),
         "meaning": ("How closely members moved together. High correlation is "
                     "the signature of one shared cause rather than several "
                     "coincidences.")},
        {"term": "Dispersion", "formula": "\u03c3 of member returns",
         "value": f"{m.dispersion:.1%}",
         "meaning": ("Spread of individual outcomes. Low dispersion means the "
                     "event hit everyone about equally.")},
        {"term": "Breadth", "formula": "n declining / n members",
         "value": f"{m.pct_declining:.0%}",
         "meaning": (f"{m.n_declining} of {m.n_members} members fell at least 3%. "
                     f"The trigger requires a supermajority, not a fixed count.")},
        {"term": "Volume anomaly", "formula": "z = (V \u2212 \u03bc\u2086\u2080) / \u03c3\u2086\u2080",
         "value": f"{m.median_volume_z:+.1f}\u03c3",
         "meaning": ("Median volume against its 60-day norm. Conviction selling "
                     "shows up as volume; a drift on thin volume does not.")},
    ]
    return rows


def render(events: List[dict], run: dict) -> str:
    for e in events:
        a = e["assessment"]
        e["level"] = LEVEL.get(a.level.value, LEVEL["NONE"])
        e["headline"] = narrative.headline(a)
        e["subhead"] = narrative.subhead(a)
        e["unusualness"] = narrative.unusualness(a)
        e["action"], e["rationale"] = narrative.suggestion(a)
        e["figures"] = narrative.key_figures(a)
        e["n_passed"] = sum(1 for t in a.triggers if t.passed)
        e["color_of"] = COMPONENT_COLORS
        e["components"] = [
            type("C", (), {"name": c.name, "contribution": c.contribution,
                           "color": COMPONENT_COLORS.get(c.name, "#8B96A3")})()
            for c in a.components
        ]
        m = a.metrics
        e["stats"] = market_stats(m)
        close = e.get("close")
        if close is not None:
            e["perf_chart"] = charts.performance_chart(
                close, [c.ticker for c in m.companies],
                e.get("market", "^GSPC"), m.benchmark_ticker, m.window,
                benchmark_name=m.benchmark_name or "")
            e["dist_chart"] = charts.distribution_chart(
                m.companies, m.window, m.market_return, m.median_return)
            e["chart_days"] = min(60, len(close))
        else:
            e["perf_chart"] = e["dist_chart"] = ""
            e["chart_days"] = 0

        for c in e.get("candidates") or []:
            c.summary = narrative.candidate_summary(
                c, a.metrics.median_return, a.metrics.window)

    run = {**run, "status_line": status_line(events, run["as_of"])}
    return TEMPLATE.render(events=events, run=run)
