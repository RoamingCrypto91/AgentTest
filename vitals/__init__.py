"""Community Vitals -- an instrument for telling a living community from a dead one.

The platform is not the subject.  Discord, Circle, Slack, a forum: all of them
produce the same handful of facts about who spoke to whom, where, and when.
Everything here works on those facts alone, so a diagnosis made on one
platform means the same thing on another.

    from vitals import audit
    report = audit.run(community)

See docs/MODEL.md for what is being measured and why.
"""

__version__ = "0.1.0"
