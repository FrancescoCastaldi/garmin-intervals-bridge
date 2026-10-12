"""Daily health run: probe both services and look for errors that would otherwise stay silent.

Most failures are handled on purpose and do not end a run: an activity that
fails gets a backoff, a Garmin endpoint that errors is noted in the raw
archive, a field Garmin stops delivering simply stops appearing. This run
turns those into one daily finding list. Exit 2 (alert mail) only for
findings that need a human; everything else is reported with exit 0.

Findings that alert
  - a service failing longer than BRIDGE_STALE_HOURS (probe)
  - no scheduled run recorded for 3 hours (timers dead, unit broken)
  - a Garmin wellness endpoint erroring on each of the last 3 archived days
  - a wellness field Garmin delivered on most of the previous week but on none
    of the last 2 finished days (a changed response shape, a stopped sensor);
    what the athlete logs or starts (weight, food, drink, Health Snapshots)
    is left out, a day without it is a day without logging
  - activities failed twice or more, or uploads pending reconciliation
"""
from __future__ import annotations

import glob
import json
import logging
import os
import time
from datetime import date, datetime, timedelta
from typing import Any

from .mapping import map_wellness

log = logging.getLogger(__name__)

STALE_RUN_HOURS = 3
ENDPOINT_ERROR_DAYS = 3
DROPOUT_BASELINE_DAYS = 7
DROPOUT_MIN_BASELINE = 5
DROPOUT_MISSING_DAYS = 2
# Raw sources that exist only when the athlete logs something (scale, food and drink log) or
# starts it (Health Snapshot): their absence is no dropout.
USER_LOGGED = ("body_composition", "nutrition", "hydration", "health_snapshots")


def _today(settings: Any) -> date:
    tz = getattr(settings, "timezone", None)
    return datetime.now(tz).date() if tz else date.today()


def probe(settings: Any, garmin: Any, intervals: Any, store: Any, *, now: float | None = None) -> dict:
    now = now or time.time()
    stale_after = settings.stale_hours * 3600
    report: dict[str, Any] = {"stale_hours": settings.stale_hours, "services": {}, "findings": [], "exit": 0}
    for name, call in (("garmin", lambda: garmin.login(interactive=False)),
                       ("intervals", lambda: intervals.activities(_today(settings) - timedelta(days=1),
                                                                   _today(settings), fields=["id"], limit=1))):
        entry: dict[str, Any] = {}
        try:
            call()
            store.set_meta(f"{name}_last_ok")
            entry.update(ok=True, last_ok_age_hours=0.0)
        except Exception as exc:                       # every failure counts here
            last_ok = store.meta_updated(f"{name}_last_ok")
            age = (now - last_ok) if last_ok else None
            entry.update(ok=False, error=f"{type(exc).__name__}: {exc}"[:200],
                         last_ok_age_hours=round(age / 3600, 1) if age is not None else None)
            if age is None or age > stale_after:
                entry["stale"] = True
                report["findings"].append({"alert": True, "what": f"{name} unreachable",
                                           "detail": f"no success for {entry['last_ok_age_hours'] or 'ever'} h "
                                                     f"(limit {settings.stale_hours} h): {entry['error']}"})
            else:
                log.warning("%s probe failed (%s); last success %.1f h ago, inside the %d h window",
                            name, type(exc).__name__, age / 3600, settings.stale_hours)
        report["services"][name] = entry
    report["findings"] += silent_error_checks(settings, store, now=now)
    if str(getattr(settings, "timezone_source", "")).startswith("fallback"):
        report["findings"].append({"alert": False, "what": "day boundary in UTC",
                                   "detail": "no BRIDGE_TIMEZONE and no time zone on this computer (a container?): "
                                             "set BRIDGE_TIMEZONE to yours, e.g. Europe/London"})
    for f in report["findings"]:
        (log.error if f["alert"] else log.warning)("%s: %s", f["what"], f["detail"])
    if any(f["alert"] for f in report["findings"]):
        report["exit"] = 2
    return report


def silent_error_checks(settings: Any, store: Any, *, now: float | None = None) -> list[dict]:
    now = now or time.time()
    findings: list[dict] = []

    # 1) Are the timers alive at all?
    for command, label in (("watch", "one-minute watch"), ("sync", "30-minute sync")):
        runs = store.recent_runs(STALE_RUN_HOURS, command)
        if not runs and store.recent_runs(24 * 14, command):      # ran before, not any more
            findings.append({"alert": True, "what": f"{label} not running",
                             "detail": f"no {command} run recorded in the last {STALE_RUN_HOURS} h"})

    # 2) Activities that keep failing, uploads waiting for a human
    failed = [f for f in store.failed_activities() if (f.get("attempts") or 0) >= 2]
    if failed:
        findings.append({"alert": True, "what": f"{len(failed)} activities failing repeatedly",
                         "detail": "; ".join(f"{f['garmin_id']}: {f['error']}" for f in failed[:5])})
    pending = store.pending_activities()
    if pending:
        findings.append({"alert": True, "what": f"{len(pending)} uploads pending reconciliation",
                         "detail": ", ".join(pending[:10])})

    # 3) Garmin wellness endpoints erroring day after day; fields that silently stopped
    raw_dir = os.path.join(str(settings.data_dir), "raw")
    days = sorted(glob.glob(os.path.join(raw_dir, "2*.json")))[-DROPOUT_BASELINE_DAYS - DROPOUT_MISSING_DAYS - 1:]
    snapshots = []
    for path in days:
        try:
            with open(path, encoding="utf-8") as fh:
                snapshots.append(json.load(fh))
        except (OSError, ValueError):
            continue
    if len(snapshots) >= ENDPOINT_ERROR_DAYS:
        recent = snapshots[-ENDPOINT_ERROR_DAYS:]
        for key in sorted({k for s in recent for k in (s.get("errors") or {})}):
            if all(key in (s.get("errors") or {}) for s in recent):
                findings.append({"alert": True, "what": f"Garmin endpoint '{key}' failing",
                                 "detail": f"error on each of the last {ENDPOINT_ERROR_DAYS} archived days: "
                                           f"{recent[-1]['errors'][key]}"})
    today = datetime.fromtimestamp(now, settings.timezone).date()
    # today's file holds only what has been read so far (the night, before the scale and the evening)
    finished = [s for s in snapshots if s["date"] < today.isoformat()][-DROPOUT_BASELINE_DAYS - DROPOUT_MISSING_DAYS:]
    if len(finished) > DROPOUT_MISSING_DAYS:
        profile = getattr(settings, "wellness_profile", "recommended")

        def fields_of(snapshot: dict) -> set[str]:
            day = date.fromisoformat(snapshot["date"])
            data = {k: v for k, v in (snapshot.get("data") or {}).items() if k not in USER_LOGGED}
            native, custom = map_wellness({**snapshot, "data": data}, day, today, profile)
            return set(native) | set(custom)

        baseline, latest = finished[:-DROPOUT_MISSING_DAYS], finished[-DROPOUT_MISSING_DAYS:]
        counts: dict[str, int] = {}
        for s in baseline:
            for f in fields_of(s):
                counts[f] = counts.get(f, 0) + 1
        present_lately = set().union(*(fields_of(s) for s in latest))
        dropped = sorted(f for f, c in counts.items()
                         if c >= min(DROPOUT_MIN_BASELINE, len(baseline)) and f not in present_lately)
        if dropped:
            findings.append({"alert": True, "what": f"{len(dropped)} wellness fields stopped arriving from Garmin",
                             "detail": f"present on most of the previous {len(baseline)} days, absent on the last "
                                       f"{DROPOUT_MISSING_DAYS}: {', '.join(dropped)}"})

    # 4) Informational: what the last day of runs did (no alert)
    day_runs = store.recent_runs(24)
    if day_runs:
        enriched = sum((r["metrics"].get("enriched") or r["metrics"].get("activities", {}).get("enriched") or 0)
                       for r in day_runs)
        writes = sum((r["metrics"].get("wellness", {}) or {}).get("writes", 0)
                     + (r["metrics"].get("writes", 0) if r["command"] == "backfill" else 0) for r in day_runs)
        requests = sum(r["metrics"].get("garmin_requests") or 0 for r in day_runs)
        findings.append({"alert": False, "what": "last 24 h",
                         "detail": f"{len(day_runs)} runs, {enriched} activities enriched, "
                                   f"{writes} wellness days written, {requests} Garmin requests"})
    return findings
