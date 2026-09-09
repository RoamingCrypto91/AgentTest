"""Reverie -- a reversible programming language, machine and time-travel debugger.

Reverie programs run in both directions.  Not by replaying a log: the machine
has no log.  Every instruction has an exact inverse, the compiler refuses to
emit anything that would destroy information, and running backwards is the same
code executed by those inverses in the opposite order.

    >>> from reverie import run_source
    >>> m = run_source('int x; proc main() { from x == 0 loop { x += 1; } until x == 5; }')
    >>> m.globals_dict()["x"]
    5
    >>> m.start_backward().run().globals_dict()["x"]
    0

The pieces:

``reverie.lexer`` / ``reverie.parser`` / ``reverie.ast``
    Surface syntax.
``reverie.checker``
    Static proof that a program is injective.
``reverie.compiler`` / ``reverie.rir``
    Lowering, and inversion as an ordinary IR operation.
``reverie.isa`` / ``reverie.vm``
    The bidirectional machine.
``reverie.inverter``
    Source-to-source program inversion.
``reverie.debugger``
    Stepping backwards through a running program.
"""

import sys as _sys

#: Reverie integers are arbitrary precision, but CPython caps how many digits
#: an int/str conversion may involve (4300 by default) as a denial-of-service
#: guard.  Without lifting it, a legal program that computes a large number
#: turns into a Python error the moment it is printed.  The cap is raised, not
#: removed: an absurd literal still gets a diagnostic rather than a hang.
_DIGIT_LIMIT = 200_000
if hasattr(_sys, "set_int_max_str_digits"):
    if _sys.get_int_max_str_digits() < _DIGIT_LIMIT:
        _sys.set_int_max_str_digits(_DIGIT_LIMIT)

from .diagnostics import (  # noqa: F401
    CheckError,
    CompileError,
    Diagnostics,
    LexError,
    ParseError,
    ReverieError,
    RuntimeFault,
    Source,
    Span,
)

__version__ = "1.0.0"
__all__ = [
    "__version__",
    "compile_source",
    "run_source",
    "invert_source",
    "parse_source",
    "check_source",
    "Machine",
    "Program",
    "ReverieError",
]


def parse_source(text: str, name: str = "<input>"):
    from .parser import parse_text

    return parse_text(text, name)


def check_source(text: str, name: str = "<input>"):
    """Analyze *text* and return the :class:`~reverie.checker.Analysis`."""
    from .checker import analyze

    return analyze(parse_source(text, name))


def compile_source(text: str, name: str = "<input>", entry: str = "main"):
    from .compiler import compile_text

    return compile_text(text, name, entry)


def run_source(text: str, name: str = "<input>", initial=None, **kw):
    from .vm import Machine

    prog = compile_source(text, name)
    m = Machine(prog, **kw)
    if initial:
        m.set_globals(initial)
    return m.start_forward().run()


def invert_source(text: str, name: str = "<input>", targets=("main",)) -> str:
    from .inverter import invert_source as _inv

    return _inv(text, name, targets)


from .vm import Machine, Program  # noqa: E402,F401
