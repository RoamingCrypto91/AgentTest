"""Bits every adapter needs."""

from __future__ import annotations

from datetime import datetime, timezone

#: role names that mean "this person runs the place".  Matched case-folded
#: against whatever the export calls roles.
STAFF_ROLES = {
    "admin", "administrator", "mod", "moderator", "owner", "staff", "team",
    "host", "founder", "manager", "community manager", "creator", "leadership",
}


def parse_time(value) -> datetime:
    """Whatever the platform wrote down, as an aware UTC datetime."""
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), tz=timezone.utc)
    text = str(value).strip()
    if not text:
        raise ValueError("empty timestamp")
    if text.replace(".", "", 1).isdigit():
        return datetime.fromtimestamp(float(text), tz=timezone.utc)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d",
                    "%d/%m/%Y %H:%M", "%m/%d/%Y %H:%M"):
            try:
                parsed = datetime.strptime(text, fmt)
                break
            except ValueError:
                continue
        else:
            raise ValueError(f"cannot read the timestamp {value!r}")
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def count_words(text) -> int:
    """A length proxy.  The text itself is not kept."""
    return len(str(text or "").split())


def is_staff(roles, names: set = frozenset()) -> bool:
    for role in roles or ():
        label = role if isinstance(role, str) else (role or {}).get("name", "")
        if str(label).strip().casefold() in STAFF_ROLES:
            return True
        if str(label).strip().casefold() in names:
            return True
    return False


def truthy(value) -> bool:
    return str(value).strip().casefold() in {"1", "true", "yes", "y", "staff"}
