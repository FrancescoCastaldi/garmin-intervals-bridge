"""Swap mode: Garmin's filtered copy for the original – upload first, delete last, carefully."""
from datetime import date, datetime, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import requests
from test_fit import build_fit
from test_sync_enrich import GarminFake, IntervalsFake

from garmin_intervals_bridge.store import Store
from garmin_intervals_bridge.swap import retry_deletes, swap_activity
from garmin_intervals_bridge.sync import watch_once

NOW = datetime(2026, 10, 8, 9, 0, tzinfo=timezone.utc)


def http_error(status):
    response = requests.Response()
    response.status_code = status
    return requests.HTTPError(str(status), response=response)


class SwapIntervals(IntervalsFake):
    """Garmin's official copy i42 (filtered), with the athlete's RPE and a paired workout on it."""

    def __init__(self):
        super().__init__()
        self.activity_obj.update({"source": "GARMIN_CONNECT", "external_id": "42", "name": "Morning Ride",
                                  "description": "", "created": "2026-10-08T08:05:00Z", "icu_rpe": 6,
                                  "paired_event_id": 77, "type": "Ride", "icu_intervals_edited": False,
                                  "GarminCaloriesConsumed": 1200, "Computed": 5, "LTPace": "NaN"})
        self.comments, self.calls, self.created, self.delete_status = [], [], {}, None
        self.upload_result = None

    def activity(self, iid):
        if iid in self.created:
            return dict(self.created[iid])
        if iid == "i42" and "delete i42" in self.calls:
            raise http_error(404)
        return dict(self.activity_obj)

    def custom_items(self):
        return super().custom_items() + [
            {"type": "ACTIVITY_FIELD", "content": {"code": "GarminCaloriesConsumed", "type": "numeric"}},
            {"type": "ACTIVITY_FIELD", "content": {"code": "Computed", "script": "activity.x"}}]

    def activity_messages(self, iid):
        return list(self.comments)

    def upload_fit(self, gid, path, **extra):
        self.calls.append(("upload", gid, extra))
        if self.upload_result is not None:
            return self.upload_result
        self.created["i99"] = {"id": "i99", "source": "UPLOAD", "external_id": f"garmin:{gid}", "type": "Ride",
                               "name": extra.get("name"), "paired_event_id": None}
        return {"created": True, "items": [{"id": "i99"}]}

    def update_activity(self, iid, fields):
        self.calls.append(("update", iid, fields))
        if iid in self.created:
            self.created[iid].update(fields)
        return {"id": iid}

    def delete_activity(self, iid):
        if self.delete_status:
            raise http_error(self.delete_status)
        self.calls.append(f"delete {iid}")


def test_swap_uploads_the_original_carries_the_athletes_values_then_deletes_the_copy(tmp_path):
    st, g, i = Store(tmp_path), GarminFake(), SwapIntervals()
    out = swap_activity("42", "i42", g, i, st, apply=True, now=NOW)
    assert out["outcome"] == "swapped" and out["new_id"] == "i99"
    upload, *rest = i.calls
    assert upload == ("upload", "42", {"name": "Morning Ride", "description": "", "paired_event_id": 77})
    carried = {"icu_rpe": 6, "GarminCaloriesConsumed": 1200}       # typed values, not the computed one
    assert ("update", "i99", carried) in rest
    assert rest.index("delete i42") > rest.index(("update", "i99", carried))           # delete last
    assert ("update", "i99", {"paired_event_id": 77}) in rest                            # paired again
    assert st.activity_status("42") == "uploaded" and st.intervals_seen_source("i99") == "UPLOAD"
    st.close()


def test_a_dry_run_only_plans(tmp_path):
    st, i = Store(tmp_path), SwapIntervals()
    out = swap_activity("42", "i42", GarminFake(), i, st, apply=False, now=NOW)
    assert out["outcome"] == "planned" and "icu_rpe" in out["carry"] and i.calls == []
    st.close()


def test_guards_keep_the_copy(tmp_path):
    st, g = Store(tmp_path), GarminFake()
    i = SwapIntervals()
    i.comments = [{"content": "nice ride"}]
    assert swap_activity("42", "i42", g, i, st, apply=True, now=NOW)["reason"] == "it has comments"
    i = SwapIntervals()
    late = datetime(2026, 10, 11, 9, 0, tzinfo=timezone.utc)
    assert "more than 48 h" in swap_activity("42", "i42", g, i, st, apply=True, now=late)["reason"]
    assert swap_activity("42", "i42", g, i, st, apply=True, window_hours=None, now=late)["outcome"] == "swapped"
    st.close()
    st, i = Store(tmp_path / "b"), SwapIntervals()
    i.activity_obj["icu_intervals_edited"] = True
    assert swap_activity("42", "i42", g, i, st, apply=True, now=NOW)["reason"] == "its intervals were edited by hand"
    i.activity_obj.update(icu_intervals_edited=False, source="STRAVA")
    assert "not Garmin's import" in swap_activity("42", "i42", g, i, st, apply=True, now=NOW)["reason"]
    assert i.calls == []
    st.close()


def test_nothing_to_swap_when_garmin_sent_the_original(tmp_path):
    st, i = Store(tmp_path), SwapIntervals()
    i.activity_file = lambda iid: build_fit(records=3, training_effect=3.2)      # the very same file
    assert swap_activity("42", "i42", GarminFake(), i, st, apply=True, now=NOW)["outcome"] == "same"
    assert i.calls == []
    st.close()


def test_never_a_loss(tmp_path):
    # Intervals already had the file: nothing deleted
    st, i = Store(tmp_path), SwapIntervals()
    i.upload_result = {"created": False, "items": []}
    assert swap_activity("42", "i42", GarminFake(), i, st, apply=True, now=NOW)["outcome"] == "kept"
    assert not any(str(c).startswith("delete") for c in i.calls)
    st.close()
    # the original became two activities (multisport): nothing deleted, a person looks
    st, i = Store(tmp_path / "b"), SwapIntervals()
    i.upload_result = {"created": True, "items": [{"id": "i98"}, {"id": "i99"}]}
    assert swap_activity("42", "i42", GarminFake(), i, st, apply=True, now=NOW)["outcome"] == "review"
    assert not any(str(c).startswith("delete") for c in i.calls)
    st.close()


def test_a_failed_delete_is_tried_again(tmp_path):
    st, i = Store(tmp_path), SwapIntervals()
    i.delete_status = 502
    assert swap_activity("42", "i42", GarminFake(), i, st, apply=True, now=NOW)["outcome"] == "delete_failed"
    assert st.meta_items("swap:delete:*") == [("swap:delete:i42", "i99")]
    i.delete_status = None
    assert retry_deletes(i, st) == 1 and "delete i42" in i.calls
    assert retry_deletes(i, st) == 0                                   # done once
    st.close()


def test_watch_swaps_new_imports_when_swap_mode_is_on_and_completes_kept_ones(tmp_path):
    class Watched(SwapIntervals):
        def activities(self, start, end, fields=None, limit=None):
            return [{"id": "i42", "source": "GARMIN_CONNECT", "external_id": "42", "start_date": "2026-10-08T08:00:00Z"}]

    settings = SimpleNamespace(data_dir=tmp_path, timezone=ZoneInfo("Europe/Vienna"), swap=True, swap_hours=10_000)
    st, i = Store(tmp_path), Watched()
    m = watch_once(settings, GarminFake(), i, st, apply=True, today=date(2026, 10, 8))
    assert m["swap_swapped"] == 1 and m["enriched"] == 0 and i.updates == []          # no enrich of the copy
    st.close()
    st, i = Store(tmp_path / "b"), Watched()
    i.comments = [{"content": "!"}]
    settings.data_dir = tmp_path / "b"
    m = watch_once(settings, GarminFake(), i, st, apply=True, today=date(2026, 10, 8))
    assert m["swap_kept"] == 1 and m["enriched"] == 1                                    # completed instead
    st.close()


def test_enrich_after_a_swap_rewrites_no_streams_into_the_original(tmp_path, monkeypatch):
    from garmin_intervals_bridge import enrich
    st, g, i = Store(tmp_path), GarminFake(), SwapIntervals()
    st.record_enrichment("42", "i42", "old-sha", ["AerobicEffect"], ["Stamina"])      # the copy was enriched
    swap_activity("42", "i42", g, i, st, apply=True, now=NOW)
    seen = []
    real = enrich.plan_streams
    monkeypatch.setattr(enrich, "plan_streams", lambda *a, **k: seen.append(k.get("refresh")) or real(*a, **k))
    i.activity_obj = {**i.created["i99"], "stream_types": ["time"]}
    i.activity_file = lambda iid: build_fit(records=3, training_effect=3.2)          # Intervals holds the original
    enrich.enrich_activity("42", g, i, st, apply=False, intervals_id="i99", refresh_own=True, recheck=True)
    assert seen == [None]                          # the copy's streams are not "own streams" of the original
    st.close()
