"""Source-level program inversion.

``rev invert prog.rev`` reads a Reverie program and writes the program that
undoes it.  Not a decompiler, not a trace replayer -- a syntactic transformation
whose correctness follows from the language being reversible in the first place.

The whole thing is a structural recursion:

* a sequence inverts by inverting each statement and reversing the order
* ``+=`` and ``-=`` swap; ``^=``, ``<=>``, ``neg``, ``not`` and ``assert`` are
  their own inverses; ``*=`` and ``/=`` swap
* ``local`` and ``delocal`` swap; ``push`` and ``pop`` swap; ``call`` and
  ``uncall`` swap; ``print`` and ``unprint`` swap
* a conditional swaps its entry test with its exit predicate
* a loop swaps its entry assertion with its exit test
* ``undo { S }`` inverts to plain ``S``
* an ``embed`` block is very nearly its own inverse -- Bennett's
  compute/copy/uncompute sandwich is symmetric, so only the sign of a ``+=``
  binding has to flip

The last one is worth dwelling on.  ``embed`` lets you write ordinary
destructive code; the compiler makes it reversible *and garbage-free*.  Because
the resulting shape is ``compute; copy; uncompute``, reversing it gives
``compute; uncopy; uncompute`` -- the same block, running the same classical
code forwards, with only the copy step reversed.
"""

from __future__ import annotations

import copy
from typing import Optional, Sequence

from . import ast
from .diagnostics import CompileError

#: reversible update operators and their inverses
OP_INVERSE = {"+": "-", "-": "+", "^": "^", "*": "/", "/": "*", "<<": ">>", ">>": "<<"}

BINDING_INVERSE = {"^": "^", "+": "-", "-": "+"}


def invert_stmt(s: ast.Stmt) -> ast.Stmt:
    """Return a statement that exactly undoes *s*."""
    if isinstance(s, ast.Skip):
        return ast.Skip(s.span)
    if isinstance(s, ast.Block):
        return ast.Block(s.span, [invert_stmt(x) for x in reversed(s.stmts)])
    if isinstance(s, ast.Update):
        return ast.Update(s.span, OP_INVERSE[s.op], s.target, s.expr)
    if isinstance(s, ast.UnaryStmt):
        return ast.UnaryStmt(s.span, s.op, s.target)
    if isinstance(s, ast.Swap):
        return ast.Swap(s.span, s.left, s.right)
    if isinstance(s, ast.Assert):
        return ast.Assert(s.span, s.expr)
    if isinstance(s, ast.If):
        return ast.If(
            s.span,
            s.exit,
            invert_stmt(s.then),
            invert_stmt(s.otherwise),
            s.entry,
        )
    if isinstance(s, ast.Loop):
        return ast.Loop(
            s.span,
            s.exit,
            invert_stmt(s.body),
            invert_stmt(s.step),
            s.entry,
        )
    if isinstance(s, ast.LocalDecl):
        return ast.LocalDecl(s.span, s.name, s.type, s.expr, not s.release)
    if isinstance(s, ast.Call):
        return ast.Call(s.span, s.name, s.args, not s.uncall)
    if isinstance(s, ast.StackOp):
        return ast.StackOp(s.span, s.var, s.stack, not s.pop)
    if isinstance(s, ast.Print):
        return ast.Print(s.span, s.parts, not s.reverse, s.newline)
    if isinstance(s, ast.Undo):
        return s.body if s.body is not None else ast.Skip(s.span)
    if isinstance(s, ast.Embed):
        bindings = [
            ast.Binding(b.target, BINDING_INVERSE[b.op], b.expr, b.span)
            for b in reversed(s.bindings)
        ]
        return ast.Embed(s.span, bindings, s.body, s.proc_name)
    raise CompileError(
        f"cannot invert {type(s).__name__}", getattr(s, "span", None)
    )  # pragma: no cover


def invert_block(b: Optional[ast.Stmt]) -> ast.Block:
    if b is None:
        return ast.Block(None, [])
    inv = invert_stmt(b)
    if isinstance(inv, ast.Block):
        return inv
    return ast.Block(getattr(b, "span", None), [inv])


def invert_proc(p: ast.ProcDecl, rename: Optional[str] = None) -> ast.ProcDecl:
    doc = p.doc
    note = f"The inverse of `{p.name}`."
    doc = f"{doc}\n{note}" if doc else note
    return ast.ProcDecl(
        p.span,
        rename or p.name,
        list(p.params),
        invert_block(p.body),
        doc,
    )


def invert_module(
    module: ast.Module,
    targets: Sequence[str] = ("main",),
    *,
    keep_original: bool = False,
    suffix: str = "_inv",
) -> ast.Module:
    """Invert selected procedures.

    Only the named procedures are transformed.  Procedures they *call* are left
    alone on purpose: the inverse of ``call p`` is ``uncall p``, which runs the
    original ``p`` backwards.  Inverting ``p`` as well would undo the undoing.
    """
    names = {p.name for p in module.procs()}
    for t in targets:
        if t not in names:
            raise CompileError(
                f"cannot invert unknown procedure `{t}`",
                notes=["known procedures: " + ", ".join(sorted(names))],
            )
    out: list[ast.Decl] = []
    for d in module.decls:
        if isinstance(d, ast.ProcDecl) and d.name in targets:
            if keep_original:
                out.append(d)
                out.append(invert_proc(d, d.name + suffix))
            else:
                out.append(invert_proc(d))
        else:
            out.append(d)
    return ast.Module(out, module.source_name)


def invert_source(text: str, name: str = "<input>", targets: Sequence[str] = ("main",)) -> str:
    from .parser import parse_text
    from .printer import print_module

    return print_module(invert_module(parse_text(text, name), targets))
