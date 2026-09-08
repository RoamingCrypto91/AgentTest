"""A dependency-free test runner.

Reverie ships with no third-party dependencies, tests included.  This module is
a small but complete xunit-style runner: discovery, parametrised cases, fixtures
via plain decorators, filtering, fail-fast, timing, deterministic seeding for
the property tests, and a readable summary.

    python3 tests/run_tests.py                 # everything
    python3 tests/run_tests.py -k roundtrip    # substring filter
    python3 tests/run_tests.py -x -v           # stop on first failure, verbose
    python3 tests/run_tests.py --seed 12345    # reproduce a fuzz failure
    python3 tests/run_tests.py --repeat 10     # crank the property tests
"""

from __future__ import annotations

import argparse
import importlib.util
import io
import os
import random
import sys
import time
import traceback
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Optional

# ---------------------------------------------------------------------------
# registry
# ---------------------------------------------------------------------------


@dataclass
class TestCase:
    name: str
    func: Callable[..., Any]
    module: str
    args: tuple = ()
    kwargs: dict = field(default_factory=dict)
    tags: tuple[str, ...] = ()
    slow: bool = False

    @property
    def full_name(self) -> str:
        if self.args or self.kwargs:
            bits = [_short(a) for a in self.args]
            bits += [f"{k}={_short(v)}" for k, v in self.kwargs.items()]
            return f"{self.module}::{self.name}[{', '.join(bits)}]"
        return f"{self.module}::{self.name}"


def _short(v: Any, limit: int = 24) -> str:
    s = repr(v)
    return s if len(s) <= limit else s[: limit - 1] + "…"


_REGISTRY: list[TestCase] = []
_CURRENT_MODULE = "?"


def _register(func, args=(), kwargs=None, tags=(), slow=False) -> None:
    _REGISTRY.append(
        TestCase(
            name=func.__name__,
            func=func,
            module=getattr(func, "__module__", _CURRENT_MODULE),
            args=tuple(args),
            kwargs=dict(kwargs or {}),
            tags=tuple(tags),
            slow=slow,
        )
    )


def case(*args, **kwargs):
    """Attach one parameter set to a test function.  Stackable."""

    def deco(func):
        pending = getattr(func, "_reverie_cases", None)
        if pending is None:
            pending = []
            func._reverie_cases = pending
        pending.append((args, kwargs))
        return func

    return deco


def slow(func):
    """Mark a test as slow; skipped unless ``--slow`` is passed."""
    func._reverie_slow = True
    return func


def tag(*names):
    def deco(func):
        func._reverie_tags = tuple(names) + getattr(func, "_reverie_tags", ())
        return func

    return deco


class Skip(Exception):
    """Raise to skip a test at runtime."""


def skip(reason: str) -> None:
    raise Skip(reason)


# ---------------------------------------------------------------------------
# assertions
# ---------------------------------------------------------------------------


class AssertionFail(AssertionError):
    pass


def _fail(msg: str) -> None:
    raise AssertionFail(msg)


def eq(actual, expected, msg: str = "") -> None:
    if actual != expected:
        detail = f"\n  expected: {expected!r}\n  actual:   {actual!r}"
        if isinstance(actual, str) and isinstance(expected, str):
            detail += "\n" + _string_diff(expected, actual)
        _fail((msg + detail) if msg else "values differ" + detail)


def ne(actual, unexpected, msg: str = "") -> None:
    if actual == unexpected:
        _fail(msg or f"expected something other than {unexpected!r}")


def is_true(value, msg: str = "") -> None:
    if not value:
        _fail(msg or f"expected truthy, got {value!r}")


def is_false(value, msg: str = "") -> None:
    if value:
        _fail(msg or f"expected falsy, got {value!r}")


def close(a: float, b: float, tol: float = 1e-9, msg: str = "") -> None:
    if abs(a - b) > tol:
        _fail(msg or f"{a} and {b} differ by more than {tol}")


def contains(haystack, needle, msg: str = "") -> None:
    if needle not in haystack:
        _fail(msg or f"{needle!r} not found in {haystack!r}")


class _Raises:
    def __init__(self, exc_type, match: str = "") -> None:
        self.exc_type = exc_type
        self.match = match
        self.error: Optional[BaseException] = None

    def __enter__(self) -> "_Raises":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        if exc is None:
            _fail(f"expected {self.exc_type.__name__} but no exception was raised")
        if not isinstance(exc, self.exc_type):
            return False
        if self.match and self.match not in str(exc):
            _fail(
                f"expected {self.exc_type.__name__} matching {self.match!r},\n"
                f"  got: {exc}"
            )
        self.error = exc
        return True


def raises(exc_type, match: str = "") -> _Raises:
    return _Raises(exc_type, match)


def _string_diff(expected: str, actual: str) -> str:
    exp_lines = expected.splitlines()
    act_lines = actual.splitlines()
    out = ["  --- diff (expected vs actual) ---"]
    for i in range(max(len(exp_lines), len(act_lines))):
        e = exp_lines[i] if i < len(exp_lines) else "<missing>"
        a = act_lines[i] if i < len(act_lines) else "<missing>"
        if e != a:
            out.append(f"  line {i + 1}:")
            out.append(f"    - {e}")
            out.append(f"    + {a}")
    if len(out) == 1:
        out.append("  (identical line-by-line; trailing whitespace differs)")
    return "\n".join(out[:40])


# ---------------------------------------------------------------------------
# golden files
# ---------------------------------------------------------------------------

GOLDEN_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "golden")


def golden(name: str, actual: str) -> None:
    """Compare *actual* against ``tests/golden/<name>``.

    ``REVERIE_UPDATE_GOLDEN=1`` rewrites the file instead of failing, which is
    how the snapshots in this repo are regenerated.
    """
    os.makedirs(GOLDEN_DIR, exist_ok=True)
    path = os.path.join(GOLDEN_DIR, name)
    if os.environ.get("REVERIE_UPDATE_GOLDEN") == "1" or not os.path.exists(path):
        with open(path, "w") as fh:
            fh.write(actual)
        return
    with open(path) as fh:
        expected = fh.read()
    if expected != actual:
        _fail(
            f"golden mismatch for {name}\n"
            + _string_diff(expected, actual)
            + "\n  (rerun with REVERIE_UPDATE_GOLDEN=1 to accept)"
        )


# ---------------------------------------------------------------------------
# runner
# ---------------------------------------------------------------------------


@dataclass
class Result:
    case: TestCase
    status: str  # pass | fail | error | skip
    seconds: float
    message: str = ""
    output: str = ""


class Runner:
    def __init__(self, args) -> None:
        self.args = args
        self.results: list[Result] = []
        self.color = sys.stdout.isatty() and not args.no_color

    def c(self, text: str, code: str) -> str:
        return f"\x1b[{code}m{text}\x1b[0m" if self.color else text

    def discover(self, paths: Iterable[str]) -> None:
        global _CURRENT_MODULE
        here = os.path.dirname(os.path.abspath(__file__))
        files: list[str] = []
        for p in paths:
            if os.path.isdir(p):
                files.extend(
                    sorted(
                        os.path.join(p, f)
                        for f in os.listdir(p)
                        if f.startswith("test_") and f.endswith(".py")
                    )
                )
            else:
                files.append(p)
        for path in files:
            mod_name = os.path.splitext(os.path.basename(path))[0]
            _CURRENT_MODULE = mod_name
            spec = importlib.util.spec_from_file_location(mod_name, path)
            assert spec and spec.loader
            module = importlib.util.module_from_spec(spec)
            sys.modules[mod_name] = module
            spec.loader.exec_module(module)
            for name in sorted(vars(module)):
                obj = getattr(module, name)
                if not name.startswith("test_") or not callable(obj):
                    continue
                cases = getattr(obj, "_reverie_cases", None)
                tags = getattr(obj, "_reverie_tags", ())
                is_slow = getattr(obj, "_reverie_slow", False)
                if cases:
                    for a, kw in reversed(cases):
                        _register(obj, a, kw, tags, is_slow)
                else:
                    _register(obj, (), {}, tags, is_slow)
                _REGISTRY[-1].module = mod_name
                for r in _REGISTRY:
                    if r.func is obj:
                        r.module = mod_name

    def selected(self) -> list[TestCase]:
        out = []
        for tc in _REGISTRY:
            if self.args.k and not any(
                part.strip() and part.strip() in tc.full_name
                for part in self.args.k.split(",")
            ):
                continue
            if tc.slow and not self.args.slow:
                continue
            out.append(tc)
        return out

    def run(self) -> int:
        cases = self.selected()
        started = time.time()
        width = 0
        failures: list[Result] = []
        print(
            self.c(
                f"reverie test suite — {len(cases)} cases, seed={self.args.seed}",
                "1",
            )
        )
        by_module: dict[str, list[TestCase]] = {}
        for tc in cases:
            by_module.setdefault(tc.module, []).append(tc)

        for module, mod_cases in by_module.items():
            line_start = time.time()
            marks = []
            mod_failed = 0
            for tc in mod_cases:
                res = self.run_one(tc)
                self.results.append(res)
                if res.status == "pass":
                    marks.append(self.c(".", "32"))
                elif res.status == "skip":
                    marks.append(self.c("s", "33"))
                else:
                    marks.append(self.c("F", "31;1"))
                    failures.append(res)
                    mod_failed += 1
                    if self.args.x:
                        break
                if self.args.v:
                    stat = res.status.upper().ljust(5)
                    print(f"  {stat} {tc.full_name} ({res.seconds * 1000:.1f} ms)")
            if not self.args.v:
                dur = time.time() - line_start
                bar = "".join(marks)
                print(f"  {module:<28s} {bar}  {dur:.2f}s")
            if self.args.x and mod_failed:
                break

        total = time.time() - started
        self.report(failures, total)
        return 1 if failures else 0

    def run_one(self, tc: TestCase) -> Result:
        random.seed(self.args.seed + hash(tc.full_name) % 100000)
        buf = io.StringIO()
        stdout = sys.stdout
        t0 = time.time()
        try:
            if not self.args.capture_off:
                sys.stdout = buf
            tc.func(*tc.args, **tc.kwargs)
            status, message = "pass", ""
        except Skip as exc:
            status, message = "skip", str(exc)
        except AssertionFail as exc:
            status, message = "fail", str(exc)
        except Exception:
            status, message = "error", traceback.format_exc()
        finally:
            sys.stdout = stdout
        return Result(tc, status, time.time() - t0, message, buf.getvalue())

    def report(self, failures: list[Result], total: float) -> None:
        npass = sum(1 for r in self.results if r.status == "pass")
        nskip = sum(1 for r in self.results if r.status == "skip")
        for res in failures:
            print()
            print(self.c(f"{'=' * 72}", "31"))
            print(self.c(f"{res.status.upper()}: {res.case.full_name}", "31;1"))
            print(self.c(f"{'=' * 72}", "31"))
            print(res.message.rstrip())
            if res.output.strip():
                print(self.c("--- captured stdout ---", "90"))
                print(res.output.rstrip())
        print()
        slowest = sorted(self.results, key=lambda r: -r.seconds)[:5]
        if self.args.durations and slowest:
            print(self.c("slowest cases:", "1"))
            for r in slowest:
                print(f"  {r.seconds * 1000:8.1f} ms  {r.case.full_name}")
        summary = f"{npass} passed"
        if failures:
            summary += f", {len(failures)} failed"
        if nskip:
            summary += f", {nskip} skipped"
        summary += f" in {total:.2f}s"
        print(self.c(summary, "32;1" if not failures else "31;1"))


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Reverie test runner")
    ap.add_argument("paths", nargs="*", default=None)
    ap.add_argument("-k", default="", help="substring filter; comma-separated alternatives")
    ap.add_argument("-x", action="store_true", help="stop after first failure")
    ap.add_argument("-v", action="store_true", help="verbose, one line per test")
    ap.add_argument("--slow", action="store_true", help="include slow tests")
    ap.add_argument("--seed", type=int, default=0xC0FFEE, help="RNG seed")
    ap.add_argument("--repeat", type=int, default=1, help="fuzz iteration multiplier")
    ap.add_argument("--durations", action="store_true", help="show slowest tests")
    ap.add_argument("--no-color", action="store_true")
    ap.add_argument("--capture-off", action="store_true", help="let tests print")
    args = ap.parse_args(argv)
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.dirname(here)
    if root not in sys.path:
        sys.path.insert(0, root)
    os.environ["REVERIE_FUZZ_REPEAT"] = str(args.repeat)
    os.environ["REVERIE_SEED"] = str(args.seed)
    runner = Runner(args)
    runner.discover(args.paths or [here])
    return runner.run()
