"""One shared vehicle list for all followed stops.

The device endpoint returns every vehicle in the country, so stops whose
tracking windows overlap would all download the same list. Entries with the
same data key share one feed: a fetch younger than MAX_AGE is reused, and
callers arriving while a fetch is running wait for it instead of starting
their own.
"""

from __future__ import annotations

import asyncio
import time

from homeassistant.core import HomeAssistant

from .api import Device, PaikannuspalveluClient
from .const import DOMAIN

# Shorter than the shortest poll interval, so every entry still gets fresh data.
MAX_AGE_S = 8.0


class DeviceFeed:
    def __init__(self, client: PaikannuspalveluClient) -> None:
        self.client = client
        self.users = 0
        self._lock = asyncio.Lock()
        self._fetched_at = 0.0
        self._devices: list[Device] | None = None

    async def devices(self) -> list[Device]:
        async with self._lock:
            if self._devices is not None and time.monotonic() - self._fetched_at < MAX_AGE_S:
                return self._devices
            devices = await self.client.devices()
            self._devices, self._fetched_at = devices, time.monotonic()
            return devices


def acquire_feed(hass: HomeAssistant, data_key: str, client: PaikannuspalveluClient) -> DeviceFeed:
    feeds: dict[str, DeviceFeed] = hass.data.setdefault(DOMAIN, {}).setdefault("feeds", {})
    feed = feeds.get(data_key)
    if feed is None:
        feed = feeds[data_key] = DeviceFeed(client)
    feed.users += 1
    return feed


def release_feed(hass: HomeAssistant, data_key: str) -> None:
    feeds: dict[str, DeviceFeed] = hass.data.get(DOMAIN, {}).get("feeds", {})
    feed = feeds.get(data_key)
    if feed is not None:
        feed.users -= 1
        if feed.users <= 0:
            del feeds[data_key]
