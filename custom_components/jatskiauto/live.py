"""Live tracking around each scheduled visit.

Outside the tracking window (scheduled time - before ... + after, on days the
stop has a visit) nothing is polled; a single timer wakes the coordinator when
the next window opens. Inside the window the vehicle positions are polled with
an interval that shortens as the truck gets closer, and polling stops once the
truck has passed the stop.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
import json
import logging
from pathlib import Path
from typing import Any

from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_track_point_in_utc_time
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import CannotConnect, Device, InvalidAuth, PaikannuspalveluClient, Stop
from .const import (
    CONF_NOTIFY_MINUTES,
    CONF_RECORD_TRACES,
    CONF_ROUTE_FILE,
    CONF_WINDOW_AFTER,
    CONF_WINDOW_BEFORE,
    DEFAULT_NOTIFY_MINUTES,
    DEFAULT_WINDOW_AFTER,
    DEFAULT_WINDOW_BEFORE,
    DOMAIN,
    SCHEDULE_TZ,
)
from .estimate import Estimate, estimate_arrival
from .geometry import fill_legs
from .tracker import TRACK_STATES, Target, VisitTracker, build_route_model, parse_gps_timestamp, parse_hhmm, route_candidates

_LOGGER = logging.getLogger(__name__)

ROUTE_MATCH_RADIUS_M = 150
NEAR_DISTANCE_M = 1500
TRACKER_CFG: dict[str, Any] = {"approach": {"route_match_radius_m": ROUTE_MATCH_RADIUS_M}, "live": {"stale_after_seconds": 180}}

# Poll intervals in seconds.
POLL_SEARCHING = 60
POLL_FAR = 60
POLL_NEAR = 20
POLL_APPROACHING = 10
POLL_ARRIVED = 20
POLL_STALE = 60

STATUS_NO_VISITS = "no_visits"
STATUS_SCHEDULED = "scheduled"
STATUS_SEARCHING = "searching"
STATUSES = [
    STATUS_NO_VISITS,
    STATUS_SCHEDULED,
    STATUS_SEARCHING,
    "on_route",
    "approaching_2_stops",
    "approaching_1_stop",
    "arrived",
    "passed",
    "stale",
]

EVENT_TRACKING_STARTED = "tracking_started"
EVENT_REACHED_STOP = "reached_stop"
EVENT_WITHIN_THRESHOLD = "within_threshold"
EVENT_ARRIVED = "arrived"
EVENT_PASSED = "passed"
EVENT_SIGNAL_LOST = "signal_lost"
EVENT_TYPES = [EVENT_TRACKING_STARTED, EVENT_REACHED_STOP, EVENT_WITHIN_THRESHOLD, EVENT_ARRIVED, EVENT_PASSED, EVENT_SIGNAL_LOST]


def visit_store_key(entry_id: str) -> str:
    return f"{DOMAIN}.visit.{entry_id}"


def event_signal(entry_id: str) -> str:
    return f"{DOMAIN}_{entry_id}_event"


@dataclass(frozen=True)
class Visit:
    scheduled: datetime
    start: datetime
    end: datetime


@dataclass(frozen=True)
class Position:
    latitude: float
    longitude: float
    accuracy: int
    gps_time: datetime | None


@dataclass
class LiveState:
    """What the entities show. Volatile diagnostics live in `debug` only."""

    status: str
    visit: Visit | None = None
    estimate: Estimate | None = None
    stops_remaining: int | None = None
    arrival: datetime | None = None
    passed_at: datetime | None = None
    position: Position | None = None
    device_name: str | None = None
    within_threshold: bool = False
    debug: dict[str, Any] = field(default_factory=dict, compare=False)


def schedule_times(stop: Stop) -> list[datetime]:
    return sorted(
        datetime.combine(datetime.fromisoformat(s["date"]).date(), parse_hhmm(s["time"]), tzinfo=SCHEDULE_TZ) for s in stop.schedules
    )


def current_or_next_visit(stop: Stop, now: datetime, before: timedelta, after: timedelta) -> Visit | None:
    """The visit whose window contains now, else the next one."""
    for sched in schedule_times(stop):
        if now <= sched + after:
            return Visit(sched, sched - before, sched + after)
    return None


def stop_times_on(stops: list[Stop], day) -> list[datetime | None]:
    out: list[datetime | None] = []
    for stop in stops:
        times = [t for t in schedule_times(stop) if t.date() == day]
        out.append(times[0] if times else None)
    return out


def pick_device(devices: list[Device], tracked_id: str | None, model: dict[str, Any]) -> Device | None:
    """The tracked vehicle, else the one closest to the route (within the match radius)."""
    if tracked_id is not None:
        for d in devices:
            if d.id == tracked_id:
                return d
    best, best_d = None, float("inf")
    for d in devices:
        cands = route_candidates(d.lat, d.lon, model, ROUTE_MATCH_RADIUS_M)
        if cands:
            dist = min(c["distance_from_route_m"] for c in cands)
            if dist < best_d:
                best, best_d = d, dist
    return best


class LiveCoordinator(DataUpdateCoordinator[LiveState]):
    """Window scheduling, polling, tracking, estimate and events for one stop."""

    def __init__(self, hass: HomeAssistant, entry, client: PaikannuspalveluClient, route_coordinator) -> None:
        super().__init__(hass, _LOGGER, config_entry=entry, name=f"{DOMAIN} live", update_interval=None, always_update=False)
        self.client = client
        self.route = route_coordinator
        self.legs: list | None = None
        self._model_key: tuple | None = None
        self._model: dict[str, Any] | None = None
        self._target: Target | None = None
        self._tracker: VisitTracker | None = None
        self._visit: Visit | None = None
        self._fired: set[str] = set()
        self._visited_seen = 0
        self._within_latched = False
        self._wake: CALLBACK_TYPE | None = None
        self._last_position: Position | None = None
        self._device_name: str | None = None
        self._reached_fired: set[int] = set()
        # What was already announced for the current visit survives a restart.
        self._store: Store[dict[str, Any]] = Store(hass, 1, visit_store_key(entry.entry_id))
        self._restored: dict[str, Any] | None = None
        self._saved: dict[str, Any] | None = None

    async def async_restore(self) -> None:
        self._restored = await self._store.async_load()

    def _snapshot(self) -> dict[str, Any]:
        tracker = self._tracker
        return {
            "visit": self._visit.scheduled.isoformat() if self._visit else None,
            "fired": sorted(self._fired),
            "reached": sorted(self._reached_fired),
            "within_latched": self._within_latched,
            "track_state": tracker.state if tracker else None,
            "arrival_ts": tracker.arrival_ts if tracker else None,
            "passed_ts": tracker.passed_ts if tracker else None,
        }

    @callback
    def _save_if_changed(self) -> None:
        snap = self._snapshot()
        if snap != self._saved:
            self._saved = snap
            self._store.async_delay_save(lambda: snap, 1)

    # -- options ---------------------------------------------------------
    def _minutes(self, key: str, default: int) -> timedelta:
        return timedelta(minutes=self.config_entry.options.get(key, default))

    @property
    def notify_minutes(self) -> float:
        return float(self.config_entry.options.get(CONF_NOTIFY_MINUTES, DEFAULT_NOTIFY_MINUTES))

    # -- timers ----------------------------------------------------------
    @callback
    def _wake_at(self, when: datetime) -> None:
        self._cancel_wake()

        @callback
        def _fire(_now: datetime) -> None:
            self._wake = None
            self.hass.async_create_task(self.async_refresh(), eager_start=True)

        self._wake = async_track_point_in_utc_time(self.hass, _fire, dt_util.as_utc(when))

    @callback
    def _cancel_wake(self) -> None:
        if self._wake:
            self._wake()
            self._wake = None

    @callback
    def async_shutdown_timers(self) -> None:
        self._cancel_wake()

    # -- model -----------------------------------------------------------
    def _ensure_model(self, stops: list[Stop], index: int) -> None:
        """(Re)build the route model, but never in the middle of a visit."""
        key = (tuple((s.lat, s.lon) for s in stops), index, id(self.legs))
        if key == self._model_key or (self._tracker is not None and self._tracker.state is not None):
            return
        legs = fill_legs(stops, self.legs or [])
        route = {"stops": [s.as_dict() for s in stops]}
        self._target = Target(route, route["stops"][index], index)
        self._model = build_route_model(route, legs)
        self._tracker = VisitTracker(self._target, self._model, TRACKER_CFG)
        self._model_key = key

    # -- events ----------------------------------------------------------
    @callback
    def _fire(self, event_type: str, data: dict[str, Any], once: bool = True) -> None:
        if once:
            if event_type in self._fired:
                return
            self._fired.add(event_type)
        async_dispatcher_send(self.hass, event_signal(self.config_entry.entry_id), event_type, data)

    # -- update ----------------------------------------------------------
    async def _async_update_data(self) -> LiveState:
        self._cancel_wake()
        self.update_interval = None
        route = self.route.data
        target = route.target if route else None
        now = dt_util.now(SCHEDULE_TZ)
        if target is None:
            return LiveState(STATUS_NO_VISITS)
        before = self._minutes(CONF_WINDOW_BEFORE, DEFAULT_WINDOW_BEFORE)
        after = self._minutes(CONF_WINDOW_AFTER, DEFAULT_WINDOW_AFTER)
        visit = current_or_next_visit(target, now, before, after)
        if visit is None:
            return LiveState(STATUS_NO_VISITS)
        if now < visit.start:
            self._wake_at(visit.start)
            return self._idle_state(STATUS_SCHEDULED, visit)

        if visit != self._visit:
            self._start_visit(visit, route.stops, route.stop_index)
        assert self._tracker is not None and self._model is not None
        if self._tracker.state == "passed":
            # Done for this visit; wake up at the end of the window to show the next one.
            self._wake_at(visit.end + timedelta(seconds=1))
            return self._state(now, visit, route.stops, route.stop_index, None)

        try:
            devices = await self.client.devices()
        except InvalidAuth as err:
            raise ConfigEntryAuthFailed from err
        except CannotConnect as err:
            self.update_interval = timedelta(seconds=POLL_STALE)
            raise UpdateFailed(str(err)) from err

        dev = pick_device(devices, self._tracker.device_id, self._model)
        fix = None
        if dev is not None:
            fix = {"device_id": dev.id, "lat": dev.lat, "lon": dev.lon, "gps_ts": parse_gps_timestamp(dev.timestamp, SCHEDULE_TZ)}
        result = self._tracker.update(now, fix)
        if dev is not None and result.get("fix_used"):
            self._device_name = dev.name
            self._last_position = Position(
                dev.lat, dev.lon, max(20, round(result.get("distance_from_route_m") or 20)), dt_util.utc_from_timestamp(fix["gps_ts"]) if fix["gps_ts"] else None
            )
        state = self._state(now, visit, route.stops, route.stop_index, result)
        self._fire_events(state, result)
        self._save_if_changed()
        await self._record(now, visit, devices, result, state)

        interval = self._poll_interval(state, result)
        if interval is None:
            self._wake_at(visit.end + timedelta(seconds=1))
        elif now + timedelta(seconds=interval) > visit.end:
            self._wake_at(visit.end + timedelta(seconds=1))
        else:
            self.update_interval = timedelta(seconds=interval)
        return state

    def _start_visit(self, visit: Visit, stops: list[Stop], index: int) -> None:
        if self._tracker is not None:
            self._tracker.reset()
        self._ensure_model(stops, index)
        self._visit = visit
        self._fired = set()
        self._visited_seen = 0
        self._within_latched = False
        self._reached_fired = set()
        self._last_position = None
        self._device_name = None
        restored, self._restored = self._restored, None
        if restored and restored.get("visit") == visit.scheduled.isoformat() and self._tracker is not None:
            # Home Assistant restarted during this visit: do not announce anything twice.
            self._fired = set(restored.get("fired", []))
            self._reached_fired = set(restored.get("reached", []))
            self._within_latched = bool(restored.get("within_latched"))
            if restored.get("track_state") in TRACK_STATES:
                self._tracker.state_idx = TRACK_STATES.index(restored["track_state"])
            self._tracker.arrival_ts = restored.get("arrival_ts")
            self._tracker.passed_ts = restored.get("passed_ts")
        self._fire(EVENT_TRACKING_STARTED, {"scheduled": visit.scheduled.isoformat()})
        self._save_if_changed()

    def _idle_state(self, status: str, visit: Visit) -> LiveState:
        est = Estimate(visit.scheduled, visit.scheduled, "schedule")
        return LiveState(status, visit=visit, estimate=est)

    def _state(self, now: datetime, visit: Visit, stops: list[Stop], index: int, result: dict[str, Any] | None) -> LiveState:
        tracker = self._tracker
        assert tracker is not None
        track_state = tracker.state
        if result is None:
            status = track_state or STATUS_SEARCHING
        elif result["status"] == "unknown":
            status = STATUS_SEARCHING
        else:
            status = result["status"]
        track = result if result is not None else {"arrival_ts": tracker.arrival_ts, "progress_m": tracker.progress_m}
        est = estimate_arrival(now, visit.scheduled, stop_times_on(stops, visit.scheduled.date()), index, track, tracker.visited)
        arrival = datetime.fromtimestamp(tracker.arrival_ts, SCHEDULE_TZ) if tracker.arrival_ts else None
        passed_at = datetime.fromtimestamp(tracker.passed_ts, SCHEDULE_TZ) if tracker.passed_ts else None
        # Latched like the event: once on, stays on until the truck has passed,
        # even if the estimate moves back over the threshold.
        if track_state == "arrived" or (track_state != "passed" and est.minutes_until(now) <= self.notify_minutes):
            self._within_latched = True
        within = self._within_latched and track_state != "passed"
        return LiveState(
            status=status,
            visit=visit,
            estimate=est,
            stops_remaining=result.get("stops_until_target") if result else (0 if track_state in ("arrived", "passed") else None),
            arrival=arrival,
            passed_at=passed_at,
            position=self._last_position if track_state != "passed" else None,
            device_name=self._device_name,
            within_threshold=within,
            debug={k: (result or {}).get(k) for k in ("match", "progress_m", "meters_until", "fix_age_seconds", "visited_stops")},
        )

    @callback
    def _fire_events(self, state: LiveState, result: dict[str, Any]) -> None:
        tracker = self._tracker
        assert tracker is not None and state.visit is not None
        target_no = tracker.target_no
        for v in tracker.visited[self._visited_seen :]:
            if v["stop_number"] < target_no and v["stop_number"] not in self._reached_fired:
                self._reached_fired.add(v["stop_number"])
                self._fire(
                    EVENT_REACHED_STOP,
                    {"stop_number": v["stop_number"], "stops_remaining": target_no - v["stop_number"]},
                    once=False,
                )
        self._visited_seen = len(tracker.visited)
        base = {
            "scheduled": state.visit.scheduled.isoformat(),
            "eta_earliest": state.estimate.earliest.isoformat() if state.estimate else None,
            "eta_likely": state.estimate.likely.isoformat() if state.estimate else None,
            "stops_remaining": state.stops_remaining,
        }
        if state.within_threshold and tracker.state != "passed":
            self._fire(EVENT_WITHIN_THRESHOLD, {**base, "minutes": round(state.estimate.minutes_until(dt_util.now(SCHEDULE_TZ))) if state.estimate else None})
        if tracker.state in ("arrived", "passed") and state.arrival:
            self._fire(EVENT_ARRIVED, {**base, "arrival": state.arrival.isoformat()})
        if tracker.state == "passed":
            self._fire(EVENT_PASSED, {**base, "arrival_observed": state.arrival is not None})
        if state.status == "stale":
            self._fire(EVENT_SIGNAL_LOST, base)

    def _poll_interval(self, state: LiveState, result: dict[str, Any]) -> int | None:
        status = state.status
        if status == "passed":
            return None
        if status in ("approaching_1_stop", "approaching_2_stops"):
            return POLL_APPROACHING
        if status == "arrived":
            return POLL_ARRIVED
        if status == "stale":
            return POLL_STALE
        if status == STATUS_SEARCHING:
            return POLL_SEARCHING
        meters = result.get("meters_until")
        return POLL_NEAR if meters is not None and meters <= NEAR_DISTANCE_M else POLL_FAR

    # -- traces ----------------------------------------------------------
    async def _record(self, now: datetime, visit: Visit, devices: list[Device], result: dict[str, Any], state: LiveState) -> None:
        if not self.config_entry.options.get(CONF_RECORD_TRACES, False) or self._model is None:
            return
        pts = self._model["points"]
        lat_lo, lat_hi = min(p[0] for p in pts) - 0.02, max(p[0] for p in pts) + 0.02
        lon_lo, lon_hi = min(p[1] for p in pts) - 0.04, max(p[1] for p in pts) + 0.04
        record = {
            "poll": now.isoformat(),
            "visit": visit.scheduled.isoformat(),
            "devices": [
                {"id": d.id, "name": d.name, "lat": d.lat, "lon": d.lon, "speed": d.speed, "heading": d.heading, "timestamp": d.timestamp}
                for d in devices
                if lat_lo <= d.lat <= lat_hi and lon_lo <= d.lon <= lon_hi
            ],
            "result": {k: result.get(k) for k in ("status", "track_state", "match", "progress_m", "meters_until", "reached_stop_number", "visited_stops", "fix_age_seconds")},
            "eta_earliest": state.estimate.earliest.isoformat() if state.estimate else None,
            "eta_likely": state.estimate.likely.isoformat() if state.estimate else None,
        }
        path = Path(self.hass.config.path(DOMAIN, "traces", self.config_entry.data[CONF_ROUTE_FILE], f"{now.date().isoformat()}.jsonl"))
        line = json.dumps(record, ensure_ascii=False) + "\n"
        await self.hass.async_add_executor_job(_append_line, path, line)


def _append_line(path: Path, line: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(line)
