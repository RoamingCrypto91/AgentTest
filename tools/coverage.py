"""A minimal line-coverage collector, so the test suite can be measured.

`coverage.py` is not installed and Reverie has no dependencies, so this uses
`sys.settrace` directly: record every line executed in `reverie/`, subtract from
the lines that are executable, and report what is left.

    python3 tools/coverage.py                 # run the suite under the tracer
    python3 tools/coverage.py --slow          # including the slow tests
    python3 tools/coverage.py --show vm.py    # list the missed lines
"""

from __future__ import annotations

import argparse
import dis
import os
import sys
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PKG = os.path.join(ROOT, "reverie")


def executable_lines(path: str) -> set[int]:
    """Line numbers that can actually be executed, from the code objects.

    Lines marked ``# pragma: no cover`` are excluded, along with the rest of
    the statement they open -- which is how a multi-line ``raise`` for a case
    the type system already rules out stays out of the numbers.
    """
    with open(path) as fh:
        src = fh.read()
    lines = src.splitlines()
    code = compile(src, path, "exec")
    seen: set[int] = set()
    stack = [code]
    while stack:
        c = stack.pop()
        for _, _, line in c.co_lines():
            if line:
                seen.add(line)
        for const in c.co_consts:
            if hasattr(const, "co_code"):
                stack.append(const)

    excused: set[int] = set()
    for i, text in enumerate(lines, 1):
        if "pragma: no cover" not in text:
            continue
        excused.add(i)
        indent = len(text) - len(text.lstrip())
        for j in range(i, len(lines)):
            nxt = lines[j]
            if not nxt.strip():
                continue
            if len(nxt) - len(nxt.lstrip()) <= indent and not nxt.lstrip().startswith(
                (")", "]", "}")
            ):
                break
            excused.add(j + 1)
    return seen - excused


class Tracer:
    def __init__(self, prefix: str) -> None:
        self.prefix = prefix
        self.hits: dict[str, set[int]] = {}

    def __call__(self, frame, event, arg):
        path = frame.f_code.co_filename
        if not path.startswith(self.prefix):
            return None
        if event == "call":
            return self
        if event == "line":
            self.hits.setdefault(path, set()).add(frame.f_lineno)
        return self

    def start(self) -> None:
        threading.settrace(self)
        sys.settrace(self)

    def stop(self) -> None:
        sys.settrace(None)
        threading.settrace(None)


def main() -> int:
    ap = argparse.ArgumentParser(description="measure the Reverie test suite")
    ap.add_argument("--slow", action="store_true")
    ap.add_argument("--repeat", type=int, default=1)
    ap.add_argument("--show", default="",
                    help="list missed lines (comma-separated module names)")
    ap.add_argument("--min", type=float, default=0.0, help="fail below this percent")
    args = ap.parse_args()

    sys.path.insert(0, ROOT)
    sys.path.insert(0, os.path.join(ROOT, "tests"))
    from framework import main as run_tests

    tracer = Tracer(PKG + os.sep)
    argv = ["--no-color", "--repeat", str(args.repeat)]
    if args.slow:
        argv.append("--slow")
    tracer.start()
    try:
        status = run_tests(argv)
    finally:
        tracer.stop()

    print()
    rows = []
    total_exec = total_hit = 0
    for name in sorted(os.listdir(PKG)):
        if not name.endswith(".py"):
            continue
        path = os.path.join(PKG, name)
        lines = executable_lines(path)
        hit = tracer.hits.get(path, set()) & lines
        total_exec += len(lines)
        total_hit += len(hit)
        rows.append((name, len(lines), len(hit), sorted(lines - hit)))

    width = max(len(r[0]) for r in rows)
    print(f"{'module'.ljust(width)}  lines   hit   miss   coverage")
    print("-" * (width + 32))
    for name, n, h, missed in sorted(rows, key=lambda r: r[2] / max(1, r[1])):
        pct = 100.0 * h / max(1, n)
        print(f"{name.ljust(width)}  {n:5d} {h:5d}  {n - h:5d}   {pct:6.1f}%")
    pct = 100.0 * total_hit / max(1, total_exec)
    print("-" * (width + 32))
    print(f"{'total'.ljust(width)}  {total_exec:5d} {total_hit:5d}  "
          f"{total_exec - total_hit:5d}   {pct:6.1f}%")

    if args.show:
        wanted = [w.strip() for w in args.show.split(",") if w.strip()]
        for name, n, h, missed in rows:
            if any(w in name for w in wanted):
                print(f"\nmissed in {name}:")
                with open(os.path.join(PKG, name)) as fh:
                    src = fh.read().splitlines()
                for line in missed:
                    text = src[line - 1] if line <= len(src) else ""
                    print(f"  {line:5d}  {text}")
    if args.min and pct < args.min:
        print(f"\ncoverage {pct:.1f}% is below the required {args.min:.1f}%")
        return 1
    return status


if __name__ == "__main__":
    raise SystemExit(main())
