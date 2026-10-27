"""Arrival estimate."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from custom_components.jatskiauto.estimate import estimate_arrival, observed_pace, scheduled_pace

TZ = ZoneInfo("Europe/Helsinki")
SCHED = datetime(2027, 6, 4, 17, 20, tzinfo=TZ)
# Stops 1..20 scheduled every 5 minutes, target is stop 20 (index 19).
TIMES = [SCHED - timedelta(minutes=5 * (19 - i)) for i in range(20)]


def ts(h, m):
    return datetime(2027, 6, 4, h, m, tzinfo=TZ).timestamp()


def test_scheduled_and_observed_pace():
    assert scheduled_pace(TIMES, 19) == 5.0
    assert scheduled_pace([None] * 20, 19) == 5.0
    assert observed_pace([{"stop_number": 10, "arrived_ts": ts(17, 0)}, {"stop_number": 12, "arrived_ts": ts(17, 14)}]) == 7.0
    assert observed_pace([{"stop_number": 10, "arrived_ts": ts(17, 0)}]) is None


def test_schedule_only_before_live_data():
    now = datetime(2027, 6, 4, 16, 30, tzinfo=TZ)
    est = estimate_arrival(now, SCHED, TIMES, 19, None, [])
    assert (est.likely, est.earliest, est.source) == (SCHED, SCHED, "schedule")


def test_late_truck_keeps_its_pace():
    # 30 min late at stop 12 and slower than the schedule: 7 min per stop.
    visited = [{"stop_number": 10, "arrived_ts": ts(17, 5)}, {"stop_number": 12, "arrived_ts": ts(17, 19)}]
    now = datetime(2027, 6, 4, 17, 21, tzinfo=TZ)
    track = {"progress_m": 3000.0, "meters_until": 2000.0, "reached_stop_number": 12}
    est = estimate_arrival(now, SCHED, TIMES, 19, track, visited)
    assert est.source == "live"
    assert est.likely == datetime(2027, 6, 4, 18, 15, tzinfo=TZ)  # 17:19 + 8 stops * 7 min
    assert now < est.earliest < est.likely
    # Earliest: 8 stops at 40 % of the pace (2.8 min) from 17:19.
    assert est.earliest == datetime(2027, 6, 4, 17, 41, 24, tzinfo=TZ)


def test_early_truck_is_not_expected_much_before_schedule():
    visited = [{"stop_number": 18, "arrived_ts": ts(16, 50)}]
    now = datetime(2027, 6, 4, 16, 52, tzinfo=TZ)
    track = {"progress_m": 6000.0, "meters_until": 400.0, "reached_stop_number": 18}
    est = estimate_arrival(now, SCHED, TIMES, 19, track, visited)
    assert est.likely == SCHED - timedelta(minutes=10)
    assert est.earliest == SCHED - timedelta(minutes=10)


def test_arrived():
    now = datetime(2027, 6, 4, 17, 30, tzinfo=TZ)
    est = estimate_arrival(now, SCHED, TIMES, 19, {"arrival_ts": ts(17, 27), "progress_m": 1.0}, [])
    assert est.source == "arrived" and est.likely == datetime(2027, 6, 4, 17, 27, tzinfo=TZ)
