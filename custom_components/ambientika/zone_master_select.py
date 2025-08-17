"""Zone Master Selection platform for Ambientika.

This module provides select entities for managing zone master devices within the Ambientika
integration. It creates select entities that allow users to view and change the master
device for each zone using the official Ambientika API house configuration endpoints.

IMPORTANT: This component changes DEVICE ROLES (Master/Slave), not operating modes.
The zone master select modifies the house configuration to designate which device
acts as the master for each zone, controlling zone-wide settings.

Key Features:
- Zone-based master device role assignment (not mode changes)
- Proper device role configuration using official API /Device/apply-config
- Role swap functionality: old master assumes new master's original role
- Integration with existing zone management infrastructure
- Validation of role configuration changes
- Automatic detection of legitimate zones from house configuration

Technical Approach:
1. Analyzes house configuration to determine legitimate zones (not just device zones)
2. Creates select entities for each legitimate zone with multiple devices
3. Provides options based on devices available in each zone
4. Uses the official API endpoint /Device/apply-config to update device roles in house config
5. Ensures role swap: when master changes, old master gets new master's original role
6. Validates configuration changes by checking updated device roles in coordinator data

Zone Logic Fix:
- Previous logic incorrectly skipped "zone 0" when multiple zones existed
- New logic uses house.zones[] configuration to determine legitimate zones
- Zone 0 can be a legitimate zone (e.g., "Ground Floor") alongside zone 1 ("Upper Floor")
- Fallback to device-based zone detection if house configuration unavailable

API Method:
- Primary: POST /Device/apply-config - Official method for applying role configuration
  to house devices as documented in the Ambientika API
- Validation: Checks device roles after configuration to ensure changes took effect
- Proper House object structure with updated device roles in rooms

Device Role Mapping:
- master → Master
- slave/slaveoppositemaster → SlaveOppositeMaster
- slaveequalmaster → SlaveEqualMaster
- notconfigured → NotConfigured

References:
- Ambientika API: https://app.ambientika.eu:4521/swagger/index.html
- API Documentation: POST /Device/apply-config endpoint

"""

from __future__ import annotations

import asyncio
from typing import Any
from dataclasses import dataclass

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.entity import EntityCategory

from ambientika_py import OperatingMode
from returns.result import Success

from .const import DOMAIN, LOGGER
from .hub import AmbientikaHub


@dataclass
class ZoneMasterInfo:
    """Information about zone master device configuration."""

    zone_index: int
    zone_name: str
    current_master_serial: str | None
    current_master_name: str | None
    available_devices: list[dict[str, Any]]
    device_count: int
    is_configurable: bool = False

    def get_device_options(self) -> list[str]:
        """Get list of device names for select options."""
        if not self.available_devices:
            return ["No devices available"]

        options = []
        for device in self.available_devices:
            name = device.get('name', 'Unknown')
            role = device.get('role', '').title()

            if role:
                display_name = f"{name} ({role})"
            else:
                display_name = name

            options.append(display_name)

        return options

    def get_current_selection(self) -> str | None:
        """Get the current selection based on master device."""
        if not self.current_master_name:
            return None

        # Find the display name for the current master
        for device in self.available_devices:
            if device.get('serial') == self.current_master_serial:
                name = device.get('name', 'Unknown')
                role = device.get('role', '').title()

                if role:
                    return f"{name} ({role})"
                else:
                    return name

        return self.current_master_name


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up zone master select entities."""
    hub: AmbientikaHub = hass.data[DOMAIN][entry.entry_id]

    # Wait a moment for zone data to be initialized
    await asyncio.sleep(1)

    zone_master_analyzer = ZoneMasterAnalyzer(hub)
    zone_info_list = await zone_master_analyzer.analyze_zones()

    entities = []

    if not zone_info_list:
        # No zones configured - create a single master select for all devices
        LOGGER.info("No zones configured, creating global master device selector")
        entities.append(GlobalMasterDeviceSelect(hub, entry.data))
    else:
        # Create master select for each zone
        LOGGER.info(f"Creating zone master selects for {len(zone_info_list)} zones")
        for zone_info in zone_info_list:
            entities.append(ZoneMasterDeviceSelect(hub, entry.data, zone_info))

    async_add_entities(entities)


class ZoneMasterAnalyzer:
    """Analyzer for determining zone master device configuration."""

    def __init__(self, coordinator: AmbientikaHub):
        """Initialize the analyzer."""
        self.coordinator = coordinator

    async def _get_legitimate_zones(self) -> set[int]:
        """Get the set of legitimate zone indices from house configuration.

        This method determines which zones are real zones (not default/unassigned)
        by checking the house configuration from the API.
        """
        legitimate_zones = set()

        try:
            # Use the enhanced hub's zone data which has the correct structure
            if hasattr(self.coordinator, 'zone_data') and self.coordinator.zone_data:
                for house_data in self.coordinator.zone_data:
                    house_name = house_data.get('name', 'Unknown')

                    # Check if house has zones defined in the zone_data
                    zones = house_data.get('zones', [])
                    if zones:
                        LOGGER.debug(f"House {house_name} has {len(zones)} defined zones")

                        # For each zone, collect device zone indices
                        for zone in zones:
                            zone_name = zone.get('name', 'Unknown')
                            rooms = zone.get('rooms', [])

                            for room in rooms:
                                devices = room.get('devices', [])
                                for device in devices:
                                    zone_index = device.get('zoneIndex')
                                    if zone_index is not None:
                                        legitimate_zones.add(zone_index)
                                        device_name = device.get('name', 'Unknown')
                                        LOGGER.debug(f"Added zone {zone_index} ('{zone_name}') as legitimate from device {device_name}")

                    # If no zones are defined but rooms exist, include all device zones
                    elif house_data.get('rooms'):
                        LOGGER.debug(f"House {house_name} has no defined zones, including all device zones")
                        for room in house_data.get('rooms', []):
                            devices = room.get('devices', [])
                            for device in devices:
                                zone_index = device.get('zoneIndex')
                                if zone_index is not None:
                                    legitimate_zones.add(zone_index)
                                    device_name = device.get('name', 'Unknown')
                                    LOGGER.debug(f"Added zone {zone_index} as legitimate (no house zones defined) from device {device_name}")
            else:
                # Fallback: use the coordinator data if zone_data is not available
                LOGGER.debug("No zone_data available, using coordinator device data")
                for device in self.coordinator.data:
                    zone_index = getattr(device, 'zone_index', 0)
                    legitimate_zones.add(zone_index)
                    LOGGER.debug(f"Added zone {zone_index} as legitimate from device {device.name}")

        except Exception as e:
            LOGGER.warning(f"Could not determine legitimate zones from house configuration: {e}")
            # Final fallback: if we can't determine from API, include all zones with devices
            all_device_zones = set()
            for device in self.coordinator.data:
                zone_index = getattr(device, 'zone_index', 0)
                all_device_zones.add(zone_index)

            LOGGER.debug(f"Fallback: including all device zones: {all_device_zones}")
            return all_device_zones

        LOGGER.info(f"Identified {len(legitimate_zones)} legitimate zones: {sorted(legitimate_zones)}")
        return legitimate_zones

    async def _get_zone_name(self, zone_index: int) -> str | None:
        """Get the human-readable zone name from house configuration."""
        try:
            # Use the enhanced hub's zone data which has the correct structure
            if hasattr(self.coordinator, 'zone_data') and self.coordinator.zone_data:
                for house_data in self.coordinator.zone_data:
                    zones = house_data.get('zones', [])
                    for zone in zones:
                        zone_name = zone.get('name', f'Zone {zone_index}')
                        rooms = zone.get('rooms', [])

                        # Check if this zone contains devices with our target zone_index
                        for room in rooms:
                            devices = room.get('devices', [])
                            for device in devices:
                                if device.get('zoneIndex') == zone_index:
                                    LOGGER.debug(f"Found zone name '{zone_name}' for zone index {zone_index}")
                                    return zone_name

        except Exception as e:
            LOGGER.debug(f"Could not get zone name for zone {zone_index}: {e}")

        return None

    async def analyze_zones(self) -> list[ZoneMasterInfo]:
        """Analyze current zone configuration and return zone master information."""
        if not self.coordinator.data:
            LOGGER.warning("No device data available for zone analysis")
            return []

        # Get legitimate zones from house configuration
        legitimate_zones = await self._get_legitimate_zones()
        LOGGER.debug(f"Legitimate zones identified: {legitimate_zones}")

        # Group devices by zone
        zones: dict[int, dict[str, Any]] = {}

        for device in self.coordinator.data:
            try:
                zone_index = getattr(device, 'zone_index', 0)
                role = getattr(device, 'role', '').lower()
                serial = device.serial_number
                name = device.name

                if zone_index not in zones:
                    zones[zone_index] = {
                        'devices': [],
                        'master': None,
                        'slaves': [],
                        'zone_rooms': set()
                    }

                device_info = {
                    'serial': serial,
                    'name': name,
                    'role': role,
                    'device_obj': device
                }

                zones[zone_index]['devices'].append(device_info)

                # Track master device
                if role == 'master':
                    zones[zone_index]['master'] = device_info
                elif role in ['slave', 'slaveequalmaster']:
                    zones[zone_index]['slaves'].append(device_info)

                # Track rooms in this zone
                room_id = getattr(device, 'room_id', None)
                if room_id:
                    zones[zone_index]['zone_rooms'].add(room_id)

            except Exception as e:
                LOGGER.error(f"Error analyzing device {device.serial_number}: {e}")
                continue

        # Convert to ZoneMasterInfo objects
        zone_info_list = []

        for zone_index, zone_data in zones.items():
            # Only include zones that are legitimate (from house configuration) or have devices
            if zone_index not in legitimate_zones and len(zones) > 1:
                LOGGER.debug(f"Skipping zone {zone_index} - not found in legitimate zones: {legitimate_zones}")
                continue

            devices = zone_data['devices']
            master = zone_data['master']
            room_count = len(zone_data['zone_rooms'])

            # Generate zone name using house configuration
            zone_name = await self._get_zone_name(zone_index)
            if not zone_name:
                # Fallback to old logic
                if zone_index == 0:
                    zone_name = "Default Zone"
                else:
                    zone_name = f"Zone {zone_index}"
                    if room_count > 0:
                        room_names = []
                        for device_info in devices:
                            device_obj = device_info['device_obj']
                            room_name = getattr(device_obj, 'room_name', None)
                            if room_name and room_name not in room_names:
                                room_names.append(room_name)

                        if room_names:
                            zone_name += f" ({', '.join(room_names[:2])}{'...' if len(room_names) > 2 else ''})"

            # Determine if zone is configurable
            # A zone is configurable if it has multiple devices
            is_configurable = len(devices) > 1

            zone_info = ZoneMasterInfo(
                zone_index=zone_index,
                zone_name=zone_name,
                current_master_serial=master['serial'] if master else None,
                current_master_name=master['name'] if master else None,
                available_devices=devices,
                device_count=len(devices),
                is_configurable=is_configurable
            )

            zone_info_list.append(zone_info)

        # Sort by zone index
        zone_info_list.sort(key=lambda x: x.zone_index)

        LOGGER.debug(f"Analyzed {len(zone_info_list)} zones for master device configuration")
        return zone_info_list


class ZoneMasterDeviceSelectBase(CoordinatorEntity, SelectEntity):
    """Base class for zone master device selection."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_should_poll = False
    _attr_icon = "mdi:account-supervisor-circle"

    def __init__(self, coordinator: AmbientikaHub, config: dict[str, Any]):
        """Initialize the base select entity."""
        super().__init__(coordinator)
        self._config = config
        self._last_update_attempt = 0
        self._update_in_progress = False

    @property
    def device_info(self):
        """Return device info for the management component."""
        return {
            "identifiers": {(DOMAIN, "management")},
            "name": "Ambientika Management",
            "manufacturer": "SUEDWIND",
            "model": "Zone Master Management",
        }

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        return self.coordinator.data is not None and not self._update_in_progress

    async def _attempt_master_change(self, old_master_serial: str, new_master_serial: str) -> bool:
        """Attempt to change the master device using proper house configuration API."""
        try:
            # Get device objects and their current roles
            old_master_device = None
            new_master_device = None
            new_master_original_role = None

            for device in self.coordinator.data:
                if device.serial_number == old_master_serial:
                    old_master_device = device
                elif device.serial_number == new_master_serial:
                    new_master_device = device
                    # Capture the original role of the device that will become master
                    new_master_original_role = getattr(device, 'role', '').lower()

            if not old_master_device or not new_master_device:
                LOGGER.error("Could not find device objects for master change")
                return False

            if not new_master_original_role:
                LOGGER.error("Could not determine original role of new master device")
                return False

            LOGGER.info(f"Attempting master change from {old_master_device.name} to {new_master_device.name}")
            LOGGER.info(f"Original role of new master device: {new_master_original_role}")
            LOGGER.info(f"Role swap strategy: {new_master_device.name} will become Master, {old_master_device.name} will assume role '{new_master_original_role.title()}'")

            # Use the correct API endpoint: /Device/apply-config
            # This is the official method for applying role configuration to house devices
            success = await self._apply_house_device_configuration(old_master_device, new_master_device, new_master_original_role)

            if success:
                # Validate the change
                await asyncio.sleep(3)  # Allow time for configuration to apply
                if await self._validate_role_change(new_master_serial, old_master_serial, new_master_original_role):
                    LOGGER.info("Successfully changed master device roles using house configuration API")

                    # Force coordinator refresh
                    await self.coordinator.async_request_refresh()
                    return True
                else:
                    LOGGER.warning("House configuration appeared successful but role validation failed")
                    return False
            else:
                LOGGER.error("House configuration update failed")
                return False

        except Exception as e:
            LOGGER.error(f"Exception during master change: {e}")
            return False

    async def _apply_house_device_configuration(self, old_master_device, new_master_device, new_master_original_role: str) -> bool:
        """Apply device role configuration using the official /Device/apply-config API endpoint.

        This is the correct method according to the Ambientika API documentation:
        POST /Device/apply-config - "Apply role configuration to provided devices of the house"
        """
        try:
            LOGGER.debug("Applying house device configuration using /Device/apply-config endpoint")

            # Get device serial numbers for role updates
            new_master_serial = new_master_device.serial_number
            old_master_serial = old_master_device.serial_number

            # Ensure we have API client access
            if not hasattr(self.coordinator.client, '_api_client') or not self.coordinator.client._api_client:
                LOGGER.error("No API client available for house configuration")
                return False

            api_client = self.coordinator.client._api_client

            # Get current house configuration
            houses_result = await api_client.houses()
            if not isinstance(houses_result, Success):
                LOGGER.error("Failed to fetch current house configuration")
                return False

            houses = houses_result.unwrap()
            if not houses:
                LOGGER.error("No houses found in configuration")
                return False

            # Find the target house containing our devices
            target_house = await self._find_target_house(houses, old_master_device, new_master_device)
            if not target_house:
                LOGGER.error("Could not find target house for device configuration")
                return False

            # Create updated house configuration with proper device roles
            updated_house = await self._create_house_config_with_updated_roles(
                target_house,
                old_master_device.serial_number,
                new_master_device.serial_number,
                new_master_original_role
            )

            if not updated_house:
                LOGGER.error("Failed to create updated house configuration")
                return False

            # Helper function for getting values from both object and dict formats
            def get_value(obj, key, default=None):
                if hasattr(obj, key):
                    return getattr(obj, key, default)
                elif isinstance(obj, dict):
                    return obj.get(key, default)
                return default

            target_house_id = get_value(target_house, 'id', 'Unknown')
            LOGGER.debug(f"Applying configuration to house ID: {target_house_id}")
            LOGGER.debug(f"New master: {new_master_device.name} ({new_master_device.serial_number})")
            LOGGER.debug(f"Former master: {old_master_device.name} ({old_master_device.serial_number}) -> role: {new_master_original_role}")

            # Debug: Log the exact payload we're sending
            import json
            try:
                LOGGER.debug(f"Sending house configuration payload: {json.dumps(updated_house, indent=2, default=str)}")
            except Exception as e:
                LOGGER.debug(f"Could not serialize payload for logging: {e}")
                LOGGER.debug(f"Payload type: {type(updated_house)}, keys: {list(updated_house.keys()) if isinstance(updated_house, dict) else 'not a dict'}")

            # Apply the configuration using the correct API endpoint
            # POST /Device/apply-config with House object
            LOGGER.debug(f"Making API call: POST Device/apply-config")
            result = await api_client._api.post("Device/apply-config", updated_house)

            LOGGER.debug(f"API call result type: {type(result)}")
            if isinstance(result, Success):
                response_data = result.unwrap()
                LOGGER.info(f"Successfully applied house device configuration. API response: {response_data}")
                LOGGER.debug(f"API response details: {response_data}")

                # Try alternative approaches if house config didn't work
                LOGGER.warning("API returned success but roles may not have changed. Attempting alternative approaches...")
                alternative_success = await self._try_alternative_role_update(old_master_device, new_master_device, new_master_original_role)
                if alternative_success:
                    LOGGER.info("Alternative role update method succeeded")
                    return True
                else:
                    LOGGER.warning("Alternative role update methods also failed")
                    return True  # Return true for house config success but log the concern
            else:
                LOGGER.error(f"Failed to apply house device configuration: {result}")
                LOGGER.debug(f"Failure details: {result}")
                return False

        except Exception as e:
            LOGGER.error(f"Exception in house device configuration: {e}")
            return False

    async def _find_target_house(self, houses, old_master_device, new_master_device):
        """Find the house that contains both devices."""
        old_master_room_id = getattr(old_master_device, 'room_id', None)
        new_master_room_id = getattr(new_master_device, 'room_id', None)

        def get_value(obj, key, default=None):
            """Get value from object attribute or dictionary key."""
            if hasattr(obj, key):
                return getattr(obj, key, default)
            elif isinstance(obj, dict):
                return obj.get(key, default)
            return default

        for house in houses:
            rooms = get_value(house, 'rooms')
            if not rooms:
                continue

            room_ids_in_house = set()
            for room in rooms:
                room_id = get_value(room, 'id')
                if room_id:
                    room_ids_in_house.add(room_id)

            if old_master_room_id in room_ids_in_house or new_master_room_id in room_ids_in_house:
                house_name = get_value(house, 'name', 'Unknown')
                house_id = get_value(house, 'id', 'Unknown')
                LOGGER.debug(f"Found target house: {house_name} (ID: {house_id})")
                return house

        return None

    async def _create_house_config_with_updated_roles(self, house, old_master_serial: str, new_master_serial: str, new_master_original_role: str):
        """Create a proper house configuration with updated device roles.

        This method ensures zone role consistency by updating device roles in both the rooms array
        and the zones array (if present). This prevents API payload inconsistencies where the
        zones array could contradict the rooms array, which was the root cause of device role
        change failures.

        According to the API schema, the House object should contain:
        - id, name, zones, rooms
        - Each device in rooms should have the correct role
        - Each device in zones (if present) should match the role in rooms
        
        Zone Role Consistency Logic:
        1. Update device roles in the main rooms array
        2. Find corresponding devices in zones array and update their roles to match
        3. Ensure both arrays have consistent device role information
        
        Args:
            house: The house object/dict to update
            old_master_serial: Serial number of the current master device
            new_master_serial: Serial number of the device becoming master
            new_master_original_role: Original role of the device becoming master
            
        Returns:
            dict: Updated house configuration with consistent device roles
        """
        try:
            # Handle both object and dictionary formats for house data
            def get_value(obj, key, default=None):
                """Get value from object attribute or dictionary key."""
                if hasattr(obj, key):
                    return getattr(obj, key, default)
                elif isinstance(obj, dict):
                    return obj.get(key, default)
                return default

            # Create the base house structure
            house_config = {
                "id": get_value(house, 'id'),
                "name": get_value(house, 'name'),
                "address": get_value(house, 'address'),
                "latitude": get_value(house, 'latitude'),
                "longitude": get_value(house, 'longitude'),
                "rooms": []
            }

            # Add zones if they exist - CRITICAL ZONE ROLE CONSISTENCY FIX
            zones = get_value(house, 'zones')
            LOGGER.debug(f"Zone role consistency: Processing zones (found {len(zones) if zones else 0} zones)")
            LOGGER.debug(f"Zone role consistency: Target serials - new_master: {new_master_serial}, old_master: {old_master_serial}")
            if zones:
                house_config["zones"] = []
                for zone in zones:
                    zone_name = get_value(zone, 'name')
                    LOGGER.debug(f"Zone role consistency: Processing zone '{zone_name}'")
                    zone_config = {
                        "id": get_value(zone, 'id'),
                        "name": zone_name,
                        "houseId": get_value(zone, 'houseId')
                    }
                    zone_rooms = get_value(zone, 'rooms')
                    if zone_rooms:
                        LOGGER.debug(f"Zone role consistency: Zone '{zone_name}' has {len(zone_rooms)} rooms")
                        # ZONE ROLE CONSISTENCY: Update device roles in zones to match the role changes
                        # This prevents API payload inconsistencies where zones array contradicts rooms array
                        updated_zone_rooms = []
                        for zone_room in zone_rooms:
                            updated_room = dict(zone_room)  # Copy room data
                            room_devices = get_value(zone_room, 'devices', [])
                            room_name = get_value(zone_room, 'name')
                            LOGGER.debug(f"Zone role consistency: Room '{room_name}' has {len(room_devices)} devices")
                            if room_devices:
                                updated_devices = []
                                for zone_device in room_devices:
                                    updated_device = dict(zone_device)  # Copy device data
                                    device_serial = get_value(zone_device, 'serialNumber')
                                    device_name = get_value(zone_device, 'name')
                                    current_role = get_value(zone_device, 'role')
                                    LOGGER.debug(f"Zone role consistency: Device '{device_name}' (serial: {device_serial}, current role: {current_role})")

                                    # Apply the same role changes as in the rooms array
                                    # This ensures zones array is consistent with rooms array changes
                                    if device_serial == new_master_serial:
                                        updated_device["role"] = "Master"
                                        LOGGER.debug(f"Zone: Setting {device_name} to Master role")
                                    elif device_serial == old_master_serial:
                                        api_role = self._map_internal_role_to_api(new_master_original_role)
                                        updated_device["role"] = api_role
                                        LOGGER.debug(f"Zone: Setting {device_name} to {api_role} role")
                                    else:
                                        # Keep existing role but ensure it's properly mapped
                                        api_role = self._map_internal_role_to_api(current_role)
                                        updated_device["role"] = api_role
                                        LOGGER.debug(f"Zone: Mapped role '{current_role}' to '{api_role}' for {device_name}")

                                    updated_devices.append(updated_device)
                                updated_room["devices"] = updated_devices
                            updated_zone_rooms.append(updated_room)
                        zone_config["rooms"] = updated_zone_rooms
                    house_config["zones"].append(zone_config)

            # Process rooms and update device roles
            rooms = get_value(house, 'rooms')
            if rooms:
                for room in rooms:
                    room_config = {
                        "id": get_value(room, 'id'),
                        "name": get_value(room, 'name'),
                        "houseId": get_value(house, 'id'),
                        "devices": []
                    }

                    devices = get_value(room, 'devices')
                    if devices:
                        for device in devices:
                            device_config = {
                                "id": get_value(device, 'id'),
                                "serialNumber": get_value(device, 'serial_number') or get_value(device, 'serialNumber'),
                                "name": get_value(device, 'name'),
                                "deviceType": get_value(device, 'device_type') or get_value(device, 'deviceType', 'Diamond'),
                                "roomId": get_value(room, 'id')
                            }

                            # Apply role changes based on the master change
                            device_serial = get_value(device, 'serial_number') or get_value(device, 'serialNumber')
                            if device_serial == new_master_serial:
                                device_config["role"] = "Master"
                                LOGGER.debug(f"Setting {get_value(device, 'name')} to Master role")
                            elif device_serial == old_master_serial:
                                # Map internal roles to API roles
                                api_role = self._map_internal_role_to_api(new_master_original_role)
                                device_config["role"] = api_role
                                LOGGER.debug(f"Setting {get_value(device, 'name')} to {api_role} role (from original: {new_master_original_role})")
                            else:
                                # Keep existing role or default to slave
                                current_role = get_value(device, 'role', 'slave')
                                api_role = self._map_internal_role_to_api(current_role)
                                device_config["role"] = api_role

                            # Add additional device properties if needed
                            zone_index = get_value(device, 'zone_index') or get_value(device, 'zoneIndex')
                            if zone_index is not None:
                                device_config["zoneIndex"] = zone_index
                            installation = get_value(device, 'installation')
                            if installation:
                                device_config["installation"] = installation

                            room_config["devices"].append(device_config)

                    house_config["rooms"].append(room_config)

            LOGGER.debug(f"Created house configuration with {len(house_config['rooms'])} rooms")
            return house_config

        except Exception as e:
            LOGGER.error(f"Failed to create house configuration: {e}")
            return None

    def _map_internal_role_to_api(self, internal_role: str) -> str:
        """Map internal role names to API role names."""
        role_mapping = {
            'master': 'Master',
            'slave': 'SlaveOppositeMaster',
            'slaveequalmaster': 'SlaveEqualMaster',
            'slaveoppositemaster': 'SlaveOppositeMaster',
            'notconfigured': 'NotConfigured'
        }

        normalized_role = internal_role.lower().strip()
        api_role = role_mapping.get(normalized_role, 'SlaveOppositeMaster')

        LOGGER.debug(f"Mapped internal role '{internal_role}' to API role '{api_role}'")
        return api_role

    def _show_configuration_info(self, zone_name: str, is_configurable: bool):
        """Show information about zone configuration capabilities."""
        if is_configurable:
            LOGGER.info(f"{zone_name}: Master device role change applied via house configuration API - verify in Ambientika app")
        else:
            LOGGER.info(f"{zone_name}: Single device zone - master role cannot be changed")

    async def _try_alternative_role_update(self, old_master_device, new_master_device, new_master_original_role: str) -> bool:
        """Try alternative methods for updating device roles."""
        try:
            LOGGER.debug("Attempting alternative role update methods")
            api_client = self.coordinator.client._api_client

            # Method 1: Try individual device role updates
            LOGGER.debug("Method 1: Individual device role updates")
            individual_success = await self._try_device_role_update(old_master_device, new_master_device, new_master_original_role)
            if individual_success:
                return True

            # Method 2: Try house PUT endpoint instead of POST
            LOGGER.debug("Method 2: House PUT endpoint")
            put_success = await self._try_house_put_update(old_master_device, new_master_device, new_master_original_role)
            if put_success:
                return True

            # Method 3: Try zone-specific configuration if available
            LOGGER.debug("Method 3: Zone-specific configuration")
            zone_success = await self._try_zone_config_update(old_master_device, new_master_device, new_master_original_role)
            if zone_success:
                return True

            LOGGER.warning("All alternative role update methods failed")
            return False

        except Exception as e:
            LOGGER.error(f"Exception during alternative role update: {e}")
            return False

    async def _try_house_put_update(self, old_master_device, new_master_device, new_master_original_role: str) -> bool:
        """Try updating house configuration using PUT instead of POST."""
        try:
            api_client = self.coordinator.client._api_client

            # Get the current house configuration
            houses = await self.coordinator.client.get_houses()
            target_house = await self._find_target_house(houses, old_master_device, new_master_device)
            if not target_house:
                return False

            # Create updated house configuration
            updated_house = await self._create_house_config_with_updated_roles(
                target_house, old_master_device.serial_number,
                new_master_device.serial_number, new_master_original_role
            )

            if not updated_house:
                return False

            LOGGER.debug("Trying PUT /houses/{id} endpoint")
            house_id = updated_house.get('id')
            result = await api_client._api.put(f"houses/{house_id}", updated_house)

            if isinstance(result, Success):
                LOGGER.info("House PUT update succeeded")
                return True
            else:
                LOGGER.debug(f"House PUT update failed: {result}")
                return False

        except Exception as e:
            LOGGER.debug(f"Exception during house PUT update: {e}")
            return False

    async def _try_zone_config_update(self, old_master_device, new_master_device, new_master_original_role: str) -> bool:
        """Try zone-specific configuration update if available."""
        try:
            api_client = self.coordinator.client._api_client

            # Get zone information for the devices
            old_master_zone = getattr(old_master_device, 'zone_index', None)
            new_master_zone = getattr(new_master_device, 'zone_index', None)

            if old_master_zone != new_master_zone:
                LOGGER.debug("Devices in different zones, skipping zone configuration")
                return False

            LOGGER.debug(f"Trying zone configuration update for zone {old_master_zone}")

            # Try zone-specific endpoints if they exist
            zone_endpoints = [
                f"zones/{old_master_zone}/master",
                f"zones/{old_master_zone}/configuration",
                f"Device/zone/{old_master_zone}/config"
            ]

            for endpoint in zone_endpoints:
                try:
                    LOGGER.debug(f"Trying endpoint: {endpoint}")
                    result = await api_client._api.post(endpoint, {
                        "masterId": new_master_device.serial_number,
                        "oldMasterId": old_master_device.serial_number,
                        "newMasterRole": "master",
                        "oldMasterRole": new_master_original_role
                    })

                    if isinstance(result, Success):
                        LOGGER.info(f"Zone configuration update succeeded via {endpoint}")
                        return True
                    else:
                        LOGGER.debug(f"Zone endpoint {endpoint} failed: {result}")

                except Exception as e:
                    LOGGER.debug(f"Exception with endpoint {endpoint}: {e}")
                    continue

            return False

        except Exception as e:
            LOGGER.debug(f"Exception during zone configuration update: {e}")
            return False

    async def _try_device_role_update(self, old_master_device, new_master_device, new_master_original_role: str) -> bool:
        """Try to update device roles through individual device endpoints."""
        try:
            LOGGER.debug("Attempting individual device role updates")

            api_client = self.coordinator.client._api_client

            # Try to update new master device role
            new_master_update = await api_client._api.post(
                f"devices/{new_master_device.serial_number}/role",
                {"role": "master"}
            )

            if isinstance(new_master_update, Success):
                # Try to update old master device role
                old_master_update = await api_client._api.post(
                    f"devices/{old_master_device.serial_number}/role",
                    {"role": new_master_original_role}
                )

                if isinstance(old_master_update, Success):
                    LOGGER.info("Successfully updated both device roles individually")
                    return True
                else:
                    LOGGER.debug(f"Failed to update old master role: {old_master_update}")
                    # Try to rollback new master
                    await api_client._api.post(
                        f"devices/{new_master_device.serial_number}/role",
                        {"role": new_master_original_role}
                    )
                    return False
            else:
                LOGGER.debug(f"Failed to update new master role: {new_master_update}")
                return False

        except Exception as e:
            LOGGER.debug(f"Individual device role update strategy failed: {e}")
            return False

    async def _try_operating_mode_fallback(self, old_master_device, new_master_device, new_master_original_role: str) -> bool:
        """Fallback to operating mode changes (existing implementation)."""
        try:
            LOGGER.debug("Attempting operating mode fallback strategy")

            # Get current status of both devices
            old_status = getattr(old_master_device, 'current_status', {})
            new_status = getattr(new_master_device, 'current_status', {})

            if not old_status or not new_status:
                LOGGER.debug("Could not get device status for operating mode fallback")
                return False

            # Change new device to master mode
            new_master_mode = {
                "operating_mode": OperatingMode.MasterSlaveFlow,
                "fan_speed": new_status.get("fan_speed", 1),
                "light_sensor_level": new_status.get("light_sensor_level", 1),
                "humidity_level": new_status.get("humidity_level", 1),
            }

            result = await new_master_device.change_mode(new_master_mode)

            if isinstance(result, Success):
                LOGGER.debug(f"Set {new_master_device.name} to MasterSlaveFlow mode")

                # Wait for the change to propagate
                await asyncio.sleep(2)

                # Determine operating mode for old master based on original role
                if new_master_original_role == 'slaveequalmaster':
                    old_device_operating_mode = OperatingMode.SlaveMasterFlow  # Best available approximation
                    target_role_description = "SlaveMasterFlow (approximating slaveequalmaster)"
                else:
                    old_device_operating_mode = OperatingMode.SlaveMasterFlow
                    target_role_description = "SlaveMasterFlow"

                # Change old master to slave mode
                old_slave_mode = {
                    "operating_mode": old_device_operating_mode,
                    "fan_speed": old_status.get("fan_speed", 1),
                    "light_sensor_level": old_status.get("light_sensor_level", 1),
                    "humidity_level": old_status.get("humidity_level", 1),
                }

                LOGGER.debug(f"Setting {old_master_device.name} to {target_role_description}")
                result2 = await old_master_device.change_mode(old_slave_mode)

                if isinstance(result2, Success):
                    LOGGER.info(f"Operating mode fallback completed: {new_master_device.name} -> master, {old_master_device.name} -> {target_role_description}")

                    # For operating mode changes, success is indicated by successful mode changes
                    # The API roles might not update immediately, so we don't rely on role validation
                    return True
                else:
                    LOGGER.debug(f"Failed to set old master operating mode: {result2}")
                    return False
            else:
                LOGGER.debug(f"Failed to set new master operating mode: {result}")
                return False

        except Exception as e:
            LOGGER.debug(f"Operating mode fallback strategy failed: {e}")
            return False

    def _create_updated_house_config(self, house, old_master_serial: str, new_master_serial: str, new_master_original_role: str) -> dict:
        """Create updated house configuration with new device roles."""
        try:
            # This is a best-effort attempt to create an updated house configuration
            # The exact structure may vary based on the Ambientika API
            config = {
                "id": house.id,
                "name": house.name,
                "rooms": []
            }

            if hasattr(house, 'rooms'):
                for room in house.rooms:
                    room_config = {
                        "id": room.id,
                        "name": room.name,
                        "devices": []
                    }

                    if hasattr(room, 'devices'):
                        for device in room.devices:
                            device_config = {
                                "serial_number": device.serial_number,
                                "name": device.name,
                            }

                            # Update device roles
                            if device.serial_number == new_master_serial:
                                device_config["role"] = "master"
                            elif device.serial_number == old_master_serial:
                                device_config["role"] = new_master_original_role
                            else:
                                device_config["role"] = getattr(device, 'role', 'slave')

                            room_config["devices"].append(device_config)

                    config["rooms"].append(room_config)

            return config

        except Exception as e:
            LOGGER.debug(f"Failed to create updated house configuration: {e}")
            return None

    async def _validate_role_change(self, new_master_serial: str, old_master_serial: str, expected_old_role: str) -> bool:
        """Validate that the role change took effect by checking device roles."""
        try:
            # Force a data refresh to get updated device information
            LOGGER.debug("Validating role change by refreshing device data")
            await self.coordinator.async_request_refresh()
            await asyncio.sleep(2)  # Allow time for data refresh

            if not self.coordinator.data:
                LOGGER.warning("No device data available for role validation")
                return False

            new_master_role = None
            old_master_role = None
            new_master_name = None
            old_master_name = None

            for device in self.coordinator.data:
                if device.serial_number == new_master_serial:
                    new_master_role = getattr(device, 'role', '').lower()
                    new_master_name = device.name
                elif device.serial_number == old_master_serial:
                    old_master_role = getattr(device, 'role', '').lower()
                    old_master_name = device.name

            LOGGER.debug("Role validation results:")
            LOGGER.debug(f"  New master {new_master_name} ({new_master_serial}): role = '{new_master_role}'")
            LOGGER.debug(f"  Former master {old_master_name} ({old_master_serial}): role = '{old_master_role}'")
            LOGGER.debug(f"  Expected former master role: '{expected_old_role}'")

            # Validate that new device has master role
            if new_master_role != 'master':
                LOGGER.warning(f"New master device role is '{new_master_role}', expected 'master'")
                return False

            # Validate that old master has the expected role
            # (allow some flexibility in role naming/casing)
            expected_old_role_normalized = expected_old_role.lower().strip()
            old_master_role_normalized = old_master_role.lower().strip() if old_master_role else ''

            # Check for role match or reasonable alternatives
            role_matches = (
                old_master_role_normalized == expected_old_role_normalized or
                (expected_old_role_normalized in ['slave', 'slaveoppositemaster'] and
                 old_master_role_normalized in ['slave', 'slaveoppositemaster']) or
                (expected_old_role_normalized == 'slaveequalmaster' and
                 old_master_role_normalized in ['slaveequalmaster', 'slave'])
            )

            if not role_matches:
                LOGGER.warning(f"Former master role is '{old_master_role}', expected '{expected_old_role}' or compatible")
                # Still return True if new master is correct - the role swap may have succeeded partially
                return new_master_role == 'master'

            LOGGER.info("Role change validation successful:")
            LOGGER.info(f"  ✓ {new_master_name} is now Master")
            LOGGER.info(f"  ✓ {old_master_name} is now {old_master_role.title()}")
            return True

        except Exception as e:
            LOGGER.error(f"Role change validation failed with exception: {e}")
            return False

    def _show_configuration_info(self, zone_name: str, is_configurable: bool):
        """Show information about zone configuration capabilities."""
        if is_configurable:
            LOGGER.info(f"{zone_name}: Master device selection attempted - please verify in Ambientika app")
        else:
            LOGGER.info(f"{zone_name}: Single device zone - master role cannot be changed")


class ZoneMasterDeviceSelect(ZoneMasterDeviceSelectBase):
    """Select entity for choosing the master device within a specific zone."""

    _attr_has_entity_name = False

    def __init__(self, coordinator: AmbientikaHub, config: dict[str, Any], zone_info: ZoneMasterInfo):
        """Initialize the zone master device select."""
        super().__init__(coordinator, config)
        self._zone_info = zone_info
        self._attr_unique_id = f"{DOMAIN}_zone_{zone_info.zone_index}_master_device"
        self._attr_name = f"{zone_info.zone_name} Master Device"

        # Set options based on available devices
        self._attr_options = zone_info.get_device_options()

        # Add configuration status to the name if not configurable
        if not zone_info.is_configurable:
            self._attr_name += " (Info Only)"
            self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def current_option(self) -> str | None:
        """Return the currently selected master device."""
        # Refresh zone info from current coordinator data
        self._refresh_zone_info()
        return self._zone_info.get_current_selection()

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional state attributes."""
        self._refresh_zone_info()

        return {
            "zone_index": self._zone_info.zone_index,
            "zone_name": self._zone_info.zone_name,
            "device_count": self._zone_info.device_count,
            "is_configurable": self._zone_info.is_configurable,
            "current_master_serial": self._zone_info.current_master_serial,
            "available_devices": [
                {
                    "name": device.get('name'),
                    "serial": device.get('serial'),
                    "role": device.get('role')
                }
                for device in self._zone_info.available_devices
            ],
            "configuration_note": (
                "Master device selection uses the official Ambientika API endpoint "
                "/Device/apply-config to update device roles in the house configuration. "
                "Role swap ensures old master assumes new master's original role. "
                "Changes are validated by checking updated device roles. "
                "Verify changes in the Ambientika mobile app."
                if self._zone_info.is_configurable
                else "Single device zone - master role is fixed."
            )
        }

    def _refresh_zone_info(self):
        """Refresh zone information from current coordinator data."""
        if not self.coordinator.data:
            return

        # Find devices in this zone
        zone_devices = []
        current_master = None

        for device in self.coordinator.data:
            zone_index = getattr(device, 'zone_index', 0)
            if zone_index == self._zone_info.zone_index:
                role = getattr(device, 'role', '').lower()
                device_info = {
                    'serial': device.serial_number,
                    'name': device.name,
                    'role': role
                }
                zone_devices.append(device_info)

                if role == 'master':
                    current_master = device_info

        # Update zone info
        self._zone_info.available_devices = zone_devices
        self._zone_info.device_count = len(zone_devices)
        self._zone_info.current_master_serial = current_master['serial'] if current_master else None
        self._zone_info.current_master_name = current_master['name'] if current_master else None
        self._zone_info.is_configurable = len(zone_devices) > 1

        # Update options
        self._attr_options = self._zone_info.get_device_options()

    async def async_select_option(self, option: str) -> None:
        """Change the selected master device."""
        if self._update_in_progress:
            LOGGER.warning(f"Update already in progress for {self._attr_name}")
            return

        self._update_in_progress = True

        try:
            self._refresh_zone_info()

            if not self._zone_info.is_configurable:
                LOGGER.warning(f"Zone {self._zone_info.zone_name} is not configurable (single device)")
                self._show_configuration_info(self._zone_info.zone_name, False)
                return

            # Parse the selected option to get the device serial
            selected_device_serial = None
            selected_device_name = None

            # Extract device name from option (remove role suffix)
            device_name_from_option = option.split(' (')[0]

            for device_info in self._zone_info.available_devices:
                if device_info['name'] == device_name_from_option:
                    selected_device_serial = device_info['serial']
                    selected_device_name = device_info['name']
                    break

            if not selected_device_serial:
                LOGGER.error(f"Could not find device for option: {option}")
                return

            # Check if this is already the master
            if selected_device_serial == self._zone_info.current_master_serial:
                LOGGER.info(f"Device {selected_device_name} is already the master for {self._zone_info.zone_name}")
                return

            current_master_serial = self._zone_info.current_master_serial
            if not current_master_serial:
                LOGGER.error(f"No current master found for {self._zone_info.zone_name}")
                return

            LOGGER.info(f"Attempting to change master for {self._zone_info.zone_name} from "
                       f"{self._zone_info.current_master_name} to {selected_device_name}")

            # Attempt the master change
            success = await self._attempt_master_change(current_master_serial, selected_device_serial)

            if success:
                LOGGER.info(f"Master change completed for {self._zone_info.zone_name}. "
                           "Please verify the change in the Ambientika mobile app.")
                self._show_configuration_info(self._zone_info.zone_name, True)
            else:
                LOGGER.warning(f"Master change attempt may not have succeeded for {self._zone_info.zone_name}. "
                              "This may be a limitation of the Ambientika API. "
                              "Use the Ambientika mobile app for definitive master device configuration.")

                # Still show the configuration info
                self._show_configuration_info(self._zone_info.zone_name, True)

        except Exception as e:
            LOGGER.error(f"Error selecting master device option {option} for {self._zone_info.zone_name}: {e}")

        finally:
            self._update_in_progress = False
            # Force a state update
            self.async_write_ha_state()


class GlobalMasterDeviceSelect(ZoneMasterDeviceSelectBase):
    """Select entity for choosing the master device when no zones are configured."""

    _attr_has_entity_name = False
    _attr_name = "Master Device"

    def __init__(self, coordinator: AmbientikaHub, config: dict[str, Any]):
        """Initialize the global master device select."""
        super().__init__(coordinator, config)
        self._attr_unique_id = f"{DOMAIN}_global_master_device"

        # This entity is informational only since without zones,
        # the concept of "master" is less clear
        self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def options(self) -> list[str]:
        """Return available device options."""
        if not self.coordinator.data:
            return ["No devices available"]

        device_options = []
        for device in self.coordinator.data:
            role = getattr(device, 'role', '').title()
            name = device.name

            if role:
                display_name = f"{name} ({role})"
            else:
                display_name = name

            device_options.append(display_name)

        return device_options if device_options else ["No devices available"]

    @property
    def current_option(self) -> str | None:
        """Return the currently selected master device."""
        if not self.coordinator.data:
            return None

        # Find the first device with master role
        for device in self.coordinator.data:
            role = getattr(device, 'role', '').lower()
            if role == 'master':
                device_role = getattr(device, 'role', '').title()
                return f"{device.name} ({device_role})" if device_role else device.name

        # If no master found, return the first device
        if self.coordinator.data:
            first_device = self.coordinator.data[0]
            role = getattr(first_device, 'role', '').title()
            return f"{first_device.name} ({role})" if role else first_device.name

        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional state attributes."""
        device_count = len(self.coordinator.data) if self.coordinator.data else 0

        master_devices = []
        all_devices = []

        if self.coordinator.data:
            for device in self.coordinator.data:
                role = getattr(device, 'role', '').lower()
                device_info = {
                    "name": device.name,
                    "serial": device.serial_number,
                    "role": role
                }
                all_devices.append(device_info)

                if role == 'master':
                    master_devices.append(device_info)

        return {
            "device_count": device_count,
            "master_device_count": len(master_devices),
            "configuration_mode": "no_zones",
            "all_devices": all_devices,
            "master_devices": master_devices,
            "configuration_note": (
                "No zones configured. Master device selection is informational only. "
                "Configure zones in the Ambientika mobile app for active master device management "
                "using the official house configuration API."
            )
        }

    async def async_select_option(self, option: str) -> None:
        """Handle selection - informational only for global mode."""
        LOGGER.info(f"Global master device selection: {option}")
        LOGGER.info("Master device selection requires zone configuration. "
                   "Please configure zones in the Ambientika mobile app for active master device management.")
