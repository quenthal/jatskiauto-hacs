"""Visit tracker on a made-up looping route (ported from the MQTT bridge tests)."""

from datetime import datetime, timedelta
import math
from zoneinfo import ZoneInfo

from custom_components.jatskiauto import tracker as app

TZ = ZoneInfo("Europe/Helsinki")
LAT0, LON0 = 60.2, 25.0
DAY = "2027-06-18"


def ll(x, y):
    """Local metres (east, north) to lat/lon."""
    return LAT0 + y / 111195.0, LON0 + x / (111195.0 * math.cos(math.radians(LAT0)))


# Looping route: east along y=0, turn north, come back west
# along y=80. Stop 6 on the return leg is 80 m from the target stop 4.
PATH = [(0, 0), (1000, 0), (1000, 80), (0, 80)]
STOPS_XY = [(0, 0), (200, 0), (400, 0), (600, 0), (800, 0), (600, 80), (300, 80)]
SCHED = ["17:05", "17:10", "17:15", "17:20", "17:25", "17:35", "17:40"]


def make_route():
    stops = [
        {"name": f"Stop {i + 1}", "lat": ll(*xy)[0], "lon": ll(*xy)[1], "schedules": [{"date": DAY, "time": SCHED[i]}]}
        for i, xy in enumerate(STOPS_XY)
    ]
    # One geometry leg per stop pair, following PATH.
    def along(xy):
        x, y = xy
        return x if y == 0 else 1000 + 80 + (1000 - x)

    dense = []
    for (ax, ay), (bx, by) in zip(PATH, PATH[1:], strict=False):
        n = int(max(abs(bx - ax), abs(by - ay)) // 20)
        dense += [(ax + (bx - ax) * k / n, ay + (by - ay) * k / n) for k in range(n)]
    dense.append(PATH[-1])
    legs = []
    for a, b in zip(STOPS_XY, STOPS_XY[1:], strict=False):
        pa, pb = along(a), along(b)
        leg = [a] + [p for p in dense if pa < along(p) < pb] + [b]
        legs.append([list(ll(*p)) for p in leg])
    route = {"route_id": "test", "stops": stops}
    return app.Target(route, stops[3], 3), app.build_route_model(route, legs)


CFG = {"approach": {}, "live": {"stale_after_seconds": 180}}


def at(hhmm_ss):
    h, m, s = (int(v) for v in hhmm_ss.split(":"))
    return datetime(2027, 6, 18, h, m, s, tzinfo=TZ)


def feed(tracker, t0, points, step_s=20, dev="1"):
    out = []
    for i, (x, y) in enumerate(points):
        now = t0 + timedelta(seconds=step_s * i)
        lat, lon = ll(x, y)
        out.append(tracker.update(now, {"device_id": dev, "lat": lat, "lon": lon, "gps_ts": now.timestamp()}))
    return out


def test_model_stop_progress_matches_path():
    _, model = make_route()
    assert [round(p) for p in model["stop_progress"]] == [0, 200, 400, 600, 800, 1480, 1780]


def test_route_candidates_sees_both_loop_sections():
    _, model = make_route()
    lat, lon = ll(600, 45)
    cands = app.route_candidates(lat, lon, model, 150)
    assert sorted(round(c["progress_m"]) for c in cands) == [600, 1480]
    window = app.route_candidates(lat, lon, model, 150, lo=400, hi=800)
    assert [round(c["progress_m"]) for c in window] == [600]


def test_noisy_fix_near_return_leg_does_not_pass():
    # Global nearest projection of (600, 45) is the return leg (35 m vs 45 m),
    # which is what produced the false "passed" states in 2026.
    target, model = make_route()
    nearest = min(app.route_candidates(*ll(600, 45), model, 150), key=lambda c: c["distance_from_route_m"])
    assert nearest["progress_m"] > 1400
    tr = app.VisitTracker(target, model, CFG)
    pts = [(300, 0), (340, 5), (380, 0), (420, 10), (470, 0), (520, 45), (560, 45)]
    res = feed(tr, at("17:20:00"), pts)
    assert all(r["status"] != "passed" for r in res)
    assert res[-1]["status"] == "approaching_1_stop"
    assert res[-1]["stops_until_target"] == 1


def test_states_only_move_forward_and_arrival_needs_the_stop():
    target, model = make_route()
    tr = app.VisitTracker(target, model, CFG)
    # Approach, GPS jitter backwards, dwell at the target, then leave.
    pts = [(150, 0), (200, 0), (200, 0), (250, 0), (420, 0), (380, 0), (560, 0), (598, 0), (601, 3), (603, 2), (700, 0), (760, 0), (790, 0)]
    res = feed(tr, at("17:30:00"), pts)
    statuses = [r["status"] for r in res]
    order = [app.TRACK_STATES.index(s) for s in statuses]
    assert order == sorted(order)
    assert "arrived" in statuses
    assert statuses[-1] == "passed"
    assert 2 in res[-1]["visited_stops"] and 4 in res[-1]["visited_stops"]
    # Arrival time is the start of the dwell at the target, not its detection.
    assert tr.arrival_ts == (at("17:30:00") + timedelta(seconds=20 * 6)).timestamp()


def test_passed_needs_two_fixes_beyond_threshold():
    target, model = make_route()
    tr = app.VisitTracker(target, model, CFG)
    res = feed(tr, at("17:30:00"), [(450, 0), (550, 0), (740, 0)])
    assert res[-1]["status"] != "passed"
    res = feed(tr, at("17:31:00"), [(760, 0)])
    assert res[-1]["status"] == "passed"
    assert res[-1]["arrival_observed"] is False


def test_stale_and_repeated_fixes():
    target, model = make_route()
    tr = app.VisitTracker(target, model, CFG)
    feed(tr, at("17:20:00"), [(300, 0)])
    now = at("17:24:00")
    lat, lon = ll(320, 0)
    r = tr.update(now, {"device_id": "1", "lat": lat, "lon": lon, "gps_ts": at("17:20:00").timestamp()})
    assert r["match"] == "stale_fix"
    assert r["status"] == "stale"
    r = tr.update(at("17:24:20"), None)
    assert r["status"] == "stale"


def test_other_device_is_ignored_once_locked():
    target, model = make_route()
    tr = app.VisitTracker(target, model, CFG)
    feed(tr, at("17:20:00"), [(300, 0)])
    lat, lon = ll(900, 0)
    r = tr.update(at("17:20:20"), {"device_id": "2", "lat": lat, "lon": lon, "gps_ts": at("17:20:20").timestamp()})
    assert r["match"] == "other_device_ignored"
    assert round(r["progress_m"]) == 300


def test_acquire_prefers_section_behind_schedule():
    target, model = make_route()
    tr = app.VisitTracker(target, model, CFG)
    # At 17:22 the schedule says stop 4 (600 m); the return leg (1480 m) is ahead of it.
    r = feed(tr, at("17:22:00"), [(600, 45)])[0]
    assert round(r["progress_m"]) == 600
    # Late in the visit the return leg is the plausible section.
    tr.reset()
    r = feed(tr, at("17:38:00"), [(600, 45)])[0]
    assert round(r["progress_m"]) == 1480


def test_lost_lock_after_arrival_counts_as_passed():
    target, model = make_route()
    tr = app.VisitTracker(target, model, CFG)
    feed(tr, at("17:30:00"), [(560, 0), (600, 0), (602, 0)])
    assert tr.state == "arrived"
    res = feed(tr, at("17:31:00"), [(600, -400), (600, -450), (600, -500)])
    assert res[-1]["status"] == "passed"


def test_parse_gps_timestamp_formats():
    epoch = at("17:20:00").timestamp()
    assert app.parse_gps_timestamp(epoch) == epoch
    assert app.parse_gps_timestamp(int(epoch * 1000)) == epoch
    assert app.parse_gps_timestamp("2027-06-18T14:20:00Z") == epoch
    assert app.parse_gps_timestamp("2027-06-18 17:20:00", TZ) == epoch
    assert app.parse_gps_timestamp("garbage") is None
    assert app.parse_gps_timestamp(None) is None
