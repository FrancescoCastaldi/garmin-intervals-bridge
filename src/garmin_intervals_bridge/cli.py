"""Command line interface. NO REMOTE WRITES unless --apply is present."""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from contextlib import ExitStack
from datetime import date, datetime, timedelta
from pathlib import Path

from . import __version__, imready4
from .charts import setup_charts
from .config import Settings
from .enrich import enrich_activity, gap_report, match_activity
from .fit import compare_fit, extract_original_fit, sha256
from .garmin import DAY_ENDPOINTS, GarminBlocked, GarminSource, parse_endpoints
from .gear import SensorCache as GearSensors
from .gear import apply_plan as apply_gear_plan
from .gear import build_plan as build_gear_plan
from .gear import load_config as load_gear_config
from .gear import refresh_totals as refresh_gear_totals
from .gear import report as gear_report
from .gear import save_config as save_gear_config
from .gear import sync_recent as sync_gear_recent
from .health import probe
from .intervals import IntervalsClient
from .store import InstanceBusy, Store, single_instance
from .sync import (
    backfill_intake,
    sync_activities,
    sync_enrich,
    sync_wellness,
    sync_wellness_files,
    watch_once,
    wellness_check,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="garmin-intervals-bridge", description=__doc__)
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--verbose", action="store_true")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("login", help="Sign into Garmin once and save refreshable tokens (interactive)")
    probe = sub.add_parser("probe", help="Download and archive one untouched ORIGINAL FIT; no Intervals writes")
    probe.add_argument("--activity-id", help="Garmin Connect activity ID (default: latest)")
    cmp = sub.add_parser("compare-fit", help="Compare two local FITs by message/field inventories")
    cmp.add_argument("file_a", type=Path)
    cmp.add_argument("file_b", type=Path)
    gap = sub.add_parser("gap", help="Compare one Garmin original with the copy Intervals received; no writes "
                                     "(works next to a running bridge)")
    gap.add_argument("--activity-id", required=True, help="Garmin Connect activity ID")
    gap.add_argument("--intervals-id", help="Intervals activity ID (default: match automatically)")
    enrich = sub.add_parser("enrich", help="Fill the Intervals activity's custom fields/streams "
                                           "from the Garmin original (dry run unless --apply)")
    enrich.add_argument("--activity-id", required=True, help="Garmin Connect activity ID")
    enrich.add_argument("--intervals-id", help="Intervals activity ID (default: match automatically)")
    enrich.add_argument("--apply", action="store_true", help="Actually PUT fields and streams")
    enrich.add_argument("--refresh-own-streams", action="store_true",
                        help="Rewrite streams this bridge wrote before (e.g. after a mapping fix)")
    setup = sub.add_parser("setup-fields", help="Preview/create missing private numeric custom fields")
    setup.add_argument("--apply", action="store_true", help="Actually create missing custom fields")
    sync = sub.add_parser("sync", help="Synchronize (local archive only / dry-run by default)")
    sync.add_argument("--apply", action="store_true", help="Permit Intervals writes")
    sync.add_argument("--scope", choices=["all", "activities", "wellness"], default="all")
    sync.add_argument("--mode", choices=["enrich", "upload"], default="enrich",
                      help="enrich = official import stays on, add what Garmin stripped (default); "
                           "upload = post originals as new activities (only with the official import off)")
    sync.add_argument("--allow-activity-upload", action="store_true",
                      help="Required in addition to --apply; first disable official Garmin activity import")
    sync.add_argument("--force-wellness", action="store_true", help="Ignore cached wellness refresh window")
    sync.add_argument("--activity-days", type=int, help="Override activity lookback (1-30 days)")
    sync.add_argument("--wellness-days", type=int, help="Override wellness lookback (1-30 days)")
    sync.add_argument("--activity-interval", type=int, default=0, metavar="MINUTES",
                      help="Enrich mode, for scheduled runs: skip the Garmin activity scan while the last one "
                           "is younger than this (the watch enriches new activities in between)")
    watch = sub.add_parser("watch", help="One cheap poll of Intervals; enrich only what appeared "
                                         "since the last poll, and read last night's wellness once the "
                                         "watch has synced (dry run unless --apply)")
    watch.add_argument("--apply", action="store_true")
    run = sub.add_parser("run", help="Keep running: poll Intervals every minute, full sync every 30 minutes, "
                                     "health probe once a day. For Windows and anything without systemd "
                                     "(dry run unless --apply)")
    run.add_argument("--apply", action="store_true")
    run.add_argument("--mode", choices=["enrich", "upload"], default="enrich",
                     help="enrich = official import stays on, the bridge adds what Garmin stripped (default); "
                          "upload = official import off, the bridge uploads every original as your own file")
    run.add_argument("--poll-seconds", type=int, default=60,
                     help="Seconds between polls: Intervals in enrich mode (default 60), Garmin in upload mode "
                          "(default 600, min 300)")
    run.add_argument("--sync-minutes", type=int, default=30, help="Minutes between full runs (default 30)")
    run.add_argument("--iterations", type=int, help=argparse.SUPPRESS)      # tests
    backfill = sub.add_parser("backfill", help="Enrich a date range from the past, paced (dry run unless --apply)")
    backfill.add_argument("--scope", choices=["activities", "wellness"], required=True)
    backfill.add_argument("--mode", choices=["enrich", "upload"], default="enrich",
                          help="Activities: enrich the officially imported ones (default) or upload the originals "
                               "of those Intervals does not have (official import off)")
    backfill.add_argument("--from", dest="start", required=True, help="YYYY-MM-DD")
    backfill.add_argument("--to", dest="end", help="YYYY-MM-DD (default: today)")
    backfill.add_argument("--apply", action="store_true")
    backfill.add_argument("--pause", type=float, default=2.0, help="Seconds between days/activities (default 2)")
    backfill.add_argument("--force-wellness", action="store_true", help="Re-fetch days already fetched")
    backfill.add_argument("--endpoints", default="essential",
                          help="Wellness: 'essential' (per-day measurements, default), 'all', or a comma-separated "
                               "list of endpoint keys (e.g. heart_rates,steps_intraday) to add to days fetched before")
    backfill.add_argument("--wellness-files", action="store_true",
                          help="Wellness: mirror the device's original wellness files of each day (monitoring, sleep, "
                               "HRV, Health Snapshots ...) into wellness-files/; one Garmin request per day, no "
                               "Intervals")
    backfill.add_argument("--intake", action="store_true",
                          help="Activities: fluid (ml) and food (kcal) logged in Garmin into the activity fields "
                               "GarminFluidIntake / GarminCaloriesConsumed, from the archived summaries; no Garmin "
                               "request")
    backfill.add_argument("--archive-only", action="store_true",
                          help="Activities: download the original FIT files that are not on disk yet and "
                               "leave Intervals alone (a local mirror of every recording)")
    backfill.add_argument("--rewrite", help="Wellness: comma-separated bridge fields (Garmin...) whose existing "
                                            "values may be replaced, after a mapping correction")
    backfill.add_argument("--from-archive", action="store_true",
                          help="Wellness: map the locally archived snapshots instead of asking Garmin "
                               "(completes the past after the mapping gained fields; no Garmin requests)")
    charts = sub.add_parser("setup-charts", help="Create private Intervals fitness charts for the synced "
                                                 "Garmin values (dry run unless --apply)")
    charts.add_argument("--apply", action="store_true")
    sub.add_parser("health", help="Probe Garmin and Intervals once; exit 2 when one has been failing "
                                  "longer than BRIDGE_STALE_HOURS (for a daily timer with an alert)")
    pending = sub.add_parser("reset-pending", help="After manual duplicate check, clear a pending upload lock")
    pending.add_argument("--activity-id", required=True)
    pending.add_argument("--i-checked-intervals", action="store_true", required=True,
                         help="Confirm you checked Intervals for duplicates and verified it is absent")
    gear = sub.add_parser("gear", help="Put Garmin's gear (bikes, shoes ...) on the Intervals activities, from the "
                                       "local archive: no Garmin requests, nothing removed (dry run unless --apply)")
    gear.add_argument("--apply", action="store_true", help="Write the plan; later scheduled runs keep the last "
                                                           "week in line with the same choices")
    gear.add_argument("--prefer", choices=["intervals", "evidence", "garmin"], default="intervals",
                      help="When both sides have different gear: keep Intervals' (default), take Garmin's only "
                           "with proof (gear not bought yet / retired that day, or the bike's own sensors missing "
                           "from the recording), or always take Garmin's")
    gear.add_argument("--map", action="append", default=[], metavar="GARMIN=INTERVALS",
                      help="Pair gear whose names differ, e.g. --map 'Zoom Fly Orange=Nike Zoom Fly - Red' "
                           "(repeatable)")
    gear.add_argument("--create", action="store_true", help="Create Garmin gear Intervals does not have")
    gear.add_argument("--include-retired", action="store_true", help="With --create: retired gear too")
    gear.add_argument("--from", dest="start", help="YYYY-MM-DD (default: everything archived)")
    gear.add_argument("--to", dest="end", help="YYYY-MM-DD (default: today)")
    gear.add_argument("--details", action="store_true", help="List every activity, not only the counts")
    gear.add_argument("--recalc", action="store_true",
                      help="Also recount the totals of every paired gear and its components with Intervals' own "
                           "count, when they drifted (e.g. after reassignments by hand)")
    advice = sub.add_parser("imready4", help="ImReady4-compatible training advice from Intervals' HRV and resting "
                                             "HR into the TrainingAdvice field: a day (default today), a range, a "
                                             "comparison with values already there (dry run unless --apply)")
    advice.add_argument("--date", help="YYYY-MM-DD (default: today)")
    advice.add_argument("--from", dest="start", help="YYYY-MM-DD: a range instead of one day")
    advice.add_argument("--to", dest="end", help="YYYY-MM-DD (default: today)")
    advice.add_argument("--compare", action="store_true",
                        help="Compare with the values TrainingAdvice already holds (e.g. from running ImReady4)")
    advice.add_argument("--apply", action="store_true", help="Write days whose TrainingAdvice is empty")
    advice.add_argument("--overwrite", action="store_true",
                        help="Also replace values TrainingAdvice already holds (with --apply)")
    advice.add_argument("--details", action="store_true", help="With --compare: list every day too")
    sub.add_parser("status", help="Show pending uploads requiring manual reconciliation")
    account = sub.add_parser("snapshot-account", help="Archive what is not a time series: profile, settings, "
                                                      "devices, zones, gear, personal records, badges, workouts, "
                                                      "training plans, goals, the calendar, FTP and running "
                                                      "tolerance of the recent weeks")
    account.add_argument("--history-from", metavar="DATE",
                         help="Also the calendar and Garmin's FTP / running tolerance series since DATE (YYYY-MM-DD)")
    return parser


ACTIVITY_SCAN_MINUTES = 120     # run loop, enrich mode: the poll catches new activities in between


def _gear_recent(settings: Settings, store: Store, intervals: IntervalsClient, *, apply: bool) -> dict:
    """Gear of the last week, once `gear --apply` has set the choices; never fails the run."""
    try:
        return sync_gear_recent(settings, store, intervals, apply=apply)
    except Exception as exc:
        logging.getLogger("bridge").warning("Gear not synced this time: %s", type(exc).__name__)
        return {"error": type(exc).__name__}


def run_loop(settings: Settings, store: Store, *, apply: bool, poll_seconds: int, sync_minutes: int,
             iterations: int | None = None, mode: str = "enrich") -> int:
    """What the three systemd timers do, in one long-running process.

    A fresh Garmin session per full run (like the timer), the cheap poll in between,
    a health probe once a day written to the log. A Garmin block pauses everything
    for 15 minutes; any other error is logged and the loop goes on.

    Upload mode (official import off): the poll asks Garmin for new activities and
    uploads their originals; the full run does the same plus wellness.
    """
    log = logging.getLogger("bridge")
    intervals = IntervalsClient(settings.intervals_api_key, settings.intervals_athlete_id)
    garmin = GarminSource(settings.token_dir, settings.garmin_request_delay)
    next_sync, next_health, count = 0.0, time.time() + 300, 0
    log.info("garmin-intervals-bridge %s running (%s mode): poll every %ds, full run every %d min, %s", __version__,
             mode, poll_seconds, sync_minutes, "writing to Intervals" if apply else "DRY RUN (add --apply to write)")
    source = getattr(settings, "timezone_source", "BRIDGE_TIMEZONE")
    (log.warning if source.startswith("fallback") else log.info)(
        "Day boundary: %s (%s)%s", settings.timezone.key, source,
        "; set BRIDGE_TIMEZONE to yours" if source.startswith("fallback") else "")
    while iterations is None or count < iterations:
        count += 1
        now = time.time()
        try:
            if now >= next_sync:
                garmin = GarminSource(settings.token_dir, settings.garmin_request_delay)
                results: dict = {"apply": apply, "scope": "all", "mode": mode}
                if mode == "upload":
                    results["activities"] = sync_activities(settings, garmin, intervals, store, apply=apply,
                                                            allow_upload=True)
                else:
                    results["activities"] = sync_enrich(settings, garmin, intervals, store, apply=apply,
                                                        scan_interval_minutes=ACTIVITY_SCAN_MINUTES)
                    if not results["activities"].get("scan_skipped"):
                        gear_result = _gear_recent(settings, store, intervals, apply=apply)
                        if gear_result:
                            results["gear"] = gear_result
                results["wellness"] = sync_wellness(settings, garmin, intervals, store, apply=apply)
                results["garmin_requests"] = garmin.requests
                store.record_run("sync", results)
                a, w = results["activities"], results["wellness"]
                labels = {("upload", True): ("uploaded", "uploaded"),
                          ("upload", False): ("would_upload", "to upload"),
                          ("enrich", True): ("enriched", "enriched"),
                          ("enrich", False): ("planned", "planned")}
                key, label = labels[(mode, apply)]
                done = a.get(key, 0)
                log.info("Full run: %d activities seen, %d %s; wellness %d days checked, %d written; "
                         "%d Garmin requests", a["seen"], done, label, w["days_checked"], w["writes"], garmin.requests)
                next_sync = now + sync_minutes * 60
            elif mode == "upload":
                garmin = GarminSource(settings.token_dir, settings.garmin_request_delay)
                result = sync_activities(settings, garmin, intervals, store, apply=apply, allow_upload=True,
                                         activity_days=2)
                result["wellness"] = wellness_check(settings, garmin, intervals, store, apply=apply)
                store.record_run("watch", {"apply": apply, "mode": mode, "garmin_requests": garmin.requests, **result})
                if result.get("uploaded") or result.get("would_upload"):
                    log.info("Garmin poll: %d uploaded, %d would upload", result["uploaded"], result["would_upload"])
            else:
                result = watch_once(settings, garmin, intervals, store, apply=apply)
                wellness = wellness_check(settings, garmin, intervals, store, apply=apply)
                if wellness.get("intervals_checks"):
                    result["wellness"] = wellness
                store.record_run("watch", {"apply": apply, "garmin_requests": garmin.requests, **result})
                if result["new"]:
                    log.info("Poll: %d new, %d enriched, %d planned", result["new"], result["enriched"],
                             result["planned"])
            if now >= next_health:
                report = probe(settings, garmin, intervals, store)
                for finding in report.get("findings", []):
                    (log.warning if finding.get("alert") else log.info)("Health: %s - %s", finding.get("what"),
                                                                        finding.get("detail"))
                next_health = now + 86400
        except KeyboardInterrupt:
            log.info("Stopped")
            return 0
        except GarminBlocked as exc:
            log.warning("%s; pausing for 15 minutes", exc)
            next_sync = max(next_sync, time.time() + 900)
            if iterations is None:
                time.sleep(900)
            continue
        except Exception as exc:
            if exc.__class__.__module__.startswith("requests"):
                log.error("HTTP/API request failed: %s", type(exc).__name__)
            else:
                log.error("%s: %s", type(exc).__name__, exc)
        if iterations is None or count < iterations:
            time.sleep(poll_seconds)
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(levelname)s %(message)s")
    log = logging.getLogger("bridge")
    try:
        if args.cmd == "compare-fit":
            print(json.dumps(compare_fit(args.file_a, args.file_b), indent=2))
            return 0
        settings = Settings.from_env()
        settings.ensure_data_dir()
        if args.cmd == "login":
            garmin = GarminSource(settings.token_dir, settings.garmin_request_delay)
            garmin.login(interactive=True)
            print("Garmin authenticated; session tokens saved locally.")
            return 0
        scope = getattr(args, "scope", None)
        lock_scopes = (("activities", "wellness") if scope in (None, "all") and args.cmd in ("sync", "run", "watch")
                       else ("wellness",) if scope == "wellness"
                       else ("health",) if args.cmd == "health"
                       # gear touches only the gear field: next to a running `run`, only not twice at once
                       else ("gear",) if args.cmd == "gear"
                       else ("imready4",) if args.cmd == "imready4"
                       # read-only diagnosis must work next to a running `run`: no shared state written
                       else () if args.cmd in ("status", "snapshot-account", "gap")
                       else ("activities",))
        locks = ExitStack()
        held: list[str] = []
        for lock_scope in lock_scopes:
            try:
                locks.enter_context(single_instance(settings.data_dir, (lock_scope,)))
                held.append(lock_scope)
            except InstanceBusy as exc:
                if args.cmd not in ("watch", "sync"):
                    locks.close()
                    raise
                # A backfill holds this scope for hours: the scheduled runs do what is free and
                # come back later (no failure, no OnFailure alert).
                log.info("%s; %s skipped this time", exc, lock_scope)
        if lock_scopes and not held:
            locks.close()
            return 0
        if args.cmd == "sync" and scope == "all" and held != ["activities", "wellness"]:
            args.scope = held[0]
        with locks:
            store = Store(settings.data_dir)
            try:
                if args.cmd == "status":
                    print(json.dumps({"pending_garmin_activity_ids": store.pending_activities(),
                                      "failed_activities": store.failed_activities()}, indent=2))
                    return 0
                if args.cmd == "reset-pending":
                    if not args.activity_id.isdecimal() or store.activity_status(args.activity_id) != "pending":
                        raise ValueError("Activity must have numeric ID and pending status")
                    store.record_activity(args.activity_id, "downloaded")
                    print(f"Pending lock cleared locally for {args.activity_id}. Next sync will recheck remote.")
                    return 0
                if args.cmd == "health":
                    intervals = IntervalsClient(settings.intervals_api_key, settings.intervals_athlete_id)
                    garmin = GarminSource(settings.token_dir, settings.garmin_request_delay)
                    report = probe(settings, garmin, intervals, store)
                    print(json.dumps(report, indent=2))
                    return report["exit"]
                if args.cmd == "setup-charts":
                    intervals = IntervalsClient(settings.intervals_api_key, settings.intervals_athlete_id)
                    print(json.dumps(setup_charts(intervals, apply=args.apply), indent=2))
                    return 0
                if args.cmd == "setup-fields":
                    intervals = IntervalsClient(settings.intervals_api_key, settings.intervals_athlete_id)
                    fields = intervals.provision_fields(apply=args.apply)
                    print(json.dumps({"apply": args.apply, "missing_codes": fields}, indent=2))
                    return 0
                if args.cmd == "imready4":
                    intervals = IntervalsClient(settings.intervals_api_key, settings.intervals_athlete_id)
                    today = datetime.now(settings.timezone).date()
                    if args.date and args.start:
                        raise ValueError("--date or --from, not both")
                    start = date.fromisoformat(args.start or args.date or today.isoformat())
                    end = date.fromisoformat(args.end) if args.end else (today if args.start else start)
                    if start > end:
                        raise ValueError("--from must not be after --to")
                    out = imready4.run_days(intervals, start, end, apply=args.apply, overwrite=args.overwrite,
                                            compare=args.compare, details=args.details, today=today)
                    print(json.dumps(out, indent=2, ensure_ascii=False))
                    return 0
                if args.cmd == "gear":
                    intervals = IntervalsClient(settings.intervals_api_key, settings.intervals_athlete_id)
                    start = date.fromisoformat(args.start) if args.start else None
                    end = date.fromisoformat(args.end) if args.end else None
                    if args.prefer == "evidence":
                        log.info("Reading the paired sensors of the archived rides (once; cached in gear-sensors.json)")
                    plan = build_gear_plan(store, intervals, start=start, end=end, policy=args.prefer,
                                           explicit=args.map, create=args.create,
                                           include_retired=args.include_retired, config=load_gear_config(store),
                                           sensors=GearSensors(store) if args.prefer == "evidence" else None)
                    out = gear_report(plan, details=args.details)
                    out["apply"] = args.apply
                    if args.apply:
                        done = apply_gear_plan(plan, intervals)
                        save_gear_config(store, plan, done.pop("ids"))
                        out["result"] = done
                    if args.recalc:
                        corrected = {x["gear"] for x in (out.get("result") or {}).get("totals_corrected", [])}
                        out["totals"] = [x for x in refresh_gear_totals(
                            intervals, sorted({t for t in plan["pairs"].values() if not t.startswith("new:")}),
                            apply=args.apply) if x["gear"] not in corrected]
                    print(json.dumps(out, indent=2, ensure_ascii=False, default=str))
                    return 0
                garmin = GarminSource(settings.token_dir, settings.garmin_request_delay)
                if args.cmd not in ("watch", "sync", "run") and not (
                        args.cmd == "backfill" and (args.from_archive or args.intake)):
                    garmin.login(interactive=False)      # fail early for one-off commands
                # (the scheduled ones log in on their first Garmin request, so a run with nothing to do costs none)
                if args.cmd == "snapshot-account":
                    raw = garmin.account_snapshot(
                        history_start=date.fromisoformat(args.history_from) if args.history_from else None,
                        today=datetime.now(settings.timezone).date())
                    path = store.save_account_snapshot(raw)
                    print(json.dumps({"file": str(path), "sections": sorted(raw["data"]), "errors": raw["errors"],
                                      "garmin_requests": garmin.requests}, indent=2))
                    return 0
                if args.cmd == "gap":
                    intervals = IntervalsClient(settings.intervals_api_key, settings.intervals_athlete_id)
                    gid = args.activity_id
                    path = store.fit_path(gid)
                    if not path.is_file():
                        store.atomic_save(path, garmin.original_fit(gid))
                    original = path.read_bytes()
                    if args.intervals_id:
                        remote_id = args.intervals_id
                        matched = {"id": remote_id, "how": "given"}
                    else:
                        activity = garmin.activity(gid)
                        day = datetime.fromisoformat(activity["startTimeGMT"]).date()
                        candidates = intervals.activities(day - timedelta(days=1), day + timedelta(days=1))
                        found = match_activity(activity, candidates)
                        if found is None:
                            print(json.dumps({"activity_id": gid, "matched": None,
                                              "candidates_seen": len(candidates),
                                              "report": gap_report(original, None)}, indent=2, default=str))
                            return 0
                        remote_id = str(found["id"])
                        matched = {"id": remote_id, "how": "matched", "source": found.get("source"),
                                   "external_id": found.get("external_id"), "start_date": found.get("start_date")}
                    partner = extract_original_fit(intervals.activity_file(remote_id))
                    store.atomic_save(store.partner_path(gid), partner)
                    print(json.dumps({"activity_id": gid, "matched": matched,
                                      "partner_file": str(store.partner_path(gid)),
                                      "report": gap_report(original, partner)}, indent=2, default=str))
                    return 0
                if args.cmd == "enrich":
                    intervals = IntervalsClient(settings.intervals_api_key, settings.intervals_athlete_id)
                    plan = enrich_activity(args.activity_id, garmin, intervals, store, apply=args.apply,
                                           intervals_id=args.intervals_id, refresh_own=args.refresh_own_streams)
                    print(json.dumps(plan, indent=2, default=str))
                    return 0
                if args.cmd == "probe":
                    if args.activity_id:
                        gid = args.activity_id
                    else:
                        today = datetime.now(settings.timezone).date()
                        recent = garmin.activities(today - timedelta(days=7), today)
                        if not recent:
                            raise ValueError("No recent Garmin activities; provide --activity-id")
                        gid = str(sorted(recent, key=lambda a: a.get("startTimeGMT", ""))[-1]["activityId"])
                    path = store.fit_path(gid)
                    fit = garmin.original_fit(gid)
                    store.atomic_save(path, fit)
                    print(json.dumps({"activity_id": gid, "file": str(path), "bytes": len(fit),
                                      "sha256": sha256(fit)}, indent=2))
                    return 0
                if args.cmd == "run":
                    poll = args.poll_seconds
                    if args.mode == "upload" and poll == 60:
                        poll = 600                            # a Garmin poll costs three requests, not one
                    if poll < (300 if args.mode == "upload" else 30) or args.sync_minutes < 5:
                        raise ValueError("--poll-seconds too small for this mode, or --sync-minutes < 5")
                    return run_loop(settings, store, apply=args.apply, poll_seconds=poll,
                                    sync_minutes=args.sync_minutes, iterations=args.iterations, mode=args.mode)
                if args.cmd == "watch":
                    intervals = IntervalsClient(settings.intervals_api_key, settings.intervals_athlete_id)
                    result: dict = {}
                    if "activities" in held:
                        result = watch_once(settings, garmin, intervals, store, apply=args.apply)
                    wellness = (wellness_check(settings, garmin, intervals, store, apply=args.apply)
                                if "wellness" in held else {})
                    record = {"apply": args.apply, "garmin_requests": garmin.requests, **result}
                    if wellness.get("intervals_checks") or wellness.get("days_from_archive"):
                        record["wellness"] = wellness
                    store.record_run("watch", record)
                    print(json.dumps({"apply": args.apply, "watch": result, "wellness": wellness}, indent=2))
                    return 0
                if args.cmd == "backfill":
                    intervals = IntervalsClient(settings.intervals_api_key, settings.intervals_athlete_id)
                    start = date.fromisoformat(args.start)
                    end = date.fromisoformat(args.end) if args.end else datetime.now(settings.timezone).date()
                    if start > end:
                        raise ValueError("--from must not be after --to")
                    if args.scope == "wellness" and args.wellness_files:
                        # only finished days: today's files are fetched with its final read, complete
                        today = datetime.now(settings.timezone).date()
                        days = [start + timedelta(days=n) for n in range((end - start).days + 1)
                                if start + timedelta(days=n) < today]
                        log.info("Backfill wellness files: %d days, one Garmin request each, %.1fs pause",
                                 len(days), args.pause)
                        out = sync_wellness_files(settings, garmin, store, days=days, pause_seconds=args.pause)
                    elif args.scope == "wellness":
                        days = [start + timedelta(days=n) for n in range((end - start).days + 1)]
                        if args.from_archive:
                            log.info("Backfill wellness from the archive: %d days, no Garmin requests", len(days))
                        else:
                            log.info("Backfill wellness: %d days, %d Garmin requests each, %.1fs pause", len(days),
                                     len(parse_endpoints(args.endpoints) or DAY_ENDPOINTS), args.pause)
                        endpoints = parse_endpoints(args.endpoints) or tuple(DAY_ENDPOINTS)
                        rewrite = {c.strip() for c in (args.rewrite or "").split(",") if c.strip()}
                        if any(not c.startswith("Garmin") for c in rewrite):
                            raise ValueError("--rewrite accepts the bridge's own Garmin... fields only")
                        out = sync_wellness(settings, garmin, intervals, store, apply=args.apply,
                                            force=args.force_wellness, days=days,
                                            pause_seconds=0 if args.from_archive else args.pause,
                                            endpoints=endpoints, from_archive=args.from_archive,
                                            rewrite=rewrite or None)
                    elif args.intake:
                        log.info("Backfill intake %s..%s from the archived summaries, no Garmin requests", start, end)
                        out = backfill_intake(intervals, store, start=start, end=end, apply=args.apply)
                    elif args.mode == "upload" and not args.archive_only:
                        log.info("Backfill activities %s..%s, UPLOAD mode: originals of activities Intervals "
                                 "does not have, %.1fs pause", start, end, args.pause)
                        out = sync_activities(settings, garmin, intervals, store, apply=args.apply,
                                              allow_upload=True, date_range=(start, end), pause_seconds=args.pause)
                    else:
                        log.info("Backfill activities %s..%s, one original download per new activity, %.1fs pause%s",
                                 start, end, args.pause, " (archive only)" if args.archive_only else "")
                        out = sync_enrich(settings, garmin, intervals, store, apply=args.apply,
                                          date_range=(start, end), pause_seconds=args.pause,
                                          archive_only=args.archive_only)
                    store.record_run("backfill", {"apply": args.apply, "scope": args.scope, "from": str(start),
                                                  "to": str(end), "garmin_requests": garmin.requests, **out})
                    print(json.dumps({"apply": args.apply, "scope": args.scope, "from": str(start), "to": str(end),
                                      "result": out}, indent=2))
                    return 0
                if args.cmd == "sync":
                    for name in ("activity_days", "wellness_days"):
                        v = getattr(args, name)
                        if v is not None and not 1 <= v <= 30:
                            raise ValueError(f"--{name.replace('_','-')} must be 1..30")
                    intervals = IntervalsClient(settings.intervals_api_key, settings.intervals_athlete_id)
                    results: dict = {"apply": args.apply, "scope": args.scope}
                    results["mode"] = args.mode
                    if args.scope in ("all", "activities") and args.mode == "enrich":
                        results["activities"] = sync_enrich(settings, garmin, intervals, store,
                            apply=args.apply, activity_days=args.activity_days,
                            scan_interval_minutes=max(args.activity_interval, 0))
                        if not results["activities"].get("scan_skipped"):
                            gear_result = _gear_recent(settings, store, intervals, apply=args.apply)
                            if gear_result:
                                results["gear"] = gear_result
                    elif args.scope in ("all", "activities"):
                        if args.apply and not args.allow_activity_upload:
                            log.warning("Activity uploads disabled; add --allow-activity-upload ONLY AFTER "
                                        "disabling Garmin auto-activity sync")
                        results["activities"] = sync_activities(settings, garmin, intervals, store,
                            apply=args.apply, allow_upload=args.allow_activity_upload,
                            activity_days=args.activity_days)
                    if args.scope in ("all", "wellness"):
                        results["wellness"] = sync_wellness(settings, garmin, intervals, store,
                            apply=args.apply, wellness_days=args.wellness_days,
                            force=args.force_wellness)
                    results["garmin_requests"] = garmin.requests
                    store.record_run("sync", results)
                    print(json.dumps(results, indent=2))
                    return 0
            finally:
                store.close()
    except Exception as exc:
        # Do not echo possibly credential-bearing exception messages for requests failures.
        if exc.__class__.__module__.startswith("requests"):
            log.error("HTTP/API request failed: %s", type(exc).__name__)
        else:
            log.error("%s: %s", type(exc).__name__, str(exc))
        return 1
    return 1


if __name__ == "__main__":
    sys.exit(main())
