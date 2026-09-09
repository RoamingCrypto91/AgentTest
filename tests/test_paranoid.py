"""Paranoid mode: the machine checking itself, step by step."""

import os

from framework import case, contains, eq, is_true, raises, slow
from support import EXAMPLES
from reverie import isa
from reverie.compiler import compile_text
from reverie.diagnostics import RuntimeFault
from reverie.vm import Machine, state_equal
from test_examples import INPUTS, build

SRC = """int x;
int y;
stack s;
proc main() {
    x += 3;
    if x > 2 { y += 5; } else { y -= 5; } fi y > 0;
    local int t = 0;
    t += x;
    push(t, s);
    delocal int t = 0;
    call helper(y);
    uncall helper(y);
    embed (y ^= r) { var r = 0; while (r < 4) { r = r + 1; } }
    print "y = ", y;
}
proc helper(int p) { p *= 3; }
"""


def machine(paranoid=True, **kw):
    prog = compile_text(SRC, "p.rev")
    return Machine(prog, mem_size=512, paranoid=paranoid, **kw)


def test_a_clean_program_passes():
    m = machine().start_forward().run()
    eq(m.globals_dict()["x"], 3)
    m.check_clean()


def test_the_report_is_identical_either_way():
    plain = machine(paranoid=False).start_forward().run()
    checked = machine(paranoid=True).start_forward().run()
    eq(checked.position, plain.position)
    eq(checked.globals_dict(), plain.globals_dict())
    eq(checked.output, plain.output)
    eq(checked.stats.merge_report(), plain.stats.merge_report())


def test_it_works_backwards_too():
    m = machine()
    before = m.snapshot()
    m.start_forward().run()
    m.start_backward().run()
    ok, why = state_equal(before, m.snapshot())
    is_true(ok, why)
    eq(m.position, 0)


def test_the_fingerprint_ignores_dead_frame_cells():
    m = machine(paranoid=False).start_forward()
    m.run(limit=3)
    fp = m.fingerprint()
    # cells above the stack pointer are not live state
    m.mem[m.sp + 3] = 12345
    eq(m.fingerprint(), fp)


# ---------------------------------------------------------------------------
# it has to actually catch things
# ---------------------------------------------------------------------------


class sabotage:
    """Temporarily replace a method on an instruction class."""

    def __init__(self, cls, name, fn):
        self.cls, self.name, self.fn = cls, name, fn

    def __enter__(self):
        self.old = getattr(self.cls, self.name)
        setattr(self.cls, self.name, self.fn)

    def __exit__(self, *exc):
        setattr(self.cls, self.name, self.old)
        return False


def test_it_catches_an_update_that_does_not_invert():
    def broken(self, m):
        a = self.addr.resolve(m)
        m.mem[a] = isa._apply_update(self.op, m.mem[a], self.expr.eval(m), m)
        m.pc -= 1

    with sabotage(isa.Update, "backward", broken):
        with raises(RuntimeFault, "is not invertible") as r:
            machine().start_forward().run()
    contains(r.error.notes[0], "mem[")


def test_it_catches_a_conditional_that_lands_on_the_wrong_boundary():
    def broken(self, m):
        m.pc = (self.then_end if self.exit_cond.eval(m) != 0 else self.at) + 1

    with sabotage(isa.Fi, "backward", broken):
        with raises(RuntimeFault, "is not invertible") as r:
            machine().start_forward().run()
    contains(r.error.notes[0], "pc was")


def test_it_catches_a_classical_loop_that_miscounts():
    def broken(self, m):
        m.pc = self.at  # forgets to decrement the counter

    with sabotage(isa.CUntil, "backward", broken):
        with raises(RuntimeFault, "is not invertible"):
            machine().start_forward().run()


def test_it_catches_a_stack_push_that_does_not_pop():
    def broken(self, m):
        m.pc -= 1  # forgets to undo the push entirely

    with sabotage(isa.StackPush, "backward", broken):
        with raises(RuntimeFault, "is not invertible") as r:
            machine().start_forward().run()
    # push zeroes its source, so the cell is the first thing to differ
    contains(r.error.notes[0], "mem[")


def test_it_catches_output_that_is_not_taken_back():
    def broken(self, m):
        m.pc -= 1

    with sabotage(isa.Emit, "backward", broken):
        with raises(RuntimeFault, "is not invertible") as r:
            machine().start_forward().run()
    contains(r.error.notes[0], "output")


def test_it_catches_a_step_that_faces_the_wrong_way():
    def broken(self, m):
        m.pc = self.at
        m.dir = 1

    with sabotage(isa.Update, "backward", broken):
        with raises(RuntimeFault):
            machine().start_forward().run()


# ---------------------------------------------------------------------------
# and it holds for everything that ships
# ---------------------------------------------------------------------------


for _name in sorted(INPUTS):

    def _make(name=_name):
        def test():
            prog = build(os.path.join(EXAMPLES, name))
            m = Machine(prog, mem_size=1 << 13, max_steps=20_000_000, paranoid=True)
            m.set_globals(INPUTS[name])
            before = m.snapshot()
            m.start_forward().run()
            m.check_clean(name)
            m.start_backward().run()
            ok, why = state_equal(before, m.snapshot())
            is_true(ok, f"{name}: {why}")

        test.__name__ = f"test_paranoid_{name.replace('.rev', '')}"
        test.__doc__ = f"{name}: every individual step is invertible."
        return test

    _fn = _make()
    globals()[_fn.__name__] = _fn
