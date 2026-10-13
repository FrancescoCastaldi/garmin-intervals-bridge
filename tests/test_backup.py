"""Backup mode: Garmin into the data folder, kindly, without Intervals."""
from datetime import date, datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from test_sync_enrich import GarminFake

from garmin_intervals_bridge import backup, cli, config
from garmin_intervals_bridge.store import Store, single_instance

VIENNA = ZoneInfo("Europe/Vienna")


class BackupGarmin(GarminFake):
    def __init__(self):
        super().__init__()
        self.days, self.file_days, self.accounts = [], [], []

    def snapshot(self, day, endpoints=None):
        self.days.append((day, endpoints))
        return {"date": day.isoformat(), "data": {"stats": {"steps": 1000}}, "errors": {}}

    def wellness_files(self, day):
        self.file_days.append(day)
        return None                                   # nothing on the watch that day

    def account_snapshot(self, history_start=None, today=None):
        self.accounts.append(history_start)
        return {"taken": "now", "data": {"profile": {}}, "errors": {}}


def settings(tmp_path):
    return SimpleNamespace(data_dir=tmp_path, timezone=VIENNA)


def test_the_hourly_backup_takes_new_activities_final_days_and_the_account_weekly(tmp_path):
    st, g = Store(tmp_path), BackupGarmin()
    now = datetime(2026, 10, 13, 13, 0, tzinfo=VIENNA)
    m = backup.backup_once(settings(tmp_path), g, st, now=now)
    assert m["activities"] == 1 and st.fit_path("42").is_file() and st.activity_json_path("42").is_file()
    days = [d for d, _ in g.days]
    assert days == [date(2026, 10, 6) + timedelta(days=n) for n in range(7)]     # the last week, up to yesterday
    assert m["wellness_days"] == 7 and st.load_snapshot(date(2026, 10, 12)) is not None
    assert len(g.file_days) == 7 and m["account"] == 1
    # an hour later: everything is final already (read after its late-sync point) – nothing again
    g.days.clear()
    m = backup.backup_once(settings(tmp_path), g, st, now=now + timedelta(hours=1))
    assert g.days == [] and m["activities"] == 0 and m["account"] == 0 and g.downloads == 1
    # the next day at noon: yesterday for the first time, and the day three days back once more
    m = backup.backup_once(settings(tmp_path), g, st, now=datetime(2026, 10, 14, 12, 30, tzinfo=VIENNA))
    assert [d for d, _ in g.days] == [date(2026, 10, 11), date(2026, 10, 13)]
    st.close()


def test_a_day_is_read_again_three_days_later_for_a_late_sync():
    day = date(2026, 10, 12)
    assert backup._due(day, datetime(2026, 10, 13, 11, 0, tzinfo=VIENNA), 0) is False       # before noon
    assert backup._due(day, datetime(2026, 10, 13, 12, 0, tzinfo=VIENNA), 0) is True
    first = datetime(2026, 10, 13, 12, 5, tzinfo=VIENNA).timestamp()
    assert backup._due(day, datetime(2026, 10, 14, 12, 0, tzinfo=VIENNA), 1, first) is False
    assert backup._due(day, datetime(2026, 10, 15, 12, 0, tzinfo=VIENNA), 1, first) is True
    assert backup._due(day, datetime(2026, 10, 20, 12, 0, tzinfo=VIENNA), 2, first) is False


def test_the_past_skips_days_on_disk_and_takes_the_account_with_its_history(tmp_path):
    st, g = Store(tmp_path), BackupGarmin()
    st.save_snapshot(date(2026, 1, 2), {"data": {"stats": {}}})
    m = backup.backup_range(settings(tmp_path), g, st, start=date(2026, 1, 1), end=date(2026, 1, 3),
                            scopes={"activities", "wellness", "account"}, pause=0)
    assert [d for d, _ in g.days] == [date(2026, 1, 1), date(2026, 1, 3)]          # 2 Jan was there
    assert m["account"] == 1 and g.accounts == [date(2026, 1, 1)]
    m = backup.backup_range(settings(tmp_path), g, st, start=date(2026, 1, 1), end=date(2026, 1, 3),
                            scopes={"wellness"}, endpoints=("hill_score",), pause=0)
    assert m["wellness_days"] == 3 and g.days[-1] == (date(2026, 1, 3), ("hill_score",))   # added to every day
    st.close()


def test_backup_needs_no_intervals_key_and_runs_next_to_the_bridge(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("BRIDGE_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("INTERVALS_API_KEY", raising=False)
    monkeypatch.setattr(cli.GarminSource, "login", lambda self, interactive=True: None)
    monkeypatch.setattr(cli, "IntervalsClient", lambda *a, **k: pytest.fail("backup must not need Intervals"))
    monkeypatch.setattr(cli, "backup_once", lambda *a, **k: backup.new_metrics() | {"activities": 2})
    with single_instance(tmp_path, ("activities", "wellness")):                 # the background bridge
        assert cli.main(["backup"]) == 0
    assert '"activities": 2' in capsys.readouterr().out
    st = Store(tmp_path)
    assert [r["command"] for r in st.recent_runs(1)] == ["backup"]
    st.close()


def test_run_in_backup_mode_never_asks_intervals(tmp_path, monkeypatch):
    monkeypatch.setenv("BRIDGE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("BRIDGE_MODE", "backup")
    monkeypatch.delenv("INTERVALS_API_KEY", raising=False)
    monkeypatch.setattr(cli, "IntervalsClient", lambda *a, **k: pytest.fail("no Intervals in backup mode"))
    monkeypatch.setattr(cli, "backup_once", lambda *a, **k: backup.new_metrics() | {"wellness_days": 1})
    monkeypatch.setattr(cli.time, "sleep", lambda s: None)
    assert cli.main(["run", "--iterations", "2"]) == 0
    st = Store(tmp_path)
    assert [r["command"] for r in st.recent_runs(1)] == ["backup", "backup"]
    st.close()


def test_mode_setting_is_checked(monkeypatch):
    monkeypatch.setenv("BRIDGE_MODE", "mirror")
    with pytest.raises(ValueError, match="BRIDGE_MODE"):
        config._mode()


def test_health_in_backup_mode(tmp_path):
    from garmin_intervals_bridge.health import probe

    class Garmin:
        def login(self, interactive=False):
            return None
    st = Store(tmp_path)
    st.record_run("backup", backup.new_metrics() | {"activities": 1, "wellness_days": 2, "garmin_requests": 40})
    s = SimpleNamespace(data_dir=tmp_path, timezone=VIENNA, stale_hours=24, wellness_profile="recommended")
    report = probe(s, Garmin(), None, st)
    assert set(report["services"]) == {"garmin"} and report["exit"] == 0
    summary = next(f for f in report["findings"] if f["what"] == "last 24 h")
    assert "1 activities, 2 days of wellness, 40 Garmin requests" in summary["detail"]
    st.close()
