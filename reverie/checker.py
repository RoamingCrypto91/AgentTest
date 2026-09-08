"""Name resolution, storage layout, and the static reversibility rules.

This pass answers two different questions at once.

The ordinary compiler question -- what does each name refer to, how big is
each frame, does this call have the right number of arguments -- and the
question that makes Reverie a reversible language rather than an imperative one
with reversible-looking syntax:

    *Is this program actually injective?*

A handful of syntactic rules are enough to guarantee it:

1. **No self-reference in an update.**  ``x += f(...)`` is undone by
   ``x -= f(...)``, which is only correct if ``f`` evaluates identically before
   and after -- so ``x`` may not appear in ``f``.  For an indexed target
   ``a[i] += e`` neither ``e`` nor ``i`` may mention ``a``.
2. **Balanced locals.**  Every ``local`` needs a matching ``delocal`` in the
   same block, last-in-first-out, so a frame is provably zero when it is
   released.  Nothing is ever dropped on the floor.
3. **No aliasing across arguments.**  Arguments are passed by reference; two
   parameters bound to the same cell would let a procedure violate rule 1
   without any local evidence of it.
4. **Classical code stays in its box.**  Inside ``embed`` you may assign
   destructively, but only to variables the block itself declared; enclosing
   state is read-only, because it is not on the history tape.

Violations are reported with spans and, where there is an obvious fix,
a note suggesting it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Optional, Sequence

from . import ast
from .diagnostics import CheckError, Diagnostics, Span
from .parser import BUILTINS

# ---------------------------------------------------------------------------
# symbols
# ---------------------------------------------------------------------------


@dataclass
class Symbol:
    name: str
    kind: str  # const | global | local | param | ctemp | cparam
    type: str  # int | array | stack
    length: int = 1
    addr: int = -1
    off: int = -1
    index: int = -1
    value: int = 0
    span: Optional[Span] = None
    stack_id: int = 0
    #: for `cparam` symbols: the enclosing symbol this one stands in for
    origin: Optional["Symbol"] = None

    @property
    def is_array(self) -> bool:
        return self.type == "array"


@dataclass
class EmbedPlan:
    """Everything the compiler needs to lower one ``embed`` block."""

    node: ast.Embed
    proc_name: str
    free: list[Symbol] = field(default_factory=list)  # enclosing locals/params
    ctemps: list[Symbol] = field(default_factory=list)  # classical variables
    outputs: list[Symbol] = field(default_factory=list)  # synthetic result cells
    counters: int = 0
    body: Optional[ast.CBlock] = None

    @property
    def params(self) -> list[Symbol]:
        return self.free + self.ctemps


@dataclass
class ConstArg:
    """A compile-time constant passed by reference through a hidden local."""

    index: int
    value: int
    off: int = -1
    name: str = ""


@dataclass
class ProcAnalysis:
    decl: ast.ProcDecl
    params: list[Symbol] = field(default_factory=list)
    frame_size: int = 0
    embeds: list[EmbedPlan] = field(default_factory=list)
    #: parameter positions this procedure may write to, directly or through a
    #: call it makes.  Computed to a fixpoint over the call graph.
    writes: set[int] = field(default_factory=set)
    #: globals this procedure may touch, directly or through a call it makes
    touches: set[str] = field(default_factory=set)


@dataclass
class Analysis:
    consts: dict[str, int] = field(default_factory=dict)
    globals: dict[str, Symbol] = field(default_factory=dict)
    n_globals: int = 0
    n_stacks: int = 0
    procs: dict[str, ProcAnalysis] = field(default_factory=dict)
    #: id(ast node) -> Symbol, for every Var / Index reference
    uses: dict[int, Symbol] = field(default_factory=dict)
    #: id(Embed node) -> plan
    plans: dict[int, EmbedPlan] = field(default_factory=dict)
    #: id(Call node) -> constant arguments needing a hidden local
    const_args: dict[int, list[ConstArg]] = field(default_factory=dict)
    diagnostics: Diagnostics = field(default_factory=Diagnostics)
    module: Optional[ast.Module] = None

    @property
    def ok(self) -> bool:
        return self.diagnostics.ok


class Scope:
    def __init__(self, parent: Optional["Scope"] = None) -> None:
        self.parent = parent
        self.names: dict[str, Symbol] = {}

    def lookup(self, name: str) -> Optional[Symbol]:
        s: Optional[Scope] = self
        while s is not None:
            if name in s.names:
                return s.names[name]
            s = s.parent
        return None

    def declare(self, sym: Symbol) -> None:
        self.names[sym.name] = sym


# ---------------------------------------------------------------------------
# the analyzer
# ---------------------------------------------------------------------------


class Analyzer:
    def __init__(self, module: ast.Module, require_main: bool = True) -> None:
        self.module = module
        self.require_main = require_main
        self.a = Analysis(module=module)
        self.d = self.a.diagnostics
        self.embed_counter = 0

    # -- helpers ----------------------------------------------------------
    def error(self, message: str, span: Optional[Span], **kw) -> None:
        self.d.error(message, span, **kw)

    def quiet(self):
        """Context for "only report this if nothing else went wrong"."""
        return len(self.d.errors)

    def clean_since(self, mark: int) -> bool:
        return len(self.d.errors) == mark

    def const_eval(self, e: Optional[ast.Expr], what: str) -> int:
        """Evaluate a compile-time constant expression."""
        if e is None:
            self.error(f"{what} requires a constant", None)
            return 0
        if isinstance(e, ast.Num):
            return e.value
        if isinstance(e, ast.Var):
            if e.name in self.a.consts:
                return self.a.consts[e.name]
            sym = self.a.globals.get(e.name)
            if sym is not None and sym.is_array:
                self.error(f"{what} cannot depend on the array `{e.name}`", e.span)
                return 0
            self.error(
                f"`{e.name}` is not a compile-time constant", e.span,
                notes=[f"{what} must be known when the program is compiled"],
            )
            return 0
        if isinstance(e, ast.BinOp):
            from .ir import BINOPS, idiv

            left = self.const_eval(e.left, what)
            right = self.const_eval(e.right, what)
            if e.op in ("&&", "||"):
                return int(bool(left) and bool(right)) if e.op == "&&" else int(
                    bool(left) or bool(right)
                )
            if e.op in ("/", "%") and right == 0:
                self.error("division by zero in a constant expression", e.span)
                return 0
            return BINOPS[e.op](left, right)
        if isinstance(e, ast.UnOp):
            from .ir import UNOPS

            return UNOPS[e.op](self.const_eval(e.operand, what))
        if isinstance(e, ast.Builtin):
            if e.name == "len":
                arg = e.args[0]
                if isinstance(arg, ast.Var):
                    sym = self.current_lookup(arg.name)
                    if sym is not None and sym.is_array:
                        if sym.length < 0:
                            self.error(
                                f"`len({arg.name})` is not known at compile time",
                                e.span,
                                notes=[
                                    "the length of a `[]` parameter depends on the",
                                    "caller, so it cannot size a declaration",
                                ],
                            )
                            return 0
                        return sym.length
                self.error("`len` needs an array name", e.span)
                return 0
            if e.name in ("min", "max", "abs", "sign"):
                from .ir import BINOPS, UNOPS

                vals = [self.const_eval(a, what) for a in e.args]
                if e.name in ("min", "max"):
                    return BINOPS[e.name](vals[0], vals[1])
                return UNOPS[e.name](vals[0])
        self.error(f"{what} must be a constant expression", getattr(e, "span", None))
        return 0

    def current_lookup(self, name: str) -> Optional[Symbol]:
        scope = getattr(self, "scope", None)
        if scope is not None:
            sym = scope.lookup(name)
            if sym is not None:
                return sym
        return self.a.globals.get(name)

    # -- entry point ------------------------------------------------------
    def run(self) -> Analysis:
        self.collect_globals()
        self.collect_procs()
        self.compute_parameter_writes()
        self.compute_global_touches()
        for proc in self.module.procs():
            self.analyze_proc(proc)
        if "main" not in self.a.procs:
            if self.require_main:
                self.error(
                    "no `main` procedure", None,
                    notes=["execution starts at `proc main()`"],
                )
        else:
            main = self.a.procs["main"]
            if main.decl.params:
                self.error(
                    "`main` must not take parameters", main.decl.span,
                    notes=["initial state comes from global declarations"],
                )
        return self.a

    # -- declarations -----------------------------------------------------
    def collect_globals(self) -> None:
        addr = 0
        seen: dict[str, Span] = {}
        for d in self.module.decls:
            if isinstance(d, ast.ConstDecl):
                if d.name in seen:
                    self.error(f"`{d.name}` is declared twice", d.span)
                seen[d.name] = d.span
                d.value = self.const_eval(d.expr, "a const initialiser")
                self.a.consts[d.name] = d.value
            elif isinstance(d, ast.GlobalDecl):
                if d.name in seen:
                    self.error(f"`{d.name}` is declared twice", d.span)
                seen[d.name] = d.span
                if isinstance(d.type, ast.TArray):
                    mark = self.quiet()
                    n = self.const_eval(d.type.size, "an array length")
                    if n <= 0:
                        if self.clean_since(mark):
                            self.error(
                                f"array `{d.name}` must have a positive length, "
                                f"got {n}",
                                d.span,
                            )
                        n = 1
                    d.type.length = n
                    sym = Symbol(d.name, "global", "array", n, addr=addr, span=d.span)
                    addr += n
                elif isinstance(d.type, ast.TStack):
                    self.a.n_stacks += 1
                    sym = Symbol(
                        d.name, "global", "stack", 1, addr=addr, span=d.span,
                        stack_id=self.a.n_stacks,
                    )
                    addr += 1
                else:
                    sym = Symbol(d.name, "global", "int", 1, addr=addr, span=d.span)
                    addr += 1
                self.a.globals[d.name] = sym
        self.a.n_globals = addr

    def collect_procs(self) -> None:
        for p in self.module.procs():
            if p.name in self.a.procs:
                self.error(f"procedure `{p.name}` is declared twice", p.span)
                continue
            if p.name.startswith("__embed"):
                self.error(
                    f"`{p.name}` collides with a compiler-generated name", p.span
                )
            params: list[Symbol] = []
            for i, prm in enumerate(p.params):
                if isinstance(prm.type, ast.TArray):
                    if prm.type.size is None:
                        # `int a[]` -- length comes from whatever is passed in.
                        n = -1
                    else:
                        n = self.const_eval(prm.type.size, "an array parameter length")
                    prm.type.length = n
                    params.append(Symbol(prm.name, "param", "array", n, index=i, span=prm.span))
                elif isinstance(prm.type, ast.TStack):
                    params.append(Symbol(prm.name, "param", "stack", 1, index=i, span=prm.span))
                else:
                    params.append(Symbol(prm.name, "param", "int", 1, index=i, span=prm.span))
            seen = set()
            for s in params:
                if s.name in seen:
                    self.error(f"duplicate parameter `{s.name}`", s.span)
                seen.add(s.name)
            self.a.procs[p.name] = ProcAnalysis(p, params)

    def compute_parameter_writes(self) -> None:
        """Which parameters can a procedure write?

        Reverie passes everything by reference, which normally means every
        argument has to name a cell the caller owns.  But a parameter a
        procedure only ever *reads* is safe to bind to a constant, because
        nothing can come back through it -- so ``call fill(xs, 0)`` is
        meaningful while ``call bump(counter, 0)`` is not.  The analysis is the
        obvious fixpoint: start from direct writes, then propagate through
        calls until nothing changes.
        """
        index_of: dict[str, dict[str, int]] = {}
        for name, info in self.a.procs.items():
            index_of[name] = {p.name: i for i, p in enumerate(info.params)}
            for node in ast.walk(info.decl.body) if info.decl.body else ():
                for written in self._direct_writes(node):
                    i = index_of[name].get(written)
                    if i is not None:
                        info.writes.add(i)
        changed = True
        while changed:
            changed = False
            for name, info in self.a.procs.items():
                if info.decl.body is None:
                    continue
                for node in ast.walk(info.decl.body):
                    if not isinstance(node, ast.Call):
                        continue
                    callee = self.a.procs.get(node.name)
                    if callee is None:
                        continue
                    for pos, arg in enumerate(node.args):
                        if pos not in callee.writes:
                            continue
                        if not isinstance(arg, ast.Var):
                            continue
                        i = index_of[name].get(arg.name)
                        if i is not None and i not in info.writes:
                            info.writes.add(i)
                            changed = True

    def compute_global_touches(self) -> None:
        """Which globals can a procedure reach?

        Parameters are references.  If a caller passes global ``g`` to a
        procedure that also mentions ``g`` by name, the parameter and the global
        are two names for one cell -- and ``p += g`` inside that procedure is
        secretly ``p += p``, which is not reversible.  Nothing local to the
        procedure reveals this, so it has to be caught where the aliasing is
        created: at the call.
        """
        names = set(self.a.globals)
        for name, info in self.a.procs.items():
            params = {p.name for p in info.params}
            mentioned: set[str] = set()
            if info.decl.body is not None:
                for node in ast.walk(info.decl.body):
                    if isinstance(node, ast.CStmt):
                        continue  # classical blocks are handled as a unit below
                    for e in ast.expressions(node):
                        for sub in ast.subexpressions(e):
                            if isinstance(sub, (ast.Var, ast.Index)):
                                mentioned.add(sub.name)
                    if isinstance(node, ast.Embed):
                        for b in node.bindings:
                            if b.target is not None:
                                mentioned.add(b.target.name)
                        # names the classical block declares itself are its
                        # own, however they happen to be spelled
                        declared, used = self._classical_names(node.body)
                        for b in node.bindings:
                            for sub in ast.subexpressions(b.expr):
                                if isinstance(sub, (ast.Var, ast.Index)):
                                    used.add(sub.name)
                        mentioned |= used - declared
            info.touches = (mentioned & names) - params
        changed = True
        while changed:
            changed = False
            for name, info in self.a.procs.items():
                if info.decl.body is None:
                    continue
                for node in ast.walk(info.decl.body):
                    if isinstance(node, ast.Call):
                        callee = self.a.procs.get(node.name)
                        if callee is None:
                            continue
                        extra = callee.touches - {p.name for p in info.params}
                        if not extra <= info.touches:
                            info.touches |= extra
                            changed = True

    @staticmethod
    def _classical_names(block) -> tuple[set[str], set[str]]:
        """(names the block declares, names it mentions)."""
        declared: set[str] = set()
        used: set[str] = set()
        stack = [block]
        while stack:
            node = stack.pop()
            if node is None:
                continue
            if isinstance(node, ast.CVar):
                declared.add(node.name)
            for attr in ("expr", "cond", "size"):
                for sub in ast.subexpressions(getattr(node, attr, None)):
                    if isinstance(sub, (ast.Var, ast.Index)):
                        used.add(sub.name)
            target = getattr(node, "target", None)
            if isinstance(target, (ast.Var, ast.Index)):
                used.add(target.name)
            for attr in ("stmts", "then", "otherwise", "body", "init", "step"):
                val = getattr(node, attr, None)
                if isinstance(val, list):
                    stack.extend(val)
                elif val is not None:
                    stack.append(val)
        return declared, used

    @staticmethod
    def _direct_writes(node) -> set[str]:
        out: set[str] = set()
        if isinstance(node, (ast.Update, ast.UnaryStmt)) and node.target is not None:
            out.add(node.target.name)
        elif isinstance(node, ast.Swap):
            for t in (node.left, node.right):
                if t is not None:
                    out.add(t.name)
        elif isinstance(node, ast.StackOp):
            if node.var is not None:
                out.add(node.var.name)
            if isinstance(node.stack, (ast.Var, ast.Index)):
                out.add(node.stack.name)
        elif isinstance(node, ast.Embed):
            for b in node.bindings:
                if b.target is not None:
                    out.add(b.target.name)
        return out

    # -- procedure bodies -------------------------------------------------
    def analyze_proc(self, p: ast.ProcDecl) -> None:
        info = self.a.procs.get(p.name)
        if info is None:
            return
        self.proc = info
        self.scope = Scope(None)
        for sym in info.params:
            # A parameter shadowing a global is fine, and in fact safer: inside
            # the body the global is simply unreachable, so it cannot alias the
            # parameter.  The hazard the checker cares about is the other
            # shape -- passing a global to a procedure that *also* names it
            # directly -- and that is caught at the call site.
            self.scope.declare(sym)
        self.next_off = 0
        self.max_off = 0
        self.in_embed = False
        self.analyze_block(p.body, top=True)
        info.frame_size = self.max_off

    def alloc(self, size: int) -> int:
        off = self.next_off
        self.next_off += size
        self.max_off = max(self.max_off, self.next_off)
        return off

    def analyze_block(self, block: Optional[ast.Stmt], top: bool = False) -> None:
        if block is None:
            return
        if not isinstance(block, ast.Block):
            self.analyze_stmt(block)
            return
        parent_scope = self.scope
        self.scope = Scope(parent_scope)
        saved_off = self.next_off
        opened: list[Symbol] = []
        for s in block.stmts:
            if isinstance(s, ast.LocalDecl) and not s.release:
                sym = self.declare_local(s)
                if sym is not None:
                    opened.append(sym)
                continue
            if isinstance(s, ast.LocalDecl) and s.release:
                self.release_local(s, opened)
                continue
            self.analyze_stmt(s)
        if opened:
            names = ", ".join(f"`{s.name}`" for s in reversed(opened))
            self.error(
                f"{'this local is' if len(opened) == 1 else 'these locals are'} "
                f"never released: {names}",
                opened[-1].span,
                notes=[
                    "add a matching `delocal` before the end of the block --",
                    "leaving the block would otherwise erase the cell",
                ],
            )
        self.scope = parent_scope
        self.next_off = saved_off

    def declare_local(self, s: ast.LocalDecl) -> Optional[Symbol]:
        if self.scope.lookup(s.name) is not None or s.name in self.a.globals:
            self.error(
                f"`{s.name}` is already in scope", s.span,
                notes=["locals may not shadow other names"],
            )
        if isinstance(s.type, ast.TStack):
            self.error("local stacks are not supported", s.span,
                       notes=["declare stacks at module scope"])
            return None
        if isinstance(s.type, ast.TArray):
            mark = self.quiet()
            n = self.const_eval(s.type.size, "a local array length")
            if n <= 0:
                if self.clean_since(mark):
                    self.error(
                        f"local array `{s.name}` needs a positive length", s.span
                    )
                n = 1
            s.type.length = n
            if s.expr is not None:
                self.error("local arrays are created zeroed and take no initialiser",
                           s.span)
            sym = Symbol(s.name, "local", "array", n, off=self.alloc(n), span=s.span)
        else:
            if s.expr is None:
                self.error(
                    f"`local {s.name}` needs an initialiser", s.span,
                    notes=["write `local int " + s.name + " = 0;` for a zeroed cell"],
                )
            else:
                self.analyze_expr(s.expr)
                self.forbid_reference(s.expr, s.name, "a local's initialiser")
            sym = Symbol(s.name, "local", "int", 1, off=self.alloc(1), span=s.span)
        self.scope.declare(sym)
        self.a.uses[id(s)] = sym
        return sym

    def release_local(self, s: ast.LocalDecl, opened: list[Symbol]) -> None:
        if not opened:
            self.error(
                f"`delocal {s.name}` has no matching `local` in this block", s.span
            )
            return
        sym = opened[-1]
        if sym.name != s.name:
            self.error(
                f"expected `delocal {sym.name}` but found `delocal {s.name}`",
                s.span,
                notes=[
                    "locals are released last-in-first-out so the frame unwinds",
                    "exactly the way it was built",
                ],
            )
            # Recover by releasing whichever local was actually named, so a
            # block that closes its locals out of order reports once rather
            # than once per mismatch.
            for other in reversed(opened):
                if other.name == s.name:
                    opened.remove(other)
                    self.scope.names.pop(other.name, None)
                    self.next_off -= other.length
                    break
            else:
                opened.pop()
                self.scope.names.pop(sym.name, None)
                self.next_off -= sym.length
            return
        want_array = isinstance(s.type, ast.TArray)
        if want_array != sym.is_array:
            self.error(f"`delocal {s.name}` does not match its declaration", s.span)
            return
        if sym.is_array:
            n = self.const_eval(s.type.size, "a local array length")
            if n != sym.length:
                self.error(
                    f"`delocal {s.name}` has length {n}, declared as {sym.length}",
                    s.span,
                )
            s.type.length = sym.length
        else:
            if s.expr is None:
                self.error(
                    f"`delocal {s.name}` needs an expression reproducing its value",
                    s.span,
                    notes=[
                        "the cell can only be released if its contents can be",
                        "reconstructed -- otherwise releasing it would erase them",
                    ],
                )
            else:
                self.analyze_expr(s.expr)
                self.forbid_reference(s.expr, s.name, "a delocal expression")
        opened.pop()
        self.a.uses[id(s)] = sym
        self.scope.names.pop(sym.name, None)
        self.next_off -= sym.length

    # -- statements -------------------------------------------------------
    def analyze_stmt(self, s: ast.Stmt) -> None:
        if isinstance(s, ast.Skip):
            return
        if isinstance(s, ast.Block):
            self.analyze_block(s)
            return
        if isinstance(s, ast.Update):
            self.analyze_update(s)
            return
        if isinstance(s, ast.UnaryStmt):
            self.analyze_lvalue(s.target, "an in-place operation")
            return
        if isinstance(s, ast.Swap):
            self.analyze_swap(s)
            return
        if isinstance(s, ast.If):
            self.analyze_if(s)
            return
        if isinstance(s, ast.Loop):
            self.analyze_expr(s.entry)
            self.analyze_expr(s.exit)
            self.analyze_block(s.body)
            self.analyze_block(s.step)
            return
        if isinstance(s, ast.LocalDecl):
            # a local/delocal outside of a block context
            self.error(
                f"`{'delocal' if s.release else 'local'} {s.name}` must appear "
                f"directly inside a block",
                s.span,
            )
            return
        if isinstance(s, ast.Call):
            self.analyze_call(s)
            return
        if isinstance(s, ast.StackOp):
            self.analyze_stack_op(s)
            return
        if isinstance(s, ast.Print):
            for p in s.parts:
                if isinstance(p, ast.Expr):
                    self.analyze_expr(p)
            return
        if isinstance(s, ast.Assert):
            self.analyze_expr(s.expr)
            return
        if isinstance(s, ast.Undo):
            self.analyze_block(s.body)
            return
        if isinstance(s, ast.Embed):
            self.analyze_embed(s)
            return
        self.error(f"unsupported statement {type(s).__name__}", s.span)

    def analyze_update(self, s: ast.Update) -> None:
        base = self.analyze_lvalue(s.target, "an update target")
        self.analyze_expr(s.expr)
        if base is None:
            return
        if base.kind == "const":
            return  # already reported by analyze_lvalue
        if base.type == "stack":
            self.error(
                f"cannot update the stack `{base.name}` directly", s.span,
                notes=["use `push` and `pop`"],
            )
            return
        self.forbid_reference(s.expr, base.name, "the right-hand side of an update")
        if isinstance(s.target, ast.Index):
            self.forbid_reference(
                s.target.index, base.name, "an index on the left of an update"
            )

    def analyze_swap(self, s: ast.Swap) -> None:
        left = self.analyze_lvalue(s.left, "a swap operand")
        right = self.analyze_lvalue(s.right, "a swap operand")
        for target, sym in ((s.left, left), (s.right, right)):
            if sym is not None and sym.type == "stack":
                self.error(f"cannot swap the stack `{sym.name}`", s.span)
        names = {sym.name for sym in (left, right) if sym is not None}
        for target in (s.left, s.right):
            if isinstance(target, ast.Index):
                for name in sorted(names):
                    self.forbid_reference(target.index, name, "a swap index")

    def analyze_if(self, s: ast.If) -> None:
        self.analyze_expr(s.entry)
        self.analyze_block(s.then)
        self.analyze_block(s.otherwise)
        if s.exit is s.entry:
            # `fi;` sugar: only safe when the branches cannot disturb the test
            touched = set()
            for branch in (s.then, s.otherwise):
                touched |= self.assigned_names(branch)
            metadata = ast.bare_name_ids(s.entry, ast.METADATA_BUILTINS)
            used = {
                v.name
                for v in ast.subexpressions(s.entry)
                if isinstance(v, (ast.Var, ast.Index)) and id(v) not in metadata
            }
            clash = sorted(touched & used)
            if clash:
                self.error(
                    "`fi;` reuses the entry test, but "
                    + ", ".join(f"`{c}`" for c in clash)
                    + (" is" if len(clash) == 1 else " are")
                    + " modified inside the conditional",
                    s.span,
                    notes=[
                        "write an explicit exit predicate: `} fi <expr>;`",
                        "it must be true after the then-branch and false after the else",
                    ],
                )
        else:
            self.analyze_expr(s.exit)

    def assigned_names(self, s: Optional[ast.Stmt]) -> set[str]:
        """Names a statement may write, including through calls (conservatively)."""
        out: set[str] = set()
        if s is None:
            return out
        for node in ast.walk(s):
            if isinstance(node, ast.Update) and node.target is not None:
                out.add(node.target.name)
            elif isinstance(node, ast.UnaryStmt) and node.target is not None:
                out.add(node.target.name)
            elif isinstance(node, ast.Swap):
                for t in (node.left, node.right):
                    if t is not None:
                        out.add(t.name)
            elif isinstance(node, ast.StackOp):
                if node.var is not None:
                    out.add(node.var.name)
                if isinstance(node.stack, (ast.Var, ast.Index)):
                    out.add(node.stack.name)
            elif isinstance(node, ast.Call):
                for arg in node.args:
                    if isinstance(arg, (ast.Var, ast.Index)):
                        out.add(arg.name)
                # a callee may touch any global
                out |= set(self.a.globals)
            elif isinstance(node, ast.Embed):
                for b in node.bindings:
                    if b.target is not None:
                        out.add(b.target.name)
        return out

    def analyze_call(self, s: ast.Call) -> None:
        info = self.a.procs.get(s.name)
        if info is None:
            known = sorted(self.a.procs)
            self.error(
                f"unknown procedure `{s.name}`", s.span,
                notes=["declared procedures: " + ", ".join(known)] if known else (),
            )
            for a in s.args:
                self.analyze_expr(a)
            return
        if len(s.args) != len(info.params):
            self.error(
                f"`{s.name}` takes {len(info.params)} argument(s), "
                f"{len(s.args)} given",
                s.span,
            )
        seen: dict[str, ast.Expr] = {}
        consts: list[ConstArg] = []
        base_off = self.next_off
        for i, arg in enumerate(s.args):
            if not isinstance(arg, ast.Var) or (
                arg.name in self.a.consts and self.scope.lookup(arg.name) is None
            ):
                if self._constant_argument(s, info, i, arg, consts):
                    continue
                self.error(
                    "arguments must name a variable, or be a compile-time constant "
                    "bound to a parameter the callee never writes",
                    getattr(arg, "span", s.span),
                    notes=[
                        "parameters are passed by reference, so an argument has to",
                        "name a cell; compute into a `local` first if you need a",
                        "value the callee will write back through",
                    ],
                )
                self.analyze_expr(arg)
                continue
            sym = self.resolve(arg.name, arg.span)
            if sym is None:
                continue
            self.a.uses[id(arg)] = sym
            if sym.kind == "const":
                self.error(f"cannot pass the constant `{sym.name}` by reference", arg.span)
                continue
            if arg.name in seen:
                self.error(
                    f"`{arg.name}` is passed twice to `{s.name}`", arg.span,
                    notes=[
                        "two parameters bound to the same cell would let the callee",
                        "update a variable using itself",
                    ],
                )
            seen[arg.name] = arg
            if sym.kind == "global" and arg.name in info.touches:
                self.error(
                    f"`{s.name}` refers to the global `{arg.name}` by name, so it "
                    f"cannot also receive it as an argument",
                    arg.span,
                    label="aliases a global the callee already uses",
                    notes=[
                        "the parameter and the global would be two names for one",
                        "cell, and an update could then read the cell it writes",
                        "rename the global, or copy it into a `local` first",
                    ],
                )
            if i < len(info.params):
                want = info.params[i]
                if want.type != sym.type:
                    self.error(
                        f"argument {i + 1} of `{s.name}` is `{want.type}`, "
                        f"but `{sym.name}` is `{sym.type}`",
                        arg.span,
                    )
                elif (
                    want.type == "array"
                    and want.length >= 0
                    and sym.length >= 0
                    and want.length != sym.length
                ):
                    self.error(
                        f"argument {i + 1} of `{s.name}` expects length "
                        f"{want.length}, `{sym.name}` has length {sym.length}",
                        arg.span,
                    )
        self.next_off = base_off

    def _constant_argument(
        self,
        call: ast.Call,
        info: "ProcAnalysis",
        pos: int,
        arg: ast.Expr,
        consts: list[ConstArg],
    ) -> bool:
        """Try to pass *arg* as a constant through a hidden local."""
        if pos >= len(info.params):
            return False
        want = info.params[pos]
        if want.type != "int":
            return False
        if pos in info.writes:
            self.error(
                f"`{info.decl.name}` writes to parameter `{want.name}`, so "
                f"argument {pos + 1} must name a variable",
                getattr(arg, "span", call.span),
                notes=["a constant has nowhere to receive the result"],
            )
            return True
        saved = list(self.d.errors)
        value = self.const_eval(arg, "a constant argument")
        if len(self.d.errors) != len(saved):
            self.d.errors[:] = saved
            return False
        name = f"__arg{len(self.a.const_args)}_{pos}"
        entry = ConstArg(pos, value, self.alloc(1), name)
        consts.append(entry)
        self.a.const_args.setdefault(id(call), []).append(entry)
        return True

    def analyze_stack_op(self, s: ast.StackOp) -> None:
        sym = self.analyze_lvalue(s.var, "a push/pop target")
        if sym is not None and sym.type != "int" and not isinstance(s.var, ast.Index):
            self.error(f"`{sym.name}` is not an integer cell", s.span)
        if not isinstance(s.stack, ast.Var):
            self.error("the second argument of push/pop must be a stack name",
                       getattr(s.stack, "span", s.span))
            return
        st = self.resolve(s.stack.name, s.stack.span)
        if st is None:
            return
        self.a.uses[id(s.stack)] = st
        if st.type != "stack":
            self.error(f"`{st.name}` is not a stack", s.stack.span)
        if sym is not None and sym.name == st.name:
            self.error("cannot push a stack onto itself", s.span)

    # -- expressions ------------------------------------------------------
    def resolve(self, name: str, span: Optional[Span]) -> Optional[Symbol]:
        sym = self.scope.lookup(name)
        if sym is not None:
            return sym
        if name in self.a.globals:
            return self.a.globals[name]
        if name in self.a.consts:
            return Symbol(name, "const", "int", value=self.a.consts[name], span=span)
        candidates = (
            set(self.scope.names) | set(self.a.globals) | set(self.a.consts)
        )
        hint = _closest(name, candidates)
        self.error(
            f"unknown name `{name}`",
            span,
            notes=[f"did you mean `{hint}`?"] if hint else (),
        )
        return None

    def analyze_expr(self, e: Optional[ast.Expr]) -> None:
        if e is None:
            return
        bare = ast.bare_name_ids(e)
        for node in ast.subexpressions(e):
            if id(node) in bare:
                continue  # handled by analyze_builtin
            if isinstance(node, ast.Var):
                sym = self.resolve(node.name, node.span)
                if sym is None:
                    continue
                self.a.uses[id(node)] = sym
                if sym.is_array:
                    self.error(
                        f"`{sym.name}` is an array; index it or use `len({sym.name})`",
                        node.span,
                    )
            elif isinstance(node, ast.Index):
                sym = self.resolve(node.name, node.span)
                if sym is None:
                    continue
                self.a.uses[id(node)] = sym
                if not sym.is_array:
                    self.error(f"`{sym.name}` is not an array", node.span)
            elif isinstance(node, ast.Builtin):
                self.analyze_builtin(node)

    def analyze_builtin(self, node: ast.Builtin) -> None:
        if node.name in ("empty", "top", "size"):
            arg = node.args[0]
            if not isinstance(arg, ast.Var):
                self.error(f"`{node.name}` needs a stack name", node.span)
                return
            sym = self.resolve(arg.name, arg.span)
            if sym is None:
                return
            self.a.uses[id(arg)] = sym
            if sym.type != "stack":
                self.error(f"`{sym.name}` is not a stack", arg.span)
        elif node.name == "len":
            arg = node.args[0]
            if not isinstance(arg, ast.Var):
                self.error("`len` needs an array name", node.span)
                return
            sym = self.resolve(arg.name, arg.span)
            if sym is None:
                return
            self.a.uses[id(arg)] = sym
            if not sym.is_array:
                self.error(f"`{sym.name}` is not an array", arg.span)

    def analyze_lvalue(self, lv: Optional[ast.Expr], what: str) -> Optional[Symbol]:
        if lv is None:
            return None
        if isinstance(lv, ast.Var):
            sym = self.resolve(lv.name, lv.span)
            if sym is None:
                return None
            self.a.uses[id(lv)] = sym
            if sym.is_array:
                self.error(
                    f"`{sym.name}` is an array; {what} needs an element like "
                    f"`{sym.name}[i]`",
                    lv.span,
                )
            if sym.kind == "const":
                self.error(f"`{sym.name}` is a constant and cannot be written", lv.span)
            return sym
        if isinstance(lv, ast.Index):
            sym = self.resolve(lv.name, lv.span)
            if sym is None:
                return None
            self.a.uses[id(lv)] = sym
            if not sym.is_array:
                self.error(f"`{sym.name}` is not an array", lv.span)
            self.analyze_expr(lv.index)
            return sym
        self.error(f"{what} must be a variable or array element", getattr(lv, "span", None))
        return None

    def forbid_reference(self, e: Optional[ast.Expr], name: str, where: str) -> None:
        """Rule 1: the updated variable may not be read while it is updated."""
        if e is None:
            return
        metadata = ast.bare_name_ids(e, ast.METADATA_BUILTINS)
        for node in ast.subexpressions(e):
            if id(node) in metadata:
                continue
            if isinstance(node, (ast.Var, ast.Index)) and node.name == name:
                self.error(
                    f"`{name}` may not appear in {where}",
                    node.span,
                    label="reads the variable being updated",
                    notes=[
                        "a reversible update is undone by re-evaluating this",
                        "expression, so it must not depend on the target",
                        f"introduce `local int t = ...;` if you need the old value",
                    ],
                )
                return

    # -- embed ------------------------------------------------------------
    def analyze_embed(self, s: ast.Embed) -> None:
        if self.in_embed:
            self.error("`embed` blocks cannot be nested", s.span)
            return
        self.embed_counter += 1
        plan = EmbedPlan(s, f"__embed_{self.embed_counter}")
        s.proc_name = plan.proc_name
        self.a.plans[id(s)] = plan
        self.proc.embeds.append(plan)

        # output targets are ordinary reversible lvalues in the enclosing scope
        for b in s.bindings:
            sym = self.analyze_lvalue(b.target, "an embed output")
            if sym is not None and sym.type == "stack":
                self.error("embed outputs must be integer cells", b.span)
            if isinstance(b.target, ast.Index):
                self.analyze_expr(b.target.index)

        # walk the classical body in its own namespace
        self.in_embed = True
        self.cscope = Scope(None)
        self.free_syms: dict[str, Symbol] = {}
        self.free_order: list[Symbol] = []
        plan_body = ast.CBlock(s.span, list(s.body.stmts if s.body else []))
        # bindings become synthetic classical variables so the copy phase has a cell
        for i, b in enumerate(s.bindings):
            plan_body.stmts.append(
                ast.CVar(b.span, f"__out{i}", b.expr, None)
            )
        self.cvars: list[Symbol] = []
        self.counters = 0
        self.analyze_cstmt(plan_body)
        self.in_embed = False

        plan.body = plan_body
        plan.free = list(self.free_order)
        plan.counters = self.counters
        n_free = len(plan.free)
        for i, sym in enumerate(plan.free):
            sym.index = i
        temps = self.cvars
        for i, sym in enumerate(temps):
            sym.index = n_free + i
            sym.off = self.alloc(sym.length)
        plan.ctemps = temps
        plan.outputs = [sym for sym in temps if sym.name.startswith("__out")]
        # the enclosing frame holds the classical cells only for the block's extent
        for sym in temps:
            self.next_off -= sym.length

    def cresolve(self, name: str, span: Optional[Span]) -> Optional[Symbol]:
        sym = self.cscope.lookup(name)
        if sym is not None:
            return sym
        outer = self.scope.lookup(name)
        if outer is not None:
            if outer.name in self.free_syms:
                return self.free_syms[outer.name]
            free = Symbol(
                outer.name, "cparam", outer.type, outer.length, span=outer.span
            )
            free.origin = outer
            self.free_syms[outer.name] = free
            self.free_order.append(free)
            return free
        if name in self.a.globals:
            return self.a.globals[name]
        if name in self.a.consts:
            return Symbol(name, "const", "int", value=self.a.consts[name], span=span)
        hint = _closest(name, set(self.cscope.names) | set(self.a.globals) | set(self.a.consts))
        self.error(
            f"unknown name `{name}` in an embed block",
            span,
            notes=[f"did you mean `{hint}`?"] if hint else (),
        )
        return None

    def analyze_cexpr(self, e: Optional[ast.Expr]) -> None:
        if e is None:
            return
        bare = ast.bare_name_ids(e)
        for node in ast.subexpressions(e):
            if id(node) in bare:
                continue
            if isinstance(node, ast.Var):
                sym = self.cresolve(node.name, node.span)
                if sym is None:
                    continue
                self.a.uses[id(node)] = sym
                if sym.is_array:
                    self.error(f"`{sym.name}` is an array; index it", node.span)
            elif isinstance(node, ast.Index):
                sym = self.cresolve(node.name, node.span)
                if sym is None:
                    continue
                self.a.uses[id(node)] = sym
                if not sym.is_array:
                    self.error(f"`{sym.name}` is not an array", node.span)
            elif isinstance(node, ast.Builtin):
                if node.name == "len":
                    arg = node.args[0]
                    if isinstance(arg, ast.Var):
                        sym = self.cresolve(arg.name, arg.span)
                        if sym is not None:
                            self.a.uses[id(arg)] = sym
                elif node.name in ("empty", "top", "size"):
                    arg = node.args[0]
                    if isinstance(arg, ast.Var):
                        sym = self.cresolve(arg.name, arg.span)
                        if sym is not None:
                            self.a.uses[id(arg)] = sym
                            if sym.type != "stack":
                                self.error(f"`{sym.name}` is not a stack", arg.span)

    def analyze_cstmt(self, s: Optional[ast.CStmt]) -> None:
        if s is None:
            return
        if isinstance(s, ast.CBlock):
            for x in s.stmts:
                self.analyze_cstmt(x)
            return
        if isinstance(s, ast.CVar):
            if self.cscope.lookup(s.name) is not None:
                self.error(f"`{s.name}` is already declared in this embed block", s.span)
            if s.size is not None:
                n = self.const_eval(s.size, "a classical array length")
                if n <= 0:
                    self.error(f"`var {s.name}` needs a positive length", s.span)
                    n = 1
                sym = Symbol(s.name, "ctemp", "array", n, span=s.span)
            else:
                self.analyze_cexpr(s.expr)
                sym = Symbol(s.name, "ctemp", "int", 1, span=s.span)
            self.cscope.declare(sym)
            self.cvars.append(sym)
            self.a.uses[id(s)] = sym
            return
        if isinstance(s, ast.CAssign):
            target = s.target
            if target is None:
                return
            sym = self.cresolve(target.name, target.span)
            if sym is None:
                return
            self.a.uses[id(target)] = sym
            if sym.kind != "ctemp":
                self.error(
                    f"`{sym.name}` is not declared inside this embed block",
                    target.span,
                    label="assigned here",
                    notes=[
                        "classical code may read enclosing state but never write it:",
                        "the uncompute step would undo the write anyway",
                        f"declare `var {sym.name} = ...;` to work on a copy",
                    ],
                )
                return
            if isinstance(target, ast.Index):
                if not sym.is_array:
                    self.error(f"`{sym.name}` is not an array", target.span)
                self.analyze_cexpr(target.index)
            elif sym.is_array:
                self.error(f"`{sym.name}` is an array; assign to an element", target.span)
            self.analyze_cexpr(s.expr)
            return
        if isinstance(s, ast.CIf):
            self.analyze_cexpr(s.cond)
            self.analyze_cstmt(s.then)
            self.analyze_cstmt(s.otherwise)
            return
        if isinstance(s, ast.CWhile):
            self.counters += 1
            self.analyze_cexpr(s.cond)
            self.analyze_cstmt(s.body)
            return
        if isinstance(s, ast.CFor):
            self.counters += 1
            self.analyze_cstmt(s.init)
            self.analyze_cexpr(s.cond)
            self.analyze_cstmt(s.step)
            self.analyze_cstmt(s.body)
            return
        self.error(f"unsupported classical statement {type(s).__name__}", s.span)


def _closest(name: str, candidates: Iterable[str]) -> Optional[str]:
    """A tiny edit-distance suggester for unknown identifiers."""
    best, best_d = None, 3
    for c in candidates:
        d = _edit(name, c)
        if d < best_d:
            best, best_d = c, d
    return best


def _edit(a: str, b: str) -> int:
    if abs(len(a) - len(b)) > 3:
        return 99
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def analyze(module: ast.Module, require_main: bool = True) -> Analysis:
    return Analyzer(module, require_main).run()
