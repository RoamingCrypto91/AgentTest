"""Execution traces: a recording of a run that can be scrubbed in either
direction.

A trace is *not* how Reverie steps backwards -- the machine does that on its
own.  Traces exist so that a debugger UI, a diff, or the HTML visualizer can
show what happened without re-running anything.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Optional

from .isa import Instr
from .vm import Machine, Program


@dataclass
class Frame:
    """One recorded step."""

    step: int
    pc: int
    dir: int
    instr: str
    line: int
    depth: int
    changed: dict[int, int] = field(default_factory=dict)
    tape: int = 0
    output: int = 0


class Recorder:
    """Records a run as a list of deltas, small enough to embed in a web page."""

    def __init__(
        self,
        machine: Machine,
        *,
        watch: Optional[range] = None,
        max_steps: int = 20000,
    ) -> None:
        self.m = machine
        self.frames: list[Frame] = []
        self.watch = watch or range(0, machine.program.n_globals)
        self.max_steps = max_steps
        self.prev = [machine.mem[i] for i in self.watch]
        self.truncated = False

    def record(self, m: Machine, ins: Instr) -> None:
        if len(self.frames) >= self.max_steps:
            self.truncated = True
            raise StopRecording()
        line = 0
        if ins.span is not None:
            line = ins.span.source.line_col(ins.span.start)[0]
        self.frames.append(
            Frame(
                step=len(self.frames),
                pc=m.pc,
                dir=m.dir,
                instr=ins.render(),
                line=line,
                depth=len(m.frames),
                tape=len(m.history),
                output=len(m.output),
            )
        )

    def snap_changes(self) -> None:
        """Call after each step to attach the memory delta to the last frame."""
        if not self.frames:
            return
        changed = {}
        for k, i in enumerate(self.watch):
            v = self.m.mem[i]
            if v != self.prev[k]:
                changed[i] = v
                self.prev[k] = v
        self.frames[-1].changed = changed


class StopRecording(Exception):
    pass


def record_run(
    program: Program,
    initial: Optional[dict] = None,
    *,
    max_steps: int = 20000,
    mem_size: int = 4096,
    backward: bool = False,
) -> tuple[Machine, Recorder]:
    m = Machine(program, mem_size=mem_size)
    if initial:
        m.set_globals(initial)
    rec = Recorder(m, max_steps=max_steps)
    m.trace = rec.record
    if backward:
        m.start_backward()
    else:
        m.start_forward()
    try:
        while True:
            alive = m.step()
            rec.snap_changes()
            if not alive:
                break
    except StopRecording:
        pass
    m.trace = None
    return m, rec


def to_json(
    program: Program,
    machine: Machine,
    rec: Recorder,
    source: Optional[str] = None,
) -> str:
    globals_meta = [
        {"name": g.name, "addr": g.addr, "size": g.size, "kind": g.kind}
        for g in sorted(program.globals.values(), key=lambda g: g.addr)
    ]
    payload = {
        "source_name": program.source_name,
        "source": source,
        "globals": globals_meta,
        "n_globals": program.n_globals,
        "code": [
            {
                "at": i,
                "text": ins.render(),
                "line": (
                    ins.span.source.line_col(ins.span.start)[0]
                    if ins.span is not None
                    else 0
                ),
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
        "frames": [
            {
                "s": f.step,
                "pc": f.pc,
                "d": f.dir,
                "i": f.instr,
                "l": f.line,
                "depth": f.depth,
                "c": {str(k): v for k, v in f.changed.items()},
                "tape": f.tape,
                "out": f.output,
            }
            for f in rec.frames
        ],
        "truncated": rec.truncated,
        "final": machine.globals_dict(),
        "output": machine.output,
        "stats": {
            "steps": machine.stats.steps,
            "calls": machine.stats.calls,
            "tape_peak": machine.stats.history_peak,
            "tape_bits": machine.stats.history_bits,
            "bits_erased": machine.bits_erased,
        },
    }
    return json.dumps(payload, separators=(",", ":"))
