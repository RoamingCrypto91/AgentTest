"""Tokeniser for Reverie source."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .diagnostics import LexError, Source, Span

KEYWORDS = {
    "proc",
    "const",
    "int",
    "stack",
    "local",
    "delocal",
    "call",
    "uncall",
    "if",
    "else",
    "fi",
    "from",
    "do",
    "loop",
    "until",
    "push",
    "pop",
    "print",
    "unprint",
    "write",
    "unwrite",
    "assert",
    "skip",
    "undo",
    "embed",
    "var",
    "while",
    "for",
    "neg",
    "not",
    "import",
}

#: multi-character operators, longest first so the scanner is greedy
OPERATORS = [
    "<=>",
    "<<=",
    ">>=",
    "**=",
    "+=",
    "-=",
    "^=",
    "*=",
    "/=",
    "%=",
    "&=",
    "|=",
    "==",
    "!=",
    "<=",
    ">=",
    "&&",
    "||",
    "<<",
    ">>",
    "**",
    "->",
    "(",
    ")",
    "{",
    "}",
    "[",
    "]",
    ",",
    ";",
    ":",
    "=",
    "+",
    "-",
    "*",
    "/",
    "%",
    "&",
    "|",
    "^",
    "~",
    "!",
    "<",
    ">",
    ".",
]

TOK_IDENT = "ident"
TOK_INT = "int"
TOK_STRING = "string"
TOK_KEYWORD = "keyword"
TOK_OP = "op"
TOK_DOC = "doc"
TOK_EOF = "eof"


@dataclass(frozen=True)
class Token:
    kind: str
    text: str
    span: Span
    value: object = None

    def is_op(self, *ops: str) -> bool:
        return self.kind == TOK_OP and self.text in ops

    def is_kw(self, *kws: str) -> bool:
        return self.kind == TOK_KEYWORD and self.text in kws

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"{self.kind}:{self.text!r}"


ESCAPES = {
    "n": "\n",
    "t": "\t",
    "r": "\r",
    "0": "\0",
    "\\": "\\",
    '"': '"',
    "'": "'",
}


def tokenize(source: Source) -> list[Token]:
    text = source.text
    n = len(text)
    i = 0
    out: list[Token] = []

    def span(a: int, b: int) -> Span:
        return Span(source, a, b)

    while i < n:
        ch = text[i]

        # whitespace
        if ch in " \t\r\n":
            i += 1
            continue

        # comments
        if ch == "/" and i + 1 < n:
            if text[i + 1] == "/":
                j = text.find("\n", i)
                end = n if j == -1 else j
                if text.startswith("///", i):
                    out.append(
                        Token(TOK_DOC, text[i + 3 : end].strip(), span(i, end))
                    )
                i = n if j == -1 else j + 1
                continue
            if text[i + 1] == "*":
                j = text.find("*/", i + 2)
                if j == -1:
                    raise LexError("unterminated block comment", span(i, n))
                i = j + 2
                continue

        # identifiers and keywords
        if ch.isalpha() or ch == "_":
            j = i
            while j < n and (text[j].isalnum() or text[j] == "_"):
                j += 1
            word = text[i:j]
            kind = TOK_KEYWORD if word in KEYWORDS else TOK_IDENT
            out.append(Token(kind, word, span(i, j)))
            i = j
            continue

        # numbers
        if ch.isdigit():
            j = i
            base = 10
            if ch == "0" and j + 1 < n and text[j + 1] in "xXbBoO":
                base = {"x": 16, "b": 2, "o": 8}[text[j + 1].lower()]
                j += 2
                start_digits = j
                while j < n and (text[j].isalnum() or text[j] == "_"):
                    j += 1
                digits = text[start_digits:j].replace("_", "")
                if not digits:
                    raise LexError("numeric literal has no digits", span(i, j))
                try:
                    value = int(digits, base)
                except ValueError as exc:
                    if "Exceeds the limit" in str(exc):
                        raise LexError(
                            f"numeric literal has {len(digits)} digits, which "
                            f"is more than this interpreter will convert",
                            span(i, j),
                        )
                    raise LexError(
                        f"invalid base-{base} literal {text[i:j]!r}", span(i, j)
                    )
            else:
                while j < n and (text[j].isdigit() or text[j] == "_"):
                    j += 1
                if j < n and (text[j].isalpha()):
                    raise LexError(
                        f"invalid numeric literal {text[i:j + 1]!r}", span(i, j + 1)
                    )
                digits = text[i:j].replace("_", "")
                try:
                    value = int(digits)
                except ValueError:
                    raise LexError(
                        f"numeric literal has {len(digits)} digits, which is "
                        f"more than this interpreter will convert",
                        span(i, j),
                        notes=["Reverie integers are unbounded, but a literal "
                               "this long is almost certainly a mistake"],
                    )
            out.append(Token(TOK_INT, text[i:j], span(i, j), value))
            i = j
            continue

        # strings
        if ch == '"':
            j = i + 1
            buf = []
            while True:
                if j >= n or text[j] == "\n":
                    raise LexError("unterminated string literal", span(i, min(j, n)))
                c = text[j]
                if c == "\\":
                    if j + 1 >= n:
                        raise LexError("unterminated escape", span(j, n))
                    esc = text[j + 1]
                    if esc not in ESCAPES:
                        raise LexError(
                            f"unknown escape \\{esc}", span(j, j + 2),
                            notes=["valid escapes: " + " ".join("\\" + k for k in ESCAPES)],
                        )
                    buf.append(ESCAPES[esc])
                    j += 2
                    continue
                if c == '"':
                    j += 1
                    break
                buf.append(c)
                j += 1
            out.append(Token(TOK_STRING, text[i:j], span(i, j), "".join(buf)))
            i = j
            continue

        # character literals are just integers
        if ch == "'":
            j = i + 1
            if j < n and text[j] == "\\":
                esc = text[j + 1] if j + 1 < n else ""
                if esc not in ESCAPES:
                    raise LexError(f"unknown escape \\{esc}", span(j, j + 2))
                value = ord(ESCAPES[esc])
                j += 2
            else:
                if j >= n:
                    raise LexError("unterminated character literal", span(i, n))
                value = ord(text[j])
                j += 1
            if j >= n or text[j] != "'":
                raise LexError("unterminated character literal", span(i, min(j + 1, n)))
            j += 1
            out.append(Token(TOK_INT, text[i:j], span(i, j), value))
            i = j
            continue

        # operators
        for op in OPERATORS:
            if text.startswith(op, i):
                out.append(Token(TOK_OP, op, span(i, i + len(op))))
                i += len(op)
                break
        else:
            raise LexError(f"unexpected character {ch!r}", span(i, i + 1))

    out.append(Token(TOK_EOF, "", span(n, n)))
    return out
