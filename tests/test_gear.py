import json
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from garmin_intervals_bridge import gear
from garmin_intervals_bridge.store import Store

HAIBIKE = {"uuid": "u-hai", "displayName": "Haibike", "gearTypeName": "Bike", "dateBegin": "2017-12-31T00:00:00.0",
           "gearStatusName": "active"}
GRAIL = {"uuid": "u-grail", "displayName": "Grail CF SLX 8 Di2", "gearTypeName": "Bike",
         "dateBegin": "2023-06-02T00:00:00.0", "gearStatusName": "active"}
SHOES = {"uuid": "u-shoe", "displayName": '"Zoom Fly" Orange', "gearTypeName": "Shoes",
         "dateBegin": "2025-05-04T00:00:00.0", "gearStatusName": "active"}
OLD_SHOES = {"uuid": "u-old", "displayName": "Salewa Wildfire", "gearTypeName": "Shoes",
             "dateBegin": "2020-09-11T00:00:00.0", "dateEnd": "2024-06-06T00:00:00.0", "gearStatusName": "retired"}

INTERVALS_GEAR = [
    {"id": "b-hai", "name": "Haibike", "type": "Bike"},
    {"id": "b-grail", "name": "Canyon Grail CF SLX 8 Di2", "type": "Bike"},
    {"id": "c-frame", "name": "Grail CF SLX 8 Di2", "type": "Frame", "component": True},
    {"id": "s-zoom", "name": "Nike Zoom Fly - Rot", "type": "Shoes"},
]


class IntervalsFake:
    def __init__(self, activities, gear_list=None):
        self.remote = activities
        self.gear_list = gear_list if gear_list is not None else [dict(g) for g in INTERVALS_GEAR]
        self.assigned, self.recalculated, self.created, self.updated = [], [], [], []
        self.fail_for = set()

    def gear(self):
        return self.gear_list

    def activities(self, oldest, newest, fields=None, limit=None):
        return [a for a in self.remote if oldest.isoformat() <= a["start_date"][:10] <= newest.isoformat()]

    def set_activity_gear(self, activity_id, gear_id):
        if activity_id in self.fail_for:
            raise ConnectionError("down")
        self.assigned.append((activity_id, gear_id))
        for a in self.remote:
            if a["id"] == activity_id:
                a["gear"] = {"id": gear_id}

    def recalc_gear(self, gear_id):
        self.recalculated.append(gear_id)
        return {"distance": 4556000.0, "moving_time": 3600.0, "elapsed_time": 4000.0, "activities": 108}

    def update_gear(self, gear_id, fields):
        self.updated.append((gear_id, fields))

    def create_gear(self, payload):
        self.created.append(payload)
        return {"id": f"g{len(self.created)}", **payload}


def archive(store, gid, day, sport, gear_items, km=40):
    store.save_activity_json(gid, {"activityTypeDTO": {"typeKey": sport}, "startTimeGMT": f"{day} 08:00:00",
                                   "summaryDTO": {"startTimeLocal": f"{day}T10:00:00.0", "distance": km * 1000}})
    store.save_activity_extras(gid, {"gear": gear_items, "errors": {}})


def remote(iid, gid, day, gear_id=None, source="GARMIN_CONNECT", external=None):
    return {"id": iid, "start_date": f"{day}T08:00:00Z", "external_id": external if external is not None else gid,
            "source": source, "gear": {"id": gear_id} if gear_id else None, "type": "Ride", "distance": 40000}


class FakeSensors:
    def __init__(self, by_gid):
        self.by_gid = by_gid

    def get(self, gid):
        return frozenset(self.by_gid.get(gid, ()))

    def save(self):
        pass


def test_names_pair_without_components_and_with_an_explicit_map():
    assert gear.same_name("Grail CF SLX 8 Di2", "Canyon Grail CF SLX 8 Di2")
    assert gear.same_name("Blizzard Zero G 95", "Blizzard Zero G95")
    assert not gear.same_name('"Zoom Fly" Orange', "Nike Zoom Fly - Rot")
    ggear = {g["uuid"]: gear._garmin_gear(g) for g in (HAIBIKE, GRAIL, SHOES, OLD_SHOES)}
    pairs, why = gear.pair_gear(ggear, INTERVALS_GEAR)
    assert pairs == {"u-hai": "b-hai", "u-grail": "b-grail"}            # the frame component is no candidate
    assert why == {"u-shoe": "not in Intervals", "u-old": "not in Intervals"}
    pairs, _ = gear.pair_gear(ggear, INTERVALS_GEAR, ["Zoom Fly Orange=Nike Zoom Fly - Rot"])
    assert pairs["u-shoe"] == "s-zoom"
    with pytest.raises(ValueError):
        gear.pair_gear(ggear, INTERVALS_GEAR, ["Nonexistent=Haibike"])


def scene(tmp_path):
    st = Store(tmp_path)
    archive(st, "101", "2023-05-01", "mountain_biking", [HAIBIKE])        # gap
    archive(st, "102", "2023-05-06", "mountain_biking", [HAIBIKE])        # Intervals: Grail, bought only in June
    archive(st, "103", "2024-07-01", "indoor_cycling", [GRAIL])           # Intervals: Haibike, no proof
    archive(st, "104", "2025-01-10", "backcountry_skiing", [])            # Intervals only
    archive(st, "105", "2025-06-01", "running", [SHOES])                  # gap, shoes paired by --map
    archive(st, "106", "2025-06-03", "mountain_biking", [HAIBIKE])        # a Strava copy in Intervals
    archive(st, "107", "2023-09-10", "mountain_biking", [GRAIL])          # same on both sides
    acts = [remote("i101", "101", "2023-05-01"), remote("i102", "102", "2023-05-06", "b-grail"),
            remote("i103", "103", "2024-07-01", "b-hai"), remote("i104", "104", "2025-01-10", "b-hai"),
            remote("i105", "x_105.fit", "2025-06-01", external="user_999999999.fit"),     # matched by start time
            remote("i106", "106", "2025-06-03", source="STRAVA"), remote("i107", "107", "2023-09-10", "b-grail")]
    return st, IntervalsFake(acts)


def test_default_fills_gaps_only_and_never_removes(tmp_path):
    st, iv = scene(tmp_path)
    plan = gear.build_plan(st, iv, explicit=["Zoom Fly Orange=Nike Zoom Fly - Rot"])
    assert sorted((r["intervals_id"], r["target"], r["reason"]) for r in plan["assign"]) == [
        ("i101", "b-hai", "gap"), ("i105", "s-zoom", "gap")]
    kept = {r["intervals_id"]: r for r in plan["kept"]}
    assert set(kept) == {"i102", "i103"}
    assert kept["i102"]["proof"] == "Canyon Grail CF SLX 8 Di2 not bought until 2023-06-02"   # shown, not acted on
    assert plan["intervals_only"] == {"Haibike": 1}
    assert plan["skipped"] == {"Strava activity (Intervals does not let it be changed)": 1}
    st.close()


def test_evidence_replaces_only_what_the_record_disproves_and_garmin_replaces_all(tmp_path):
    st, iv = scene(tmp_path)
    plan = gear.build_plan(st, iv, policy="evidence")
    replaced = {r["intervals_id"]: r["reason"] for r in plan["assign"] if r.get("replaces")}
    assert replaced == {"i102": "evidence: Canyon Grail CF SLX 8 Di2 not bought until 2023-06-02"}
    plan = gear.build_plan(st, iv, policy="garmin")
    assert {r["intervals_id"] for r in plan["assign"] if r.get("replaces")} == {"i102", "i103"}
    st.close()


def test_sensor_evidence_needs_the_bike_s_own_sensors_missing(tmp_path):
    st = Store(tmp_path)
    acts, sensors = [], {}
    di2, strap, pedals = ("1", "shimano", "28728", "2864"), ("hr", "garmin", "hrm", "7"), ("pw", "garmin", "rally", "5")
    for n in range(6):                                     # agreed rides teach the bikes' sensors
        archive(st, f"2{n}", f"2024-0{n + 1}-01", "gravel_cycling", [GRAIL])
        acts.append(remote(f"i2{n}", f"2{n}", f"2024-0{n + 1}-01", "b-grail"))
        sensors[f"2{n}"] = {di2, strap, pedals}
        archive(st, f"3{n}", f"2024-0{n + 1}-02", "mountain_biking", [HAIBIKE])
        acts.append(remote(f"i3{n}", f"3{n}", f"2024-0{n + 1}-02", "b-hai"))
        sensors[f"3{n}"] = {strap}
    archive(st, "40", "2024-08-01", "mountain_biking", [HAIBIKE])        # Intervals says Grail, no Di2 or pedals
    acts.append(remote("i40", "40", "2024-08-01", "b-grail"))
    sensors["40"] = {strap}
    archive(st, "41", "2024-08-02", "mountain_biking", [HAIBIKE])        # Intervals says Grail, and its Di2 is there
    acts.append(remote("i41", "41", "2024-08-02", "b-grail"))
    sensors["41"] = {strap, di2}
    plan = gear.build_plan(st, IntervalsFake(acts), policy="evidence", sensors=FakeSensors(sensors))
    assert [(r["intervals_id"], r["reason"]) for r in plan["assign"]] == [
        ("i40", "evidence: none of Canyon Grail CF SLX 8 Di2's own sensors in the recording")]
    assert [r["intervals_id"] for r in plan["kept"]] == ["i41"]
    assert set(map(tuple, plan["signatures"]["b-grail"])) == {di2, pedals}    # the chest strap is on every bike
    st.close()


def test_apply_creates_assigns_recalculates_and_remembers(tmp_path):
    st, iv = scene(tmp_path)
    iv.fail_for = {"i105"}
    plan = gear.build_plan(st, iv, create=True)
    assert [c["payload"] for c in plan["create"]] == [
        {"name": '"Zoom Fly" Orange', "type": "Shoes", "purchased": "2025-05-04"}]   # retired shoes left out
    done = gear.apply_plan(plan, iv)
    assert iv.assigned == [("i101", "b-hai")] and done["failed"] == 1 and done["written"] == 1
    assert iv.recalculated == ["b-hai"] and done["created"] == ['"Zoom Fly" Orange']
    assert iv.updated == [("b-hai", {"distance": 4556000.0, "activities": 108, "time": 3600.0})]
    assert done["totals_corrected"] == [{"gear": "Haibike", "km": [0, 4556], "activities": [None, 108]}]
    gear.save_config(st, plan, done["ids"])
    config = gear.load_config(st)
    assert config["policy"] == "intervals" and config["map"]["u-shoe"] == "g1"
    st.close()


def test_scheduled_runs_follow_the_saved_choices_for_the_last_week(tmp_path):
    st, iv = scene(tmp_path)
    settings = SimpleNamespace(timezone=ZoneInfo("Europe/Vienna"))
    now = datetime(2025, 6, 5, 12, tzinfo=ZoneInfo("Europe/Vienna"))
    assert gear.sync_recent(settings, st, iv, apply=True, now=now) == {}          # nothing until gear --apply
    st.set_meta(gear.CONFIG_KEY, json.dumps({"policy": "intervals", "map": {"u-shoe": "s-zoom"}, "signatures": {}}))
    out = gear.sync_recent(settings, st, iv, apply=True, now=now)
    assert out == {"checked": 2, "to_assign": 1, "written": 1, "failed": 0}
    assert iv.assigned == [("i105", "s-zoom")] and iv.recalculated == []          # Intervals counts it itself
    st.close()


def test_gear_command_plans_without_garmin_and_applies_on_request(tmp_path, monkeypatch, capsys):
    from garmin_intervals_bridge import cli
    st, iv = scene(tmp_path)
    st.close()
    monkeypatch.setenv("BRIDGE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("GARMIN_TOKEN_DIR", str(tmp_path / "tokens"))
    monkeypatch.setenv("INTERVALS_API_KEY", "not-a-real-key")
    monkeypatch.setattr(cli, "IntervalsClient", lambda *a, **k: iv)
    monkeypatch.setattr(cli, "GarminSource", lambda *a, **k: pytest.fail("gear must not touch Garmin"))
    assert cli.main(["gear", "--map", "Zoom Fly Orange=Nike Zoom Fly - Rot"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["apply"] is False and sum(g["count"] for g in out["assign"]) == 2 and iv.assigned == []
    assert cli.main(["gear", "--prefer", "evidence", "--apply"]) == 0                # a dry run saves nothing
    assert json.loads(capsys.readouterr().out)["result"]["written"] == 2
    assert cli.main(["gear", "--map", "Zoom Fly Orange=Nike Zoom Fly - Rot", "--prefer", "evidence", "--apply"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["result"]["written"] == 1 and sorted(iv.assigned) == [("i101", "b-hai"), ("i102", "b-hai"),
                                                                       ("i105", "s-zoom")]
    st = Store(tmp_path)
    assert gear.load_config(st)["policy"] == "evidence"          # the scheduled runs keep to it
    st.close()


def test_recount_covers_the_bike_s_components_and_writes_only_what_drifted():
    gear_list = [{"id": "b-grail", "name": "Grail", "distance": 11021000.0, "activities": 209, "time": 3600.0,
                  "component_ids": ["c-chain", "c-tyre"]},
                 {"id": "c-chain", "name": "Chain", "component": True, "distance": 4556000.0, "activities": 108,
                  "time": 3600.0},
                 {"id": "c-tyre", "name": "Tyre", "component": True, "distance": 0.0, "activities": 0, "time": 0.0}]
    iv = IntervalsFake([], gear_list)
    assert gear.refresh_totals(iv, ["b-grail"], apply=False) == [
        {"gear": "Grail", "km": [11021, 4556], "activities": [209, 108]},
        {"gear": "Tyre", "km": [0, 4556], "activities": [0, 108]}]          # the chain was right already
    assert iv.recalculated == ["b-grail", "c-chain", "c-tyre"] and iv.updated == []
    gear.refresh_totals(iv, ["b-grail"])
    assert [u[0] for u in iv.updated] == ["b-grail", "c-tyre"]


def test_plan_points_at_activities_from_before_a_gear_was_bought(tmp_path):
    st, iv = scene(tmp_path)
    next(g for g in iv.gear_list if g["id"] == "b-grail")["purchased"] = "2023-06-02"
    for a in iv.remote:
        a["start_date_local"] = a["start_date"][:19]
    out = gear.report(gear.build_plan(st, iv))
    assert out["before_purchase"]["Canyon Grail CF SLX 8 Di2"] == 1           # i102, 2023-05-06
    st.close()


def test_gear_runs_next_to_a_running_bridge(tmp_path, monkeypatch, capsys):
    from garmin_intervals_bridge import cli
    from garmin_intervals_bridge.store import single_instance
    st, iv = scene(tmp_path)
    st.close()
    monkeypatch.setenv("BRIDGE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("GARMIN_TOKEN_DIR", str(tmp_path / "tokens"))
    monkeypatch.setenv("INTERVALS_API_KEY", "not-a-real-key")
    monkeypatch.setattr(cli, "IntervalsClient", lambda *a, **k: iv)
    with single_instance(tmp_path, ("activities", "wellness")):                # what `run` holds
        assert cli.main(["gear"]) == 0
    assert json.loads(capsys.readouterr().out)["apply"] is False
