"""Execution recording and JSON export."""

import json

from framework import case, contains, eq, is_true
from reverie.compiler import compile_text
from reverie.trace import Recorder, record_run, to_json

SRC = """int x;
int y;
proc main() {
    from x == 0 loop { x += 1; } until x == 4;
    y += x * 3;
    print "y = ", y;
}
"""


def build():
    return compile_text(SRC, "t.rev")


def test_recording_covers_every_step():
    prog = build()
    m, rec = record_run(prog, {})
    eq(len(rec.frames), m.position)
    eq(rec.truncated, False)
    eq(rec.frames[0].pc, 0)
    eq(rec.frames[0].dir, 1)


def test_frames_carry_source_lines():
    prog = build()
    m, rec = record_run(prog, {})
    lines = {f.line for f in rec.frames}
    is_true(4 in lines and 5 in lines, f"lines seen: {sorted(lines)}")


def test_memory_deltas_are_recorded_with_both_values():
    prog = build()
    m, rec = record_run(prog, {})
    changes = [f.changed for f in rec.frames if f.changed]
    eq(len(changes), 5)  # four increments of x, one update of y
    eq(changes[0], {0: (0, 1)})
    eq(changes[-1], {1: (0, 12)})


def test_depth_and_output_counters():
    prog = build()
    m, rec = record_run(prog, {})
    is_true(max(f.depth for f in rec.frames) >= 1)
    emits = [f.emitted for f in rec.frames if f.emitted]
    eq(emits, [("y = 12", True)])
    eq(m.output, ["y = 12"])


def test_truncation_is_reported():
    prog = build()
    m, rec = record_run(prog, {}, max_steps=5)
    eq(len(rec.frames), 5)
    eq(rec.truncated, True)


def test_backward_recording():
    """Recording a reversal runs forwards first, then records the walk back."""
    prog = build()
    m, rec = record_run(prog, {}, backward=True)
    eq(rec.frames[0].dir, -1)
    eq(m.globals_dict(), {"x": 0, "y": 0})
    eq(m.output, [])
    eq(m.position, 0)


def test_stack_operations_are_recorded():
    prog = compile_text(
        "stack s; proc main() { local int t = 3; push(t, s); delocal int t = 0;\n"
        "  local int u = 0; pop(u, s); push(u, s); delocal int u = 0; }",
        "s.rev",
    )
    m, rec = record_run(prog, {})
    ops = [f.stack_op for f in rec.frames if f.stack_op]
    eq(ops, [(0, "push", 3), (0, "pop", 3), (0, "push", 3)])


def test_write_records_partial_lines():
    prog = compile_text(
        'int x; proc main() { x += 1; write "a"; write x; print "!"; }', "w.rev"
    )
    m, rec = record_run(prog, {})
    eq([f.emitted for f in rec.frames if f.emitted],
       [("a", False), ("1", False), ("!", True)])


def test_json_shape():
    prog = build()
    m, rec = record_run(prog, {})
    data = json.loads(to_json(prog, m, rec, SRC))
    eq(data["source"], SRC)
    eq(data["final"]["y"], 12)
    eq(data["output"], ["y = 12"])
    eq(data["stats"]["bits_erased"], 0)
    eq([g["name"] for g in data["globals"]], ["x", "y"])
    eq(len(data["code"]), len(prog.code))
    eq(len(data["frames"]), len(rec.frames))
    eq(data["start_mem"], [0, 0])
    names = [p["name"] for p in data["procs"]]
    contains(names, "main")


def test_json_is_compact():
    prog = build()
    m, rec = record_run(prog, {})
    payload = to_json(prog, m, rec, None)
    is_true(len(payload) < 40000, f"{len(payload)} bytes for {len(rec.frames)} frames")


def test_embed_traces_show_the_tape():
    prog = compile_text(
        "int n; int r; proc main() { embed (r ^= v) { var v = 0;\n"
        "  while (v < 8) { v = v + 1; } } }",
        "e.rev",
    )
    m, rec = record_run(prog, {"n": 3})
    peak = max(f.tape for f in rec.frames)
    is_true(peak > 8, f"tape peaked at {peak}")
    eq(rec.frames[-1].tape, 0)
