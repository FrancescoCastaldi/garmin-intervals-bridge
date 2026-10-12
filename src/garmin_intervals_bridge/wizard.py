"""`garmin-intervals-bridge setup`: from nothing to a bridge running in the background, by
answering questions.

Every answer is checked where it can be: the key against Intervals, the zone against the
time zone database, Garmin by signing in. The settings file is saved after each step (only
its owner may read it; lines setup does not set are kept), so an interrupted setup goes on
where it stopped. Nothing is written to Intervals before the last question, which follows
a trial run. Run it again to change anything: the saved answers are the defaults.
"""
from __future__ import annotations

import getpass
import os
import shutil
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import requests

from . import __version__, autostart, hrvtrend, imready4
from .charts import setup_charts
from .config import Settings, bridge_home, env_file, env_line_key, parse_env_file
from .garmin import GarminBlocked, GarminLoginNeeded, GarminSource
from .gear import RECENT_DAYS
from .gear import apply_plan as apply_gear_plan
from .gear import build_plan as build_gear_plan
from .gear import load_config as load_gear_config
from .gear import save_config as save_gear_config
from .intervals import IntervalsClient
from .store import InstanceBusy, Store, single_instance

HEADER = ("# Garmin -> Intervals.icu Bridge settings, written by `garmin-intervals-bridge setup`.\n"
          "# Run setup again to change them, or edit this file (all settings: README, Configuration).\n")
STEPS = 5
GUIDES = "https://github.com/futureweb/garmin-intervals-bridge/tree/main/docs"


def command_name() -> str:
    """How this person starts the bridge, for the commands setup prints."""
    if autostart.platform() == "windows":
        return "py -m garmin_intervals_bridge"
    started = Path(sys.argv[0]) if sys.argv and sys.argv[0] else None
    if started is not None and started.name == "garmin-intervals-bridge":
        found = shutil.which("garmin-intervals-bridge")
        if found and Path(found).resolve() == started.resolve():
            return "garmin-intervals-bridge"
        path = str(started.resolve())
        home = str(Path.home())
        return "~" + path[len(home):] if path.startswith(home + os.sep) else path
    return "python3 -m garmin_intervals_bridge"


class Prompt:
    """Questions on the terminal; tests hand in scripted answers instead."""

    def __init__(self, ask: Callable[[str], str] = input, secret: Callable[[str], str] = getpass.getpass,
                 say: Callable[[str], Any] = print):
        self._ask, self._secret, self._say = ask, secret, say

    def say(self, text: str = "") -> None:
        self._say(text)

    def ask(self, question: str, default: str = "") -> str:
        answer = self._ask(f"{question} [{default}]: " if default else f"{question}: ").strip()
        return answer or default

    def yes(self, question: str, default: bool = True) -> bool:
        while True:
            answer = self._ask(f"{question} [{'Y/n' if default else 'y/N'}]: ").strip().lower()
            if not answer:
                return default
            if answer in ("y", "yes", "j", "ja"):
                return True
            if answer in ("n", "no", "nein"):
                return False
            self.say("Please answer y or n.")

    def secret(self, question: str) -> str:
        return self._secret(f"{question}: ").strip()


# ---- the settings file ----

def write_env(path: Path, values: dict[str, str]) -> None:
    """Set these variables, keep every other line; readable by its owner only."""
    lines = path.read_text(encoding="utf-8-sig").splitlines() if path.is_file() else HEADER.splitlines()
    done: set[str] = set()
    for index, line in enumerate(lines):
        key = env_line_key(line)
        if key in values:
            lines[index] = f"{key}={values[key]}"
            done.add(key)
    lines += [f"{key}={value}" for key, value in values.items() if key not in done]
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(lines) + "\n")
    os.replace(temporary, path)
    try:
        path.chmod(0o600)
    except OSError:
        pass


def _is_bridge_file(path: Path) -> bool:
    """A ./.env of some other project is not ours to write into."""
    try:
        return any(k == "INTERVALS_API_KEY" or k.startswith(("BRIDGE_", "GARMIN_")) for k in parse_env_file(path))
    except (OSError, UnicodeDecodeError):
        return False


def _settings_file(p: Prompt) -> Path:
    named = os.getenv("BRIDGE_ENV_FILE", "").strip()
    if named:
        return Path(named).expanduser().resolve()
    found = env_file()
    if found is not None and _is_bridge_file(found):
        p.say(f"Settings file: {found.resolve()} (your earlier answers are the defaults)")
        return found.resolve()
    folder = p.ask("Folder for the bridge: settings, log and your Garmin data", str(bridge_home()))
    return (Path(folder).expanduser() / ".env").resolve()


# ---- the steps ----

def _athlete_name(athlete: dict) -> str:
    name = athlete.get("name") or " ".join(x for x in (athlete.get("firstname"), athlete.get("lastname")) if x)
    return str(name or athlete.get("id") or "your account")


def _intervals_key(p: Prompt, saved: str, client: Callable[[str], Any]) -> tuple[str, Any] | None:
    if saved:
        try:
            intervals = client(saved)
            name = _athlete_name(intervals.athlete())
            if p.yes(f"The saved key works ({name}). Keep it?"):
                return saved, intervals
        except requests.HTTPError:
            p.say("Intervals does not accept the saved key any more.")
        except requests.RequestException as exc:
            p.say(f"Could not reach intervals.icu ({type(exc).__name__}); check the internet connection.")
            return None
    p.say("Your Intervals.icu API key: intervals.icu -> Settings -> Developer Settings -> API key.\n"
          "Copy it from there; it is not shown while you paste it, and it stays on this computer.")
    for _ in range(3):
        key = p.secret("API key")
        if not key:
            continue
        try:
            intervals = client(key)
            name = _athlete_name(intervals.athlete())
        except requests.HTTPError as exc:
            status = getattr(exc.response, "status_code", None)
            p.say("Intervals refused this key; check that it is complete." if status in (401, 403)
                  else f"Intervals answered with an error ({status}); try again.")
            continue
        except requests.RequestException as exc:
            p.say(f"Could not reach intervals.icu ({type(exc).__name__}); check the internet connection.")
            continue
        p.say(f"Connected to Intervals.icu as {name}.")
        return key, intervals
    return None


def _timezone(p: Prompt, saved: str) -> str:
    try:
        from tzlocal import get_localzone_name
        detected = get_localzone_name() or ""
    except Exception:
        detected = ""
    current = saved or detected or "UTC"
    p.say("The time zone decides which date last night's sleep and today's totals belong to.")
    if p.yes(f"Time zone {current} - right?"):
        return current
    while True:
        name = p.ask("Your time zone, like Europe/London or America/New_York").strip()
        try:
            ZoneInfo(name)
            return name
        except (ZoneInfoNotFoundError, ValueError):
            p.say(f"'{name}' is not a known time zone name (Region/City, as in the list on "
                  "https://en.wikipedia.org/wiki/List_of_tz_database_time_zones).")


def _garmin(p: Prompt, settings: Settings, source: Callable[..., Any]) -> Any | None:
    garmin = source(settings.token_dir, settings.garmin_request_delay)
    try:
        garmin.login(interactive=False)
        p.say("Garmin Connect: signed in with the saved session.")
        return garmin
    except GarminLoginNeeded:
        pass
    except GarminBlocked as exc:
        p.say(f"Garmin is not answering or limits sign-ins right now ({exc}). Wait an hour, then run setup "
              "again; your answers so far are saved.")
        return None
    p.say("Sign in to Garmin Connect once. Your password is used for this sign-in only and not stored; "
          "the session is saved in the bridge's folder and renews itself for about a year.")
    try:
        garmin.login(interactive=True)
    except GarminBlocked as exc:
        p.say(f"Garmin refused the sign-in for now ({exc}). If it says 429: wait an hour, then run setup again.")
        return None
    except Exception as exc:
        p.say(f"Garmin sign-in failed ({type(exc).__name__}): check e-mail and password, then run setup again.")
        return None
    p.say("Garmin Connect: signed in.")
    return garmin


def _extras(p: Prompt, saved: dict[str, str], intervals: Any, gear_config: dict | None) -> dict[str, Any]:
    out: dict[str, Any] = {"gear": False}
    if gear_config:
        p.say(f"Gear: set up earlier, stays as it is (`{command_name()} gear` shows it).")
    else:
        p.say("Your gear: Garmin knows which bike or shoes an activity used; Intervals only knows what you\n"
              "pick there. The bridge can fill that in and create your Garmin gear in Intervals.")
        out["gear"] = p.yes("Put your Garmin gear on your Intervals activities?", default=True)
        if out["gear"]:
            out["retired"] = p.yes("Your retired gear too (created as retired)?", default=False)
    field = imready4.find_field(intervals)
    out["imready4"] = False
    if field is not None and imready4.field_problem(field) is None:
        p.say("You have ImReady4's TrainingAdvice field. The bridge can fill it each morning from your\n"
              "HRV and resting heart rate, as ImReady4's script does (only if you do not run that script).")
        out["imready4"] = p.yes("Fill TrainingAdvice each morning?",
                                default=saved.get("BRIDGE_IMREADY4_ENABLED", "").lower() == "true")
    out["hrvtrend"] = p.yes("Experimental: a training advice from your 7-day HRV trend, in its own field?",
                            default=saved.get("BRIDGE_HRVTREND_ENABLED", "").lower() == "true")
    out["charts"] = p.yes("Add charts for the Garmin values to your Intervals Fitness page?", default=True)
    return out


def _trial(settings: Settings, garmin: Any, intervals: Any, store: Store) -> dict:
    """What a first real run would do, writing nothing: two days of activities, last night."""
    from .sync import sync_enrich, sync_wellness
    activities = sync_enrich(settings, garmin, intervals, store, apply=False, activity_days=2)
    wellness = sync_wellness(settings, garmin, intervals, store, apply=False, wellness_days=1)
    return {"activities": activities, "wellness": wellness}


def _describe_trial(result: dict) -> list[str]:
    a, w = result["activities"], result["wellness"]
    waiting = f", {a['unmatched']} not in Intervals yet (Garmin's sync brings them)" if a.get("unmatched") else ""
    lines = [f"  Activities of the last 2 days in Garmin: {a.get('seen', 0)}; "
             f"{a.get('planned', 0)} would get what Garmin's own sync leaves out{waiting}"]
    if w.get("days_with_changes"):
        lines.append("  Last night's wellness values would be written.")
    elif w.get("days_checked"):
        lines.append("  Wellness: nothing new for last night yet (it comes after your watch has synced).")
    if w.get("new_fields"):
        lines.append(f"  New wellness fields in Intervals: {len(w['new_fields'])} (private, created on first use)")
    if a.get("failed"):
        lines.append(f"  {a['failed']} activities failed this time; the bridge retries them.")
    return lines


def run(prompt: Prompt | None = None, *, intervals_client: Callable[[str], Any] | None = None,
        garmin_source: Callable[..., Any] = GarminSource, install: Callable[..., Any] = autostart.install,
        trial: Callable[..., dict] = _trial) -> int:
    p = prompt or Prompt()
    if autostart.platform() == "container":
        p.say("In a container the settings are the container's variables (INTERVALS_API_KEY, BRIDGE_TIMEZONE),\n"
              "and it restarts itself. Sign in to Garmin with `garmin-intervals-bridge login` in its terminal.\n"
              "Guide: https://github.com/futureweb/garmin-intervals-bridge/blob/main/docs/SYNOLOGY.md")
        return 1
    if prompt is None and not sys.stdin.isatty():
        raise ValueError("setup asks questions: run it in a terminal window")
    client = intervals_client or IntervalsClient
    p.say(f"Garmin -> Intervals.icu Bridge {__version__}: setup. Ctrl+C stops it at any point.\n")
    try:
        return _run(p, client, garmin_source, install, trial)
    except KeyboardInterrupt:
        p.say("\nSetup stopped. What was answered so far is saved; run setup again to go on.")
        return 1


def _run(p: Prompt, client: Callable[[str], Any], garmin_source: Callable[..., Any],
         install: Callable[..., Any], trial: Callable[..., dict]) -> int:
    path = _settings_file(p)
    saved = parse_env_file(path) if path.is_file() else {}

    def save(values: dict[str, str]) -> Settings:
        write_env(path, values)
        os.environ["BRIDGE_ENV_FILE"] = str(path)
        os.environ.update(values)                       # what this setup chose wins over earlier variables
        return Settings.from_env()

    p.say(f"\n1/{STEPS} Intervals.icu")
    key = _intervals_key(p, saved.get("INTERVALS_API_KEY", ""), client)
    if key is None:
        p.say("No working key; run setup again when you have it.")
        return 1
    api_key, intervals = key

    p.say(f"\n2/{STEPS} Time zone")
    zone = _timezone(p, saved.get("BRIDGE_TIMEZONE", ""))
    settings = save({"INTERVALS_API_KEY": api_key, "BRIDGE_TIMEZONE": zone,
                     "BRIDGE_LOG_FILE": saved.get("BRIDGE_LOG_FILE") or "bridge.log"})
    settings.ensure_data_dir()
    p.say(f"Saved in {path}")

    p.say(f"\n3/{STEPS} Garmin Connect")
    garmin = _garmin(p, settings, garmin_source)
    if garmin is None:
        return 1

    p.say(f"\n4/{STEPS} Extras")
    store = Store(settings.data_dir)
    try:
        extras = _extras(p, saved, intervals, load_gear_config(store))
        settings = save({"BRIDGE_IMREADY4_ENABLED": str(extras["imready4"]).lower(),
                         "BRIDGE_HRVTREND_ENABLED": str(extras["hrvtrend"]).lower()})

        p.say(f"\n5/{STEPS} Trial run and start")
        running = False
        try:
            with single_instance(settings.data_dir, ("activities", "wellness")):
                p.say("Trial run, nothing is written yet ...")
                for line in _describe_trial(trial(settings, garmin, intervals, store)):
                    p.say(line)
        except InstanceBusy:
            running = True
            p.say("The bridge already runs in the background; it restarts with these settings.")
        if not p.yes("Start now: write to Intervals, and run in the background whenever you are logged in?"):
            me = command_name()
            p.say(f"Nothing written to Intervals. Start it later with: {me} autostart\n"
                  f"(or in a window that stays open: {me} run --apply)")
            return 0
        notes = _first_writes(p, settings, store, intervals, extras)
    finally:
        store.close()
    steps = install(settings.env_file or path)
    p.say("\nDone. The bridge runs in the background now"
          + (" (restarted)." if running else " and starts whenever you log in."))
    for line in notes:
        p.say(line)
    me = command_name()
    p.say(f"  Log:       {settings.log_file}\n"
          f"  Settings:  {path}  (to change them: {me} setup)\n"
          f"  Is it on:  {me} autostart --status\n"
          f"  Stop it:   {me} autostart --remove\n"
          f"  The past, gear of older activities, updating: the guide for your system, {GUIDES}")
    if steps.get("note"):
        p.say(f"  Note:      {steps['note']}")
    return 0


def _first_writes(p: Prompt, settings: Settings, store: Store, intervals: Any, extras: dict) -> list[str]:
    """The writes the answers asked for; a failure is reported, the bridge still starts."""
    notes: list[str] = []
    if extras.get("charts"):
        try:
            out = setup_charts(intervals, apply=True)
            notes.append(f"  Charts:    {len(out['created'])} created, {len(out['updated'])} updated "
                         "(Intervals -> Fitness, chart picker)")
        except Exception as exc:
            notes.append(f"  Charts:    not created ({type(exc).__name__}); later: {command_name()} "
                         "setup-charts --apply")
    if extras.get("hrvtrend"):
        try:
            hrvtrend.ensure_fields(intervals, apply=True)
        except Exception as exc:
            notes.append(f"  HRV trend: {exc}")
    if extras.get("gear"):
        try:
            today = datetime.now(settings.timezone).date()
            with single_instance(settings.data_dir, ("gear",)):
                # the last week, as the scheduled runs do: older activities only after `gear` showed its plan
                plan = build_gear_plan(store, intervals, start=today - timedelta(days=RECENT_DAYS), end=today,
                                       create=True, include_retired=bool(extras.get("retired")))
                done = apply_gear_plan(plan, intervals)
                save_gear_config(store, plan, done.pop("ids"), create=True,
                                 include_retired=bool(extras.get("retired")))
            made = len(done.get("created") or [])
            notes.append(f"  Gear:      on; {done.get('written', 0)} activities of the last week, {made} gear created"
                         if done.get("written") or made else "  Gear:      on; new activities get their Garmin gear")
        except Exception as exc:
            notes.append(f"  Gear:      not set up ({type(exc).__name__}); later: {command_name()} gear "
                         "--create --apply")
    if extras.get("imready4"):
        notes.append("  ImReady4:  TrainingAdvice is filled each morning once the night's values are in")
    return notes
