import pytest

from garmin_intervals_bridge import config


def test_day_boundary_from_setting_computer_or_utc(monkeypatch):
    import tzlocal
    monkeypatch.setenv("BRIDGE_TIMEZONE", "Europe/Dublin")
    tz, source = config._timezone()
    assert tz.key == "Europe/Dublin" and source == "BRIDGE_TIMEZONE"
    monkeypatch.delenv("BRIDGE_TIMEZONE")
    monkeypatch.setattr(tzlocal, "get_localzone_name", lambda: "Europe/Rome")
    tz, source = config._timezone()
    assert tz.key == "Europe/Rome" and source == "this computer"         # no more Vienna for everyone

    def unknown():
        raise LookupError("no zone configured")
    monkeypatch.setattr(tzlocal, "get_localzone_name", unknown)
    tz, source = config._timezone()
    assert tz.key == "UTC" and source.startswith("fallback")


def test_an_unknown_zone_says_what_to_set(monkeypatch):
    monkeypatch.setenv("BRIDGE_TIMEZONE", "Europe/Atlantis")
    with pytest.raises(ValueError, match="set BRIDGE_TIMEZONE"):
        config._timezone()


def test_poll_hours_ranges_also_over_midnight():
    from datetime import datetime
    assert config.poll_hours("") == ()
    assert config.poll_hours("10-18") == ((600, 1080),)
    assert config.poll_hours("6:30-9, 17-22") == ((390, 540), (1020, 1320))
    night = config.poll_hours("22-6")
    assert config.within_hours(datetime(2026, 10, 12, 23, 30), night)
    assert config.within_hours(datetime(2026, 10, 12, 5, 59), night)
    assert not config.within_hours(datetime(2026, 10, 12, 6, 0), night)
    assert config.within_hours(datetime(2026, 10, 12, 3, 0), ())               # no window: always
    for bad in ("10", "10-10", "25-3", "morning"):
        with pytest.raises(ValueError, match="BRIDGE_POLL_HOURS"):
            config.poll_hours(bad)


def test_positive_int_fallback_and_validation(monkeypatch):
    monkeypatch.delenv("TEST_VAR", raising=False)
    assert config._positive_int("TEST_VAR", 5, 10) == 5

    monkeypatch.setenv("TEST_VAR", "")
    assert config._positive_int("TEST_VAR", 5, 10) == 5

    monkeypatch.setenv("TEST_VAR", "   ")
    assert config._positive_int("TEST_VAR", 5, 10) == 5

    monkeypatch.setenv("TEST_VAR", " 8 ")
    assert config._positive_int("TEST_VAR", 5, 10) == 8

    monkeypatch.setenv("TEST_VAR", "15")
    with pytest.raises(ValueError, match="TEST_VAR must be between 1 and 10"):
        config._positive_int("TEST_VAR", 5, 10)


def test_delay_empty_fallback(monkeypatch):
    from unittest.mock import patch
    monkeypatch.setenv("BRIDGE_GARMIN_REQUEST_DELAY", "   ")
    with patch.object(config, "load_env_file"), patch.object(config, "load_options_file"):
        monkeypatch.setenv("INTERVALS_API_KEY", "test-key")
        settings = config.Settings.from_env()
        assert settings.garmin_request_delay == 0.5

