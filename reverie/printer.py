"""Pretty-printer for Reverie ASTs.

Doubles as the formatter (``rev fmt``) and as the back end of the source-level
inverter (``rev invert``): inverting a program produces an AST, and this module
turns it back into readable source rather than a debug dump.
"""

from __future__ import annotations

from typing import Optional, Union

from . import ast

INDENT = "    "

#: binding power for expression printing; higher binds tighter
_PREC = {
    "||": 1,
    "&&": 2,
    "|": 3,
    "^": 4,
    "&": 5,
    "==": 6,
    "!=": 6,
    "<": 7,
    "<=": 7,
    ">": 7,
    ">=": 7,
    "<<": 8,
    ">>": 8,
    "+": 9,
    "-": 9,
    "*": 10,
    "/": 10,
    "%": 10,
    "**": 12,
}
_UNARY_PREC = 11
_ATOM_PREC = 20


def expr_prec(e: ast.Expr) -> int:
    if isinstance(e, ast.BinOp):
        return _PREC.get(e.op, 0)
    if isinstance(e, ast.UnOp):
        return _UNARY_PREC
    return _ATOM_PREC


def print_expr(e: Optional[ast.Expr], parent: int = 0) -> str:
    if e is None:
        return "?"
    if isinstance(e, ast.Num):
        return str(e.value)
    if isinstance(e, ast.Var):
        return e.name
    if isinstance(e, ast.Index):
        return f"{e.name}[{print_expr(e.index)}]"
    if isinstance(e, ast.Builtin):
        return f"{e.name}({', '.join(print_expr(a) for a in e.args)})"
    if isinstance(e, ast.UnOp):
        inner = print_expr(e.operand, _UNARY_PREC)
        text = f"{e.op}{inner}"
        return f"({text})" if parent > _UNARY_PREC else text
    if isinstance(e, ast.BinOp):
        prec = _PREC.get(e.op, 0)
        # `**` is right associative, everything else left associative
        lbias = prec + (1 if e.op == "**" else 0)
        rbias = prec + (0 if e.op == "**" else 1)
        text = f"{print_expr(e.left, lbias)} {e.op} {print_expr(e.right, rbias)}"
        return f"({text})" if parent > prec else text
    raise TypeError(f"cannot print expression {e!r}")  # pragma: no cover


def print_type(t: Optional[ast.Type], name: str) -> str:
    if isinstance(t, ast.TStack):
        return f"stack {name}"
    if isinstance(t, ast.TArray):
        size = print_expr(t.size) if t.size is not None else ""
        return f"int {name}[{size}]"
    return f"int {name}"


def print_stmt(s: ast.Stmt, depth: int = 0) -> str:
    pad = INDENT * depth
    if isinstance(s, ast.Skip):
        return f"{pad}skip;"
    if isinstance(s, ast.Block):
        inner = "\n".join(print_stmt(x, depth + 1) for x in s.stmts)
        if not s.stmts:
            return f"{pad}{{ }}"
        return f"{pad}{{\n{inner}\n{pad}}}"
    if isinstance(s, ast.Update):
        return f"{pad}{print_expr(s.target)} {s.op}= {print_expr(s.expr)};"
    if isinstance(s, ast.UnaryStmt):
        return f"{pad}{s.op} {print_expr(s.target)};"
    if isinstance(s, ast.Swap):
        return f"{pad}{print_expr(s.left)} <=> {print_expr(s.right)};"
    if isinstance(s, ast.If):
        out = [f"{pad}if {print_expr(s.entry)} {{"]
        out.append(_body(s.then, depth))
        if not isinstance(s.otherwise, ast.Skip):
            out.append(f"{pad}}} else {{")
            out.append(_body(s.otherwise, depth))
        out.append(f"{pad}}} fi {print_expr(s.exit)};")
        return "\n".join(x for x in out if x != "")
    if isinstance(s, ast.Loop):
        out = [f"{pad}from {print_expr(s.entry)}"]
        if not isinstance(s.body, ast.Skip):
            out[-1] += " do {"
            out.append(_body(s.body, depth))
            out.append(f"{pad}}}")
        if not isinstance(s.step, ast.Skip):
            out[-1] += " loop {" if out[-1].endswith("}") else " loop {"
            out.append(_body(s.step, depth))
            out.append(f"{pad}}}")
        out[-1] += f" until {print_expr(s.exit)};"
        return "\n".join(x for x in out if x != "")
    if isinstance(s, ast.LocalDecl):
        kw = "delocal" if s.release else "local"
        decl = print_type(s.type, s.name)
        if s.expr is not None:
            return f"{pad}{kw} {decl} = {print_expr(s.expr)};"
        return f"{pad}{kw} {decl};"
    if isinstance(s, ast.Call):
        kw = "uncall" if s.uncall else "call"
        args = ", ".join(print_expr(a) for a in s.args)
        return f"{pad}{kw} {s.name}({args});"
    if isinstance(s, ast.StackOp):
        kw = "pop" if s.pop else "push"
        return f"{pad}{kw}({print_expr(s.var)}, {print_expr(s.stack)});"
    if isinstance(s, ast.Print):
        parts = [
            _quote(p) if isinstance(p, str) else print_expr(p) for p in s.parts
        ]
        kw = "unprint" if s.reverse else "print"
        return f"{pad}{kw} {', '.join(parts)};"
    if isinstance(s, ast.Assert):
        return f"{pad}assert {print_expr(s.expr)};"
    if isinstance(s, ast.Undo):
        return f"{pad}undo {{\n{_body(s.body, depth)}\n{pad}}}"
    if isinstance(s, ast.Embed):
        binds = ", ".join(
            f"{print_expr(b.target)} {b.op}= {print_expr(b.expr)}" for b in s.bindings
        )
        body = print_cstmt(s.body, depth + 1)
        return f"{pad}embed ({binds}) {{\n{body}\n{pad}}}"
    raise TypeError(f"cannot print statement {s!r}")  # pragma: no cover


def _body(s: Optional[ast.Stmt], depth: int) -> str:
    if s is None:
        return ""
    if isinstance(s, ast.Block):
        if not s.stmts:
            return INDENT * (depth + 1) + "skip;"
        return "\n".join(print_stmt(x, depth + 1) for x in s.stmts)
    return print_stmt(s, depth + 1)


def _quote(text: str) -> str:
    out = ['"']
    for ch in text:
        if ch == "\n":
            out.append("\\n")
        elif ch == "\t":
            out.append("\\t")
        elif ch == '"':
            out.append('\\"')
        elif ch == "\\":
            out.append("\\\\")
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)


def print_cstmt(s: Optional[ast.CStmt], depth: int = 0) -> str:
    pad = INDENT * depth
    if s is None:
        return ""
    if isinstance(s, ast.CBlock):
        return "\n".join(print_cstmt(x, depth) for x in s.stmts)
    if isinstance(s, ast.CVar):
        if s.size is not None:
            return f"{pad}var {s.name}[{print_expr(s.size)}];"
        return f"{pad}var {s.name} = {print_expr(s.expr)};"
    if isinstance(s, ast.CAssign):
        return f"{pad}{print_expr(s.target)} = {print_expr(s.expr)};"
    if isinstance(s, ast.CIf):
        out = [f"{pad}if ({print_expr(s.cond)}) {{"]
        out.append(print_cstmt(s.then, depth + 1))
        if s.otherwise is not None:
            out.append(f"{pad}}} else {{")
            out.append(print_cstmt(s.otherwise, depth + 1))
        out.append(f"{pad}}}")
        return "\n".join(x for x in out if x != "")
    if isinstance(s, ast.CWhile):
        return (
            f"{pad}while ({print_expr(s.cond)}) {{\n"
            f"{print_cstmt(s.body, depth + 1)}\n{pad}}}"
        )
    if isinstance(s, ast.CFor):
        init = print_cstmt(s.init, 0).strip().rstrip(";")
        step = print_cstmt(s.step, 0).strip().rstrip(";")
        return (
            f"{pad}for ({init}; {print_expr(s.cond)}; {step}) {{\n"
            f"{print_cstmt(s.body, depth + 1)}\n{pad}}}"
        )
    raise TypeError(f"cannot print classical statement {s!r}")  # pragma: no cover


def print_decl(d: ast.Decl) -> str:
    if isinstance(d, ast.Import):
        return f'import {_quote(d.path)};'
    if isinstance(d, ast.ConstDecl):
        return f"const {d.name} = {print_expr(d.expr)};"
    if isinstance(d, ast.GlobalDecl):
        return print_type(d.type, d.name) + ";"
    if isinstance(d, ast.ProcDecl):
        out = []
        for line in (d.doc or "").splitlines():
            out.append(f"/// {line}" if line else "///")
        params = ", ".join(print_type(p.type, p.name) for p in d.params)
        out.append(f"proc {d.name}({params}) {{")
        for s in (d.body.stmts if d.body else []):
            out.append(print_stmt(s, 1))
        if not (d.body and d.body.stmts):
            out.append(INDENT + "skip;")
        out.append("}")
        return "\n".join(out)
    raise TypeError(f"cannot print declaration {d!r}")  # pragma: no cover


def print_module(m: ast.Module) -> str:
    chunks: list[str] = []
    prev_kind: Optional[type] = None
    for d in m.decls:
        text = print_decl(d)
        if prev_kind is not None and (
            prev_kind is not type(d) or isinstance(d, ast.ProcDecl)
        ):
            chunks.append("")
        chunks.append(text)
        prev_kind = type(d)
    return "\n".join(chunks).rstrip() + "\n"
