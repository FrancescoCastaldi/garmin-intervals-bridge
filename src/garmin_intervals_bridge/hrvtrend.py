"""Experimental: training advice from the HRV trend, as in the HRV-guided training studies.

ImReady4 judges each morning's single values. The studies that guided training by HRV
mostly judged a week: the 7-day mean of ln(rMSSD) against an individual normal range
(baseline mean ± 0.5 SD, the "smallest worthwhile change"; Plews et al. 2012/2013,
Vesterinen et al. 2016, Javaloyes et al. 2019), with a quicker rule for two bad days in a
row (Kiviniemi et al. 2007) and the night's heart rate as a second marker (Nuuttila et al.
2022 used nocturnal recordings). This module writes such an advice into a field of its
own, `HRVTrendAdvice` (same values as ImReady4's `TrainingAdvice`: 1 Rest, 2 LIT,
3 Planned, 4 HIT), so both can be compared side by side; `HRVTrendCV` holds the week's
coefficient of variation of ln(rMSSD) (Plews et al. 2012).

For a day D, with x = ln(hrv) and h = avgSleepingHR (the night's average heart rate):

  week      D-6 … D; at least 5 of the 7 nights measured, D itself included
  baseline  the 60 days before the week (D-66 … D-7); at least 30 nights measured
  band      baseline mean ± 0.5 · baseline SD (population SD), for x and for h
  rules, first match:
    1 Rest     today's x below −1 SD and today's h above +2 SD   (acute: HRV down, night HR well up)
    1 Rest     week x below the band and week h above its band   (trend: HRV down, night HR up)
    2 LIT      week x below the band                             (trend: HRV below normal)
    2 LIT      today's and yesterday's x below −1 SD, or today's below −2 SD   (acute drop)
    3 Planned  otherwise – within the normal range the studies trained as planned, hard
               sessions included; they have no "more than planned" state, so HIT is not used.
               Week x above and week h below their bands stays Planned with a note: Vesterinen
               counted it as outside the band, but mostly it is good form, and the evidence for
               parasympathetic saturation is mixed

Without enough night heart rate, the rules that need it are left out (HRV alone).
This is an experiment: none of these thresholds has been validated for this exact
combination, and the night values of a watch differ from the morning readings most
studies used.
"""
from __future__ import annotations

import logging
import math
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from typing import Any

from . import advice as advice_rules

log = logging.getLogger(__name__)

FIELD = "HRVTrendAdvice"
CV_FIELD = "HRVTrendCV"
VALUES = {1: "Rest", 2: "LIT", 3: "Planned", 4: "HIT"}
WEEK_DAYS, WEEK_MIN = 7, 5
BASELINE_DAYS, BASELINE_MIN = 60, 30
SWC = 0.5                                   # the band: baseline mean ± 0.5 SD
FIELD_ITEMS = {
    FIELD: {"name": "HRV Trend Advice", "type": "INPUT_FIELD", "visibility": "PRIVATE",
            "description": "Experimental training advice from the 7-day HRV trend, by garmin-intervals-bridge.",
            "content": {"code": FIELD, "name": "HRV Trend Advice", "type": "select",
                        "options": [{"value": v, "text": t} for v, t in VALUES.items()]}},
    CV_FIELD: {"name": "HRV Trend CV", "type": "INPUT_FIELD", "visibility": "PRIVATE",
               "description": "7-day coefficient of variation of ln(rMSSD), by garmin-intervals-bridge.",
               "content": {"code": CV_FIELD, "name": "HRV Trend CV", "type": "numeric", "units": "%"}},
}


def _ln(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        return None
    return math.log(value)


def _hr(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        return None
    return float(value)


def _mean_sd(values: list[float]) -> tuple[float, float]:
    m = sum(values) / len(values)
    return m, math.sqrt(sum((v - m) ** 2 for v in values) / len(values))


@dataclass
class Trend:
    day: str
    value: int | None = None
    advice: str | None = None
    rule: str | None = None
    reason: str | None = None
    waiting: bool = False
    hrv_week_ms: float | None = None        # geometric mean of the week's rMSSD
    hrv_band_ms: list | None = None         # the normal range, in ms
    cv_percent: float | None = None
    night_hr_week: float | None = None
    night_hr_band: list | None = None
    z_today: float | None = None
    z_yesterday: float | None = None
    z_night_hr_today: float | None = None

    def explain(self) -> dict:
        out = {k: v for k, v in asdict(self).items() if v not in (None, False)}
        for k in ("z_today", "z_yesterday", "z_night_hr_today"):
            if k in out:
                out[k] = round(out[k], 2)
        return out


def advise(records: list[dict], day: date, today: date | None = None) -> Trend:
    by_day = {str(r.get("id")): r for r in records}

    def series(key, convert, days):
        return [convert((by_day.get((day - timedelta(days=n)).isoformat()) or {}).get(key)) for n in days]

    out = Trend(day=day.isoformat())
    pending = today is None or day >= today
    week_x = series("hrv", _ln, range(WEEK_DAYS))
    if week_x[0] is None:
        out.reason, out.waiting = "no hrv for the day" + (" yet" if pending else ""), pending
        return out
    base_x = [v for v in series("hrv", _ln, range(WEEK_DAYS, WEEK_DAYS + BASELINE_DAYS)) if v is not None]
    measured = [v for v in week_x if v is not None]
    if len(measured) < WEEK_MIN:
        out.reason = f"only {len(measured)} of the last {WEEK_DAYS} nights measured, {WEEK_MIN} needed"
        return out
    if len(base_x) < BASELINE_MIN:
        out.reason = f"not enough history: {len(base_x)} nights in the 60-day baseline, {BASELINE_MIN} needed"
        return out
    mean_x, sd_x = _mean_sd(base_x)
    if sd_x == 0:
        out.reason = "no variability in the baseline"
        return out
    week_mean, week_sd = _mean_sd(measured)
    low, high = mean_x - SWC * sd_x, mean_x + SWC * sd_x
    out.hrv_week_ms = round(math.exp(week_mean), 1)
    out.hrv_band_ms = [round(math.exp(low), 1), round(math.exp(high), 1)]
    out.cv_percent = round(100 * week_sd / week_mean, 1) if week_mean > 0 else None
    out.z_today = (week_x[0] - mean_x) / sd_x
    out.z_yesterday = (week_x[1] - mean_x) / sd_x if week_x[1] is not None else None

    week_h = [v for v in series("avgSleepingHR", _hr, range(WEEK_DAYS)) if v is not None]
    base_h = [v for v in series("avgSleepingHR", _hr, range(WEEK_DAYS, WEEK_DAYS + BASELINE_DAYS)) if v is not None]
    hr_trend = None
    if len(week_h) >= WEEK_MIN and len(base_h) >= BASELINE_MIN and (hr := _mean_sd(base_h))[1] > 0:
        mean_h, sd_h = hr
        hr_week = sum(week_h) / len(week_h)
        out.night_hr_week = round(hr_week, 1)
        out.night_hr_band = [round(mean_h - SWC * sd_h, 1), round(mean_h + SWC * sd_h, 1)]
        hr_trend = "above" if hr_week > mean_h + SWC * sd_h else "below" if hr_week < mean_h - SWC * sd_h else "normal"
        today_h = _hr((by_day.get(day.isoformat()) or {}).get("avgSleepingHR"))
        if today_h is not None:
            out.z_night_hr_today = (today_h - mean_h) / sd_h
    hrv_trend = "below" if week_mean < low else "above" if week_mean > high else "normal"

    if out.z_today < -1 and out.z_night_hr_today is not None and out.z_night_hr_today > 2:
        out.value, out.rule = 1, "acute: HRV down and night heart rate well up"
    elif hrv_trend == "below" and hr_trend == "above":
        out.value, out.rule = 1, "trend: HRV below its normal range, night heart rate above"
    elif hrv_trend == "below":
        out.value, out.rule = 2, "trend: HRV below its normal range"
    elif out.z_today < -2 or (out.z_today < -1 and out.z_yesterday is not None and out.z_yesterday < -1):
        out.value, out.rule = 2, "acute: HRV well below normal" + (" two nights in a row" if out.z_today >= -2 else "")
    elif hrv_trend == "above" and hr_trend == "below":
        # Vesterinen's rule treats this side as "outside the band" too; the evidence for parasympathetic
        # saturation is mixed (Le Meur 2013 vs. later meta-analysis), and mostly it is plain good form
        out.value, out.rule = 3, ("HRV above and night heart rate below normal: rested - after hard weeks, "
                                  "also a possible sign of saturation")
    else:
        out.value, out.rule = 3, "within the normal range: train as planned"
    out.advice = VALUES[out.value]
    return out


# ---- the fields ----

def ensure_fields(intervals: Any, *, apply: bool) -> list[str]:
    """Create the two fields if missing (on apply); an existing field of another kind is refused."""
    existing = {(item.get("content") or {}).get("code"): item for item in intervals.custom_items()
                if item.get("type") == "INPUT_FIELD"}
    missing = []
    for code, spec in FIELD_ITEMS.items():
        if code in existing:
            if (existing[code].get("content") or {}).get("type") != spec["content"]["type"]:
                raise ValueError(f"'{code}' exists but is not a {spec['content']['type']} field; refusing to write")
            continue
        missing.append(code)
        if apply:
            intervals.create_custom_item(spec)
            log.info("HRV trend: created the wellness field %s", code)
    return missing


def history(intervals: Any, start: date, end: date) -> list[dict]:
    return intervals.wellness_range(start - timedelta(days=WEEK_DAYS + BASELINE_DAYS), end,
                                    cols=["hrv", "avgSleepingHR", FIELD, CV_FIELD, "TrainingAdvice", "locked"])


def apply_day(intervals: Any, day: date, trend: Trend, record: dict, *, apply: bool, overwrite: bool = False) -> dict:
    extra = {CV_FIELD: trend.cv_percent} if trend.cv_percent is not None else None
    action = advice_rules.write(intervals, day, FIELD, trend.value, record, apply=apply, waiting=trend.waiting,
                                overwrite=overwrite, extra=extra)
    return {**trend.explain(), "intervals_value": advice_rules.stored_value(record, FIELD),
            "imready4": advice_rules.stored_value(record, "TrainingAdvice"), "action": action}


# ---- the command ----

def run_days(intervals: Any, start: date, end: date, *, apply: bool, overwrite: bool = False,
             compare: bool = False, details: bool = False, today: date | None = None) -> dict:
    created = ensure_fields(intervals, apply=apply)
    records = history(intervals, start, end)
    by_day = {str(r.get("id")): r for r in records}
    results = []
    day = start
    while day <= end:
        record = by_day.get(day.isoformat(), {"id": day.isoformat()})
        results.append(apply_day(intervals, day, advise(records, day, today), record, apply=apply,
                                 overwrite=overwrite))
        day += timedelta(days=1)
    out: dict = {"apply": apply, "from": start.isoformat(), "to": end.isoformat()}
    if created:
        out["fields"] = {"created" if apply else "would create": created}
    if compare:
        out["comparison"] = comparison(results)
    out["actions"] = dict(sorted(Counter(r["action"] for r in results).items()))
    if details or not compare:
        out["days"] = results
    return out


def comparison(results: list[dict]) -> dict:
    """The trend advice next to ImReady4's TrainingAdvice: agreement, both distributions, stability."""
    both = [r for r in results if r.get("value") is not None and r.get("imready4") is not None]

    def changes(values):
        values = [v for v in values if v is not None]
        return sum(1 for a, b in zip(values, values[1:]) if a != b)

    trend = [r.get("value") for r in results]
    imready = [r.get("imready4") for r in results]
    return {"days_with_both": len(both),
            "same_advice": sum(1 for r in both if r["value"] == r["imready4"]),
            "trend": {VALUES[k]: v for k, v in sorted(Counter(v for v in trend if v).items())},
            "imready4": {VALUES[k]: v for k, v in sorted(Counter(v for v in imready if v).items())},
            "changes_day_to_day": {"trend": changes(trend), "imready4": changes(imready)},
            "pairs": {f"{VALUES[a]}/{VALUES[b]}": n for (a, b), n in sorted(
                Counter((r["imready4"], r["value"]) for r in both).items())},
            "pairs_note": "ImReady4/trend"}


# ---- the scheduled runs ----

def due(settings: Any, store: Any, day: date, now_ts: float, *, apply: bool) -> bool:
    return advice_rules.due(getattr(settings, "hrvtrend", False), store, "hrvtrend", day, now_ts, apply=apply)


def _describe(result: dict) -> str:
    if result["action"] in ("written", "would write", "same", "keep"):
        return f"{result['advice']} ({result['rule']}; {result['action']})"
    return result["action"] + (f" ({result['reason']})" if result.get("reason") else "")


def morning(settings: Any, intervals: Any, store: Any, day: date, now: datetime, *, apply: bool) -> dict:
    def compute() -> dict:
        ensure_fields(intervals, apply=apply)
        records = history(intervals, day, day)
        record = next((r for r in records if str(r.get("id")) == day.isoformat()), {"id": day.isoformat()})
        return apply_day(intervals, day, advise(records, day, day), record, apply=apply)

    result = advice_rules.morning(store, "hrvtrend", "HRV trend advice", day, now, apply=apply, compute=compute,
                                  describe=_describe)
    return {k: result.get(k) for k in ("action", "value", "advice", "rule", "cv_percent", "reason")
            if result.get(k) is not None}
