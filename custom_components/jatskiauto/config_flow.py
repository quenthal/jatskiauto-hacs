"""Config flow for Jätskiauto."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
import logging
from typing import Any, override

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlowWithReload
from homeassistant.const import CONF_API_KEY, CONF_LOCATION
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    BooleanSelector,
    LocationSelector,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
)
from homeassistant.util import dt as dt_util
import probatio

from .api import CannotConnect, InvalidAuth, PaikannuspalveluClient, Route, Stop
from .const import (
    CONF_NOTIFY_MINUTES,
    CONF_POSTAL_CODE,
    CONF_RECORD_TRACES,
    CONF_ROUTE_FILE,
    CONF_ROUTE_ID,
    CONF_ROUTE_NAME,
    CONF_STOP,
    CONF_STOP_INDEX,
    CONF_STOP_NAME,
    CONF_WINDOW_AFTER,
    CONF_WINDOW_BEFORE,
    DEFAULT_NOTIFY_MINUTES,
    DEFAULT_WINDOW_AFTER,
    DEFAULT_WINDOW_BEFORE,
    DOMAIN,
    NEAREST_STOP_COUNT,
    SCHEDULE_TZ,
)
from .stops import StopChoice, nearest_stops, next_visit, routes_for_postal_code
from .tracker import haversine_m

_LOGGER = logging.getLogger(__name__)

WEEKDAY_LABEL = {
    "monday": "ma", "tuesday": "ti", "wednesday": "ke", "thursday": "to",
    "friday": "pe", "saturday": "la", "sunday": "su",
}
# Postal codes can match many routes in the 3-digit fallback; keep the API load sane.
MAX_ROUTES_TO_SEARCH = 25


def choice_label(choice: StopChoice) -> str:
    """'Stop name – 140 m – reitti 11_101_1 (pe) – seuraava 17.7. 17:20'."""
    weekday = WEEKDAY_LABEL.get(choice.route.weekday or "", "")
    route = f"reitti {choice.route.route_id}" + (f" ({weekday})" if weekday else "")
    visit = f"seuraava {choice.next_visit.astimezone(SCHEDULE_TZ):%-d.%-m. %H:%M}" if choice.next_visit else "ei tulevaa aikataulua"
    return f"{choice.stop.name} – {round(choice.distance_m)} m – {route} – {visit}"


class JatskiautoConfigFlow(ConfigFlow, domain=DOMAIN):
    """Pick a route and a stop to follow."""

    VERSION = 1

    def __init__(self) -> None:
        self._api_key: str | None = None
        self._routes: list[Route] = []
        self._location: tuple[float, float] | None = None
        self._choices: dict[str, StopChoice] = {}

    def _client(self) -> PaikannuspalveluClient:
        assert self._api_key is not None
        return PaikannuspalveluClient(async_get_clientsession(self.hass), self._api_key)

    @override
    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Ask for the data key and check it."""
        errors: dict[str, str] = {}
        if user_input is not None:
            self._api_key = user_input[CONF_API_KEY].strip()
            try:
                self._routes = await self._client().routes()
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except CannotConnect:
                errors["base"] = "cannot_connect"
            else:
                if not self._routes:
                    return self.async_abort(reason="no_routes")
                return await self.async_step_pick()
        existing = next((e.data[CONF_API_KEY] for e in self._async_current_entries() if CONF_API_KEY in e.data), "")
        return self.async_show_form(
            step_id="user",
            data_schema=probatio.Schema({probatio.Required(CONF_API_KEY, default=existing): str}),
            errors=errors,
        )

    async def async_step_pick(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Choose how to find the stop."""
        return self.async_show_menu(step_id="pick", menu_options=["location", "route"])

    async def async_step_location(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Find the stops nearest a location within a postal code area."""
        errors: dict[str, str] = {}
        if user_input is not None:
            loc = user_input[CONF_LOCATION]
            self._location = (loc["latitude"], loc["longitude"])
            routes = routes_for_postal_code(self._routes, user_input[CONF_POSTAL_CODE])[:MAX_ROUTES_TO_SEARCH]
            if not routes:
                errors[CONF_POSTAL_CODE] = "no_routes_for_postal_code"
            else:
                try:
                    stops_by_route = await self._fetch_stops(routes)
                except CannotConnect:
                    errors["base"] = "cannot_connect"
                else:
                    found = nearest_stops(stops_by_route, *self._location, dt_util.now(), NEAREST_STOP_COUNT)
                    if not found:
                        errors["base"] = "no_stops"
                    else:
                        self._choices = {c.key: c for c in found}
                        return await self.async_step_stop()
        schema = probatio.Schema(
            {
                probatio.Required(
                    CONF_LOCATION,
                    default={"latitude": self.hass.config.latitude, "longitude": self.hass.config.longitude},
                ): LocationSelector(),
                probatio.Required(CONF_POSTAL_CODE): TextSelector(),
            }
        )
        return self.async_show_form(step_id="location", data_schema=schema, errors=errors)

    async def async_step_route(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Pick a route from the full list, then a stop on it."""
        errors: dict[str, str] = {}
        if user_input is not None:
            route = next(r for r in self._routes if r.file == user_input[CONF_ROUTE_FILE])
            try:
                stops_by_route = await self._fetch_stops([route])
            except CannotConnect:
                errors["base"] = "cannot_connect"
            else:
                now = dt_util.now()
                home = (self.hass.config.latitude, self.hass.config.longitude)
                self._choices = {
                    c.key: c
                    for c in (
                        StopChoice(route, stop, haversine_m(*home, stop.lat, stop.lon), next_visit(stop, now))
                        for stop in stops_by_route[0][1]
                    )
                }
                if not self._choices:
                    errors["base"] = "no_stops"
                else:
                    return await self.async_step_stop()
        options = [
            SelectOptionDict(value=r.file, label=r.name or r.route_id)
            for r in sorted(self._routes, key=lambda r: r.route_id)
        ]
        schema = probatio.Schema(
            {probatio.Required(CONF_ROUTE_FILE): SelectSelector(SelectSelectorConfig(options=options, mode=SelectSelectorMode.DROPDOWN))}
        )
        return self.async_show_form(step_id="route", data_schema=schema, errors=errors)

    async def async_step_stop(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Pick the stop and create the entry."""
        if user_input is not None:
            choice = self._choices[user_input[CONF_STOP]]
            await self.async_set_unique_id(f"{choice.route.route_id}_{choice.stop.index}")
            self._abort_if_unique_id_configured()
            return self.async_create_entry(
                title=f"{choice.stop.name} ({choice.route.route_id})",
                data={
                    CONF_API_KEY: self._api_key,
                    CONF_ROUTE_FILE: choice.route.file,
                    CONF_ROUTE_ID: choice.route.route_id,
                    CONF_ROUTE_NAME: choice.route.name,
                    CONF_STOP_INDEX: choice.stop.index,
                    CONF_STOP_NAME: choice.stop.name,
                },
            )
        options = [SelectOptionDict(value=key, label=choice_label(c)) for key, c in self._choices.items()]
        schema = probatio.Schema(
            {probatio.Required(CONF_STOP): SelectSelector(SelectSelectorConfig(options=options, mode=SelectSelectorMode.LIST))}
        )
        return self.async_show_form(step_id="stop", data_schema=schema)

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        """The data key stopped working."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            self._api_key = user_input[CONF_API_KEY].strip()
            try:
                await self._client().routes()
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except CannotConnect:
                errors["base"] = "cannot_connect"
            else:
                return self.async_update_reload_and_abort(
                    self._get_reauth_entry(), data_updates={CONF_API_KEY: self._api_key}
                )
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=probatio.Schema({probatio.Required(CONF_API_KEY): str}),
            errors=errors,
        )

    async def _fetch_stops(self, routes: list[Route]) -> list[tuple[Route, list[Stop]]]:
        client = self._client()
        today = dt_util.now(SCHEDULE_TZ).date()
        results = await asyncio.gather(*(client.stops(r.file, today) for r in routes))
        return list(zip(routes, results, strict=True))

    @staticmethod
    @callback
    @override
    def async_get_options_flow(config_entry: ConfigEntry) -> JatskiautoOptionsFlow:
        return JatskiautoOptionsFlow()


class JatskiautoOptionsFlow(OptionsFlowWithReload):
    """Notification threshold, tracking window and trace recording."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            # Notifications come from live tracking, which starts window_before ahead of the schedule.
            if user_input[CONF_NOTIFY_MINUTES] > user_input[CONF_WINDOW_BEFORE]:
                errors[CONF_NOTIFY_MINUTES] = "notify_before_window"
            else:
                return self.async_create_entry(data=user_input)
        opts = {**self.config_entry.options, **(user_input or {})}

        def minutes(max_value: int) -> NumberSelector:
            return NumberSelector(NumberSelectorConfig(min=0, max=max_value, step=1, mode=NumberSelectorMode.BOX, unit_of_measurement="min"))

        schema = probatio.Schema(
            {
                probatio.Required(CONF_NOTIFY_MINUTES, default=opts.get(CONF_NOTIFY_MINUTES, DEFAULT_NOTIFY_MINUTES)): minutes(60),
                probatio.Required(CONF_WINDOW_BEFORE, default=opts.get(CONF_WINDOW_BEFORE, DEFAULT_WINDOW_BEFORE)): minutes(240),
                probatio.Required(CONF_WINDOW_AFTER, default=opts.get(CONF_WINDOW_AFTER, DEFAULT_WINDOW_AFTER)): minutes(360),
                probatio.Required(CONF_RECORD_TRACES, default=opts.get(CONF_RECORD_TRACES, False)): BooleanSelector(),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema, errors=errors)
