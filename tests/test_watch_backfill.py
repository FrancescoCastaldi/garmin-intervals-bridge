import time
from datetime import date
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from garminconnect import GarminConnectConnectionError
from requests import HTTPError, Response
from test_mapping import sample
from test_sync_enrich import GarminFake, IntervalsFake

from garmin_intervals_bridge.store import Store
from garmin_intervals_bridge.sync import _error_name, sync_wellness, watch_once


def settings(tmp_path):
    return SimpleNamespace(data_dir=tmp_path, timezone=ZoneInfo("Europe/Vienna"), activity_days=3,
                           wellness_days=3, wellness_refresh_hours=8)


class PollingIntervals(IntervalsFake):
    def __init__(self):
        super().__init__()
        self.polls = []

    def activities(self, start, end, fields=None, limit=None):
        self.polls.append((fields, limit))
        return self.remote + [{"id": "i7", "source": "UPLOAD", "external_id": "7_ACTIVITY.fit"}]


def test_watch_contacts_garmin_only_for_unseen_official_imports(tmp_path):
    st, g, i = Store(tmp_path), GarminFake(), PollingIntervals()
    m = watch_once(settings(tmp_path), g, i, st, apply=True, today=date(2026, 10, 8))
    assert i.polls[0] == (["id", "external_id", "source", "start_date"], 50)
    assert m["new"] == 2 and m["ignored"] == 1 and m["enriched"] == 1
    assert g.downloads == 1 and i.updates == [("i42", {"AerobicEffect": 3.2})]
    # Next minute: nothing new, no Garmin call, no write
    m = watch_once(settings(tmp_path), g, i, st, apply=True, today=date(2026, 10, 8))
    assert m["new"] == 0 and g.downloads == 1 and len(i.updates) == 1
    st.close()


def test_watch_dry_run_plans_without_writing(tmp_path):
    st, g, i = Store(tmp_path), GarminFake(), PollingIntervals()
    m = watch_once(settings(tmp_path), g, i, st, apply=False, today=date(2026, 10, 8))
    assert m["planned"] == 1 and i.updates == []
    st.close()


class NotReadyGarmin(GarminFake):
    def original_fit(self, gid):
        raise GarminConnectConnectionError("API Error 503")


def test_a_garmin_server_error_right_after_the_sync_is_retried_within_minutes(tmp_path, caplog):
    st, g, i = Store(tmp_path), NotReadyGarmin(), PollingIntervals()
    with caplog.at_level("WARNING"):
        m = watch_once(settings(tmp_path), g, i, st, apply=True, today=date(2026, 10, 8))
    assert m["failed"] == 1 and "GarminConnectConnectionError (HTTP 503)" in caplog.text
    (failed,) = st.failed_activities()
    assert failed["next_retry"] - time.time() <= 10 * 60
    st.close()


def test_error_name_carries_the_http_status():
    response = Response()
    response.status_code = 502
    assert _error_name(HTTPError("Bad Gateway", response=response)) == "HTTPError (HTTP 502)"
    assert _error_name(GarminConnectConnectionError("API Error 500 - oops")) == "GarminConnectConnectionError (HTTP 500)"
    assert _error_name(ValueError("value 503 out of range")) == "ValueError"


class WellnessGarmin:
    def __init__(self):
        self.days = []

    def snapshot(self, day, endpoints=None):
        self.days.append(day)
        return sample()


class WellnessIntervals:
    def __init__(self):
        self.writes = []

    def wellness(self, day):
        return {"locked": False}

    def write_wellness(self, day, patch):
        self.writes.append((day, patch))

    def provision_fields(self, apply, needed):
        return list(needed)


def test_backfill_walks_an_explicit_day_list_in_order(tmp_path):
    st, g, i = Store(tmp_path), WellnessGarmin(), WellnessIntervals()
    days = [date(2026, 9, 1), date(2026, 9, 2), date(2026, 9, 3)]
    m = sync_wellness(settings(tmp_path), g, i, st, apply=True, days=days, pause_seconds=0,
                      today=date(2026, 10, 8))
    assert g.days == days and m["days_checked"] == 3 and m["writes"] == 3
    assert all(st.wellness_recent(d, 8) for d in days)
    # A second backfill skips fetched days unless forced
    m = sync_wellness(settings(tmp_path), g, i, st, apply=True, days=days, pause_seconds=0,
                      today=date(2026, 10, 8))
    assert m["days_skipped_recent"] == 3 and len(g.days) == 3
    st.close()


def test_yesterday_is_fetched_once_more_after_midnight(tmp_path):
    import time
    st = Store(tmp_path)
    yesterday = date(2026, 10, 7)
    # fetched "yesterday evening": within the throttle window, but before today's midnight
    st.mark_wellness(yesterday)
    st.db.execute("UPDATE wellness SET fetched = ? WHERE day = ?", (time.time() - 2 * 3600, yesterday.isoformat()))
    st.db.commit()
    midnight_later_than_fetch = time.time() - 3600
    assert st.wellness_recent(yesterday, 8) is True
    assert st.wellness_recent(yesterday, 8, since=midnight_later_than_fetch) is False
    st.mark_wellness(yesterday)                           # fetched again after midnight
    assert st.wellness_recent(yesterday, 8, since=midnight_later_than_fetch) is True
    st.close()
