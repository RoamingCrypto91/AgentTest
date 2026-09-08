"""RIR -- the structured Reversible Intermediate Representation.

Flat bytecode is what the machine runs, but it is a bad place to *invert* a
program: once control flow has been flattened into paired jump targets, undoing
it means re-deriving the block structure.  So Reverie keeps a tree in between.

Inversion on this tree is four lines of insight and about forty of code:

===========================  =================================================
``S1 ; S2``                  ``inv(S2) ; inv(S1)``
``x += e``                   ``x -= e``
``if a then S else T fi b``  ``if b then inv(S) else inv(T) fi a``
``from a do S loop T until b``  ``from b do inv(S) loop inv(T) until a``
``call p`` / ``uncall p``    each other
===========================  =================================================

The conditional rule is the one people find surprising.  A reversible ``if``
carries *two* predicates: the entry test and an exit assertion.  Going forwards
the entry test picks the branch and the exit assertion is checked; going
backwards the exit assertion picks the branch and the entry test is checked.
Inverting simply swaps their roles, which is why no branch history is needed.
"""

from __future__ import annotations

from typing import Iterable, Optional, Sequence

from . import isa
from .diagnostics import Span
from .ir import Addr, Const, Expr
from .isa import UPDATE_INVERSE


class RStmt:
    """Base class for structured reversible statements."""

    __slots__ = ("span",)

    def __init__(self, span: Optional[Span] = None) -> None:
        self.span = span

    def invert(self) -> "RStmt":  # pragma: no cover - abstract
        raise NotImplementedError

    def lower(self, b: "CodeBuilder") -> None:  # pragma: no cover - abstract
        raise NotImplementedError

    def children(self) -> Sequence["RStmt"]:
        return ()

    def walk(self) -> Iterable["RStmt"]:
        yield self
        for c in self.children():
            yield from c.walk()

    def render(self, indent: int = 0) -> str:  # pragma: no cover - abstract
        raise NotImplementedError

    def __repr__(self) -> str:
        return self.render()


class RSkip(RStmt):
    __slots__ = ()

    def invert(self) -> RStmt:
        return self

    def lower(self, b: "CodeBuilder") -> None:
        b.emit(isa.Nop(self.span))

    def render(self, indent: int = 0) -> str:
        return "  " * indent + "skip"


class RSeq(RStmt):
    __slots__ = ("stmts",)

    def __init__(self, stmts: Sequence[RStmt], span: Optional[Span] = None) -> None:
        super().__init__(span)
        self.stmts = list(stmts)

    def invert(self) -> RStmt:
        return RSeq([s.invert() for s in reversed(self.stmts)], self.span)

    def lower(self, b: "CodeBuilder") -> None:
        for s in self.stmts:
            s.lower(b)

    def children(self):
        return self.stmts

    def render(self, indent: int = 0) -> str:
        if not self.stmts:
            return "  " * indent + "skip"
        return "\n".join(s.render(indent) for s in self.stmts)


class RUpdate(RStmt):
    __slots__ = ("op", "addr", "expr")

    def __init__(self, op: str, addr: Addr, expr: Expr, span: Optional[Span] = None) -> None:
        super().__init__(span)
        self.op = op
        self.addr = addr
        self.expr = expr

    def invert(self) -> RStmt:
        return RUpdate(UPDATE_INVERSE[self.op], self.addr, self.expr, self.span)

    def lower(self, b: "CodeBuilder") -> None:
        b.emit(isa.Update(self.op, self.addr, self.expr, self.span))

    def render(self, indent: int = 0) -> str:
        return f"{'  ' * indent}{self.addr.render()} {self.op}= {self.expr.render()}"


class RUnary(RStmt):
    __slots__ = ("op", "addr")

    def __init__(self, op: str, addr: Addr, span: Optional[Span] = None) -> None:
        super().__init__(span)
        self.op = op
        self.addr = addr

    def invert(self) -> RStmt:
        return self

    def lower(self, b: "CodeBuilder") -> None:
        b.emit(isa.UnaryUpdate(self.op, self.addr, self.span))

    def render(self, indent: int = 0) -> str:
        return f"{'  ' * indent}{self.op} {self.addr.render()}"


class RSwap(RStmt):
    __slots__ = ("a", "b")

    def __init__(self, a: Addr, b: Addr, span: Optional[Span] = None) -> None:
        super().__init__(span)
        self.a = a
        self.b = b

    def invert(self) -> RStmt:
        return self

    def lower(self, bld: "CodeBuilder") -> None:
        bld.emit(isa.Swap(self.a, self.b, self.span))

    def render(self, indent: int = 0) -> str:
        return f"{'  ' * indent}{self.a.render()} <=> {self.b.render()}"


class RAssert(RStmt):
    __slots__ = ("expr", "message")

    def __init__(self, expr: Expr, message: str = "", span: Optional[Span] = None) -> None:
        super().__init__(span)
        self.expr = expr
        self.message = message

    def invert(self) -> RStmt:
        return self

    def lower(self, b: "CodeBuilder") -> None:
        b.emit(isa.Assert(self.expr, self.message, self.span))

    def render(self, indent: int = 0) -> str:
        return f"{'  ' * indent}assert {self.expr.render()}"


class RLocal(RStmt):
    __slots__ = ("off", "expr", "size", "name")

    def __init__(
        self,
        off: int,
        expr: Optional[Expr],
        size: int = 1,
        name: str = "",
        span: Optional[Span] = None,
    ) -> None:
        super().__init__(span)
        self.off = off
        self.expr = expr
        self.size = size
        self.name = name

    def invert(self) -> RStmt:
        return RDelocal(self.off, self.expr, self.size, self.name, self.span)

    def lower(self, b: "CodeBuilder") -> None:
        b.emit(isa.LocalAlloc(self.off, self.expr, self.size, self.name, self.span))

    def render(self, indent: int = 0) -> str:
        rhs = f" = {self.expr.render()}" if self.expr is not None else f"[{self.size}]"
        return f"{'  ' * indent}local {self.name}{rhs}"


class RDelocal(RLocal):
    __slots__ = ()

    def invert(self) -> RStmt:
        return RLocal(self.off, self.expr, self.size, self.name, self.span)

    def lower(self, b: "CodeBuilder") -> None:
        b.emit(isa.LocalFree(self.off, self.expr, self.size, self.name, self.span))

    def render(self, indent: int = 0) -> str:
        rhs = f" = {self.expr.render()}" if self.expr is not None else f"[{self.size}]"
        return f"{'  ' * indent}delocal {self.name}{rhs}"


class RPush(RStmt):
    __slots__ = ("var", "stack")

    def __init__(self, var: Addr, stack: Addr, span: Optional[Span] = None) -> None:
        super().__init__(span)
        self.var = var
        self.stack = stack

    def invert(self) -> RStmt:
        return RPop(self.var, self.stack, self.span)

    def lower(self, b: "CodeBuilder") -> None:
        b.emit(isa.StackPush(self.var, self.stack, self.span))

    def render(self, indent: int = 0) -> str:
        return f"{'  ' * indent}push({self.var.render()}, {self.stack.render()})"


class RPop(RPush):
    __slots__ = ()

    def invert(self) -> RStmt:
        return RPush(self.var, self.stack, self.span)

    def lower(self, b: "CodeBuilder") -> None:
        b.emit(isa.StackPop(self.var, self.stack, self.span))

    def render(self, indent: int = 0) -> str:
        return f"{'  ' * indent}pop({self.var.render()}, {self.stack.render()})"


class REmit(RStmt):
    __slots__ = ("parts",)

    def __init__(self, parts: Sequence[object], span: Optional[Span] = None) -> None:
        super().__init__(span)
        self.parts = list(parts)

    def invert(self) -> RStmt:
        return RUnemit(self.parts, self.span)

    def lower(self, b: "CodeBuilder") -> None:
        b.emit(isa.Emit(self.parts, self.span))

    def render(self, indent: int = 0) -> str:
        bits = [repr(p) if isinstance(p, str) else p.render() for p in self.parts]
        return f"{'  ' * indent}print {', '.join(bits)}"


class RUnemit(REmit):
    __slots__ = ()

    def invert(self) -> RStmt:
        return REmit(self.parts, self.span)

    def lower(self, b: "CodeBuilder") -> None:
        b.emit(isa.Unemit(self.parts, self.span))

    def render(self, indent: int = 0) -> str:
        bits = [repr(p) if isinstance(p, str) else p.render() for p in self.parts]
        return f"{'  ' * indent}unprint {', '.join(bits)}"


class RIf(RStmt):
    """``if c1 then S else T fi c2`` -- two predicates, no branch history."""

    __slots__ = ("entry", "then", "otherwise", "exit")

    def __init__(
        self,
        entry: Expr,
        then: RStmt,
        otherwise: RStmt,
        exit: Expr,
        span: Optional[Span] = None,
    ) -> None:
        super().__init__(span)
        self.entry = entry
        self.then = then
        self.otherwise = otherwise
        self.exit = exit

    def invert(self) -> RStmt:
        return RIf(
            self.exit, self.then.invert(), self.otherwise.invert(), self.entry, self.span
        )

    def lower(self, b: "CodeBuilder") -> None:
        i = b.emit(isa.If(self.entry, self.span))
        self.then.lower(b)
        ee = b.emit(isa.ElseEnd(self.exit, self.span))
        eb = b.emit(isa.ElseBegin(self.entry, self.span))
        self.otherwise.lower(b)
        fi = b.emit(isa.Fi(self.exit, self.span))
        b.code[i].else_begin, b.code[i].fi = eb, fi
        b.code[ee].fi = fi
        b.code[eb].if_at = i
        b.code[fi].then_end, b.code[fi].else_end, b.code[fi].if_at = ee, fi - 1, i

    def children(self):
        return (self.then, self.otherwise)

    def render(self, indent: int = 0) -> str:
        pad = "  " * indent
        return (
            f"{pad}if {self.entry.render()} then\n"
            f"{self.then.render(indent + 1)}\n"
            f"{pad}else\n"
            f"{self.otherwise.render(indent + 1)}\n"
            f"{pad}fi {self.exit.render()}"
        )


class RLoop(RStmt):
    """``from c1 do S1 loop S2 until c2``."""

    __slots__ = ("entry", "body", "step", "exit")

    def __init__(
        self,
        entry: Expr,
        body: RStmt,
        step: RStmt,
        exit: Expr,
        span: Optional[Span] = None,
    ) -> None:
        super().__init__(span)
        self.entry = entry
        self.body = body
        self.step = step
        self.exit = exit

    def invert(self) -> RStmt:
        return RLoop(
            self.exit, self.body.invert(), self.step.invert(), self.entry, self.span
        )

    def lower(self, b: "CodeBuilder") -> None:
        f = b.emit(isa.From(self.entry, self.span))
        self.body.lower(b)
        u = b.emit(isa.Until(self.exit, self.span))
        self.step.lower(b)
        r = b.emit(isa.Repeat(self.entry, self.exit, self.span))
        b.code[f].until, b.code[f].repeat = u, r
        b.code[u].from_at, b.code[u].repeat = f, r
        b.code[r].from_at, b.code[r].until = f, u

    def children(self):
        return (self.body, self.step)

    def render(self, indent: int = 0) -> str:
        pad = "  " * indent
        return (
            f"{pad}from {self.entry.render()} do\n"
            f"{self.body.render(indent + 1)}\n"
            f"{pad}loop\n"
            f"{self.step.render(indent + 1)}\n"
            f"{pad}until {self.exit.render()}"
        )


class RCall(RStmt):
    __slots__ = ("proc", "args", "uncall")

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

    def invert(self) -> RStmt:
        return RCall(self.proc, self.args, not self.uncall, self.span)

    def lower(self, b: "CodeBuilder") -> None:
        b.emit(isa.Call(self.proc, self.args, self.uncall, self.span))

    def render(self, indent: int = 0) -> str:
        kw = "uncall" if self.uncall else "call"
        args = ", ".join(a.render() for a in self.args)
        return f"{'  ' * indent}{kw} {self.proc}({args})"


# ---------------------------------------------------------------------------
# classical statements -- only legal inside a generated `embed` procedure
# ---------------------------------------------------------------------------


class CStmt(RStmt):
    """A classical (information-destroying) statement, made reversible by tape.

    These are never inverted individually.  An ``embed`` block compiles to a
    procedure built from these, and the block's *inverse* is a ``uncall`` of
    that same procedure -- Bennett's uncompute step.
    """

    __slots__ = ()

    def invert(self) -> RStmt:
        raise TypeError(
            "classical statements are inverted wholesale by uncalling their "
            "procedure, never individually"
        )


class CSetStmt(CStmt):
    __slots__ = ("addr", "expr")

    def __init__(self, addr: Addr, expr: Expr, span: Optional[Span] = None) -> None:
        super().__init__(span)
        self.addr = addr
        self.expr = expr

    def lower(self, b: "CodeBuilder") -> None:
        b.emit(isa.CSet(self.addr, self.expr, self.span))

    def render(self, indent: int = 0) -> str:
        return f"{'  ' * indent}{self.addr.render()} := {self.expr.render()}"


class CIfStmt(CStmt):
    __slots__ = ("cond", "then", "otherwise")

    def __init__(
        self, cond: Expr, then: RStmt, otherwise: RStmt, span: Optional[Span] = None
    ) -> None:
        super().__init__(span)
        self.cond = cond
        self.then = then
        self.otherwise = otherwise

    def lower(self, b: "CodeBuilder") -> None:
        i = b.emit(isa.CIf(self.cond, self.span))
        self.then.lower(b)
        ee = b.emit(isa.CElseEnd(self.span))
        eb = b.emit(isa.CElseBegin(self.span))
        self.otherwise.lower(b)
        fi = b.emit(isa.CFi(self.span))
        b.code[i].else_begin, b.code[i].fi = eb, fi
        b.code[ee].fi = fi
        b.code[eb].if_at = i
        b.code[fi].then_end, b.code[fi].else_end, b.code[fi].if_at = ee, fi - 1, i

    def children(self):
        return (self.then, self.otherwise)

    def render(self, indent: int = 0) -> str:
        pad = "  " * indent
        return (
            f"{pad}cif {self.cond.render()} {{\n{self.then.render(indent + 1)}\n"
            f"{pad}}} else {{\n{self.otherwise.render(indent + 1)}\n{pad}}}"
        )


class CWhileStmt(CStmt):
    __slots__ = ("cond", "body", "counter")

    def __init__(
        self, cond: Expr, body: RStmt, counter: int, span: Optional[Span] = None
    ) -> None:
        super().__init__(span)
        self.cond = cond
        self.body = body
        self.counter = counter

    def lower(self, b: "CodeBuilder") -> None:
        f = b.emit(isa.CFrom(self.counter, self.span))
        u = b.emit(isa.CUntil(self.cond, self.counter, self.span))
        self.body.lower(b)
        r = b.emit(isa.CRepeat(self.counter, self.span))
        b.code[f].repeat = r
        b.code[u].repeat = r
        b.code[r].from_at, b.code[r].until = f, u

    def children(self):
        return (self.body,)

    def render(self, indent: int = 0) -> str:
        pad = "  " * indent
        return f"{pad}cwhile {self.cond.render()} {{\n{self.body.render(indent + 1)}\n{pad}}}"


# ---------------------------------------------------------------------------
# lowering
# ---------------------------------------------------------------------------


class CodeBuilder:
    """Accumulates flat instructions and hands back their indices."""

    def __init__(self) -> None:
        self.code: list[isa.Instr] = []

    def emit(self, ins: isa.Instr) -> int:
        ins.at = len(self.code)
        self.code.append(ins)
        return ins.at

    def __len__(self) -> int:
        return len(self.code)


def lower_body(stmt: RStmt) -> list[isa.Instr]:
    b = CodeBuilder()
    stmt.lower(b)
    return b.code
