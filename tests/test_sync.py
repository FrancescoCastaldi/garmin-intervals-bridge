from datetime import date
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from test_fit import minimal_fit
from test_mapping import sample

from garmin_intervals_bridge.fit import validate_fit
from garmin_intervals_bridge.garmin import GarminBlocked
from garmin_intervals_bridge.store import Store
from garmin_intervals_bridge.sync import activity_match, sync_activities, sync_wellness


def settings(tmp_path):
    return SimpleNamespace(data_dir=tmp_path, timezone=ZoneInfo("Europe/Vienna"),
                           activity_days=3, wellness_days=3, wellness_refresh_hours=8)


class GarminFake:
    def __init__(self):
        self.acts = [{"activityId": 42, "startTimeGMT": "2026-10-07 08:00:00",
                      "activityName": "Ride"}]
        self.download_count = 0

    def activities(self, start, end, fields=None, limit=None):
        return self.acts

    def original_fit(self, activity_id):
        self.download_count += 1
        return minimal_fit()

    def activity(self, activity_id):
        return next(a for a in self.acts if str(a["activityId"]) == str(activity_id))

    def activity_extras(self, activity, keys=None):
        found = {"weather": {"temp": 20}, "gear": None, "exercise_sets": None, "typed_splits": {"splits": []},
                 "split_summaries": {"splitSummaries": []}}
        return {**{k: v for k, v in found.items() if keys is None or k in keys}, "errors": {}}

    def snapshot(self, day, endpoints=None):
        return sample()


class IntervalsFake:
    def __init__(self, remote=None):
        self.remote = remote or []
        self.uploads = []
        self.wellness_writes = []
        self.provision_calls = []
        self.current_wellness = {}
        self.fail_upload = False

    def activities(self, start, end, fields=None, limit=None):
        return self.remote

    def nearby_activities(self, day):
        return self.remote

    def upload_fit(self, gid, path):
        if self.fail_upload:
            raise TimeoutError("Connection failed after server may have accepted upload")
        self.uploads.append((gid, path))
        return {"created": True, "items": [{"id": "i88"}]}

    def wellness(self, day):
        return self.current_wellness

    def write_wellness(self, day, patch):
        self.wellness_writes.append((day, patch))

    def provision_fields(self, apply, needed):
        self.provision_calls.append((apply, needed))
        return list(needed)


def test_match_by_external_id_or_start_and_fail_closed():
    a = {"activityId": 42, "startTimeGMT": "2026-10-07T08:00:00"}
    assert activity_match(a, [{"id": "i1", "external_id": "garmin:42"}])["id"] == "i1"
    assert activity_match(a, [{"id": "i2", "start_date": "2026-10-07T08:01:00Z"}])["id"] == "i2"
    assert activity_match(a, [{"id": "i3", "start_date": "2026-10-07T12:00:00Z"}]) is None
    with pytest.raises(ValueError):
        activity_match({"activityId": 42}, [])


def test_dry_run_archives_but_does_not_upload(tmp_path):
    st = Store(tmp_path)
    g, i = GarminFake(), IntervalsFake()
    result = sync_activities(settings(tmp_path), g, i, st, apply=False, allow_upload=False,
                             today=date(2026, 10, 8))
    assert result["downloaded"] == 1 and result["would_upload"] == 1
    assert i.uploads == []
    validate_fit(st.fit_path("42").read_bytes())
    st.close()


def test_remote_existing_activity_never_overwritten(tmp_path):
    st = Store(tmp_path)
    g = GarminFake()
    i = IntervalsFake(remote=[{"id": "i42", "source": "GARMIN_CONNECT",
                              "start_date": "2026-10-07T08:00:20Z"}])
    result = sync_activities(settings(tmp_path), g, i, st, apply=True, allow_upload=True,
                             today=date(2026, 10, 8))
    assert result["remote_exists"] == 1 and not i.uploads
    assert st.activity_status("42") == "remote_exists"
    st.close()


def test_upload_timeout_leaves_pending_and_run_continues(tmp_path):
    st = Store(tmp_path)
    g, i = GarminFake(), IntervalsFake()
    i.fail_upload = True
    result = sync_activities(settings(tmp_path), g, i, st, apply=True, allow_upload=True,
                             today=date(2026, 10, 8))
    # The upload's outcome is unknown: pending, not failed, and not retried blindly.
    assert result["pending"] == 1 and result["failed"] == 0
    assert st.activity_status("42") == "pending"
    i.fail_upload = False
    result = sync_activities(settings(tmp_path), g, i, st, apply=True, allow_upload=True,
                             today=date(2026, 10, 8))
    assert result["pending"] == 1 and i.uploads == []
    assert st.failed_activities() == []
    st.close()


def test_successful_upload_is_only_once(tmp_path):
    st = Store(tmp_path)
    g, i = GarminFake(), IntervalsFake()
    result = sync_activities(settings(tmp_path), g, i, st, apply=True, allow_upload=True,
                             today=date(2026, 10, 8))
    assert result["uploaded"] == 1
    assert st.activity_status("42") == "uploaded"
    sync_activities(settings(tmp_path), g, i, st, apply=True, allow_upload=True,
                    today=date(2026, 10, 8))
    assert len(i.uploads) == 1
    st.close()


def test_wellness_dryrun_does_not_write(tmp_path):
    st = Store(tmp_path)
    g, i = GarminFake(), IntervalsFake()
    i.current_wellness = {"locked": False, "restingHR": 44, "BodyBatteryMax": 95, "TrainingAdvice": 2}
    result = sync_wellness(settings(tmp_path), g, i, st, apply=False, wellness_days=2,
                           today=date(2026, 10, 8), force=True)
    assert result["days_checked"] == 2
    assert result["days_with_changes"] == 2
    assert not i.wellness_writes
    assert i.provision_calls and all(not apply for apply, _ in i.provision_calls)
    fetched, written = st.wellness_state(date(2026, 10, 8))
    assert fetched is not None and written is None          # read counts for the throttle, nothing written
    st.close()


def test_wellness_apply_never_overwrites_existing_and_respects_lock(tmp_path):
    st = Store(tmp_path)
    g, i = GarminFake(), IntervalsFake()
    i.current_wellness = {"locked": False, "hrv": 55, "BodyBatteryMax": 99}
    result = sync_wellness(settings(tmp_path), g, i, st, apply=True, wellness_days=1,
                           today=date(2026, 10, 8), force=True)
    assert result["writes"] == 1
    day, patch = i.wellness_writes[0]
    assert "hrv" not in patch and "BodyBatteryMax" not in patch
    assert "customFields" not in patch
    # Readiness may be written on the same day; running totals like Body Battery may not.
    assert patch["GarminTrainingReadiness"] == 72 and "BodyBatteryMin" not in patch
    assert st.wellness_recent(date(2026, 10, 8), 8)
    i.current_wellness = {"locked": True}
    result = sync_wellness(settings(tmp_path), g, i, st, apply=True, wellness_days=1,
                           force=True, today=date(2026, 10, 8))
    assert result["days_locked"] == 1
    assert len(i.wellness_writes) == 1
    st.close()


class FlakyGarmin(GarminFake):
    """Three activities; selected IDs raise on download, or Garmin blocks everything."""

    def __init__(self, fail_ids=(), blocked=False):
        super().__init__()
        self.acts = [{"activityId": 41, "startTimeGMT": "2026-10-07 07:00:00"},
                     {"activityId": 42, "startTimeGMT": "2026-10-07 08:00:00"},
                     {"activityId": 43, "startTimeGMT": "2026-10-07 09:00:00"}]
        self.fail_ids = {str(x) for x in fail_ids}
        self.blocked = blocked
        self.requested = []

    def original_fit(self, activity_id):
        self.requested.append(str(activity_id))
        if self.blocked:
            raise GarminBlocked("Garmin API blocked request: GarminConnectTooManyRequestsError")
        if str(activity_id) in self.fail_ids:
            raise ValueError("no original file for this activity")
        return super().original_fit(activity_id)


def run(tmp_path, st, g, i, **kw):
    kw.setdefault("apply", False)
    kw.setdefault("allow_upload", False)
    return sync_activities(settings(tmp_path), g, i, st, today=date(2026, 10, 8), **kw)


def test_upload_mode_completes_the_local_mirror(tmp_path):
    st, g, i = Store(tmp_path), FlakyGarmin(), IntervalsFake()
    out = run(tmp_path, st, g, i)
    assert out["archived"] == 3 and out["archive_failed"] == 0
    assert all(st.activity_extras_path(x).is_file() for x in ("41", "42", "43"))
    assert run(tmp_path, st, g, i)["archived"] == 0
    st.close()


def test_one_failing_download_does_not_block_the_others(tmp_path):
    st = Store(tmp_path)
    g, i = FlakyGarmin(fail_ids=[42]), IntervalsFake()
    result = run(tmp_path, st, g, i)
    assert result["failed"] == 1 and result["downloaded"] == 2 and result["would_upload"] == 2
    assert st.activity_status("41") == "downloaded"
    assert st.activity_status("42") == "failed"
    assert st.activity_status("43") == "downloaded"
    failed = st.failed_activities()
    assert [f["garmin_id"] for f in failed] == ["42"]
    assert failed[0]["attempts"] == 1 and "ValueError" in failed[0]["error"]
    st.close()


def test_failed_activity_is_deferred_then_retried_and_recovers(tmp_path):
    st = Store(tmp_path)
    g, i = FlakyGarmin(fail_ids=[42]), IntervalsFake()
    run(tmp_path, st, g, i)
    assert g.requested.count("42") == 1
    # Immediately afterwards: still inside the backoff window, no new attempt.
    result = run(tmp_path, st, g, i)
    assert result["deferred"] == 1 and result["failed"] == 0
    assert g.requested.count("42") == 1
    # Let the backoff expire, fix the cause, and the activity recovers.
    st.db.execute("UPDATE activity SET next_retry = next_retry - 7200 WHERE garmin_id='42'")
    st.db.commit()
    g.fail_ids = set()
    result = run(tmp_path, st, g, i)
    assert result["deferred"] == 0 and result["failed"] == 0
    assert st.activity_status("42") == "downloaded"
    attempts, error = st.db.execute("SELECT attempts, error FROM activity WHERE garmin_id='42'").fetchone()
    assert (attempts, error) == (0, None)
    st.close()


def test_backoff_grows_then_caps_at_a_week(tmp_path):
    import time
    st = Store(tmp_path)
    started = time.time()
    delays = []
    for _ in range(6):
        st.record_failure("7", "boom")
        attempts, next_retry = st.db.execute(
            "SELECT attempts, next_retry FROM activity WHERE garmin_id='7'").fetchone()
        delays.append(next_retry - started)
    assert attempts == 6
    expected = [600, 3600, 6 * 3600, 24 * 3600, 7 * 86400, 7 * 86400]
    for actual, wanted in zip(delays, expected):
        assert abs(actual - wanted) < 60
    assert st.is_deferred("7") is True
    assert st.is_deferred("7", now=started + 8 * 86400) is False
    st.close()


def test_garmin_block_aborts_the_run_and_blames_no_activity(tmp_path):
    st = Store(tmp_path)
    g, i = FlakyGarmin(blocked=True), IntervalsFake()
    with pytest.raises(GarminBlocked):
        run(tmp_path, st, g, i)
    assert g.requested == ["41"]
    assert st.failed_activities() == []
    assert st.activity_status("41") is None
    st.close()


def test_manual_activity_is_skipped_without_a_download(tmp_path):
    st = Store(tmp_path)
    g, i = FlakyGarmin(), IntervalsFake()
    g.acts.append({"activityId": 44, "startTimeGMT": "2026-10-07 10:00:00", "manualActivity": True})
    result = run(tmp_path, st, g, i)
    assert result["skipped"] == 1 and result["seen"] == 4
    assert st.activity_status("44") == "skipped"
    assert "44" not in g.requested
    assert not st.fit_path("44").exists()
    st.close()


def test_failure_never_downgrades_a_pending_upload(tmp_path):
    st = Store(tmp_path)
    st.record_activity("5", "pending", "abc")
    assert st.record_failure("5", "late error") == 0
    assert st.activity_status("5") == "pending"
    assert st.failed_activities() == []
    st.close()


def test_schema_migrates_a_v01_database_in_place(tmp_path):
    import sqlite3
    db = sqlite3.connect(str(tmp_path / "bridge.db"))
    db.execute("""CREATE TABLE activity (garmin_id TEXT PRIMARY KEY, status TEXT NOT NULL,
                  sha256 TEXT, intervals_id TEXT, updated REAL NOT NULL)""")
    db.execute("INSERT INTO activity VALUES ('9','uploaded','x','i9',1.0)")
    db.commit()
    db.close()
    st = Store(tmp_path)
    assert st.activity_status("9") == "uploaded"
    assert st.is_deferred("9") is False
    assert st.record_failure("9", "ignored") == 0 and st.activity_status("9") == "uploaded"
    st.close()


def test_upload_backfill_walks_a_date_range(tmp_path):
    st = Store(tmp_path)
    g, i = FlakyGarmin(), IntervalsFake()
    out = sync_activities(settings(tmp_path), g, i, st, apply=False, allow_upload=True,
                          date_range=(date(2026, 10, 1), date(2026, 10, 8)))
    assert out["seen"] == 3 and out["would_upload"] == 3 and g.download_count == 3
    st.close()
