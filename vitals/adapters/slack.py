"""A Slack workspace export: a directory of per-channel, per-day JSON.

Included to keep the instrument honest about being platform-agnostic.  Slack
gives us something Discord does not -- threads, explicitly, via ``thread_ts``
-- and it names admins and owners in ``users.json``, so staff identification
needs no guessing.
"""

from __future__ import annotations

import json
import os

from ..schema import MESSAGE, REACTION, Community, Event, Member
from .common import count_words, parse_time

MARKERS = ("users.json", "channels.json")


def looks_like(path: str) -> bool:
    if os.path.isdir(path):
        return any(os.path.exists(os.path.join(path, m)) for m in MARKERS)
    return os.path.basename(path) in MARKERS


def _read(path: str):
    with open(path) as fh:
        return json.load(fh)


def load(path: str, staff: tuple = (), name: str = "", **_) -> Community:
    root = path if os.path.isdir(path) else os.path.dirname(path)
    community = Community(name=name or os.path.basename(root.rstrip("/")) or "slack",
                          platform="slack")
    staff_names = {s.strip().casefold() for s in staff if s.strip()}

    users_file = os.path.join(root, "users.json")
    if os.path.exists(users_file):
        for user in _read(users_file):
            uid = str(user.get("id") or "")
            if not uid:
                continue
            member = community.member(uid)
            member.name = (user.get("profile") or {}).get("display_name") \
                or user.get("real_name") or user.get("name") or uid
            member.bot = bool(user.get("is_bot") or user.get("is_app_user"))
            member.staff = bool(user.get("is_admin") or user.get("is_owner")) \
                or str(member.name).casefold() in staff_names

    channels_file = os.path.join(root, "channels.json")
    if os.path.exists(channels_file):
        for channel in _read(channels_file):
            room = channel.get("name")
            if room and room not in community.surfaces:
                community.surfaces.append(str(room))

    for entry in sorted(os.listdir(root)):
        folder = os.path.join(root, entry)
        if not os.path.isdir(folder):
            continue
        if entry not in community.surfaces:
            community.surfaces.append(entry)
        for day in sorted(os.listdir(folder)):
            if not day.lower().endswith(".json"):
                continue
            for msg in _read(os.path.join(folder, day)):
                if msg.get("type") != "message" or msg.get("subtype"):
                    continue
                actor = str(msg.get("user") or "")
                if not actor:
                    continue
                when = parse_time(msg.get("ts"))
                thread = msg.get("thread_ts")
                parent = str(thread) if thread and str(thread) != str(msg.get("ts")) else ""
                community.add(Event(
                    at=when, actor=actor, kind=MESSAGE, surface=entry,
                    id=str(msg.get("ts") or ""),
                    parent_id=parent,
                    addressed=tuple(_mentioned(msg)),
                    words=count_words(msg.get("text")),
                ))
                for reaction in msg.get("reactions") or ():
                    for who in (reaction or {}).get("users") or ():
                        if not who or who == actor:
                            continue
                        community.add(Event(
                            at=when, actor=str(who), kind=REACTION,
                            surface=entry, id=f"x{msg.get('ts')}-{who}",
                            parent_id=str(msg.get("ts") or ""),
                            addressed=(actor,),
                        ))

    if not community.events:
        raise ValueError(f"{path} produced no usable messages")
    community.notes.append(
        "Slack exports record no joins, so arrival measures fall back to "
        "first-seen activity"
    )
    return community.finalise()


def _mentioned(msg: dict) -> list:
    """Slack writes mentions into the text as <@U123>."""
    text = str(msg.get("text") or "")
    out = []
    start = 0
    while True:
        open_at = text.find("<@", start)
        if open_at < 0:
            break
        close_at = text.find(">", open_at)
        if close_at < 0:
            break
        uid = text[open_at + 2:close_at].split("|")[0]
        if uid:
            out.append(uid)
        start = close_at + 1
    parent = msg.get("parent_user_id")
    if parent:
        out.append(str(parent))
    return out
