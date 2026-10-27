<img src="custom_components/jatskiauto/brand/icon.png" alt="" width="128" align="right">

# Jätskiauto for Home Assistant

> **Unofficial project.** This integration is not made, endorsed or supported by any ice-cream truck company, nor by Paikannuspalvelu. It is an independent hobby project that reads publicly available route, schedule and position data. All names and trademarks belong to their owners. Use at your own risk.

> **Epävirallinen projekti.** Tämä integraatio ei ole minkään jäätelöautoyrityksen eikä Paikannuspalvelun tekemä, hyväksymä tai tukema. Se on itsenäinen harrastusprojekti, joka käyttää julkisesti saatavilla olevaa reitti-, aikataulu- ja sijaintidataa. Nimet ja tavaramerkit kuuluvat omistajilleen. Käyttö omalla vastuulla.

A custom integration that follows the ice-cream truck on its route to the stop you choose, so that you know when to head out. Data comes from the public Paikannuspalvelu API.

**Status:** live tracking, arrival estimate and notification events are in place but have not yet been tested against a real visit (the season starts in summer). Expect tuning.

## Installation

HACS: add this repository as a custom repository (category *Integration*), install *Jätskiauto* and restart Home Assistant.

Manual: copy `custom_components/jatskiauto` into your Home Assistant `config/custom_components/` and restart.

Requires Home Assistant 2026.10 or newer.

## Data key

The integration needs a data key for the Paikannuspalvelu API. The key is **not included** in this integration.

Where to find one: the open-source hobby project [davwunder/jatskiauto](https://github.com/davwunder/jatskiauto) (MIT) uses the same API and has a key in [`scripts/fetch_routes.py`](https://github.com/davwunder/jatskiauto/blob/main/scripts/fetch_routes.py) (`DATA_KEY`), described there as the public API key from Paikannuspalvelu's documentation. The same API powers the public live map of the ice-cream trucks. We have not found that documentation ourselves, so use the key at your own risk and according to the provider's terms. If the key stops working, Home Assistant asks for a new one.

**Suomeksi:** integraatio tarvitsee Paikannuspalvelun data keyn, jota ei toimiteta mukana. Avaimen löytää harrastajaprojektista [davwunder/jatskiauto](https://github.com/davwunder/jatskiauto): tiedostossa `scripts/fetch_routes.py` muuttuja `DATA_KEY`, jota siellä kuvataan Paikannuspalvelun julkiseksi avaimeksi. Käyttö omalla vastuulla.

## Setup

*Settings → Devices & services → Add integration → Jätskiauto*

1. Enter a Paikannuspalvelu data key.
2. Find the stop either
   - from a location (defaults to your home) and postal code: the nearest stops are listed with distance, route, weekday and next visit, or
   - by picking a route and a stop from the full list.

Each followed stop is its own entry; add the integration again to follow another stop. Your home location is only used for the search and is not stored.

Options: notification lead time, live tracking window around the scheduled time, and optional GPS trace recording for troubleshooting.

## Entities

| Entity | Description |
|---|---|
| Next scheduled visit | Next published visit at the stop. |
| Status | `scheduled`, `searching`, `on_route`, `approaching_2_stops`, `approaching_1_stop`, `arrived`, `passed`, `stale`, `no_visits`. Attributes: scheduled time, tracking window, lateness, vehicle. |
| Arrival estimate | Most likely arrival time. |
| Earliest arrival | Earliest plausible arrival; notifications use this. |
| Stops remaining | Stops before the truck reaches yours. |
| Arrived at | When the truck arrived at your stop. |
| Can be here soon | On when the earliest arrival is within the lead time set in the options. |
| Truck | The truck on the map while it is tracked (updated when it moves more than 30 m). |
| Events | `tracking_started`, `reached_stop`, `within_threshold`, `arrived`, `passed`, `signal_lost`. |

## How it works

- Schedules are read twice a day. Nothing else is polled between visits: live tracking runs only in a window around a scheduled visit (by default 60 min before to 150 min after), and stops once the truck has passed your stop.
- The truck is matched to the route only near its previous position, and its status only moves forward during a visit, so a noisy GPS fix does not flip it back and forth.
- The arrival estimate follows the truck's pace today (minutes per stop, selling included), not the schedule, because the truck is often late and rarely catches up. Before the truck is found, the schedule is used.
- The road line between stops is fetched once per route from the public OSRM server and cached; straight lines are used until then.
- Quiet by design: no log lines for normal polling, and only real changes are written to the history database.

Schedules are interpreted in Finnish time regardless of the Home Assistant time zone.

## Notifications

`within_threshold` fires once per visit when the earliest plausible arrival is within the lead time (*Notify when the truck can be here within* in the options). Use the blueprint, or your own automation triggered by the *Events* entity:

[![Import blueprint](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2Fquenthal%2Fjatskiauto-hacs%2Fblob%2Fmain%2Fblueprints%2Fautomation%2Fjatskiauto%2Fnotify_when_close.yaml)

```yaml
triggers:
  - trigger: state
    entity_id: event.jatskiauto_<stop>_events
conditions:
  - condition: template
    value_template: "{{ trigger.to_state.attributes.event_type == 'within_threshold' }}"
actions:
  - action: notify.notify
    data:
      message: "🍦 The ice-cream truck can be here in {{ trigger.to_state.attributes.minutes }} min"
```

Event attributes: `minutes`, `eta_earliest`, `eta_likely`, `stops_remaining`, `scheduled` (and `stop_number` for `reached_stop`, `arrival` for `arrived`).

## Versions

Versions are `YEAR.MONTH.N`, where `N` is the release number within that month (for example `2026.10.1`, `2026.10.2`, `2026.11.1`). Releases are published as GitHub releases with the same tag.

## Development

```bash
uv venv --python 3.14
uv pip install -r requirements_test.txt
.venv/bin/ruff check .
.venv/bin/python -m pytest
```

Tests use made-up routes, stops and positions only.

## License

MIT, see [LICENSE](LICENSE).
