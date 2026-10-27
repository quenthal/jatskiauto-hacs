"""Arrival estimate for the followed stop.

Pure logic. The truck is usually late and rarely catches up, so the estimate
follows today's observed pace (minutes per stop, driving and selling
included) instead of the published schedule. Two values come out:

- likely: when the truck most probably arrives
- earliest: the earliest plausible arrival, used for notifications because a
  notification that is a few minutes early costs little and a late one means
  the truck is gone.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

# Pace limits, minutes per stop including driving and selling.
DEFAULT_PACE_MIN = 5.0
MIN_PACE_MIN = 1.5
MAX_PACE_MIN = 15.0
# Earliest-case pace as a share of the observed pace, and the floor for it.
EARLIEST_PACE_SHARE = 0.4
EARLIEST_PACE_FLOOR_MIN = 1.0
# The truck is rarely more than this early compared to the schedule.
MAX_EARLY = timedelta(minutes=10)
MAX_SPEED_MPS = 30 / 3.6


@dataclass(frozen=True)
class Estimate:
    likely: datetime
    earliest: datetime
    source: str  # "schedule", "live" or "arrived"
    pace_min: float | None = None

    def minutes_until(self, now: datetime, which: str = "earliest") -> float:
        return max(0.0, (getattr(self, which) - now).total_seconds() / 60.0)


def scheduled_pace(stop_times: list[datetime | None], target_index: int) -> float:
    """Minutes per stop in the published schedule before the target."""
    known = [(i, t) for i, t in enumerate(stop_times[: target_index + 1]) if t is not None]
    if len(known) < 2:
        return DEFAULT_PACE_MIN
    (i0, t0), (i1, t1) = known[0], known[-1]
    if i1 == i0:
        return DEFAULT_PACE_MIN
    return min(MAX_PACE_MIN, max(MIN_PACE_MIN, (t1 - t0).total_seconds() / 60.0 / (i1 - i0)))


def observed_pace(visited: list[dict[str, Any]]) -> float | None:
    """Minutes per stop between the first and last stop visit seen today."""
    if len(visited) < 2:
        return None
    first, last = visited[0], visited[-1]
    stops = last["stop_number"] - first["stop_number"]
    if stops <= 0:
        return None
    pace = (last["arrived_ts"] - first["arrived_ts"]) / 60.0 / stops
    return min(MAX_PACE_MIN, max(MIN_PACE_MIN, pace))


def estimate_arrival(
    now: datetime,
    scheduled: datetime,
    stop_times: list[datetime | None],
    target_index: int,
    track: dict[str, Any] | None,
    visited: list[dict[str, Any]],
) -> Estimate:
    """Estimate the arrival at stops[target_index].

    track is the VisitTracker result (None before any usable GPS fix);
    visited is the tracker's list of stop visits ({"stop_number", "arrived_ts"}).
    """
    tz = now.tzinfo
    if track and track.get("arrival_ts"):
        at = datetime.fromtimestamp(track["arrival_ts"], tz)
        return Estimate(at, at, "arrived")
    if not track or track.get("progress_m") is None:
        return Estimate(max(now, scheduled), max(now, scheduled), "schedule")

    target_no = target_index + 1
    pace = observed_pace(visited) or scheduled_pace(stop_times, target_index)
    reached = track.get("reached_stop_number") or 0
    last = visited[-1] if visited else None
    if last and last["stop_number"] >= reached:
        # Measure from the arrival at the last stop the truck sold at.
        ref_no, ref_time = last["stop_number"], datetime.fromtimestamp(last["arrived_ts"], tz)
    else:
        ref_no, ref_time = reached, now
    slots = max(0, target_no - ref_no)
    floor = scheduled - MAX_EARLY

    likely = max(now, floor, ref_time + timedelta(minutes=slots * pace))
    fast_pace = max(EARLIEST_PACE_FLOOR_MIN, pace * EARLIEST_PACE_SHARE)
    meters = max(0.0, track.get("meters_until") or 0.0)
    earliest = max(
        now + timedelta(seconds=meters / MAX_SPEED_MPS),
        ref_time + timedelta(minutes=slots * fast_pace),
        floor,
    )
    return Estimate(likely, min(earliest, likely), "live", round(pace, 1))
