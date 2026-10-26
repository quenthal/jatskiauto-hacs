"""Visit tracking along an ice-cream truck route.

Pure logic without Home Assistant, network or I/O, so it can be tested and
replayed offline. Ported from the jatskiauto MQTT bridge (v17).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from datetime import time as dtime
import math
import re
from typing import Any
from zoneinfo import ZoneInfo


@dataclass
class Target:
    route: dict[str, Any]
    stop: dict[str, Any]
    stop_index: int


def parse_hhmm(value: str) -> dtime:
    h, m = value.split(":")
    return dtime(int(h), int(m))


def stop_schedule_datetime(stop: dict[str, Any], day, tz: ZoneInfo) -> datetime | None:
    for item in stop.get("schedules", []):
        if item.get("date") == day.isoformat() and item.get("time"):
            return datetime.combine(day, parse_hhmm(item["time"]), tzinfo=tz)
    return None


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dp/2)**2 + math.cos(p1) * math.cos(p2) * math.sin(dl/2)**2
    return r * 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))


def local_xy(lat: float, lon: float, lat0: float, lon0: float) -> tuple[float, float]:
    r = 6371000.0
    x = math.radians(lon - lon0) * r * math.cos(math.radians(lat0))
    y = math.radians(lat - lat0) * r
    return x, y


def leg_points_from_stops(stops: list[dict[str, Any]], i: int) -> list[list[float]]:
    return [[float(stops[i]["lat"]), float(stops[i]["lon"])], [float(stops[i+1]["lat"]), float(stops[i+1]["lon"])] ]


def build_route_model(route: dict[str, Any], legs: list[list[list[float]]]) -> dict[str, Any]:
    points = []
    stop_progress = [0.0]
    progress = 0.0
    for i, leg in enumerate(legs):
        clean = [[float(p[0]), float(p[1])] for p in leg]
        if i == 0:
            points.append(clean[0])
        for p in clean[1:]:
            last = points[-1]
            progress += haversine_m(last[0], last[1], p[0], p[1])
            points.append(p)
        stop_progress.append(progress)
    if not points:
        stops = route["stops"]
        points = [[float(s["lat"]), float(s["lon"])] for s in stops]
        stop_progress = [0.0]
        for i in range(len(points)-1):
            stop_progress.append(stop_progress[-1] + haversine_m(points[i][0], points[i][1], points[i+1][0], points[i+1][1]))
    return {"points": points, "stop_progress": stop_progress, "length_m": stop_progress[-1] if stop_progress else 0.0}


def route_candidates(
    lat: float,
    lon: float,
    model: dict[str, Any],
    radius_m: float,
    lo: float | None = None,
    hi: float | None = None,
    score: Callable[[float, float], float] | None = None,
) -> list[dict[str, float]]:
    """Return the best projection for each route section within radius_m.

    The route loops, so one GPS point can be near several route sections. Each
    contiguous run of nearby segments is one candidate. lo/hi limit the search to
    a progress window; the window bounds are clamped onto the segment, so the
    returned progress is always inside the window. score(progress_m, distance_m)
    ranks segments inside a run (default: distance); out-and-back streets make
    one run cover the same road twice, so a progress-aware score is needed there.
    """
    points = model["points"]
    if "point_progress" not in model:
        cum = [0.0]
        for a, b in zip(points, points[1:], strict=False):
            cum.append(cum[-1] + math.hypot(*local_xy(b[0], b[1], a[0], a[1])))
        model["point_progress"] = cum
    cum = model["point_progress"]
    cands: list[dict[str, float]] = []
    run: dict[str, float] | None = None
    for i in range(len(points) - 1):
        seg_start, seg_end = cum[i], cum[i + 1]
        if seg_end == seg_start or (lo is not None and seg_end < lo) or (hi is not None and seg_start > hi):
            if run:
                cands.append(run)
                run = None
            continue
        a, b = points[i], points[i + 1]
        ax, ay = local_xy(a[0], a[1], lat, lon)
        bx, by = local_xy(b[0], b[1], lat, lon)
        vx, vy = bx - ax, by - ay
        seg_len = seg_end - seg_start
        t_min = 0.0 if lo is None else max(0.0, (lo - seg_start) / seg_len)
        t_max = 1.0 if hi is None else min(1.0, (hi - seg_start) / seg_len)
        t = max(t_min, min(t_max, (-ax * vx - ay * vy) / (seg_len * seg_len)))
        d = math.hypot(ax + t * vx, ay + t * vy)
        if d > radius_m:
            if run:
                cands.append(run)
                run = None
            continue
        progress = seg_start + t * seg_len
        value = score(progress, d) if score else d
        if run is None or value < run["score"]:
            run = {"progress_m": progress, "distance_from_route_m": d, "score": value}
    if run:
        cands.append(run)
    return cands


def parse_gps_timestamp(value: Any, tz: ZoneInfo | None = None) -> float | None:
    """Parse a Paikannuspalvelu GPS timestamp to epoch seconds, or None."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)) or (isinstance(value, str) and re.fullmatch(r"\d+(\.\d+)?", value.strip())):
        v = float(value)
        return v / 1000.0 if v > 1e12 else v
    try:
        dt = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=tz or ZoneInfo("Europe/Helsinki"))
    return dt.timestamp()


TRACK_STATES = ["on_route", "approaching_2_stops", "approaching_1_stop", "arrived", "passed"]


class VisitTracker:
    """Follow one vehicle along the target route for one target-stop visit.

    Fixes are matched to the route only inside a window around the
    previous progress (speed-limited), which keeps the position from snapping to
    other sections of the looping route. The state only moves forward:
    on_route -> approaching_2_stops -> approaching_1_stop -> arrived -> passed.
    """

    def __init__(self, target: Target, model: dict[str, Any], cfg: dict[str, Any]):
        self.target = target
        self.model = model
        self.stop_progress = model["stop_progress"]
        self.target_progress = self.stop_progress[target.stop_index]
        self.target_no = target.stop_index + 1
        ap = cfg.get("approach", {})
        self.radius_m = float(ap.get("route_match_radius_m", 150))
        self.max_kmh = float(ap.get("max_progress_speed_kmh", 30))
        self.backward_m = float(ap.get("progress_backward_tolerance_m", 160))
        self.arrived_tol_m = float(ap.get("arrived_progress_tolerance_m", 35))
        self.arrived_stop_radius_m = float(ap.get("arrived_stop_radius_m", 60))
        self.passed_m = float(ap.get("passed_progress_threshold_m", 120))
        self.passed_fixes = int(ap.get("passed_confirm_fixes", 2))
        self.passed_stop_dist_m = float(ap.get("passed_min_stop_distance_m", 100))
        self.visit_radius_m = float(ap.get("stop_visit_radius_m", 50))
        self.visit_min_s = float(ap.get("stop_visit_min_seconds", 20))
        self.jump_penalty = float(ap.get("progress_jump_penalty", 0.25))
        self.backward_penalty = float(ap.get("progress_backward_penalty", 1.0))
        self.noise_m = float(ap.get("progress_noise_m", 0))
        self.reacquire_fixes = int(ap.get("reacquire_after_off_route_fixes", 3))
        self.stale_s = float(cfg.get("live", {}).get("stale_after_seconds", 180))
        self.reset()

    def reset(self) -> None:
        self.device_id: str | None = None
        self.progress_m: float | None = None
        self.fix_ts: float | None = None
        self.state_idx = -1
        self.arrival_ts: float | None = None
        self.passed_ts: float | None = None
        self.passed_count = 0
        self.off_route_count = 0
        self.visited: list[dict[str, Any]] = []
        self.dwell: dict[str, Any] | None = None

    @property
    def state(self) -> str | None:
        return TRACK_STATES[self.state_idx] if self.state_idx >= 0 else None

    def expected_progress(self, now: datetime) -> float | None:
        """Route progress of the last stop whose schedule time today has come."""
        best = None
        for idx, stop in enumerate(self.target.route.get("stops", [])):
            sched = stop_schedule_datetime(stop, now.date(), now.tzinfo)
            if sched and sched <= now and idx < len(self.stop_progress):
                best = self.stop_progress[idx]
        return best

    def _acquire(self, lat: float, lon: float, now: datetime) -> dict[str, float] | None:
        cands = route_candidates(lat, lon, self.model, self.radius_m)
        if not cands:
            return None
        # Trucks run late far more often than early: take the furthest section
        # that is not clearly ahead of the schedule, else the earliest one.
        expected = self.expected_progress(now)
        if expected is not None:
            behind = [c for c in cands if c["progress_m"] <= expected + 300]
            if behind:
                return max(behind, key=lambda c: c["progress_m"])
        return min(cands, key=lambda c: c["progress_m"])

    def _reached_stop_no(self) -> int:
        reached = 0
        for idx, sp in enumerate(self.stop_progress):
            if sp <= (self.progress_m or 0.0) + 5:
                reached = idx + 1
        return reached

    def _update_dwell(self, gps_ts: float) -> None:
        """A stop is visited when consecutive fixes stay near it for visit_min_s.

        Uses route progress only: fix-to-fix movement is dominated by GPS noise
        while parked, and a truck driving past leaves the radius within seconds.
        """
        stop_idx = None
        for idx, sp in enumerate(self.stop_progress):
            if abs((self.progress_m or 0.0) - sp) <= self.visit_radius_m:
                stop_idx = idx
                break
        if stop_idx is None:
            self.dwell = None
            return
        if not self.dwell or self.dwell["stop_index"] != stop_idx:
            self.dwell = {"stop_index": stop_idx, "since": gps_ts}
            return
        if gps_ts - self.dwell["since"] >= self.visit_min_s:
            stop_no = stop_idx + 1
            if not self.visited or self.visited[-1]["stop_number"] != stop_no:
                self.visited.append({"stop_number": stop_no, "arrived_ts": self.dwell["since"], "last_ts": gps_ts})
            else:
                self.visited[-1]["last_ts"] = gps_ts

    def update(self, now: datetime, fix: dict[str, Any] | None) -> dict[str, Any]:
        """Feed one poll. fix: device_id, lat, lon and gps_ts (epoch or None)."""
        now_ts = now.timestamp()
        info: dict[str, Any] = {"fix_used": False, "match": None}
        if fix is not None:
            gps_ts = fix.get("gps_ts") or now_ts
            dev_id = str(fix.get("device_id"))
            if self.device_id is not None and dev_id != self.device_id:
                info["match"] = "other_device_ignored"
            elif now_ts - gps_ts > self.stale_s:
                info["match"] = "stale_fix"
            elif self.fix_ts is not None and gps_ts <= self.fix_ts:
                info["match"] = "repeated_fix"
            else:
                info.update(self._match(now, dev_id, float(fix["lat"]), float(fix["lon"]), gps_ts))
        return self._result(now_ts, info)

    def _window_match(self, lat: float, lon: float, lo: float, hi: float) -> dict[str, float] | None:
        """Best route position inside the progress window, or None if off route.

        Prefer positions near the previous progress: a later loop section may be
        a little closer to a noisy fix, but the truck moves continuously and
        practically never backwards. Out-and-back streets put the same road on
        the route twice; the backward penalty keeps the estimate on the leg the
        truck is actually driving.
        """
        prev = self.progress_m

        def cost(progress: float, distance: float) -> float:
            delta = progress - prev
            if delta >= 0:
                return distance + self.jump_penalty * delta
            # Small steps back are GPS noise around a parked truck, not reversing.
            return distance + self.backward_penalty * max(0.0, -delta - self.noise_m)

        cands = route_candidates(lat, lon, self.model, self.radius_m, lo, hi, score=cost)
        return min(cands, key=lambda c: c["score"]) if cands else None

    def _match(self, now: datetime, dev_id: str, lat: float, lon: float, gps_ts: float) -> dict[str, Any]:
        if self.progress_m is None:
            cand = self._acquire(lat, lon, now)
            if cand is None:
                return {"match": "no_route_section_near"}
            match = "acquired"
        else:
            dt = max(1.0, gps_ts - (self.fix_ts or gps_ts))
            lo = self.progress_m - self.backward_m
            hi = self.progress_m + max(150.0, self.max_kmh / 3.6 * dt * 1.5 + 50.0)
            cand = self._window_match(lat, lon, lo, hi)
            if cand is None:
                self.off_route_count += 1
                if self.off_route_count >= self.reacquire_fixes:
                    if self.state == "arrived":
                        # Left the route after the visit, e.g. a shortcut to the next area.
                        self.state_idx = TRACK_STATES.index("passed")
                        self.passed_ts = gps_ts
                    elif self.state != "passed":
                        # Lost lock (detour or wrong initial section): start over, keep state.
                        self.progress_m = None
                        self.device_id = None
                return {"match": "off_route_window", "off_route_count": self.off_route_count}
            match = "window"
        self.off_route_count = 0
        self.device_id = dev_id
        self.progress_m = cand["progress_m"]
        self._update_dwell(gps_ts)
        self.fix_ts = gps_ts
        self._advance_state(lat, lon, gps_ts)
        return {"fix_used": True, "match": match, "distance_from_route_m": cand["distance_from_route_m"]}

    def _advance_state(self, lat: float, lon: float, gps_ts: float) -> None:
        meters_until = self.target_progress - self.progress_m
        stop = self.target.stop
        stop_dist = haversine_m(lat, lon, float(stop["lat"]), float(stop["lon"]))
        visited_target = any(v["stop_number"] == self.target_no for v in self.visited)
        visited_after = any(v["stop_number"] > self.target_no for v in self.visited)
        # Progress alone can be pushed past the stop by GPS noise on a looping
        # route; passed also needs the truck to be physically away from the stop.
        if -meters_until > self.passed_m and stop_dist > self.passed_stop_dist_m:
            self.passed_count += 1
        else:
            self.passed_count = 0
        reached = self._reached_stop_no()
        if self.passed_count >= self.passed_fixes or visited_after:
            cand = "passed"
        elif visited_target or (-self.passed_m <= meters_until <= self.arrived_tol_m and stop_dist <= self.arrived_stop_radius_m):
            cand = "arrived"
        elif reached < self.target_no and self.target_no - reached == 1:
            cand = "approaching_1_stop"
        elif reached < self.target_no and self.target_no - reached == 2:
            cand = "approaching_2_stops"
        else:
            cand = "on_route"
        idx = TRACK_STATES.index(cand)
        if idx > self.state_idx:
            self.state_idx = idx
            if cand == "arrived" and self.arrival_ts is None:
                visit = next((v for v in self.visited if v["stop_number"] == self.target_no), None)
                self.arrival_ts = visit["arrived_ts"] if visit else gps_ts
            if cand == "passed" and self.passed_ts is None:
                self.passed_ts = gps_ts

    def _result(self, now_ts: float, info: dict[str, Any]) -> dict[str, Any]:
        stale = self.fix_ts is None or now_ts - self.fix_ts > self.stale_s
        if self.state is None:
            status = "unknown"
        elif stale and self.state not in ("arrived", "passed"):
            status = "stale"
        else:
            status = self.state
        reached = self._reached_stop_no() if self.progress_m is not None else None
        return {
            "status": status,
            "track_state": self.state,
            "progress_m": self.progress_m,
            "meters_until": (self.target_progress - self.progress_m) if self.progress_m is not None else None,
            "reached_stop_number": reached,
            "stops_until_target": max(0, self.target_no - reached) if reached is not None else None,
            "visited_stops": [v["stop_number"] for v in self.visited],
            "arrival_ts": self.arrival_ts,
            "passed_ts": self.passed_ts,
            "arrival_observed": self.arrival_ts is not None,
            "last_fix_ts": self.fix_ts,
            "fix_age_seconds": round(now_ts - self.fix_ts) if self.fix_ts is not None else None,
            "device_id": self.device_id,
            **info,
        }
