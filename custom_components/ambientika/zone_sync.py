"""Bi-directional zone synchronization between Ambientika and Home Assistant.

This module provides comprehensive synchronization between Ambientika's zone management
and Home Assistant's area/floor structure, creating a unified zone management experience.

Features:
- Sync Ambientika zones to Home Assistant areas/floors
- Create missing floors and areas based on Ambientika structure
- Assign devices to appropriate areas/floors
- Bi-directional updates when zones change
- Maintain synchronization state and handle conflicts
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar, device_registry as dr, floor_registry as fr
from homeassistant.helpers.entity import EntityCategory
from homeassistant.components.sensor import SensorEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.event import async_track_time_interval

from .const import DOMAIN, LOGGER
from .hub import AmbientikaHub

# Sync configuration constants
SYNC_INTERVAL = timedelta(minutes=15)  # How often to check for sync
ZONE_PREFIX = "ambientika_zone_"
FLOOR_PREFIX = "ambientika_floor_"
DEFAULT_FLOOR_NAME = "Ground Floor"


@dataclass
class ZoneMappingInfo:
    """Information about zone to area/floor mapping."""

    ambientika_zone_id: int
    ambientika_house_id: int
    ambientika_house_name: str
    ambientika_room_ids: list[int] = field(default_factory=list)
    ambientika_room_names: list[str] = field(default_factory=list)

    # Home Assistant mappings
    ha_floor_id: str | None = None
    ha_area_ids: list[str] = field(default_factory=list)
    ha_device_ids: list[str] = field(default_factory=list)

    # Sync state
    last_synced: datetime | None = None
    sync_conflicts: list[str] = field(default_factory=list)


@dataclass
class DeviceMappingInfo:
    """Information about device to area assignment."""

    ambientika_serial: str
    ambientika_name: str
    ambientika_zone_id: int
    ambientika_room_id: int | None
    ambientika_room_name: str | None

    # Home Assistant mappings
    ha_device_id: str | None = None
    ha_area_id: str | None = None
    ha_floor_id: str | None = None

    # Sync state
    needs_sync: bool = True
    last_synced: datetime | None = None


class AmbientikaZoneSync:
    """Manages bi-directional synchronization between Ambientika zones and Home Assistant areas."""

    def __init__(self, hass: HomeAssistant, hub: AmbientikaHub, config: Mapping[str, Any]):
        """Initialize the zone sync manager."""
        self.hass = hass
        self.hub = hub
        self.config = config
        self._logger = LOGGER.getChild("zone_sync")

        # Registry references
        self.area_registry = ar.async_get(hass)
        self.device_registry = dr.async_get(hass)
        self.floor_registry = fr.async_get(hass)

        # Sync state
        self._zone_mappings: dict[int, ZoneMappingInfo] = {}
        self._device_mappings: dict[str, DeviceMappingInfo] = {}
        self._sync_in_progress = False
        self._last_full_sync: datetime | None = None

        # Configuration
        self._auto_sync_enabled = config.get("auto_sync_zones", True)
        self._create_missing_areas = config.get("create_missing_areas", True)
        self._create_missing_floors = config.get("create_missing_floors", True)

        # New configuration options
        self._sync_zones_to_floors = config.get("sync_zones_to_floors", True)
        self._sync_rooms_to_areas = config.get("sync_rooms_to_areas", True)

        self._logger.info("Zone sync manager initialized with auto_sync=%s", self._auto_sync_enabled)

    async def async_initialize(self) -> None:
        """Initialize the sync system."""
        try:
            self._logger.info("Initializing Ambientika zone synchronization...")

            # Build initial mappings from current data
            await self._build_zone_mappings()
            await self._build_device_mappings()

            # Perform initial sync if enabled
            if self._auto_sync_enabled:
                await self.async_sync_zones()

            # Set up periodic sync
            if self._auto_sync_enabled:
                async_track_time_interval(
                    self.hass, self._periodic_sync, SYNC_INTERVAL
                )

            self._logger.info("Zone synchronization initialized successfully")

        except Exception as e:
            self._logger.error("Failed to initialize zone sync: %s", e)
            raise

    async def async_update_config(self, new_config: Mapping[str, Any]) -> None:
        """Update configuration and trigger re-sync if needed."""
        old_zones_to_floors = self._sync_zones_to_floors
        old_rooms_to_areas = self._sync_rooms_to_areas

        # Update configuration
        self._sync_zones_to_floors = new_config.get("sync_zones_to_floors", True)
        self._sync_rooms_to_areas = new_config.get("sync_rooms_to_areas", True)
        self._create_missing_areas = new_config.get("create_missing_areas", True)
        self._create_missing_floors = new_config.get("create_missing_floors", True)
        self._auto_sync_enabled = new_config.get("auto_sync_zones", True)

        # Log configuration changes
        if old_zones_to_floors != self._sync_zones_to_floors:
            self._logger.info("Updated sync_zones_to_floors: %s -> %s", old_zones_to_floors, self._sync_zones_to_floors)
        if old_rooms_to_areas != self._sync_rooms_to_areas:
            self._logger.info("Updated sync_rooms_to_areas: %s -> %s", old_rooms_to_areas, self._sync_rooms_to_areas)

        # Trigger re-sync if settings changed
        if (old_zones_to_floors != self._sync_zones_to_floors or
            old_rooms_to_areas != self._sync_rooms_to_areas):
            self._logger.info("Configuration changed, triggering zone re-sync...")
            await self.async_sync_zones()

    async def _build_zone_mappings(self) -> None:
        """Build zone mappings from Ambientika data."""
        self._zone_mappings.clear()

        try:
            # Get house and zone data from hub
            if hasattr(self.hub, 'zone_data') and self.hub.zone_data:
                houses = self.hub.zone_data
            elif hasattr(self.hub, '_zone_data') and self.hub._zone_data:
                houses = self.hub._zone_data
            elif hasattr(self.hub, 'client') and self.hub.client:
                # Fetch fresh house data
                api_client = self.hub.client._api_client
                if api_client:
                    houses_result = await api_client.houses()
                    houses = houses_result.unwrap() if hasattr(houses_result, 'unwrap') else houses_result
                else:
                    houses = []
            else:
                houses = []

            # Process each house and its zones/rooms
            for house_data in houses:
                try:
                    if isinstance(house_data, dict):
                        # Handle dictionary format (from zone_data)
                        house_id = house_data.get('id')
                        house_name = house_data.get('name', 'Unknown House')
                        zones = house_data.get('zones', [])
                        house_rooms = house_data.get('rooms', [])
                    else:
                        # Handle object format (from API)
                        house_id = getattr(house_data, 'id', None)
                        house_name = getattr(house_data, 'name', 'Unknown House')
                        zones = getattr(house_data, 'zones', [])
                        house_rooms = getattr(house_data, 'rooms', [])

                    self._logger.debug(f"Processing house {house_name} with {len(zones)} zones")

                    # Get zone information and room information
                    zones_data = {}
                    room_data = {}

                    # Process zones if they exist
                    if zones:
                        for zone in zones:
                            if isinstance(zone, dict):
                                zone_id = zone.get('id')
                                zone_name = zone.get('name', f'Zone {zone_id}')
                                zone_rooms = zone.get('rooms', [])
                            else:
                                zone_id = getattr(zone, 'id', None)
                                zone_name = getattr(zone, 'name', f'Zone {zone_id}')
                                zone_rooms = getattr(zone, 'rooms', [])

                            self._logger.debug(f"Processing zone {zone_id} ({zone_name}) with {len(zone_rooms)} rooms")

                            # Process rooms in this zone
                            for room in zone_rooms:
                                if isinstance(room, dict):
                                    room_id = room.get('id')
                                    room_name = room.get('name', 'Unknown Room')
                                    room_devices = room.get('devices', [])
                                else:
                                    room_id = getattr(room, 'id', None)
                                    room_name = getattr(room, 'name', 'Unknown Room')
                                    room_devices = getattr(room, 'devices', [])

                                # Track rooms for area creation
                                room_data[room_id] = {
                                    'name': room_name,
                                    'devices': room_devices
                                }

                                # Process devices in this room
                                for device in room_devices:
                                    if isinstance(device, dict):
                                        device_zone_index = device.get('zoneIndex', 0)
                                    else:
                                        device_zone_index = getattr(device, 'zoneIndex', 0)

                                    if device_zone_index not in zones_data:
                                        zones_data[device_zone_index] = {
                                            'room_ids': set(),
                                            'room_names': set(),
                                            'devices': []
                                        }
                                    zones_data[device_zone_index]['room_ids'].add(room_id)
                                    zones_data[device_zone_index]['room_names'].add(room_name)
                                    zones_data[device_zone_index]['devices'].append(device)

                    else:
                        # No zones defined, process rooms directly
                        for room in house_rooms:
                            if isinstance(room, dict):
                                room_id = room.get('id')
                                room_name = room.get('name', 'Unknown Room')
                                room_devices = room.get('devices', [])
                            else:
                                room_id = getattr(room, 'id', None)
                                room_name = getattr(room, 'name', 'Unknown Room')
                                room_devices = getattr(room, 'devices', [])

                            room_data[room_id] = {
                                'name': room_name,
                                'devices': room_devices
                            }

                            for device in room_devices:
                                if isinstance(device, dict):
                                    device_zone_index = device.get('zoneIndex', 0)
                                else:
                                    device_zone_index = getattr(device, 'zoneIndex', 0)

                                if device_zone_index not in zones_data:
                                    zones_data[device_zone_index] = {
                                        'room_ids': set(),
                                        'room_names': set(),
                                        'devices': []
                                    }
                                zones_data[device_zone_index]['room_ids'].add(room_id)
                                zones_data[device_zone_index]['room_names'].add(room_name)
                                zones_data[device_zone_index]['devices'].append(device)

                    # Create zone-based mappings for ALL zones that have devices
                    # Don't skip zone 0 if it has legitimate devices and a zone structure exists
                    if zones:
                        # When zones are defined, include all zones with devices
                        for zone_id, zone_data in zones_data.items():
                            mapping = ZoneMappingInfo(
                                ambientika_zone_id=zone_id,
                                ambientika_house_id=house_id,
                                ambientika_house_name=house_name,
                                ambientika_room_ids=list(zone_data['room_ids']),
                                ambientika_room_names=list(zone_data['room_names'])
                            )

                            self._zone_mappings[zone_id] = mapping
                            self._logger.debug(f"Created zone mapping for zone {zone_id} with {len(zone_data['devices'])} devices")

                        self._logger.debug("Processed house %s with %d zones", house_name, len(zones_data))
                    else:
                        # No zones defined - create room-based mappings only
                        for room_id, room_info in room_data.items():
                            if room_info['devices']:  # Only if room has devices
                                mapping_key = f"room_{room_id}"
                                mapping = ZoneMappingInfo(
                                    ambientika_zone_id=0,  # Default zone
                                    ambientika_house_id=house_id,
                                    ambientika_house_name=house_name,
                                    ambientika_room_ids=[room_id],
                                    ambientika_room_names=[room_info['name']]
                                )
                                self._zone_mappings[mapping_key] = mapping

                        self._logger.debug("Processed house %s with %d rooms (no zones configured)", house_name, len(room_data))

                except Exception as e:
                    self._logger.warning("Error processing house %s: %s", house_name if 'house_name' in locals() else 'Unknown', e)

        except Exception as e:
            self._logger.error("Error building zone mappings: %s", e)

    async def _build_device_mappings(self) -> None:
        """Build device mappings from Ambientika data."""
        self._device_mappings.clear()

        try:
            for device in self.hub.devices:
                zone_id = getattr(device, 'zone_index', 0)
                room_id = getattr(device, 'room_id', None)

                # Find room name from hub data
                room_name = None
                if hasattr(self.hub, '_zone_data') and self.hub._zone_data:
                    for house in self.hub._zone_data:
                        if hasattr(house, 'rooms'):
                            for room in house.rooms:
                                if room.id == room_id:
                                    room_name = room.name
                                    break

                mapping = DeviceMappingInfo(
                    ambientika_serial=device.serial_number,
                    ambientika_name=device.name,
                    ambientika_zone_id=zone_id,
                    ambientika_room_id=room_id,
                    ambientika_room_name=room_name
                )

                # Find existing Home Assistant device
                ha_device = self.device_registry.async_get_device(
                    identifiers={(DOMAIN, device.serial_number)}
                )
                if ha_device:
                    mapping.ha_device_id = ha_device.id
                    mapping.ha_area_id = ha_device.area_id

                self._device_mappings[device.serial_number] = mapping

            self._logger.debug("Built device mappings for %d devices", len(self._device_mappings))

        except Exception as e:
            self._logger.error("Error building device mappings: %s", e)

    async def async_sync_zones(self) -> dict[str, Any]:
        """Perform comprehensive zone synchronization."""
        if self._sync_in_progress:
            self._logger.warning("Sync already in progress, skipping")
            return {"status": "skipped", "reason": "sync_in_progress"}

        self._sync_in_progress = True
        sync_results = {
            "status": "success",
            "timestamp": datetime.now().isoformat(),
            "floors_created": 0,
            "areas_created": 0,
            "devices_assigned": 0,
            "conflicts": [],
            "errors": []
        }

        try:
            self._logger.info("Starting comprehensive zone synchronization...")

            # Refresh mappings
            await self._build_zone_mappings()
            await self._build_device_mappings()

            # Step 1: Create floors for houses
            floors_created = await self._sync_floors()
            sync_results["floors_created"] = floors_created

            # Step 2: Create areas for zones
            areas_created = await self._sync_areas()
            sync_results["areas_created"] = areas_created

            # Step 3: Assign devices to areas
            devices_assigned = await self._sync_device_assignments()
            sync_results["devices_assigned"] = devices_assigned

            # Update sync timestamp
            self._last_full_sync = datetime.now()
            for mapping in self._zone_mappings.values():
                mapping.last_synced = self._last_full_sync

            self._logger.info(
                "Zone sync completed: %d floors, %d areas, %d devices assigned",
                floors_created, areas_created, devices_assigned
            )

        except Exception as e:
            self._logger.error("Zone sync failed: %s", e)
            sync_results["status"] = "error"
            sync_results["errors"].append(str(e))

        finally:
            self._sync_in_progress = False

        return sync_results

    async def _sync_floors(self) -> int:
        """Sync Ambientika zones to Home Assistant floors."""
        floors_created = 0

        if not self._create_missing_floors or not self._sync_zones_to_floors:
            self._logger.info("Floor creation disabled in configuration")
            return floors_created

        try:
            # Get zone information from hub's zone_data if available
            zone_names = {}
            if hasattr(self.hub, 'zone_data') and self.hub.zone_data:
                for house_data in self.hub.zone_data:
                    zones = house_data.get('zones', [])
                    for zone in zones:
                        zone_name = zone.get('name', f'Zone {zone.get("id", "Unknown")}')
                        rooms = zone.get('rooms', [])

                        # Extract zone indices from devices in this zone
                        for room in rooms:
                            devices = room.get('devices', [])
                            for device in devices:
                                zone_index = device.get('zoneIndex')
                                if zone_index is not None:
                                    zone_names[zone_index] = zone_name
                                    break

            # Determine which zones should get floors
            all_zones = set()
            for mapping in self._zone_mappings.values():
                all_zones.add(mapping.ambientika_zone_id)

            # Check if we should create floors based on the rules:
            # 1. Multiple zones exist, OR
            # 2. Single zone with a meaningful name (not default)
            should_create_floors = False
            zones_to_create_floors = set()

            if len(all_zones) > 1:
                # Multiple zones - create floors for all zones
                should_create_floors = True
                zones_to_create_floors = all_zones
                self._logger.info("Multiple zones detected (%d), creating floors for all zones", len(all_zones))
            elif len(all_zones) == 1:
                # Single zone - only create floor if it has a meaningful name
                single_zone = list(all_zones)[0]
                if single_zone in zone_names:
                    zone_name = zone_names[single_zone]
                    # Check if it's a meaningful name (not just "Zone X" pattern)
                    if not zone_name.startswith(f'Zone {single_zone}'):
                        should_create_floors = True
                        zones_to_create_floors.add(single_zone)
                        self._logger.info("Single zone with meaningful name '%s' detected, creating floor", zone_name)
                    else:
                        self._logger.info("Single zone with default name '%s', skipping floor creation", zone_name)
                else:
                    self._logger.info("Single zone without name detected, skipping floor creation")
            else:
                self._logger.info("No zones detected, skipping floor creation")

            if not should_create_floors:
                self._logger.info("Floor creation criteria not met, skipping floor creation")
                return floors_created

            self._logger.debug(f"Creating floors for zones: {sorted(zones_to_create_floors)}")

            for zone_id in zones_to_create_floors:
                # Determine floor name
                if zone_id in zone_names:
                    floor_name = zone_names[zone_id]
                else:
                    floor_name = f"Zone {zone_id}" if zone_id != 0 else "Ground Floor"

                floor_id = f"{ZONE_PREFIX}floor_{zone_id}"

                # Check if floor already exists (by name to avoid duplicates)
                existing_floor = None
                for floor in self.floor_registry.floors.values():
                    if floor.name.lower() == floor_name.lower():
                        existing_floor = floor
                        break

                if not existing_floor:
                    # Create new floor
                    try:
                        new_floor = self.floor_registry.async_create(
                            name=floor_name
                            # Note: floor_id parameter was removed in newer Home Assistant versions
                        )
                        floors_created += 1
                        self._logger.info("Created floor '%s' for zone %s", floor_name, zone_id)

                        # Update zone mappings with the actual floor_id from created floor
                        actual_floor_id = new_floor.floor_id
                        for mapping in self._zone_mappings.values():
                            if mapping.ambientika_zone_id == zone_id:
                                mapping.ha_floor_id = actual_floor_id

                    except Exception as e:
                        self._logger.error("Failed to create floor '%s' for zone %s: %s", floor_name, zone_id, e)
                else:
                    self._logger.info("Using existing floor '%s' for zone %s", floor_name, zone_id)
                    # Update mapping with existing floor
                    for mapping in self._zone_mappings.values():
                        if mapping.ambientika_zone_id == zone_id:
                            mapping.ha_floor_id = existing_floor.floor_id

        except Exception as e:
            self._logger.error("Error syncing floors: %s", e)

        return floors_created

    async def _sync_areas(self) -> int:
        """Sync Ambientika zones/rooms to Home Assistant areas."""
        areas_created = 0

        if not self._create_missing_areas or not self._sync_rooms_to_areas:
            self._logger.info("Area creation disabled in configuration")
            return areas_created

        try:
            # Get zone names from hub data for better area naming
            zone_names = {}
            if hasattr(self.hub, 'zone_data') and self.hub.zone_data:
                for house_data in self.hub.zone_data:
                    zones = house_data.get('zones', [])
                    for zone in zones:
                        zone_name = zone.get('name', f'Zone {zone.get("id", "Unknown")}')
                        rooms = zone.get('rooms', [])

                        # Extract zone indices from devices in this zone
                        for room in rooms:
                            devices = room.get('devices', [])
                            for device in devices:
                                zone_index = device.get('zoneIndex')
                                if zone_index is not None:
                                    zone_names[zone_index] = zone_name
                                    break

            for zone_key, mapping in self._zone_mappings.items():
                # Determine if this is a room-based mapping or zone-based mapping
                if isinstance(zone_key, str) and zone_key.startswith('room_'):
                    # This is a room-based mapping (zone 0 - no zones configured)
                    room_name = mapping.ambientika_room_names[0] if mapping.ambientika_room_names else f"Room_{zone_key}"
                    area_name = room_name
                    area_id = f"{ZONE_PREFIX}room_{mapping.ambientika_room_ids[0]}"
                    mapping_type = "room"
                else:
                    # This is a zone-based mapping (real zones configured)
                    zone_id = mapping.ambientika_zone_id

                    # Use zone name from API if available, otherwise create descriptive name
                    if zone_id in zone_names:
                        zone_display_name = zone_names[zone_id]
                    else:
                        zone_display_name = f"Zone {zone_id}" if zone_id != 0 else "Ground Floor"

                    # Create area name based on zone and rooms
                    if mapping.ambientika_room_names:
                        area_name = f"{zone_display_name} - {', '.join(sorted(mapping.ambientika_room_names))}"
                    else:
                        area_name = zone_display_name

                    area_id = f"{ZONE_PREFIX}{zone_id}"
                    mapping_type = "zone"

                # Check if area already exists (by ID or name)
                existing_area = None
                for area in self.area_registry.areas.values():
                    if area.id == area_id:
                        existing_area = area
                        break
                    # Also check for similar names (case-insensitive)
                    if area.name.lower() == area_name.lower():
                        existing_area = area
                        break

                if not existing_area:
                    # Create new area
                    try:
                        # Assign floor_id if we have zones and sync_zones_to_floors is enabled
                        # For zone-based mappings, always try to assign floor if available
                        floor_id_to_assign = None
                        if mapping_type == "zone" and self._sync_zones_to_floors and mapping.ha_floor_id:
                            floor_id_to_assign = mapping.ha_floor_id

                        new_area = self.area_registry.async_create(
                            name=area_name,
                            floor_id=floor_id_to_assign
                        )
                        areas_created += 1
                        mapping.ha_area_ids = [new_area.id]

                        if mapping_type == "room":
                            self._logger.info("Created area '%s' for room %s (no floor assignment)", area_name, mapping.ambientika_room_ids[0])
                        else:
                            floor_info = f" on floor {floor_id_to_assign}" if floor_id_to_assign else " (no floor)"
                            self._logger.info("Created area '%s' for zone %s%s", area_name, mapping.ambientika_zone_id, floor_info)

                    except Exception as e:
                        self._logger.error("Failed to create area for %s: %s", zone_key, e)
                else:
                    # Update mapping with existing area
                    mapping.ha_area_ids = [existing_area.id]

                    # Update floor assignment if needed (only for zone-based mappings and if sync_zones_to_floors is enabled)
                    if (mapping_type == "zone" and self._sync_zones_to_floors and
                        existing_area.floor_id != mapping.ha_floor_id and mapping.ha_floor_id):
                        try:
                            self.area_registry.async_update(
                                existing_area.id,
                                floor_id=mapping.ha_floor_id
                            )
                            self._logger.info("Updated area '%s' floor assignment to %s", area_name, mapping.ha_floor_id)
                        except Exception as e:
                            self._logger.error("Failed to update area floor: %s", e)

        except Exception as e:
            self._logger.error("Error syncing areas: %s", e)

        return areas_created

    async def _sync_device_assignments(self) -> int:
        """Sync device assignments to areas."""
        devices_assigned = 0

        try:
            for serial, device_mapping in self._device_mappings.items():
                if not device_mapping.ha_device_id:
                    continue

                # Find the area for this device's zone or room
                zone_mapping = None

                # First try to find by zone ID
                if device_mapping.ambientika_zone_id != 0:
                    zone_mapping = self._zone_mappings.get(device_mapping.ambientika_zone_id)
                else:
                    # For zone 0 devices, find by room
                    if device_mapping.ambientika_room_id:
                        room_key = f"room_{device_mapping.ambientika_room_id}"
                        zone_mapping = self._zone_mappings.get(room_key)

                if not zone_mapping or not zone_mapping.ha_area_ids:
                    self._logger.debug(
                        "No area mapping found for device %s (zone: %s, room: %s)",
                        device_mapping.ambientika_name,
                        device_mapping.ambientika_zone_id,
                        device_mapping.ambientika_room_id
                    )
                    continue

                target_area_id = zone_mapping.ha_area_ids[0]

                # Check if device needs assignment
                if device_mapping.ha_area_id != target_area_id:
                    try:
                        self.device_registry.async_update_device(
                            device_mapping.ha_device_id,
                            area_id=target_area_id
                        )
                        device_mapping.ha_area_id = target_area_id
                        device_mapping.last_synced = datetime.now()
                        devices_assigned += 1

                        if device_mapping.ambientika_zone_id != 0:
                            assignment_info = f"zone {device_mapping.ambientika_zone_id}"
                        else:
                            assignment_info = f"room {device_mapping.ambientika_room_name or device_mapping.ambientika_room_id}"

                        self._logger.info(
                            "Assigned device '%s' to area %s (%s)",
                            device_mapping.ambientika_name,
                            target_area_id,
                            assignment_info
                        )

                    except Exception as e:
                        self._logger.error(
                            "Failed to assign device %s to area: %s",
                            device_mapping.ambientika_name, e
                        )

        except Exception as e:
            self._logger.error("Error syncing device assignments: %s", e)

        return devices_assigned

    async def _periodic_sync(self, now: datetime) -> None:
        """Perform periodic sync check."""
        try:
            if self._sync_in_progress:
                return

            # Check if we need to sync (e.g., if hub data has changed)
            needs_sync = False

            # Simple check: see if we have new devices
            current_device_count = len(self.hub.devices)
            mapped_device_count = len(self._device_mappings)

            if current_device_count != mapped_device_count:
                needs_sync = True
                self._logger.info("Device count changed, triggering sync")

            # Check if it's been a while since last sync
            if self._last_full_sync:
                time_since_sync = now - self._last_full_sync
                if time_since_sync > timedelta(hours=24):
                    needs_sync = True
                    self._logger.info("Daily sync interval reached")
            else:
                needs_sync = True

            if needs_sync:
                await self.async_sync_zones()

        except Exception as e:
            self._logger.error("Error in periodic sync: %s", e)

    def get_sync_status(self) -> dict[str, Any]:
        """Get current sync status."""
        return {
            "auto_sync_enabled": self._auto_sync_enabled,
            "sync_in_progress": self._sync_in_progress,
            "last_full_sync": self._last_full_sync.isoformat() if self._last_full_sync else None,
            "zone_mappings_count": len(self._zone_mappings),
            "device_mappings_count": len(self._device_mappings),
            "mapped_zones": [
                {
                    "zone_id": mapping.ambientika_zone_id,
                    "house_name": mapping.ambientika_house_name,
                    "room_names": mapping.ambientika_room_names,
                    "ha_floor_id": mapping.ha_floor_id,
                    "ha_area_ids": mapping.ha_area_ids,
                    "last_synced": mapping.last_synced.isoformat() if mapping.last_synced else None
                }
                for mapping in self._zone_mappings.values()
            ]
        }


class ZoneSyncSensor(CoordinatorEntity, SensorEntity):
    """Sensor that provides zone synchronization status and controls."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_has_entity_name = False
    _attr_name = "Zone Synchronization"
    _attr_icon = "mdi:sync"
    _attr_should_poll = False

    def __init__(self, coordinator: AmbientikaHub, config: dict[str, Any], zone_sync: AmbientikaZoneSync) -> None:
        """Initialize the zone sync sensor."""
        super().__init__(coordinator)
        self._config = config
        self._zone_sync = zone_sync
        self._attr_unique_id = f"{DOMAIN}_zone_sync_status"

    @property
    def device_info(self):
        """Return device info for the integration."""
        return {
            "identifiers": {(DOMAIN, "zone_sync")},
            "name": "Ambientika Zone Synchronization",
            "manufacturer": "SUEDWIND",
            "model": "Zone Sync Manager",
        }

    @property
    def state(self) -> str:
        """Return the state of the zone sync sensor."""
        if self._zone_sync._sync_in_progress:
            return "syncing"
        elif self._zone_sync._last_full_sync:
            return "synchronized"
        else:
            return "pending"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return comprehensive zone sync attributes."""
        try:
            status = self._zone_sync.get_sync_status()

            # Add summary statistics
            ha_areas = len(self._zone_sync.area_registry.areas)
            ha_floors = len(self._zone_sync.floor_registry.floors)

            return {
                **status,
                "home_assistant_areas": ha_areas,
                "home_assistant_floors": ha_floors,
                "sync_interval_minutes": SYNC_INTERVAL.total_seconds() / 60,
                "configuration": {
                    "auto_sync_enabled": self._zone_sync._auto_sync_enabled,
                    "create_missing_areas": self._zone_sync._create_missing_areas,
                    "create_missing_floors": self._zone_sync._create_missing_floors
                }
            }

        except Exception as e:
            LOGGER.error(f"Error generating zone sync attributes: {e}")
            return {"error": str(e), "error_type": "attribute_generation_failed"}
