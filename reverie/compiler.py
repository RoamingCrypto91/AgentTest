"""AST -> RIR -> flat bytecode.

The compiler is deliberately thin.  All the semantic weight lives in
:mod:`reverie.checker` (which decides whether a program is reversible) and
:mod:`reverie.rir` (which knows how to invert and lower one).  What is left here
is address arithmetic and two genuinely interesting translations:

* ``undo { S }`` compiles ``S`` and then calls ``.invert()`` on the result.
  Program inversion is not a special case in the back end; it is an ordinary
  operation on the IR that the language exposes as a statement.

* ``embed`` compiles Bennett's construction:

      local temps...                 # scratch, provably zero on both sides
      call   __embed_k(inputs, temps)   # compute -- writes a history tape
      out ^= temps[result]              # copy the answer out
      uncall __embed_k(inputs, temps)   # uncompute -- drains the tape to empty
      delocal temps...

  The middle line is the only thing that survives.  Everything the classical
  code scribbled -- intermediate values, loop counters, branch decisions -- is
  put back exactly where it was found, so an ``embed`` block is a pure function
  applied reversibly, with zero bits erased.
"""

from __future__ import annotations

from typing import Optional, Sequence

from . import ast, rir
from .checker import Analysis, EmbedPlan, Symbol, analyze
from .diagnostics import CompileError, Span
from .ir import (
    AbsA,
    Addr,
    ArrayLen,
    Bin,
    Const,
    Expr,
    IndexA,
    Load,
    LocalA,
    ParamA,
    StackQuery,
    Un,
)
from .isa import Call as CallInstr
from .isa import Halt, ProcEntry, ProcExit
from .rir import CodeBuilder
from .vm import GlobalInfo, ProcInfo, Program


class Compiler:
    def __init__(self, module: ast.Module, analysis: Analysis) -> None:
        self.module = module
        self.a = analysis
        #: how ``ctemp`` symbols should be addressed right now
        self.ctemp_as_param = False
        self.counter_offsets: dict[int, int] = {}
        self.next_counter = 0

    # -- addresses --------------------------------------------------------
    def symbol(self, node) -> Symbol:
        sym = self.a.uses.get(id(node))
        if sym is None:  # pragma: no cover - checker guarantees this
            raise CompileError(
                f"unresolved name `{getattr(node, 'name', node)}`",
                getattr(node, "span", None),
            )
        return sym

    def base_addr(self, sym: Symbol) -> Addr:
        if sym.kind == "global":
            return AbsA(sym.addr, sym.name, max(sym.length, 1))
        if sym.kind == "local":
            return LocalA(sym.off, sym.name, max(sym.length, 1))
        if sym.kind in ("param", "cparam"):
            return ParamA(sym.index, sym.name)
        if sym.kind == "ctemp":
            if self.ctemp_as_param:
                return ParamA(sym.index, sym.name)
            return LocalA(sym.off, sym.name, max(sym.length, 1))
        raise CompileError(  # pragma: no cover
            f"`{sym.name}` has no storage ({sym.kind})", sym.span
        )

    def addr_of(self, node: ast.Expr) -> Addr:
        sym = self.symbol(node)
        base = self.base_addr(sym)
        if isinstance(node, ast.Index):
            # Array parameters are bounds-checked against the length the caller
            # actually passed, never against the declaration, so a procedure
            # cannot run off the end of a shorter array than it expected.
            length = -1 if sym.kind in ("param", "cparam") else sym.length
            return IndexA(base, self.expr(node.index), length, sym.name)
        return base

    def arg_addr(self, node: ast.Expr) -> Addr:
        """A call argument: the address of the referent, not its value."""
        return self.addr_of(node)

    # -- expressions ------------------------------------------------------
    def expr(self, e: Optional[ast.Expr]) -> Expr:
        if e is None:
            return Const(0)
        if isinstance(e, ast.Num):
            return Const(e.value)
        if isinstance(e, ast.Var):
            sym = self.symbol(e)
            if sym.kind == "const":
                return Const(sym.value)
            return Load(self.base_addr(sym))
        if isinstance(e, ast.Index):
            return Load(self.addr_of(e))
        if isinstance(e, ast.BinOp):
            return Bin(e.op, self.expr(e.left), self.expr(e.right))
        if isinstance(e, ast.UnOp):
            return Un(e.op, self.expr(e.operand))
        if isinstance(e, ast.Builtin):
            return self.builtin(e)
        raise CompileError(  # pragma: no cover
            f"cannot compile expression {type(e).__name__}", getattr(e, "span", None)
        )

    def builtin(self, e: ast.Builtin) -> Expr:
        if e.name in ("min", "max"):
            return Bin(e.name, self.expr(e.args[0]), self.expr(e.args[1]))
        if e.name in ("abs", "sign"):
            return Un(e.name, self.expr(e.args[0]))
        if e.name in ("empty", "top", "size"):
            sym = self.symbol(e.args[0])
            return StackQuery(e.name, self.base_addr(sym))
        if e.name == "len":
            sym = self.symbol(e.args[0])
            if sym.kind in ("param", "cparam"):
                return ArrayLen(self.base_addr(sym))
            return Const(sym.length)
        raise CompileError(f"unknown builtin `{e.name}`", e.span)  # pragma: no cover

    # -- statements -------------------------------------------------------
    def stmt(self, s: ast.Stmt) -> rir.RStmt:
        if isinstance(s, ast.Skip):
            return rir.RSkip(s.span)
        if isinstance(s, ast.Block):
            return rir.RSeq([self.stmt(x) for x in s.stmts], s.span)
        if isinstance(s, ast.Update):
            return rir.RUpdate(s.op, self.addr_of(s.target), self.expr(s.expr), s.span)
        if isinstance(s, ast.UnaryStmt):
            return rir.RUnary(s.op, self.addr_of(s.target), s.span)
        if isinstance(s, ast.Swap):
            return rir.RSwap(self.addr_of(s.left), self.addr_of(s.right), s.span)
        if isinstance(s, ast.If):
            entry = self.expr(s.entry)
            exit_ = entry if s.exit is s.entry else self.expr(s.exit)
            return rir.RIf(
                entry, self.stmt(s.then), self.stmt(s.otherwise), exit_, s.span
            )
        if isinstance(s, ast.Loop):
            return rir.RLoop(
                self.expr(s.entry),
                self.stmt(s.body),
                self.stmt(s.step),
                self.expr(s.exit),
                s.span,
            )
        if isinstance(s, ast.LocalDecl):
            sym = self.a.uses[id(s)]
            cls = rir.RDelocal if s.release else rir.RLocal
            expr = None if sym.is_array else self.expr(s.expr)
            return cls(sym.off, expr, sym.length, sym.name, s.span)
        if isinstance(s, ast.Call):
            return self.call(s)
        if isinstance(s, ast.StackOp):
            cls = rir.RPop if s.pop else rir.RPush
            return cls(self.addr_of(s.var), self.addr_of(s.stack), s.span)
        if isinstance(s, ast.Print):
            parts = [p if isinstance(p, str) else self.expr(p) for p in s.parts]
            cls = rir.RUnemit if s.reverse else rir.REmit
            return cls(parts, s.span, s.newline)
        if isinstance(s, ast.Assert):
            return rir.RAssert(self.expr(s.expr), "", s.span)
        if isinstance(s, ast.Undo):
            return self.stmt(s.body).invert()
        if isinstance(s, ast.Embed):
            return self.embed(s)
        raise CompileError(  # pragma: no cover
            f"cannot compile statement {type(s).__name__}", getattr(s, "span", None)
        )

    def call(self, s: ast.Call) -> rir.RStmt:
        """A call, wrapping any constant arguments in hidden locals.

        Reverie has no by-value parameters, so a constant argument becomes a
        cell the caller owns for exactly the duration of the call.  The
        checker has already established that the callee never writes through
        it, which is what makes the surrounding ``local``/``delocal`` pair
        provable.
        """
        consts = self.a.const_args.get(id(s), [])
        by_pos = {c.index: c for c in consts}
        args: list[Addr] = []
        for i, arg in enumerate(s.args):
            c = by_pos.get(i)
            if c is not None:
                args.append(LocalA(c.off, c.name))
            else:
                args.append(self.arg_addr(arg))
        core = rir.RCall(s.name, args, s.uncall, s.span)
        if not consts:
            return core
        out: list[rir.RStmt] = [
            rir.RLocal(c.off, Const(c.value), 1, c.name, s.span) for c in consts
        ]
        out.append(core)
        out.extend(
            rir.RDelocal(c.off, Const(c.value), 1, c.name, s.span)
            for c in reversed(consts)
        )
        return rir.RSeq(out, s.span)

    # -- Bennett's construction ------------------------------------------
    def embed(self, s: ast.Embed) -> rir.RStmt:
        plan = self.a.plans[id(s)]
        out: list[rir.RStmt] = []
        self.ctemp_as_param = False
        for sym in plan.ctemps:
            init = None if sym.is_array else Const(0)
            out.append(rir.RLocal(sym.off, init, sym.length, sym.name, s.span))
        args: list[Addr] = []
        for free in plan.free:
            args.append(self.base_addr(free.origin))
        for sym in plan.ctemps:
            args.append(LocalA(sym.off, sym.name))
        out.append(rir.RCall(plan.proc_name, args, False, s.span))
        for binding, cell in zip(s.bindings, plan.outputs):
            out.append(
                rir.RUpdate(
                    binding.op,
                    self.addr_of(binding.target),
                    Load(LocalA(cell.off, cell.name)),
                    binding.span,
                )
            )
        out.append(rir.RCall(plan.proc_name, args, True, s.span))
        for sym in reversed(plan.ctemps):
            init = None if sym.is_array else Const(0)
            out.append(rir.RDelocal(sym.off, init, sym.length, sym.name, s.span))
        return rir.RSeq(out, s.span)

    # -- classical statements (inside a generated embed procedure) --------
    def cstmt(self, s: Optional[ast.CStmt]) -> rir.RStmt:
        if s is None:
            return rir.RSkip()
        if isinstance(s, ast.CBlock):
            return rir.RSeq([self.cstmt(x) for x in s.stmts], s.span)
        if isinstance(s, ast.CVar):
            sym = self.a.uses[id(s)]
            if sym.is_array:
                return rir.RSeq([], s.span)  # frame cells are already zero
            return rir.CSetStmt(
                ParamA(sym.index, sym.name), self.expr(s.expr), s.span
            )
        if isinstance(s, ast.CAssign):
            return rir.CSetStmt(self.addr_of(s.target), self.expr(s.expr), s.span)
        if isinstance(s, ast.CIf):
            return rir.CIfStmt(
                self.expr(s.cond),
                self.cstmt(s.then),
                self.cstmt(s.otherwise),
                s.span,
            )
        if isinstance(s, ast.CWhile):
            counter = self.alloc_counter()
            return rir.CWhileStmt(self.expr(s.cond), self.cstmt(s.body), counter, s.span)
        if isinstance(s, ast.CFor):
            init = self.cstmt(s.init)
            counter = self.alloc_counter()
            body = rir.RSeq([self.cstmt(s.body), self.cstmt(s.step)], s.span)
            return rir.RSeq(
                [init, rir.CWhileStmt(self.expr(s.cond), body, counter, s.span)], s.span
            )
        raise CompileError(  # pragma: no cover
            f"cannot compile classical statement {type(s).__name__}",
            getattr(s, "span", None),
        )

    def alloc_counter(self) -> int:
        off = self.next_counter
        self.next_counter += 1
        return off

    # -- whole program ----------------------------------------------------
    def compile(self, entry: str = "main") -> Program:
        b = CodeBuilder()
        b.emit(CallInstr(entry, []))
        b.emit(Halt())
        prog = Program(code=b.code, n_globals=self.a.n_globals, entry=entry)
        prog.source_name = self.module.source_name
        prog.n_stacks = self.a.n_stacks
        for name, g in self.a.globals.items():
            prog.globals[name] = GlobalInfo(
                name, g.addr, g.length, g.type, g.stack_id
            )

        for decl in self.module.procs():
            info = self.a.procs[decl.name]
            self.ctemp_as_param = False
            body = self.stmt(decl.body)
            entry_at = b.emit(ProcEntry(decl.name))
            body.lower(b)
            exit_at = b.emit(ProcExit(decl.name))
            prog.procs[decl.name] = ProcInfo(
                decl.name,
                entry_at,
                exit_at,
                info.frame_size,
                tuple(p.name for p in info.params),
                tuple(p.type for p in info.params),
                decl.doc,
            )
            for plan in info.embeds:
                self.emit_embed_proc(b, prog, plan)
        return prog.finalize()

    def emit_embed_proc(self, b: CodeBuilder, prog: Program, plan: EmbedPlan) -> None:
        self.ctemp_as_param = True
        self.next_counter = 0
        body = self.cstmt(plan.body)
        frame_size = self.next_counter
        entry_at = b.emit(ProcEntry(plan.proc_name))
        body.lower(b)
        exit_at = b.emit(ProcExit(plan.proc_name))
        params = plan.params
        prog.procs[plan.proc_name] = ProcInfo(
            plan.proc_name,
            entry_at,
            exit_at,
            frame_size,
            tuple(p.name for p in params),
            tuple(p.type for p in params),
            "compute phase of an embed block",
        )
        self.ctemp_as_param = False


def compile_module(module: ast.Module, entry: str = "main") -> Program:
    a = analyze(module)
    a.diagnostics.raise_if_errors()
    return Compiler(module, a).compile(entry)


def compile_text(text: str, name: str = "<input>", entry: str = "main") -> Program:
    from .parser import parse_text

    return compile_module(parse_text(text, name), entry)
