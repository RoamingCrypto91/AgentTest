"""DiscordChatExporter JSON, which is what people actually have.

Point it at one exported channel or at a directory of them.  Each file
becomes a surface, named by its category and channel so that a report reads
the way the server looks.

Two things are worth knowing about this format.  It records inline replies
under ``reference``, which is the strongest interaction evidence there is,
and it records ``roles`` per message author, which is how staff get
identified without anyone typing out a list.  It does not record joins, so
activation cannot be measured from it -- the instrument says so rather than
reporting the share of *posters* who posted and calling it activation.
"""

from __future__ import annotations

import json
import os

from ..schema import MESSAGE, REACTION, Community, Event, Member
from .common import count_words, is_staff, parse_time

SKIP_TYPES = {"ChannelPinnedMessage", "GuildMemberJoin", "ThreadCreated"}


def _files(path: str) -> list:
    if os.path.isdir(path):
        found = []
        for root, _dirs, names in os.walk(path):
            found += [os.path.join(root, n) for n in sorted(names)
                      if n.lower().endswith(".json")]
        if not found:
            raise ValueError(f"no .json exports under {path}")
        return found
    return [path]


def _surface(blob: dict, fallback: str) -> str:
    channel = blob.get("channel") or {}
    name = channel.get("name") or fallback
    category = channel.get("category")
    return f"{category}/{name}" if category else str(name)


def load(path: str, staff: tuple = (), name: str = "", **_) -> Community:
    staff_names = {s.strip().casefold() for s in staff if s.strip()}
    community = Community(name=name, platform="discord")
    joins_seen = False

    for file in _files(path):
        with open(file) as fh:
            try:
                blob = json.load(fh)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{file} is not valid JSON: {exc}") from None
        if not isinstance(blob, dict) or "messages" not in blob:
            raise ValueError(
                f"{file} does not look like a DiscordChatExporter export "
                f"(no `messages` key)"
            )
        if not community.name:
            community.name = (blob.get("guild") or {}).get("name") or "discord export"
        surface = _surface(blob, os.path.splitext(os.path.basename(file))[0])
        if surface not in community.surfaces:
            community.surfaces.append(surface)

        for msg in blob["messages"]:
            if msg.get("type") in SKIP_TYPES:
                if msg.get("type") == "GuildMemberJoin":
                    joins_seen = True
                    author = msg.get("author") or {}
                    actor = author.get("id") or author.get("name")
                    if actor:
                        member = community.member(str(actor))
                        when = parse_time(msg["timestamp"])
                        if member.joined_at is None or when < member.joined_at:
                            member.joined_at = when
                continue
            author = msg.get("author") or {}
            actor = str(author.get("id") or author.get("name") or "").strip()
            if not actor:
                continue
            member = community.member(actor)
            member.name = author.get("nickname") or author.get("name") or actor
            member.bot = bool(author.get("isBot"))
            if is_staff(author.get("roles"), staff_names) or \
                    str(member.name).casefold() in staff_names or \
                    actor.casefold() in staff_names:
                member.staff = True

            when = parse_time(msg["timestamp"])
            reference = msg.get("reference") or {}
            community.add(Event(
                at=when, actor=actor, kind=MESSAGE, surface=surface,
                id=str(msg.get("id") or ""),
                parent_id=str(reference.get("messageId") or ""),
                addressed=tuple(
                    str((m or {}).get("id") or (m or {}).get("name") or "")
                    for m in (msg.get("mentions") or [])
                    if (m or {}).get("id") or (m or {}).get("name")
                ),
                words=count_words(msg.get("content")),
            ))

            for reaction in msg.get("reactions") or ():
                for user in (reaction or {}).get("users") or ():
                    who = str((user or {}).get("id") or (user or {}).get("name") or "")
                    if not who or who == actor:
                        continue
                    community.member(who).name = (
                        (user or {}).get("name") or who
                    )
                    community.add(Event(
                        at=when, actor=who, kind=REACTION, surface=surface,
                        id=f"x{msg.get('id')}-{who}",
                        parent_id=str(msg.get("id") or ""),
                        addressed=(actor,),
                    ))

    if not community.events:
        raise ValueError(f"{path} produced no usable messages")
    community.notes.append(
        f"loaded {len(community.surfaces)} channel export(s) from DiscordChatExporter"
    )
    if not joins_seen:
        community.notes.append(
            "this export records no joins, so arrival measures fall back to "
            "first-seen activity and the silent majority is invisible"
        )
    return community.finalise()
