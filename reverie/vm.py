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
    proc: ProcInfo
    base: int
    params: list[int]
    ret_pc: int
    ret_dir: int
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
    ) -> None:
        self.program = program
        self.mem: list[int] = [0] * max(mem_size, program.n_globals + 64)
        self.mem_size = len(self.mem)
        self.stacks: list[list[int]] = [[] for _ in range(program.n_stacks)]
        self.frames: list[Frame] = []
        self.stats = Stats()
        self.history = HistoryTape(self.stats)
        self.output: list[str] = []
        self.fp = 0
        self.sp = program.n_globals
        self.pc = 0
        self.dir = 1
        self.halted = False
        self.max_steps = max_steps
        self.trace = trace
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
        self, info: ProcInfo, params: list[int], ret_pc: int, ret_dir: int
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
            Frame(info, base, params, ret_pc, ret_dir, self.fp, self.sp)
        )
        self.fp = base
        self.sp = need
        self.stats.calls += 1
        if len(self.frames) > self.stats.max_depth:
            self.stats.max_depth = len(self.frames)

    def leave_frame(self, at: int) -> None:
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
        self.pc = frame.ret_pc
        self.dir = frame.ret_dir

    # -- execution --------------------------------------------------------
    def current(self) -> Optional[Instr]:
        """The instruction that the next :meth:`step` will execute."""
        i = self.pc if self.dir > 0 else self.pc - 1
        if 0 <= i < len(self.program.code):
            return self.program.code[i]
        return None

    def step(self) -> bool:
        """Execute one instruction.  Returns False once the machine stops."""
        if self.halted:
            return False
        code = self.program.code
        if self.dir > 0:
            if self.pc >= len(code):
                self.halted = True
                return False
            ins = code[self.pc]
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
        if self.stats.steps > self.max_steps:
            raise RuntimeFault(
                f"step limit exceeded ({self.max_steps}); the program may not terminate",
                pc=self.pc,
            )
        if self.dir < 0 and self.pc <= 0:
            self.halted = True
        return not self.halted

    def run(self, limit: Optional[int] = None) -> "Machine":
        n = 0
        while self.step():
            n += 1
            if limit is not None and n >= limit:
                break
        return self

    def reverse(self) -> "Machine":
        """Flip the direction of time.  Free, because ``pc`` is a boundary."""
        self.dir = -self.dir
        self.halted = False
        return self

    # -- whole-program entry points ---------------------------------------
    def start_forward(self) -> "Machine":
        self.pc = 0
        self.dir = 1
        self.halted = False
        return self

    def start_backward(self) -> "Machine":
        """Position the machine to undo a completed forward run."""
        self.pc = self._halt_boundary()
        self.dir = -1
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
            "fp": self.fp,
            "sp": self.sp,
            "halted": self.halted,
            "mem": list(self.mem),
            "stacks": [list(s) for s in self.stacks],
            "history": list(self.history),
            "output": list(self.output),
            "frames": copy.deepcopy(self.frames),
        }

    def restore(self, snap: dict) -> "Machine":
        self.pc = snap["pc"]
        self.dir = snap["dir"]
        self.fp = snap["fp"]
        self.sp = snap["sp"]
        self.halted = snap["halted"]
        self.mem = list(snap["mem"])
        self.stacks = [list(s) for s in snap["stacks"]]
        self.history.clear()
        self.history.extend(snap["history"])
        self.output = list(snap["output"])
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
    if a["fp"] != b["fp"] or a["sp"] != b["sp"]:
        return False, f"frame pointers: {a['fp']},{a['sp']} != {b['fp']},{b['sp']}"
    return True, ""
