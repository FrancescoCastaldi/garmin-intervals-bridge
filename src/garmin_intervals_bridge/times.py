"""Timestamp parsing shared by matching and sync. Fail closed on anything ambiguous."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def external_id_names(external: object, garmin_id: str | int) -> bool:
    """Does an Intervals external_id refer to this Garmin activity?

    Verified shapes: the official sync stores the bare ID (`24544097680`); a
    manual upload of the Garmin export stores the file name
    (`24544097680_ACTIVITY.fit`); the bridge's own uploads use `garmin:<id>`.
    """
    if not isinstance(external, str):
        return False
    gid = str(garmin_id).strip() if garmin_id is not None else ""
    if not gid:
        return False
    ext = external.strip()
    return ext in (gid, f"garmin:{gid}") or ext.startswith(f"{gid}_")


def parse_utc(value: Any, *, garmin_gmt: bool = False) -> datetime | None:
    """Parse an ISO-8601 string to an aware UTC datetime.

    Garmin's `startTimeGMT` is a *naive* string that is UTC by convention; only
    with garmin_gmt=True is a naive value accepted. Everything else must carry
    an offset, otherwise None is returned and the caller must not guess.
    """
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        if not garmin_gmt:
            return None
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)
