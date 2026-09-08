"""An interactive time-travel debugger.

Most "time-travel" debuggers are recorders: they log every state change and
replay the log.  This one does not record anything.  ``back`` runs the machine
in reverse, and ``goto 400`` from step 900 executes five hundred inverse
instructions.  Memory cost: zero.  The state you land on is not a reconstruction
of the state you were in -- it *is* that state.

Commands (abbreviations in brackets)::

    step [n]      [s]   run n instructions in the current direction
    back [n]      [b]   run n instructions against the current direction
    reverse       [!]   flip the arrow of time
    run           [r]   run to completion / to a breakpoint
    rewind              run backwards to the very beginning
    goto <step>   [@]   travel to an absolute step number, either way
    break <spec>  [bp]  breakpoint on a line, `pc N`, or `proc NAME`
    delete <n>          remove a breakpoint
    watch <name>  [wa]  report every change to a variable
    list [n]      [l]   source around the current instruction
    disasm [n]    [d]   instructions around the current pc
    print <name>  [p]   value of a global, local or parameter
    globals       [g]   all globals
    locals              the current frame
    mem <a> [b]         raw cells
    tape                the classical history tape
    out                 the output log
    where         [w]   call stack
    stats                machine statistics
    reset                back to the initial state
    help          [h]
    quit          [q]
"""

from __future__ import annotations

import shlex
import sys
from typing import Callable, Optional

from .diagnostics import RuntimeFault, Source
from .isa import Call, Instr, ProcEntry, ProcExit
from .vm import Machine, Program

BANNER = r"""
                   reverie debugger
   time runs both ways.  `step` and `back`, `goto` anywhere.
   `help` for commands, `quit` to leave.
"""


class Debugger:
    def __init__(
        self,
        program: Program,
        source: Optional[Source] = None,
        initial: Optional[dict] = None,
        *,
        mem_size: int = 4096,
        out=None,
        color: bool = True,
    ) -> None:
        self.program = program
        self.source = source
        self.initial = dict(initial or {})
        self.mem_size = mem_size
        self.out = out or sys.stdout
        self.color = color
        self.breakpoints: list[tuple[str, object]] = []
        self.watches: list[str] = []
        self.last_watch: dict[str, object] = {}
        self.machine = self._fresh()
        self.running = True

    # -- plumbing ---------------------------------------------------------
    def _fresh(self) -> Machine:
        m = Machine(self.program, mem_size=self.mem_size)
        if self.initial:
            m.set_globals(self.initial)
        m.start_forward()
        return m

    def c(self, text: str, code: str) -> str:
        return f"\x1b[{code}m{text}\x1b[0m" if self.color else text

    def say(self, *parts: str) -> None:
        print(*parts, file=self.out)

    def error(self, message: str) -> None:
        self.say(self.c(f"! {message}", "31;1"))

    # -- introspection ----------------------------------------------------
    def current(self) -> Optional[Instr]:
        return self.machine.current()

    def current_line(self) -> int:
        ins = self.current()
        if ins is None or ins.span is None:
            return 0
        return ins.span.source.line_col(ins.span.start)[0]

    def prompt(self) -> str:
        m = self.machine
        arrow = self.c("->", "32;1") if m.dir > 0 else self.c("<-", "35;1")
        ins = self.current()
        text = ins.render() if ins is not None else "<end>"
        line = self.current_line()
        loc = f":{line}" if line else ""
        state = "halted " if m.halted else ""
        return f"[t={m.position:<5d}] {state}{arrow} {m.pc:4d}{loc}  {text}"

    def show_position(self) -> None:
        self.say(self.prompt())

    # -- stepping ---------------------------------------------------------
    def do_step(self, n: int = 1, quiet: bool = False) -> None:
        for _ in range(max(1, n)):
            if not self.machine.step():
                if not quiet:
                    edge = "the beginning of time" if self.machine.arrow < 0 else "halt"
                    self.say(self.c(f"reached {edge}", "33"))
                break
            self.check_watches()
        if not quiet:
            self.show_position()

    def do_back(self, n: int = 1) -> None:
        m = self.machine
        m.reverse()
        try:
            for _ in range(max(1, n)):
                if not m.step():
                    edge = "halt" if m.arrow > 0 else "the beginning of time"
                    self.say(self.c(f"reached {edge}", "33"))
                    break
                self.check_watches()
        finally:
            m.reverse()
        self.show_position()

    def do_reverse(self) -> None:
        self.machine.reverse()
        self.say(
            self.c(
                "time now runs " + ("forwards" if self.machine.dir > 0 else "backwards"),
                "36;1",
            )
        )
        self.show_position()

    def do_run(self, limit: int = 10_000_000) -> None:
        m = self.machine
        n = 0
        while n < limit:
            if not m.step():
                edge = "the beginning of time" if m.arrow < 0 else "halt"
                self.say(self.c(f"reached {edge}", "33"))
                break
            self.check_watches()
            n += 1
            if self.hits_breakpoint():
                self.say(self.c("breakpoint", "31;1"))
                break
        self.show_position()

    def do_goto(self, target: int) -> None:
        """Travel to an absolute point in logical time, in either direction.

        No snapshots are consulted and nothing is replayed.  If the target is
        behind us the machine runs backwards until it arrives; the state it
        lands on is not a reconstruction, it is the state.
        """
        m = self.machine
        if target < 0:
            self.error("logical time starts at 0")
            return
        guard = 0
        while m.position != target:
            guard += 1
            if guard > 10_000_000:
                self.error("gave up travelling")
                return
            if (m.position < target) != (m.arrow > 0):
                m.reverse()
            m.halted = False
            before = m.position
            if not m.step():
                self.say(self.c("reached the edge of time", "33"))
                break
            self.check_watches()
            if m.position == before:  # pragma: no cover - defensive
                break
        if m.arrow < 0:
            m.reverse()
        self.show_position()

    def hits_breakpoint(self) -> bool:
        ins = self.current()
        if ins is None:
            return False
        for kind, value in self.breakpoints:
            if kind == "pc" and self.machine.pc == value:
                return True
            if kind == "line" and ins.span is not None:
                if ins.span.source.line_col(ins.span.start)[0] == value:
                    return True
            if kind == "proc" and isinstance(ins, (ProcEntry, ProcExit)):
                if ins.proc == value:
                    return True
        return False

    def check_watches(self) -> None:
        if not self.watches:
            return
        g = self.machine.globals_dict()
        for name in self.watches:
            new = g.get(name)
            old = self.last_watch.get(name, "<unset>")
            if new != old:
                arrow = "->" if self.machine.dir > 0 else "<-"
                self.say(
                    self.c(
                        f"  watch {name}: {old} {arrow} {new}"
                        f"  (step {self.machine.stats.steps})",
                        "33",
                    )
                )
                self.last_watch[name] = new

    # -- inspection -------------------------------------------------------
    def do_list(self, radius: int = 4) -> None:
        if self.source is None:
            self.do_disasm(radius)
            return
        line = self.current_line()
        if not line:
            self.say("(no source position for this instruction)")
            return
        lo = max(1, line - radius)
        hi = min(self.source.line_count, line + radius)
        for ln in range(lo, hi + 1):
            marker = self.c(">", "32;1") if ln == line else " "
            self.say(f" {marker} {ln:4d} | {self.source.line_text(ln)}")

    def do_disasm(self, radius: int = 5) -> None:
        code = self.program.code
        here = self.machine.pc if self.machine.dir > 0 else self.machine.pc - 1
        lo = max(0, here - radius)
        hi = min(len(code) - 1, here + radius)
        for i in range(lo, hi + 1):
            marker = self.c(">", "32;1") if i == here else " "
            self.say(f" {marker} {i:5d}  {code[i].render()}")

    def do_print(self, name: str) -> None:
        m = self.machine
        g = self.program.globals.get(name)
        if g is not None:
            self.say(f"  {name} = {m.globals_dict()[name]}   (global @{g.addr})")
            return
        frame = m.frame
        if frame is not None:
            info = frame.proc
            if name in info.params:
                i = info.params.index(name)
                addr = frame.params[i]
                self.say(f"  {name} = {m.mem[addr]}   (parameter -> @{addr})")
                return
        self.error(f"no variable named {name!r} in scope")

    def do_globals(self) -> None:
        g = self.machine.globals_dict()
        if not g:
            self.say("  (no globals)")
        width = max((len(k) for k in g), default=1)
        for k, v in g.items():
            self.say(f"  {k.ljust(width)} = {v}")

    def do_locals(self) -> None:
        m = self.machine
        frame = m.frame
        if frame is None:
            self.say("  (no active frame)")
            return
        info = frame.proc
        self.say(f"  frame for {info.name} at @{frame.base} ({info.frame_size} cells)")
        for i, pname in enumerate(info.params):
            addr = frame.params[i]
            self.say(f"    {pname} -> @{addr} = {m.mem[addr]}")
        cells = m.mem[frame.base : frame.base + info.frame_size]
        if cells:
            self.say(f"    locals: {cells}")

    def do_mem(self, a: int, b: Optional[int] = None) -> None:
        b = a + 1 if b is None else b
        for i in range(a, min(b, len(self.machine.mem))):
            self.say(f"  @{i:<5d} {self.machine.mem[i]}")

    def do_where(self) -> None:
        m = self.machine
        if not m.frames:
            self.say("  (no frames)")
        for i, f in enumerate(reversed(m.frames)):
            kind = "uncall" if f.uncall else "call"
            self.say(f"  #{i} {f.proc.name}  ({kind} at pc {f.call_at})")

    def do_stats(self) -> None:
        m = self.machine
        s = m.stats
        self.say(f"  steps        {s.steps} (forward {s.forward_steps}, backward {s.backward_steps})")
        self.say(f"  calls        {s.calls}, max depth {s.max_depth}")
        self.say(f"  history tape {len(m.history)} entries now, peak {s.history_peak}")
        self.say(f"  tape bits    {s.history_bits} written, {s.history_pops} reclaimed")
        self.say(f"  bits erased  {m.bits_erased}")

    # -- command loop -----------------------------------------------------
    def execute(self, line: str) -> None:
        try:
            parts = shlex.split(line)
        except ValueError as exc:
            self.error(str(exc))
            return
        if not parts:
            self.do_step()
            return
        cmd, args = parts[0], parts[1:]

        def as_int(default: int = 1) -> int:
            try:
                return int(args[0]) if args else default
            except ValueError:
                self.error(f"expected a number, got {args[0]!r}")
                return default

        try:
            if cmd in ("step", "s"):
                self.do_step(as_int())
            elif cmd in ("back", "b"):
                self.do_back(as_int())
            elif cmd in ("reverse", "!"):
                self.do_reverse()
            elif cmd in ("run", "r", "continue", "c"):
                self.do_run()
            elif cmd == "rewind":
                if self.machine.arrow > 0:
                    self.machine.reverse()
                self.machine.halted = False
                self.do_run()
            elif cmd in ("goto", "@"):
                self.do_goto(as_int(0))
            elif cmd in ("break", "bp"):
                self.add_breakpoint(args)
            elif cmd == "delete":
                i = as_int(0)
                if 0 <= i < len(self.breakpoints):
                    self.say(f"  removed {self.breakpoints.pop(i)}")
                else:
                    self.error("no such breakpoint")
            elif cmd in ("watch", "wa"):
                if args:
                    self.watches.append(args[0])
                    self.last_watch[args[0]] = self.machine.globals_dict().get(args[0])
                    self.say(f"  watching {args[0]}")
                else:
                    self.say("  " + (", ".join(self.watches) or "(nothing watched)"))
            elif cmd in ("list", "l"):
                self.do_list(as_int(4))
            elif cmd in ("disasm", "d"):
                self.do_disasm(as_int(5))
            elif cmd in ("print", "p"):
                if args:
                    self.do_print(args[0])
                else:
                    self.error("print what?")
            elif cmd in ("globals", "g"):
                self.do_globals()
            elif cmd == "locals":
                self.do_locals()
            elif cmd == "mem":
                if not args:
                    self.error("mem <start> [end]")
                else:
                    lo = int(args[0])
                    hi = int(args[1]) if len(args) > 1 else None
                    self.do_mem(lo, hi)
            elif cmd == "tape":
                h = self.machine.history
                self.say(f"  {len(h)} entries: {h[-20:]}")
            elif cmd == "out":
                for i, line_out in enumerate(self.machine.output):
                    self.say(f"  {i}: {line_out}")
                if not self.machine.output:
                    self.say("  (no output yet)")
            elif cmd in ("where", "w"):
                self.do_where()
            elif cmd == "stats":
                self.do_stats()
            elif cmd == "reset":
                self.machine = self._fresh()
                self.show_position()
            elif cmd in ("help", "h", "?"):
                self.say(__doc__ or "")
            elif cmd in ("quit", "q", "exit"):
                self.running = False
            else:
                self.error(f"unknown command {cmd!r} (try `help`)")
        except RuntimeFault as fault:
            self.error(str(fault))

    def add_breakpoint(self, args: list[str]) -> None:
        if not args:
            if not self.breakpoints:
                self.say("  (no breakpoints)")
            for i, (kind, value) in enumerate(self.breakpoints):
                self.say(f"  {i}: {kind} {value}")
            return
        if args[0] == "pc" and len(args) > 1:
            self.breakpoints.append(("pc", int(args[1])))
        elif args[0] == "proc" and len(args) > 1:
            self.breakpoints.append(("proc", args[1]))
        else:
            self.breakpoints.append(("line", int(args[0])))
        self.say(f"  breakpoint {len(self.breakpoints) - 1}: {self.breakpoints[-1]}")

    def repl(self, reader: Optional[Callable[[str], str]] = None) -> None:
        reader = reader or input
        self.say(BANNER)
        self.show_position()
        while self.running:
            try:
                line = reader("(rev) ")
            except (EOFError, KeyboardInterrupt):
                self.say("")
                break
            self.execute(line.strip())
