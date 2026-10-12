"""ImReady4-compatible training advice, written to Intervals' `TrainingAdvice` field.

ImReady4 is Iñigo Tolosa's MATLAB script for HRV-guided training, built on the
method and chart MedTechCD reconstructed from the ithlete training guide
(forum.intervals.icu/t/25778). Its users run it by hand every morning in MATLAB
Mobile. This module is an independent implementation of its published scoring,
version 4.43, so the bridge can do that step once the night has synced:

  input     Intervals' wellness `hrv` (rMSSD, ms) and `restingHR` (bpm), the same
            fields ImReady4 reads; one row per wellness record, newest first,
            within the 90 days up to the day
  transform HRV as 20·ln(rMSSD); resting HR as it is
  reference the 30 rows ending at the day (the day included), missing values
            left out; mean and population standard deviation (MATLAB std(x, 1))
  z-scores  (value − mean) / standard deviation, for HRV and for resting HR
  zones     ImReady4's getScore(), boundaries exactly as published
  field     HIT → 4, Normal → 3 (Planned), LIT / LIT! → 2, Rest / REST! → 1

Where this deliberately differs from the script: a day without its own HRV and
resting HR, with fewer than MIN_VALID values in the reference, or without any
variability gets no advice at all (the script sends an empty value then), and a
zero or negative value counts as missing, never as a measurement.

Validated against the reference results that come with the .NET translation of
the script (github.com/Creastoff/ImReady4.Net, produced with the original code):
30 of 30 days agree. No MATLAB code is copied here.
"""
from __future__ import annotations

import json
import logging
import math
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

log = logging.getLogger(__name__)

FIELD = "TrainingAdvice"
WINDOW_ROWS = 30                 # the reference: 30 wellness rows ending at the day
HISTORY_DAYS = 90                # the rows are taken from the 90 days up to the day, as the script does
MIN_VALID = 14                   # fewer measurements in the reference: no advice (the bridge's own guard)
FIELD_VALUES = {1: "Rest", 2: "LIT", 3: "Planned", 4: "HIT"}
CODE_TO_VALUE = {1: 4, 2: 2, 3: 2, 4: 3, 5: 1, 6: 1}
ZONES = {
    (1, "hit"): "HIT – ready for intensive training",
    (2, "lit"): "LIT – low intensity training",
    (3, "acute"): "LIT! – keep calm, acute fatigue signs (resting HR very low)",
    (4, "normal"): "Normal – go on, train as planned",
    (5, "rest"): "Rest – time to recover, avoid overtraining",
    (3, "recovering"): "LIT – recovery is not complete",
    (6, "illness"): "REST! – be careful, illness or stress detected",
}
RETRY_MINUTES = (10, 30, 60, 120, 240)
GIVE_UP_HOUR = 22                # no complete night by then: no advice for the day


# ---- the calculation ----

def _hrv(value: Any) -> float | None:
    """rMSSD on ImReady4's scale (20·ln); a non-positive or non-number value is missing."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        return None
    return 20 * math.log(value)


def _rhr(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        return None
    return float(value)


@dataclass
class Reference:
    valid: int = 0
    mean: float | None = None
    sd: float | None = None
    z: float | None = None


def reference(values_newest_first: list[float | None]) -> Reference:
    """Mean, population SD and the first value's z-score over the first WINDOW_ROWS rows."""
    window = [v for v in values_newest_first[:WINDOW_ROWS] if v is not None]
    ref = Reference(valid=len(window))
    if not window:
        return ref
    ref.mean = sum(window) / len(window)
    ref.sd = math.sqrt(sum((v - ref.mean) ** 2 for v in window) / len(window))
    first = values_newest_first[0] if values_newest_first else None
    if first is not None and ref.sd > 0:
        ref.z = (first - ref.mean) / ref.sd
    return ref


def classify(z_hrv: float, z_rhr: float) -> tuple[int, str]:
    """ImReady4 4.43 getScore(): its code (1–6) and which of its zones it was."""
    if -1 < z_rhr <= 1 and z_hrv > 1:
        return 1, "hit"
    if z_rhr <= -2 and -1 <= z_hrv < 0:
        return 2, "lit"
    if z_rhr <= -2 and z_hrv >= 0:
        return 3, "acute"
    if z_rhr < 1.7 and z_hrv >= -1:
        return 4, "normal"
    if z_hrv >= -1:
        return 2, "lit"
    if z_rhr <= -2:
        return 5, "rest"
    if z_rhr <= 1.7:
        return 3, "recovering"
    return 6, "illness"


@dataclass
class Advice:
    day: str
    value: int | None = None            # the TrainingAdvice value, 1–4
    advice: str | None = None           # Rest / LIT / Planned / HIT
    code: int | None = None             # ImReady4's own code, 1–6
    zone: str | None = None
    z_hrv: float | None = None
    z_resting_hr: float | None = None
    reason: str | None = None           # why there is no advice
    waiting: bool = False               # the day's own values are not there yet
    reference: dict = field(default_factory=dict)

    def explain(self) -> dict:
        out = {k: v for k, v in asdict(self).items() if v not in (None, {}, False)}
        for k in ("z_hrv", "z_resting_hr"):
            if k in out:
                out[k] = round(out[k], 3)
        return out


def rows_for(records: list[dict], day: date) -> list[dict]:
    """The script's rows for a day: wellness records of the 90 days up to it, newest first."""
    first = (day - timedelta(days=HISTORY_DAYS - 1)).isoformat()
    return sorted((r for r in records if first <= str(r.get("id", "")) <= day.isoformat()),
                  key=lambda r: str(r["id"]), reverse=True)


def advise(records: list[dict], day: date, today: date | None = None) -> Advice:
    """The advice for `day`; a day before `today` without its own values will not get them any more."""
    rows = rows_for(records, day)
    out = Advice(day=day.isoformat())
    pending = today is None or day >= today
    if not rows or rows[0].get("id") != day.isoformat():
        out.reason, out.waiting = "no wellness record for the day" + (" yet" if pending else ""), pending
        return out
    hrv = reference([_hrv(r.get("hrv")) for r in rows])
    rhr = reference([_rhr(r.get("restingHR")) for r in rows])
    out.reference = {"rows": min(len(rows), WINDOW_ROWS), "hrv_values": hrv.valid, "resting_hr_values": rhr.valid,
                     "hrv_typical_ms": round(math.exp(hrv.mean / 20), 1) if hrv.mean is not None else None,
                     "resting_hr_mean": round(rhr.mean, 1) if rhr.mean is not None else None,
                     "resting_hr_sd": round(rhr.sd, 2) if rhr.sd is not None else None}
    missing = [name for name, value in (("hrv", _hrv(rows[0].get("hrv"))),
                                        ("restingHR", _rhr(rows[0].get("restingHR")))) if value is None]
    if missing:
        out.reason = f"no {' and '.join(missing)} for the day" + (" yet" if pending else "")
        out.waiting = pending
        return out
    if min(hrv.valid, rhr.valid) < MIN_VALID:
        out.reason = (f"not enough history: {min(hrv.valid, rhr.valid)} of the last {WINDOW_ROWS} days measured, "
                      f"{MIN_VALID} needed")
        return out
    if hrv.z is None or rhr.z is None:
        out.reason = "no variability in the last 30 days"
        return out
    out.z_hrv, out.z_resting_hr = hrv.z, rhr.z
    out.code, kind = classify(hrv.z, rhr.z)
    out.zone = ZONES[(out.code, kind)]
    out.value = CODE_TO_VALUE[out.code]
    out.advice = FIELD_VALUES[out.value]
    return out


# ---- the field in Intervals ----

def find_field(intervals: Any) -> dict | None:
    """The athlete's TrainingAdvice wellness field, if it is the select ImReady4 writes to."""
    for item in intervals.custom_items():
        content = item.get("content") or {}
        if item.get("type") == "INPUT_FIELD" and (content.get("code") or item.get("code")) == FIELD:
            return item
    return None


def field_problem(item: dict | None) -> str | None:
    if item is None:
        return (f"Intervals has no '{FIELD}' wellness field: create it as ImReady4's guide describes "
                "(a select with 1 Rest, 2 LIT, 3 Planned, 4 HIT)")
    options = {}
    for option in (item.get("content") or {}).get("options") or []:
        try:
            options[int(float(option.get("value")))] = str(option.get("text") or "").strip().lower()
        except (TypeError, ValueError):
            continue
    expected = {v: name.lower() for v, name in FIELD_VALUES.items()}
    if any(options.get(v) != name for v, name in expected.items()):
        return f"'{FIELD}' does not offer 1 Rest, 2 LIT, 3 Planned, 4 HIT (it has {options}); nothing is written"
    return None


def stored_value(record: dict) -> int | None:
    """The day's TrainingAdvice; an empty string (the script's 'no data') counts as none."""
    value = record.get(FIELD)
    if value in (None, ""):
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


# ---- one day, with the bridge's write rules ----

def decide(advice: Advice, record: dict, *, overwrite: bool = False) -> str:
    if advice.value is None:
        return "wait" if advice.waiting else "none"
    if record.get("locked") is True:
        return "locked"
    current = stored_value(record)
    if current == advice.value:
        return "same"
    if current is not None and not overwrite:
        return "keep"
    return "write"


def apply_day(intervals: Any, day: date, advice: Advice, record: dict, *, apply: bool,
              overwrite: bool = False) -> dict:
    """Write one day's advice when the rules allow it, and read it back."""
    action = decide(advice, record, overwrite=overwrite)
    out = {**advice.explain(), "intervals_value": stored_value(record), "action": action}
    if action != "write":
        return out
    if not apply:
        out["action"] = "would write"
        return out
    intervals.write_wellness(day, {FIELD: advice.value})
    back = stored_value(intervals.wellness(day))
    if back != advice.value:
        raise RuntimeError(f"{FIELD} of {day} reads back as {back}, not {advice.value}")
    out["action"] = "written"
    return out


def history(intervals: Any, start: date, end: date) -> list[dict]:
    return intervals.wellness_range(start - timedelta(days=HISTORY_DAYS - 1), end,
                                    cols=["restingHR", "hrv", FIELD, "locked"])


# ---- the command: preview, comparison, controlled backfill ----

def run_days(intervals: Any, start: date, end: date, *, apply: bool, overwrite: bool = False,
             compare: bool = False, details: bool = False, today: date | None = None) -> dict:
    item = find_field(intervals)
    problem = field_problem(item)
    records = history(intervals, start, end)
    by_day = {str(r.get("id")): r for r in records}
    days, results = [], []
    day = start
    while day <= end:
        days.append(day)
        day += timedelta(days=1)
    for day in days:
        advice = advise(records, day, today)
        record = by_day.get(day.isoformat(), {"id": day.isoformat()})
        if problem and advice.value is not None:
            results.append({**advice.explain(), "intervals_value": stored_value(record), "action": "no field"})
            continue
        results.append(apply_day(intervals, day, advice, record, apply=apply, overwrite=overwrite))
    out: dict = {"apply": apply, "from": start.isoformat(), "to": end.isoformat()}
    if problem:
        out["field"] = problem
    if compare:
        out["comparison"] = comparison(results)
    out["actions"] = dict(sorted(Counter(r["action"] for r in results).items()))
    if details or not compare:
        out["days"] = results
    return out


def _margin(r: dict) -> float | None:
    if r.get("z_hrv") is None:
        return None
    return round(min([abs(r["z_resting_hr"] - t) for t in (-2, -1, 1, 1.7)]
                     + [abs(r["z_hrv"] - t) for t in (-1, 0, 1)]), 3)


def comparison(results: list[dict]) -> dict:
    """The computed advice against what the field already holds (e.g. from running the script)."""
    both = [r for r in results if r.get("intervals_value") is not None and r.get("value") is not None]
    agree = [r for r in both if r["value"] == r["intervals_value"]]
    return {"days_compared": len(both),
            "agree": len(agree),
            "agreement": f"{100 * len(agree) / len(both):.1f} %" if both else None,
            "only_in_intervals": sum(1 for r in results if r.get("intervals_value") is not None
                                     and r.get("value") is None),
            "mismatches": [{"day": r["day"], "intervals": r["intervals_value"], "computed": r["value"],
                            "zone": r["zone"], "z_hrv": r["z_hrv"], "z_resting_hr": r["z_resting_hr"],
                            "margin_to_zone_border": _margin(r)}
                           for r in both if r["value"] != r["intervals_value"]]}


# ---- the scheduled runs: once the night is in ----

def _state(store: Any, day: date) -> dict | None:
    raw = store.get_meta(f"imready4:{day}")
    try:
        return json.loads(raw) if raw else None
    except ValueError:
        return None


def due(settings: Any, store: Any, day: date, now_ts: float, *, apply: bool) -> bool:
    if not getattr(settings, "imready4", False) or _state(store, day):
        return False
    if not apply and store.get_meta(f"imready4:dry:{day}"):
        return False
    return now_ts >= float(store.get_meta(f"imready4:next:{day}") or 0)


def morning(settings: Any, intervals: Any, store: Any, day: date, now: datetime, *, apply: bool) -> dict:
    """Today's advice, called by wellness_check once the night's values have been read.

    Final for the day once written, kept (a value was there), skipped (day locked, no
    field, not enough history, no variability) or given up at GIVE_UP_HOUR; until then
    a day whose own HRV or resting HR is not in Intervals yet is tried again later.
    Dry runs report once a day and write nothing.
    """
    now_ts = now.timestamp()
    tries = int(store.get_meta(f"imready4:tries:{day}") or 0)

    def later(reason: str) -> dict:
        if now.hour >= GIVE_UP_HOUR:
            return final({"action": "gave up", "reason": reason})
        store.set_meta(f"imready4:tries:{day}", str(tries + 1))
        store.set_meta(f"imready4:next:{day}", str(now_ts + RETRY_MINUTES[min(tries, len(RETRY_MINUTES) - 1)] * 60))
        return {"action": "wait", "reason": reason}

    def final(result: dict) -> dict:
        if apply:
            store.set_meta(f"imready4:{day}", json.dumps({**result, "at": now.isoformat(timespec="seconds")}))
        else:
            store.set_meta(f"imready4:dry:{day}", json.dumps(result))
        return result

    problem = field_problem(find_field(intervals))
    if problem:
        log.warning("ImReady4: %s", problem)
        return final({"action": "no field", "reason": problem})
    try:
        records = history(intervals, day, day)
        advice = advise(records, day, day)
        record = next((r for r in records if str(r.get("id")) == day.isoformat()), {"id": day.isoformat()})
        result = apply_day(intervals, day, advice, record, apply=apply)
    except Exception as exc:                       # Intervals trouble: the next check tries again
        log.warning("ImReady4 advice for %s not done: %s; trying again later", day, type(exc).__name__)
        return later(type(exc).__name__)
    if result["action"] == "wait":
        log.info("ImReady4 advice for %s waits: %s", day, advice.reason)
        return later(advice.reason or "waiting")
    if result["action"] in ("written", "would write"):
        log.info("ImReady4 advice for %s: %s (%s; HRV z %+.2f, resting HR z %+.2f)%s", day, advice.advice,
                 advice.zone, advice.z_hrv, advice.z_resting_hr, "" if apply else " - dry run, not written")
    elif result["action"] == "keep":
        log.info("ImReady4 advice for %s: %s computed, the field already holds %s - kept", day, advice.advice,
                 result["intervals_value"])
    else:
        log.info("ImReady4 advice for %s: %s%s", day, result["action"],
                 f" ({advice.reason})" if advice.reason else "")
    return final({k: result.get(k) for k in ("action", "value", "advice", "code", "z_hrv", "z_resting_hr",
                                             "intervals_value", "reason") if result.get(k) is not None})
