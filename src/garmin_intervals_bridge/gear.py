"""Garmin's gear on the Intervals activities.

The official sync does not pass gear to Intervals. The bridge archives each activity's
gear with the activity anyway (the extras, no extra Garmin request), so this module
compares the archive with Intervals and writes only to Intervals; it never writes to
Garmin.

Per activity (Intervals has one gear per activity; a bike's components follow it):

  gap        Intervals has none, Garmin has one        -> Garmin's, whatever the policy
  conflict   both have one, they differ                -> by policy:
               intervals (default)  Intervals' stays: its auto-assign rules or your corrections
               evidence             Garmin's only when the record proves Intervals' wrong: that
                                    gear was not bought yet or already retired on the day, or the
                                    bike's own sensors (Di2, power meter ... by serial number,
                                    learned from the rides both sides agree on) are missing from
                                    the recording while Garmin's bike is not contradicted the
                                    same way
               garmin               Garmin's
  Intervals only                                       -> stays; nothing is ever removed

Gear is paired by name (unique after normalising) or as told (`--map`); gear Intervals
lacks is created only on request, retired gear only on top of that. After a bulk run
Intervals recalculates the totals of every gear that gained or lost an activity.
"""
from __future__ import annotations

import bisect
import glob
import json
import logging
import os
import re
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from typing import Any

from .fit import decode_fit
from .times import parse_utc

log = logging.getLogger(__name__)

POLICIES = ("intervals", "evidence", "garmin")
CONFIG_KEY = "gear:config"
MATCH_SECONDS = 180            # same start within three minutes = the same activity
SIGNATURE_MIN_RIDES = 5        # a bike's sensors are learned from at least this many agreed rides
SIGNATURE_OWN_SHARE = 0.8      # present on most of them
SIGNATURE_OTHER_SHARE = 0.1    # and on hardly any ride of another bike
RECENT_DAYS = 7                # what the scheduled runs look at
ACTIVITY_FIELDS = ["id", "type", "start_date", "start_date_local", "gear", "source", "external_id", "distance"]


# ---- names and dates ----

def _tokens(name: object) -> list[str]:
    return re.findall(r"[0-9a-zäöüß]+", str(name or "").lower())


def _compact(name: object) -> str:
    return "".join(_tokens(name))


def same_name(a: object, b: object) -> bool:
    """'Grail CF SLX 8 Di2' = 'Canyon Grail CF SLX 8 Di2', 'Zero G 95' = 'Zero G95'."""
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return False
    return "".join(ta) == "".join(tb) or set(ta) <= set(tb) or set(tb) <= set(ta)


def _day(value: object) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _garmin_gear(g: dict) -> dict:
    made = " ".join(x for x in (g.get("gearMakeName"), g.get("gearModelName")) if x and x != "Other")
    return {"uuid": str(g["uuid"]),
            "name": str(g.get("displayName") or g.get("customMakeModel") or made or str(g["uuid"])[:8]).strip(),
            "type": g.get("gearTypeName"),
            "begin": _day(g.get("dateBegin")),
            "end": _day(g.get("dateEnd")),
            "retired": str(g.get("gearStatusName") or "").lower() == "retired"}


# ---- the two sides ----

def garmin_side(store: Any, start: date | None = None, end: date | None = None) -> tuple[dict, list[dict], int]:
    """Gear and per-activity gear from the local archive: (gear by uuid, activities, without gear info)."""
    folder = store.activity_json_path("0").parent
    gear: dict[str, dict] = {}
    activities: list[dict] = []
    without = 0
    for path in glob.glob(os.path.join(str(folder), "*.json")):
        gid = os.path.basename(path)[:-5]
        if not gid.isdecimal():
            continue                                   # extras and other companions
        try:
            with open(path, encoding="utf-8") as fh:
                summary = json.load(fh)
        except (OSError, ValueError):
            continue
        dto = summary.get("summaryDTO") or {}
        local = _day(dto.get("startTimeLocal") or summary.get("startTimeLocal"))
        if local is None or (start and local < start) or (end and local > end):
            continue
        try:
            extras = json.loads(store.activity_extras_path(gid).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            extras = None
        items = extras.get("gear") if isinstance(extras, dict) else None
        if (not isinstance(extras, dict) or "gear" not in extras or "gear" in (extras.get("errors") or {})
                or (items is not None and not isinstance(items, list))):
            without += 1
            continue
        uuids = []
        for item in items or []:
            if isinstance(item, dict) and item.get("uuid"):
                record = _garmin_gear(item)
                gear.setdefault(record["uuid"], record)
                uuids.append(record["uuid"])
        started = parse_utc(summary.get("startTimeGMT") or dto.get("startTimeGMT"), garmin_gmt=True)
        activities.append({"gid": gid, "day": local, "type": (summary.get("activityTypeDTO") or {}).get("typeKey"),
                           "start": started.timestamp() if started else None,
                           "km": round(float(dto.get("distance") or 0) / 1000, 1), "gear": uuids})
    # the latest account snapshot knows current names, dates and status (and unused gear)
    snapshots = sorted(glob.glob(os.path.join(str(folder.parent), "account", "*.json")))
    if snapshots:
        try:
            with open(snapshots[-1], encoding="utf-8") as fh:
                data = json.load(fh).get("data") or {}
        except (OSError, ValueError, AttributeError):
            data = {}
        for item in data.get("gear") or []:
            if isinstance(item, dict) and item.get("uuid"):
                gear[str(item["uuid"])] = _garmin_gear(item)
    return gear, sorted(activities, key=lambda a: (a["day"], a["gid"])), without


def pair_activities(garmin_acts: list[dict], intervals_acts: list[dict]) -> dict[str, dict]:
    """Garmin id -> Intervals activity: by the id in external_id, else by start time."""
    by_id: dict[str, dict] = {}
    for a in intervals_acts:
        for number in re.findall(r"\d{6,}", str(a.get("external_id") or "")):
            by_id.setdefault(number, a)
    timed = sorted(((t.timestamp(), a) for a in intervals_acts
                    if (t := parse_utc(a.get("start_date"), garmin_gmt=True))), key=lambda x: x[0])
    keys = [k for k, _ in timed]
    pairs: dict[str, dict] = {}
    for g in garmin_acts:
        found = by_id.get(g["gid"])
        if found is None and g["start"] is not None:
            i = bisect.bisect_left(keys, g["start"] - MATCH_SECONDS)
            best = None
            while i < len(keys) and keys[i] <= g["start"] + MATCH_SECONDS:
                if best is None or abs(keys[i] - g["start"]) < abs(keys[best] - g["start"]):
                    best = i
                i += 1
            found = timed[best][1] if best is not None else None
        if found is not None:
            pairs[g["gid"]] = found
    return pairs


def pair_gear(garmin_gear: dict, intervals_gear: list[dict], explicit: list[str] | tuple = (),
              saved: dict | None = None) -> tuple[dict[str, str], dict[str, str]]:
    """Garmin uuid -> Intervals gear id, and why the others have none.

    Saved pairs (from an earlier --apply) first, then --map GARMIN=INTERVALS, then a
    unique name match among Intervals' gear (components excluded).
    """
    candidates = [g for g in intervals_gear if not g.get("component")]
    known = {str(g.get("id")) for g in intervals_gear}
    rules = []
    for item in explicit:
        if "=" not in item:
            raise ValueError(f"--map needs GARMIN=INTERVALS, got {item!r}")
        left, right = (s.strip() for s in item.split("=", 1))
        if not any(left == uuid or _compact(left) == _compact(g["name"]) for uuid, g in garmin_gear.items()):
            raise ValueError(f"--map: no Garmin gear named {left!r}")
        rules.append((left, right))
    pairs: dict[str, str] = {}
    why: dict[str, str] = {}
    for uuid, g in garmin_gear.items():
        rule = next((r for r in rules if r[0] == uuid or _compact(r[0]) == _compact(g["name"])), None)
        if rule:
            hits = [c for c in candidates
                    if str(c.get("id")) == rule[1] or _compact(c.get("name")) == _compact(rule[1])]
            if len(hits) != 1:
                raise ValueError(f"--map: no single Intervals gear named {rule[1]!r}")
            pairs[uuid] = str(hits[0]["id"])
            continue
        if saved and saved.get(uuid) in known:
            pairs[uuid] = saved[uuid]
            continue
        hits = [c for c in candidates if same_name(g["name"], c.get("name"))]
        if len(hits) == 1:
            pairs[uuid] = str(hits[0]["id"])
        else:
            why[uuid] = ("ambiguous: " + ", ".join(sorted(str(c.get("name")) for c in hits))) if hits \
                else "not in Intervals"
    return pairs, why


# ---- sensors: which bike was it ----

def _sensor_set(messages: dict) -> list[list[str]]:
    """Paired devices with a serial number, the recording device itself left out."""
    found = set()
    for d in messages.get("device_info", []):
        if d.get("device_index") == "creator":
            continue
        serial = d.get("serial_number") or d.get("ant_device_number")
        if not serial:
            continue
        kind = d.get("antplus_device_type") or d.get("device_type") or d.get("local_device_type")
        product = d.get("garmin_product") or d.get("product") or d.get("product_name")
        found.add((str(kind), str(d.get("manufacturer")), str(product), str(serial)))
    return sorted(list(x) for x in found)


class SensorCache:
    """Sensors per archived recording, decoded once and kept in gear-sensors.json."""

    def __init__(self, store: Any):
        self.store = store
        self.path = store.base / "gear-sensors.json"
        try:
            self.data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.data = {}
        self.dirty = False

    def get(self, gid: str) -> frozenset | None:
        if gid not in self.data:
            path = self.store.fit_path(gid)
            if not path.is_file():
                return None
            try:
                messages, _ = decode_fit(path.read_bytes())
            except Exception as exc:                      # a broken file is no evidence either way
                log.debug("Sensors of %s not readable: %s", gid, type(exc).__name__)
                return None
            self.data[gid] = _sensor_set(messages)
            self.dirty = True
        return frozenset(tuple(x) for x in self.data[gid])

    def save(self) -> None:
        if self.dirty:
            self.store.atomic_save(self.path, json.dumps(self.data, separators=(",", ":")).encode())
            self.dirty = False


def bike_signatures(agreed: dict[str, list[str]], sensors: SensorCache) -> dict[str, frozenset]:
    """Per bike, the sensors on most of its agreed rides and on hardly any ride of another bike.

    Needs two bikes with enough rides: a sensor is only a bike's own when another bike shows
    that it is not simply always there (a chest strap is on every ride).
    """
    shares: dict[str, dict] = {}
    for gear_id, gids in agreed.items():
        sets = [s for s in (sensors.get(g) for g in gids) if s]
        if len(sets) >= SIGNATURE_MIN_RIDES:
            counted = Counter(x for s in sets for x in s)
            shares[gear_id] = {x: n / len(sets) for x, n in counted.items()}
    if len(shares) < 2:
        return {}
    signatures = {}
    for gear_id, own in shares.items():
        marks = frozenset(x for x, share in own.items() if share >= SIGNATURE_OWN_SHARE
                          and all(o.get(x, 0) <= SIGNATURE_OTHER_SHARE for g, o in shares.items() if g != gear_id))
        if marks:
            signatures[gear_id] = marks
    return signatures


# ---- the plan ----

def _in_use(record: dict | None, day: date) -> bool:
    if not record:
        return True
    if record["begin"] and record["begin"].year >= 2000 and day < record["begin"]:
        return False
    return not (record["retired"] and record["end"] and day > record["end"])


def _usage_window(gear_id: str, intervals_gear: dict, garmin_by_target: dict) -> tuple[date | None, date | None]:
    """Earliest anyone says the gear was bought, latest anyone says it was retired."""
    g = intervals_gear.get(gear_id) or {}
    begins = [_day(g.get("purchased"))] + [r["begin"] for r in garmin_by_target.get(gear_id, [])]
    begins = [b for b in begins if b and b.year >= 2000]
    ends = [_day(g.get("retired"))] + [r["end"] for r in garmin_by_target.get(gear_id, []) if r["retired"]]
    ends = [e for e in ends if e]
    retired_everywhere = bool(g.get("retired")) and all(r["retired"] for r in garmin_by_target.get(gear_id, []))
    return (min(begins) if begins else None), (max(ends) if ends and retired_everywhere else None)


def _choose(uuids: list[str], pairs: dict[str, str], garmin_gear: dict, sport: str | None) -> str | None:
    """Garmin may list shoes and a bike for one activity; Intervals takes one."""
    mapped = [u for u in uuids if u in pairs] or uuids
    if len(mapped) <= 1:
        return mapped[0] if mapped else None
    want = "Bike" if re.search(r"cycl|bik|ride", sport or "") else "Shoes"
    fitting = [u for u in mapped if (garmin_gear.get(u) or {}).get("type") == want]
    return fitting[0] if len(fitting) == 1 else None


def build_plan(store: Any, intervals: Any, *, start: date | None = None, end: date | None = None,
               policy: str = "intervals", explicit: list[str] | tuple = (), create: bool = False,
               include_retired: bool = False, config: dict | None = None,
               sensors: SensorCache | None = None) -> dict:
    if policy not in POLICIES:
        raise ValueError(f"policy must be one of {', '.join(POLICIES)}")
    garmin_gear, garmin_acts, without_info = garmin_side(store, start, end)
    if not garmin_acts:
        return {"policy": policy, "activities": 0, "without_gear_info": without_info, "assign": [], "kept": [],
                "create": [], "pairs": {}, "unpaired": {}, "intervals_only": {}, "skipped": {}, "touched": [],
                "signatures": {}}
    first, last = garmin_acts[0]["day"], garmin_acts[-1]["day"]
    intervals_gear_list = intervals.gear()
    intervals_gear = {str(g["id"]): g for g in intervals_gear_list}
    remote = intervals.activities(first - timedelta(days=1), last + timedelta(days=1), fields=ACTIVITY_FIELDS)
    matched = pair_activities(garmin_acts, remote)
    pairs, why = pair_gear(garmin_gear, intervals_gear_list, explicit, (config or {}).get("map"))

    # gear to create: unpaired Garmin gear (used in the window or listed in the account snapshot)
    to_create = []
    if create:
        used_by = defaultdict(list)
        for a in garmin_acts:
            for u in a["gear"]:
                used_by[u].append(a["type"] or "")
        for uuid in sorted(why):
            g = garmin_gear[uuid]
            if why[uuid] != "not in Intervals" or (g["retired"] and not include_retired):
                continue
            to_create.append({"uuid": uuid, "payload": _new_gear(g, used_by.get(uuid, []))})
        for c in to_create:
            pairs[c["uuid"]] = f"new:{c['uuid']}"
            why.pop(c["uuid"], None)

    garmin_by_target = defaultdict(list)
    for uuid, target in pairs.items():
        garmin_by_target[target].append(garmin_gear[uuid])

    rows = []
    for a in garmin_acts:
        r = matched.get(a["gid"])
        current = str(((r or {}).get("gear") or {}).get("id") or "") or None
        rows.append((a, r, current, _choose(a["gear"], pairs, garmin_gear, a["type"])))

    signatures: dict[str, frozenset] = {}
    if policy == "evidence":
        if config and config.get("signatures") is not None:
            signatures = {k: frozenset(tuple(x) for x in v) for k, v in config["signatures"].items()}
        else:
            bikes = {k for k, g in intervals_gear.items() if g.get("type") == "Bike"}
            agreed = defaultdict(list)
            for a, r, current, chosen in rows:
                if chosen and current and pairs.get(chosen) == current and current in bikes:
                    agreed[current].append(a["gid"])
            signatures = bike_signatures(agreed, sensors) if sensors else {}

    assign, kept, skipped, intervals_only = [], [], Counter(), Counter()
    for a, r, current, chosen in rows:
        if r is None:
            skipped["no Intervals activity"] += 1
            continue
        target = pairs.get(chosen) if chosen else None
        if not chosen:
            if current:
                intervals_only[(intervals_gear.get(current) or {}).get("name", current)] += 1
            elif len(a["gear"]) > 1:
                skipped["several Garmin gear, none fits the sport"] += 1
            continue
        if not target:
            skipped[f"Garmin gear {garmin_gear[chosen]['name']!r}: {why.get(chosen, 'not paired')}"] += 1
            continue
        if current == target:
            continue
        if str(r.get("source") or "").upper() == "STRAVA":
            skipped["Strava activity (Intervals does not let it be changed)"] += 1
            continue
        if not _in_use(garmin_gear[chosen], a["day"]):
            skipped["Garmin's gear not in use on that day"] += 1
            continue
        row = {"gid": a["gid"], "intervals_id": str(r["id"]), "day": a["day"].isoformat(), "type": a["type"],
               "km": a["km"], "garmin": garmin_gear[chosen]["name"], "target": target, "uuid": chosen}
        if not current:
            assign.append({**row, "reason": "gap"})
            continue
        row["intervals"] = (intervals_gear.get(current) or {}).get("name", current)
        row["replaces"] = current
        if policy == "garmin":
            assign.append({**row, "reason": "Garmin preferred"})
            continue
        proof = None
        begin, ended = _usage_window(current, intervals_gear, garmin_by_target)
        if begin and a["day"] < begin:
            proof = f"{row['intervals']} not bought until {begin.isoformat()}"
        elif ended and a["day"] > ended:
            proof = f"{row['intervals']} retired on {ended.isoformat()}"
        elif policy == "evidence" and signatures.get(current) and sensors:
            seen = sensors.get(a["gid"])
            if seen and not signatures[current] & seen and (
                    not signatures.get(target) or signatures[target] & seen):
                proof = f"none of {row['intervals']}'s own sensors in the recording"
        if policy == "evidence" and proof:
            assign.append({**row, "reason": f"evidence: {proof}"})
        else:
            kept.append({**row, "proof": proof})
    if sensors:
        sensors.save()
    touched = sorted({x["target"] for x in assign} | {x["replaces"] for x in assign if x.get("replaces")})
    return {"policy": policy, "activities": len(garmin_acts), "without_gear_info": without_info,
            "window": [first.isoformat(), last.isoformat()], "assign": assign, "kept": kept, "create": to_create,
            "pairs": pairs, "unpaired": why, "intervals_only": dict(intervals_only), "skipped": dict(skipped),
            "touched": touched, "signatures": {k: sorted(list(x) for x in v) for k, v in signatures.items()},
            "_names": {**{k: g.get("name") for k, g in intervals_gear.items()},
                       **{f"new:{c['uuid']}": c["payload"]["name"] for c in to_create}},
            "_garmin": {u: g["name"] for u, g in garmin_gear.items()}}


def _new_gear(g: dict, sports: list[str]) -> dict:
    kind = {"bike": "Bike", "shoes": "Shoes"}.get(str(g["type"] or "").lower())
    if kind is None:
        joined = " ".join(sports)
        kind = ("Skis" if "ski" in joined else "Snowboard" if "snowboard" in joined
                else "Boat" if re.search(r"kayak|row|paddl|canoe", joined) else "Equipment")
    payload = {"name": g["name"], "type": kind}
    if g["begin"] and g["begin"].year >= 2000:
        payload["purchased"] = g["begin"].isoformat()
    if g["retired"] and g["end"]:
        payload["retired"] = g["end"].isoformat()
    return payload


# ---- writing ----

def apply_plan(plan: dict, intervals: Any, *, recalc: bool = True) -> dict:
    ids: dict[str, str] = {}
    created = []
    for c in plan["create"]:
        made = intervals.create_gear(c["payload"])
        ids[f"new:{c['uuid']}"] = str(made["id"])
        created.append(c["payload"]["name"])
        log.info("Gear created in Intervals: %s (%s)", c["payload"]["name"], c["payload"]["type"])
    written, failed, touched = 0, 0, set()
    for row in plan["assign"]:
        target = ids.get(row["target"], row["target"])
        if target.startswith("new:"):
            continue
        try:
            intervals.set_activity_gear(row["intervals_id"], target)
        except Exception as exc:
            failed += 1
            log.warning("Gear of %s not written: %s", row["intervals_id"], type(exc).__name__)
            continue
        written += 1
        touched.add(target)
        if row.get("replaces"):
            touched.add(row["replaces"])
    recalculated = []
    if recalc:
        for gear_id in sorted(touched):
            try:
                intervals.recalc_gear(gear_id)
                recalculated.append(gear_id)
            except Exception as exc:
                log.warning("Totals of gear %s not recalculated: %s", gear_id, type(exc).__name__)
    return {"created": created, "written": written, "failed": failed, "recalculated": len(recalculated),
            "ids": ids}


def report(plan: dict, details: bool = False) -> dict:
    """What a person reads: counts by gear and reason, the single activities on request."""
    names = plan.get("_names", {})

    def grouped(rows: list[dict], with_reason: bool) -> list[dict]:
        groups: dict[tuple, dict] = {}
        for r in rows:
            reason = r.get("reason") if with_reason else (r.get("proof") or "no proof")
            key = (r["garmin"], names.get(r["target"], r["target"]), r.get("intervals"), r["type"], reason)
            g = groups.setdefault(key, {"garmin": key[0], "to": key[1], "intervals_has": key[2], "sport": key[3],
                                        "reason": key[4], "count": 0, "km": 0.0, "from": r["day"], "until": r["day"]})
            g["count"] += 1
            g["km"] = round(g["km"] + (r["km"] or 0), 1)
            g["from"], g["until"] = min(g["from"], r["day"]), max(g["until"], r["day"])
        return sorted(groups.values(), key=lambda g: -g["count"])

    out = {"policy": plan["policy"], "window": plan.get("window"),
           "garmin_activities": plan["activities"],
           "without_gear_info": plan["without_gear_info"],
           "gear_pairs": {plan["_garmin"][u]: names.get(t, t) for u, t in sorted(plan["pairs"].items(),
                                                                                 key=lambda kv: plan["_garmin"][kv[0]])}
           if plan.get("_garmin") else {},
           "gear_unpaired": {plan["_garmin"][u]: w for u, w in plan["unpaired"].items()} if plan.get("_garmin") else {},
           "create": [c["payload"] for c in plan["create"]],
           "assign": grouped(plan["assign"], True),
           "conflicts_kept": grouped(plan["kept"], False),
           "intervals_only": plan["intervals_only"],
           "skipped": plan["skipped"]}
    if plan.get("without_gear_info"):
        out["hint"] = ("activities archived without their gear: `backfill --scope activities --from DATE "
                       "--archive-only` reads it (one Garmin request each)")
    if details:
        out["activities"] = [{k: r.get(k) for k in ("day", "type", "km", "garmin", "intervals", "reason", "proof",
                                                    "intervals_id")} | {"to": names.get(r["target"], r["target"])}
                             for r in plan["assign"] + plan["kept"]]
    return out


def save_config(store: Any, plan: dict, created_ids: dict[str, str]) -> None:
    pairs = {u: created_ids.get(t, t) for u, t in plan["pairs"].items()}
    store.set_meta(CONFIG_KEY, json.dumps({"policy": plan["policy"],
                                           "map": {u: t for u, t in pairs.items() if not t.startswith("new:")},
                                           "signatures": plan.get("signatures") or {},
                                           "since": date.today().isoformat()}))


def load_config(store: Any) -> dict | None:
    raw = store.get_meta(CONFIG_KEY)
    try:
        return json.loads(raw) if raw else None
    except ValueError:
        return None


def sync_recent(settings: Any, store: Any, intervals: Any, *, apply: bool, days: int = RECENT_DAYS,
                now: datetime | None = None) -> dict:
    """For the scheduled runs, once `gear --apply` has run: the last week, same policy, same pairs."""
    config = load_config(store)
    if not config:
        return {}
    today = (now or datetime.now(settings.timezone)).date()
    plan = build_plan(store, intervals, start=today - timedelta(days=days), end=today,
                      policy=config.get("policy", "intervals"), config=config,
                      sensors=SensorCache(store) if config.get("policy") == "evidence" else None)
    result = {"checked": plan["activities"], "to_assign": len(plan["assign"])}
    if apply and plan["assign"]:
        done = apply_plan(plan, intervals, recalc=False)       # Intervals counts each assignment itself
        result.update(written=done["written"], failed=done["failed"])
        for row in plan["assign"]:
            log.info("Gear: %s %s -> %s (%s)", row["day"], row["type"], row["garmin"], row["reason"])
    return result
