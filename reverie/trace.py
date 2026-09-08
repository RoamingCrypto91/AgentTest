"""Execution traces: a recording that can be scrubbed in either direction.

A trace is *not* how Reverie steps backwards -- the machine does that on its
own, with no history at all.  Traces exist so that something which is not the
machine (a web page, a diff, a report) can show what happened without
re-running anything.

Each frame records what changed, old value and new, so a viewer can move in
either direction by applying or unapplying deltas.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Optional

from .isa import Instr
from .vm import Machine, Program


@dataclass
class Frame:
    """One recorded step, as a delta."""

    step: int
    pc: int
    dir: int
    instr: str
    line: int
    file: str
    depth: int
    tape: int = 0
    #: cell -> (before, after)
    changed: dict[int, tuple[int, int]] = field(default_factory=dict)
    #: (stack index, "push" | "pop", value)
    stack_op: Optional[tuple[int, str, int]] = None
    #: (text, ends_the_line) when this step produced or consumed output
    emitted: Optional[tuple[str, bool]] = None
    #: True when the step *removed* output rather than adding it
    unemitted: bool = False


class StopRecording(Exception):
    """Raised internally once the frame budget is spent."""


class Recorder:
    """Records a run as a list of deltas, small enough to embed in a page."""

    def __init__(
        self,
        machine: Machine,
        *,
        watch: Optional[range] = None,
        max_steps: int = 20000,
    ) -> None:
        self.m = machine
        self.frames: list[Frame] = []
        self.watch = list(watch or range(0, max(machine.program.n_globals, 1)))
        self.max_steps = max_steps
        self.truncated = False
        self._snapshot_before()

    def _snapshot_before(self) -> None:
        m = self.m
        self.prev_mem = [m.mem[i] for i in self.watch]
        self.prev_stacks = [list(s) for s in m.stacks]
        self.prev_out = len(m.output)
        self.prev_line = m.line

    def record(self, m: Machine, ins: Instr) -> None:
        if len(self.frames) >= self.max_steps:
            self.truncated = True
            raise StopRecording()
        line, file = 0, ""
        if ins.span is not None:
            line = ins.span.source.line_col(ins.span.start)[0]
            file = ins.span.source.name
        self.frames.append(
            Frame(
                step=len(self.frames),
                pc=m.pc,
                dir=m.dir,
                instr=ins.render(),
                line=line,
                file=file,
                depth=len(m.frames),
                tape=len(m.history),
            )
        )

    def after_step(self) -> None:
        """Attach the deltas produced by the step just executed."""
        if not self.frames:
            return
        m = self.m
        frame = self.frames[-1]
        for k, i in enumerate(self.watch):
            v = m.mem[i]
            if v != self.prev_mem[k]:
                frame.changed[i] = (self.prev_mem[k], v)
                self.prev_mem[k] = v
        for idx, (before, now) in enumerate(zip(self.prev_stacks, m.stacks)):
            if len(now) > len(before):
                frame.stack_op = (idx, "push", now[-1])
            elif len(now) < len(before):
                frame.stack_op = (idx, "pop", before[-1])
            if len(now) != len(before):
                self.prev_stacks[idx] = list(now)
        if len(m.output) > self.prev_out:
            frame.emitted = (m.output[-1][len(self.prev_line) :], True)
        elif len(m.output) < self.prev_out:
            frame.unemitted = True
            frame.emitted = ("", True)
        elif m.line != self.prev_line:
            if len(m.line) > len(self.prev_line):
                frame.emitted = (m.line[len(self.prev_line) :], False)
            else:
                frame.unemitted = True
                frame.emitted = (self.prev_line[len(m.line) :], False)
        self.prev_out = len(m.output)
        self.prev_line = m.line

    # backwards-compatible alias
    snap_changes = after_step


def record_run(
    program: Program,
    initial: Optional[dict] = None,
    *,
    max_steps: int = 20000,
    mem_size: int = 4096,
    backward: bool = False,
) -> tuple[Machine, Recorder]:
    m = Machine(program, mem_size=mem_size, max_steps=50_000_000)
    if initial:
        m.set_globals(initial)
    if backward:
        # Recording a reversal only means anything from the end state, so get
        # there first -- without recording -- and then walk it back.
        m.start_forward().run()
    rec = Recorder(m, max_steps=max_steps)
    m.trace = rec.record
    if backward:
        m.start_backward()
    else:
        m.start_forward()
    try:
        while True:
            alive = m.step()
            rec.after_step()
            if not alive:
                break
    except StopRecording:
        pass
    m.trace = None
    return m, rec


def collect_sources(program: Program, main_text: Optional[str] = None) -> dict:
    """Every source file the program's instructions come from.

    A program that imports the standard library has instructions from several
    files, so a viewer that only knows about one of them highlights the wrong
    lines.  Collecting them here keeps the trace self-describing.
    """
    out: dict[str, str] = {}
    if main_text is not None:
        out[program.source_name] = main_text
    for ins in program.code:
        if ins.span is not None:
            src = ins.span.source
            out.setdefault(src.name, src.text)
    return out


def to_payload(
    program: Program,
    machine: Machine,
    rec: Recorder,
    source: Optional[str] = None,
    initial: Optional[dict] = None,
) -> dict:
    globals_meta = [
        {"name": g.name, "addr": g.addr, "size": g.size, "kind": g.kind}
        for g in sorted(program.globals.values(), key=lambda g: g.addr)
    ]
    start = Machine(program, mem_size=max(program.n_globals + 8, 64))
    if initial:
        start.set_globals(initial)
    frames = []
    for f in rec.frames:
        item: dict = {
            "s": f.step,
            "pc": f.pc,
            "d": f.dir,
            "i": f.instr,
            "l": f.line,
            "f": f.file,
            "depth": f.depth,
            "tape": f.tape,
        }
        if f.changed:
            item["c"] = {str(k): list(v) for k, v in f.changed.items()}
        if f.stack_op:
            item["k"] = list(f.stack_op)
        if f.emitted:
            item["e"] = [f.emitted[0], 1 if f.emitted[1] else 0,
                         1 if f.unemitted else 0]
        frames.append(item)
    return {
        "name": program.source_name,
        "source": source,
        "sources": collect_sources(program, source),
        "globals": globals_meta,
        "n_globals": program.n_globals,
        "n_stacks": program.n_stacks,
        "start_mem": start.mem[: program.n_globals],
        "code": [
            {
                "at": i,
                "text": ins.render(),
                "line": (
                    ins.span.source.line_col(ins.span.start)[0]
                    if ins.span is not None
                    else 0
                ),
                "file": ins.span.source.name if ins.span is not None else "",
            }
            for i, ins in enumerate(program.code)
        ],
        "procs": [
            {
                "name": p.name,
                "entry": p.entry_at,
                "exit": p.exit_at,
                "frame": p.frame_size,
                "params": list(p.params),
                "doc": p.doc,
            }
            for p in program.procs.values()
        ],
        "frames": frames,
        "truncated": rec.truncated,
        "final": machine.globals_dict(),
        "output": machine.output,
        "stats": {
            "steps": machine.stats.steps,
            "position": machine.position,
            "calls": machine.stats.calls,
            "depth": machine.stats.max_depth,
            "tape_peak": machine.stats.history_peak,
            "tape_bits": machine.stats.history_bits,
            "tape_pushes": machine.stats.history_pushes,
            "tape_pops": machine.stats.history_pops,
            "bits_erased": machine.bits_erased,
        },
    }


def to_json(
    program: Program,
    machine: Machine,
    rec: Recorder,
    source: Optional[str] = None,
    initial: Optional[dict] = None,
) -> str:
    return json.dumps(
        to_payload(program, machine, rec, source, initial), separators=(",", ":")
    )
