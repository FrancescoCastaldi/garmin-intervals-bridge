"""What the advice features share: the write rules for one wellness field, and the run once a morning.

Write rules: no value, no write; never a locked day; a value the day already holds stays
(unless `overwrite`); only the advice field (and the feature's own companions) is sent, then
read back. The morning run: due once the night has been read, final for the day once
written or decided otherwise, a day whose own values are not in Intervals yet is tried
again later, given up at GIVE_UP_HOUR; dry runs report once a day and write nothing.
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime
from typing import Any, Callable

log = logging.getLogger(__name__)

RETRY_MINUTES = (10, 30, 60, 120, 240)
WRITE_PAUSE = 0.25               # seconds between writes when a range is filled (Intervals rate-limits)
GIVE_UP_HOUR = 22                # no complete night by then: no advice for the day


def stored_value(record: dict, field: str) -> int | None:
    """The day's value of a select field; an empty string (ImReady4's 'no data') counts as none."""
    value = record.get(field)
    if value in (None, ""):
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def decide(value: int | None, waiting: bool, record: dict, field: str, *, overwrite: bool = False) -> str:
    if value is None:
        return "wait" if waiting else "none"
    if record.get("locked") is True:
        return "locked"
    current = stored_value(record, field)
    if current == value:
        return "same"
    if current is not None and not overwrite:
        return "keep"
    return "write"


def write(intervals: Any, day: date, field: str, value: int | None, record: dict, *, apply: bool,
          waiting: bool = False, overwrite: bool = False, extra: dict | None = None) -> str:
    """Write one day's value when the rules allow it, and read it back; the action taken."""
    action = decide(value, waiting, record, field, overwrite=overwrite)
    if action != "write":
        return action
    if not apply:
        return "would write"
    intervals.write_wellness(day, {field: value, **(extra or {})})
    back = stored_value(intervals.wellness(day), field)
    if back != value:
        raise RuntimeError(f"{field} of {day} reads back as {back}, not {value}")
    return "written"


def state(store: Any, prefix: str, day: date) -> dict | None:
    raw = store.get_meta(f"{prefix}:{day}")
    try:
        return json.loads(raw) if raw else None
    except ValueError:
        return None


def due(enabled: bool, store: Any, prefix: str, day: date, now_ts: float, *, apply: bool) -> bool:
    if not enabled or state(store, prefix, day):
        return False
    if not apply and store.get_meta(f"{prefix}:dry:{day}"):
        return False
    return now_ts >= float(store.get_meta(f"{prefix}:next:{day}") or 0)


def morning(store: Any, prefix: str, name: str, day: date, now: datetime, *, apply: bool,
            compute: Callable[[], dict], describe: Callable[[dict], str]) -> dict:
    """Run `compute` (which returns a result with an `action`) under the once-a-day rules."""
    now_ts = now.timestamp()
    tries = int(store.get_meta(f"{prefix}:tries:{day}") or 0)

    def final(result: dict) -> dict:
        if apply:
            store.set_meta(f"{prefix}:{day}", json.dumps({**result, "at": now.isoformat(timespec="seconds")}))
        else:
            store.set_meta(f"{prefix}:dry:{day}", json.dumps(result))
        return result

    def later(reason: str) -> dict:
        if now.hour >= GIVE_UP_HOUR:
            log.info("%s for %s: gave up (%s)", name, day, reason)
            return final({"action": "gave up", "reason": reason})
        store.set_meta(f"{prefix}:tries:{day}", str(tries + 1))
        store.set_meta(f"{prefix}:next:{day}", str(now_ts + RETRY_MINUTES[min(tries, len(RETRY_MINUTES) - 1)] * 60))
        return {"action": "wait", "reason": reason}

    try:
        result = compute()
    except Exception as exc:                       # Intervals trouble: the next check tries again
        log.warning("%s for %s not done: %s; trying again later", name, day, type(exc).__name__)
        return later(type(exc).__name__)
    if result.get("action") == "wait":
        log.info("%s for %s waits: %s", name, day, result.get("reason"))
        return later(result.get("reason") or "waiting")
    log.info("%s for %s: %s", name, day, describe(result) + ("" if apply or result.get("action") != "would write"
                                                             else " - dry run, not written"))
    return final(result)
