"""Pure expression / address IR evaluated by the reversible machine.

Reverie splits its instruction set into two tiers:

* **Tier 0 -- pure**: address computations and expressions.  These only *read*
  the store, so they need no inverse: when the machine runs backwards it simply
  re-evaluates them against the (restored) state.
* **Tier 1 -- reversible**: instructions that mutate the store.  Each one has an
  exact inverse (see :mod:`reverie.isa`).

Keeping expressions pure is what makes the whole design work.  A reversible
update such as ``x += f(y)`` is undone by ``x -= f(y)``; that is only correct if
``f(y)`` evaluates to the same value in both directions, which is guaranteed as
long as ``f(y)`` cannot read ``x`` and cannot have side effects.  The former is
enforced statically by :mod:`reverie.checker`, the latter by construction here.
"""

from __future__ import annotations

from typing import Optional, Sequence

from .diagnostics import RuntimeFault, Span

# ---------------------------------------------------------------------------
# integer semantics
# ---------------------------------------------------------------------------
#
# Reverie integers are arbitrary precision.  Division truncates toward zero and
# the remainder takes the sign of the dividend (C / Janus semantics) rather than
# Python's floor semantics, so that ``(a / b) * b + a % b == a`` holds for every
# sign combination.


def idiv(a: int, b: int) -> int:
    if b == 0:
        raise RuntimeFault("division by zero")
    q = abs(a) // abs(b)
    return q if (a >= 0) == (b >= 0) else -q


def imod(a: int, b: int) -> int:
    if b == 0:
        raise RuntimeFault("modulo by zero")
    r = abs(a) % abs(b)
    return r if a >= 0 else -r


def ipow(a: int, b: int) -> int:
    if b < 0:
        raise RuntimeFault(f"negative exponent {b}")
    if b > 1_000_000 or (abs(a) > 1 and b * max(1, abs(a).bit_length()) > 1 << 22):
        raise RuntimeFault("exponent too large (would exhaust memory)")
    return a**b


def truthy(v: int) -> bool:
    return v != 0


# ---------------------------------------------------------------------------
# addresses
# ---------------------------------------------------------------------------


class Addr:
    """An l-value: something that resolves to an absolute cell index."""

    __slots__ = ()

    def resolve(self, m) -> int:  # pragma: no cover - abstract
        raise NotImplementedError

    def render(self) -> str:  # pragma: no cover - abstract
        raise NotImplementedError

    def children(self) -> Sequence["Addr | Expr"]:
        return ()

    def __repr__(self) -> str:
        return self.render()

    def __eq__(self, other) -> bool:
        return type(self) is type(other) and self._key() == other._key()

    def __hash__(self) -> int:
        return hash((type(self).__name__, self._key()))

    def _key(self):  # pragma: no cover - abstract
        raise NotImplementedError


class AbsA(Addr):
    """A fixed global cell."""

    __slots__ = ("index", "name")

    def __init__(self, index: int, name: str = "") -> None:
        self.index = index
        self.name = name

    def resolve(self, m) -> int:
        return self.index

    def render(self) -> str:
        return f"@{self.name or self.index}" if self.name else f"@{self.index}"

    def _key(self):
        return (self.index,)


class LocalA(Addr):
    """A cell in the current frame: ``mem[fp + off]``."""

    __slots__ = ("off", "name")

    def __init__(self, off: int, name: str = "") -> None:
        self.off = off
        self.name = name

    def resolve(self, m) -> int:
        return m.fp + self.off

    def render(self) -> str:
        return f"%{self.name}+{self.off}" if self.name else f"%{self.off}"

    def _key(self):
        return (self.off,)


class ParamA(Addr):
    """A by-reference parameter: the callee frame records the caller's address."""

    __slots__ = ("index", "name")

    def __init__(self, index: int, name: str = "") -> None:
        self.index = index
        self.name = name

    def resolve(self, m) -> int:
        frame = m.frame
        if frame is None:
            raise RuntimeFault("parameter access outside of a procedure frame")
        try:
            return frame.params[self.index]
        except IndexError:  # pragma: no cover - defensive
            raise RuntimeFault(f"parameter #{self.index} not bound")

    def render(self) -> str:
        return f"&{self.name}" if self.name else f"&{self.index}"

    def _key(self):
        return (self.index,)


class IndexA(Addr):
    """``base[index]`` with a static length used for bounds checking."""

    __slots__ = ("base", "index", "length", "name")

    def __init__(self, base: Addr, index: "Expr", length: int, name: str = "") -> None:
        self.base = base
        self.index = index
        self.length = length
        self.name = name

    def resolve(self, m) -> int:
        i = self.index.eval(m)
        if i < 0 or i >= self.length:
            raise RuntimeFault(
                f"index {i} out of bounds for `{self.name or 'array'}` of length {self.length}"
            )
        return self.base.resolve(m) + i

    def render(self) -> str:
        return f"{self.base.render()}[{self.index.render()}]"

    def children(self):
        return (self.base, self.index)

    def _key(self):
        return (self.base, self.index, self.length)


# ---------------------------------------------------------------------------
# expressions
# ---------------------------------------------------------------------------

BINOPS = {
    "+": lambda a, b: a + b,
    "-": lambda a, b: a - b,
    "*": lambda a, b: a * b,
    "/": idiv,
    "%": imod,
    "**": ipow,
    "&": lambda a, b: a & b,
    "|": lambda a, b: a | b,
    "^": lambda a, b: a ^ b,
    "<<": lambda a, b: a << b,
    ">>": lambda a, b: a >> b,
    "==": lambda a, b: int(a == b),
    "!=": lambda a, b: int(a != b),
    "<": lambda a, b: int(a < b),
    "<=": lambda a, b: int(a <= b),
    ">": lambda a, b: int(a > b),
    ">=": lambda a, b: int(a >= b),
    "min": lambda a, b: min(a, b),
    "max": lambda a, b: max(a, b),
}

SHORT_CIRCUIT = {"&&", "||"}

UNOPS = {
    "-": lambda a: -a,
    "+": lambda a: a,
    "~": lambda a: ~a,
    "!": lambda a: int(a == 0),
    "abs": abs,
    "sign": lambda a: (a > 0) - (a < 0),
}


class Expr:
    """A pure, side-effect-free expression over machine state."""

    __slots__ = ("span",)

    def eval(self, m) -> int:  # pragma: no cover - abstract
        raise NotImplementedError

    def render(self) -> str:  # pragma: no cover - abstract
        raise NotImplementedError

    def children(self) -> Sequence["Expr | Addr"]:
        return ()

    def __repr__(self) -> str:
        return self.render()

    def __eq__(self, other) -> bool:
        return type(self) is type(other) and self._key() == other._key()

    def __hash__(self) -> int:
        return hash((type(self).__name__, self._key()))

    def _key(self):  # pragma: no cover - abstract
        raise NotImplementedError


class Const(Expr):
    __slots__ = ("value",)

    def __init__(self, value: int) -> None:
        self.value = int(value)

    def eval(self, m) -> int:
        return self.value

    def render(self) -> str:
        return str(self.value)

    def _key(self):
        return (self.value,)


class Load(Expr):
    __slots__ = ("addr",)

    def __init__(self, addr: Addr) -> None:
        self.addr = addr

    def eval(self, m) -> int:
        return m.mem[self.addr.resolve(m)]

    def render(self) -> str:
        return self.addr.render()

    def children(self):
        return (self.addr,)

    def _key(self):
        return (self.addr,)


class AddrOf(Expr):
    """The numeric address of a cell -- used to pass arrays and stacks around."""

    __slots__ = ("addr",)

    def __init__(self, addr: Addr) -> None:
        self.addr = addr

    def eval(self, m) -> int:
        return self.addr.resolve(m)

    def render(self) -> str:
        return f"addr({self.addr.render()})"

    def children(self):
        return (self.addr,)

    def _key(self):
        return (self.addr,)


class Bin(Expr):
    __slots__ = ("op", "left", "right")

    def __init__(self, op: str, left: Expr, right: Expr) -> None:
        if op not in BINOPS and op not in SHORT_CIRCUIT:
            raise ValueError(f"unknown binary operator {op!r}")
        self.op = op
        self.left = left
        self.right = right

    def eval(self, m) -> int:
        op = self.op
        if op == "&&":
            return 1 if (self.left.eval(m) != 0 and self.right.eval(m) != 0) else 0
        if op == "||":
            return 1 if (self.left.eval(m) != 0 or self.right.eval(m) != 0) else 0
        return BINOPS[op](self.left.eval(m), self.right.eval(m))

    def render(self) -> str:
        if self.op in ("min", "max"):
            return f"{self.op}({self.left.render()}, {self.right.render()})"
        return f"({self.left.render()} {self.op} {self.right.render()})"

    def children(self):
        return (self.left, self.right)

    def _key(self):
        return (self.op, self.left, self.right)


class Un(Expr):
    __slots__ = ("op", "operand")

    def __init__(self, op: str, operand: Expr) -> None:
        if op not in UNOPS:
            raise ValueError(f"unknown unary operator {op!r}")
        self.op = op
        self.operand = operand

    def eval(self, m) -> int:
        return UNOPS[self.op](self.operand.eval(m))

    def render(self) -> str:
        if self.op in ("abs", "sign"):
            return f"{self.op}({self.operand.render()})"
        return f"{self.op}{self.operand.render()}"

    def children(self):
        return (self.operand,)

    def _key(self):
        return (self.op, self.operand)


STACK_QUERIES = ("empty", "top", "size")


class StackQuery(Expr):
    """``empty(s)`` / ``top(s)`` / ``size(s)`` -- pure reads of a stack object."""

    __slots__ = ("kind", "addr")

    def __init__(self, kind: str, addr: Addr) -> None:
        if kind not in STACK_QUERIES:
            raise ValueError(f"unknown stack query {kind!r}")
        self.kind = kind
        self.addr = addr

    def eval(self, m) -> int:
        st = m.stack_at(self.addr.resolve(m))
        if self.kind == "empty":
            return int(not st)
        if self.kind == "size":
            return len(st)
        if not st:
            raise RuntimeFault("top() of an empty stack")
        return st[-1]

    def render(self) -> str:
        return f"{self.kind}({self.addr.render()})"

    def children(self):
        return (self.addr,)

    def _key(self):
        return (self.kind, self.addr)


# ---------------------------------------------------------------------------
# analysis helpers
# ---------------------------------------------------------------------------


def walk(node) -> "list":
    """Depth-first list of every Expr/Addr node reachable from *node*."""
    out = [node]
    i = 0
    while i < len(out):
        for c in out[i].children():
            out.append(c)
        i += 1
    return out


def reads_cell(node, addr: Addr) -> bool:
    """Conservatively decide whether *node* may read the cell named by *addr*."""
    for n in walk(node):
        if isinstance(n, (Load, StackQuery)) and may_alias(n.addr, addr):
            return True
    return False


def may_alias(a: Addr, b: Addr) -> bool:
    """Conservative aliasing test between two address expressions."""
    if isinstance(a, IndexA) or isinstance(b, IndexA):
        base_a = a.base if isinstance(a, IndexA) else a
        base_b = b.base if isinstance(b, IndexA) else b
        return may_alias(base_a, base_b)
    if type(a) is not type(b):
        # Different address families may still alias through a by-reference
        # parameter, which can point anywhere.
        return isinstance(a, ParamA) or isinstance(b, ParamA)
    return a == b


def constant_fold(e: Expr) -> Expr:
    """Fold constant sub-expressions; used to keep generated code readable."""
    if isinstance(e, Bin):
        left = constant_fold(e.left)
        right = constant_fold(e.right)
        if isinstance(left, Const) and isinstance(right, Const):
            if e.op in SHORT_CIRCUIT:
                if e.op == "&&":
                    return Const(1 if (left.value and right.value) else 0)
                return Const(1 if (left.value or right.value) else 0)
            try:
                return Const(BINOPS[e.op](left.value, right.value))
            except RuntimeFault:
                return Bin(e.op, left, right)
        return Bin(e.op, left, right)
    if isinstance(e, Un):
        operand = constant_fold(e.operand)
        if isinstance(operand, Const):
            return Const(UNOPS[e.op](operand.value))
        return Un(e.op, operand)
    return e


def logical_not(e: Expr) -> Expr:
    """Build ``!e``, folding double negation and flipping comparisons."""
    if isinstance(e, Un) and e.op == "!":
        inner = e.operand
        if isinstance(inner, Bin) and inner.op in ("==", "!=", "<", "<=", ">", ">="):
            return inner
        if isinstance(inner, Un) and inner.op == "!":
            return logical_not(inner.operand)
        return Un("!", e)
    flip = {"==": "!=", "!=": "==", "<": ">=", ">=": "<", ">": "<=", "<=": ">"}
    if isinstance(e, Bin) and e.op in flip:
        return Bin(flip[e.op], e.left, e.right)
    if isinstance(e, Const):
        return Const(int(e.value == 0))
    return Un("!", e)
