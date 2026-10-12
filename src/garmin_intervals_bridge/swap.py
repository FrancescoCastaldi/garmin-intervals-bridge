"""Swap mode (BRIDGE_SWAP=true, off by default): Garmin's filtered copy for the original.

The official import stays on and is the trigger: within a minute of a Garmin activity
arriving in Intervals, the bridge uploads the device's original file as your own and
then deletes Garmin's filtered copy. Intervals then holds the complete file – it
computes every custom field and stream itself, "Reprocess file" keeps them, and it is
your upload, not Garmin-sourced data. Nothing to poll at Garmin: one original download
per new activity, as in enrich mode; with the bridge down the copy simply stays (and is
completed or swapped later).

This is the one place the bridge deletes anything, so it is careful:
  - only a copy of the official Garmin import (source GARMIN_CONNECT, its Garmin id),
    within BRIDGE_SWAP_HOURS (default 48) of its import – `swap <id>` by hand has no limit
  - never with comments on it, never when its intervals were edited by hand
  - not when the original is the very file Intervals has (Garmin filtered nothing)
  - upload first, delete last: a failure leaves a duplicate to see, never a loss; an
    upload with an unknown outcome stays `pending` (health reports it) and deletes nothing
  - name, description, the paired workout, type, RPE, feel, tags, gear, commute/race
    flags and ignore settings go over to the new activity, and so do the values of custom
    activity fields without a script (nothing in the file computes them: the logged intake
    the bridge writes from Garmin's summary, what you typed in yourself)
  - the original stays in the local archive either way
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from .enrich import garmin_id_from
from .fit import extract_original_fit, sha256

log = logging.getLogger(__name__)

# What the athlete may have set on the copy already; carried when it differs on the new one.
CARRY = ("name", "description", "type", "sub_type", "commute", "race", "trainer", "icu_rpe", "feel", "tags",
         "gear", "icu_ignore_hr", "icu_ignore_power", "icu_ignore_time", "ignore_velocity", "ignore_pace")
DELETE_KEY = "swap:delete:"            # + the copy's Intervals id; value: the new id


def _hours_since(value: Any, now: datetime) -> float | None:
    try:
        when = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return (now - when).total_seconds() / 3600


def typed_fields(intervals: Any) -> set[str]:
    """Custom activity fields Intervals cannot compute from the file: those without a script."""
    return {str((item.get("content") or {}).get("code")) for item in intervals.custom_items()
            if item.get("type") == "ACTIVITY_FIELD" and not str((item.get("content") or {}).get("script") or "").strip()
            and (item.get("content") or {}).get("code")}


def _value(value: Any) -> bool:
    return value not in (None, "", [], False) and str(value) != "NaN"


def why_not(copy: dict, gid: str, intervals: Any, store: Any, *, window_hours: float | None,
            now: datetime | None = None) -> str | None:
    """The reason to keep this copy; None when it may be swapped."""
    if str(copy.get("source") or "").upper() != "GARMIN_CONNECT":
        return f"not Garmin's import (source {copy.get('source') or 'unknown'})"
    if garmin_id_from(copy.get("external_id")) != gid:
        return "its Garmin id does not match"
    if window_hours is not None:
        age = _hours_since(copy.get("created") or copy.get("start_date"), now or datetime.now(timezone.utc))
        if age is None or age > window_hours:
            return f"imported more than {window_hours:g} h ago (swap it by hand: `swap <id> --apply`)"
    previous = store.enrichment(gid) or {}
    if copy.get("icu_intervals_edited") and not previous.get("streams"):
        return "its intervals were edited by hand"
    if intervals.activity_messages(str(copy["id"])):
        return "it has comments"
    return None


def swap_activity(gid: str, iid: str, garmin: Any, intervals: Any, store: Any, *, apply: bool,
                  window_hours: float | None = 48, now: datetime | None = None) -> dict:
    """Outcomes: kept (with the reason), same (nothing filtered), planned, swapped,
    review (the upload made several activities: nothing deleted), delete_failed (both stay;
    tried again on the next run)."""
    copy = intervals.activity(iid)
    reason = why_not(copy, gid, intervals, store, window_hours=window_hours, now=now)
    if reason:
        return {"outcome": "kept", "intervals_id": iid, "reason": reason}
    path = store.fit_path(gid)
    if not path.is_file():
        store.atomic_save(path, garmin.original_fit(gid))
    partner = store.partner_path(gid)
    if not partner.is_file():
        store.atomic_save(partner, extract_original_fit(intervals.activity_file(iid)))
    digest = sha256(path.read_bytes())
    if digest == sha256(partner.read_bytes()):
        return {"outcome": "same", "intervals_id": iid, "reason": "Garmin sent the original itself"}
    carry = {k: copy[k] for k in (*CARRY, *sorted(typed_fields(intervals))) if _value(copy.get(k))}
    plan = {"outcome": "planned", "intervals_id": iid, "garmin_id": gid, "carry": sorted(carry),
            "paired_event_id": copy.get("paired_event_id")}
    if not apply:
        return plan
    store.record_activity(gid, "pending", digest)          # an unknown outcome is never replayed
    result = intervals.upload_fit(gid, path, name=copy.get("name"), description=copy.get("description"),
                                  paired_event_id=copy.get("paired_event_id"))
    if not result.get("created"):
        store.record_activity(gid, "remote_exists", digest, iid)
        return {**plan, "outcome": "kept", "reason": "Intervals already has this file"}
    items = result.get("items") or []
    new_id = str(items[0]["id"])
    store.record_activity(gid, "uploaded", digest, new_id)
    store.mark_intervals_seen(new_id, f"garmin:{gid}", "UPLOAD")
    if len(items) != 1:
        log.warning("Swap %s: the original became %d activities; %s kept, check by hand", gid, len(items), iid)
        return {**plan, "outcome": "review", "new_ids": [str(i["id"]) for i in items]}
    fresh = intervals.activity(new_id)
    changes = {k: v for k, v in carry.items() if fresh.get(k) != v}
    if isinstance(changes.get("gear"), dict):
        changes["gear"] = {"id": changes["gear"].get("id")}
    if changes:
        try:
            intervals.update_activity(new_id, changes)
        except Exception as exc:
            log.warning("Swap %s: %s not carried over (%s)", gid, sorted(changes), type(exc).__name__)
    plan.update(outcome="swapped", new_id=new_id, carried=sorted(changes))
    if not _delete(intervals, store, iid, new_id):
        plan["outcome"] = "delete_failed"
        return plan
    if copy.get("paired_event_id") and not intervals.activity(new_id).get("paired_event_id"):
        try:
            intervals.update_activity(new_id, {"paired_event_id": copy["paired_event_id"]})
        except Exception as exc:
            log.warning("Swap %s: the workout %s not paired again (%s)", gid, copy["paired_event_id"],
                        type(exc).__name__)
    log.info("Swapped %s: Garmin's copy %s -> the original %s (carried %s)", gid, iid, new_id, sorted(changes))
    return plan


def _delete(intervals: Any, store: Any, iid: str, new_id: str) -> bool:
    error = None
    try:
        intervals.delete_activity(iid)
    except Exception as exc:
        if getattr(getattr(exc, "response", None), "status_code", None) != 404:   # 404: gone already
            error = type(exc).__name__
    if error is not None:
        log.warning("Swap: Garmin's copy %s not deleted (%s); both stay, tried again next run", iid, error)
        store.set_meta(DELETE_KEY + iid, new_id)
        return False
    if store.get_meta(DELETE_KEY + iid):
        store.set_meta(DELETE_KEY + iid, "")
    return True


def retry_deletes(intervals: Any, store: Any) -> int:
    """Copies whose delete failed: again, once their original is still there."""
    done = 0
    for key, new_id in store.meta_items(DELETE_KEY + "*"):
        iid = key[len(DELETE_KEY):]
        if not new_id:
            continue
        try:
            intervals.activity(new_id)                     # the original must still be in Intervals
        except Exception:
            continue
        done += _delete(intervals, store, iid, new_id)
    return done
