"""Configuration flow.

This will prompt the user for input when adding the intergation.
https://developers.home-assistant.io/docs/config_entries_config_flow_handler
"""

from __future__ import annotations

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import selector

from .api import (
    AmbientikaApiClient,
    AmbientikaApiClientAuthenticationError,
    AmbientikaApiClientError,
)
from .const import DOMAIN, LOGGER

# Configuration keys for zone sync settings
CONF_SYNC_ZONES_TO_FLOORS = "sync_zones_to_floors"
CONF_SYNC_ROOMS_TO_AREAS = "sync_rooms_to_areas"


class AmbientikaFlowHandler(config_entries.ConfigFlow, domain=DOMAIN):
    """Config Flow Class."""

    VERSION = 1
    CONNECTION_CLASS = config_entries.CONN_CLASS_CLOUD_POLL

    def __init__(self):
        """Initialize the config flow."""
        self._user_input = {}

    async def async_step_user(
        self,
        user_input: dict | None = None,
    ) -> FlowResult:
        """Handle a flow initialized by the user."""
        errors = {}

        if user_input is not None:
            try:
                devices = await _test_pairing(
                    user_input[CONF_USERNAME], user_input[CONF_PASSWORD]
                )
            except AmbientikaApiClientAuthenticationError as exception:
                LOGGER.warning(exception)
                errors["base"] = "auth"
            except AmbientikaApiClientError as exception:
                LOGGER.exception(exception)
                errors["base"] = "unknown"
            except Exception as exception:  # pylint: disable=broad-except
                LOGGER.exception(exception)
                errors["base"] = "unknown"

            if not errors:
                if len(devices) == 0:
                    return self.async_abort(reason="no_supported_devices_found")

                # Store credentials and proceed to zone sync settings
                self._user_input = user_input
                return await self.async_step_zone_sync()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_USERNAME,
                        default=(user_input or {}).get(CONF_USERNAME, vol.UNDEFINED),
                    ): selector.TextSelector(
                        selector.TextSelectorConfig(
                            type=selector.TextSelectorType.TEXT
                        ),
                    ),
                    vol.Required(CONF_PASSWORD): selector.TextSelector(
                        selector.TextSelectorConfig(
                            type=selector.TextSelectorType.PASSWORD
                        ),
                    ),
                }
            ),
            errors=errors,
        )

    async def async_step_zone_sync(
        self,
        user_input: dict | None = None,
    ) -> FlowResult:
        """Handle zone synchronization settings step."""
        if user_input is not None:
            # Combine all configuration data
            config_data = {
                **self._user_input,
                **user_input
            }

            return self.async_create_entry(
                title=self._user_input[CONF_USERNAME],
                data=config_data,
            )

        return self.async_show_form(
            step_id="zone_sync",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_SYNC_ZONES_TO_FLOORS,
                        default=True,
                    ): selector.BooleanSelector(),
                    vol.Optional(
                        CONF_SYNC_ROOMS_TO_AREAS,
                        default=True,
                    ): selector.BooleanSelector(),
                }
            ),
            description_placeholders={
                "sync_zones_description": "Create Home Assistant floors for Ambientika zones when zones are configured",
                "sync_rooms_description": "Create Home Assistant areas for Ambientika rooms"
            },
        )


async def _test_pairing(username, password) -> list:
    client = AmbientikaApiClient(username, password)
    return await client.async_get_data()
