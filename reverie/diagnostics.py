"""Source positions, spans, and human-readable diagnostics.

Everything in Reverie that can fail at compile time raises a :class:`ReverieError`
carrying an optional :class:`Span`.  The :meth:`ReverieError.render` method turns
that into the familiar caret-underlined message::

    error: cannot update `x` using an expression that reads `x`
      --> examples/bad.rev:4:5
       |
     4 |     x += x * 2;
       |     ^^^^^^^^^^ `x` appears on both sides
       |
    note: reversible updates require the right-hand side to be independent
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Sequence


class Source:
    """An immutable source file with a cached line index."""

    __slots__ = ("name", "text", "_line_starts")

    def __init__(self, name: str, text: str) -> None:
        self.name = name
        self.text = text
        starts = [0]
        for i, ch in enumerate(text):
            if ch == "\n":
                starts.append(i + 1)
        self._line_starts = starts

    # -- position helpers -------------------------------------------------
    def line_col(self, offset: int) -> tuple[int, int]:
        """Return the 1-based (line, column) of a byte offset."""
        offset = max(0, min(offset, len(self.text)))
        lo, hi = 0, len(self._line_starts) - 1
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if self._line_starts[mid] <= offset:
                lo = mid
            else:
                hi = mid - 1
        return lo + 1, offset - self._line_starts[lo] + 1

    def line_text(self, lineno: int) -> str:
        """Return the text of a 1-based line number, without its newline."""
        if lineno < 1 or lineno > len(self._line_starts):
            return ""
        start = self._line_starts[lineno - 1]
        end = self.text.find("\n", start)
        if end == -1:
            end = len(self.text)
        return self.text[start:end]

    @property
    def line_count(self) -> int:
        return len(self._line_starts)

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"<Source {self.name!r} {len(self.text)} bytes>"


@dataclass(frozen=True)
class Span:
    """A half-open [start, end) range of offsets within a :class:`Source`."""

    source: Source
    start: int
    end: int

    def __post_init__(self) -> None:
        if self.end < self.start:
            raise ValueError("span end precedes start")

    def merge(self, other: Optional["Span"]) -> "Span":
        if other is None or other.source is not self.source:
            return self
        return Span(self.source, min(self.start, other.start), max(self.end, other.end))

    @property
    def text(self) -> str:
        return self.source.text[self.start : self.end]

    def location(self) -> str:
        line, col = self.source.line_col(self.start)
        return f"{self.source.name}:{line}:{col}"

    def __str__(self) -> str:  # pragma: no cover - debug helper
        return self.location()


SYNTHETIC = None  # spans are optional; None means "compiler-generated"


class ReverieError(Exception):
    """A user-facing error with an optional source span and notes."""

    kind = "error"

    def __init__(
        self,
        message: str,
        span: Optional[Span] = None,
        *,
        label: str = "",
        notes: Sequence[str] = (),
    ) -> None:
        super().__init__(message)
        self.message = message
        self.span = span
        self.label = label
        self.notes = list(notes)

    def render(self, color: bool = False) -> str:
        red = "\x1b[31;1m" if color else ""
        blue = "\x1b[34;1m" if color else ""
        bold = "\x1b[1m" if color else ""
        off = "\x1b[0m" if color else ""

        out = [f"{red}{self.kind}{off}{bold}: {self.message}{off}"]
        if self.span is not None:
            src = self.span.source
            line, col = src.line_col(self.span.start)
            end_line, end_col = src.line_col(max(self.span.start, self.span.end - 1))
            gutter = len(str(end_line))
            pad = " " * gutter
            out.append(f"{blue}{pad}--> {off}{src.name}:{line}:{col}")
            out.append(f"{blue}{pad} |{off}")
            for ln in range(line, min(end_line, line + 4) + 1):
                text = src.line_text(ln)
                if ln == line:
                    width = (
                        max(1, self.span.end - self.span.start)
                        if end_line == line
                        else max(1, len(text) - col + 1)
                    )
                    text, shown_col, width = _window(text, col, width)
                    out.append(f"{blue}{str(ln).rjust(gutter)} |{off} {text}")
                    caret = " " * (shown_col - 1) + "^" * width
                    tail = f" {self.label}" if self.label else ""
                    out.append(f"{blue}{pad} |{off} {red}{caret}{tail}{off}")
                else:
                    out.append(
                        f"{blue}{str(ln).rjust(gutter)} |{off} "
                        f"{_window(text, 1, 1)[0]}"
                    )
            if end_line > line + 4:
                out.append(f"{blue}{pad} |{off} ...")
            out.append(f"{blue}{pad} |{off}")
        for note in self.notes:
            out.append(f"{blue}note{off}: {note}")
        return "\n".join(out)

    def __str__(self) -> str:
        if self.span is not None:
            return f"{self.span.location()}: {self.message}"
        return self.message


#: how much of a source line to show around the caret
LINE_WINDOW = 100


def _window(text: str, col: int, width: int) -> tuple[str, int, int]:
    """Trim a long source line to a window around the caret.

    Generated or minified sources can put a whole program on one line, and a
    diagnostic that echoes half a megabyte of it is not a diagnostic.
    """
    if len(text) <= LINE_WINDOW:
        return text, col, min(width, max(1, len(text) - col + 1))
    half = LINE_WINDOW // 2
    start = max(0, col - 1 - half)
    end = min(len(text), start + LINE_WINDOW)
    start = max(0, end - LINE_WINDOW)
    piece = text[start:end]
    shown_col = col - start
    if start > 0:
        piece = "..." + piece
        shown_col += 3
    if end < len(text):
        piece = piece + "..."
    width = max(1, min(width, len(piece) - shown_col + 1))
    return piece, max(1, shown_col), width


class LexError(ReverieError):
    pass


class ParseError(ReverieError):
    pass


class CheckError(ReverieError):
    """Raised by the reversibility / linearity checker."""


class CompileError(ReverieError):
    pass


class AssemblyError(ReverieError):
    pass


class RuntimeFault(ReverieError):
    """A trap raised while the machine is running.

    Reversible machines are unusually fault-prone by design: an assertion that
    would silently be a no-op in a classical language (``x`` must be zero before
    it is allocated) is load-bearing here, because violating it would destroy
    information.  Every such violation stops the machine rather than erasing.
    """

    kind = "trap"

    def __init__(self, message: str, *, pc: int = -1, notes: Sequence[str] = ()) -> None:
        super().__init__(message, None, notes=notes)
        self.pc = pc

    def __str__(self) -> str:
        if self.pc >= 0:
            return f"{self.message} (at pc={self.pc})"
        return self.message


@dataclass
class Diagnostics:
    """Collects multiple errors so a pass can report more than the first one."""

    errors: list[ReverieError] = field(default_factory=list)
    limit: int = 25

    def error(self, message: str, span: Optional[Span] = None, **kw) -> None:
        self.errors.append(CheckError(message, span, **kw))

    def add(self, err: ReverieError) -> None:
        self.errors.append(err)

    @property
    def ok(self) -> bool:
        return not self.errors

    def raise_if_errors(self) -> None:
        if self.errors:
            raise self.errors[0]

    def render(self, color: bool = False) -> str:
        shown = self.errors[: self.limit]
        parts = [e.render(color) for e in shown]
        if len(self.errors) > len(shown):
            parts.append(f"... and {len(self.errors) - len(shown)} more errors")
        return "\n\n".join(parts)
