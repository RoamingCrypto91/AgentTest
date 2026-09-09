"""Mutation testing for programs, with `rev verify` as the oracle.

`tools/mutate.py` asks whether the test suite would notice if the *machine*
were wrong.  This asks the other question: if a Reverie program were wrong,
would `rev verify` notice?

It breaks the shipped library and examples on purpose -- one predicate, one
`delocal`, one loop step at a time -- and reports what happens to each mutant:

``rejected``
    the checker refused it, so it never reached the search
``caught``
    the search found an input on which the mutant is not reversible
``hung``
    every case ran past the step limit: the mutant does not terminate, which
    the search reports rather than hiding
``survived``
    nothing noticed

A survivor is not automatically a hole.  Reversibility is a claim about
whether a procedure can be undone, not about what it computes: turning
``acc += xs[i]`` into ``acc -= xs[i]`` gives a different -- and perfectly
reversible -- procedure, and no amount of searching will call it a bug.  This
tool mutates those on purpose too, in a separate group, so the difference
between "the search is weak here" and "the property does not claim this" is
visible rather than assumed.

    python3 tools/mutate_programs.py
    python3 tools/mutate_programs.py --only sort.rev --cases 40
"""

from __future__ import annotations

import argparse
import dataclasses
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from reverie import ast, verify  # noqa: E402
from reverie.cli import load_module  # noqa: E402
from reverie.diagnostics import ReverieError  # noqa: E402
from reverie.printer import print_expr  # noqa: E402

CORPUS = [
    os.path.join(folder, name)
    for folder in ("stdlib", "examples")
    for name in sorted(os.listdir(os.path.join(ROOT, folder)))
    if name.endswith(".rev")
]

#: comparisons that are one step apart: the classic off-by-one in a predicate
FLIP = {
    "<": "<=", "<=": "<", ">": ">=", ">=": ">",
    "==": "!=", "!=": "==", "&&": "||", "||": "&&",
}


# ---------------------------------------------------------------------------
# walking
# ---------------------------------------------------------------------------


def walk(node):
    """Every AST node reachable from ``node``, including itself."""
    if isinstance(node, list):
        for item in node:
            yield from walk(item)
        return
    if not dataclasses.is_dataclass(node):
        return
    yield node
    for f in dataclasses.fields(node):
        yield from walk(getattr(node, f.name))


def predicates(proc):
    """(expression, description, load-bearing) for every test the machine asserts.

    ``} fi;`` with no expression means "the same test as on the way in", and
    the parser really does hand back the same object for both.  Flipping it
    therefore flips both ends at once, which leaves the two branches just as
    distinguishable as they were: the mutant computes something different and
    is still perfectly reversible.  That is a fact about the property, not a
    hole in the search, so it is labelled as one.
    """
    for node in walk(proc):
        if isinstance(node, ast.If):
            names = ("the entry test of an `if`", "the exit test of an `if`")
        elif isinstance(node, ast.Loop):
            names = ("a loop's entry test", "a loop's exit test")
        else:
            continue
        if node.entry is node.exit:
            yield node.entry, f"{names[0]}, which is also its exit test", False
            continue
        yield node.entry, names[0], True
        yield node.exit, names[1], True


# ---------------------------------------------------------------------------
# mutations
# ---------------------------------------------------------------------------


class Mutation:
    """One deliberate change, with a way to put it back."""

    def __init__(self, proc, kind, describe, apply, undo, load_bearing=True):
        self.proc = proc
        self.kind = kind
        self.describe = describe
        self.apply = apply
        self.undo = undo
        #: False for changes reversibility is not entitled to notice
        self.load_bearing = load_bearing


def flip_comparisons(proc):
    """Move a boundary in a test the machine checks."""
    for expr, where, load_bearing in predicates(proc):
        for node in walk(expr):
            if isinstance(node, ast.BinOp) and node.op in FLIP:
                yield _swap_op(proc, node, where, load_bearing)


def _swap_op(proc, node, where, load_bearing):
    was, now = node.op, FLIP[node.op]
    before = print_expr(node)

    def apply():
        node.op = now

    def undo():
        node.op = was

    return Mutation(proc, "predicate", f"{where}: `{before}` becomes `{now}`",
                    apply, undo, load_bearing)


def nudge_delocals(proc):
    """Claim a released cell holds one more than it does."""
    for node in walk(proc):
        if isinstance(node, ast.LocalDecl) and node.release and node.expr:
            yield _nudge(proc, node, f"`delocal {node.name}`", "delocal")


def nudge_loop_steps(proc):
    """Take a different-sized step, so the loop lands somewhere else."""
    for loop in walk(proc):
        if not isinstance(loop, ast.Loop) or loop.step is None:
            continue
        for node in walk(loop.step):
            if isinstance(node, ast.Update) and node.expr is not None:
                yield _nudge(proc, node, "a loop's step", "step")


def _nudge(proc, node, where, kind):
    was = node.expr
    more = ast.BinOp(was.span, "+", was, ast.Num(was.span, 1))
    before = print_expr(was)

    def apply():
        node.expr = more

    def undo():
        node.expr = was

    return Mutation(proc, kind, f"{where}: `{before}` becomes `{before} + 1`",
                    apply, undo)


def swap_accumulations(proc):
    """Compute something else, reversibly.  The control group."""
    inside = {id(n) for loop in walk(proc) if isinstance(loop, ast.Loop)
              and loop.step is not None for n in walk(loop.step)}
    for node in walk(proc):
        if not isinstance(node, ast.Update) or id(node) in inside:
            continue
        if node.op not in ("+", "-"):
            continue
        was, now = node.op, "-" if node.op == "+" else "+"
        target = print_expr(node.target)

        def apply(node=node, now=now):
            node.op = now

        def undo(node=node, was=was):
            node.op = was

        yield Mutation(
            proc, "accumulation",
            f"`{target} {was}=` becomes `{target} {now}=`",
            apply, undo, load_bearing=False,
        )


OPERATORS = [flip_comparisons, nudge_delocals, nudge_loop_steps, swap_accumulations]


def mutations(module):
    for proc in module.procs():
        if proc.name.startswith("__"):
            continue
        for operator in OPERATORS:
            yield from operator(proc)


# ---------------------------------------------------------------------------
# the oracle
# ---------------------------------------------------------------------------


def search(module, proc_name, opts):
    try:
        return verify.verify_module(
            module, [proc_name], cases=opts.cases, length=opts.len,
            max_steps=opts.max_steps,
        )[0]
    except ReverieError as err:
        return verify.Report(proc_name, proc_name, skipped=err.message)


def outcome(module, proc_name, opts, baseline) -> tuple:
    """What the checker and the search make of the module as it now stands.

    The search builds and checks its own driver, so a mutant the checker
    refuses comes back as a skipped report rather than as an exception.  That
    still counts as noticed -- by a different part of the language.

    Running past the step limit only counts if the unmutated procedure did
    not: some programs are simply long, and saying a mutant "hung" because
    the original does too would be a lie.
    """
    r = search(module, proc_name, opts)
    if r.skipped:
        return "rejected", r.skipped
    if r.failure is not None:
        return "caught", r.failure.describe()
    if r.gave_up > baseline:
        return "hung", f"{r.gave_up} cases ran past the step limit"
    return "survived", ""


def run(path: str, opts) -> dict:
    module = load_module(os.path.join(ROOT, path))
    tally = {"rejected": 0, "caught": 0, "hung": 0, "survived": 0,
             "expected": 0, "unusable": 0}
    survivors = []
    baseline: dict = {}
    for mut in mutations(module):
        name = mut.proc.name
        if name not in baseline:
            clean = search(module, name, opts)
            if clean.failure is not None or clean.skipped:
                baseline[name] = None
            else:
                baseline[name] = clean.gave_up
        if baseline[name] is None:
            tally["unusable"] += 1
            continue  # cannot tell a mutant from the original here
        mut.apply()
        try:
            verdict, why = outcome(module, name, opts, baseline[name])
        finally:
            mut.undo()
        if not mut.load_bearing:
            tally["expected"] += 1
            if verdict == "survived":
                continue
            verdict = "caught"  # noticed anyway; no complaint about that
        tally[verdict] += 1
        if verdict == "survived":
            survivors.append(mut)
        if not opts.quiet:
            print(f"  {name:<16} {mut.describe[:56]:<58} {verdict}")
    return {"tally": tally, "survivors": survivors}


def main() -> int:
    ap = argparse.ArgumentParser(
        description="mutate the shipped programs and see whether `rev verify` notices"
    )
    ap.add_argument("--only", default="", help="one file, by name")
    ap.add_argument("--cases", type=int, default=25)
    ap.add_argument("--len", type=int, default=8)
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--max-steps", type=int, default=200_000)
    ap.add_argument("--min", type=float, default=0.0,
                    help="fail below this percentage of load-bearing mutants caught")
    args = ap.parse_args()

    paths = [p for p in CORPUS if not args.only or args.only in p]
    total = {"rejected": 0, "caught": 0, "hung": 0, "survived": 0,
             "expected": 0, "unusable": 0}
    all_survivors = []
    started = time.time()
    for path in paths:
        if not args.quiet:
            print(path)
        result = run(path, args)
        for k, v in result["tally"].items():
            total[k] += v
        all_survivors += [(path, m) for m in result["survivors"]]

    noticed = total["rejected"] + total["caught"] + total["hung"]
    seen = noticed + total["survived"]
    score = 100.0 * noticed / seen if seen else 100.0
    print("-" * 78)
    print(
        f"{seen} mutations that break reversibility: "
        f"{total['caught']} caught by the search, "
        f"{total['rejected']} rejected by the checker, "
        f"{total['hung']} left running, "
        f"{total['survived']} survived — {score:.1f}% in {time.time() - started:.1f}s"
    )
    print(
        f"{total['expected']} more changed what a procedure computes without "
        f"breaking its reversibility.\nThe search is not entitled to notice "
        f"those: an accumulation that subtracts instead of adding, or a test\n"
        f"flipped at both ends at once, is a different procedure and just as "
        f"invertible."
    )
    if total["unusable"]:
        print(
            f"{total['unusable']} mutations were skipped: the unmutated "
            f"procedure does not pass cleanly here,\nso nothing could be "
            f"concluded from breaking it."
        )
    for path, mut in all_survivors:
        print(f"  SURVIVED  {path}  {mut.proc.name}: {mut.describe}")
    if args.min and score < args.min:
        print(f"\nscore {score:.1f}% is below the required {args.min:.1f}%")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
