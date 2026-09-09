"""``rev`` -- the Reverie command line.

    rev run      prog.rev  [--set x=5]      run a program
    rev run      prog.rev  --paranoid        ... checking every step as it runs
    rev back     prog.rev                   run it, then run it backwards
    rev check    prog.rev                   type/reversibility check only
    rev fmt      prog.rev  [-i]             format source
    rev invert   prog.rev  [--proc main]    print the program that undoes it
    rev disasm   prog.rev                   show the bytecode
    rev debug    prog.rev                   interactive time-travel debugger
    rev trace    prog.rev  [-o trace.json]  record an execution
    rev viz      prog.rev  [-o out.html]    build a scrubbable visualiser
    rev doctor   prog.rev                   report on reversibility and cost
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Optional

from .checker import analyze
from .compiler import Compiler
from .diagnostics import ReverieError, RuntimeFault, Source
from .inverter import invert_module
from .parser import parse
from .printer import print_module
from .vm import Machine, Program

PRELUDE_ENV = "REVERIE_PATH"


def _color(stream=None) -> bool:
    stream = stream or sys.stdout
    return hasattr(stream, "isatty") and stream.isatty() and "NO_COLOR" not in os.environ


def read_source(path: str) -> Source:
    if path == "-":
        return Source("<stdin>", sys.stdin.read())
    with open(path) as fh:
        return Source(path, fh.read())


def resolve_import(path: str, importer: str) -> str:
    """Find an imported module among the search roots.

    An import names a module, not an arbitrary file: the resolved path has to
    end in ``.rev`` and live inside one of the roots.  Otherwise a program
    could reach out of its own directory, and a parse error would quote lines
    of whatever it found.
    """
    if os.path.isabs(path):
        raise ReverieError(
            f"import paths are relative to the search roots, not absolute: {path!r}"
        )
    roots = [os.path.dirname(os.path.abspath(importer)), os.getcwd()]
    here = os.path.dirname(os.path.abspath(__file__))
    roots.append(os.path.join(os.path.dirname(here), "stdlib"))
    roots.extend(p for p in os.environ.get(PRELUDE_ENV, "").split(os.pathsep) if p)
    here_real = os.path.abspath(importer)
    for root in roots:
        real_root = os.path.abspath(root)
        for candidate in (
            os.path.join(root, path),
            os.path.join(root, path + ".rev") if not path.endswith(".rev") else None,
        ):
            if candidate is None or not os.path.exists(candidate):
                continue
            resolved = os.path.abspath(candidate)
            if not resolved.endswith(".rev"):
                continue
            if os.path.commonpath([resolved, real_root]) != real_root:
                raise ReverieError(
                    f"the module {path!r} resolves outside the search roots",
                    notes=[f"it points at {resolved}"],
                )
            if resolved == here_real:
                continue  # a module never imports itself; keep looking
            return candidate
    raise ReverieError(
        f"cannot find module {path!r}",
        notes=["searched: " + ", ".join(roots)],
    )


def load_module(path: str, _seen: Optional[set] = None):
    """Parse *path*, splicing in any ``import`` declarations."""
    from . import ast

    seen = _seen if _seen is not None else set()
    real = os.path.abspath(path) if path != "-" else "-"
    if real in seen:
        return ast.Module([], path)
    seen.add(real)
    src = read_source(path)
    module = parse(src)
    decls: list = []
    for d in module.decls:
        if isinstance(d, ast.Import):
            target = resolve_import(d.path, path)
            imported = load_module(target, seen)
            decls.extend(imported.decls)
        else:
            decls.append(d)
    return ast.Module(decls, module.source_name)


def build(path: str, entry: str = "main") -> tuple[Program, Source]:
    module = load_module(path)
    a = analyze(module)
    if not a.ok:
        raise SystemExit(_report(a))
    src = read_source(path)
    return Compiler(module, a).compile(entry), src


def _report(analysis) -> str:
    n = len(analysis.diagnostics.errors)
    body = analysis.diagnostics.render(_color(sys.stderr))
    return f"{body}\n\n{n} error{'s' if n != 1 else ''}"


def parse_assignments(pairs: list[str]) -> dict:
    out: dict = {}
    for pair in pairs or []:
        if "=" not in pair:
            raise SystemExit(f"--set expects name=value, got {pair!r}")
        name, _, value = pair.partition("=")
        value = value.strip()
        if "," in value or value.startswith("["):
            items = value.strip("[]").split(",")
            out[name.strip()] = [int(x.strip(), 0) for x in items if x.strip()]
        else:
            out[name.strip()] = int(value, 0)
    return out


def show_globals(m: Machine, stream=None) -> None:
    stream = stream or sys.stdout
    g = m.globals_dict()
    if not g:
        return
    width = max(len(k) for k in g)
    for k, v in g.items():
        print(f"  {k.ljust(width)} = {v}", file=stream)


# ---------------------------------------------------------------------------
# commands
# ---------------------------------------------------------------------------


def cmd_run(args) -> int:
    prog, src = build(args.file, args.entry)
    m = Machine(prog, mem_size=args.mem, max_steps=args.max_steps,
                paranoid=getattr(args, "paranoid", False))
    m.set_globals(parse_assignments(args.set))
    if args.backward:
        m.start_backward()
    else:
        m.start_forward()
    m.run()
    for line in m.output:
        print(line)
    if m.line:
        print(m.line)
    if not args.quiet:
        if m.output:
            print()
        show_globals(m)
    if args.stats:
        print()
        print("  " + m.stats.merge_report())
        print(f"  bits erased: {m.bits_erased}")
    if args.check_clean:
        m.check_clean()
    return 0


def cmd_back(args) -> int:
    """Run forwards, then run backwards, and prove you got the start back."""
    prog, src = build(args.file, args.entry)
    m = Machine(prog, mem_size=args.mem, max_steps=args.max_steps,
                paranoid=getattr(args, "paranoid", False))
    m.set_globals(parse_assignments(args.set))
    before = m.snapshot()
    m.start_forward().run()
    print("forward:")
    for line in m.output:
        print("  | " + line)
    show_globals(m)
    steps = m.position
    m.start_backward().run()
    from .vm import state_equal

    ok, why = state_equal(before, m.snapshot())
    print("\nbackward:")
    show_globals(m)
    print()
    if ok:
        print(f"  {steps} steps forward, {steps} steps back, state identical.")
        return 0
    print(f"  MISMATCH after reversal: {why}")
    return 1


def cmd_check(args) -> int:
    module = load_module(args.file)
    is_library = module.find_proc(args.entry) is None
    a = analyze(module, require_main=not is_library)
    if not a.ok:
        print(_report(a), file=sys.stderr)
        return 1
    if is_library:
        names = [p.name for p in module.procs()]
        print(f"{args.file}: ok (library, no `{args.entry}`)")
        for name in names:
            info = a.procs[name]
            params = ", ".join(
                ("stack " + p.name) if p.type == "stack"
                else (f"int {p.name}[{'' if p.length < 0 else p.length}]")
                if p.type == "array" else f"int {p.name}"
                for p in info.params
            )
            ro = sorted(set(range(len(info.params))) - info.writes)
            note = ""
            if ro and info.params:
                note = "  read-only: " + ", ".join(info.params[i].name for i in ro)
            print(f"    {name}({params}){note}")
        return 0
    prog = Compiler(module, a).compile(args.entry)
    print(f"{args.file}: ok")
    print(f"  {len(prog.procs)} procedures, {len(prog.code)} instructions, "
          f"{prog.n_globals} global cells")
    for name, info in prog.procs.items():
        if name.startswith("__embed"):
            continue
        params = ", ".join(
            f"{k} {n}" for n, k in zip(info.params, info.param_kinds)
        )
        print(f"    {name}({params})  frame={info.frame_size}")
    return 0


def cmd_fmt(args) -> int:
    src = read_source(args.file)
    text = print_module(parse(src))
    if args.in_place and args.file != "-":
        with open(args.file, "w") as fh:
            fh.write(text)
        print(f"formatted {args.file}")
    else:
        sys.stdout.write(text)
    return 0


def cmd_invert(args) -> int:
    src = read_source(args.file)
    module = parse(src)
    inverted = invert_module(
        module, tuple(args.proc), keep_original=args.keep, suffix=args.suffix
    )
    text = print_module(inverted)
    if args.output:
        with open(args.output, "w") as fh:
            fh.write(text)
        print(f"wrote {args.output}")
    else:
        sys.stdout.write(text)
    return 0


def cmd_disasm(args) -> int:
    prog, _ = build(args.file, args.entry)
    if prog.globals:
        print("globals:")
        print(prog.globals_layout())
        print()
    print(prog.disassemble())
    return 0


def cmd_debug(args) -> int:
    from .debugger import Debugger

    prog, src = build(args.file, args.entry)
    dbg = Debugger(
        prog,
        src,
        parse_assignments(args.set),
        mem_size=args.mem,
        color=_color(),
    )
    if args.command:
        for line in args.command:
            dbg.execute(line)
        return 0
    dbg.repl()
    return 0


def cmd_trace(args) -> int:
    from .trace import record_run, to_json

    prog, src = build(args.file, args.entry)
    m, rec = record_run(
        prog,
        parse_assignments(args.set),
        max_steps=args.limit,
        mem_size=args.mem,
        backward=args.backward,
    )
    payload = to_json(prog, m, rec, src.text)
    if args.output:
        with open(args.output, "w") as fh:
            fh.write(payload)
        print(f"wrote {args.output} ({len(rec.frames)} frames, {len(payload)} bytes)")
    else:
        print(payload)
    return 0


def cmd_viz(args) -> int:
    from .viz import build_page

    prog, src = build(args.file, args.entry)
    html = build_page(
        prog, src, parse_assignments(args.set), limit=args.limit, mem_size=args.mem
    )
    out = args.output or os.path.splitext(args.file)[0] + ".html"
    with open(out, "w") as fh:
        fh.write(html)
    print(f"wrote {out} ({len(html)} bytes)")
    return 0


def cmd_doctor(args) -> int:
    """Report on what a program costs, thermodynamically speaking."""
    prog, src = build(args.file, args.entry)
    m = Machine(prog, mem_size=args.mem, max_steps=args.max_steps,
                paranoid=getattr(args, "paranoid", False))
    m.set_globals(parse_assignments(args.set))
    before = m.snapshot()
    m.start_forward().run()
    forward_out = list(m.output)
    steps = m.position
    m.start_backward().run()
    from .vm import state_equal

    ok, why = state_equal(before, m.snapshot())
    s = m.stats
    print(f"{args.file}")
    print(f"  instructions      {len(prog.code)}")
    print(f"  procedures        {len([p for p in prog.procs if not p.startswith('__embed')])}"
          f" (+{len([p for p in prog.procs if p.startswith('__embed')])} generated)")
    print(f"  logical time      {steps} steps")
    print(f"  work done         {s.steps} instruction executions")
    print(f"  calls             {s.calls}, max depth {s.max_depth}")
    print(f"  history tape      peak {s.history_peak} entries, "
          f"{s.history_bits} bits written")
    print(f"  tape reclaimed    {s.history_pops}/{s.history_pushes} entries")
    leftover = s.history_pushes - s.history_pops
    print(f"  bits erased       {m.bits_erased}"
          + ("" if leftover == 0 else f"  (WARNING: {leftover} tape entries left)"))
    print(f"  reversible        {'yes -- forward then backward is the identity' if ok else 'NO: ' + why}")
    if getattr(args, "paranoid", False):
        print(f"  every step        checked individually, and invertible")
    if forward_out:
        print(f"  output            {len(forward_out)} line(s)")
    return 0 if ok else 1


# ---------------------------------------------------------------------------
# argument parsing
# ---------------------------------------------------------------------------


def make_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="rev",
        description="Reverie -- a language whose programs run in both directions",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    ap.add_argument("--version", action="store_true", help="print the version and exit")
    sub = ap.add_subparsers(dest="cmd")

    def common(p, sets=True):
        p.add_argument("file", help="a .rev source file (or - for stdin)")
        p.add_argument("--entry", default="main", help="entry procedure")
        p.add_argument("--mem", type=int, default=1 << 16, help="machine memory cells")
        if sets:
            p.add_argument(
                "--set", action="append", default=[],
                metavar="NAME=VALUE",
                help="initialise a global (repeatable; arrays as 1,2,3)",
            )
        return p

    p = common(sub.add_parser("run", help="run a program"))
    p.add_argument("--backward", action="store_true", help="run it in reverse")
    p.add_argument("--stats", action="store_true")
    p.add_argument("--quiet", "-q", action="store_true", help="output only")
    p.add_argument("--check-clean", action="store_true",
                   help="assert the machine ends tidy")
    p.add_argument("--paranoid", action="store_true",
                   help="undo and redo every step as it happens, and stop if "
                        "anything differs")
    p.add_argument("--max-steps", type=int, default=50_000_000)
    p.set_defaults(func=cmd_run)

    p = common(sub.add_parser("back", help="run forwards then backwards"))
    p.add_argument("--paranoid", action="store_true",
                   help="check every individual step as well as the whole run")
    p.add_argument("--max-steps", type=int, default=50_000_000)
    p.set_defaults(func=cmd_back)

    p = common(sub.add_parser("check", help="check without running"), sets=False)
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("fmt", help="format source")
    p.add_argument("file")
    p.add_argument("-i", "--in-place", action="store_true")
    p.set_defaults(func=cmd_fmt)

    p = sub.add_parser("invert", help="print the program that undoes this one")
    p.add_argument("file")
    p.add_argument("--proc", action="append", default=None,
                   help="procedure to invert (default: main)")
    p.add_argument("--keep", action="store_true",
                   help="keep the original and add an inverted copy")
    p.add_argument("--suffix", default="_inv")
    p.add_argument("-o", "--output")
    p.set_defaults(func=cmd_invert)

    p = common(sub.add_parser("disasm", help="show bytecode"), sets=False)
    p.set_defaults(func=cmd_disasm)

    p = common(sub.add_parser("debug", help="interactive time-travel debugger"))
    p.add_argument("-c", "--command", action="append",
                   help="run a debugger command and exit (repeatable)")
    p.set_defaults(func=cmd_debug)

    p = common(sub.add_parser("trace", help="record an execution as JSON"))
    p.add_argument("-o", "--output")
    p.add_argument("--limit", type=int, default=20000)
    p.add_argument("--backward", action="store_true")
    p.set_defaults(func=cmd_trace)

    p = common(sub.add_parser("viz", help="build a scrubbable HTML visualiser"))
    p.add_argument("-o", "--output")
    p.add_argument("--limit", type=int, default=20000)
    p.set_defaults(func=cmd_viz)

    p = common(sub.add_parser("doctor", help="report reversibility and cost"))
    p.add_argument("--paranoid", action="store_true",
                   help="check every individual step as well as the whole run")
    p.add_argument("--max-steps", type=int, default=50_000_000)
    p.set_defaults(func=cmd_doctor)
    return ap


def main(argv: Optional[list[str]] = None) -> int:
    ap = make_parser()
    args = ap.parse_args(argv)
    if getattr(args, "version", False):
        from . import __version__

        print(f"reverie {__version__}")
        return 0
    if not getattr(args, "cmd", None):
        ap.print_help()
        return 1
    if getattr(args, "proc", None) is None and args.cmd == "invert":
        args.proc = ["main"]
    try:
        return args.func(args)
    except RuntimeFault as fault:
        print(f"\x1b[31;1mtrap\x1b[0m: {fault}" if _color(sys.stderr) else f"trap: {fault}",
              file=sys.stderr)
        for note in fault.notes:
            print(f"note: {note}", file=sys.stderr)
        return 2
    except ReverieError as err:
        print(err.render(_color(sys.stderr)), file=sys.stderr)
        return 1
    except FileNotFoundError as err:
        print(f"error: {err.strerror}: {err.filename}", file=sys.stderr)
        return 1
