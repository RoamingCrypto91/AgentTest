"""Property tests over randomly generated programs.

A reversible language admits a specification that needs no expected outputs:

    forward(P) then backward(P) is the identity on machine state.

Everything here is a variation on that theme, checked against programs from
``tests/generator.py`` that nobody wrote.
"""

import os

from framework import case, eq, is_true, slow, tag
from generator import Generator, generate
import random

from support import ROOT
from reverie.compiler import Compiler, compile_text
from reverie.checker import analyze
from reverie.inverter import invert_module
from reverie.parser import parse_text
from reverie.printer import print_module
from reverie.vm import Machine, state_equal

REPEAT = int(os.environ.get("REVERIE_FUZZ_REPEAT", "1"))
BASE = int(os.environ.get("REVERIE_SEED", "0")) & 0xFFFF


def seeds(n: int, offset: int = 0) -> list[int]:
    return [BASE + offset + i for i in range(n * REPEAT)]


def build(src: str, name: str = "fuzz.rev"):
    return compile_text(src, name)


def fresh(prog, snap=None, mem_size: int = 4096) -> Machine:
    m = Machine(prog, mem_size=mem_size, max_steps=2_000_000)
    if snap is not None:
        m.restore(snap)
    return m


# ---------------------------------------------------------------------------
# the core property
# ---------------------------------------------------------------------------


@case(seeds(120))
def test_forward_then_backward_is_the_identity(seed_list):
    for seed in seed_list:
        src = generate(seed)
        prog = build(src, f"gen{seed}.rev")
        m = fresh(prog)
        before = m.snapshot()
        m.start_forward().run()
        steps = m.position
        m.check_clean(f"seed {seed}")
        m.start_backward().run()
        ok, why = state_equal(before, m.snapshot())
        is_true(ok, f"seed {seed}: {why}\n\n{src}")
        eq(m.position, 0, f"seed {seed}: reversal length differs from {steps}")


@case(seeds(50, 3000))
def test_reversing_costs_the_same_as_running(seed_list):
    """Undoing a program executes exactly as many instructions as doing it.

    This is the structural form of the claim the benchmark measures in
    wall-clock terms: there is no log to write on the way out and none to read
    on the way back, so the two directions do the same amount of work.
    """
    for seed in seed_list:
        src = generate(seed)
        prog = build(src, f"gen{seed}.rev")
        m = fresh(prog)
        m.start_forward().run()
        # `forward_steps` counts *execution* direction, so an `uncall` inside a
        # forward run lands in the backward column.  Reversing the program
        # swaps the two columns exactly.
        ran, unran = m.stats.forward_steps, m.stats.backward_steps
        total = m.stats.steps
        m.start_backward().run()
        eq(m.stats.steps, 2 * total, f"seed {seed}: the reversal is a different length")
        eq(m.stats.forward_steps - ran, unran, f"seed {seed}: directions did not swap")
        eq(m.stats.backward_steps - unran, ran, f"seed {seed}: directions did not swap")


@case(seeds(60, 5000))
def test_source_inversion_undoes_the_program(seed_list):
    """`rev invert` produces a program that really is the inverse."""
    for seed in seed_list:
        src = generate(seed)
        prog = build(src, f"gen{seed}.rev")
        m = fresh(prog)
        before = m.snapshot()
        m.start_forward().run()
        after = m.snapshot()

        inverted = print_module(invert_module(parse_text(src), ("main",)))
        inv_prog = build(inverted, f"gen{seed}_inv.rev")
        m2 = fresh(inv_prog, after)
        m2.start_forward().run()
        ok, why = state_equal(before, m2.snapshot())
        is_true(ok, f"seed {seed}: {why}\n\n--- original ---\n{src}\n--- inverted ---\n{inverted}")


@case(seeds(60, 9000))
def test_inverting_twice_is_the_original(seed_list):
    """Inversion is an involution on the syntax.

    ``undo`` is the one exception and is excluded here: ``undo { S }`` inverts
    to plain ``S``, and inverting *that* gives the statement-by-statement
    inverse of ``S`` -- the same program, spelled without the operator.  The
    semantic version of this property is checked below.
    """
    for seed in seed_list:
        src = generate(seed, allow_undo=False)
        module = parse_text(src)
        once = invert_module(module, ("main",))
        twice = invert_module(once, ("main",))
        # the doc comment accumulates a note each time; compare the code only
        a = _code_only(print_module(twice))
        b = _code_only(print_module(module))
        eq(a, b, f"seed {seed}: double inversion is not the identity")


def _code_only(text: str) -> str:
    return "\n".join(l for l in text.splitlines() if not l.strip().startswith("///"))


@case(seeds(40, 10500))
def test_inverting_twice_preserves_behaviour(seed_list):
    """The semantic form of the involution, `undo` included."""
    for seed in seed_list:
        src = generate(seed)
        module = parse_text(src)
        twice = print_module(invert_module(invert_module(module, ("main",)), ("main",)))
        a = fresh(build(src, "a.rev"))
        b = fresh(build(twice, "b.rev"))
        a.start_forward().run()
        b.start_forward().run()
        ok, why = state_equal(a.snapshot(), b.snapshot())
        is_true(ok, f"seed {seed}: {why}\n\n{twice}")


@case(seeds(25, 16000))
def test_every_single_step_is_exactly_invertible(seed_list):
    """The strongest form of the property, checked at every boundary.

    Round-tripping a whole program only shows that the *composition* of the
    forward and backward runs is the identity; errors that cancel survive it.
    This checks each transition on its own: from every reachable point, step
    back and forward again and demand bit-for-bit equality of state *and*
    program counter.  It is what caught the classical branch bit being written
    underneath the branch's own tape entries instead of on top of it.
    """
    for seed in seed_list:
        src = generate(seed, max_depth=2, stmts=4)
        prog = build(src, f"gen{seed}.rev")
        m = fresh(prog)
        m.start_forward().run()
        total = m.position
        if total > 400:
            continue
        for k in range(1, total + 1):
            walk = fresh(prog).start_forward()
            for _ in range(k):
                walk.step()
            here = walk.snapshot()
            walk.reverse()
            walk.step()
            walk.reverse()
            walk.step()
            ok, why = state_equal(here, walk.snapshot())
            is_true(ok, f"seed {seed}: flip at step {k}/{total}: {why}\n\n{src}")
            eq(walk.pc, here["pc"], f"seed {seed}: pc drifted at step {k}")


@case(seeds(50, 12000))
def test_ir_inversion_agrees_with_source_inversion(seed_list):
    """Two independent inverters must produce programs that behave alike.

    One rewrites the syntax tree; the other reverses the structured IR the
    compiler builds.  They share no code.
    """
    for seed in seed_list:
        src = generate(seed)
        module = parse_text(src, f"gen{seed}.rev")
        a = analyze(module)
        a.diagnostics.raise_if_errors()
        compiler = Compiler(module, a)
        prog = compiler.compile()

        m = fresh(prog)
        before = m.snapshot()
        m.start_forward().run()
        after = m.snapshot()

        # (1) invert the source, compile the result
        src_inv = print_module(invert_module(parse_text(src), ("main",)))
        p1 = build(src_inv, f"gen{seed}_srcinv.rev")
        m1 = fresh(p1, after)
        m1.start_forward().run()

        # (2) invert the IR of `main` and relower
        p2 = _compile_with_inverted_main(module)
        m2 = fresh(p2, after)
        m2.start_forward().run()

        ok1, why1 = state_equal(before, m1.snapshot())
        ok2, why2 = state_equal(before, m2.snapshot())
        is_true(ok1, f"seed {seed}: source inversion: {why1}")
        is_true(ok2, f"seed {seed}: IR inversion: {why2}")
        ok3, why3 = state_equal(m1.snapshot(), m2.snapshot())
        is_true(ok3, f"seed {seed}: the two inverters disagree: {why3}")


def _compile_with_inverted_main(module):
    """Compile a module, inverting `main`'s RIR rather than its source."""
    from reverie import ast
    from reverie.isa import Call as CallInstr, Halt, ProcEntry, ProcExit
    from reverie.rir import CodeBuilder
    from reverie.vm import GlobalInfo, ProcInfo, Program

    a = analyze(module)
    a.diagnostics.raise_if_errors()
    c = Compiler(module, a)
    b = CodeBuilder()
    b.emit(CallInstr("main", []))
    b.emit(Halt())
    prog = Program(code=b.code, n_globals=a.n_globals, entry="main")
    prog.n_stacks = a.n_stacks
    for name, g in a.globals.items():
        prog.globals[name] = GlobalInfo(name, g.addr, g.length, g.type, g.stack_id)
    for decl in module.procs():
        info = a.procs[decl.name]
        c.ctemp_as_param = False
        body = c.stmt(decl.body)
        if decl.name == "main":
            body = body.invert()
        entry_at = b.emit(ProcEntry(decl.name))
        body.lower(b)
        exit_at = b.emit(ProcExit(decl.name))
        prog.procs[decl.name] = ProcInfo(
            decl.name, entry_at, exit_at, info.frame_size,
            tuple(p.name for p in info.params),
            tuple(p.type for p in info.params),
        )
        for plan in info.embeds:
            c.emit_embed_proc(b, prog, plan)
    return prog.finalize()


# ---------------------------------------------------------------------------
# time travel
# ---------------------------------------------------------------------------


@case(seeds(40, 21000))
def test_flipping_direction_mid_run_returns_to_the_same_state(seed_list):
    rng = random.Random(1234)
    for seed in seed_list:
        src = generate(seed)
        prog = build(src, f"gen{seed}.rev")
        m = fresh(prog)
        m.start_forward().run()
        total = m.position
        if total < 6:
            continue
        k = rng.randrange(1, total)
        j = rng.randrange(1, min(k, 12) + 1)
        m2 = fresh(prog)
        m2.start_forward()
        for _ in range(k):
            m2.step()
        here = m2.snapshot()
        m2.reverse()
        for _ in range(j):
            m2.step()
        m2.reverse()
        for _ in range(j):
            m2.step()
        ok, why = state_equal(here, m2.snapshot())
        is_true(ok, f"seed {seed}: k={k} j={j}: {why}")
        eq(m2.pc, here["pc"], f"seed {seed}: pc drifted")


@case(seeds(40, 26000))
def test_goto_lands_exactly_where_stepping_would(seed_list):
    """The debugger's `goto` must be indistinguishable from having run there."""
    from reverie.debugger import Debugger

    rng = random.Random(99)
    for seed in seed_list:
        src = generate(seed)
        prog = build(src, f"gen{seed}.rev")
        m = fresh(prog)
        m.start_forward().run()
        total = m.position
        if total < 4:
            continue
        target = rng.randrange(0, total)
        walked = fresh(prog)
        walked.start_forward()
        for _ in range(target):
            walked.step()

        dbg = Debugger(prog, None, mem_size=4096, color=False, out=open(os.devnull, "w"))
        dbg.machine.max_steps = 2_000_000
        dbg.do_run()
        dbg.do_goto(target)
        ok, why = state_equal(walked.snapshot(), dbg.machine.snapshot())
        is_true(ok, f"seed {seed}: goto {target}/{total}: {why}")
        eq(dbg.machine.position, target)


# ---------------------------------------------------------------------------
# the front end
# ---------------------------------------------------------------------------


@case(seeds(60, 31000))
def test_formatting_is_idempotent(seed_list):
    for seed in seed_list:
        src = generate(seed)
        once = print_module(parse_text(src))
        twice = print_module(parse_text(once))
        eq(twice, once, f"seed {seed}: the formatter is not a fixed point")


@case(seeds(60, 36000))
def test_formatting_preserves_behaviour(seed_list):
    for seed in seed_list:
        src = generate(seed)
        formatted = print_module(parse_text(src))
        a = fresh(build(src, "a.rev"))
        b = fresh(build(formatted, "b.rev"))
        a.start_forward().run()
        b.start_forward().run()
        ok, why = state_equal(a.snapshot(), b.snapshot())
        is_true(ok, f"seed {seed}: formatting changed behaviour: {why}")


# ---------------------------------------------------------------------------
# generator shapes -- exercise each feature on its own
# ---------------------------------------------------------------------------


@case("embed only", dict(allow_calls=False, allow_stack=False, allow_undo=False, allow_print=False))
@case("stacks only", dict(allow_calls=False, allow_embed=False, allow_undo=False, allow_print=False))
@case("calls only", dict(allow_embed=False, allow_stack=False, allow_undo=False, allow_print=False))
@case("undo only", dict(allow_calls=False, allow_embed=False, allow_stack=False, allow_print=False))
@case("deep", dict(max_depth=5, stmts=8))
@case("flat", dict(max_depth=0, stmts=10))
def test_feature_subsets_round_trip(label, options):
    for seed in seeds(25, 41000):
        src = Generator(random.Random(seed), **options).module()
        prog = build(src, f"{label}{seed}.rev")
        m = fresh(prog)
        before = m.snapshot()
        m.start_forward().run()
        m.check_clean(label)
        m.start_backward().run()
        ok, why = state_equal(before, m.snapshot())
        is_true(ok, f"{label} seed {seed}: {why}\n\n{src}")


@slow
@case(seeds(500, 60000))
def test_deep_fuzz(seed_list):
    for seed in seed_list:
        src = generate(seed, max_depth=4, stmts=9)
        prog = build(src, f"deep{seed}.rev")
        m = fresh(prog)
        before = m.snapshot()
        m.start_forward().run()
        m.check_clean()
        m.start_backward().run()
        ok, why = state_equal(before, m.snapshot())
        is_true(ok, f"seed {seed}: {why}")
