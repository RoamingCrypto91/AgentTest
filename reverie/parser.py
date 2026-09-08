"""Recursive-descent parser for Reverie.

The grammar is small enough to read in one sitting.  Two things are worth
noticing because they are unusual:

* ``if C { ... } else { ... } fi D;`` -- a conditional carries a second
  predicate.  ``D`` must be true when the then-branch finishes and false when
  the else-branch does, which is exactly the information a reader of the
  *reverse* execution needs in order to know which way control went.  Writing
  ``fi;`` with no predicate reuses ``C``, which the checker allows only when
  neither branch can disturb it.

* ``from C do { ... } loop { ... } until D;`` -- a loop carries an entry
  assertion as well as an exit test, for the same reason.
"""

from __future__ import annotations

from typing import Callable, Optional, Sequence

from . import ast
from .diagnostics import ParseError, Source, Span
from .lexer import (
    TOK_DOC,
    TOK_EOF,
    TOK_IDENT,
    TOK_INT,
    TOK_KEYWORD,
    TOK_OP,
    TOK_STRING,
    Token,
    tokenize,
)

#: binary operator precedence, lowest binds loosest
PRECEDENCE = [
    ("||",),
    ("&&",),
    ("|",),
    ("^",),
    ("&",),
    ("==", "!="),
    ("<", "<=", ">", ">="),
    ("<<", ">>"),
    ("+", "-"),
    ("*", "/", "%"),
]

RIGHT_ASSOC = {"**"}

UPDATE_OPS = {
    "+=": "+",
    "-=": "-",
    "^=": "^",
    "*=": "*",
    "/=": "/",
    "<<=": "<<",
    ">>=": ">>",
}

BUILTINS = {
    "min": 2,
    "max": 2,
    "abs": 1,
    "sign": 1,
    "empty": 1,
    "top": 1,
    "size": 1,
    "len": 1,
}


class Parser:
    def __init__(self, source: Source) -> None:
        self.source = source
        self.tokens = tokenize(source)
        self.pos = 0

    # -- token plumbing ---------------------------------------------------
    @property
    def tok(self) -> Token:
        return self.tokens[self.pos]

    def peek(self, ahead: int = 1) -> Token:
        i = min(self.pos + ahead, len(self.tokens) - 1)
        return self.tokens[i]

    def advance(self) -> Token:
        t = self.tokens[self.pos]
        if t.kind != TOK_EOF:
            self.pos += 1
        return t

    def at_op(self, *ops: str) -> bool:
        return self.tok.is_op(*ops)

    def at_kw(self, *kws: str) -> bool:
        return self.tok.is_kw(*kws)

    def accept_op(self, *ops: str) -> Optional[Token]:
        if self.tok.is_op(*ops):
            return self.advance()
        return None

    def accept_kw(self, *kws: str) -> Optional[Token]:
        if self.tok.is_kw(*kws):
            return self.advance()
        return None

    def expect_op(self, op: str, why: str = "") -> Token:
        if not self.tok.is_op(op):
            self.fail(f"expected `{op}`" + (f" {why}" if why else ""))
        return self.advance()

    def expect_kw(self, kw: str, why: str = "") -> Token:
        if not self.tok.is_kw(kw):
            self.fail(f"expected `{kw}`" + (f" {why}" if why else ""))
        return self.advance()

    def expect_ident(self, why: str = "") -> Token:
        if self.tok.kind != TOK_IDENT:
            self.fail(f"expected an identifier" + (f" {why}" if why else ""))
        return self.advance()

    def fail(self, message: str, notes: Sequence[str] = ()) -> None:
        found = self.tok
        desc = "end of file" if found.kind == TOK_EOF else f"`{found.text}`"
        raise ParseError(
            f"{message}, found {desc}", found.span, label="here", notes=notes
        )

    def span_from(self, start: Token) -> Span:
        end = self.tokens[max(0, self.pos - 1)]
        return Span(self.source, start.span.start, end.span.end)

    # -- module -----------------------------------------------------------
    def parse_module(self) -> ast.Module:
        decls: list[ast.Decl] = []
        while self.tok.kind != TOK_EOF:
            decls.append(self.parse_decl())
        return ast.Module(decls=decls, source_name=self.source.name)

    def collect_docs(self) -> str:
        lines = []
        while self.tok.kind == TOK_DOC:
            lines.append(self.advance().text)
        return "\n".join(lines)

    def parse_decl(self) -> ast.Decl:
        doc = self.collect_docs()
        start = self.tok
        if self.at_kw("import"):
            self.advance()
            if self.tok.kind != TOK_STRING:
                self.fail("expected a quoted module path after `import`")
            path = self.advance().value
            self.expect_op(";", "after an import")
            return ast.Import(self.span_from(start), path)  # type: ignore[arg-type]
        if self.at_kw("const"):
            self.advance()
            name = self.expect_ident("after `const`").text
            self.expect_op("=", "in a const declaration")
            expr = self.parse_expr()
            self.expect_op(";", "after a const declaration")
            return ast.ConstDecl(self.span_from(start), name, expr)
        if self.at_kw("proc"):
            self.advance()
            name = self.expect_ident("after `proc`").text
            self.expect_op("(", "after a procedure name")
            params = self.parse_params()
            self.expect_op(")", "to close the parameter list")
            body = self.parse_block()
            return ast.ProcDecl(self.span_from(start), name, params, body, doc)
        if self.at_kw("int", "stack"):
            ty, name = self.parse_typed_name(allow_size=True)
            self.expect_op(";", "after a global declaration")
            return ast.GlobalDecl(self.span_from(start), name, ty)
        self.fail(
            "expected a declaration",
            notes=["declarations are `const`, `int`, `stack`, `proc` or `import`"],
        )
        raise AssertionError  # pragma: no cover

    def parse_typed_name(self, allow_size: bool = True) -> tuple[ast.Type, str]:
        start = self.tok
        if self.accept_kw("stack"):
            name = self.expect_ident("after `stack`").text
            return ast.TStack(self.span_from(start)), name
        self.expect_kw("int", "to start a declaration")
        name = self.expect_ident("after `int`").text
        if allow_size and self.at_op("["):
            self.advance()
            size = None
            if not self.at_op("]"):
                size = self.parse_expr()
            self.expect_op("]", "to close an array size")
            return ast.TArray(self.span_from(start), size), name
        return ast.TInt(self.span_from(start)), name

    def parse_params(self) -> list[ast.Param]:
        params: list[ast.Param] = []
        if self.at_op(")"):
            return params
        while True:
            start = self.tok
            ty, name = self.parse_typed_name(allow_size=True)
            params.append(ast.Param(name, ty, self.span_from(start)))
            if not self.accept_op(","):
                break
        return params

    # -- statements -------------------------------------------------------
    def parse_block(self) -> ast.Block:
        start = self.expect_op("{", "to open a block")
        stmts: list[ast.Stmt] = []
        while not self.at_op("}"):
            if self.tok.kind == TOK_EOF:
                raise ParseError(
                    "unterminated block", start.span, label="opened here"
                )
            stmts.append(self.parse_stmt())
        self.advance()
        return ast.Block(self.span_from(start), stmts)

    def parse_stmt(self) -> ast.Stmt:
        while self.tok.kind == TOK_DOC:
            self.advance()
        start = self.tok
        if self.at_op("{"):
            return self.parse_block()
        if self.at_op(";"):
            self.advance()
            return ast.Skip(self.span_from(start))
        if self.at_kw("skip"):
            self.advance()
            self.expect_op(";", "after `skip`")
            return ast.Skip(self.span_from(start))
        if self.at_kw("local", "delocal"):
            release = self.advance().text == "delocal"
            ty, name = self.parse_typed_name(allow_size=True)
            expr = None
            if self.accept_op("="):
                expr = self.parse_expr()
            self.expect_op(";", "after a local declaration")
            return ast.LocalDecl(self.span_from(start), name, ty, expr, release)
        if self.at_kw("call", "uncall"):
            uncall = self.advance().text == "uncall"
            name = self.expect_ident("after `call`").text
            self.expect_op("(", "after a procedure name")
            args = self.parse_args()
            self.expect_op(")", "to close an argument list")
            self.expect_op(";", "after a call")
            return ast.Call(self.span_from(start), name, args, uncall)
        if self.at_kw("if"):
            return self.parse_if()
        if self.at_kw("from"):
            return self.parse_loop()
        if self.at_kw("push", "pop"):
            is_pop = self.advance().text == "pop"
            self.expect_op("(", "after push/pop")
            var = self.parse_lvalue("as the first argument to push/pop")
            self.expect_op(",", "between push/pop arguments")
            stack = self.parse_expr()
            self.expect_op(")", "to close push/pop")
            self.expect_op(";", "after push/pop")
            return ast.StackOp(self.span_from(start), var, stack, is_pop)
        if self.at_kw("print", "unprint"):
            reverse = self.advance().text == "unprint"
            parts: list[object] = []
            if not self.at_op(";"):
                while True:
                    if self.tok.kind == TOK_STRING:
                        parts.append(self.advance().value)
                    else:
                        parts.append(self.parse_expr())
                    if not self.accept_op(","):
                        break
            self.expect_op(";", "after `print`")
            return ast.Print(self.span_from(start), parts, reverse)
        if self.at_kw("assert"):
            self.advance()
            expr = self.parse_expr()
            self.expect_op(";", "after `assert`")
            return ast.Assert(self.span_from(start), expr)
        if self.at_kw("undo"):
            self.advance()
            body = self.parse_block()
            self.accept_op(";")
            return ast.Undo(self.span_from(start), body)
        if self.at_kw("embed"):
            return self.parse_embed()
        if self.at_kw("neg", "not"):
            op = self.advance().text
            target = self.parse_lvalue(f"after `{op}`")
            self.expect_op(";", f"after `{op}`")
            return ast.UnaryStmt(self.span_from(start), op, target)
        # otherwise: an update or a swap
        target = self.parse_lvalue("to start a statement")
        if self.at_op("<=>"):
            self.advance()
            right = self.parse_lvalue("on the right of `<=>`")
            self.expect_op(";", "after a swap")
            return ast.Swap(self.span_from(start), target, right)
        for text, op in UPDATE_OPS.items():
            if self.at_op(text):
                self.advance()
                expr = self.parse_expr()
                self.expect_op(";", "after an update")
                return ast.Update(self.span_from(start), op, target, expr)
        if self.at_op("="):
            self.fail(
                "plain assignment is not reversible",
                notes=[
                    "use a reversible update (`+=`, `-=`, `^=`, `*=`, `/=`, `<=>`),",
                    "or wrap destructive code in an `embed` block",
                ],
            )
        self.fail("expected an update operator")
        raise AssertionError  # pragma: no cover

    def parse_if(self) -> ast.Stmt:
        start = self.expect_kw("if")
        entry = self.parse_expr()
        then = self.parse_block()
        otherwise: ast.Stmt = ast.Skip(then.span)
        if self.accept_kw("else"):
            if self.at_kw("if"):
                otherwise = self.parse_if()
            else:
                otherwise = self.parse_block()
        self.expect_kw("fi", "to close a conditional with its exit predicate")
        exit_expr = entry if self.at_op(";") else self.parse_expr()
        self.expect_op(";", "after `fi`")
        return ast.If(self.span_from(start), entry, then, otherwise, exit_expr)

    def parse_loop(self) -> ast.Stmt:
        start = self.expect_kw("from")
        entry = self.parse_expr()
        body: ast.Stmt = ast.Skip(entry.span)
        step: ast.Stmt = ast.Skip(entry.span)
        if self.accept_kw("do"):
            body = self.parse_block()
        if self.accept_kw("loop"):
            step = self.parse_block()
        self.expect_kw("until", "to close a loop with its exit test")
        exit_expr = self.parse_expr()
        self.expect_op(";", "after `until`")
        return ast.Loop(self.span_from(start), entry, body, step, exit_expr)

    def parse_embed(self) -> ast.Stmt:
        start = self.expect_kw("embed")
        self.expect_op("(", "after `embed`")
        bindings: list[ast.Binding] = []
        if not self.at_op(")"):
            while True:
                bstart = self.tok
                target = self.parse_lvalue("as an embed output")
                if self.accept_op("^="):
                    op = "^"
                elif self.accept_op("+="):
                    op = "+"
                elif self.accept_op("-="):
                    op = "-"
                else:
                    self.fail(
                        "expected `^=`, `+=` or `-=` in an embed binding",
                        notes=[
                            "the result is combined into the target reversibly;",
                            "`^=` is the usual choice",
                        ],
                    )
                    raise AssertionError  # pragma: no cover
                expr = self.parse_expr()
                bindings.append(
                    ast.Binding(target, op, expr, self.span_from(bstart))
                )
                if not self.accept_op(","):
                    break
        self.expect_op(")", "to close the embed bindings")
        body = self.parse_cblock()
        self.accept_op(";")
        return ast.Embed(self.span_from(start), bindings, body)

    # -- classical statements --------------------------------------------
    def parse_cblock(self) -> ast.CBlock:
        start = self.expect_op("{", "to open a classical block")
        stmts: list[ast.CStmt] = []
        while not self.at_op("}"):
            if self.tok.kind == TOK_EOF:
                raise ParseError(
                    "unterminated classical block", start.span, label="opened here"
                )
            stmts.append(self.parse_cstmt())
        self.advance()
        return ast.CBlock(self.span_from(start), stmts)

    def parse_cstmt(self, require_semi: bool = True) -> ast.CStmt:
        start = self.tok
        if self.at_op("{"):
            return self.parse_cblock()
        if self.at_kw("var"):
            self.advance()
            name = self.expect_ident("after `var`").text
            expr = None
            size = None
            if self.accept_op("["):
                size = self.parse_expr()
                self.expect_op("]", "to close an array size")
            elif self.accept_op("="):
                expr = self.parse_expr()
            else:
                self.fail("a `var` needs an initialiser or an array size")
            if require_semi:
                self.expect_op(";", "after a `var` declaration")
            return ast.CVar(self.span_from(start), name, expr, size)
        if self.at_kw("if"):
            self.advance()
            self.expect_op("(", "after a classical `if`")
            cond = self.parse_expr()
            self.expect_op(")", "after a classical condition")
            then = self.parse_cblock()
            otherwise: Optional[ast.CStmt] = None
            if self.accept_kw("else"):
                otherwise = (
                    self.parse_cstmt() if self.at_kw("if") else self.parse_cblock()
                )
            return ast.CIf(self.span_from(start), cond, then, otherwise)
        if self.at_kw("while"):
            self.advance()
            self.expect_op("(", "after `while`")
            cond = self.parse_expr()
            self.expect_op(")", "after a while condition")
            body = self.parse_cblock()
            return ast.CWhile(self.span_from(start), cond, body)
        if self.at_kw("for"):
            self.advance()
            self.expect_op("(", "after `for`")
            init = self.parse_cstmt(require_semi=False)
            self.expect_op(";", "after the `for` initialiser")
            cond = self.parse_expr()
            self.expect_op(";", "after the `for` condition")
            step = self.parse_cstmt(require_semi=False)
            self.expect_op(")", "to close a `for` header")
            body = self.parse_cblock()
            return ast.CFor(self.span_from(start), init, cond, step, body)
        target = self.parse_lvalue("to start a classical statement")
        if self.at_op("="):
            self.advance()
            expr = self.parse_expr()
        elif self.tok.text in ("+=", "-=", "*=", "/=", "^=", "%=", "&=", "|="):
            op = self.advance().text[:-1]
            rhs = self.parse_expr()
            expr = ast.BinOp(self.span_from(start), op, target, rhs)
        else:
            self.fail("expected `=` in a classical assignment")
            raise AssertionError  # pragma: no cover
        if require_semi:
            self.expect_op(";", "after a classical assignment")
        return ast.CAssign(self.span_from(start), target, expr)

    # -- expressions ------------------------------------------------------
    def parse_args(self) -> list[ast.Expr]:
        args: list[ast.Expr] = []
        if self.at_op(")"):
            return args
        while True:
            args.append(self.parse_expr())
            if not self.accept_op(","):
                break
        return args

    def parse_lvalue(self, why: str = "") -> ast.LValue:
        start = self.tok
        if start.kind != TOK_IDENT:
            self.fail(f"expected a variable name {why}".rstrip())
        name = self.advance().text
        if self.at_op("["):
            self.advance()
            index = self.parse_expr()
            self.expect_op("]", "to close an index")
            return ast.Index(self.span_from(start), name, index)
        return ast.Var(self.span_from(start), name)

    def parse_expr(self, level: int = 0) -> ast.Expr:
        if level >= len(PRECEDENCE):
            return self.parse_power()
        start = self.tok
        left = self.parse_expr(level + 1)
        while self.tok.kind == TOK_OP and self.tok.text in PRECEDENCE[level]:
            op = self.advance().text
            right = self.parse_expr(level + 1)
            left = ast.BinOp(self.span_from(start), op, left, right)
        return left

    def parse_power(self) -> ast.Expr:
        start = self.tok
        base = self.parse_unary()
        if self.at_op("**"):
            self.advance()
            exponent = self.parse_power()  # right associative
            return ast.BinOp(self.span_from(start), "**", base, exponent)
        return base

    def parse_unary(self) -> ast.Expr:
        start = self.tok
        if self.at_op("-", "!", "~", "+"):
            op = self.advance().text
            operand = self.parse_unary()
            if op == "+":
                return operand
            return ast.UnOp(self.span_from(start), op, operand)
        return self.parse_primary()

    def parse_primary(self) -> ast.Expr:
        start = self.tok
        if self.at_op("("):
            self.advance()
            e = self.parse_expr()
            self.expect_op(")", "to close a parenthesised expression")
            return e
        if start.kind == TOK_INT:
            self.advance()
            return ast.Num(self.span_from(start), start.value)  # type: ignore[arg-type]
        if start.kind == TOK_STRING:
            self.fail("string literals are only allowed in `print`")
        if start.kind == TOK_IDENT:
            name = self.advance().text
            if self.at_op("("):
                self.advance()
                args = self.parse_args()
                self.expect_op(")", "to close a call")
                span = self.span_from(start)
                if name not in BUILTINS:
                    raise ParseError(
                        f"unknown builtin `{name}`",
                        span,
                        notes=[
                            "expressions may only call builtins: "
                            + ", ".join(sorted(BUILTINS)),
                            "procedures are invoked with `call` / `uncall` statements",
                        ],
                    )
                want = BUILTINS[name]
                if len(args) != want:
                    raise ParseError(
                        f"`{name}` takes {want} argument(s), got {len(args)}", span
                    )
                return ast.Builtin(span, name, args)
            if self.at_op("["):
                self.advance()
                index = self.parse_expr()
                self.expect_op("]", "to close an index")
                return ast.Index(self.span_from(start), name, index)
            return ast.Var(self.span_from(start), name)
        self.fail("expected an expression")
        raise AssertionError  # pragma: no cover


def parse(source: Source) -> ast.Module:
    return Parser(source).parse_module()


def parse_text(text: str, name: str = "<input>") -> ast.Module:
    return parse(Source(name, text))
