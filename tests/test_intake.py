"""Fluid and food logged for an activity, from Garmin's summary into Intervals."""
import json
import os
from datetime import date, datetime, timezone

from garmin_intervals_bridge.intake import ensure_fields, field_item, intake_values, write_intake
from garmin_intervals_bridge.store import Store
from garmin_intervals_bridge.sync import backfill_intake, complete_intake

SUMMARY = {"activityId": 42, "summaryDTO": {"startTimeLocal": "2026-09-20T13:56:00.0", "waterConsumed": 2250.0,
                                            "caloriesConsumed": 1099.99, "waterEstimated": 2108.0}}


class Intervals:
    def __init__(self, activity=None, listing=None):
        self.record = dict(activity or {})
        self.listing = listing or []
        self.items = []
        self.updates = []

    def custom_items(self):
        return list(self.items)

    def create_custom_item(self, item):
        self.items.append(item)
        return {"id": len(self.items)}

    def activity(self, iid):
        return dict(self.record)

    def update_activity(self, iid, fields):
        self.updates.append((iid, fields))
        self.record.update(fields)

    def activities(self, start, end, fields=None, limit=None):
        return self.listing


def test_values_come_from_the_summary_and_nothing_logged_stays_out():
    assert intake_values(SUMMARY) == {"GarminFluidIntake": 2250, "GarminCaloriesConsumed": 1100}
    assert intake_values({"summaryDTO": {"waterConsumed": 0, "caloriesConsumed": None}}) == {}


def test_fields_are_created_once_and_only_empty_ones_are_filled():
    i = Intervals({"GarminCaloriesConsumed": 800})
    assert write_intake("i1", intake_values(SUMMARY), i, apply=False) == {"GarminFluidIntake": 2250}
    assert i.updates == [] and i.items == []                            # a dry run writes nothing
    assert write_intake("i1", intake_values(SUMMARY), i, apply=True) == {"GarminFluidIntake": 2250}
    assert i.updates == [("i1", {"GarminFluidIntake": 2250})]
    assert [it["content"]["code"] for it in i.items] == ["GarminFluidIntake"]
    content = field_item("GarminFluidIntake")["content"]
    assert content["units"] == "ml" and content["aggregate"] == "SUM" and content["fit_session_field"] is None
    assert ensure_fields(i, {"GarminFluidIntake"}) == []                # exists now: not created twice


class Garmin:
    def __init__(self):
        self.asked = 0

    def activity(self, gid):
        self.asked += 1
        return SUMMARY


def test_the_summary_is_asked_again_once_half_a_day_later(tmp_path):
    st, g, i = Store(tmp_path), Garmin(), Intervals()
    activity = {"activityId": 42, "startTimeGMT": "2026-09-20 11:56:00"}
    st.save_activity_json("42", {"summaryDTO": {}})                     # archived right after the ride
    os.utime(st.activity_json_path("42"), (1, 1))
    metrics = {}
    complete_intake("42", activity, "i1", g, i, st, metrics, apply=True,
                    now=datetime(2026, 9, 20, 18, 0, tzinfo=timezone.utc))
    assert g.asked == 0 and i.updates == []                             # too early: the drink may come
    complete_intake("42", activity, "i1", g, i, st, metrics, apply=True,
                    now=datetime(2026, 9, 21, 7, 0, tzinfo=timezone.utc))
    assert g.asked == 1 and i.updates == [("i1", {"GarminFluidIntake": 2250, "GarminCaloriesConsumed": 1100})]
    assert json.loads(st.activity_json_path("42").read_text())["summaryDTO"]["waterConsumed"] == 2250
    complete_intake("42", activity, "i1", g, i, st, metrics, apply=True,
                    now=datetime(2026, 9, 22, 7, 0, tzinfo=timezone.utc))
    assert g.asked == 1 and metrics["intake_written"] == 1             # once per activity
    st.close()


def test_the_past_is_filled_from_the_archive_by_garmins_activity_id(tmp_path):
    st = Store(tmp_path)
    st.save_activity_json("42", SUMMARY)
    st.save_activity_json("43", {"summaryDTO": {"startTimeLocal": "2026-09-21T08:00:00.0", "waterConsumed": 500}})
    listing = [{"id": "i42", "external_id": "42", "source": "GARMIN_CONNECT"},
               {"id": "s43", "external_id": "43", "source": "STRAVA"}]     # a bare number from Strava is not ours
    i = Intervals(listing=listing)
    dry = backfill_intake(i, st, start=date(2026, 9, 1), end=date(2026, 9, 30), apply=False)
    assert dry == {"with_intake": 2, "written": 1, "already": 0, "unmatched": 1} and i.updates == []
    out = backfill_intake(i, st, start=date(2026, 9, 1), end=date(2026, 9, 30), apply=True)
    assert out["written"] == 1 and i.updates == [("i42", {"GarminFluidIntake": 2250, "GarminCaloriesConsumed": 1100})]
    st.close()


class GarminWithGear(Garmin):
    def __init__(self, gear):
        super().__init__()
        self.gear, self.gear_asked = gear, 0

    def activity_extras(self, activity, keys=None):
        assert keys == ("gear",)
        self.gear_asked += 1
        return {"gear": self.gear, "errors": {}}


def test_gear_picked_in_the_app_after_the_run_is_read_again_half_a_day_later(tmp_path):
    shoes = [{"uuid": "u1", "displayName": "Pegasus"}]
    st, g, i = Store(tmp_path), GarminWithGear(shoes), Intervals()
    activity = {"activityId": 42, "startTimeGMT": "2026-09-20 11:56:00"}
    st.save_activity_json("42", {"summaryDTO": {}})
    st.save_activity_extras("42", {"gear": [], "weather": {"temp": 12}, "errors": {}})   # archived before the pick
    for path in (st.activity_json_path("42"), st.activity_extras_path("42")):
        os.utime(path, (1, 1))
    complete_intake("42", activity, "i1", g, i, st, {}, apply=True, now=datetime(2026, 9, 21, 7, 0, tzinfo=timezone.utc))
    extras = json.loads(st.activity_extras_path("42").read_text())
    assert g.gear_asked == 1 and extras["gear"] == shoes and extras["weather"] == {"temp": 12}
    st.close()


def test_gear_archived_late_enough_is_not_asked_again(tmp_path):
    st, g, i = Store(tmp_path), GarminWithGear([]), Intervals()
    activity = {"activityId": 42, "startTimeGMT": "2026-09-20 11:56:00"}
    st.save_activity_json("42", {"summaryDTO": {}})
    st.save_activity_extras("42", {"gear": [], "errors": {}})            # read today: already after the pick
    complete_intake("42", activity, "i1", g, i, st, {}, apply=True, now=datetime(2026, 9, 21, 7, 0, tzinfo=timezone.utc))
    assert g.gear_asked == 0
    st.close()
