"""Constants for the Jätskiauto integration."""

from typing import Final
from zoneinfo import ZoneInfo

DOMAIN: Final = "jatskiauto"

DEFAULT_API_BASE: Final = "https://api.paikannuspalvelu.fi"
# The public API identifies the caller with this author address.
DEFAULT_AUTHOR: Final = "rest@paikannuspalvelu.fi"
# The API's firewall answers 403 to Home Assistant's default User-Agent.
USER_AGENT: Final = "jatskiauto-hacs (+https://github.com/quenthal/jatskiauto-hacs)"

CONF_POSTAL_CODE: Final = "postal_code"
CONF_ROUTE_ID: Final = "route_id"
CONF_ROUTE_FILE: Final = "route_file"
CONF_ROUTE_NAME: Final = "route_name"
CONF_STOP_INDEX: Final = "stop_index"
CONF_STOP_NAME: Final = "stop_name"
CONF_STOP: Final = "stop"

CONF_NOTIFY_MINUTES: Final = "notify_minutes"
CONF_WINDOW_BEFORE: Final = "window_before_minutes"
CONF_WINDOW_AFTER: Final = "window_after_minutes"
CONF_RECORD_TRACES: Final = "record_traces"

DEFAULT_NOTIFY_MINUTES: Final = 5
DEFAULT_WINDOW_BEFORE: Final = 60
DEFAULT_WINDOW_AFTER: Final = 150

NEAREST_STOP_COUNT: Final = 10

# Published schedules are Finnish local time, whatever the Home Assistant time zone is.
SCHEDULE_TZ: Final = ZoneInfo("Europe/Helsinki")
