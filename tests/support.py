"""Helpers shared by the Reverie test modules."""

from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from reverie import rir  # noqa: E402
from reverie.ir import AbsA, Bin, Const, Load  # noqa: E402
from reverie.isa import Halt  # noqa: E402
from reverie.rir import CodeBuilder, RStmt  # noqa: E402
from reverie.vm import GlobalInfo, Machine, ProcInfo, Program, state_equal  # noqa: E402
from reverie.isa import Call, ProcEntry, ProcExit  # noqa: E402

EXAMPLES = os.path.join(ROOT, "examples")
STDLIB = os.path.join(ROOT, "stdlib")


def g(name_or_addr, addr: int | None = None):
    """``g('x', 3)`` -> a Load of global cell 3 rendered as ``@x``."""
    if addr is None:
        return Load(AbsA(name_or_addr))
    return Load(AbsA(addr, name_or_addr))


def build(body: RStmt, n_globals: int = 16, frame_size: int = 8, globals_=None) -> Program:
    """Wrap an RIR statement into a runnable single-procedure program."""
    b = CodeBuilder()
    call_at = b.emit(Call("main", []))
    b.emit(Halt())
    entry = b.emit(ProcEntry("main"))
    body.lower(b)
    exit_at = b.emit(ProcExit("main"))
    prog = Program(code=b.code, n_globals=n_globals)
    prog.procs["main"] = ProcInfo("main", entry, exit_at, frame_size=frame_size)
    prog.globals = dict(globals_ or {})
    return prog.finalize()


def run(prog: Program, initial: dict | None = None, **kw) -> Machine:
    m = Machine(prog, mem_size=kw.pop("mem_size", 256), **kw)
    if initial:
        m.set_globals(initial)
    m.start_forward().run()
    return m


def roundtrip(prog: Program, initial: dict | None = None, mem_size: int = 256):
    """Run forwards then backwards; return (machine, ok, why)."""
    m = Machine(prog, mem_size=mem_size)
    if initial:
        m.set_globals(initial)
    before = m.snapshot()
    m.start_forward().run()
    forward_state = m.globals_dict()
    m.start_backward().run()
    ok, why = state_equal(before, m.snapshot())
    return m, forward_state, ok, why
