"""Shared fixtures. All route, stop and position data here is made up."""

from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Load custom_components/ in every test."""
    return


@pytest.fixture(autouse=True)
def no_osrm_delay(monkeypatch):
    """Do not wait between road geometry requests in tests."""
    monkeypatch.setattr("custom_components.jatskiauto.geometry.OSRM_DELAY_S", 0)


@pytest.fixture
def no_setup():
    """Config flow tests check the flow only, not the entry setup."""
    with patch("custom_components.jatskiauto.async_setup_entry", return_value=True) as mock:
        yield mock
