"""What to do about it.

A diagnosis nobody acts on is a report nobody pays twice for.  So every gate
has interventions attached, and each one states the mechanism it exploits,
the measure it should move, and how long to wait before measuring again.  A
prescription that cannot be checked is an opinion.

Two rules are built in.

First, everything prescribed aims at the binding constraint.  Handing someone
sixteen things to fix is the same as handing them nothing, and effort spent
past a leaking gate drains out of the leak.

Second, every intervention names its own falsification: the measure that has
to move, and by when.  If it does not move, the intervention was wrong for
this community, and that is worth knowing quickly.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Intervention:
    key: str
    gate: str
    title: str
    #: the specific thing to do.  No "increase engagement".
    do: str
    #: the behaviour it exploits
    mechanism: str
    #: the measure that should move if it worked
    moves: tuple
    #: how long before re-measuring is meaningful
    review_days: int
    #: rough ongoing cost to whoever runs the community
    effort: str
    #: only offered when this measure is at or below this score
    trigger: tuple = ()


LIBRARY = (
    # -- arrival ---------------------------------------------------------
    Intervention(
        "greeter_rota", "arrival",
        "Put named people on a greeting rota with a stated response time",
        "Two or three people, each owning specific days, with one promise: no "
        "first post from a new member goes unanswered for more than an hour "
        "during waking hours. Not a bot. A person, using their name, "
        "responding to what was actually said.",
        "An unanswered first contribution tells the member, accurately, that "
        "nobody was waiting for them. It is the single most reliable way to "
        "lose somebody, and the cheapest to stop.",
        moves=("first_post_answered", "returned"),
        review_days=21, effort="20 minutes a day, shared",
        trigger=("first_post_answered",),
    ),
    Intervention(
        "one_question_door", "arrival",
        "Replace the welcome wall with one question a stranger can answer",
        "Delete the rules-and-roles gauntlet from the first thing a newcomer "
        "sees. Put a single, specific, low-stakes question in its place, and "
        "keep it answerable in under ten words. \"What are you working on this "
        "week\" beats \"introduce yourself\" because the second one requires "
        "deciding who you are.",
        "The cost of a first action has to be near zero. Open-ended prompts "
        "raise it, because the member has to compose an identity before they "
        "can speak.",
        moves=("activation", "never_spoke"),
        review_days=28, effort="one afternoon",
        trigger=("activation", "never_spoke"),
    ),
    Intervention(
        "hand_off_to_a_person", "arrival",
        "Introduce each newcomer to one member, by name",
        "When the greeter answers, they name one existing member with "
        "something in common and address them both. One sentence: \"@x has "
        "been doing the same thing, you two should compare notes.\"",
        "The first tie to a peer, rather than to the host, is what turns an "
        "arrival into a member. Without it, the only relationship the person "
        "has here is with the brand.",
        moves=("orbit", "median_partners", "member_to_member"),
        review_days=30, effort="nothing extra, once greeting exists",
        trigger=("first_post_answered_by_member", "orbit"),
    ),

    # -- return ----------------------------------------------------------
    Intervention(
        "one_ritual", "return",
        "Run exactly one thing at exactly the same time every week",
        "One recurring event, same day, same hour, no exceptions, announced "
        "in advance and summarised afterwards. One. A calendar of five "
        "irregular things produces no habit in anybody.",
        "A habit needs a cue, and a cue only works if it is predictable. "
        "Irregular activity trains members to check nothing.",
        moves=("returned", "retention_30d", "weekly_active_trend"),
        review_days=42, effort="one hour a week, fixed",
        trigger=("returned", "retention_30d"),
    ),
    Intervention(
        "day_three_nudge", "return",
        "Reach out personally on day three and day ten",
        "Two direct messages per newcomer, written by a human, referring to "
        "the thing they actually posted. Not a drip sequence; those read as "
        "automated within one line and are ignored accordingly.",
        "Attrition is not spread evenly. It clusters in the first week and "
        "again around the point where the novelty is gone and no habit has "
        "formed yet.",
        moves=("retention_30d", "returned"),
        review_days=35, effort="15 minutes a day",
        trigger=("retention_30d",),
    ),
    Intervention(
        "member_written_recap", "return",
        "Have a member, not staff, write the weekly recap",
        "One member summarises what happened, posted on a fixed day, credited "
        "by name. Rotate it slowly.",
        "It creates a reason to look and a piece of visible status to hand "
        "out, and it does both without the host generating the content.",
        moves=("returned", "staff_voice", "connectors"),
        review_days=42, effort="delegated",
        trigger=("returned", "staff_voice"),
    ),

    # -- relationship ----------------------------------------------------
    Intervention(
        "engineered_pairs", "relationship",
        "Introduce two members to each other every week, with a reason",
        "Pick two people, state what they have in common, ask them a question "
        "that needs both of them to answer. Do it in public so others see it "
        "is normal here.",
        "Ties between members do not form on their own in a room where the "
        "host is the most interesting thing present. They have to be "
        "manufactured until the density is high enough to sustain itself.",
        moves=("member_to_member", "median_partners", "orbit"),
        review_days=45, effort="15 minutes a week",
        trigger=("member_to_member", "orbit", "median_partners"),
    ),
    Intervention(
        "staff_answer_second", "relationship",
        "Wait thirty minutes before answering anything a member could answer",
        "A deliberate delay, applied by staff to every question that is not "
        "urgent or private. If nobody has answered in thirty minutes, answer "
        "it. Tell the moderators you are doing this, or they will fill the "
        "gap themselves.",
        "Standing is earned by being useful in public. Where the host answers "
        "everything within two minutes, there is no standing available to "
        "earn, so nobody earns any, so nobody has a reason to stay.",
        moves=("first_post_answered_by_member", "staff_voice", "conversational"),
        review_days=30, effort="restraint, not time",
        trigger=("staff_voice", "first_post_answered_by_member"),
    ),

    # -- contribution ----------------------------------------------------
    Intervention(
        "name_the_regulars", "contribution",
        "Give your existing connectors a title and a job",
        "The instrument names the members who talk to the most other people. "
        "Those are already doing the work. Ask them, privately, to keep doing "
        "it with a title attached and one specific responsibility each.",
        "Escalating commitment: someone who has already invested accepts a "
        "larger ask, and a title makes the investment legible to everyone "
        "else, which is what makes it worth having.",
        moves=("connectors", "staff_voice", "absence_resilience"),
        review_days=45, effort="a few conversations",
        trigger=("connectors", "staff_voice"),
    ),
    Intervention(
        "member_led_format", "contribution",
        "Move one recurring slot to a member host",
        "Take a format you currently run and hand it to a member, with you "
        "present but silent for the first two runs.",
        "It converts a consumer into a producer in one step, and it proves to "
        "everyone watching that the stage is actually available.",
        moves=("staff_voice", "connectors", "absence_resilience"),
        review_days=60, effort="less than doing it yourself",
        trigger=("staff_voice", "connectors"),
    ),

    # -- stewardship -----------------------------------------------------
    Intervention(
        "announced_absence", "stewardship",
        "Take a fortnight off, and say so in advance",
        "Announce a two-week absence, hand named responsibilities to named "
        "people, and do not post. Measure the same things when you get back.",
        "It is the only honest test of whether the place is an asset or an "
        "obligation, and announcing it converts a risk into a rehearsal.",
        moves=("absence_resilience", "bus_factor"),
        review_days=30, effort="a fortnight of nerve",
        trigger=("absence_resilience",),
    ),
    Intervention(
        "spread_the_load", "stewardship",
        "Widen the group carrying the conversation",
        "The instrument says how few people account for half the talking. "
        "Recruit deliberately from the next tier down: the members who post "
        "regularly but talk to almost nobody.",
        "A community carried by two or three people is one bad month from "
        "silence, and the people carrying it are the ones most likely to burn "
        "out and leave.",
        moves=("bus_factor", "concentration"),
        review_days=60, effort="ongoing",
        trigger=("bus_factor", "concentration"),
    ),

    # -- structure -------------------------------------------------------
    Intervention(
        "close_the_empty_rooms", "structure",
        "Archive every room that is not warm, and stop adding them",
        "Close or merge anything with no conversation in a fortnight. Fewer "
        "rooms, all of them busy, beats a complete taxonomy of silence.",
        "An empty room is evidence to a newcomer that nobody lives here, and "
        "each additional room manufactures more of that evidence while "
        "dividing the traffic that could have disproved it.",
        moves=("graveyards", "rooms_per_person", "conversational"),
        review_days=21, effort="one afternoon",
        trigger=("graveyards", "rooms_per_person"),
    ),
)

BY_GATE: dict = {}
for _i in LIBRARY:
    BY_GATE.setdefault(_i.gate, []).append(_i)

#: never hand over more than this many at once
MAX_PRESCRIBED = 3


def prescribe(assessment: dict, limit: int = MAX_PRESCRIBED) -> dict:
    """Interventions for the binding constraint, worst measure first.

    Returns the plan plus the reasoning, so the report can say why these
    three and not the other thirteen.
    """
    binding = assessment.get("binding")
    structure = assessment.get("structure")
    plan: list = []
    reason = ""

    if binding is None:
        reason = ("No gate is leaking, so there is nothing to unblock. What is "
                  "left is housekeeping and keeping the measurements running.")
    else:
        # Rank by every failing measure, not only this gate's own: an
        # intervention at the arrival gate is often the right answer to a
        # symptom that shows up as a relationship measure.
        weak = _weak_keys(binding) + [
            k for k in _all_weak(assessment) if k not in _weak_keys(binding)
        ]
        candidates = BY_GATE.get(binding.gate.key, [])
        plan = _rank(candidates, weak)
        reason = (
            f"{binding.gate.name} is the earliest gate not holding, so it is the "
            f"only one worth spending on. Everything aimed at a later gate is "
            f"being poured through this leak."
        )
        if not plan:
            plan = list(candidates)[:limit]

    housekeeping: list = []
    if structure is not None and structure.verdict in ("failing", "strained"):
        housekeeping = _rank(BY_GATE.get("structure", []), _weak_keys(structure))

    return {
        "binding": binding,
        "reason": reason,
        "plan": plan[:limit],
        "housekeeping": housekeeping[:2],
        "held_back": [i for i in plan[limit:]],
    }


def _all_weak(assessment: dict) -> list:
    """Every measure anywhere in the assessment that is not holding."""
    out: list = []
    for result in list(assessment.get("gates", [])) + [assessment.get("structure")]:
        if result is not None:
            out += _weak_keys(result)
    return out


def _weak_keys(result) -> list:
    """Measure keys in this gate that are not holding, worst first."""
    scored = [f for f in result.findings + result.supporting if f.score is not None]
    scored.sort(key=lambda f: f.score)
    return [f.measure.key for f in scored if f.verdict != "holding"]


def _rank(candidates: list, weak: list) -> list:
    """Interventions whose trigger is among the failing measures, worst first."""
    order = {k: i for i, k in enumerate(weak)}
    hits = []
    for i in candidates:
        matched = [order[t] for t in i.trigger if t in order]
        if matched:
            hits.append((min(matched), i))
    hits.sort(key=lambda pair: pair[0])
    return [i for _, i in hits]
