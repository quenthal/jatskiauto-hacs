<img src="custom_components/jatskiauto/brand/icon.png" alt="" width="128" align="right">

# Jätskiauto for Home Assistant

> **Unofficial project.** This integration is not made, endorsed or supported by any ice-cream truck company, nor by Paikannuspalvelu. It is an independent hobby project that reads publicly available route, schedule and position data. All names and trademarks belong to their owners. Use at your own risk.

> **Epävirallinen projekti.** Tämä integraatio ei ole minkään jäätelöautoyrityksen eikä Paikannuspalvelun tekemä, hyväksymä tai tukema. Se on itsenäinen harrastusprojekti, joka käyttää julkisesti saatavilla olevaa reitti-, aikataulu- ja sijaintidataa. Nimet ja tavaramerkit kuuluvat omistajilleen. Käyttö omalla vastuulla.

A custom integration that follows the ice-cream truck on its route to the stop you choose, so that you know when to head out. Data comes from the public Paikannuspalvelu API.

**Status:** early development. The first version picks the stop and shows the next scheduled visit. Live tracking, an ETA range and notifications are in progress.

## Installation

HACS: add this repository as a custom repository (category *Integration*), install *Jätskiauto* and restart Home Assistant.

Manual: copy `custom_components/jatskiauto` into your Home Assistant `config/custom_components/` and restart.

Requires Home Assistant 2026.10 or newer.

## Data key

The integration needs a data key for the Paikannuspalvelu API. **No key is included with this integration, and this project does not tell you where to obtain one.** Ask the API provider for access, and use the API according to its terms. A wrong or missing key is reported as "The data key was rejected".

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
| `sensor.…_next_scheduled_visit` | Next published visit at the stop (timestamp). Attributes: upcoming visits, stop number. |

Schedules are interpreted in Finnish time regardless of the Home Assistant time zone.

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
