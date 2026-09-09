"""Search for inputs on which a procedure stops being reversible.

A Reverie procedure carries its own specification.  Every ``if`` ends in an
exit test, every loop begins with an entry test, every ``delocal`` states what
the cell it releases must hold.  Those tests are not decoration: the machine
checks them, and a program that violates one is a program that would have had
to erase information to keep going.

So a procedure needs no hand-written oracle.  Drive it with an input, run it
forwards, and either it completes or it tells you the input was outside the
domain where it is invertible.  Then run it backwards and see whether you land
back where you started.  That is the whole test, and it is the same test for
every procedure ever written.

This module automates the search.  It builds a driver around the procedure,
feeds it random arguments, and checks three properties on each one:

``undo``
    Running the call forwards and then running the machine backwards restores
    the starting state exactly.  This is the machine's own promise.

``uncall``
    ``call f(x); uncall f(x);`` -- both executed with time pointing forwards --
    leaves the state unchanged.  This is the *program's* promise, and it goes
    through different code: the second half enters the body at its far end.

``inverse``
    ``call f(x)`` followed by a call to the procedure ``rev invert`` prints for
    ``f`` leaves the state unchanged.  Nothing about that program came from the
    machine: it is a source-to-source transformation, running forwards through
    ordinary instructions, and this is what holds it to the same claim.

When a case fails, the input is shrunk until no single simplification keeps it
failing, so what gets reported is small enough to read.

Not every procedure is total.  ``reverse_range(xs, lo, hi)`` is only invertible
when ``lo`` and ``hi`` actually bracket a slice, and a search that ignores that
reports a bug that is really a missing sentence of documentation.  So a
procedure may state its domain in its doc comment::

    /// Reverse xs[lo .. hi) in place.
    /// requires: 0 <= lo && lo <= hi && hi <= len(xs)

The condition is an ordinary Reverie expression over the parameters.  It is
compiled and run like any other code -- there is no second little language
here -- and inputs that fail it are never offered to the procedure, during the
search or during shrinking.  Two more directives handle what a filter cannot:
``given:`` pins an argument or sizes an array, and ``setup:`` names code that
builds a state no predicate could describe.  See `pinned` and `preparation`.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Iterator, Optional

from . import ast
from .checker import analyze
from .compiler import Compiler
from .diagnostics import ReverieError, RuntimeFault, Source
from .inverter import invert_proc
from .parser import parse
from .printer import print_type
from .vm import Machine, Program, state_equal

#: properties checked on every case, in the order they are tried
PROPERTIES = ("undo", "uncall", "inverse")

DEFAULT_CASES = 100
DEFAULT_LENGTH = 6
DEFAULT_MAGNITUDE = 12
DEFAULT_STEPS = 200_000
#: memory for each case.  A procedure under test gets a frame stack and its
#: arguments and nothing else, so this is generous; the default matters
#: because the search allocates a fresh machine for every case it tries.
DEFAULT_MEM = 8192
#: how many candidate simplifications a shrink is allowed to try
SHRINK_BUDGET = 400


# ---------------------------------------------------------------------------
# results
# ---------------------------------------------------------------------------


@dataclass
class Failure:
    """One counterexample, already shrunk."""

    prop: str
    reason: str
    inputs: dict
    where: str = ""
    shrinks: int = 0

    def describe(self) -> str:
        what = {
            "undo": "running it backwards did not restore the starting state",
            "uncall": "call, then uncall, did not leave the state alone",
            "inverse": "the procedure `rev invert` prints did not undo it",
        }[self.prop]
        return f"{what}: {self.reason}"


@dataclass
class Report:
    """What the search found out about one procedure."""

    proc: str
    signature: str
    cases: int = 0
    steps: int = 0
    gave_up: int = 0
    skipped: str = ""
    failure: Optional[Failure] = None

    @property
    def ok(self) -> bool:
        return self.failure is None


# ---------------------------------------------------------------------------
# building a driver
# ---------------------------------------------------------------------------


def signature(decl: ast.ProcDecl) -> str:
    params = ", ".join(print_type(p.type, p.name) for p in decl.params)
    return f"{decl.name}({params})"


def kind_of(t: Optional[ast.Type]) -> str:
    if isinstance(t, ast.TArray):
        return "array"
    if isinstance(t, ast.TStack):
        return "stack"
    return "int"


def _fresh(base: str, taken: set) -> str:
    name = base
    n = 0
    while name in taken:
        n += 1
        name = f"{base}_{n}"
    taken.add(name)
    return name


REQUIRES = "requires:"
SETUP = "setup:"
GIVEN = "given:"
OK_GLOBAL = "__verify_ok"
#: the name the printed inverse is given inside a driver
INVERSE = "__verify_inverse"


def directive(decl: ast.ProcDecl, tag: str) -> list:
    """Doc-comment lines of the form ``tag: ...``, in order."""
    out = []
    for line in (decl.doc or "").splitlines():
        text = line.strip()
        if text.lower().startswith(tag):
            body = text[len(tag):].strip()
            if body:
                out.append(body)
    return out


def requirement(decl: ast.ProcDecl) -> str:
    """The domain a procedure claims for itself, as one condition."""
    return " && ".join(f"({p})" for p in directive(decl, REQUIRES))


def preparation(decl: ast.ProcDecl) -> str:
    """Statements that put the arguments into a state worth testing.

    Some preconditions are not conditions at all.  ``hanoi`` needs a peg with
    discs stacked on it in the right order, and no predicate over freshly
    generated inputs will ever be true of an empty stack.  What the procedure
    needs is not a filter but a constructor -- so it may name one, written in
    Reverie against its own parameters::

        /// setup: call stack_up(src, n);

    The search then runs that first, and undoes it afterwards, so the property
    still says exactly what it said before: this procedure, on this state, is
    reversible.
    """
    return " ".join(directive(decl, SETUP))


def pinned(decl: ast.ProcDecl) -> tuple:
    """Arguments the procedure wants held at a fixed value, and array lengths.

    ``given: dst = 0, used = 0`` says what a filter cannot say cheaply: that
    these are outputs, and an output starts empty.  Searching for such an
    input by drawing at random and rejecting would spend its whole budget
    missing.  ``given: len(dst) = 32`` sizes an array argument, for the
    procedures whose arguments are not all the same size.
    """
    values, lengths = {}, {}
    for line in directive(decl, GIVEN):
        for part in line.split(","):
            name, _, value = part.partition("=")
            name, value = name.strip(), value.strip()
            if not name or not value:
                raise ReverieError(f"`given:` wants name = value, got {part.strip()!r}")
            try:
                n = int(value, 0)
            except ValueError:
                raise ReverieError(
                    f"`given: {name} = {value}` needs a whole number"
                ) from None
            if name.startswith("len(") and name.endswith(")"):
                if n < 1:
                    raise ReverieError(f"`given: {name} = {n}` needs a length of 1 or more")
                lengths[name[4:-1].strip()] = n
            else:
                values[name] = n
    return values, lengths


def driver_source(decl: ast.ProcDecl, argnames: list, sizes: list, need_main: bool,
                  requires: str = "", setup: str = "") -> str:
    """Source text for globals to pass in, and procedures to drive."""
    lines = []
    for p, name, size in zip(decl.params, argnames, sizes):
        kind = kind_of(p.type)
        if kind == "array":
            lines.append(f"int {name}[{size}];")
        elif kind == "stack":
            lines.append(f"stack {name};")
        else:
            lines.append(f"int {name};")
    args = ", ".join(argnames)
    params = [print_type(p.type, p.name) for p in decl.params]
    prepare = unprepare = ""
    if setup:
        lines.append(f"proc __verify_setup({', '.join(params)}) {{ {setup} }}")
        prepare = f"call __verify_setup({args}); "
        unprepare = f" uncall __verify_setup({args});"
    lines.append(f"proc __verify_once() {{ {prepare}call {decl.name}({args}); }}")
    lines.append(
        f"proc __verify_round() {{ {prepare}call {decl.name}({args});"
        f" uncall {decl.name}({args});{unprepare} }}"
    )
    lines.append(
        f"proc __verify_mirror() {{ {prepare}call {decl.name}({args});"
        f" call {INVERSE}({args});{unprepare} }}"
    )
    if requires:
        # The condition is written against the procedure's own parameter names,
        # so give the guard the procedure's own signature and it needs no
        # rewriting at all -- the names line up by construction.
        lines.append(f"int {OK_GLOBAL};")
        lines.append(
            f"proc __verify_domain({', '.join(params + [f'int {OK_GLOBAL}'])}) "
            f"{{ {OK_GLOBAL} ^= {requires}; }}"
        )
        call_args = ", ".join(list(argnames) + [OK_GLOBAL])
        lines.append(f"proc __verify_guard() {{ call __verify_domain({call_args}); }}")
    if need_main:
        lines.append("proc main() { }")
    return "\n".join(lines) + "\n"


class Driver:
    """A procedure wrapped up so it can be called from outside."""

    def __init__(self, module: ast.Module, decl: ast.ProcDecl, length: int,
                 explore_globals: bool = False) -> None:
        self.decl = decl
        self.explore_globals = explore_globals
        self.signature = signature(decl)
        taken = {d.name for d in module.decls if hasattr(d, "name")}
        self.argnames = [_fresh(f"__arg_{p.name}", taken) for p in decl.params]
        need_main = module.find_proc("main") is None
        self.requires = requirement(decl)
        self.setup = preparation(decl)
        by_param = dict(zip((p.name for p in decl.params), self.argnames))
        kinds = {p.name: kind_of(p.type) for p in decl.params}
        values, lengths = pinned(decl)
        self.given = {}
        for name, value in values.items():
            if name not in by_param:
                raise ReverieError(f"`given: {name}` is not a parameter of {decl.name}")
            if kinds[name] == "stack":
                raise ReverieError(f"`given: {name}` cannot pin a stack to a number")
            self.given[by_param[name]] = value
        for name in lengths:
            if name not in by_param:
                raise ReverieError(f"`given: len({name})` is not a parameter of {decl.name}")
            if kinds[name] != "array":
                raise ReverieError(f"`given: len({name})` -- {name} is not an array")
        self.lengths = {by_param[k]: v for k, v in lengths.items()}
        sizes = [self.lengths.get(a, length) for a in self.argnames]
        text = driver_source(decl, self.argnames, sizes, need_main,
                             self.requires, self.setup)
        extra = parse(Source(f"<driver for {decl.name}>", text))
        decls = list(module.decls) + list(extra.decls) + [invert_proc(decl, INVERSE)]
        merged = ast.Module(decls, module.source_name)
        self.analysis = analyze(merged)
        if not self.analysis.ok:
            raise ReverieError(self.analysis.diagnostics.errors[0].message)
        self.once = Compiler(merged, self.analysis).compile("__verify_once")
        self.round = Compiler(merged, self.analysis).compile("__verify_round")
        self.mirror = Compiler(merged, self.analysis).compile("__verify_mirror")
        self.guard = (
            Compiler(merged, self.analysis).compile("__verify_guard")
            if self.requires else None
        )
        self.slots = self._slots()

    def satisfies(self, values: dict, mem: int = 0) -> bool:
        """Is this input inside the domain the procedure claims?

        A rejected draw is redrawn, so this runs far more often than the
        procedure itself does; the machine it builds is sized to the globals
        and nothing more.
        """
        if self.guard is None:
            return True
        m = Machine(self.guard, mem_size=mem, max_steps=100_000)
        m.set_globals(values)
        try:
            m.start_forward().run()
        except ReverieError:
            return False
        return m.globals_dict().get(OK_GLOBAL) != 0

    def _slots(self) -> list:
        """(global name, kind, size) for everything the search may set.

        Arguments only, unless the caller asks for more.  A procedure's globals
        are usually the state it was written to start from -- ``main`` almost
        never survives being handed a random one -- so varying them by default
        would bury the real findings in reports about programs being run from
        the middle.
        """
        out = []
        for name in self.argnames:
            g = self.once.globals[name]
            out.append((name, g.kind, g.size))
        if self.explore_globals:
            info = self.analysis.procs.get(self.decl.name)
            for name in sorted(info.touches if info else ()):
                g = self.once.globals.get(name)
                if g is not None and name not in self.argnames:
                    out.append((name, g.kind, g.size))
        return out

    def report_names(self, values: dict) -> dict:
        """Rename the driver's globals back to the procedure's parameters."""
        back = dict(zip(self.argnames, (p.name for p in self.decl.params)))
        return {back.get(k, k): v for k, v in values.items()}


# ---------------------------------------------------------------------------
# generating and shrinking inputs
# ---------------------------------------------------------------------------


def gen_int(rng: random.Random, magnitude: int, length: int = 0) -> int:
    """One argument value.

    Half of the draws come from ``0 .. length-1`` and the edges just outside
    it.  Integers in a Reverie program are so often indices, and the inputs
    that break a loop are so often the ones at the ends of an array, that
    sampling uniformly from a wide range would spend most of its time far away
    from anything interesting -- and would be rejected by any procedure that
    states its domain.
    """
    r = rng.random()
    if r < 0.12:
        return 0
    if r < 0.20:
        return rng.choice((1, -1))
    if r < 0.26:
        return rng.choice((magnitude, -magnitude))
    if length > 0:
        if r < 0.36:
            return rng.choice((length - 1, length, -1))
        if r < 0.68:
            return rng.randint(0, length - 1)
    return rng.randint(-magnitude, magnitude)


def gen_case(rng: random.Random, slots: list, magnitude: int, length: int = 0,
             given: Optional[dict] = None) -> dict:
    given = given or {}
    case = {}
    for name, kind, size in slots:
        fixed = given.get(name)
        if kind == "array":
            case[name] = ([fixed] * size if fixed is not None
                          else [gen_int(rng, magnitude) for _ in range(size)])
        elif kind == "stack":
            case[name] = []
        elif fixed is not None:
            case[name] = fixed
        else:
            case[name] = gen_int(rng, magnitude, length)
    return case


def smaller(v: int) -> list:
    """Simpler integers than ``v``, closest to zero first."""
    if v == 0:
        return []
    out = [0]
    if abs(v) > 1:
        out.append(1 if v > 0 else -1)
        out.append(int(v / 2))
    return [c for c in dict.fromkeys(out) if c != v]


def shrinks(case: dict, frozen: frozenset = frozenset()) -> Iterator[dict]:
    """Candidate simplifications of one case, most aggressive first."""
    for name, val in case.items():
        if name in frozen:
            continue
        if isinstance(val, list):
            if any(v != 0 for v in val):
                yield {**case, name: [0] * len(val)}
            for i, v in enumerate(val):
                for cand in smaller(v):
                    nv = list(val)
                    nv[i] = cand
                    yield {**case, name: nv}
        else:
            for cand in smaller(val):
                yield {**case, name: cand}


# ---------------------------------------------------------------------------
# the search
# ---------------------------------------------------------------------------


class _GaveUp(Exception):
    """The case ran past the step limit.  Not a counterexample."""


#: how many draws to make before deciding a `requires:` cannot be satisfied
DRAW_ATTEMPTS = 400


def _draw(driver: "Driver", rng: random.Random, magnitude: int, length: int):
    """An input inside the procedure's domain, or None if none was found."""
    if not driver.slots:
        return {} if driver.satisfies({}) else None
    for _ in range(DRAW_ATTEMPTS):
        values = gen_case(rng, driver.slots, magnitude, length, driver.given)
        if driver.satisfies(values):
            return values
    return None


def _where(prog: Program, fault: RuntimeFault) -> str:
    if not isinstance(fault, RuntimeFault) or not (0 <= fault.pc < len(prog.code)):
        return ""
    span = prog.code[fault.pc].span
    return span.location() if span else ""


def _machine(prog: Program, values: dict, mem: int, max_steps: int, paranoid: bool):
    m = Machine(prog, mem_size=mem, max_steps=max_steps, paranoid=paranoid)
    m.set_globals(values)
    return m


def check_case(driver: Driver, values: dict, prop: str, *, mem: int,
               max_steps: int, paranoid: bool) -> tuple[Optional[Failure], int]:
    """Run one property on one input.  Returns (failure or None, steps)."""
    prog = {"undo": driver.once, "uncall": driver.round,
            "inverse": driver.mirror}[prop]
    m = _machine(prog, values, mem, max_steps, paranoid)
    before = m.snapshot()
    try:
        m.start_forward().run()
        if prop == "undo":
            m.start_backward().run()
        ok, why = state_equal(before, m.snapshot())
        if ok:
            m.check_clean(f"the end of {driver.decl.name}")
    except RuntimeFault as fault:
        if "step limit" in fault.message:
            raise _GaveUp()
        return Failure(prop, fault.message, values, _where(prog, fault)), m.stats.steps
    except ReverieError as err:
        return Failure(prop, err.message, values), m.stats.steps
    except RecursionError:
        return Failure(prop, "the interpreter ran out of stack", values), m.stats.steps
    except Exception as exc:
        # A Python-level exception escaping the machine is a bug in the
        # machine, not in the program under test -- but it is still something
        # this input found, and reporting it beats a traceback.
        return Failure(
            prop, f"the interpreter crashed: {type(exc).__name__}: {exc}", values
        ), m.stats.steps
    if not ok:
        return Failure(prop, why, values), m.stats.steps
    return None, m.stats.steps


def shrink(driver: Driver, failure: Failure, **kw) -> Failure:
    """Simplify a failing input while it keeps failing."""
    case = failure.inputs
    best = failure
    tried = 0
    improved = True
    while improved and tried < SHRINK_BUDGET:
        improved = False
        for cand in shrinks(case, frozenset(driver.given)):
            tried += 1
            if tried > SHRINK_BUDGET:
                break
            if not driver.satisfies(cand):
                continue
            try:
                found, _ = check_case(driver, cand, failure.prop, **kw)
            except _GaveUp:
                continue
            if found is not None:
                best = Failure(found.prop, found.reason, cand, found.where,
                               best.shrinks + 1)
                case = cand
                improved = True
                break
    return best


def verify_proc(module: ast.Module, decl: ast.ProcDecl, *, cases: int = DEFAULT_CASES,
                seed: int = 0, length: int = DEFAULT_LENGTH,
                magnitude: int = DEFAULT_MAGNITUDE, mem: int = DEFAULT_MEM,
                max_steps: int = DEFAULT_STEPS, paranoid: bool = False,
                explore_globals: bool = False) -> Report:
    """Hunt for an input on which ``decl`` is not reversible."""
    try:
        driver = Driver(module, decl, length, explore_globals)
    except ReverieError as err:
        return Report(decl.name, signature(decl), skipped=err.message)
    report = Report(decl.name, driver.signature)
    rng = random.Random(f"{seed}:{decl.name}")
    kw = dict(mem=mem, max_steps=max_steps, paranoid=paranoid)
    seen = set()
    for _ in range(max(1, cases)):
        values = _draw(driver, rng, magnitude, length)
        if values is None:
            report.skipped = (
                f"no input satisfied `requires: {driver.requires}`"
                if report.cases == 0 else ""
            )
            break
        key = repr(sorted((k, str(v)) for k, v in values.items()))
        if key in seen and driver.slots:
            continue  # already tried this exact input
        seen.add(key)
        report.cases += 1
        for prop in PROPERTIES:
            try:
                failure, steps = check_case(driver, values, prop, **kw)
            except _GaveUp:
                report.gave_up += 1
                continue
            report.steps += steps
            if failure is not None:
                failure = shrink(driver, failure, **kw)
                failure.inputs = driver.report_names(failure.inputs)
                report.failure = failure
                return report
        if not driver.slots:
            break  # nothing to vary: one case is the whole input space
    return report


def verifiable(module: ast.Module) -> list:
    """The procedures worth driving: everything the user actually wrote."""
    out = []
    for d in module.procs():
        if d.name.startswith("__"):
            continue
        out.append(d)
    return out


def verify_module(module: ast.Module, names: Optional[list] = None, **kw) -> list:
    """Run the search over some or all of a module's procedures."""
    wanted = verifiable(module)
    if names:
        by_name = {d.name: d for d in module.procs()}
        missing = [n for n in names if n not in by_name]
        if missing:
            raise ReverieError(f"no procedure named {missing[0]!r}")
        wanted = [by_name[n] for n in names]
    return [verify_proc(module, d, **kw) for d in wanted]


# ---------------------------------------------------------------------------
# rendering
# ---------------------------------------------------------------------------


def show_value(v) -> str:
    if isinstance(v, list):
        return "[" + ", ".join(str(x) for x in v) + "]"
    return str(v)


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def render(reports: list, name: str, width: int = 0) -> str:
    """The report the command line prints."""
    lines = [name]
    if not width:
        longest = max((len(r.signature) for r in reports), default=0)
        width = min(max(longest, 24), 58)
    total_cases = total_steps = 0
    failed = 0
    for r in reports:
        total_cases += r.cases
        total_steps += r.steps
        sig = r.signature if len(r.signature) <= width else r.signature[: width - 1] + "~"
        if r.skipped:
            lines.append(f"  {sig.ljust(width)}  skipped: {r.skipped}")
            continue
        count = f"{r.cases:>5} " + ("case " if r.cases == 1 else "cases")
        gave = f"  ({r.gave_up} gave up)" if r.gave_up else ""
        if r.ok:
            lines.append(f"  {sig.ljust(width)}  {count}   ok{gave}")
            continue
        failed += 1
        lines.append(f"  {sig.ljust(width)}  {count}   FAILED{gave}")
        f = r.failure
        lines.append(f"      {f.describe()}")
        if f.where:
            lines.append(f"      at {f.where}")
        if f.inputs:
            shown = ", ".join(f"{k} = {show_value(v)}" for k, v in f.inputs.items())
            lines.append(f"      smallest input found: {shown}")
        else:
            lines.append("      with every global at zero")
    lines.append("  " + "-" * (width + 22))
    lines.append(
        f"  {_plural(len(reports), 'procedure')}, "
        f"{_plural(total_cases, 'case')}, "
        f"{total_steps:,} instructions executed"
    )
    lines.append(
        "  every procedure was reversible on every input tried"
        if failed == 0
        else f"  {failed} of {_plural(len(reports), 'procedure')} failed"
    )
    return "\n".join(lines)
