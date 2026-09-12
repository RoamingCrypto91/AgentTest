"""``vitals`` -- run an audit and get something you can send to a client.

    vitals audit export.json -o report.html      audit a real export
    vitals audit export/ --staff "Dan" --window 90
    vitals show export.json                      the numbers, in the terminal
    vitals demo dying -o demo.html               a report from a simulated community
    vitals template -o events.csv                the shape a CSV needs to be
    vitals playbook -o PLAYBOOK.md               every intervention, as a document
    vitals benchmark audits/*.json -o bands.json turn past audits into benchmarks
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Optional

from . import adapters, audit, bench, report, simulate
from .model import FAILING, HOLDING, STRAINED

TEMPLATE = """timestamp,actor,surface,kind,id,parent_id,addressed,staff,words
2026-01-02T11:00:00Z,dana,,join,,,,1,
2026-01-02T11:30:00Z,sam,,join,,,,0,
2026-01-04T09:12:00Z,sam,general,message,m1,,,0,14
2026-01-04T09:20:00Z,dana,general,message,m2,m1,sam,1,8
2026-01-11T18:02:00Z,sam,general,message,m3,,,0,21
"""

COLOURS = {HOLDING: "32", STRAINED: "33", FAILING: "31"}


def _paint(text: str, code: str, stream) -> str:
    if not code or not hasattr(stream, "isatty") or not stream.isatty():
        return text
    if "NO_COLOR" in os.environ:
        return text
    return f"\033[{code}m{text}\033[0m"


def _load(args):
    return adapters.load(
        args.file, fmt=getattr(args, "format", "") or "",
        staff=tuple(getattr(args, "staff", None) or ()),
        name=getattr(args, "name", "") or "",
        surfaces_file=getattr(args, "surfaces", "") or "",
    )


def cmd_audit(args, out=None) -> int:
    out = out or sys.stdout
    community = _load(args)
    result = audit.run(community, window_days=args.window,
                       benchmark_file=args.benchmarks or "")
    if args.json:
        target = args.json if args.json != "-" else None
        blob = json.dumps(audit.to_dict(result), indent=2)
        if target:
            with open(target, "w") as fh:
                fh.write(blob)
        else:
            print(blob, file=out)
    path = args.output or _default_name(community.name)
    html = report.render(result, title=args.title or "")
    with open(path, "w") as fh:
        fh.write(html)
    print(f"{community.describe()}", file=out)
    print(f"  {result.headline}", file=out)
    print(f"  report written to {path}", file=out)
    return 0


def cmd_show(args, out=None) -> int:
    out = out or sys.stdout
    community = _load(args)
    result = audit.run(community, window_days=args.window,
                       benchmark_file=args.benchmarks or "")
    print(community.describe(), file=out)
    print(file=out)
    gates = result.assessment["gates"] + [result.assessment["structure"]]
    for gate in gates:
        mark = "<<" if result.binding is not None and gate is result.binding else ""
        head = f"{gate.gate.name:<14} {gate.verdict:<9} {mark}"
        print(_paint(head, COLOURS.get(gate.verdict, ""), out), file=out)
        for finding in gate.findings + gate.supporting:
            m = finding.measure
            value = report.fmt(m) if m.measured else "n/a"
            band = ""
            if finding.band and finding.band.typical is not None:
                shim = type(m)(m.key, m.label, finding.band.typical, m.unit)
                shim.good = m.good
                band = f"  (typical {report.fmt(shim)})"
            line = f"    {m.label:<46} {value:>8}{band}"
            print(_paint(line, COLOURS.get(finding.verdict, ""), out), file=out)
        print(file=out)
    print(f"{result.headline}", file=out)
    for i, action in enumerate(result.plan["plan"], start=1):
        print(f"  {i}. {action.title}", file=out)
    return 0


def cmd_demo(args, out=None) -> int:
    out = out or sys.stdout
    community = simulate.archetype(args.archetype)
    result = audit.run(community)
    path = args.output or f"vitals-{args.archetype}.html"
    with open(path, "w") as fh:
        fh.write(report.render(result, title=args.title or
                               f"{args.archetype.title()} community vitals"))
    print(f"{community.describe()}", file=out)
    print(f"  {result.headline}", file=out)
    print(f"  report written to {path}", file=out)
    return 0


def cmd_template(args, out=None) -> int:
    out = out or sys.stdout
    path = args.output or "events.csv"
    with open(path, "w") as fh:
        fh.write(TEMPLATE)
    print(f"wrote {path}", file=out)
    print("columns: timestamp and actor are required; `join` rows supply the "
          "roster, which is what makes activation measurable", file=out)
    return 0


def cmd_playbook(args, out=None) -> int:
    """Write the intervention library out as Markdown.

    Generated rather than kept as prose, so the document a client is handed
    and the library the instrument prescribes from can never drift apart.
    """
    out = out or sys.stdout
    from . import model, prescribe

    lines = [
        "# The playbook",
        "",
        "Every intervention the instrument can prescribe, grouped by the gate "
        "it unblocks. Generated from `vitals/prescribe.py`, so this document "
        "and the tool always say the same thing.",
        "",
    ]
    gates = {g.key: g for g in model.GATES}
    gates[model.STRUCTURE.key] = model.STRUCTURE
    for key, gate in gates.items():
        actions = prescribe.BY_GATE.get(key, [])
        if not actions:
            continue
        lines += [f"## {gate.name}", "", f"*{gate.question}*", "",
                  gate.mechanism, ""]
        for action in actions:
            lines += [
                f"### {action.title}", "",
                action.do, "",
                f"**Why it works.** {action.mechanism}", "",
                f"- Should move: {', '.join(action.moves)}",
                f"- Re-measure after: {action.review_days} days",
                f"- Effort: {action.effort}",
                "",
            ]
    text = "\n".join(lines)
    path = args.output or "PLAYBOOK.md"
    with open(path, "w") as fh:
        fh.write(text)
    print(f"wrote {path} ({len(prescribe.LIBRARY)} interventions)", file=out)
    return 0


def cmd_benchmark(args, out=None) -> int:
    out = out or sys.stdout
    built = bench.write(args.files, args.output or "benchmarks.json",
                        minimum=args.minimum)
    print(f"wrote {args.output or 'benchmarks.json'} with bands for "
          f"{len(built)} measures", file=out)
    print("pass it to a later audit with --benchmarks", file=out)
    return 0


def _default_name(name: str) -> str:
    safe = "".join(c if c.isalnum() or c in "-_" else "-" for c in (name or "community"))
    return f"vitals-{safe.strip('-').lower() or 'community'}.html"


def make_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="vitals",
        description="Tell a living community from a dead one, on any platform.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = ap.add_subparsers(dest="cmd")

    def source(p):
        p.add_argument("file", help="an export: a file or a directory")
        p.add_argument("--format", choices=sorted(adapters.ADAPTERS),
                       help="override the guessed format")
        p.add_argument("--staff", action="append", default=[],
                       help="a name or id that runs the place (repeatable)")
        p.add_argument("--name", default="", help="what to call the community")
        p.add_argument("--surfaces", default="",
                       help="a text file of room names, one per line, so that "
                            "silent rooms stay visible")
        p.add_argument("--window", type=int, default=0,
                       help="audit only the last N days")
        p.add_argument("--benchmarks", default="",
                       help="a measured benchmark file from `vitals benchmark`")
        return p

    p = source(sub.add_parser("audit", help="audit an export and write a report"))
    p.add_argument("-o", "--output", default="", help="where to write the HTML")
    p.add_argument("--json", default="", help="also write the audit as JSON "
                                             "(use - for stdout)")
    p.add_argument("--title", default="")
    p.set_defaults(func=cmd_audit)

    p = source(sub.add_parser("show", help="print the numbers to the terminal"))
    p.set_defaults(func=cmd_show)

    p = sub.add_parser("demo", help="a report from a simulated community")
    p.add_argument("archetype", nargs="?", default="broadcast",
                   choices=sorted(simulate.ARCHETYPES))
    p.add_argument("-o", "--output", default="")
    p.add_argument("--title", default="")
    p.set_defaults(func=cmd_demo)

    p = sub.add_parser("template", help="write a CSV template")
    p.add_argument("-o", "--output", default="")
    p.set_defaults(func=cmd_template)

    p = sub.add_parser("playbook", help="write the intervention library as Markdown")
    p.add_argument("-o", "--output", default="")
    p.set_defaults(func=cmd_playbook)

    p = sub.add_parser("benchmark", help="build benchmarks from stored audits")
    p.add_argument("files", nargs="+", help="audit JSON files")
    p.add_argument("-o", "--output", default="")
    p.add_argument("--minimum", type=int, default=bench.MINIMUM_AUDITS,
                   help="how many audits a measure needs before it gets a band")
    p.set_defaults(func=cmd_benchmark)
    return ap


def main(argv: Optional[list] = None, out=None) -> int:
    out = out or sys.stdout
    args = make_parser().parse_args(argv)
    if not getattr(args, "cmd", None):
        make_parser().print_help(out)
        return 1
    try:
        return args.func(args, out=out)
    except (ValueError, KeyError, OSError) as exc:
        print(f"vitals: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
