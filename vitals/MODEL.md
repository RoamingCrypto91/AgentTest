# What is being measured, and why

## Activity metrics lie

Every community platform reports the same three things: members, messages,
and some ratio of the two called engagement. All three can rise while the
community dies, and the way it happens is not subtle.

A server takes forty new members a week. Almost none of them ever post. Of
the few who do, most are never answered and never come back. The five people
who were there at the start keep talking to each other and to the host.
Member count climbs, message count holds, and the owner is told the numbers
look healthy right up until the week the five get bored.

The simulated `dying` community in `vitals/simulate.py` is built to
demonstrate exactly this, and the test suite asserts it: it loses its
connectors halfway through the window, and its weekly poster count **goes
up**, because arrivals keep replacing the people leaving. Nothing in an
activity dashboard can see that. The cohort measure sees it immediately.

So this instrument measures none of those three things.

## The chain

A person who ends up holding a community up passes through five stages, and
each one has a gate that can fail. The stages are not invented here: they are
the reader-to-leader progression (Preece and Shneiderman), legitimate
peripheral participation (Lave and Wenger), and the four components of sense
of community — membership, influence, shared emotional connection, fulfilment
of needs (McMillan and Chavis). What is new is only the arrangement, and the
insistence that each stage be measurable from message metadata.

| Stage | The question | The mechanism that fails |
| --- | --- | --- |
| **Arrival** | Does a new member ever say anything, and does anybody answer? | A first contribution nobody acknowledges tells the member, accurately, that nobody was waiting for them. |
| **Return** | Is there a reason to come back next week? | A habit needs a cue, and a cue only works if it recurs predictably. Irregular activity trains members to check nothing. |
| **Relationship** | Does anybody here know anybody other than you? | If every thread of connection runs through the host, members have nothing holding them except the host. |
| **Contribution** | Is anyone doing the work besides you? | Standing is earned by being useful in public. Where the host answers everything, no standing is available to earn. |
| **Stewardship** | Does the place survive a fortnight of you being busy? | Until the answer is yes, the community is a job rather than an asset. |

Plus one set of conditions that sabotage every stage at once, grouped as
**Structure**: empty rooms, too many rooms, and activity concentrated in too
few hands.

## The binding constraint

The report names the **earliest** failing gate, not the worst one.

This is the only opinionated thing in the instrument and it is the reason the
report is worth reading. A community whose Relationship gate is catastrophic
and whose Arrival gate is merely strained should fix Arrival first, because
everyone being poured into the relationship stage is arriving through the
leak. Effort spent past a leaking gate drains out of the leak.

It also means the prescription is short. Three interventions, all aimed at one
gate. Handing somebody sixteen things to fix is the same as handing them
nothing.

## The measurements

Nineteen measures, each computable from metadata alone, each attached to the
gate it tests.

### Arrival

- **Members who have never posted.** Needs join dates. Most large communities
  sit near nine in ten; the question is whether this one is at nine or at
  ninety-nine.
- **Arrivals who posted within seven days.** The prompt version of the same
  question.
- **Median wait before a first post.** Read it next to the first measure: it
  is conditioned on people who did eventually speak, so a silent community
  can post a flattering wait. This is why the two are reported together, and
  it is the one measure where a broadcast beats a healthy community.
- **First posts that somebody answered, within twenty-four hours.** The
  strongest single predictor in the instrument and the cheapest thing to fix.
  A reaction counts: for a first post, being noticed is the point.
- **First posts answered by a member rather than staff.** The same question,
  asked of the community instead of the host.

### Return

- **Newcomers who posted in more than one week.** One visit is an event. Two
  is the beginning of a habit.
- **Newcomers still posting a month later.** The measure an activity
  dashboard cannot fake.
- **Change in weekly posters, first third to last.** Deliberately crude. A
  regression slope over twelve noisy weeks invites more confidence than the
  data supports; "the last third is down forty percent on the first" is a
  sentence somebody can act on.

### Relationship

- **Interactions between members rather than with staff.** The number that
  defines the difference between the two kinds of room.
- **People the median regular has spoken with.**
- **Members who have only ever interacted with staff.** This is what
  "announcement platform" means, stated as a number. These people are an
  audience standing in a room together.
- **Messages that are part of an exchange.** A noticeboard scores near zero
  however busy it looks.

### Contribution

- **Connectors per hundred active members.** Members who talk to at least
  five distinct people and turn up in at least three separate weeks. The
  report names them, because the useful version of "recruit some moderators"
  is a list.
- **Share of all posting done by staff.**

### Stewardship

- **Member posting on staff-quiet days, against staff-active days.** The only
  honest test of whether the place runs without you.
- **People accounting for half the conversation.** A community carried by two
  or three people is one bad month from silence, and those two or three are
  the most likely to burn out.

### Structure

- **Inequality of contribution** (Gini over messages per person).
- **Rooms with nothing in them for a fortnight**, named so they can be closed.
- **Rooms per hundred active members.**

## Who spoke to whom

Several measures need the interaction graph, and chat platforms only
sometimes record who a message was answering. The graph is therefore built
from three kinds of evidence:

1. **Reply** — the platform recorded it. Unambiguous.
2. **Mention** — the message named somebody. Nearly as good.
3. **Adjacency** — neither, but somebody else had just spoken in the same room
   within ten minutes. A guess, and the only way to see anything at all in
   the many servers where nobody uses the reply button.

Findings are computed on the strict graph, reply and mention only, because a
number a client is shown should not be built on guesses. Reactions are
counted as acknowledgement but never as conversation: for a newcomer's first
post, being noticed is the whole question, and a thumbs-up is not a
discussion.

## What it deliberately does not do

**It reads no message content.** The instrument works from who posted, where,
when, and who they were answering. Nobody's words are stored or examined to
produce any number in a report. That is a privacy property worth having and
it is also a constraint: nothing here can tell you whether the conversation
is any good, only whether it is happening and between whom.

**It does not measure sentiment, quality, or revenue.** Those matter and they
are somebody else's instrument.

**It cannot see what an export does not record.** Discord exports carry no
join dates, so from one of those the arrival measures fall back to first-seen
activity, which excludes everyone who never posted — exactly the population
an activation measure is about. The report says so rather than quietly
printing a flattering number.

## The thresholds are provisional, and that is the point

Every band in `vitals/benchmarks.py` is a judgement. They are good enough to
tell a failing measure from a fine one and not good enough to quote as a
percentile, and the report admits this in writing.

They are kept in one small file for a reason. Ten audited communities produce
a distribution nobody else has, and `vitals benchmark` turns stored audits
into measured quartile bands. From then on a report can say *your first-post
response rate is in the bottom quartile of the communities we have measured*,
which is a far stronger sentence than any judgement can support — and one
that cannot be copied by somebody who has not done the audits.

That is the only part of this that compounds.
