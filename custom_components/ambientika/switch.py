"""Switch platform for Ambientika management settings.

This module provides toggle switches for managing Ambientika zone synchronization settings.
"""

from __future__ import annotations

from typing import Any
from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.components.switch import SwitchEntity
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, LOGGER
from .hub import AmbientikaHub


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up Ambientika management switches."""
    hub: AmbientikaHub = hass.data[DOMAIN][entry.entry_id]

    # Add management toggle switches
    switches = [
        SyncZonesToFloorsSwitch(hub, entry),
        SyncRoomsToAreasSwitch(hub, entry),
    ]

    async_add_entities(switches)


class AmbientikaManagementSwitchBase(CoordinatorEntity, SwitchEntity):
    """Base class for Ambientika management switches."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_should_poll = False

    def __init__(self, coordinator: AmbientikaHub, entry: ConfigEntry, setting_key: str) -> None:
        """Initialize the management switch."""
        super().__init__(coordinator)
        self._entry = entry
        self._setting_key = setting_key

    @property
    def device_info(self):
        """Return device info for the integration."""
        return {
            "identifiers": {(DOMAIN, "management")},
            "name": "Ambientika Management",
            "manufacturer": "SUEDWIND",
            "model": "Integration Management",
        }

    @property
    def is_on(self) -> bool:
        """Return if the switch is on."""
        return self._entry.data.get(self._setting_key, True)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on the switch."""
        await self._update_setting(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off the switch."""
        await self._update_setting(False)

    async def _update_setting(self, value: bool) -> None:
        """Update the setting in config entry."""
        try:
            # Update the config entry data
            new_data = dict(self._entry.data)
            new_data[self._setting_key] = value

            self.hass.config_entries.async_update_entry(
                self._entry,
                data=new_data
            )

            # Trigger zone sync configuration update if we have a zone sync manager
            if hasattr(self.coordinator, '_zone_sync') and self.coordinator._zone_sync:
                sync_manager = self.coordinator._zone_sync
                if hasattr(sync_manager, 'async_update_config'):
                    await sync_manager.async_update_config(new_data)

            # Update the coordinator state
            self.async_write_ha_state()

            LOGGER.info(f"Updated {self._setting_key} to {value}")

        except Exception as e:
            LOGGER.error(f"Error updating {self._setting_key}: {e}")


class SyncZonesToFloorsSwitch(AmbientikaManagementSwitchBase):
    """Switch to control zone to floors synchronization."""

    _attr_has_entity_name = True
    _attr_name = "Sync Zones to Floors"
    _attr_icon = "mdi:floor-plan"

    def __init__(self, coordinator: AmbientikaHub, entry: ConfigEntry) -> None:
        """Initialize the zones to floors switch."""
        super().__init__(coordinator, entry, "sync_zones_to_floors")
        self._attr_unique_id = f"{DOMAIN}_sync_zones_to_floors"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional state attributes."""
        return {
            "description": "Create Home Assistant floors for Ambientika zones when zones are configured",
            "recommendation": "Enable for multi-zone setups to organize devices by floors",
            "affects": "Floor creation in Home Assistant Areas & Zones settings",
        }


class SyncRoomsToAreasSwitch(AmbientikaManagementSwitchBase):
    """Switch to control room to areas synchronization."""

    _attr_has_entity_name = True
    _attr_name = "Sync Rooms to Areas"
    _attr_icon = "mdi:home-group"

    def __init__(self, coordinator: AmbientikaHub, entry: ConfigEntry) -> None:
        """Initialize the rooms to areas switch."""
        super().__init__(coordinator, entry, "sync_rooms_to_areas")
        self._attr_unique_id = f"{DOMAIN}_sync_rooms_to_areas"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional state attributes."""
        return {
            "description": "Create Home Assistant areas for Ambientika rooms for better device organization",
            "recommendation": "Enable for automatic device grouping by room",
            "affects": "Area creation and device assignment in Home Assistant",
        }
