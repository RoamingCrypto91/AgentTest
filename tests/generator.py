"""A generator of random, valid Reverie programs.

The point of this module is the oracle it enables.  For a reversible language
there is a property that needs no expected-output file, no reference
implementation and no hand-written assertions:

    running a program forwards and then backwards must return the machine to
    exactly the state it started in -- every cell, every stack, every line of
    output, every entry of the history tape.

That is a total specification of the machine's behaviour, and it can be checked
on programs nobody wrote.  So this module writes them.

Everything it emits is valid by construction: guard variables used in a
conditional's test are frozen inside its branches, loops carry a fresh
counter, locals are released in the order they were taken, and no call ever
receives the same variable twice.  Generated programs use only ``+=``, ``*=``,
``<<=`` and ``^=``; inverting one therefore produces ``-=``, ``/=`` and ``>>=``,
whose runtime checks (exact division, no discarded bits) are exercised by the
inverse rather than by the original.
"""

from __future__ import annotations

import random
from typing import Optional, Sequence

GUARDS = ["g0", "g1", "g2"]
DATA = ["d0", "d1", "d2", "d3"]
ARRAY = "arr"
ARRAY_LEN = 6
STACK = "st"

HEADER = f"""// generated program
const K = {ARRAY_LEN};
int {'; int '.join(GUARDS)};
int {'; int '.join(DATA)};
int {ARRAY}[K];
stack {STACK};
"""


class Generator:
    def __init__(
        self,
        rng: random.Random,
        *,
        max_depth: int = 3,
        stmts: int = 6,
        allow_embed: bool = True,
        allow_calls: bool = True,
        allow_stack: bool = True,
        allow_print: bool = True,
        allow_undo: bool = True,
    ) -> None:
        self.rng = rng
        self.max_depth = max_depth
        self.stmts = stmts
        self.allow_embed = allow_embed
        self.allow_calls = allow_calls
        self.allow_stack = allow_stack
        self.allow_print = allow_print
        self.allow_undo = allow_undo
        self.counter = 0
        self.frozen: set[str] = set()
        self.loop_depth = 0
        self.helpers: list[str] = []

    # -- helpers ----------------------------------------------------------
    def fresh(self, prefix: str = "t") -> str:
        self.counter += 1
        return f"{prefix}{self.counter}"

    def pick(self, seq: Sequence):
        return self.rng.choice(list(seq))

    def maybe(self, p: float) -> bool:
        return self.rng.random() < p

    # -- expressions ------------------------------------------------------
    def atom(self, extra: Sequence[str] = ()) -> str:
        choices = list(extra) + GUARDS + DATA + [
            str(self.rng.randint(-9, 9)),
            f"{ARRAY}[{self.rng.randrange(ARRAY_LEN)}]",
            "K",
        ]
        if self.allow_stack:
            choices.append(f"size({STACK})")
        return self.pick(choices)

    def expr(self, depth: int = 2, extra: Sequence[str] = ()) -> str:
        if depth <= 0 or self.maybe(0.3):
            return self.atom(extra)
        kind = self.rng.random()
        if kind < 0.12:
            op = self.pick(["-", "!", "~"])
            return f"{op}({self.expr(depth - 1, extra)})"
        if kind < 0.2:
            fn = self.pick(["min", "max"])
            return f"{fn}({self.expr(depth - 1, extra)}, {self.expr(depth - 1, extra)})"
        if kind < 0.26:
            return f"abs({self.expr(depth - 1, extra)})"
        if kind < 0.34:
            # keep divisors non-zero
            return (
                f"({self.expr(depth - 1, extra)} "
                f"{self.pick(['/', '%'])} {self.rng.choice([2, 3, 5, 7, -3])})"
            )
        if kind < 0.4:
            return f"({self.expr(depth - 1, extra)} >> {self.rng.randint(0, 3)})"
        op = self.pick(["+", "-", "*", "&", "|", "^", "<", ">", "==", "!=", "<=", ">="])
        left = self.expr(depth - 1, extra)
        right = self.expr(depth - 1, extra) if op != "*" else str(self.rng.randint(-4, 4))
        return f"({left} {op} {right})"

    def safe_expr(self, depth: int = 2, extra: Sequence[str] = ()) -> str:
        """An expression that does not mention any frozen or excluded name."""
        for _ in range(40):
            e = self.expr(depth, extra)
            if not any(self._mentions(e, n) for n in self.excluded):
                return e
        return str(self.rng.randint(-5, 5))

    @staticmethod
    def _mentions(text: str, name: str) -> bool:
        import re

        return re.search(rf"\b{re.escape(name)}\b", text) is not None

    def condition(self, over: Sequence[str]) -> str:
        a = self.pick(over)
        op = self.pick(["<", ">", "<=", ">=", "==", "!="])
        return f"{a} {op} {self.rng.randint(-4, 4)}"

    # -- statements -------------------------------------------------------
    def writable(self) -> list[str]:
        return [d for d in DATA if d not in self.frozen]

    @property
    def excluded(self) -> set[str]:
        return getattr(self, "_excluded", set())

    def update(self, indent: str) -> list[str]:
        targets = self.writable()
        use_array = self.maybe(0.3)
        if use_array:
            idx = self.rng.randrange(ARRAY_LEN)
            target = f"{ARRAY}[{idx}]"
            self._excluded = {ARRAY}
        elif targets:
            target = self.pick(targets)
            self._excluded = {target}
        else:
            target = ARRAY + f"[{self.rng.randrange(ARRAY_LEN)}]"
            self._excluded = {ARRAY}
        roll = self.rng.random()
        if roll < 0.55:
            op, rhs = self.pick(["+=", "-=", "^="]), self.safe_expr(2)
        elif roll < 0.7:
            op, rhs = "*=", str(self.pick([2, 3, -1, 5]))
        elif roll < 0.8:
            op, rhs = "<<=", str(self.rng.randint(0, 2))
        elif roll < 0.9 and len(targets) >= 2:
            a, b = self.rng.sample(targets, 2)
            self._excluded = set()
            return [f"{indent}{a} <=> {b};"]
        else:
            self._excluded = set()
            return [f"{indent}{self.pick(['neg', 'not'])} {target};"]
        self._excluded = set()
        return [f"{indent}{target} {op} {rhs};"]

    def conditional(self, indent: str, depth: int) -> list[str]:
        guard = self.pick(GUARDS)
        cond = self.condition([guard])
        was = set(self.frozen)
        self.frozen |= {guard}
        if self.maybe(0.5):
            body_then = self.block(indent + "    ", depth - 1, calls=False)
            body_else = self.block(indent + "    ", depth - 1, calls=False)
            self.frozen = was
            return (
                [f"{indent}if {cond} {{"]
                + body_then
                + [f"{indent}}} else {{"]
                + body_else
                + [f"{indent}}} fi;"]
            )
        # a conditional whose exit predicate really does differ per branch:
        # a witness local, released by a second conditional that reads it.
        w = self.fresh("w")
        body_then = self.block(indent + "    ", depth - 1, calls=False)
        body_else = self.block(indent + "    ", depth - 1, calls=False)
        self.frozen = was
        return (
            [f"{indent}local int {w} = 0;", f"{indent}if {cond} {{"]
            + body_then
            + [f"{indent}    {w} += 1;", f"{indent}}} else {{"]
            + body_else
            + [
                f"{indent}}} fi {w} != 0;",
                f"{indent}if {w} != 0 {{",
                f"{indent}    {w} -= 1;",
                f"{indent}}} else {{",
                f"{indent}    skip;",
                f"{indent}}} fi {cond};",
                f"{indent}delocal int {w} = 0;",
            ]
        )

    def loop(self, indent: str, depth: int, calls: bool = True) -> list[str]:
        i = self.fresh("i")
        n = self.rng.randint(0, 4)
        was = set(self.frozen)
        self.frozen |= {i}
        self.loop_depth += 1
        body = self.block(indent + "    ", depth - 1, calls)
        self.loop_depth -= 1
        self.frozen = was
        return (
            [f"{indent}local int {i} = 0;", f"{indent}from {i} == 0 do {{"]
            + body
            + [
                f"{indent}}} loop {{",
                f"{indent}    {i} += 1;",
                f"{indent}}} until {i} == {n};",
                f"{indent}delocal int {i} = {n};",
            ]
        )

    def local_block(self, indent: str, depth: int) -> list[str]:
        name = self.fresh("v")
        init = str(self.rng.randint(-6, 6))
        body: list[str] = []
        for _ in range(self.rng.randint(1, 2)):
            self._excluded = set(DATA) | {ARRAY, name, STACK}
            body.append(f"{indent}    {name} {self.pick(['+=', '-=', '^='])} "
                        f"{self.safe_expr(1)};")
            self._excluded = set()
        # release it by undoing exactly what was done
        undo = [line.replace("+=", "\x00").replace("-=", "+=").replace("\x00", "-=")
                for line in reversed(body)]
        return (
            [f"{indent}{{", f"{indent}    local int {name} = {init};"]
            + body
            + [f"{indent}    {self.pick(self.writable() or DATA)} ^= {name};"]
            + undo
            + [f"{indent}    delocal int {name} = {init};", f"{indent}}}"]
        )

    def stack_stmt(self, indent: str) -> list[str]:
        t = self.fresh("s")
        self._excluded = set()
        if self.maybe(0.6):
            return [
                f"{indent}{{",
                f"{indent}    local int {t} = 0;",
                f"{indent}    {t} += {self.safe_expr(1)};",
                f"{indent}    push({t}, {STACK});",
                f"{indent}    delocal int {t} = 0;",
                f"{indent}}}",
            ]
        target = self.pick(self.writable() or DATA)
        # The branches pop and push the same value back, so `empty(st)` is a
        # legitimate exit predicate: true after the then-branch, false after
        # the else.  (`fi;` would not do here -- the checker is conservative
        # about anything that touches the stack.)
        return [
            f"{indent}if empty({STACK}) == 0 {{",
            f"{indent}    {{",
            f"{indent}        local int {t} = 0;",
            f"{indent}        pop({t}, {STACK});",
            f"{indent}        {target} ^= {t};",
            f"{indent}        push({t}, {STACK});",
            f"{indent}        delocal int {t} = 0;",
            f"{indent}    }}",
            f"{indent}}} else {{",
            f"{indent}    skip;",
            f"{indent}}} fi empty({STACK}) == 0;",
        ]

    def call_stmt(self, indent: str) -> list[str]:
        if not self.helpers:
            self.make_helper()
        name = self.pick(self.helpers)
        a, b = self.rng.sample(DATA, 2)
        kw = "uncall" if self.maybe(0.3) else "call"
        return [f"{indent}{kw} {name}({a}, {b});"]

    def make_helper(self) -> None:
        name = f"helper{len(self.helpers)}"
        self.helpers.append(name)

    def undo_stmt(self, indent: str, depth: int, calls: bool = True) -> list[str]:
        """``undo`` is a partial operation -- it only makes sense on a state
        the block could have produced.  Two shapes are always well defined:

        * ``S; undo { S }`` for arbitrary ``S`` -- do it, then take it back
        * ``undo { ... }`` over updates that are total in both directions
        """
        if self.maybe(0.5):
            body = self.block(indent + "    ", depth - 1, calls)
            return body + [f"{indent}undo {{"] + body + [f"{indent}}}"]
        lines = []
        for _ in range(self.rng.randint(1, 3)):
            target = self.pick(self.writable() or DATA)
            self._excluded = {target}
            lines.append(
                f"{indent}    {target} {self.pick(['+=', '-=', '^='])} "
                f"{self.safe_expr(1)};"
            )
            self._excluded = set()
        return [f"{indent}undo {{"] + lines + [f"{indent}}}"]

    def embed_stmt(self, indent: str) -> list[str]:
        target = self.pick(self.writable() or DATA)
        a, c, n = self.fresh("a"), self.fresh("c"), self.rng.randint(1, 4)
        self._excluded = {target}
        seed = self.safe_expr(1)
        self._excluded = set()
        lines = [
            f"{indent}embed ({target} ^= {a}) {{",
            f"{indent}    var {a} = {seed};",
            f"{indent}    var {c} = 0;",
            f"{indent}    while ({c} < {n}) {{",
            f"{indent}        {c} = {c} + 1;",
            f"{indent}        {a} = {a} + {c} * 2;",
            f"{indent}    }}",
        ]
        if self.maybe(0.6):
            lines += [
                f"{indent}    if ({a} > 0) {{",
                f"{indent}        {a} = {a} - 1;",
                f"{indent}    }} else {{",
                f"{indent}        {a} = {a} + 3;",
                f"{indent}    }}",
            ]
        lines.append(f"{indent}}}")
        return lines

    def print_stmt(self, indent: str) -> list[str]:
        self._excluded = set()
        return [f'{indent}print "v=", {self.safe_expr(1)};']

    def statement(self, indent: str, depth: int, calls: bool = True) -> list[str]:
        roll = self.rng.random()
        if depth > 0:
            if roll < 0.16:
                return self.conditional(indent, depth)
            if roll < 0.32 and self.loop_depth < 2:
                return self.loop(indent, depth, calls)
            if roll < 0.38:
                return self.local_block(indent, depth)
            if roll < 0.44 and self.allow_undo:
                return self.undo_stmt(indent, depth, calls)
        if roll < 0.52 and self.allow_stack:
            return self.stack_stmt(indent)
        if roll < 0.6 and self.allow_calls and calls:
            return self.call_stmt(indent)
        if roll < 0.68 and self.allow_embed:
            return self.embed_stmt(indent)
        if roll < 0.72 and self.allow_print:
            return self.print_stmt(indent)
        return self.update(indent)

    def block(self, indent: str, depth: int, calls: bool = True) -> list[str]:
        n = self.rng.randint(1, max(1, self.stmts // 2))
        out: list[str] = []
        for _ in range(n):
            out.extend(self.statement(indent, depth, calls))
        return out or [f"{indent}skip;"]

    # -- whole module -----------------------------------------------------
    def module(self) -> str:
        self._excluded = set()
        body: list[str] = []
        for _ in range(self.rng.randint(2, self.stmts)):
            body.extend(self.statement("    ", self.max_depth))
        # guards get their own updates at the top level only
        for g in GUARDS:
            if self.maybe(0.5):
                body.append(f"    {g} += {self.rng.randint(-5, 5)};")
        helpers = []
        for name in self.helpers:
            helpers.append(
                f"proc {name}(int p, int q) {{\n"
                f"    p += q * {self.rng.randint(1, 3)};\n"
                f"    q ^= {self.rng.randint(1, 15)};\n"
                f"}}"
            )
        self._excluded = set()
        return (
            HEADER
            + "\nproc main() {\n"
            + "\n".join(body)
            + "\n}\n"
            + ("\n" + "\n\n".join(helpers) + "\n" if helpers else "")
        )


def generate(seed: int, **kw) -> str:
    return Generator(random.Random(seed), **kw).module()
