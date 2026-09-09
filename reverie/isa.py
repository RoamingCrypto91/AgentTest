"""The Reverie reversible instruction set.

Every instruction implements two methods, ``forward`` and ``backward``, and the
machine is required to satisfy::

    backward(forward(state)) == state    and    forward(backward(state)) == state

for every reachable state.  That is not a comment describing an aspiration -- it
is the property the fuzzer in ``tests/test_roundtrip.py`` checks on hundreds of
thousands of randomly generated programs.

Control flow is *structured and paired*.  A classical machine can jump anywhere
because forgetting where you came from is free; a reversible machine cannot.
So each control construct is a matched set of instructions that record enough
in the code itself (not in a runtime history) to be walked in either direction:

    IF c1 ... ELSE_END c2 / ELSE_BEGIN c1 ... FI c2
    FROM c1 ... UNTIL c2 ... REPEAT

The ``C``-prefixed instructions are the exception: they implement *classical*
(irreversible) statements by pushing the discarded information onto a history
tape.  They are only emitted inside ``embed`` blocks, whose compute phase is
always paired with an uncompute phase that drains the tape back to empty --
Bennett's construction.  See :mod:`reverie.lowering.embed`.

**The program counter is a boundary index.**  ``pc == k`` names the point
*between* instruction ``k-1`` and instruction ``k``.  Running forwards executes
``code[pc]``; running backwards executes ``code[pc - 1]``.  Both directions then
agree about what ``pc`` means, so reversing the machine mid-run is a matter of
flipping one sign -- no bookkeeping, no saved history, no "where did I come
from?".  Every ``backward`` method below leaves ``pc`` pointing at the boundary
*above* the next instruction to undo.
"""

from __future__ import annotations

from typing import Optional, Sequence

from .diagnostics import RuntimeFault, Span
from .ir import Addr, Expr, IndexA, idiv

# ---------------------------------------------------------------------------
# base
# ---------------------------------------------------------------------------


class Instr:
    """Base class for every instruction."""

    __slots__ = ("at", "span")
    name = "?"
    #: instructions that must never be reached in the forward direction
    forward_unreachable = False
    #: ... and the mirror image
    backward_unreachable = False

    def __init__(self, span: Optional[Span] = None) -> None:
        self.at = -1
        self.span = span

    # -- execution --------------------------------------------------------
    def forward(self, m) -> None:  # pragma: no cover - abstract
        raise NotImplementedError

    def backward(self, m) -> None:  # pragma: no cover - abstract
        raise NotImplementedError

    # -- presentation -----------------------------------------------------
    def operands(self) -> str:
        return ""

    def render(self) -> str:
        ops = self.operands()
        return f"{self.name} {ops}".rstrip()

    def __repr__(self) -> str:
        return f"<{self.at}: {self.render()}>"



class Nop(Instr):
    __slots__ = ()
    name = "nop"

    def forward(self, m) -> None:
        m.pc += 1

    def backward(self, m) -> None:
        m.pc -= 1


class Halt(Instr):
    """Program terminator.

    The machine recognises ``halt`` before executing it and simply stops, so it
    costs no logical time.  That keeps a forward run and its reversal exactly
    the same length -- otherwise "go back to the beginning" would overshoot by
    one instruction that has no inverse.
    """

    __slots__ = ()
    name = "halt"
    backward_unreachable = True

    def forward(self, m) -> None:  # pragma: no cover - the machine stops first
        m.halted = True

    def backward(self, m) -> None:  # pragma: no cover - unreachable
        raise RuntimeFault("cannot step backwards through `halt`", pc=self.at)


# ---------------------------------------------------------------------------
# reversible store updates
# ---------------------------------------------------------------------------

#: forward operator -> backward operator
UPDATE_INVERSE = {
    "+": "-",
    "-": "+",
    "^": "^",
    "*": "/",
    "/": "*",
    "<<": ">>",
    ">>": "<<",
}


def _apply_update(op: str, cur: int, v: int, m) -> int:
    if op == "+":
        return cur + v
    if op == "-":
        return cur - v
    if op == "^":
        return cur ^ v
    if op == "*":
        if v == 0:
            raise RuntimeFault(
                "`*=` by zero destroys information",
                notes=["multiplying by zero is not injective; Reverie refuses to erase"],
            )
        return cur * v
    if op == "/":
        if v == 0:
            raise RuntimeFault("`/=` by zero")
        if cur % v != 0:
            raise RuntimeFault(
                f"`/=` is not exact: {cur} is not divisible by {v}",
                notes=["a lossy division would erase the remainder"],
            )
        return idiv(cur, v)
    if op == "<<":
        if v < 0:
            raise RuntimeFault(f"negative shift {v}")
        if v > 1 << 20:
            raise RuntimeFault(f"shift {v} too large")
        return cur << v
    if op == ">>":
        if v < 0:
            raise RuntimeFault(f"negative shift {v}")
        if cur & ((1 << v) - 1):
            raise RuntimeFault(
                f"`>>=` by {v} would discard {v} nonzero low bits",
                notes=["shifting out set bits erases information"],
            )
        return cur >> v
    raise RuntimeFault(f"unknown update operator {op!r}")  # pragma: no cover


class Update(Instr):
    """``addr op= expr`` -- the workhorse reversible instruction."""

    __slots__ = ("op", "addr", "expr")
    name = "upd"

    def __init__(self, op: str, addr: Addr, expr: Expr, span: Optional[Span] = None) -> None:
        super().__init__(span)
        if op not in UPDATE_INVERSE:
            raise ValueError(f"non-invertible update operator {op!r}")
        self.op = op
        self.addr = addr
        self.expr = expr

    def forward(self, m) -> None:
        a = self.addr.resolve(m)
        m.mem[a] = _apply_update(self.op, m.mem[a], self.expr.eval(m), m)
        m.pc += 1

    def backward(self, m) -> None:
        a = self.addr.resolve(m)
        m.mem[a] = _apply_update(UPDATE_INVERSE[self.op], m.mem[a], self.expr.eval(m), m)
        m.pc -= 1

    def operands(self) -> str:
        return f"{self.addr.render()} {self.op}= {self.expr.render()}"



#: in-place unary operators, each its own inverse
UNARY_INPLACE = {
    "neg": lambda v: -v,
    "not": lambda v: ~v,
}


class UnaryUpdate(Instr):
    """Self-inverse in-place operations: ``neg x`` and ``not x``."""

    __slots__ = ("op", "addr")
    name = "un"

    def __init__(self, op: str, addr: Addr, span: Optional[Span] = None) -> None:
        super().__init__(span)
        if op not in UNARY_INPLACE:
            raise ValueError(f"unknown in-place unary {op!r}")
        self.op = op
        self.addr = addr

    def forward(self, m) -> None:
        a = self.addr.resolve(m)
        m.mem[a] = UNARY_INPLACE[self.op](m.mem[a])
        m.pc += 1

    backward = None  # filled in below

    def operands(self) -> str:
        return f"{self.op} {self.addr.render()}"



def _unary_backward(self, m) -> None:
    a = self.addr.resolve(m)
    m.mem[a] = UNARY_INPLACE[self.op](m.mem[a])
    m.pc -= 1


UnaryUpdate.backward = _unary_backward


class Swap(Instr):
    """``a <=> b`` -- self-inverse."""

    __slots__ = ("a", "b")
    name = "swap"

    def __init__(self, a: Addr, b: Addr, span: Optional[Span] = None) -> None:
        super().__init__(span)
        self.a = a
        self.b = b

    def _do(self, m) -> None:
        ia = self.a.resolve(m)
        ib = self.b.resolve(m)
        m.mem[ia], m.mem[ib] = m.mem[ib], m.mem[ia]

    def forward(self, m) -> None:
        self._do(m)
        m.pc += 1

    def backward(self, m) -> None:
        self._do(m)
        m.pc -= 1

    def operands(self) -> str:
        return f"{self.a.render()} <=> {self.b.render()}"



class Assert(Instr):
    """``assert e`` -- self-inverse; traps if *e* is zero."""

    __slots__ = ("expr", "message")
    name = "assert"

    def __init__(self, expr: Expr, message: str = "", span: Optional[Span] = None) -> None:
        super().__init__(span)
        self.expr = expr
        self.message = message

    def _check(self, m) -> None:
        if self.expr.eval(m) == 0:
            raise RuntimeFault(
                self.message or f"assertion failed: {self.expr.render()}", pc=self.at
            )

    def forward(self, m) -> None:
        self._check(m)
        m.pc += 1

    def backward(self, m) -> None:
        self._check(m)
        m.pc -= 1

    def operands(self) -> str:
        return self.expr.render()



# ---------------------------------------------------------------------------
# local storage: allocation is an assertion, deallocation is an assertion
# ---------------------------------------------------------------------------


class LocalAlloc(Instr):
    """``local x = e`` -- the cell must be zero beforehand and holds *e* after."""

    __slots__ = ("off", "expr", "size", "vname")
    name = "local"

    def __init__(
        self,
        off: int,
        expr: Optional[Expr],
        size: int = 1,
        vname: str = "",
        span: Optional[Span] = None,
    ) -> None:
        super().__init__(span)
        self.off = off
        self.expr = expr
        self.size = size
        self.vname = vname

    def _alloc(self, m) -> None:
        base = m.fp + self.off
        for i in range(self.size):
            if m.mem[base + i] != 0:
                raise RuntimeFault(
                    f"`local {self.vname}` requires a zeroed cell, found {m.mem[base + i]}",
                    pc=self.at,
                )
        if self.expr is not None:
            m.mem[base] = self.expr.eval(m)

    def _free(self, m) -> None:
        base = m.fp + self.off
        if self.expr is not None:
            want = self.expr.eval(m)
            got = m.mem[base]
            if got != want:
                raise RuntimeFault(
                    f"`delocal {self.vname}` expected {want} but the cell holds {got}",
                    pc=self.at,
                    notes=[
                        "the delocal expression must reconstruct the value so the cell",
                        "can be released without erasing information",
                    ],
                )
            m.mem[base] = 0
        else:
            for i in range(self.size):
                if m.mem[base + i] != 0:
                    raise RuntimeFault(
                        f"`delocal {self.vname}` requires all cells zeroed; "
                        f"element {i} holds {m.mem[base + i]}",
                        pc=self.at,
                    )

    def forward(self, m) -> None:
        self._alloc(m)
        m.pc += 1

    def backward(self, m) -> None:
        self._free(m)
        m.pc -= 1

    def operands(self) -> str:
        if self.expr is None:
            return f"%{self.off}[{self.size}]"
        return f"%{self.off} = {self.expr.render()}"



class LocalFree(LocalAlloc):
    """``delocal x = e`` -- the exact inverse of :class:`LocalAlloc`."""

    __slots__ = ()
    name = "delocal"

    def forward(self, m) -> None:
        self._free(m)
        m.pc += 1

    def backward(self, m) -> None:
        self._alloc(m)
        m.pc -= 1


# ---------------------------------------------------------------------------
# stacks -- push/pop are exact inverses because push zeroes its source
# ---------------------------------------------------------------------------


class StackPush(Instr):
    """``push(x, s)``: pushes *x* onto *s* and leaves *x* zero."""

    __slots__ = ("var", "stack")
    name = "push"

    def __init__(self, var: Addr, stack: Addr, span: Optional[Span] = None) -> None:
        super().__init__(span)
        self.var = var
        self.stack = stack

    def _push(self, m) -> None:
        a = self.var.resolve(m)
        st = m.stack_at(self.stack.resolve(m))
        st.append(m.mem[a])
        m.mem[a] = 0

    def _pop(self, m) -> None:
        a = self.var.resolve(m)
        if m.mem[a] != 0:
            raise RuntimeFault(
                f"`pop` requires a zeroed target, found {m.mem[a]}", pc=self.at
            )
        st = m.stack_at(self.stack.resolve(m))
        if not st:
            raise RuntimeFault("`pop` from an empty stack", pc=self.at)
        m.mem[a] = st.pop()

    def forward(self, m) -> None:
        self._push(m)
        m.pc += 1

    def backward(self, m) -> None:
        self._pop(m)
        m.pc -= 1

    def operands(self) -> str:
        return f"{self.var.render()}, {self.stack.render()}"



class StackPop(StackPush):
    """``pop(x, s)``: the exact inverse of :class:`StackPush`."""

    __slots__ = ()
    name = "pop"

    def forward(self, m) -> None:
        self._pop(m)
        m.pc += 1

    def backward(self, m) -> None:
        self._push(m)
        m.pc -= 1


# ---------------------------------------------------------------------------
# output -- printing is reversible if you un-print on the way back
# ---------------------------------------------------------------------------


class Emit(Instr):
    """``print`` / ``write`` -- output, which rewinding takes back.

    The machine keeps a partial-line buffer alongside the finished lines, so
    that building a line piece by piece is reversible too: ``write`` appends to
    the buffer, ``print`` flushes it, and each undoes exactly what it did.
    """

    __slots__ = ("parts", "newline")
    name = "emit"

    def __init__(
        self,
        parts: Sequence[object],
        span: Optional[Span] = None,
        newline: bool = True,
    ) -> None:
        super().__init__(span)
        self.parts = list(parts)
        self.newline = newline

    def _text(self, m) -> str:
        out = []
        for p in self.parts:
            if isinstance(p, str):
                out.append(p)
            else:
                out.append(str(p.eval(m)))
        return "".join(out)

    def _produce(self, m) -> None:
        text = self._text(m)
        if self.newline:
            m.output.append(m.line + text)
            m.line = ""
        else:
            m.line += text

    def _consume(self, m) -> None:
        expect = self._text(m)
        if self.newline:
            # Report the open line first: it is the more specific complaint,
            # and both are true when nothing has been printed yet.
            if m.line:
                raise RuntimeFault(
                    "cannot un-print: a partial line is still open", pc=self.at
                )
            if not m.output:
                raise RuntimeFault(
                    "cannot un-print: the output log is empty", pc=self.at
                )
            got = m.output.pop()
            if not got.endswith(expect):
                raise RuntimeFault(
                    f"un-print mismatch: the log holds {got!r} but the "
                    f"instruction reproduces {expect!r}",
                    pc=self.at,
                )
            m.line = got[: len(got) - len(expect)] if expect else got
        else:
            if not m.line.endswith(expect):
                raise RuntimeFault(
                    f"un-write mismatch: the open line is {m.line!r} but the "
                    f"instruction reproduces {expect!r}",
                    pc=self.at,
                )
            m.line = m.line[: len(m.line) - len(expect)] if expect else m.line

    def forward(self, m) -> None:
        self._produce(m)
        m.pc += 1

    def backward(self, m) -> None:
        self._consume(m)
        m.pc -= 1

    def operands(self) -> str:
        bits = []
        for p in self.parts:
            bits.append(repr(p) if isinstance(p, str) else p.render())
        return ("" if self.newline else "-nl ") + ", ".join(bits)



class Unemit(Emit):
    """The inverse of :class:`Emit`: consumes a line of output.

    This is what ``print`` becomes when a program is inverted.  An inverted
    program does not print its output again -- it *un-prints* it, consuming the
    log the forward program produced.  Output is information like any other.
    """

    __slots__ = ()
    name = "unemit"

    def forward(self, m) -> None:
        self._consume(m)
        m.pc += 1

    def backward(self, m) -> None:
        Emit._produce(self, m)
        m.pc -= 1


# ---------------------------------------------------------------------------
# reversible conditional
# ---------------------------------------------------------------------------


class If(Instr):
    __slots__ = ("cond", "else_begin", "fi")
    name = "if"

    def __init__(self, cond: Expr, span: Optional[Span] = None) -> None:
        super().__init__(span)
        self.cond = cond
        self.else_begin = -1
        self.fi = -1

    def forward(self, m) -> None:
        m.pc = self.at + 1 if self.cond.eval(m) != 0 else self.else_begin + 1

    def backward(self, m) -> None:
        if self.cond.eval(m) == 0:
            raise RuntimeFault(
                "reverse-entering the then-branch requires the entry test to hold",
                pc=self.at,
            )
        m.pc = self.at

    def operands(self) -> str:
        return f"{self.cond.render()} else->{self.else_begin} fi->{self.fi}"



class ElseEnd(Instr):
    """Closes the then-branch: asserts the exit test and jumps past ``fi``.

    Never executed backwards.  Going the other way, ``fi`` jumps straight to
    this instruction's *boundary*, so that a forward run and its reversal
    execute the same number of instructions -- which is what makes it safe to
    reverse the machine in the middle of a conditional.
    """

    __slots__ = ("exit_cond", "fi")
    name = "else_end"
    backward_unreachable = True

    def __init__(self, exit_cond: Expr, span: Optional[Span] = None) -> None:
        super().__init__(span)
        self.exit_cond = exit_cond
        self.fi = -1

    def forward(self, m) -> None:
        if self.exit_cond.eval(m) == 0:
            raise RuntimeFault(
                f"exit assertion `{self.exit_cond.render()}` must hold after the "
                f"then-branch, otherwise the conditional could not be reversed",
                pc=self.at,
            )
        m.pc = self.fi + 1

    def backward(self, m) -> None:  # pragma: no cover - unreachable
        raise RuntimeFault("fell into else_end going backwards", pc=self.at)

    def operands(self) -> str:
        return f"{self.exit_cond.render()} fi->{self.fi}"



class ElseBegin(Instr):
    """Opens the else-branch; only ever executed in the backward direction."""

    __slots__ = ("cond", "if_at")
    name = "else_begin"
    forward_unreachable = True

    def __init__(self, cond: Expr, span: Optional[Span] = None) -> None:
        super().__init__(span)
        self.cond = cond
        self.if_at = -1

    def forward(self, m) -> None:  # pragma: no cover - unreachable by construction
        raise RuntimeFault("fell into else_begin going forward", pc=self.at)

    def backward(self, m) -> None:
        if self.cond.eval(m) != 0:
            raise RuntimeFault(
                "reverse-entering the else-branch requires the entry test to fail",
                pc=self.at,
            )
        m.pc = self.if_at

    def operands(self) -> str:
        return f"{self.cond.render()} if->{self.if_at}"



class Fi(Instr):
    __slots__ = ("exit_cond", "then_end", "else_end", "if_at")
    name = "fi"

    def __init__(self, exit_cond: Expr, span: Optional[Span] = None) -> None:
        super().__init__(span)
        self.exit_cond = exit_cond
        self.then_end = -1
        self.else_end = -1
        self.if_at = -1

    def forward(self, m) -> None:
        if self.exit_cond.eval(m) != 0:
            raise RuntimeFault(
                f"exit assertion `{self.exit_cond.render()}` must fail after the "
                f"else-branch, otherwise the conditional could not be reversed",
                pc=self.at,
            )
        m.pc = self.at + 1

    def backward(self, m) -> None:
        # Land on the boundary the forward run departed from: the then-branch
        # leaves through `else_end`, the else-branch through `fi` itself.
        m.pc = self.then_end if self.exit_cond.eval(m) != 0 else self.at

    def operands(self) -> str:
        return f"{self.exit_cond.render()} then_end->{self.then_end} else_end->{self.else_end}"



# ---------------------------------------------------------------------------
# reversible loop:  from c1 do S1 loop S2 until c2
# ---------------------------------------------------------------------------


class From(Instr):
    __slots__ = ("entry_cond", "until", "repeat")
    name = "from"

    def __init__(self, entry_cond: Expr, span: Optional[Span] = None) -> None:
        super().__init__(span)
        self.entry_cond = entry_cond
        self.until = -1
        self.repeat = -1

    def forward(self, m) -> None:
        if self.entry_cond.eval(m) == 0:
            raise RuntimeFault(
                f"loop entry assertion `{self.entry_cond.render()}` must hold on "
                f"the first arrival",
                pc=self.at,
            )
        m.pc = self.at + 1

    def backward(self, m) -> None:
        # Running backwards, the entry test becomes the loop's exit test.
        m.pc = self.at if self.entry_cond.eval(m) != 0 else self.repeat

    def operands(self) -> str:
        return f"{self.entry_cond.render()} until->{self.until} repeat->{self.repeat}"



class Until(Instr):
    __slots__ = ("exit_cond", "from_at", "repeat")
    name = "until"

    def __init__(self, exit_cond: Expr, span: Optional[Span] = None) -> None:
        super().__init__(span)
        self.exit_cond = exit_cond
        self.from_at = -1
        self.repeat = -1

    def forward(self, m) -> None:
        m.pc = self.repeat + 1 if self.exit_cond.eval(m) != 0 else self.at + 1

    def backward(self, m) -> None:
        if self.exit_cond.eval(m) != 0:
            raise RuntimeFault(
                f"loop exit assertion `{self.exit_cond.render()}` must fail when "
                f"re-entering the loop backwards",
                pc=self.at,
            )
        m.pc = self.at

    def operands(self) -> str:
        return f"{self.exit_cond.render()} from->{self.from_at} repeat->{self.repeat}"



class Repeat(Instr):
    """Closes the loop; also the backward entry point into it."""

    __slots__ = ("entry_cond", "exit_cond", "from_at", "until")
    name = "repeat"

    def __init__(
        self, entry_cond: Expr, exit_cond: Expr, span: Optional[Span] = None
    ) -> None:
        super().__init__(span)
        self.entry_cond = entry_cond
        self.exit_cond = exit_cond
        self.from_at = -1
        self.until = -1

    def forward(self, m) -> None:
        if self.entry_cond.eval(m) != 0:
            raise RuntimeFault(
                f"loop entry assertion `{self.entry_cond.render()}` must fail on "
                f"every arrival after the first",
                pc=self.at,
            )
        m.pc = self.from_at + 1

    def backward(self, m) -> None:
        if self.exit_cond.eval(m) == 0:
            raise RuntimeFault(
                f"reverse-entering the loop requires `{self.exit_cond.render()}` to hold",
                pc=self.at,
            )
        m.pc = self.until

    def operands(self) -> str:
        return (
            f"entry={self.entry_cond.render()} exit={self.exit_cond.render()} "
            f"from->{self.from_at} until->{self.until}"
        )



# ---------------------------------------------------------------------------
# procedures
# ---------------------------------------------------------------------------


class Call(Instr):
    """``call p(...)`` / ``uncall p(...)``.

    Arguments are passed by reference: the *address* each argument resolves to
    is recorded in the callee's frame.  Because those addresses live in the
    control stack rather than the data store, entering and leaving a procedure
    erases nothing.
    """

    __slots__ = ("proc", "args", "uncall")
    name = "call"

    def __init__(
        self,
        proc: str,
        args: Sequence[Addr],
        uncall: bool = False,
        span: Optional[Span] = None,
    ) -> None:
        super().__init__(span)
        self.proc = proc
        self.args = list(args)
        self.uncall = uncall

    def _enter(self, m, reverse: bool) -> None:
        info = m.program.procs.get(self.proc)
        if info is None:
            raise RuntimeFault(f"call to unknown procedure `{self.proc}`", pc=self.at)
        if len(self.args) != info.arity:
            raise RuntimeFault(
                f"`{self.proc}` expects {info.arity} arguments, got {len(self.args)}",
                pc=self.at,
            )
        params = [a.resolve(m) for a in self.args]
        lens = [a.extent(m) for a in self.args]
        m.enter_frame(info, params, lens, self.at, self.uncall)
        if reverse:
            # enter at the far end, walking the body backwards.  `exit_at` is
            # the boundary just above the last body instruction.
            m.pc = info.exit_at
            m.dir = -1
        else:
            m.pc = info.entry_at + 1
            m.dir = 1

    def forward(self, m) -> None:
        self._enter(m, reverse=self.uncall)

    def backward(self, m) -> None:
        self._enter(m, reverse=not self.uncall)

    def operands(self) -> str:
        args = ", ".join(a.render() for a in self.args)
        kind = "uncall" if self.uncall else "call"
        return f"{kind} {self.proc}({args})"

    def render(self) -> str:
        return self.operands()



class ProcEntry(Instr):
    """Marker below a procedure body.

    Never executed forwards -- a forward ``call`` jumps past it.  Executed
    *backwards* when the machine walks out of the bottom of a body, which is
    what returning looks like when time runs the other way.
    """

    __slots__ = ("proc",)
    name = "entry"
    forward_unreachable = True

    def __init__(self, proc: str, span: Optional[Span] = None) -> None:
        super().__init__(span)
        self.proc = proc

    def forward(self, m) -> None:  # pragma: no cover - unreachable by construction
        raise RuntimeFault(f"fell into the entry marker of `{self.proc}`", pc=self.at)

    def backward(self, m) -> None:
        m.leave_frame("entry", self.at)

    def operands(self) -> str:
        return self.proc


class ProcExit(Instr):
    """Marker above a procedure body; the mirror image of :class:`ProcEntry`."""

    __slots__ = ("proc",)
    name = "exit"
    backward_unreachable = True

    def __init__(self, proc: str, span: Optional[Span] = None) -> None:
        super().__init__(span)
        self.proc = proc

    def forward(self, m) -> None:
        m.leave_frame("exit", self.at)

    def backward(self, m) -> None:  # pragma: no cover - unreachable by construction
        raise RuntimeFault(f"fell into the exit marker of `{self.proc}`", pc=self.at)

    def operands(self) -> str:
        return self.proc


# ---------------------------------------------------------------------------
# classical (history-backed) instructions, emitted only inside `embed`
# ---------------------------------------------------------------------------


class CSet(Instr):
    """``x = e`` -- classical assignment; the overwritten value goes on the tape."""

    __slots__ = ("addr", "expr", "dynamic")
    name = "cset"

    def __init__(self, addr: Addr, expr: Expr, span: Optional[Span] = None) -> None:
        super().__init__(span)
        self.addr = addr
        self.expr = expr
        # An indexed target's address may itself depend on the cell being
        # written, so record the resolved address alongside the old value.
        self.dynamic = isinstance(addr, IndexA)

    def forward(self, m) -> None:
        a = self.addr.resolve(m)
        v = self.expr.eval(m)
        m.history.append(m.mem[a])
        if self.dynamic:
            m.history.append(a)
        m.mem[a] = v
        m.pc += 1

    def backward(self, m) -> None:
        if not m.history:
            raise RuntimeFault("history tape underflow in cset", pc=self.at)
        a = m.history.pop() if self.dynamic else self.addr.resolve(m)
        if not m.history:
            raise RuntimeFault("history tape underflow in cset", pc=self.at)
        m.mem[a] = m.history.pop()
        m.pc -= 1

    def operands(self) -> str:
        return f"{self.addr.render()} = {self.expr.render()}"



class CIf(Instr):
    __slots__ = ("cond", "else_begin", "fi")
    name = "cif"

    def __init__(self, cond: Expr, span: Optional[Span] = None) -> None:
        super().__init__(span)
        self.cond = cond
        self.else_begin = -1
        self.fi = -1

    def forward(self, m) -> None:
        # No tape entry here.  The branch is recorded where the two paths meet
        # (`celse_end` / `cfi`), so that when the machine walks back into the
        # conditional the bit is the *first* thing it finds, above everything
        # the branch body itself wrote.
        m.pc = self.at + 1 if self.cond.eval(m) != 0 else self.else_begin + 1

    def backward(self, m) -> None:
        m.pc = self.at

    def operands(self) -> str:
        return f"{self.cond.render()} else->{self.else_begin} fi->{self.fi}"



class CElseEnd(Instr):
    __slots__ = ("fi",)
    name = "celse_end"
    backward_unreachable = True

    def __init__(self, span: Optional[Span] = None) -> None:
        super().__init__(span)
        self.fi = -1

    def forward(self, m) -> None:
        m.history.append(1)
        m.pc = self.fi + 1

    def backward(self, m) -> None:  # pragma: no cover - unreachable
        raise RuntimeFault("fell into celse_end going backwards", pc=self.at)

    def operands(self) -> str:
        return f"fi->{self.fi}"


class CElseBegin(Instr):
    __slots__ = ("if_at",)
    name = "celse_begin"
    forward_unreachable = True

    def __init__(self, span: Optional[Span] = None) -> None:
        super().__init__(span)
        self.if_at = -1

    def forward(self, m) -> None:  # pragma: no cover - unreachable by construction
        raise RuntimeFault("fell into celse_begin going forward", pc=self.at)

    def backward(self, m) -> None:
        m.pc = self.if_at

    def operands(self) -> str:
        return f"if->{self.if_at}"


class CFi(Instr):
    __slots__ = ("then_end", "else_end", "if_at")
    name = "cfi"

    def __init__(self, span: Optional[Span] = None) -> None:
        super().__init__(span)
        self.then_end = -1
        self.else_end = -1
        self.if_at = -1

    def forward(self, m) -> None:
        m.history.append(0)
        m.pc = self.at + 1

    def backward(self, m) -> None:
        if not m.history:
            raise RuntimeFault("history tape underflow in cfi", pc=self.at)
        v = m.history.pop()
        m.pc = self.then_end if v else self.at

    def operands(self) -> str:
        return f"then_end->{self.then_end} else_end->{self.else_end}"


class CFrom(Instr):
    """Head of a classical ``while``.

    Classical loops get the same skeleton as reversible ones -- head, test,
    tail -- because that shape is what makes the loop-back edge reversible: the
    edge lands *after* the head, so the head is the instruction that decides,
    on the way back, whether this was the first entry or another iteration.  A
    reversible ``from`` loop settles that with its entry predicate; a classical
    loop has no predicate to offer, so it counts iterations into a frame cell
    instead.
    """

    __slots__ = ("counter", "repeat")
    name = "cfrom"

    def __init__(self, counter: int, span: Optional[Span] = None) -> None:
        super().__init__(span)
        self.counter = counter
        self.repeat = -1

    def forward(self, m) -> None:
        if m.mem[m.fp + self.counter] != 0:
            raise RuntimeFault(
                "classical loop counter was not zero on entry", pc=self.at
            )
        m.pc = self.at + 1

    def backward(self, m) -> None:
        m.pc = self.at if m.mem[m.fp + self.counter] == 0 else self.repeat

    def operands(self) -> str:
        return f"cnt=%{self.counter} repeat->{self.repeat}"


class CUntil(Instr):
    """The test of a classical ``while``; counts iterations on the way in."""

    __slots__ = ("cond", "counter", "repeat")
    name = "cuntil"

    def __init__(self, cond: Expr, counter: int, span: Optional[Span] = None) -> None:
        super().__init__(span)
        self.cond = cond
        self.counter = counter
        self.repeat = -1

    def forward(self, m) -> None:
        c = m.fp + self.counter
        if self.cond.eval(m) != 0:
            m.mem[c] += 1
            m.pc = self.at + 1
        else:
            m.history.append(m.mem[c])
            m.mem[c] = 0
            m.pc = self.repeat + 1

    def backward(self, m) -> None:
        m.mem[m.fp + self.counter] -= 1
        m.pc = self.at

    def operands(self) -> str:
        return f"{self.cond.render()} cnt=%{self.counter} repeat->{self.repeat}"



class CRepeat(Instr):
    """Closes a classical loop; also the loop's backward entry point."""

    __slots__ = ("counter", "from_at", "until")
    name = "crepeat"

    def __init__(self, counter: int, span: Optional[Span] = None) -> None:
        super().__init__(span)
        self.counter = counter
        self.from_at = -1
        self.until = -1

    def forward(self, m) -> None:
        if m.mem[m.fp + self.counter] == 0:
            raise RuntimeFault(
                "classical loop reached its tail without counting an iteration",
                pc=self.at,
            )
        m.pc = self.from_at + 1

    def backward(self, m) -> None:
        c = m.fp + self.counter
        if m.mem[c] != 0:
            raise RuntimeFault(
                "classical loop counter was not zero on reverse entry", pc=self.at
            )
        if not m.history:
            raise RuntimeFault("history tape underflow in crepeat", pc=self.at)
        m.mem[c] = m.history.pop()
        m.pc = self.until

    def operands(self) -> str:
        return f"cnt=%{self.counter} from->{self.from_at} until->{self.until}"


#: every instruction class, keyed by mnemonic -- used by the assembler
INSTRUCTIONS = {
    cls.name: cls
    for cls in (
        Nop,
        Halt,
        Update,
        UnaryUpdate,
        Swap,
        Assert,
        LocalAlloc,
        LocalFree,
        StackPush,
        StackPop,
        Emit,
        Unemit,
        If,
        ElseEnd,
        ElseBegin,
        Fi,
        From,
        Until,
        Repeat,
        Call,
        ProcEntry,
        ProcExit,
        CSet,
        CIf,
        CElseEnd,
        CElseBegin,
        CFi,
        CFrom,
        CUntil,
        CRepeat,
    )
}
