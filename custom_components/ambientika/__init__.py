"""Custom integration for Ambientika devices with Home Assistant.

For more details about this integration, please refer to
https://github.com/ambientika/HomeAssistant-integration-for-Ambientika
"""

from __future__ import annotations

import logging
import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import config_validation as cv

from .const import DOMAIN
from .hub import AmbientikaHub

# Optional enhanced hub for advanced zone management features
try:
    from .enhanced_hub import EnhancedAmbientikaHub
    ENHANCED_HUB_AVAILABLE = True
except ImportError:
    ENHANCED_HUB_AVAILABLE = False

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.BUTTON,
    Platform.BINARY_SENSOR,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
]

# Optional platforms that can be enabled
DIAGNOSTIC_PLATFORMS: list[Platform] = [
    Platform.SENSOR,  # For diagnostic_sensor.py
]

# Service schemas
SYNC_ZONES_SCHEMA = vol.Schema({
    vol.Optional("entry_id"): cv.string,
    vol.Optional("force_resync", default=False): cv.boolean,
    vol.Optional("create_missing_floors", default=True): cv.boolean,
    vol.Optional("create_missing_areas", default=True): cv.boolean,
})

GET_ZONE_STATUS_SCHEMA = vol.Schema({
    vol.Optional("entry_id"): cv.string,
})


# TODO: can we reduce the frequency of api calls for bad devices?
# https://developers.home-assistant.io/docs/config_entries_index/#setting-up-an-entry
async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up this integration using UI."""
    hass.data.setdefault(DOMAIN, {})

    # Choose hub type based on availability and configuration
    # For testing purposes, enable enhanced hub if available
    use_enhanced_hub = ENHANCED_HUB_AVAILABLE  # Temporarily force enhanced hub for testing

    if use_enhanced_hub:
        _LOGGER.info("Using Enhanced Ambientika Hub with zone management features")
        hub = EnhancedAmbientikaHub(hass=hass, config=entry.data)
    else:
        _LOGGER.info("Using standard Ambientika Hub")
        hub = AmbientikaHub(hass=hass, config=entry.data)

    await hub.login()
    hass.data[DOMAIN][entry.entry_id] = hub

    # https://developers.home-assistant.io/docs/integration_fetching_data#coordinated-single-api-poll-for-data-for-all-entities
    await hub.async_config_entry_first_refresh()

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))

    # Register zone sync services
    await _register_zone_sync_services(hass)

    return True


async def _register_zone_sync_services(hass: HomeAssistant) -> None:
    """Register zone synchronization services."""
    try:
        # Only register if not already registered and zone sync is available
        if not hass.services.has_service(DOMAIN, "sync_zones"):
            hass.services.async_register(
                DOMAIN,
                "sync_zones",
                _handle_sync_zones_service,
                schema=SYNC_ZONES_SCHEMA,
            )
            _LOGGER.debug("Registered sync_zones service")

        if not hass.services.has_service(DOMAIN, "get_zone_status"):
            hass.services.async_register(
                DOMAIN,
                "get_zone_status",
                _handle_get_zone_status_service,
                schema=GET_ZONE_STATUS_SCHEMA,
            )
            _LOGGER.debug("Registered get_zone_status service")

    except Exception as e:
        _LOGGER.error("Failed to register zone sync services: %s", e)


async def _handle_sync_zones_service(call: ServiceCall) -> None:
    """Handle the sync_zones service call."""
    try:
        entry_id = call.data.get("entry_id")
        force_resync = call.data.get("force_resync", False)
        create_missing_floors = call.data.get("create_missing_floors", True)
        create_missing_areas = call.data.get("create_missing_areas", True)

        # Get zone sync managers
        zone_sync_data = call.hass.data.get(DOMAIN, {}).get('zone_sync', {})

        if not zone_sync_data:
            _LOGGER.warning("No zone sync managers available")
            return

        # Sync specific entry or all entries
        entries_to_sync = [entry_id] if entry_id else list(zone_sync_data.keys())

        results = {}
        for sync_entry_id in entries_to_sync:
            zone_sync = zone_sync_data.get(sync_entry_id)
            if zone_sync:
                # Update configuration if provided
                zone_sync._create_missing_floors = create_missing_floors
                zone_sync._create_missing_areas = create_missing_areas

                if force_resync:
                    zone_sync._last_full_sync = None

                result = await zone_sync.async_sync_zones()
                results[sync_entry_id] = result
                _LOGGER.info("Zone sync completed for entry %s: %s", sync_entry_id, result.get("status"))
            else:
                _LOGGER.warning("Zone sync manager not found for entry %s", sync_entry_id)

    except Exception as e:
        _LOGGER.error("Error in sync_zones service: %s", e)


async def _handle_get_zone_status_service(call: ServiceCall) -> None:
    """Handle the get_zone_status service call."""
    try:
        entry_id = call.data.get("entry_id")

        # Get zone sync managers
        zone_sync_data = call.hass.data.get(DOMAIN, {}).get('zone_sync', {})

        if not zone_sync_data:
            _LOGGER.warning("No zone sync managers available")
            return

        # Get status for specific entry or all entries
        entries_to_check = [entry_id] if entry_id else list(zone_sync_data.keys())

        status_results = {}
        for sync_entry_id in entries_to_check:
            zone_sync = zone_sync_data.get(sync_entry_id)
            if zone_sync:
                status = zone_sync.get_sync_status()
                status_results[sync_entry_id] = status
                _LOGGER.debug("Zone sync status for entry %s: %s", sync_entry_id, status.get("zone_mappings_count", 0))
            else:
                _LOGGER.warning("Zone sync manager not found for entry %s", sync_entry_id)

        # Log the combined status
        _LOGGER.info("Zone sync status retrieved for %d entries", len(status_results))

    except Exception as e:
        _LOGGER.error("Error in get_zone_status service: %s", e)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Handle removal of an entry."""
    if unloaded := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        hub = hass.data[DOMAIN].pop(entry.entry_id)
        await hub.async_unload()

        # Clean up zone sync data
        if 'zone_sync' in hass.data.get(DOMAIN, {}):
            hass.data[DOMAIN]['zone_sync'].pop(entry.entry_id, None)

    return unloaded


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload config entry."""
    await async_unload_entry(hass, entry)
    await async_setup_entry(hass, entry)
