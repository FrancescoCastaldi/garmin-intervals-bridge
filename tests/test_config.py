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
