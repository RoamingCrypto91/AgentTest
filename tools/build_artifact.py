"""Build the Reverie showcase page.

Everything the page shows is produced by running the real tools: the traces
come from `rev trace`, the diagnostics from the checker, the inverted source
from `rev invert`, the cost readout from `rev doctor`.  Nothing is transcribed
by hand, so the page cannot drift away from the thing it describes.

    python3 tools/build_artifact.py -o build/reverie.html
"""

from __future__ import annotations

import argparse
import html
import io
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from reverie.checker import analyze  # noqa: E402
from reverie.cli import load_module  # noqa: E402
from reverie.compiler import Compiler  # noqa: E402
from reverie.inverter import invert_source  # noqa: E402
from reverie.parser import parse_text  # noqa: E402
from reverie.trace import record_run, to_payload  # noqa: E402
from reverie.vm import Machine  # noqa: E402

DEMOS = [
    (
        "square root",
        "examples/tour.rev",
        {"n": 1000},
        "Ordinary destructive code — a `while` loop and a division — compiled "
        "into Bennett’s compute–copy–uncompute. Watch the history tape climb, "
        "hold while the answer is copied out, and drain back to nothing.",
    ),
    (
        "run-length coding",
        "examples/rle.rev",
        {"data": [1, 1, 1, 4, 4, 7, 7, 7, 2, 2, 9, 9, 9, 3, 3, 3]},
        "The encoder moves information rather than copying it: `data` drains to "
        "zero as `out` fills. That is what makes it invertible — drag left and "
        "you are watching the decoder, which nobody wrote.",
    ),
    (
        "sorting",
        "examples/sorting.rev",
        {"xs": [5, 3, 9, 1, 7, 2, 8, 4]},
        "Sorting is not injective, so the original order has to be kept "
        "somewhere. It goes on `trail`, one bit per comparison — the "
        "permutation, written down.",
    ),
]


def compile_file(path: str):
    module = load_module(os.path.join(ROOT, path))
    a = analyze(module)
    a.diagnostics.raise_if_errors()
    return Compiler(module, a).compile()


def trace_for(path: str, initial: dict) -> dict:
    prog = compile_file(path)
    machine, rec = record_run(prog, initial, max_steps=30000, mem_size=8192)
    with open(os.path.join(ROOT, path)) as fh:
        text = fh.read()
    payload = to_payload(prog, machine, rec, text, initial)
    payload["initial"] = {k: v for k, v in initial.items()}
    return payload


def run_cli(argv: list[str]) -> str:
    from reverie.cli import main

    buf = io.StringIO()
    old = sys.stdout, sys.stderr
    sys.stdout = sys.stderr = buf
    cwd = os.getcwd()
    os.chdir(ROOT)
    try:
        main(argv)
    except SystemExit:
        pass
    finally:
        sys.stdout, sys.stderr = old
        os.chdir(cwd)
    return buf.getvalue().rstrip()


def read(path: str) -> str:
    with open(os.path.join(ROOT, path)) as fh:
        return fh.read()


def dedent(text: str) -> str:
    lines = text.splitlines()
    pad = min((len(l) - len(l.lstrip()) for l in lines if l.strip()), default=0)
    return "\n".join(l[pad:] if l.strip() else "" for l in lines)


def excerpt(path: str, start: str, end: str | None = None) -> str:
    text = read(path)
    i = text.index(start)
    j = text.index(end, i) if end else len(text)
    return text[i:j].rstrip()


def collect() -> dict:
    bad = """int total;

proc main() {
    total += total;
}
"""
    bad_analysis = analyze(parse_text(bad, "bad.rev"))
    countdown = read("examples/countdown.rev")
    data = {
        "demos": [
            {
                "label": label,
                "path": path,
                "blurb": blurb,
                "trace": trace_for(path, initial),
            }
            for label, path, initial, blurb in DEMOS
        ],
        "diagnostic": {
            "source": bad,
            "message": bad_analysis.diagnostics.render(),
        },
        "inversion": {
            "forward": countdown,
            "inverse": invert_source(countdown, "countdown.rev"),
        },
        "doctor": run_cli(
            ["doctor", "examples/embedding.rev", "--set", "n=1234567"]
        ),
        "fibonacci": run_cli(["run", "examples/fibonacci.rev", "--set", "n=10"]),
        # a real compare-and-swap, where the exit predicate genuinely differs
        # between the branches
        "conditional": dedent(
            excerpt("stdlib/sort.rev", "            local int swapped", "        } loop {")
        ),
    }
    return data


# ---------------------------------------------------------------------------

PAGE = read("tools/artifact.html") if os.path.exists(
    os.path.join(ROOT, "tools", "artifact.html")
) else ""


def build(out_path: str) -> str:
    data = collect()
    template = read("tools/artifact.html")
    payload = json.dumps(data, separators=(",", ":")).replace("</", "<\\/")
    page = template.replace("__DATA__", payload)
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w") as fh:
        fh.write(page)
    return page


def main() -> int:
    ap = argparse.ArgumentParser(description="build the Reverie showcase page")
    ap.add_argument("-o", "--output", default=os.path.join(ROOT, "build", "reverie.html"))
    args = ap.parse_args()
    page = build(args.output)
    print(f"wrote {args.output} ({len(page):,} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
