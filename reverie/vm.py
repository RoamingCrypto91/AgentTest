"""The Reverie machine: a store, a control stack, and a direction.

The machine has exactly one bit of state that a classical VM lacks -- ``dir`` --
and lacks one thing a classical VM has: any way to throw information away.
Running a program backwards is not "replay from a log"; there is no log.  It is
the same instructions, executed by their inverses, in the opposite order.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Callable, Iterable, Optional

from .diagnostics import RuntimeFault
from .isa import Halt, Instr


@dataclass
class ProcInfo:
    """Static description of a procedure."""

    name: str
    entry_at: int = -1
    exit_at: int = -1
    frame_size: int = 0
    params: tuple[str, ...] = ()
    param_kinds: tuple[str, ...] = ()
    doc: str = ""

    @property
    def arity(self) -> int:
        return len(self.params)


@dataclass
class GlobalInfo:
    name: str
    addr: int
    size: int = 1
    kind: str = "int"  # int | array | stack
    stack_id: int = 0  # 1-based handle stored in the cell, for `stack` globals


@dataclass
class Program:
    """A linked Reverie program: flat code plus symbol tables."""

    code: list[Instr] = field(default_factory=list)
    procs: dict[str, ProcInfo] = field(default_factory=dict)
    globals: dict[str, GlobalInfo] = field(default_factory=dict)
    n_globals: int = 0
    n_stacks: int = 0
    entry: str = "main"
    source_name: str = "<program>"

    def finalize(self) -> "Program":
        for i, ins in enumerate(self.code):
            ins.at = i
        return self

    def disassemble(self, width: int = 0) -> str:
        lines = []
        proc_at = {}
        for name, info in self.procs.items():
            proc_at[info.entry_at] = name
        for i, ins in enumerate(self.code):
            if i in proc_at:
                lines.append("")
            lines.append(f"{i:5d}  {ins.render()}")
        return "\n".join(lines)

    def globals_layout(self) -> str:
        rows = sorted(self.globals.values(), key=lambda g: g.addr)
        return "\n".join(
            f"  @{g.addr:<4d} {g.name:<20s} {g.kind}{'[%d]' % g.size if g.size > 1 else ''}"
            for g in rows
        )


@dataclass
class Frame:
    """One activation.

    A frame does not record "where to return to" as a single address, because
    in Reverie you can enter a procedure going forwards and leave it going
    backwards.  It records the *call site* instead, and which end of the body
    the caller meant to start from.  Whichever end the machine eventually walks
    out of, the return is then determined:

    ==================  ==========  ==============================
    left through        entered by  outcome
    ==================  ==========  ==============================
    ``exit`` forwards   ``call``    the call completed  -> after it
    ``entry`` backwards ``call``    the call was undone -> before it
    ``entry`` backwards ``uncall``  the uncall completed -> after it
    ``exit`` forwards   ``uncall``  the uncall was undone -> before it
    ==================  ==========  ==============================
    """

    proc: ProcInfo
    base: int
    params: list[int]
    param_lens: list[int]
    call_at: int
    uncall: bool
    saved_fp: int
    saved_sp: int


@dataclass
class Stats:
    steps: int = 0
    forward_steps: int = 0
    backward_steps: int = 0
    history_pushes: int = 0
    history_pops: int = 0
    history_peak: int = 0
    history_bits: int = 0
    calls: int = 0
    max_depth: int = 0

    def merge_report(self) -> str:
        return (
            f"steps={self.steps} (fwd={self.forward_steps} bwd={self.backward_steps}) "
            f"calls={self.calls} depth={self.max_depth} "
            f"tape_peak={self.history_peak} tape_bits={self.history_bits}"
        )


class HistoryTape(list):
    """A list that reports pushes/pops to the machine's statistics.

    Reverie's whole thermodynamic story lives here.  Everything the machine
    would otherwise have had to forget is written on this tape, and Bennett's
    construction guarantees the tape is empty again when an ``embed`` block
    finishes -- so the number of bits actually erased is zero.
    """

    __slots__ = ("stats",)

    def __init__(self, stats: Stats) -> None:
        super().__init__()
        self.stats = stats

    def append(self, value: int) -> None:  # type: ignore[override]
        super().append(value)
        st = self.stats
        st.history_pushes += 1
        st.history_bits += max(1, int(value).bit_length())
        if len(self) > st.history_peak:
            st.history_peak = len(self)

    def pop(self, index: int = -1) -> int:  # type: ignore[override]
        self.stats.history_pops += 1
        return super().pop(index)


class Machine:
    """The bidirectional interpreter."""

    def __init__(
        self,
        program: Program,
        *,
        mem_size: int = 1 << 16,
        max_steps: int = 5_000_000,
        trace: Optional[Callable[["Machine", Instr], None]] = None,
        paranoid: bool = False,
    ) -> None:
        self.program = program
        self.mem: list[int] = [0] * max(mem_size, program.n_globals + 64)
        self.mem_size = len(self.mem)
        self.stacks: list[list[int]] = [[] for _ in range(program.n_stacks)]
        self.frames: list[Frame] = []
        self.stats = Stats()
        self.history = HistoryTape(self.stats)
        self.output: list[str] = []
        #: partial line built by `write` and flushed by `print`
        self.line = ""
        self.fp = 0
        self.sp = program.n_globals
        self.pc = 0
        #: direction the *next instruction* is executed in.  ``uncall`` flips
        #: this without reversing time -- a procedure run backwards inside a
        #: program that is still going forwards.
        self.dir = 1
        #: the arrow of logical time.  Only ``reverse()`` flips this.
        self.arrow = 1
        #: logical position: +1 per forward step, -1 per backward step.  This
        #: is the coordinate the debugger's `goto` travels along.
        self.position = 0
        self.halted = False
        self.max_steps = max_steps
        self.trace = trace
        #: check every step for invertibility as it happens.  See `step`.
        self.paranoid = paranoid
        # stack-typed globals hold a 1-based stack id
        for g in program.globals.values():
            if g.kind == "stack":
                self.mem[g.addr] = g.stack_id

    # -- accessors --------------------------------------------------------
    @property
    def frame(self) -> Optional[Frame]:
        return self.frames[-1] if self.frames else None

    def stack_at(self, addr: int) -> list[int]:
        sid = self.mem[addr]
        if sid <= 0 or sid > len(self.stacks):
            raise RuntimeFault(
                f"cell @{addr} does not hold a stack handle (found {sid})", pc=self.pc
            )
        return self.stacks[sid - 1]

    # -- frames -----------------------------------------------------------
    def enter_frame(
        self,
        info: ProcInfo,
        params: list[int],
        param_lens: list[int],
        call_at: int,
        uncall: bool,
    ) -> None:
        base = self.sp
        need = base + info.frame_size
        if need > self.mem_size:
            raise RuntimeFault(
                f"out of memory: frame for `{info.name}` needs {need} cells, "
                f"machine has {self.mem_size}",
                pc=self.pc,
            )
        self.frames.append(
            Frame(info, base, params, param_lens, call_at, uncall, self.fp, self.sp)
        )
        self.fp = base
        self.sp = need
        self.stats.calls += 1
        if len(self.frames) > self.stats.max_depth:
            self.stats.max_depth = len(self.frames)

    def leave_frame(self, via: str, at: int) -> None:
        """Unwind one frame.  *via* is ``"exit"`` or ``"entry"``."""
        if not self.frames:
            raise RuntimeFault("return with no active frame", pc=at)
        frame = self.frames.pop()
        base = frame.base
        for i in range(frame.proc.frame_size):
            if self.mem[base + i] != 0:
                raise RuntimeFault(
                    f"procedure `{frame.proc.name}` leaks: frame cell {i} holds "
                    f"{self.mem[base + i]} at exit",
                    pc=at,
                    notes=[
                        "every `local` must be matched by a `delocal` that restores",
                        "the cell to zero -- otherwise leaving the frame would erase it",
                    ],
                )
        self.fp = frame.saved_fp
        self.sp = frame.saved_sp
        completed = (via == "exit") != frame.uncall
        if completed:
            self.pc, self.dir = frame.call_at + 1, 1
        else:
            self.pc, self.dir = frame.call_at, -1

    # -- execution --------------------------------------------------------
    def current(self) -> Optional[Instr]:
        """The instruction that the next :meth:`step` will execute."""
        i = self.pc if self.dir > 0 else self.pc - 1
        if 0 <= i < len(self.program.code):
            return self.program.code[i]
        return None

    def step(self) -> bool:
        """Execute one instruction.

        Returns True if an instruction actually ran.  A False result means the
        machine has nothing left to do in the current direction -- it reached
        ``halt`` going forwards, or the very beginning of the program going
        backwards.

        In paranoid mode each step is checked as it happens: the machine takes
        the step, undoes it, verifies that everything is exactly as it was, and
        redoes it.  That is the property the test suite proves for generated
        programs, available as a switch for real ones -- three times the work
        and a lot of copying, but nothing about a program can hide from it.
        """
        if self.paranoid:
            return self._checked_step()
        return self._step()

    # -- the paranoid check -----------------------------------------------
    def fingerprint(self) -> tuple:
        """Everything a step could have changed, cheap enough to compare.

        Only cells below the stack pointer are live, so the frames above it are
        not part of the machine's state and are excluded.
        """
        return (
            self.pc,
            self.fp,
            self.sp,
            tuple(self.mem[: self.sp]),
            tuple(tuple(s) for s in self.stacks),
            tuple(self.history),
            len(self.output),
            self.output[-1] if self.output else None,
            self.line,
            tuple((f.proc.name, f.base, tuple(f.params)) for f in self.frames),
        )

    def _checked_step(self) -> bool:
        before = self.fingerprint()
        pc0, dir0, arrow0 = self.pc, self.dir, self.arrow
        if not self._step():
            return False
        # Everything after this point is bookkeeping about a step that has
        # already happened, so the statistics are frozen here and restored at
        # the end: the check must not show up in what the machine reports.
        stats1 = _stat_tuple(self.stats)
        position1 = self.position
        after = self.fingerprint()
        pc1, dir1, arrow1 = self.pc, self.dir, self.arrow
        instr = self.program.code[pc0 if dir0 > 0 else pc0 - 1]

        self.reverse()
        self.halted = False
        self._step()
        if self.fingerprint() != before:
            raise RuntimeFault(
                f"`{instr.render()}` is not invertible: undoing it did not "
                f"restore the machine",
                pc=instr.at,
                notes=[_first_difference(before, self.fingerprint())],
            )
        if self.dir != -dir0:
            raise RuntimeFault(
                f"`{instr.render()}` left the machine facing the wrong way "
                f"when undone",
                pc=instr.at,
            )

        self.dir, self.arrow = dir0, arrow0
        self.halted = False
        self._step()
        if self.fingerprint() != after or (self.dir, self.arrow) != (dir1, arrow1):
            raise RuntimeFault(
                f"`{instr.render()}` is not deterministic: repeating it from "
                f"the same state gave a different result",
                pc=instr.at,
                notes=[_first_difference(after, self.fingerprint())],
            )
        _restore_stats(self.stats, stats1)
        self.position = position1
        self.dir, self.arrow = dir1, arrow1
        return True

    def _step(self) -> bool:
        if self.halted:
            return False
        code = self.program.code
        if self.dir > 0:
            if self.pc >= len(code):
                self.halted = True
                return False
            ins = code[self.pc]
            if type(ins) is Halt:
                # `halt` is a terminator, not a reversible instruction.  Not
                # counting it keeps logical time symmetric: a complete forward
                # run and its complete reversal have the same length.
                self.halted = True
                return False
        else:
            if self.pc <= 0:
                self.halted = True
                return False
            ins = code[self.pc - 1]
        if self.trace is not None:
            self.trace(self, ins)
        if self.dir > 0:
            ins.forward(self)
            self.stats.forward_steps += 1
        else:
            ins.backward(self)
            self.stats.backward_steps += 1
        self.stats.steps += 1
        self.position += self.arrow
        if self.stats.steps > self.max_steps:
            raise RuntimeFault(
                f"step limit exceeded ({self.max_steps}); the program may not terminate",
                pc=self.pc,
            )
        if self.dir < 0 and self.pc <= 0:
            self.halted = True
        return True

    def run(self, limit: Optional[int] = None) -> "Machine":
        n = 0
        while self.step():
            n += 1
            if limit is not None and n >= limit:
                break
        return self

    def reverse(self) -> "Machine":
        """Flip the direction of time.  Free, because ``pc`` is a boundary.

        There is no bookkeeping to do and no history to rewind: ``pc`` names a
        point *between* instructions, so the same value is valid in either
        direction.  Flipping two signs is the whole operation.
        """
        self.dir = -self.dir
        self.arrow = -self.arrow
        self.halted = False
        return self

    # -- whole-program entry points ---------------------------------------
    def start_forward(self) -> "Machine":
        self.pc = 0
        self.dir = 1
        self.arrow = 1
        self.halted = False
        return self

    def start_backward(self) -> "Machine":
        """Position the machine to undo a completed forward run."""
        self.pc = self._halt_boundary()
        self.dir = -1
        self.arrow = -1
        self.halted = False
        return self

    def _halt_boundary(self) -> int:
        for i, ins in enumerate(self.program.code):
            if isinstance(ins, Halt):
                return i
        return len(self.program.code)

    # -- invariants -------------------------------------------------------
    def check_clean(self, where: str = "program exit") -> None:
        """Assert the machine ends in a physically tidy state."""
        problems = []
        if self.history:
            problems.append(f"history tape holds {len(self.history)} unreclaimed entries")
        if self.line:
            problems.append(f"a line of output was never finished: {self.line!r}")
        if self.frames:
            problems.append(f"{len(self.frames)} frames still open")
        if self.sp != self.program.n_globals:
            problems.append(f"stack pointer is {self.sp}, expected {self.program.n_globals}")
        for i in range(self.program.n_globals, self.mem_size):
            if self.mem[i] != 0:
                problems.append(f"scratch cell @{i} holds {self.mem[i]}")
                break
        if problems:
            raise RuntimeFault(f"unclean state at {where}: " + "; ".join(problems))

    @property
    def bits_erased(self) -> int:
        """Bits the machine has irreversibly destroyed.  Always zero, by design."""
        return 0

    # -- state capture ----------------------------------------------------
    def snapshot(self) -> dict:
        return {
            "pc": self.pc,
            "dir": self.dir,
            "arrow": self.arrow,
            "position": self.position,
            "fp": self.fp,
            "sp": self.sp,
            "halted": self.halted,
            "mem": list(self.mem),
            "stacks": [list(s) for s in self.stacks],
            "history": list(self.history),
            "output": list(self.output),
            "line": self.line,
            "frames": copy.deepcopy(self.frames),
        }

    def restore(self, snap: dict) -> "Machine":
        self.pc = snap["pc"]
        self.dir = snap["dir"]
        self.arrow = snap.get("arrow", 1)
        self.position = snap.get("position", 0)
        self.fp = snap["fp"]
        self.sp = snap["sp"]
        self.halted = snap["halted"]
        self.mem = list(snap["mem"])
        self.stacks = [list(s) for s in snap["stacks"]]
        self.history.clear()
        self.history.extend(snap["history"])
        self.output = list(snap["output"])
        self.line = snap.get("line", "")
        self.frames = copy.deepcopy(snap["frames"])
        return self

    def globals_dict(self) -> dict:
        """Named view of global memory, for tests and the debugger."""
        out: dict[str, object] = {}
        for name, g in self.program.globals.items():
            if g.kind == "array":
                out[name] = self.mem[g.addr : g.addr + g.size]
            elif g.kind == "stack":
                out[name] = list(self.stacks[self.mem[g.addr] - 1])
            else:
                out[name] = self.mem[g.addr]
        return out

    def set_globals(self, values: dict) -> "Machine":
        for name, val in values.items():
            g = self.program.globals.get(name)
            if g is None:
                raise KeyError(f"no global named {name!r}")
            if g.kind == "array":
                seq = list(val)
                if len(seq) > g.size:
                    raise ValueError(f"{name} holds {g.size} elements, got {len(seq)}")
                for i, v in enumerate(seq):
                    self.mem[g.addr + i] = int(v)
            elif g.kind == "stack":
                self.stacks[self.mem[g.addr] - 1] = [int(v) for v in val]
            else:
                self.mem[g.addr] = int(val)
        return self

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        arrow = "->" if self.dir > 0 else "<-"
        return f"<Machine pc={self.pc}{arrow} steps={self.stats.steps}>"


#: the statistics a paranoid step must not disturb
_STAT_FIELDS = (
    "steps",
    "forward_steps",
    "backward_steps",
    "history_pushes",
    "history_pops",
    "history_peak",
    "history_bits",
    "calls",
    "max_depth",
)


def _stat_tuple(stats: Stats) -> tuple:
    return tuple(getattr(stats, name) for name in _STAT_FIELDS)


def _restore_stats(stats: Stats, saved: tuple) -> None:
    for name, value in zip(_STAT_FIELDS, saved):
        setattr(stats, name, value)


def _first_difference(a: tuple, b: tuple) -> str:
    """Name the first component of two fingerprints that differs."""
    names = (
        "pc", "fp", "sp", "memory", "stacks", "history tape",
        "output length", "last output line", "open line", "frames",
    )
    for name, x, y in zip(names, a, b):
        if x != y:
            if name == "memory":
                for i, (u, v) in enumerate(zip(x, y)):
                    if u != v:
                        return f"mem[{i}] was {u}, is now {v}"
                return f"memory length changed: {len(x)} -> {len(y)}"
            return f"{name} was {x!r}, is now {y!r}"
    return "nothing differs (the comparison itself is wrong)"


def state_equal(a: dict, b: dict) -> tuple[bool, str]:
    """Compare two snapshots, ignoring pc/dir.  Used by the round-trip fuzzer."""
    if a["mem"] != b["mem"]:
        for i, (x, y) in enumerate(zip(a["mem"], b["mem"])):
            if x != y:
                return False, f"mem[{i}]: {x} != {y}"
        return False, "memory length differs"
    if a["stacks"] != b["stacks"]:
        return False, f"stacks: {a['stacks']} != {b['stacks']}"
    if a["history"] != b["history"]:
        return False, f"history: {a['history']} != {b['history']}"
    if a["output"] != b["output"]:
        return False, f"output: {a['output']} != {b['output']}"
    if a.get("line", "") != b.get("line", ""):
        return False, f"open line: {a.get('line')!r} != {b.get('line')!r}"
    if a["fp"] != b["fp"] or a["sp"] != b["sp"]:
        return False, f"frame pointers: {a['fp']},{a['sp']} != {b['fp']},{b['sp']}"
    return True, ""
