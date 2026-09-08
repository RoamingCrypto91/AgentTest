"""``rev viz`` -- build a self-contained page for scrubbing an execution.

The page has no dependencies and no network access: the whole trace is
embedded, and the scrubber applies or unapplies deltas as you drag it.  That
mirrors what the machine itself does, except that the machine does not need the
deltas -- it recomputes them.
"""

from __future__ import annotations

import html
import json
from typing import Optional

from .trace import record_run, to_payload
from .vm import Program

PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>
:root {
  color-scheme: light dark;
  --bg: #fbfaf7;
  --panel: #ffffff;
  --ink: #1b1a17;
  --dim: #6a6760;
  --line: #e3e0d8;
  --accent: #b4530a;
  --accent-soft: #fdf0e4;
  --back: #4a5fa5;
  --back-soft: #eaeefb;
  --grid: #f4f2ed;
  --mono: ui-monospace, "SF Mono", "Cascadia Mono", Menlo, Consolas, monospace;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #15161a;
    --panel: #1c1e23;
    --ink: #e7e5e0;
    --dim: #8d8b85;
    --line: #2c2f36;
    --accent: #f0913f;
    --accent-soft: #3a2a18;
    --back: #8fa5e8;
    --back-soft: #23283a;
    --grid: #202329;
  }
}
* { box-sizing: border-box; }
html, body { height: 100%; }
body {
  margin: 0; background: var(--bg); color: var(--ink);
  font: 14px/1.5 var(--mono);
  display: flex; flex-direction: column;
}
#body { flex: 1 1 auto; overflow: auto; min-height: 0; }
header {
  padding: 14px 18px; border-bottom: 1px solid var(--line);
  display: flex; flex-wrap: wrap; gap: 6px 22px; align-items: baseline;
  flex: 0 0 auto;
}
header h1 { font-size: 15px; margin: 0; font-weight: 600; letter-spacing: .01em; }
header .name { color: var(--accent); }
header .stat { color: var(--dim); font-size: 12px; }
header .stat b { color: var(--ink); font-weight: 600; }
main {
  display: grid; gap: 1px; background: var(--line);
  grid-template-columns: minmax(0, 1.15fr) minmax(0, 1fr);
  border-bottom: 1px solid var(--line);
}
@media (max-width: 820px) { main { grid-template-columns: 1fr; } }
section { background: var(--panel); padding: 12px 14px; min-width: 0; }
section h2 {
  font-size: 11px; text-transform: uppercase; letter-spacing: .09em;
  color: var(--dim); margin: 0 0 8px; font-weight: 600;
}
.scroll { max-height: 44vh; overflow: auto; }
.tabs { display: flex; gap: 4px; margin-bottom: 6px; flex-wrap: wrap; }
.tab { font-size: 11px; padding: 1px 8px; border-radius: 999px; cursor: pointer;
       border: 1px solid var(--line); background: var(--panel); color: var(--dim); }
.tab.on { border-color: var(--accent); color: var(--accent); }
pre { margin: 0; white-space: pre; }
.srcline { display: grid; grid-template-columns: 3.2em 1fr; }
.srcline .n { color: var(--dim); text-align: right; padding-right: 10px; user-select: none; }
.srcline.on { background: var(--accent-soft); }
.srcline.on .n { color: var(--accent); font-weight: 700; }
.srcline.back.on { background: var(--back-soft); }
.srcline.back.on .n { color: var(--back); }
.code { display: grid; grid-template-columns: 4em 1fr; gap: 0 10px; }
.code .a { color: var(--dim); text-align: right; }
.code .row.on { background: var(--accent-soft); }
.code .row.on .a { color: var(--accent); font-weight: 700; }
.code .row { display: contents; }
table { border-collapse: collapse; width: 100%; font-size: 13px; }
td, th { padding: 2px 8px 2px 0; text-align: left; vertical-align: top; }
th { color: var(--dim); font-weight: 600; font-size: 11px;
     text-transform: uppercase; letter-spacing: .08em; }
td.v { font-variant-numeric: tabular-nums; }
tr.hit td { background: var(--accent-soft); }
.cells { display: flex; flex-wrap: wrap; gap: 3px; }
.cell {
  min-width: 2.6em; padding: 1px 5px; text-align: right; border-radius: 3px;
  background: var(--grid); font-variant-numeric: tabular-nums;
}
.cell.hit { background: var(--accent); color: var(--panel); }
.meter { height: 6px; background: var(--grid); border-radius: 3px; overflow: hidden; }
.meter > i { display: block; height: 100%; background: var(--accent); }
.out {
  background: var(--grid); border-radius: 4px; padding: 8px 10px;
  min-height: 3.4em; white-space: pre-wrap; word-break: break-word;
}
.out .cursor { border-left: 2px solid var(--accent); margin-left: 1px; }
footer { padding: 12px 18px 14px; background: var(--bg); flex: 0 0 auto;
         border-top: 1px solid var(--line); }
.transport { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
button {
  font: inherit; font-size: 13px; padding: 4px 11px; border-radius: 5px;
  border: 1px solid var(--line); background: var(--panel); color: var(--ink);
  cursor: pointer;
}
button:hover { border-color: var(--accent); color: var(--accent); }
button.primary { background: var(--accent); color: var(--bg); border-color: var(--accent); }
button.primary:hover { color: var(--bg); filter: brightness(1.08); }
input[type=range] { flex: 1 1 240px; accent-color: var(--accent); min-width: 160px; }
.clock { color: var(--dim); font-variant-numeric: tabular-nums; white-space: nowrap; }
.clock b { color: var(--ink); }
.arrow { display: inline-block; width: 1.4em; text-align: center; font-weight: 700; }
.arrow.fwd { color: var(--accent); }
.arrow.rev { color: var(--back); }
.hint { color: var(--dim); font-size: 12px; margin-top: 8px; }
.badge { display: inline-block; padding: 0 6px; border-radius: 999px;
         background: var(--grid); color: var(--dim); font-size: 11px; }
</style>
</head>
<body>
<header>
  <h1>reverie &middot; <span class="name">__NAME__</span></h1>
  <span class="stat">steps <b id="s-steps"></b></span>
  <span class="stat">instructions <b id="s-code"></b></span>
  <span class="stat">tape peak <b id="s-tape"></b></span>
  <span class="stat">bits erased <b id="s-erased"></b></span>
  <span class="stat" id="s-trunc"></span>
</header>

<div id="body">
<main>
  <section>
    <h2>source</h2>
    <div class="tabs" id="tabs"></div>
    <div class="scroll" id="source"></div>
  </section>
  <section>
    <h2>state</h2>
    <table id="globals"></table>
    <h2 style="margin-top:14px">stacks</h2>
    <div id="stacks"></div>
    <h2 style="margin-top:14px">history tape <span class="badge" id="tape-n"></span></h2>
    <div class="meter"><i id="tape-bar" style="width:0"></i></div>
    <p class="hint" id="tape-note"></p>
    <h2 style="margin-top:14px">output</h2>
    <div class="out" id="output"></div>
  </section>
</main>

<main>
  <section>
    <h2>bytecode</h2>
    <div class="scroll" id="code"></div>
  </section>
  <section>
    <h2>call stack</h2>
    <div id="frames"></div>
    <h2 style="margin-top:14px">next instruction</h2>
    <div class="out" id="instr"></div>
  </section>
</main>
</div>

<footer>
  <div class="transport">
    <button id="home" title="Home">&#124;&#9664;</button>
    <button id="back10">&#9664;&#9664;</button>
    <button id="back">&#9664; step</button>
    <button class="primary" id="play">play</button>
    <button id="fwd">step &#9654;</button>
    <button id="fwd10">&#9654;&#9654;</button>
    <button id="end" title="End">&#9654;&#124;</button>
    <input type="range" id="scrub" min="0" value="0">
    <span class="clock"><span class="arrow" id="arrow"></span>t = <b id="t"></b>/<span id="tmax"></span></span>
  </div>
  <p class="hint">
    Drag the scrubber, or use &larr; &rarr; to step, shift+&larr;/&rarr; to jump ten,
    space to play, home/end for the extremes.
    Time runs both ways: dragging left is not a replay, it is the reverse execution.
  </p>
</footer>

<script id="trace" type="application/json">__DATA__</script>
<script>
const D = JSON.parse(document.getElementById("trace").textContent);
const F = D.frames, N = F.length;

// ---- rebuild the state at any point by applying or unapplying deltas -------
const mem = D.start_mem.slice();
const stacks = Array.from({length: D.n_stacks}, () => []);
let outLines = [], outLine = "", t = 0;

function applyFrame(f, dir) {
  if (f.c) for (const k in f.c) mem[+k] = dir > 0 ? f.c[k][1] : f.c[k][0];
  if (f.k) {
    const [idx, op, val] = f.k;
    const push = (op === "push") === (dir > 0);
    if (push) stacks[idx].push(val); else stacks[idx].pop();
  }
  if (f.e) {
    const [text, endsLine, undo] = f.e;
    const producing = (dir > 0) !== !!undo;
    if (producing) {
      if (endsLine) { outLines.push(outLine + text); outLine = ""; }
      else outLine += text;
    } else {
      if (endsLine) {
        const last = outLines.pop() || "";
        outLine = last.slice(0, last.length - text.length);
      } else outLine = outLine.slice(0, outLine.length - text.length);
    }
  }
}

function goto(target) {
  target = Math.max(0, Math.min(N, target));
  while (t < target) { applyFrame(F[t], 1); t++; }
  while (t > target) { t--; applyFrame(F[t], -1); }
  render();
}

// ---- static scaffolding ---------------------------------------------------
const srcEl = document.getElementById("source");
const tabsEl = document.getElementById("tabs");
const files = Object.keys(D.sources || {});
if (!files.length && D.source) { D.sources = {[D.name]: D.source}; files.push(D.name); }
let shownFile = files.includes(D.name) ? D.name : files[0];
let pinnedFile = null;

function shortName(f) { return f.split("/").pop(); }
function renderTabs() {
  tabsEl.innerHTML = files.map(f =>
    `<span class="tab${f === shownFile ? " on" : ""}" data-file="${escapeHtml(f)}">` +
    `${escapeHtml(shortName(f))}</span>`).join("");
  tabsEl.querySelectorAll(".tab").forEach(el => el.onclick = () => {
    pinnedFile = el.dataset.file; showFile(el.dataset.file); render();
  });
}
function showFile(f) {
  shownFile = f;
  const lines = (D.sources[f] || "").split("\\n");
  srcEl.innerHTML = lines.map((l, i) =>
    `<div class="srcline" data-line="${i + 1}"><span class="n">${i + 1}</span>` +
    `<pre>${escapeHtml(l) || " "}</pre></div>`).join("");
  renderTabs();
}
showFile(shownFile);

const codeEl = document.getElementById("code");
codeEl.innerHTML = `<div class="code">` + D.code.map(c =>
  `<div class="row" data-at="${c.at}"><span class="a">${c.at}</span>` +
  `<pre>${escapeHtml(c.text)}</pre></div>`).join("") + `</div>`;

document.getElementById("s-steps").textContent = N;
document.getElementById("s-code").textContent = D.code.length;
document.getElementById("s-tape").textContent = D.stats.tape_peak;
document.getElementById("s-erased").textContent = D.stats.bits_erased;
document.getElementById("tmax").textContent = N;
document.getElementById("scrub").max = N;
if (D.truncated) document.getElementById("s-trunc").textContent =
  "(trace truncated)";

const tapePeak = Math.max(1, D.stats.tape_peak);
document.getElementById("tape-note").textContent = D.stats.tape_pushes
  ? `${D.stats.tape_pushes} entries written, ${D.stats.tape_pops} reclaimed, ` +
    `${D.stats.bits_erased} bits erased`
  : "this program never needed the tape";

function escapeHtml(s) {
  return s.replace(/[&<>]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;"}[c]));
}

// ---- rendering ------------------------------------------------------------
let lastLine = -1, lastPc = -1;
function render() {
  const f = t < N ? F[t] : null;
  const prev = t > 0 ? F[t - 1] : null;
  const line = f ? f.l : (prev ? prev.l : 0);
  const file = (f ? f.f : (prev ? prev.f : "")) || shownFile;
  const pc = f ? f.pc : (prev ? prev.pc : 0);
  const want = pinnedFile || file;
  if (want !== shownFile && D.sources[want] !== undefined) { showFile(want); lastLine = -1; }
  const dir = f ? f.d : 1;
  const changed = new Set();
  if (prev && prev.c) for (const k in prev.c) changed.add(+k);

  document.querySelectorAll(".srcline.on").forEach(e => e.classList.remove("on", "back"));
  const sl = (file === shownFile) ? srcEl.querySelector(`[data-line="${line}"]`) : null;
  if (sl) { sl.classList.add("on"); if (dir < 0) sl.classList.add("back");
            if (line !== lastLine) sl.scrollIntoView({block: "nearest"}); }
  lastLine = line;

  document.querySelectorAll(".code .row.on").forEach(e => e.classList.remove("on"));
  const target = dir > 0 ? pc : pc - 1;
  const cr = codeEl.querySelector(`[data-at="${target}"]`);
  if (cr) { cr.classList.add("on");
            if (target !== lastPc) cr.scrollIntoView({block: "nearest"}); }
  lastPc = target;

  let rows = "<tr><th>name</th><th>value</th></tr>";
  for (const g of D.globals) {
    if (g.kind === "stack") continue;
    const hit = [...Array(g.size).keys()].some(i => changed.has(g.addr + i));
    if (g.kind === "array") {
      const cells = [...Array(g.size).keys()].map(i =>
        `<span class="cell${changed.has(g.addr + i) ? " hit" : ""}">${mem[g.addr + i]}</span>`
      ).join("");
      rows += `<tr><td>${g.name}</td><td><div class="cells">${cells}</div></td></tr>`;
    } else {
      rows += `<tr class="${hit ? "hit" : ""}"><td>${g.name}</td>` +
              `<td class="v">${mem[g.addr]}</td></tr>`;
    }
  }
  document.getElementById("globals").innerHTML = rows;

  const stackNames = D.globals.filter(g => g.kind === "stack");
  document.getElementById("stacks").innerHTML = stackNames.length
    ? stackNames.map((g, i) => {
        const s = stacks[mem[g.addr] - 1] || [];
        const cells = s.map(v => `<span class="cell">${v}</span>`).join("") ||
                      `<span class="badge">empty</span>`;
        return `<div style="margin-bottom:4px">${g.name} <div class="cells">${cells}</div></div>`;
      }).join("")
    : `<span class="badge">none</span>`;

  const tape = f ? f.tape : (prev ? prev.tape : 0);
  document.getElementById("tape-n").textContent = tape + " entries";
  document.getElementById("tape-bar").style.width =
    (100 * Math.min(1, tape / tapePeak)).toFixed(1) + "%";

  document.getElementById("output").innerHTML =
    escapeHtml(outLines.join("\\n") + (outLines.length && outLine ? "\\n" : "") + outLine) +
    (outLine ? '<span class="cursor"></span>' : "");

  const depth = f ? f.depth : (prev ? prev.depth : 0);
  document.getElementById("frames").innerHTML =
    depth ? Array.from({length: depth}, (_, i) =>
      `<span class="cell">#${depth - i}</span>`).join(" ")
          : `<span class="badge">outside any procedure</span>`;
  document.getElementById("instr").textContent =
    f ? f.i : "— end of the run —";

  document.getElementById("t").textContent = t;
  document.getElementById("scrub").value = t;
  const arrow = document.getElementById("arrow");
  arrow.textContent = dir > 0 ? "\\u2192" : "\\u2190";
  arrow.className = "arrow " + (dir > 0 ? "fwd" : "rev");
}

// ---- transport ------------------------------------------------------------
let timer = null;
function stop() { clearInterval(timer); timer = null;
                  document.getElementById("play").textContent = "play"; }
function play() {
  if (timer) return stop();
  document.getElementById("play").textContent = "pause";
  timer = setInterval(() => { if (t >= N) return stop(); goto(t + 1); }, 45);
}
document.getElementById("play").onclick = play;
document.getElementById("fwd").onclick = () => { stop(); goto(t + 1); };
document.getElementById("back").onclick = () => { stop(); goto(t - 1); };
document.getElementById("fwd10").onclick = () => { stop(); goto(t + 10); };
document.getElementById("back10").onclick = () => { stop(); goto(t - 10); };
document.getElementById("home").onclick = () => { stop(); goto(0); };
document.getElementById("end").onclick = () => { stop(); goto(N); };
document.getElementById("scrub").oninput = e => { stop(); goto(+e.target.value); };
addEventListener("keydown", e => {
  if (e.key === "ArrowRight") { stop(); goto(t + (e.shiftKey ? 10 : 1)); }
  else if (e.key === "ArrowLeft") { stop(); goto(t - (e.shiftKey ? 10 : 1)); }
  else if (e.key === " ") { e.preventDefault(); play(); }
  else if (e.key === "Home") { stop(); goto(0); }
  else if (e.key === "End") { stop(); goto(N); }
  else return;
  e.preventDefault();
});
render();
</script>
</body>
</html>
"""


def build_page(
    program: Program,
    source,
    initial: Optional[dict] = None,
    *,
    limit: int = 20000,
    mem_size: int = 4096,
) -> str:
    text = source.text if hasattr(source, "text") else (source or "")
    machine, rec = record_run(
        program, initial, max_steps=limit, mem_size=mem_size
    )
    payload = to_payload(program, machine, rec, text, initial)
    data = json.dumps(payload, separators=(",", ":")).replace("</", "<\\/")
    name = html.escape(program.source_name)
    return (
        PAGE.replace("__DATA__", data)
        .replace("__NAME__", name)
        .replace("__TITLE__", f"reverie — {name}")
    )
