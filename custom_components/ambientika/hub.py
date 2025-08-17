"""A demonstration 'hub' that connects several devices.

References:
 - https://developers.home-assistant.io/docs/integration_fetching_data/#coordinated-single-api-poll-for-data-for-all-entities

"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import timedelta
import asyncio
from typing import Any

from returns.result import Success, Failure

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed

from homeassistant.helpers.update_coordinator import UpdateFailed, DataUpdateCoordinator


from .api import (
    AmbientikaApiClient,
    AmbientikaApiClientAuthenticationError,
    AmbientikaApiClientError,
)
from .const import DOMAIN, LOGGER


class AmbientikaHub(DataUpdateCoordinator):
    """Connection Hub to all devices."""

    config_entry: ConfigEntry

    def __init__(self, hass: HomeAssistant, config: Mapping[str, Any]) -> None:
        """Initialize the hub to manage all devices and the API facade."""
        self._hass_config = hass
        self._hass = hass
        self._credentials = {
            "username": config.get(CONF_USERNAME, ""),
            "password": config.get(CONF_PASSWORD, ""),
        }
        self.devices = []
        self.client = None
        self._rate_limit_lock = asyncio.Lock()
        self._last_update_time = 0
        self._cached_data = None
        self._min_time_between_updates = 30  # Minimum seconds between API calls

        super().__init__(
            hass=hass,
            logger=LOGGER,
            name=DOMAIN,
            update_interval=timedelta(minutes=5),
        )

    async def login(self) -> None:
        """Async loading of the devices."""

        self.client = AmbientikaApiClient(
            username=self._credentials["username"],
            password=self._credentials["password"],
        )
        self.devices = await self.client.async_get_data()

    def invalidate_cache(self) -> None:
        """Invalidate the cache to force a fresh data fetch on next update."""
        LOGGER.debug("Cache invalidated - forcing fresh data fetch on next update")
        self._cached_data = None
        self._last_update_time = 0

    async def _async_update_data(self):
        """Update data via library."""
        try:
            # Use a lock to prevent multiple simultaneous updates
            async with self._rate_limit_lock:
                current_time = self._hass.loop.time()
                time_since_last_update = current_time - self._last_update_time

                # If we have cached data and haven't waited long enough, return the cache
                if self._cached_data is not None and time_since_last_update < self._min_time_between_updates:
                    LOGGER.debug("Using cached data (%.2f seconds since last update)", time_since_last_update)
                    return self._cached_data

                # Otherwise, fetch new data
                LOGGER.debug("HUB: Fetching data from Ambientika API.")
                devices = await self.client.async_get_data()

                # Update device status for each device
                device_updates = []
                for device in devices:
                    try:
                        LOGGER.debug(f"Updating status for device {device.serial_number}")
                        status = await device.status()
                        if isinstance(status, Success):
                            # Store the current status in the device object
                            device.current_status = status.unwrap()
                            LOGGER.debug(f"Successfully updated device {device.serial_number}")
                        elif isinstance(status, Failure):
                            LOGGER.warning(f"Failed to get status for device {device.serial_number}: {status.failure()}")
                        else:
                            LOGGER.warning(f"Unexpected status result type for device {device.serial_number}")
                    except Exception as e:
                        LOGGER.error(f"Error updating device {device.serial_number}: {str(e)}")
                        continue

                LOGGER.debug(f"Update completed for {len(devices)} devices")
                self._cached_data = devices
                self._last_update_time = current_time
                return self._cached_data

        except AmbientikaApiClientAuthenticationError as exception:
            await self.client.close()  # Clean up the failed client
            self.client = None  # Force re-auth on next update
            self._cached_data = None  # Clear cache on auth error
            raise ConfigEntryAuthFailed(exception) from exception
        except AmbientikaApiClientError as exception:
            self._cached_data = None  # Clear cache on error
            raise UpdateFailed(exception) from exception

    async def async_unload(self):
        """Clean up resources when unloading the integration."""
        if self.client:
            await self.client.close()
            self.client = None
