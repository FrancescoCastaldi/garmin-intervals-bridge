from datetime import datetime, timezone

from garmin_intervals_bridge.times import external_id_names, parse_utc


def test_external_id_names_matching():
    # Official direct sync format
    assert external_id_names("24544097680", "24544097680")
    assert external_id_names(" 24544097680 ", "24544097680")
    assert external_id_names("24544097680", 24544097680)

    # Bridge upload format
    assert external_id_names("garmin:24544097680", "24544097680")
    assert external_id_names(" garmin:24544097680 ", "24544097680")

    # Manual file upload format
    assert external_id_names("24544097680_ACTIVITY.fit", "24544097680")

    # Negative matches
    assert not external_id_names("99999999999", "24544097680")
    assert not external_id_names("garmin:99999999999", "24544097680")
    assert not external_id_names(None, "24544097680")
    assert not external_id_names(12345, "12345")
    assert not external_id_names("", "24544097680")
    assert not external_id_names("24544097680", "")


def test_parse_utc():
    # ISO-8601 with Z
    dt = parse_utc("2026-10-13T12:00:00Z")
    assert dt == datetime(2026, 10, 13, 12, 0, 0, tzinfo=timezone.utc)

    # ISO-8601 with whitespace
    dt_padded = parse_utc("  2026-10-13T12:00:00Z  \n")
    assert dt_padded == datetime(2026, 10, 13, 12, 0, 0, tzinfo=timezone.utc)

    # ISO-8601 with offset
    dt_offset = parse_utc("2026-10-13T14:00:00+02:00")
    assert dt_offset == datetime(2026, 10, 13, 12, 0, 0, tzinfo=timezone.utc)

    # Garmin naive GMT
    assert parse_utc("2026-10-13 12:00:00", garmin_gmt=False) is None
    dt_gmt = parse_utc("2026-10-13 12:00:00", garmin_gmt=True)
    assert dt_gmt == datetime(2026, 10, 13, 12, 0, 0, tzinfo=timezone.utc)

    # Invalid / empty
    assert parse_utc(None) is None
    assert parse_utc("") is None
    assert parse_utc("   ") is None
    assert parse_utc("not-a-date") is None
