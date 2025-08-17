"""Enhanced hub implementation with zone-aware device organization.

This module extends the existing hub to include comprehensive zone management,
providing zone information to all entities and enabling zone-based device grouping.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import timedelta, datetime
import asyncio
from typing import Any
from dataclasses import dataclass

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


@dataclass
class ZoneInfo:
    """Information about a zone configuration."""

    zone_index: int
    master_device: str | None = None
    slave_devices: list[str] = None
    room_ids: list[int] = None
    device_count: int = 0

    def __post_init__(self):
        if self.slave_devices is None:
            self.slave_devices = []
        if self.room_ids is None:
            self.room_ids = []


@dataclass
class HouseInfo:
    """Information about a house and its zones."""

    house_id: int
    house_name: str
    zones: dict[int, ZoneInfo]
    has_zones: bool = False
    total_devices: int = 0


class EnhancedAmbientikaHub(DataUpdateCoordinator):
    """Enhanced hub with zone management capabilities."""

    config_entry: ConfigEntry

    def __init__(self, hass: HomeAssistant, config: Mapping[str, Any]) -> None:
        """Initialize the enhanced hub."""
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
        self._min_time_between_updates = 30

        # Zone management attributes
        self._houses: dict[int, HouseInfo] = {}
        self._device_zones: dict[str, int] = {}  # serial_number -> zone_index
        self._zone_masters: dict[int, str] = {}  # zone_index -> master_serial
        self._zone_slaves: dict[int, list[str]] = {}  # zone_index -> [slave_serials]

        super().__init__(
            hass=hass,
            logger=LOGGER,
            name=DOMAIN,
            update_interval=timedelta(minutes=5),
        )

    async def login(self) -> None:
        """Login and initialize zone data."""
        self.client = AmbientikaApiClient(
            username=self._credentials["username"],
            password=self._credentials["password"],
        )

        # Fetch initial device data
        self.devices = await self.client.async_get_data()

        # Initialize zone information
        await self._initialize_zone_data()

    async def _initialize_zone_data(self) -> None:
        """Initialize zone configuration from device data."""
        LOGGER.debug("Initializing zone data...")

        try:
            # Get houses with zone information if available
            if hasattr(self.client, '_api_client') and self.client._api_client:
                houses_result = await self.client._api_client.houses()

                if isinstance(houses_result, Success):
                    houses = houses_result.unwrap()
                    await self._process_house_zone_data(houses)

        except Exception as e:
            LOGGER.warning(f"Could not fetch house zone data: {e}")

        # Always process device-level zone information
        self._process_device_zone_data()

        LOGGER.info(f"Zone initialization complete: {len(self._houses)} houses, "
                   f"{len(self._device_zones)} devices in zones")

    async def _process_house_zone_data(self, houses) -> None:
        """Process zone data from house information."""
        for house in houses:
            try:
                house_info = HouseInfo(
                    house_id=house.id,
                    house_name=house.name,
                    zones={},
                    has_zones=getattr(house, 'has_zones', False),
                    total_devices=len([device for room in house.rooms for device in room.devices])
                )

                # Process zone information if available
                if hasattr(house, 'zones') and house.zones:
                    LOGGER.debug(f"House {house.name} has zone data: {house.zones}")
                    # Note: The zones structure from API might need additional processing
                    # depending on the actual API response format

                self._houses[house.id] = house_info

            except Exception as e:
                LOGGER.error(f"Error processing house {getattr(house, 'name', 'Unknown')}: {e}")

    def _process_device_zone_data(self) -> None:
        """Process zone information from device data."""
        for device in self.devices:
            try:
                serial = device.serial_number
                zone_index = getattr(device, 'zone_index', 0)
                role = getattr(device, 'role', '').lower()

                # Track device zone assignment
                self._device_zones[serial] = zone_index

                # Initialize zone tracking if needed
                if zone_index not in self._zone_slaves:
                    self._zone_slaves[zone_index] = []

                # Assign master/slave roles
                if role == 'master':
                    self._zone_masters[zone_index] = serial
                    LOGGER.debug(f"Device {device.name} ({serial}) is master for zone {zone_index}")
                elif role in ['slave', 'slaveequalmaster']:
                    self._zone_slaves[zone_index].append(serial)
                    LOGGER.debug(f"Device {device.name} ({serial}) is slave in zone {zone_index}")

            except Exception as e:
                LOGGER.error(f"Error processing device zone data for {device.serial_number}: {e}")

    def get_device_zone(self, serial_number: str) -> int:
        """Get the zone index for a device."""
        return self._device_zones.get(serial_number, 0)

    def get_zone_master(self, zone_index: int) -> str | None:
        """Get the master device serial for a zone."""
        return self._zone_masters.get(zone_index)

    def get_zone_slaves(self, zone_index: int) -> list[str]:
        """Get the slave device serials for a zone."""
        return self._zone_slaves.get(zone_index, [])

    def get_zone_devices(self, zone_index: int) -> list[str]:
        """Get all device serials in a zone."""
        devices = []
        master = self.get_zone_master(zone_index)
        if master:
            devices.append(master)
        devices.extend(self.get_zone_slaves(zone_index))
        return devices

    def get_device_role_in_zone(self, serial_number: str) -> str:
        """Get the role of a device in its zone."""
        zone = self.get_device_zone(serial_number)
        if self.get_zone_master(zone) == serial_number:
            return "Master"
        elif serial_number in self.get_zone_slaves(zone):
            return "Slave"
        return "Unknown"

    def get_zone_summary(self) -> dict[str, Any]:
        """Get a summary of all zone configurations."""
        zones = {}

        # Get all unique zones
        all_zones = set(self._device_zones.values())

        for zone_index in all_zones:
            master_serial = self.get_zone_master(zone_index)
            slaves = self.get_zone_slaves(zone_index)

            # Get device names
            master_name = None
            slave_names = []

            for device in self.devices:
                if device.serial_number == master_serial:
                    master_name = device.name
                elif device.serial_number in slaves:
                    slave_names.append(device.name)

            zones[zone_index] = {
                "master": {
                    "serial": master_serial,
                    "name": master_name
                } if master_serial else None,
                "slaves": [
                    {"serial": serial, "name": name}
                    for serial, name in zip(slaves, slave_names)
                ],
                "device_count": len(self.get_zone_devices(zone_index))
            }

        return {
            "total_zones": len(zones),
            "total_devices": len(self.devices),
            "zones": zones,
            "houses": {
                house_id: {
                    "name": house_info.house_name,
                    "has_zones": house_info.has_zones,
                    "total_devices": house_info.total_devices
                }
                for house_id, house_info in self._houses.items()
            }
        }

    def invalidate_cache(self) -> None:
        """Invalidate the cache and zone data."""
        LOGGER.debug("Cache invalidated - forcing fresh data fetch on next update")
        self._cached_data = None
        self._last_update_time = 0

    async def _async_update_data(self):
        """Update data via library with zone information."""
        try:
            async with self._rate_limit_lock:
                current_time = self._hass.loop.time()
                time_since_last_update = current_time - self._last_update_time

                if self._cached_data is not None and time_since_last_update < self._min_time_between_updates:
                    LOGGER.debug("Using cached data (%.2f seconds since last update)", time_since_last_update)
                    return self._cached_data

                LOGGER.debug("HUB: Fetching data from Ambientika API with zone information.")
                devices = await self.client.async_get_data()

                # Update device status
                for device in devices:
                    try:
                        LOGGER.debug(f"Updating status for device {device.serial_number}")
                        status = await device.status()
                        if isinstance(status, Success):
                            device.current_status = status.unwrap()
                            # Add zone information to device status
                            device.zone_info = {
                                "zone_index": self.get_device_zone(device.serial_number),
                                "role_in_zone": self.get_device_role_in_zone(device.serial_number),
                                "zone_master": self.get_zone_master(self.get_device_zone(device.serial_number)),
                                "zone_devices": self.get_zone_devices(self.get_device_zone(device.serial_number))
                            }
                            LOGGER.debug(f"Successfully updated device {device.serial_number} with zone info")
                        elif isinstance(status, Failure):
                            LOGGER.warning(f"Failed to get status for device {device.serial_number}: {status.failure()}")
                    except Exception as e:
                        LOGGER.error(f"Error updating device {device.serial_number}: {str(e)}")
                        continue

                # Refresh zone data periodically
                if current_time - self._last_update_time > 300:  # Refresh zone data every 5 minutes
                    self._process_device_zone_data()

                LOGGER.debug(f"Update completed for {len(devices)} devices")
                self._cached_data = devices
                self._last_update_time = current_time
                return self._cached_data

        except AmbientikaApiClientAuthenticationError as exception:
            await self.client.close()
            self.client = None
            self._cached_data = None
            raise ConfigEntryAuthFailed(exception) from exception
        except AmbientikaApiClientError as exception:
            self._cached_data = None
            raise UpdateFailed(exception) from exception

    async def async_unload(self):
        """Clean up resources when unloading the integration."""
        if self.client:
            await self.client.close()
            self.client = None

    @property
    def last_update_time(self):
        """Return the last update time as a datetime object."""
        if self._last_update_time:
            return datetime.fromtimestamp(self._last_update_time)
        return None
