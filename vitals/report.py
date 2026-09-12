"""The report: the thing a client actually reads.

Written as one HTML fragment -- title, fonts, styles, then content -- which a
browser renders as a standalone file and the artifact publisher accepts
unchanged.

The design carries one idea: a community is a chain, and the report's job is
to point at the link that is broken.  So the chain is the first thing on the
page, the broken link is marked on it, and everything below is either
evidence for that verdict or the three things to do about it.  Every measure
is drawn on the same component -- a track running from failing to fine, with
the value marked and typical marked -- because nineteen differently-shaped
charts would hide the one comparison that matters.
"""

from __future__ import annotations

import html
from datetime import datetime, timedelta, timezone

from . import benchmarks
from .model import FAILING, HOLDING, STRAINED, UNKNOWN

VERDICT_WORDS = {
    HOLDING: "holding",
    STRAINED: "strained",
    FAILING: "failing",
    UNKNOWN: "not measurable",
}

STYLE = """
:root{
  --ground:#f6f8f6; --surface:#ffffff; --sunk:#eef1ee;
  --ink:#16232b; --ink-soft:#4a5b61; --ink-faint:#7b8a8e;
  --edge:#d6dcd8; --edge-soft:#e6eae6;
  --accent:#2f6f6a; --accent-soft:#dcebe8;
  --holding:#3d7a4e; --strained:#a8701c; --failing:#a3403c;
  --track:#e2e7e3;
  color-scheme:light dark;
}
@media (prefers-color-scheme:dark){
  :root:not([data-theme="light"]){
    --ground:#111719; --surface:#192024; --sunk:#141b1e;
    --ink:#e6ece8; --ink-soft:#a7b5b6; --ink-faint:#7d8c8e;
    --edge:#2b373b; --edge-soft:#222c30;
    --accent:#64b5ac; --accent-soft:#1d3a38;
    --holding:#63b077; --strained:#d09a45; --failing:#d1706b;
    --track:#263236;
  }
}
:root[data-theme="dark"]{
  --ground:#111719; --surface:#192024; --sunk:#141b1e;
  --ink:#e6ece8; --ink-soft:#a7b5b6; --ink-faint:#7d8c8e;
  --edge:#2b373b; --edge-soft:#222c30;
  --accent:#64b5ac; --accent-soft:#1d3a38;
  --holding:#63b077; --strained:#d09a45; --failing:#d1706b;
  --track:#263236;
}
*{box-sizing:border-box}
body{
  margin:0; background:var(--ground); color:var(--ink);
  font:400 16px/1.6 "IBM Plex Sans","Helvetica Neue",Arial,sans-serif;
  -webkit-font-smoothing:antialiased;
}
.page{max-width:940px; margin:0 auto; padding-block:40px 72px; padding-left:20px; padding-right:20px;}
h1,h2,h3{font-family:Bitter,Georgia,"Times New Roman",serif; text-wrap:balance; margin:0; font-weight:600;}
h1{font-size:2.1rem; line-height:1.15; letter-spacing:-0.01em}
h2{font-size:1.3rem; line-height:1.25}
h3{font-size:1.02rem; line-height:1.3}
p{margin:0}
a{color:var(--accent)}
.eyebrow{
  font:500 11px/1 "IBM Plex Mono",ui-monospace,monospace;
  letter-spacing:.16em; text-transform:uppercase; color:var(--ink-faint);
}
.prose{max-width:64ch; color:var(--ink-soft)}
.stack{display:flex; flex-direction:column; gap:14px}
.wide{display:flex; flex-direction:column; gap:44px}
header.mast{
  display:flex; justify-content:space-between; align-items:flex-end;
  gap:20px; flex-wrap:wrap; border-bottom:2px solid var(--ink);
  padding-bottom:16px; margin-bottom:36px;
}
header.mast .meta{
  font:400 12px/1.5 "IBM Plex Mono",ui-monospace,monospace;
  color:var(--ink-faint); text-align:right;
}

/* the chain */
.chain{display:flex; gap:0; flex-wrap:wrap; align-items:stretch}
.link{
  flex:1 1 150px; min-width:0; padding:14px 16px 16px; background:var(--surface);
  border:1px solid var(--edge); border-left-width:0; position:relative;
}
.link:first-child{border-left-width:1px}
.link .n{
  font:500 10px/1 "IBM Plex Mono",ui-monospace,monospace;
  letter-spacing:.14em; color:var(--ink-faint);
}
.link .name{font-family:Bitter,Georgia,serif; font-weight:600; font-size:1rem; margin-top:6px}
.link .state{
  margin-top:10px; font:500 11px/1 "IBM Plex Mono",ui-monospace,monospace;
  letter-spacing:.1em; text-transform:uppercase;
}
.link::after{content:""; position:absolute; inset:0 0 auto 0; height:3px; background:var(--track)}
.link.holding::after{background:var(--holding)}
.link.strained::after{background:var(--strained)}
.link.failing::after{background:var(--failing)}
.link.holding .state{color:var(--holding)}
.link.strained .state{color:var(--strained)}
.link.failing .state{color:var(--failing)}
.link.unknown .state{color:var(--ink-faint)}
.link.bind{background:var(--sunk); box-shadow:inset 0 0 0 2px var(--failing)}
.link.bind .n::after{content:" · binding"; color:var(--failing)}

/* verdict */
.verdict{
  display:grid; grid-template-columns:minmax(0,1.35fr) minmax(0,1fr); gap:32px;
  background:var(--surface); border:1px solid var(--edge); padding:28px;
}
.verdict .figure{
  font:600 3.1rem/1 "IBM Plex Mono",ui-monospace,monospace;
  font-variant-numeric:tabular-nums; letter-spacing:-0.02em;
}
.verdict .figure.bad{color:var(--failing)}
.verdict .figure.ok{color:var(--holding)}
.verdict .caption{color:var(--ink-soft); font-size:.92rem; margin-top:8px}

/* actions */
.actions{display:flex; flex-direction:column; gap:2px}
.action{
  display:grid; grid-template-columns:44px minmax(0,1fr); gap:0 20px;
  background:var(--surface); border:1px solid var(--edge); padding:22px 24px;
}
.action + .action{border-top-width:0}
.action .rank{
  font:600 1.5rem/1 "IBM Plex Mono",ui-monospace,monospace; color:var(--accent);
}
.action h3{margin-bottom:10px}
.action .mech{color:var(--ink-soft); font-size:.94rem; margin-top:10px}
.action .check{
  margin-top:14px; padding-top:12px; border-top:1px dashed var(--edge);
  font:400 12px/1.5 "IBM Plex Mono",ui-monospace,monospace; color:var(--ink-faint);
}

/* measures */
.gate{border-top:1px solid var(--ink); padding-top:18px}
.gate .q{color:var(--ink-soft); max-width:62ch; margin-top:4px}
.rows{margin-top:20px; display:flex; flex-direction:column; gap:0}
.row{
  display:grid; grid-template-columns:minmax(0,1fr) 120px 150px; gap:16px;
  align-items:center; padding:12px 0; border-bottom:1px solid var(--edge-soft);
}
.row .label{font-size:.95rem}
.row .label small{display:block; color:var(--ink-faint); font-size:.78rem; margin-top:2px}
.row .value{
  text-align:right; font:500 1.05rem/1 "IBM Plex Mono",ui-monospace,monospace;
  font-variant-numeric:tabular-nums;
}
.row .value.holding{color:var(--holding)}
.row .value.strained{color:var(--strained)}
.row .value.failing{color:var(--failing)}
.row .value.unknown{color:var(--ink-faint); font-size:.8rem}
.row.support .label{color:var(--ink-soft)}
.track{position:relative; height:8px; background:var(--track); border-radius:1px}
.track .mark{position:absolute; top:-3px; width:3px; height:14px; background:var(--ink)}
.track .typ{position:absolute; top:-1px; width:1px; height:10px; background:var(--ink-faint)}
.track .fill{position:absolute; left:0; top:0; bottom:0; border-radius:1px}
.track .fill.holding{background:var(--holding)}
.track .fill.strained{background:var(--strained)}
.track .fill.failing{background:var(--failing)}
.scalelab{
  display:grid; grid-template-columns:minmax(0,1fr) 120px 150px; gap:16px;
  font:400 10px/1 "IBM Plex Mono",ui-monospace,monospace; color:var(--ink-faint);
  letter-spacing:.08em; text-transform:uppercase; padding-bottom:6px;
}
.scalelab .ends{display:flex; justify-content:space-between}

/* lists */
.cols{display:grid; grid-template-columns:repeat(auto-fit,minmax(250px,1fr)); gap:24px}
.panel{background:var(--surface); border:1px solid var(--edge); padding:20px 22px}
.panel h3{margin-bottom:4px}
.panel .why{color:var(--ink-soft); font-size:.88rem; margin-bottom:14px}
.names{
  list-style:none; margin:0; padding:0;
  font:400 13px/1.7 "IBM Plex Mono",ui-monospace,monospace;
}
.names li{display:flex; justify-content:space-between; gap:12px; border-bottom:1px solid var(--edge-soft); padding:3px 0}
.names li span:last-child{color:var(--ink-faint); font-variant-numeric:tabular-nums}
.empty{color:var(--ink-faint); font-size:.9rem}

/* change table */
table.change{width:100%; border-collapse:collapse; font-size:.93rem}
table.change th{
  text-align:left; font:500 10px/1 "IBM Plex Mono",ui-monospace,monospace;
  letter-spacing:.12em; text-transform:uppercase; color:var(--ink-faint);
  padding:0 12px 8px 0; border-bottom:1px solid var(--edge);
}
table.change td{padding:9px 12px 9px 0; border-bottom:1px solid var(--edge-soft);
  font-variant-numeric:tabular-nums}
table.change td.num{text-align:right; font-family:"IBM Plex Mono",ui-monospace,monospace}
table.change td.up{color:var(--holding)}
table.change td.down{color:var(--failing)}
.scroller{overflow-x:auto}

.sample{
  border:1px solid var(--strained); border-left-width:4px; padding:14px 18px;
  color:var(--ink-soft); font-size:.92rem; background:var(--surface);
}
.sample b{color:var(--strained)}
.sample code{font:400 .86rem "IBM Plex Mono",ui-monospace,monospace}
figure.spark{margin:0; display:flex; flex-direction:column; gap:10px}
figure.spark svg{width:100%; height:auto; display:block}
figure.spark figcaption{color:var(--ink-faint); font-size:.84rem; max-width:60ch}
footer.method{
  border-top:1px solid var(--edge); padding-top:20px; color:var(--ink-faint);
  font-size:.86rem; display:flex; flex-direction:column; gap:10px;
}
footer.method b{color:var(--ink-soft); font-weight:500}
@media (max-width:640px){
  .verdict{grid-template-columns:minmax(0,1fr)}
  .row,.scalelab{grid-template-columns:minmax(0,1fr) 84px; }
  .row .track,.scalelab .ends{display:none}
  .link{flex-basis:100%; border-left-width:1px; border-top-width:0}
  .link:first-child{border-top-width:1px}
}
@media print{
  body{background:#fff}
  .page{padding:0}
  .action,.panel,.verdict,.link{break-inside:avoid}
}
@media (prefers-reduced-motion:reduce){*{animation:none!important; transition:none!important}}
"""


def esc(text) -> str:
    return html.escape(str(text), quote=True)


def fmt(measure) -> str:
    if not measure.measured:
        return "not measurable"
    v = measure.value
    if measure.unit == "share":
        return f"{v * 100:.0f}%"
    if measure.unit == "hours":
        return f"{v:.0f}h" if v < 72 else f"{v / 24:.1f}d"
    if measure.unit == "ratio":
        return f"{v:+.0%}" if measure.key.endswith("trend") else f"{v:.2f}x"
    if measure.unit == "index":
        return f"{v:.2f}"
    return f"{v:,.0f}" if v >= 10 else f"{v:.1f}"


def _position(finding) -> float:
    """Where the value sits on the failing-to-fine track, as a percentage."""
    if finding.score is None:
        return 0.0
    return max(2.0, min(98.0, finding.score * 100))


def _typical(finding) -> float:
    band = finding.band
    if band is None or band.typical is None:
        return -1.0
    return max(0.0, min(100.0, band.score(band.typical, finding.measure.good) * 100))


def _row(finding, support: bool = False) -> str:
    m = finding.measure
    verdict = finding.verdict
    cls = "row support" if support else "row"
    if finding.score is None:
        track = '<div class="track"></div>'
    else:
        pos = _position(finding)
        typ = _typical(finding)
        tick = f'<div class="typ" style="left:{typ:.1f}%"></div>' if typ >= 0 else ""
        track = (
            f'<div class="track">'
            f'<div class="fill {verdict}" style="width:{pos:.1f}%"></div>'
            f'{tick}<div class="mark" style="left:calc({pos:.1f}% - 1px)"></div>'
            f"</div>"
        )
    note = m.basis or ""
    if not m.measured and m.unavailable:
        note = m.unavailable
    return (
        f'<div class="{cls}">'
        f'<div class="label">{esc(m.label)}<small>{esc(note)}</small></div>'
        f'<div class="value {verdict}">{esc(fmt(m))}</div>'
        f"{track}</div>"
    )


def _chain(audit) -> str:
    binding = audit.binding
    out = []
    for i, result in enumerate(audit.assessment["gates"], start=1):
        bind = " bind" if binding is not None and result is binding else ""
        out.append(
            f'<div class="link {result.verdict}{bind}">'
            f'<div class="n">stage {i}</div>'
            f'<div class="name">{esc(result.gate.name)}</div>'
            f'<div class="state">{esc(VERDICT_WORDS[result.verdict])}</div>'
            f"</div>"
        )
    return f'<div class="chain">{"".join(out)}</div>'


def _verdict(audit) -> str:
    binding = audit.binding
    if binding is None:
        return (
            '<section class="verdict"><div class="stack">'
            '<div class="eyebrow">verdict</div>'
            "<h2>Every gate is holding</h2>"
            '<p class="prose">Nothing here is leaking badly enough to be worth a '
            "project. The work now is keeping the measurements running so that "
            "when something does start to slide, you see it in the month it "
            "happens rather than the quarter after.</p>"
            '</div><div class="stack"><div class="figure ok">&#10003;</div>'
            '<p class="caption">No binding constraint found in this window.</p>'
            "</div></section>"
        )
    worst = binding.worst
    figure = fmt(worst.measure) if worst else "&mdash;"
    label = worst.measure.label if worst else ""
    return (
        '<section class="verdict"><div class="stack">'
        '<div class="eyebrow">binding constraint</div>'
        f"<h2>{esc(binding.gate.name)}: {esc(binding.gate.question)}</h2>"
        f'<p class="prose">{esc(binding.gate.mechanism)}</p>'
        f'<p class="prose"><b>{esc(audit.plan["reason"])}</b></p>'
        f'</div><div class="stack">'
        f'<div class="figure bad">{figure}</div>'
        f'<p class="caption">{esc(label)}<br>{esc(worst.measure.basis if worst else "")}</p>'
        "</div></section>"
    )


def _actions(audit) -> str:
    plan = audit.plan
    items = list(plan["plan"])
    if not items and not plan["housekeeping"]:
        return ""
    out = []
    for i, action in enumerate(items, start=1):
        moves = ", ".join(
            audit.measures[k].label.lower() for k in action.moves if k in audit.measures
        )
        out.append(
            f'<div class="action"><div class="rank">{i}</div><div>'
            f"<h3>{esc(action.title)}</h3>"
            f'<p class="prose">{esc(action.do)}</p>'
            f'<p class="mech"><b>Why it works.</b> {esc(action.mechanism)}</p>'
            f'<p class="check">re-measure in {action.review_days} days &middot; '
            f"should move: {esc(moves)} &middot; effort: {esc(action.effort)}</p>"
            f"</div></div>"
        )
    for action in plan["housekeeping"]:
        out.append(
            f'<div class="action"><div class="rank">&#8901;</div><div>'
            f"<h3>{esc(action.title)}</h3>"
            f'<p class="prose">{esc(action.do)}</p>'
            f'<p class="mech"><b>Why it works.</b> {esc(action.mechanism)}</p>'
            f'<p class="check">housekeeping &middot; '
            f"re-measure in {action.review_days} days</p>"
            f"</div></div>"
        )
    held = len(plan.get("held_back", ()))
    tail = (
        f'<p class="prose">{held} further intervention'
        f'{"s" if held != 1 else ""} for this gate are held back on purpose. '
        "Three things get done; ten things get discussed.</p>" if held else ""
    )
    return (
        '<section class="stack"><div class="eyebrow">what to do, in this order</div>'
        "<h2>Three things, all aimed at the same gate</h2>"
        f'<div class="actions">{"".join(out)}</div>{tail}</section>'
    )


def _measures(audit) -> str:
    blocks = []
    gates = list(audit.assessment["gates"]) + [audit.assessment["structure"]]
    for result in gates:
        rows = [_row(f) for f in result.findings]
        rows += [_row(f, support=True) for f in result.supporting]
        blocks.append(
            f'<div class="gate"><h3>{esc(result.gate.name)}</h3>'
            f'<p class="q">{esc(result.gate.question)}</p>'
            '<div class="scalelab"><div></div><div style="text-align:right">measured</div>'
            '<div class="ends"><span>failing</span><span>fine</span></div></div>'
            f'<div class="rows">{"".join(rows)}</div></div>'
        )
    return (
        '<section class="stack"><div class="eyebrow">the measurements</div>'
        "<h2>Every number, and what it is out of</h2>"
        '<p class="prose">The tick on each track is what communities of this kind '
        "typically manage. The bar is where this one sits between failing and "
        "fine.</p>"
        f'<div class="wide" style="gap:30px; margin-top:8px">{"".join(blocks)}</div>'
        "</section>"
    )


def _names(audit) -> str:
    def panel(title, why, people, right, empty) -> str:
        if not people:
            return (f'<div class="panel"><h3>{esc(title)}</h3>'
                    f'<p class="why">{esc(why)}</p>'
                    f'<p class="empty">{esc(empty)}</p></div>')
        items = "".join(
            f"<li><span>{esc(p.name)}</span><span>{esc(right(p))}</span></li>"
            for p in people
        )
        return (f'<div class="panel"><h3>{esc(title)}</h3>'
                f'<p class="why">{esc(why)}</p>'
                f'<ul class="names">{items}</ul></div>')

    graves = audit.graveyards
    if graves:
        items = "".join(
            f"<li><span>{esc(room)}</span><span>"
            f'{"never used" if days is None else f"{days}d quiet"}</span></li>'
            for room, days in graves
        )
        grave_panel = (
            '<div class="panel"><h3>Rooms to close or merge</h3>'
            '<p class="why">Every one of these is evidence to a newcomer that '
            "nobody lives here.</p>"
            f'<ul class="names">{items}</ul></div>'
        )
    else:
        grave_panel = ('<div class="panel"><h3>Rooms to close or merge</h3>'
                       '<p class="why">Every room in the export has recent '
                       "conversation in it.</p>"
                       '<p class="empty">Nothing to close.</p></div>')

    return (
        '<section class="stack"><div class="eyebrow">names, not categories</div>'
        "<h2>The people and places to act on this week</h2>"
        f'<div class="cols">'
        + panel("Recruit these members", "They already talk to more people than "
                "anyone else here. Give them a title and one job each.",
                audit.connectors, lambda p: f"{p.partners} partners",
                "Nobody here talks to enough people to hold the place up yet. "
                "That is the finding.")
        + panel("Introduce these members", "They keep coming back and have met "
                "almost nobody. An introduction is the whole fix.",
                audit.quiet_regulars, lambda p: f"{p.weeks} weeks, {p.messages} posts",
                "Nobody is turning up regularly and going unintroduced.")
        + grave_panel
        + "</div></section>"
    )


def _sparkline(audit) -> str:
    """Weekly posters over the window: the only picture in the report.

    Drawn to one scale, with the highest and final weeks labelled so every
    label names a value the chart actually reaches.
    """
    series = audit.weekly
    if len(series) < 4:
        return ""
    values = [p["posters"] for p in series]
    top = max(values) or 1
    w, h = 880.0, 150.0
    pad_l, pad_r, pad_t, pad_b = 44.0, 56.0, 18.0, 26.0
    span = w - pad_l - pad_r
    tall = h - pad_t - pad_b
    step = span / max(1, len(values) - 1)

    points = [
        (pad_l + i * step, pad_t + tall - (v / top) * tall)
        for i, v in enumerate(values)
    ]
    line = " ".join(f"{x:.1f},{y:.1f}" for x, y in points)
    area = (f"{pad_l:.1f},{pad_t + tall:.1f} " + line +
            f" {pad_l + (len(values) - 1) * step:.1f},{pad_t + tall:.1f}")
    peak = values.index(top)
    last_x, last_y = points[-1]
    mid_x = pad_l + (len(values) - 1) * step / 2

    return f"""<figure class="spark">
  <svg viewBox="0 0 {w:.0f} {h:.0f}" role="img"
       aria-label="Distinct members posting each week, {series[0]['week']} to {series[-1]['week']}">
    <polyline points="{area}" fill="var(--accent-soft)" stroke="none"></polyline>
    <polyline points="{line}" fill="none" stroke="var(--accent)" stroke-width="2"
              stroke-linejoin="round"></polyline>
    <line x1="{pad_l:.1f}" y1="{pad_t + tall:.1f}" x2="{pad_l + span:.1f}"
          y2="{pad_t + tall:.1f}" stroke="var(--edge)" stroke-width="1"></line>
    <line x1="{mid_x:.1f}" y1="{pad_t:.1f}" x2="{mid_x:.1f}" y2="{pad_t + tall:.1f}"
          stroke="var(--ink-faint)" stroke-width="1" stroke-dasharray="3 4"></line>
    <text x="{mid_x + 5:.1f}" y="{pad_t + 10:.1f}" fill="var(--ink-faint)"
          font-size="11" font-family="IBM Plex Mono, monospace">halfway</text>
    <circle cx="{points[peak][0]:.1f}" cy="{points[peak][1]:.1f}" r="3"
            fill="var(--accent)"></circle>
    <text x="{pad_l - 8:.1f}" y="{pad_t + 4:.1f}" fill="var(--ink-soft)" font-size="12"
          text-anchor="end" font-family="IBM Plex Mono, monospace">{top}</text>
    <text x="{pad_l - 8:.1f}" y="{pad_t + tall + 4:.1f}" fill="var(--ink-faint)"
          font-size="12" text-anchor="end"
          font-family="IBM Plex Mono, monospace">0</text>
    <circle cx="{last_x:.1f}" cy="{last_y:.1f}" r="4" fill="var(--ink)"></circle>
    <text x="{last_x + 9:.1f}" y="{last_y + 4:.1f}" fill="var(--ink)" font-size="12"
          font-family="IBM Plex Mono, monospace">{values[-1]}</text>
    <text x="{pad_l:.1f}" y="{h - 8:.0f}" fill="var(--ink-faint)" font-size="11"
          font-family="IBM Plex Mono, monospace">{esc(series[0]['week'])}</text>
    <text x="{pad_l + span:.1f}" y="{h - 8:.0f}" fill="var(--ink-faint)" font-size="11"
          text-anchor="end" font-family="IBM Plex Mono, monospace">{esc(series[-1]['week'])}</text>
  </svg>
  <figcaption>Distinct members posting each week. Staff excluded, so this is the
    community talking rather than the community being talked at.</figcaption>
</figure>"""


def _changes(audit) -> str:
    spark = _sparkline(audit)
    if not audit.changes and not spark:
        return ""
    rows = []
    for c in audit.changes:
        measure = audit.measures[c.key]
        direction = "up" if c.better else "down"
        rel = "" if c.relative is None else f"{c.relative:+.0%}"
        rows.append(
            f"<tr><td>{esc(c.label)}</td>"
            f'<td class="num">{esc(_val(measure, c.before))}</td>'
            f'<td class="num">{esc(_val(measure, c.after))}</td>'
            f'<td class="num {direction}">{esc(rel)}</td></tr>'
        )
    return (
        '<section class="stack"><div class="eyebrow">direction of travel</div>'
        "<h2>What has moved since the first half of this window</h2>"
        '<p class="prose">Both halves are measured with the same code, so a '
        "difference here is a difference in the community rather than in the "
        "method. Only changes of more than a tenth are listed.</p>"
        f"{spark}"
        '<div class="scroller"><table class="change"><thead><tr>'
        "<th>Measure</th><th>First half</th><th>Second half</th><th>Change</th>"
        f'</tr></thead><tbody>{"".join(rows)}</tbody></table></div></section>'
    )


def _val(measure, value: float) -> str:
    shim = type(measure)(measure.key, measure.label, value, measure.unit)
    shim.good = measure.good
    return fmt(shim)


def _method(audit) -> str:
    community = audit.community
    start, end = community.span()
    unknown = [m for m in audit.assessment["unknowns"]]
    lines = [
        "<p><b>No message content was read.</b> This instrument works from "
        "metadata only: who posted, where, when, and who they were answering. "
        "Nobody's words were stored or examined to produce any number above.</p>",
        f"<p><b>Window.</b> {esc(start.date())} to {esc(end.date())}, "
        f"{len(community.messages):,} messages from {len(community.humans()):,} "
        f"people across {len(community.surfaces)} rooms.</p>",
    ]
    if audit.benchmark_source == "judgement":
        lines.append(
            "<p><b>Benchmarks are provisional.</b> The typical values marked on "
            "each track are set by judgement, not measured from a sample. They "
            "are good enough to tell a failing measure from a fine one and not "
            "good enough to quote as a percentile. They get replaced as audits "
            "accumulate.</p>"
        )
    else:
        lines.append(
            f"<p><b>Benchmarks.</b> Measured, from "
            f"{esc(audit.benchmark_source)}.</p>"
        )
    if unknown:
        items = "; ".join(
            f"{esc(m.label.lower())} ({esc(m.unavailable)})" for m in unknown
        )
        lines.append(f"<p><b>Could not be measured.</b> {items}.</p>")
    for note in community.notes:
        lines.append(f"<p>{esc(note)}</p>")
    return f'<footer class="method">{"".join(lines)}</footer>'


def _sample(community) -> str:
    """Say so, loudly, when the numbers are from a simulation.

    A report full of plausible figures about a plausible community is exactly
    the thing that should never be mistaken for a real one.
    """
    if community.platform != "synthetic":
        return ""
    return (
        '<div class="sample"><b>This is a sample.</b> Every figure below comes '
        "from a community generated by <code>vitals.simulate</code> from an "
        "explicit model of behaviour. No real community, and no real person, "
        "appears anywhere on this page.</div>"
    )


def render(audit, title: str = "") -> str:
    community = audit.community
    start, end = community.span()
    name = community.name or "this community"
    heading = title or f"{name}: community vitals"
    generated = datetime.now(timezone.utc).date()
    return f"""<title>{esc(heading)}</title>
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Bitter:wght@500;600&family=IBM+Plex+Mono:wght@400;500;600&family=IBM+Plex+Sans:wght@400;500&display=swap">
<style>{STYLE}</style>
<div class="page wide">
  {_sample(community)}
  <header class="mast">
    <div class="stack" style="gap:6px">
      <div class="eyebrow">community vitals &middot; diagnostic</div>
      <h1>{esc(name)}</h1>
    </div>
    <div class="meta">
      {esc(start.date())} &rarr; {esc(end.date())}<br>
      {len(community.messages):,} messages &middot; {len(community.humans()):,} people<br>
      issued {esc(generated)}
    </div>
  </header>

  <section class="stack">
    <div class="eyebrow">the chain</div>
    <h2>{esc(audit.headline)}</h2>
    <p class="prose">A member passes through five stages: they arrive, they say
      something, they come back, they come to know people, they start doing the
      work, and eventually they hold the place up. Each stage can leak. Fixing a
      later one is wasted effort while an earlier one is leaking, so only the
      earliest failure is worth spending on.</p>
    {_chain(audit)}
  </section>

  {_verdict(audit)}
  {_actions(audit)}
  {_changes(audit)}
  {_names(audit)}
  {_measures(audit)}
  {_method(audit)}
</div>
"""
