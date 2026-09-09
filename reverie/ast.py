"""Abstract syntax for Reverie.

Two statement families live here.  ``Stmt`` covers the reversible language --
every node has a defined inverse.  ``CStmt`` covers the *classical* sublanguage
that may only appear inside an ``embed`` block, where ordinary destructive
assignment is allowed because the compiler will pay for it with a history tape
and then hand the tape back.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Sequence, Union

from .diagnostics import Span

# ---------------------------------------------------------------------------
# types
# ---------------------------------------------------------------------------


@dataclass
class Type:
    span: Optional[Span] = None


@dataclass
class TInt(Type):
    def __str__(self) -> str:
        return "int"


@dataclass
class TArray(Type):
    size: Optional["Expr"] = None
    #: filled in by the checker
    length: int = -1

    def __str__(self) -> str:
        return f"int[{self.length if self.length >= 0 else '?'}]"


@dataclass
class TStack(Type):
    def __str__(self) -> str:
        return "stack"


# ---------------------------------------------------------------------------
# expressions
# ---------------------------------------------------------------------------


@dataclass
class Expr:
    span: Optional[Span] = None


@dataclass
class Num(Expr):
    value: int = 0


@dataclass
class Var(Expr):
    name: str = ""


@dataclass
class Index(Expr):
    name: str = ""
    index: Optional[Expr] = None


@dataclass
class BinOp(Expr):
    op: str = ""
    left: Optional[Expr] = None
    right: Optional[Expr] = None


@dataclass
class UnOp(Expr):
    op: str = ""
    operand: Optional[Expr] = None


@dataclass
class Builtin(Expr):
    """``min``, ``max``, ``abs``, ``sign``, ``empty``, ``top``, ``size``, ``len``."""

    name: str = ""
    args: list[Expr] = field(default_factory=list)


LValue = Union[Var, Index]

# ---------------------------------------------------------------------------
# reversible statements
# ---------------------------------------------------------------------------


@dataclass
class Stmt:
    span: Optional[Span] = None


@dataclass
class Skip(Stmt):
    pass


@dataclass
class Block(Stmt):
    stmts: list[Stmt] = field(default_factory=list)


@dataclass
class Update(Stmt):
    """``x += e`` and friends."""

    op: str = "+"
    target: Optional[LValue] = None
    expr: Optional[Expr] = None


@dataclass
class UnaryStmt(Stmt):
    """``neg x`` / ``not x`` -- self-inverse in-place operations."""

    op: str = "neg"
    target: Optional[LValue] = None


@dataclass
class Swap(Stmt):
    left: Optional[LValue] = None
    right: Optional[LValue] = None


@dataclass
class If(Stmt):
    entry: Optional[Expr] = None
    then: Optional[Stmt] = None
    otherwise: Optional[Stmt] = None
    exit: Optional[Expr] = None


@dataclass
class Loop(Stmt):
    entry: Optional[Expr] = None
    body: Optional[Stmt] = None
    step: Optional[Stmt] = None
    exit: Optional[Expr] = None


@dataclass
class LocalDecl(Stmt):
    name: str = ""
    type: Optional[Type] = None
    expr: Optional[Expr] = None
    #: True for ``delocal``
    release: bool = False


@dataclass
class Call(Stmt):
    name: str = ""
    args: list[Expr] = field(default_factory=list)
    uncall: bool = False


@dataclass
class StackOp(Stmt):
    var: Optional[LValue] = None
    stack: Optional[Expr] = None
    pop: bool = False


@dataclass
class Print(Stmt):
    parts: list[Union[str, Expr]] = field(default_factory=list)
    #: ``unprint`` -- consumes a line of output instead of producing one.
    #: This is what ``print`` becomes when a program is inverted.
    reverse: bool = False
    #: ``print`` finishes the line; ``write`` leaves it open.
    newline: bool = True


@dataclass
class Assert(Stmt):
    expr: Optional[Expr] = None


@dataclass
class Undo(Stmt):
    """``undo { ... }`` -- run a block backwards.  Inversion as an operator."""

    body: Optional[Stmt] = None


@dataclass
class Binding:
    """One ``out ^= classical_expr`` clause of an ``embed`` header."""

    target: Optional[LValue] = None
    op: str = "^"
    expr: Optional[Expr] = None
    span: Optional[Span] = None


@dataclass
class Embed(Stmt):
    """``embed (o ^= e) { classical body }`` -- Bennett's construction."""

    bindings: list[Binding] = field(default_factory=list)
    body: Optional["CBlock"] = None
    #: assigned by the compiler
    proc_name: str = ""


# ---------------------------------------------------------------------------
# classical statements (inside `embed` only)
# ---------------------------------------------------------------------------


@dataclass
class CStmt:
    span: Optional[Span] = None


@dataclass
class CBlock(CStmt):
    stmts: list[CStmt] = field(default_factory=list)


@dataclass
class CVar(CStmt):
    """``var x = e;`` or ``var a[n];``"""

    name: str = ""
    expr: Optional[Expr] = None
    size: Optional[Expr] = None


@dataclass
class CAssign(CStmt):
    target: Optional[LValue] = None
    expr: Optional[Expr] = None


@dataclass
class CIf(CStmt):
    cond: Optional[Expr] = None
    then: Optional[CStmt] = None
    otherwise: Optional[CStmt] = None


@dataclass
class CWhile(CStmt):
    cond: Optional[Expr] = None
    body: Optional[CStmt] = None


@dataclass
class CFor(CStmt):
    init: Optional[CStmt] = None
    cond: Optional[Expr] = None
    step: Optional[CStmt] = None
    body: Optional[CStmt] = None


# ---------------------------------------------------------------------------
# declarations and modules
# ---------------------------------------------------------------------------


@dataclass
class Decl:
    span: Optional[Span] = None


@dataclass
class ConstDecl(Decl):
    name: str = ""
    expr: Optional[Expr] = None
    value: int = 0


@dataclass
class GlobalDecl(Decl):
    name: str = ""
    type: Optional[Type] = None


@dataclass
class Param:
    name: str = ""
    type: Optional[Type] = None
    span: Optional[Span] = None


@dataclass
class ProcDecl(Decl):
    name: str = ""
    params: list[Param] = field(default_factory=list)
    body: Optional[Block] = None
    doc: str = ""


@dataclass
class Import(Decl):
    path: str = ""


@dataclass
class Module:
    decls: list[Decl] = field(default_factory=list)
    source_name: str = "<module>"

    def procs(self) -> list[ProcDecl]:
        return [d for d in self.decls if isinstance(d, ProcDecl)]

    def find_proc(self, name: str) -> Optional[ProcDecl]:
        for d in self.decls:
            if isinstance(d, ProcDecl) and d.name == name:
                return d
        return None


# ---------------------------------------------------------------------------
# traversal
# ---------------------------------------------------------------------------

_CHILD_FIELDS = (
    "stmts",
    "then",
    "otherwise",
    "body",
    "step",
    "init",
)


def children(node) -> list:
    """Structural children of an AST node, in source order."""
    out = []
    for name in ("stmts", "body", "step", "then", "otherwise", "init"):
        val = getattr(node, name, None)
        if isinstance(val, list):
            out.extend(v for v in val if isinstance(v, (Stmt, CStmt)))
        elif isinstance(val, (Stmt, CStmt)):
            out.append(val)
    return out


def walk(node):
    """Depth-first traversal of statements."""
    yield node
    for c in children(node):
        yield from walk(c)


def expressions(node) -> list[Expr]:
    """Every expression directly attached to a statement node."""
    out: list[Expr] = []
    for name in ("expr", "entry", "exit", "cond", "index", "left", "right", "stack"):
        val = getattr(node, name, None)
        if isinstance(val, Expr):
            out.append(val)
    for name in ("args", "parts"):
        val = getattr(node, name, None)
        if isinstance(val, list):
            out.extend(v for v in val if isinstance(v, Expr))
    tgt = getattr(node, "target", None)
    if isinstance(tgt, Expr):
        out.append(tgt)
    var = getattr(node, "var", None)
    if isinstance(var, Expr):
        out.append(var)
    return out


def expr_depth(e: Optional[Expr]) -> int:
    """How deeply an expression tree nests, computed without recursion.

    A long left-associative chain like ``1 + 1 + ... + 1`` is as deep as it is
    long, so the parser has to measure the tree it built rather than count how
    deep it went to build it.
    """
    if e is None:
        return 0
    best = 0
    stack = [(e, 1)]
    while stack:
        node, d = stack.pop()
        if d > best:
            best = d
        if isinstance(node, BinOp):
            for child in (node.left, node.right):
                if child is not None:
                    stack.append((child, d + 1))
        elif isinstance(node, UnOp):
            if node.operand is not None:
                stack.append((node.operand, d + 1))
        elif isinstance(node, Builtin):
            for child in node.args:
                stack.append((child, d + 1))
        elif isinstance(node, Index):
            if node.index is not None:
                stack.append((node.index, d + 1))
    return best


def subexpressions(e: Optional[Expr]) -> list[Expr]:
    """Every node in an expression tree, including *e* itself."""
    if e is None:
        return []
    out = [e]
    i = 0
    while i < len(out):
        node = out[i]
        if isinstance(node, BinOp):
            out.extend(x for x in (node.left, node.right) if x is not None)
        elif isinstance(node, UnOp):
            if node.operand is not None:
                out.append(node.operand)
        elif isinstance(node, Builtin):
            out.extend(node.args)
        elif isinstance(node, Index):
            if node.index is not None:
                out.append(node.index)
        i += 1
    return out


#: builtins whose argument is a *name*, not a value read out of storage
METADATA_BUILTINS = ("len",)
#: builtins whose argument names a stack that really is read
STACK_BUILTINS = ("empty", "top", "size")


def bare_name_ids(e: Optional[Expr], names=("len", "empty", "top", "size")) -> set[int]:
    """Ids of ``Var`` nodes that name a thing rather than read it.

    ``len(a)`` mentions ``a`` but does not look inside it, so it does not count
    as reading the array -- which matters, because ``a[i] <=> a[len(a) - 1 - i]``
    has to be legal for anything like a standard library to exist.
    """
    out: set[int] = set()
    for node in subexpressions(e):
        if isinstance(node, Builtin) and node.name in names:
            for arg in node.args:
                if isinstance(arg, Var):
                    out.add(id(arg))
    return out
