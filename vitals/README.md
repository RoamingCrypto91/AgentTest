# Community Vitals

An instrument for telling a living community from a dead one, on any platform.

Every community platform reports members, messages and engagement. All three
can rise while the community dies. This measures the things that cannot be
faked: whether a newcomer ever speaks, whether anybody answers, whether
anybody comes back, whether anybody here knows anybody other than the host,
and whether the place survives you being busy for a fortnight.

```
$ vitals demo broadcast -o report.html
broadcast (synthetic): 3,041 messages from 722 people across 11 rooms over 119 days
  Arrival is the binding constraint: first posts that somebody answered.
  report written to report.html
```

## Try it in thirty seconds

No dependencies. Python 3.11 and the standard library.

```bash
python3 -m vitals demo dying -o demo.html     # a report from a simulated community
python3 -m vitals template -o events.csv      # the shape any platform maps to
python3 -m vitals show events.csv             # the numbers, in the terminal
```

## On a real community

See `GETTING-DATA.md` for how to obtain an export from each platform, and for
the one fact no export tool gives you.

```bash
# DiscordChatExporter JSON, one channel or a directory of them
python3 -m vitals audit export/ --staff "Dan" --roster members.csv -o report.html

# a Slack workspace export
python3 -m vitals audit slack-export/ -o report.html

# anything else, via CSV
python3 -m vitals audit events.csv --surfaces rooms.txt -o report.html
```

Adding a platform means writing one function that emits events. That is the
whole reason it is built this way round: the platform is not the subject.

## What comes out

A report that names one thing. Five gates, the earliest failing one marked as
the binding constraint, three interventions aimed at it and nothing else, and
the names of the people and rooms to act on this week.

- **Connectors**, listed. The members who already talk to more people than
  anyone else. The useful version of "recruit some moderators" is a list.
- **Quiet regulars**, listed. People who keep turning up and have met almost
  nobody. An introduction is the whole fix.
- **Graveyard rooms**, listed. Every one is evidence to a newcomer that
  nobody lives here.
- **What has moved** since the first half of the window, measured with the
  same code on both halves.

Every intervention states the behaviour it exploits, the measure that should
move, and how long to wait before re-measuring. A prescription that cannot be
checked is an opinion.

## It reads no message content

The instrument works from metadata: who posted, where, when, and who they were
answering. Nobody's words are stored or examined to produce any number in a
report. An audit can be run on somebody else's community without holding a
single sentence their members wrote.

## Layout

```
vitals/
  schema.py         the only facts about a community that matter
  interactions.py   who spoke to whom: reply, mention, adjacency, reaction
  metrics.py        nineteen measures, each attached to a gate
  model.py          five gates, and the binding-constraint rule
  benchmarks.py     where the lines are drawn, and how provisional they are
  prescribe.py      the intervention library
  audit.py          one audit, end to end
  report.py         the HTML a client reads
  simulate.py       synthetic communities, for validation and for demos
  bench.py          turning stored audits into measured benchmarks
  adapters/         discord, slack, csv
  tests/            116 cases, standard library only
  roster.py         join dates, merged in from wherever they can be found
  MODEL.md          what is measured and why
  PLAYBOOK.md       generated from the library, so it cannot drift
  GETTING-DATA.md   how to get an export out of each platform
  COMMERCIAL.md     what to sell, and in what order
```

## How it is validated

The hard part of an instrument is knowing it measures anything. So
`vitals/simulate.py` generates communities from an explicit model of
behaviour — engagement decays daily, being answered lifts it, being ignored
drops it hard, below a floor people stop coming — and produces four with known
character: a broadcast where nobody answers anybody, a clique that talks only
to itself, a healthy mesh, and a mesh that loses its connectors halfway
through.

`vitals/tests/test_discrimination.py` then asserts that the measurements
recover that character, including the case that matters most: the collapsing
community's weekly poster count **rises** while almost nobody who arrives is
still there a month later. An instrument that cannot separate those two is
worthless, and that test is what says this one can.

```bash
python3 -m unittest discover -s vitals/tests -t .
```
