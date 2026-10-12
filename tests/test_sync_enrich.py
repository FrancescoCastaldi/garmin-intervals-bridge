from datetime import date
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from test_enrich import ITEMS
from test_fit import build_fit

from garmin_intervals_bridge.store import Store
from garmin_intervals_bridge.sync import sync_enrich


def settings(tmp_path):
    return SimpleNamespace(data_dir=tmp_path, timezone=ZoneInfo("Europe/Vienna"), activity_days=3)


class GarminFake:
    def __init__(self):
        self.acts = [{"activityId": 42, "startTimeGMT": "2026-10-07 08:00:00", "duration": 3.0}]
        self.downloads, self.details, self.extras_calls = 0, 0, 0

    def activities(self, start, end, fields=None, limit=None):
        return self.acts

    def original_fit(self, gid):
        self.downloads += 1
        return build_fit(records=3, training_effect=3.2)       # the device original

    def activity(self, gid):
        self.details += 1
        return next(a for a in self.acts if str(a["activityId"]) == str(gid))

    def activity_extras(self, activity, keys=None):
        self.extras_calls += 1
        found = {"weather": {"temp": 12}, "gear": None, "exercise_sets": None, "typed_splits": {"splits": []},
                 "split_summaries": {"splitSummaries": []}}
        return {**{k: v for k, v in found.items() if keys is None or k in keys}, "errors": {}}


class IntervalsFake:
    """One officially imported activity whose partner file lacks the training effect."""

    def __init__(self):
        self.remote = [{"id": "i42", "source": "GARMIN_CONNECT", "external_id": "42",
                        "start_date": "2026-10-07T08:00:00Z", "elapsed_time": 3}]
        self.activity_obj = {"id": "i42", "stream_types": ["time"], "AerobicEffect": 0.0, "Sweatloss": None}
        self.updates, self.stream_puts = [], []

    def activities(self, start, end, fields=None, limit=None):
        return self.remote

    def activity(self, iid):
        return dict(self.activity_obj)

    def activity_file(self, iid):
        return build_fit(records=3, training_effect=None)      # filtered partner copy

    def custom_items(self):
        return ITEMS

    def streams(self, iid, types=None):
        return [{"type": "time", "data": [0, 1, 2]}]

    def update_activity(self, iid, fields):
        self.updates.append((iid, fields))
        self.activity_obj.update(fields)
        return {"id": iid}

    def put_streams(self, iid, body):
        self.stream_puts.append((iid, body))
        return {"updated": [b["type"] for b in body], "deleted": []}


def test_scheduled_enrich_dry_run_plans_but_writes_nothing(tmp_path):
    st, g, i = Store(tmp_path), GarminFake(), IntervalsFake()
    m = sync_enrich(settings(tmp_path), g, i, st, apply=False, today=date(2026, 10, 8))
    assert m["seen"] == 1 and m["planned"] == 1 and m["enriched"] == 0
    assert i.updates == [] and i.stream_puts == []
    assert st.enrichment("42") is None
    st.close()


def test_scheduled_enrich_writes_once_then_remembers(tmp_path):
    st, g, i = Store(tmp_path), GarminFake(), IntervalsFake()
    m = sync_enrich(settings(tmp_path), g, i, st, apply=True, today=date(2026, 10, 8))
    assert m["enriched"] == 1 and m["fields_written"] == 1
    # AerobicEffect was 0.0 from the filtered file and its source is absent there -> replaced
    assert i.updates == [("i42", {"AerobicEffect": 3.2})]
    assert st.enrichment("42")["fields"] == ["AerobicEffect"]
    again = sync_enrich(settings(tmp_path), g, i, st, apply=True, today=date(2026, 10, 8))
    assert again["already_enriched"] == 1 and len(i.updates) == 1 and g.downloads == 1
    st.close()


def test_scheduled_enrich_waits_for_the_official_import(tmp_path):
    st, g, i = Store(tmp_path), GarminFake(), IntervalsFake()
    i.remote = []                                              # not imported yet
    m = sync_enrich(settings(tmp_path), g, i, st, apply=True, today=date(2026, 10, 8))
    assert m["unmatched"] == 1 and m["failed"] == 0 and i.updates == []
    assert st.failed_activities() == []                        # not an error, just not yet
    st.close()


def test_scheduled_enrich_completes_the_local_mirror(tmp_path):
    st, g, i = Store(tmp_path), GarminFake(), IntervalsFake()
    m = sync_enrich(settings(tmp_path), g, i, st, apply=True, today=date(2026, 10, 8))
    assert m["archived"] == 1 and g.details == 1 and g.extras_calls == 1
    assert st.activity_json_path("42").is_file() and st.activity_extras_path("42").is_file()
    again = sync_enrich(settings(tmp_path), g, i, st, apply=True, today=date(2026, 10, 8))
    assert again["archived"] == 0 and g.details == 1 and g.extras_calls == 1       # nothing is asked twice
    st.close()


def test_archive_waits_for_the_extras_of_a_fresh_activity(tmp_path):
    from datetime import datetime, timezone

    from garmin_intervals_bridge.sync import archive_activity
    st, g = Store(tmp_path), GarminFake()
    activity = g.acts[0]
    g.activity_extras = lambda a, keys=None: {"weather": None, "gear": None, "exercise_sets": None,
                                              "typed_splits": None, "split_summaries": None,
                                              "errors": {"weather": "HTTPError"}}
    just_after = datetime(2026, 10, 7, 9, 0, tzinfo=timezone.utc)
    assert archive_activity("42", activity, g, st, now=just_after) == "pending"
    assert st.activity_json_path("42").is_file() and not st.activity_extras_path("42").is_file()
    assert archive_activity("42", activity, g, st, now=just_after) == "pending"      # asked again next pass
    days_later = datetime(2026, 10, 10, 9, 0, tzinfo=timezone.utc)
    assert archive_activity("42", activity, g, st, now=days_later) == "archived"     # Garmin never filled it in
    assert st.activity_extras_path("42").is_file()
    assert archive_activity("42", activity, g, st, now=days_later) == "on_disk"
    st.close()


def test_scheduled_enrich_isolates_a_broken_activity(tmp_path):
    st, g, i = Store(tmp_path), GarminFake(), IntervalsFake()
    g.acts = [{"activityId": 41, "startTimeGMT": "2026-10-07 07:00:00"}] + g.acts
    original = g.original_fit
    g.original_fit = lambda gid: (_ for _ in ()).throw(ValueError("no file")) if str(gid) == "41" else original(gid)
    m = sync_enrich(settings(tmp_path), g, i, st, apply=True, today=date(2026, 10, 8))
    assert m["failed"] == 1 and m["enriched"] == 1
    assert st.activity_status("41") == "failed"
    st.close()


def test_one_activity_again_after_intervals_reprocess_file(tmp_path):
    from garmin_intervals_bridge.enrich import enrich_activity, resolve_one
    st, g, i = Store(tmp_path), GarminFake(), IntervalsFake()
    sync_enrich(settings(tmp_path), g, i, st, apply=True, today=date(2026, 10, 8))
    i.activity_obj["AerobicEffect"] = 0.0                     # "Reprocess file": the filtered copy's value again
    i.activity_obj["external_id"] = "42"                      # what the official sync stores
    gid, iid = resolve_one(i, "https://intervals.icu/activities/i42")
    assert (gid, iid) == ("42", "i42")
    assert enrich_activity(gid, g, i, st, apply=True, intervals_id=iid)["outcome"] == "already_enriched"
    plan = enrich_activity(gid, g, i, st, apply=True, intervals_id=iid, refresh_own=True, recheck=True)
    assert plan["outcome"] == "enriched" and i.updates[-1] == ("i42", {"AerobicEffect": 3.2})
    assert g.downloads == 1                                    # the archived original, not a new download
    st.close()
