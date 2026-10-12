"""Config from environment without loading secrets into logs."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def _timezone() -> tuple[ZoneInfo, str]:
    """BRIDGE_TIMEZONE, else this computer's own zone (TZ, /etc/localtime, Windows' setting), else UTC.

    The zone is the day boundary: which date last night's sleep and today's totals belong to.
    """
    name, source = os.getenv("BRIDGE_TIMEZONE", "").strip(), "BRIDGE_TIMEZONE"
    if not name:
        try:
            from tzlocal import get_localzone_name
            name, source = get_localzone_name() or "", "this computer"
        except Exception:                      # no zone configured at all (a bare container)
            name = ""
    if not name:
        name, source = "UTC", "fallback, nothing set"
    try:
        return ZoneInfo(name), source
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError(f"Unknown time zone {name!r} (from {source}): set BRIDGE_TIMEZONE to yours, "
                         "e.g. Europe/London") from exc


HOME_FOLDER = "GarminBridge"


def bridge_home() -> Path:
    """Where `setup` puts a bridge: ~/GarminBridge (on Windows %USERPROFILE%\\GarminBridge)."""
    return Path.home() / HOME_FOLDER


def env_file() -> Path | None:
    """The settings file that applies: BRIDGE_ENV_FILE, else ./.env, else ~/GarminBridge/.env.

    The last one is what `setup` writes, so a bridge set up that way answers in any terminal,
    from any folder. systemd and containers set everything themselves and have none of these.
    """
    named = os.getenv("BRIDGE_ENV_FILE", "").strip()
    if named:
        return Path(named).expanduser()
    for candidate in (Path(".env"), bridge_home() / ".env"):
        if candidate.is_file():
            return candidate
    return None


def _path(name: str, default: str | None, base: Path) -> Path | None:
    """A path setting; a relative one counts from the settings file's folder."""
    value = os.getenv(name, "").strip() or default
    if not value:
        return None
    path = Path(value).expanduser()
    return (path if path.is_absolute() else base / path).resolve()


def configured_log_file() -> Path | None:
    """BRIDGE_LOG_FILE as the settings will see it, read before them: so that even a broken
    setting is reported in the log file of a bridge that runs without a window."""
    settings_file = env_file()
    try:
        load_env_file(settings_file)
        base = settings_file.parent.resolve() if settings_file is not None else Path.cwd()
        return _path("BRIDGE_LOG_FILE", None, base)
    except (OSError, ValueError):
        return None


def _flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name, "").strip().lower()
    if value == "":
        return default
    if value in ("0", "false", "no", "off"):
        return False
    if value in ("1", "true", "yes", "on"):
        return True
    raise ValueError(f"{name} must be true or false")


def _positive_int(name: str, default: int, max_value: int) -> int:
    value = int(os.getenv(name, str(default)))
    if not 1 <= value <= max_value:
        raise ValueError(f"{name} must be between 1 and {max_value}")
    return value


def load_env_file(path: Path | None = None) -> list[str]:
    """Read KEY=VALUE lines from `.env` (or BRIDGE_ENV_FILE) into the environment.

    Variables already set win, so a systemd EnvironmentFile or an exported
    variable is never overridden. Values are never logged; the names are returned.
    """
    candidate = path or env_file()
    if candidate is None or not candidate.is_file():
        return []
    loaded: list[str] = []
    for key, value in parse_env_file(candidate).items():
        if key not in os.environ:
            os.environ[key] = value
            loaded.append(key)
    return loaded


def env_line_key(line: str) -> str | None:
    """The variable a KEY=VALUE line sets; None for comments, blanks and anything else."""
    line = line.strip()
    if not line or line.startswith("#") or "=" not in line:
        return None
    key = line.split("=", 1)[0].strip().removeprefix("export ").strip()
    return key if re.fullmatch(r"[A-Z][A-Z0-9_]*", key) else None


def parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        key = env_line_key(line)
        if key is None:
            continue
        value = line.split("=", 1)[1].strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key] = value
    return values


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    token_dir: Path
    intervals_api_key: str
    intervals_athlete_id: str
    timezone: ZoneInfo
    activity_days: int
    wellness_days: int
    wellness_refresh_hours: int
    garmin_request_delay: float
    wellness_profile: str = "recommended"
    stale_hours: int = 24
    timezone_source: str = "BRIDGE_TIMEZONE"
    imready4: bool = False
    hrvtrend: bool = False
    env_file: Path | None = None
    log_file: Path | None = None
    update_check: bool = True
    intervals_notes: bool = True

    @classmethod
    def from_env(cls) -> "Settings":
        settings_file = env_file()
        load_env_file(settings_file)
        base = settings_file.parent.resolve() if settings_file is not None else Path.cwd()
        data_dir = _path("BRIDGE_DATA_DIR", "data", base)
        token_dir = _path("GARMIN_TOKEN_DIR", str(data_dir / "tokens"), base)
        tz, tz_source = _timezone()
        delay = float(os.getenv("BRIDGE_GARMIN_REQUEST_DELAY", "0.5"))
        if delay < 0.25:
            raise ValueError("BRIDGE_GARMIN_REQUEST_DELAY must be >= 0.25 seconds")
        profile = os.getenv("BRIDGE_WELLNESS_PROFILE", "recommended").strip().lower()
        if profile not in ("recommended", "all"):
            raise ValueError("BRIDGE_WELLNESS_PROFILE must be 'recommended' or 'all'")
        athlete_id = os.getenv("INTERVALS_ATHLETE_ID", "0").strip()
        if re.fullmatch(r"i?\d+", athlete_id) is None:
            raise ValueError("Invalid INTERVALS_ATHLETE_ID")
        return cls(
            data_dir=data_dir,
            token_dir=token_dir,
            intervals_api_key=os.getenv("INTERVALS_API_KEY", "").strip(),
            intervals_athlete_id=athlete_id,
            timezone=tz,
            activity_days=_positive_int("BRIDGE_ACTIVITY_LOOKBACK_DAYS", 4, 30),
            wellness_days=_positive_int("BRIDGE_WELLNESS_LOOKBACK_DAYS", 3, 30),
            wellness_refresh_hours=_positive_int("BRIDGE_WELLNESS_REFRESH_HOURS", 4, 72),
            garmin_request_delay=delay,
            wellness_profile=profile,
            stale_hours=_positive_int("BRIDGE_STALE_HOURS", 24, 24 * 30),
            timezone_source=tz_source,
            imready4=_flag("BRIDGE_IMREADY4_ENABLED"),
            hrvtrend=_flag("BRIDGE_HRVTREND_ENABLED"),
            env_file=settings_file.resolve() if settings_file is not None else None,
            log_file=_path("BRIDGE_LOG_FILE", None, base),
            update_check=_flag("BRIDGE_UPDATE_CHECK", True),
            intervals_notes=_flag("BRIDGE_INTERVALS_NOTES", True),
        )

    def ensure_data_dir(self) -> None:
        try:
            self.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        except PermissionError:
            pass                                       # reported just below, in words
        if not os.access(self.data_dir, os.W_OK):
            uid = os.getuid() if hasattr(os, "getuid") else None
            raise PermissionError(
                f"The data folder {self.data_dir} is not writable for the bridge"
                + (f" (user id {uid})" if uid is not None else "") + ". In a container: give that folder "
                "read and write access for everyone (Synology: File Station, Properties, Permission) or "
                f"`chown -R {uid if uid is not None else 10001} <folder>` on the host")
        try:
            self.data_dir.chmod(0o700)
        except OSError:
            pass
