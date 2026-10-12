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
    candidate = path or Path(os.getenv("BRIDGE_ENV_FILE") or ".env")
    if not candidate.is_file():
        return []
    loaded: list[str] = []
    for line in candidate.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip().removeprefix("export ").strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if re.fullmatch(r"[A-Z][A-Z0-9_]*", key) and key not in os.environ:
            os.environ[key] = value
            loaded.append(key)
    return loaded


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

    @classmethod
    def from_env(cls) -> "Settings":
        load_env_file()
        data_dir = Path(os.getenv("BRIDGE_DATA_DIR", "./data")).expanduser().resolve()
        token_dir = Path(os.getenv("GARMIN_TOKEN_DIR", str(data_dir / "tokens"))).expanduser().resolve()
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
        )

    def ensure_data_dir(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            self.data_dir.chmod(0o700)
        except OSError:
            pass
