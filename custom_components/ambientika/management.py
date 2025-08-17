"""Ambientika Management component.

This module provides management and diagnostic sensors for the Ambientika integration,
including zone configuration diagnostics and synchronization settings.
"""

from __future__ import annotations

from typing import Any
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import Entity, EntityCategory
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.components.sensor import SensorEntity

from .const import DOMAIN, LOGGER
from .hub import AmbientikaHub


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up Ambientika Management sensors."""
    hub: AmbientikaHub = hass.data[DOMAIN][entry.entry_id]

    # Add management sensors
    management_sensors = [
        AmbientikaManagementSensor(hub, entry.data),
        ZoneManagementSensor(hub, entry.data)
    ]

    # Add diagnostic sensors for each device
    for device in hub.devices:
        management_sensors.extend([
            DeviceRoleSensor(hub, device),
            DeviceZoneIndexSensor(hub, device),
            DeviceConfigurationSensor(hub, device)
        ])

    # Add zone summary sensors (one per house)
    houses_processed = set()
    for device in hub.devices:
        house_id = getattr(device, 'house_id', None)
        if house_id and house_id not in houses_processed:
            management_sensors.append(ZoneConfigurationSummarySensor(hub, house_id))
            houses_processed.add(house_id)

    async_add_entities(management_sensors)


class AmbientikaManagementSensor(CoordinatorEntity, Entity):
    """Main management sensor for Ambientika integration settings."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_has_entity_name = False
    _attr_name = "Sync Management"
    _attr_icon = "mdi:cog-outline"
    _attr_should_poll = False

    def __init__(self, coordinator: AmbientikaHub, config: dict[str, Any]) -> None:
        """Initialize the management sensor."""
        super().__init__(coordinator)
        self._config = config
        self._attr_unique_id = f"{DOMAIN}_management"

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
    def state(self) -> str:
        """Return the state of the management sensor."""
        return "active"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return management configuration attributes."""
        try:
            # Get zone sync settings from config
            sync_zones_to_floors = self._config.get("sync_zones_to_floors", True)
            sync_rooms_to_areas = self._config.get("sync_rooms_to_areas", True)

            # Get device counts
            device_count = len(self.coordinator.data) if self.coordinator.data else 0

            # Analyze zones
            zones_info = self._analyze_zones()

            return {
                "device_count": device_count,
                "zone_count": zones_info.get("zone_count", 0),
                "room_count": zones_info.get("room_count", 0),
                "has_real_zones": zones_info.get("has_real_zones", False),
                "sync_zones_to_floors": sync_zones_to_floors,
                "sync_rooms_to_areas": sync_rooms_to_areas,
                "integration_mode": "zone_based" if zones_info.get("has_real_zones") else "room_based",
                "sync_controls": {
                    "zones_to_floors_switch": f"switch.{DOMAIN}_sync_zones_to_floors",
                    "rooms_to_areas_switch": f"switch.{DOMAIN}_sync_rooms_to_areas",
                    "description": "Use the toggle switches to control synchronization settings"
                },
                "configuration": {
                    "auto_sync_enabled": self._config.get("auto_sync_zones", True),
                    "create_missing_areas": self._config.get("create_missing_areas", True),
                    "create_missing_floors": self._config.get("create_missing_floors", True),
                }
            }

        except Exception as e:
            LOGGER.error(f"Error generating management attributes: {e}")
            return {"error": str(e), "error_type": "attribute_generation_failed"}

    def _analyze_zones(self) -> dict[str, Any]:
        """Analyze zone configuration."""
        if not self.coordinator.data:
            return {"zone_count": 0, "room_count": 0, "has_real_zones": False}

        zones = set()
        rooms = set()
        has_real_zones = False

        for device in self.coordinator.data:
            try:
                zone_index = getattr(device, 'zone_index', 0)
                room_id = getattr(device, 'room_id', None)

                zones.add(zone_index)
                if room_id:
                    rooms.add(room_id)

                if zone_index != 0:
                    has_real_zones = True

            except Exception as e:
                LOGGER.warning(f"Error analyzing device {device.serial_number}: {e}")

        return {
            "zone_count": len(zones),
            "room_count": len(rooms),
            "has_real_zones": has_real_zones
        }


class DiagnosticSensorBase(CoordinatorEntity, Entity):
    """Base class for diagnostic sensors."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_should_poll = False

    def __init__(self, coordinator: AmbientikaHub, device) -> None:
        """Initialize the diagnostic sensor."""
        super().__init__(coordinator)
        self._device = device
        self._serial = device.serial_number

    @property
    def device_info(self):
        """Return information to link this entity with the correct device."""
        return {
            "identifiers": {(DOMAIN, self._serial)},
            "name": self._device.name,
            "manufacturer": "SUEDWIND",
            "model": "Ambientika",
            "serial_number": self._serial,
        }

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        if not self.coordinator.data:
            return False
        for device in self.coordinator.data:
            if device.serial_number == self._serial:
                return True
        return False


class DeviceRoleSensor(DiagnosticSensorBase):
    """Sensor showing the device role (Master/Slave)."""

    _attr_has_entity_name = True
    _attr_name = "Device Role"
    _attr_icon = "mdi:account-supervisor"

    def __init__(self, coordinator: AmbientikaHub, device) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, device)
        self._attr_unique_id = f"{self._serial}_device_role"

    @property
    def state(self) -> str | None:
        """Return the device role."""
        try:
            role = getattr(self._device, 'role', None)
            if role:
                return role.title()
            return "Unknown"
        except Exception as e:
            LOGGER.error(f"Error getting device role for {self._serial}: {e}")
            return None

    @property
    def extra_state_attributes(self) -> dict:
        """Return additional state attributes."""
        return {
            "device_serial": self._serial,
            "device_name": self._device.name,
            "raw_role": getattr(self._device, 'role', None),
        }


class DeviceZoneIndexSensor(DiagnosticSensorBase):
    """Sensor showing the device zone index."""

    _attr_has_entity_name = True
    _attr_name = "Zone Index"
    _attr_icon = "mdi:home-group"

    def __init__(self, coordinator: AmbientikaHub, device) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, device)
        self._attr_unique_id = f"{self._serial}_zone_index"

    @property
    def state(self) -> int | None:
        """Return the zone index."""
        try:
            zone_index = getattr(self._device, 'zone_index', None)
            return zone_index if zone_index is not None else 0
        except Exception as e:
            LOGGER.error(f"Error getting zone index for {self._serial}: {e}")
            return None

    @property
    def extra_state_attributes(self) -> dict:
        """Return additional state attributes."""
        return {
            "device_serial": self._serial,
            "device_name": self._device.name,
            "room_id": getattr(self._device, 'room_id', None),
        }


class DeviceConfigurationSensor(DiagnosticSensorBase):
    """Comprehensive sensor showing device configuration details."""

    _attr_has_entity_name = True
    _attr_name = "Configuration"
    _attr_icon = "mdi:cog-outline"

    def __init__(self, coordinator: AmbientikaHub, device) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, device)
        self._attr_unique_id = f"{self._serial}_configuration"

    @property
    def state(self) -> str:
        """Return a summary of the device configuration."""
        try:
            role = getattr(self._device, 'role', 'Unknown')
            zone_index = getattr(self._device, 'zone_index', 0)
            return f"Zone {zone_index} - {role.title()}"
        except Exception as e:
            LOGGER.error(f"Error getting configuration for {self._serial}: {e}")
            return "Configuration Error"

    @property
    def extra_state_attributes(self) -> dict:
        """Return detailed configuration attributes."""
        try:
            return {
                "device_serial": self._serial,
                "device_name": self._device.name,
                "device_id": getattr(self._device, 'id', None),
                "device_type": getattr(self._device, 'device_type', None),
                "role": getattr(self._device, 'role', None),
                "zone_index": getattr(self._device, 'zone_index', None),
                "room_id": getattr(self._device, 'room_id', None),
                "user_id": getattr(self._device, 'user_id', None),
                "installation": getattr(self._device, 'installation', None),
            }
        except Exception as e:
            LOGGER.error(f"Error getting configuration attributes for {self._serial}: {e}")
            return {}


class ZoneConfigurationSummarySensor(CoordinatorEntity, Entity):
    """Sensor providing a summary of all zone configurations."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_has_entity_name = False
    _attr_name = "Zone Configuration Summary"
    _attr_icon = "mdi:home-group"
    _attr_should_poll = False

    def __init__(self, coordinator: AmbientikaHub, house_id: int) -> None:
        """Initialize the zone summary sensor."""
        super().__init__(coordinator)
        self._house_id = house_id
        self._attr_unique_id = f"ambientika_house_{house_id}_zone_summary"

    @property
    def device_info(self):
        """Return house-level device info."""
        return {
            "identifiers": {(DOMAIN, f"house_{self._house_id}")},
            "name": f"Ambientika House {self._house_id}",
            "manufacturer": "SUEDWIND",
            "model": "Ambientika System",
        }

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        return self.coordinator.data is not None

    @property
    def state(self) -> str:
        """Return zone summary state."""
        if not self.coordinator.data:
            return "No Data"

        try:
            zones = self._analyze_zones()
            if not zones:
                return "No Zones Configured"

            zone_count = len(zones)
            device_count = sum(len(devices) for devices in zones.values())
            return f"{zone_count} Zones, {device_count} Devices"
        except Exception as e:
            LOGGER.error(f"Error calculating zone summary: {e}")
            return "Error"

    def _analyze_zones(self) -> dict:
        """Analyze devices and group by zones."""
        zones = {}

        for device in self.coordinator.data:
            try:
                zone_index = getattr(device, 'zone_index', 0)
                role = getattr(device, 'role', 'Unknown')

                if zone_index not in zones:
                    zones[zone_index] = {
                        'devices': [],
                        'master': None,
                        'slaves': []
                    }

                device_info = {
                    'name': device.name,
                    'serial': device.serial_number,
                    'role': role
                }

                zones[zone_index]['devices'].append(device_info)

                if role.lower() == 'master':
                    zones[zone_index]['master'] = device_info
                elif role.lower() in ['slaveoppositemaster', 'slaveequalmaster']:
                    zones[zone_index]['slaves'].append(device_info)

            except Exception as e:
                LOGGER.warning(f"Error processing device {device.serial_number}: {e}")
                continue

        return zones

    @property
    def extra_state_attributes(self) -> dict:
        """Return detailed zone configuration."""
        if not self.coordinator.data:
            return {}

        try:
            zones = self._analyze_zones()

            attributes = {
                "zone_count": len(zones),
                "total_devices": len(self.coordinator.data),
                "zones": {}
            }

            for zone_index, zone_data in zones.items():
                attributes["zones"][f"zone_{zone_index}"] = {
                    "device_count": len(zone_data['devices']),
                    "master_device": zone_data['master']['name'] if zone_data['master'] else None,
                    "master_serial": zone_data['master']['serial'] if zone_data['master'] else None,
                    "slave_count": len(zone_data['slaves']),
                    "slave_devices": [slave['name'] for slave in zone_data['slaves']],
                    "all_devices": [dev['name'] for dev in zone_data['devices']]
                }

            return attributes

        except Exception as e:
            LOGGER.error(f"Error generating zone attributes: {e}")
            return {"error": str(e)}


class ZoneManagementSensor(CoordinatorEntity, SensorEntity):
    """Sensor that provides comprehensive zone management information."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_has_entity_name = False
    _attr_name = "Zone Management"
    _attr_icon = "mdi:home-group-plus"
    _attr_should_poll = False

    def __init__(self, coordinator: AmbientikaHub, config: dict[str, Any]) -> None:
        """Initialize the zone management sensor."""
        super().__init__(coordinator)
        self._config = config
        self._attr_unique_id = f"{DOMAIN}_zone_management"
        self._zone_data = None

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
    def state(self) -> str:
        """Return the state of the zone management sensor."""
        if not self.coordinator.data:
            return "No Connection"

        try:
            zone_info = self._analyze_zone_configuration()
            return f"Active - {zone_info['total_zones']} Zones"
        except Exception as e:
            LOGGER.error(f"Error in zone management state: {e}")
            return "Error"

    async def async_update_zone_data(self) -> None:
        """Fetch detailed zone data from the API."""
        try:
            # Use the existing client from the hub
            if hasattr(self.coordinator, 'client') and self.coordinator.client:
                api_client = self.coordinator.client._api_client
                if api_client:
                    # Fetch houses with complete zone information
                    houses_result = await api_client.houses()
                    if houses_result:
                        self._zone_data = houses_result.unwrap() if hasattr(houses_result, 'unwrap') else houses_result
                        LOGGER.debug(f"Fetched zone data: {len(self._zone_data) if self._zone_data else 0} houses")
        except Exception as e:
            LOGGER.error(f"Error fetching zone data: {e}")
            self._zone_data = None

    def _analyze_zone_configuration(self) -> dict[str, Any]:
        """Analyze the current zone configuration."""
        if not self.coordinator.data:
            return {"total_zones": 0, "total_devices": 0, "zones": {}}

        zones = {}
        device_count = 0

        # Group devices by zone
        for device in self.coordinator.data:
            try:
                zone_index = getattr(device, 'zone_index', 0)
                role = getattr(device, 'role', 'Unknown')
                room_id = getattr(device, 'room_id', None)

                if zone_index not in zones:
                    zones[zone_index] = {
                        'master': None,
                        'slaves': [],
                        'rooms': set(),
                        'device_count': 0
                    }

                device_info = {
                    'name': device.name,
                    'serial': device.serial_number,
                    'role': role,
                    'room_id': room_id,
                    'device_type': getattr(device, 'device_type', 'Unknown'),
                    'installation': getattr(device, 'installation', 'Unknown')
                }

                zones[zone_index]['device_count'] += 1
                zones[zone_index]['rooms'].add(room_id)
                device_count += 1

                if role.lower() == 'master':
                    zones[zone_index]['master'] = device_info
                elif role.lower() in ['slave', 'slaveequalmaster']:
                    zones[zone_index]['slaves'].append(device_info)

            except Exception as e:
                LOGGER.warning(f"Error processing device {device.serial_number}: {e}")
                continue

        # Convert rooms set to list for JSON serialization
        for zone in zones.values():
            zone['rooms'] = list(zone['rooms'])

        return {
            "total_zones": len(zones),
            "total_devices": device_count,
            "zones": zones
        }

    def _get_house_zone_info(self) -> dict[str, Any]:
        """Get zone information from house data if available."""
        if not self._zone_data:
            return {}

        house_zones = {}
        try:
            for house in self._zone_data:
                if hasattr(house, 'zones') and house.zones:
                    house_zones[house.name] = {
                        'house_id': house.id,
                        'has_zones': getattr(house, 'has_zones', False),
                        'zones': house.zones,
                        'address': getattr(house, 'address', ''),
                        'room_count': len(getattr(house, 'rooms', []))
                    }
        except Exception as e:
            LOGGER.error(f"Error processing house zone info: {e}")

        return house_zones

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return comprehensive zone management attributes."""
        try:
            zone_config = self._analyze_zone_configuration()
            house_zones = self._get_house_zone_info()

            # Build master-slave relationships
            relationships = {}
            for zone_id, zone_data in zone_config["zones"].items():
                master = zone_data.get('master')
                slaves = zone_data.get('slaves', [])

                if master:
                    relationships[f"zone_{zone_id}"] = {
                        "master_device": master['name'],
                        "master_serial": master['serial'],
                        "slave_count": len(slaves),
                        "slaves": [slave['name'] for slave in slaves],
                        "room_coverage": len(zone_data.get('rooms', [])),
                        "total_devices": zone_data.get('device_count', 0)
                    }

            # Configuration recommendations
            recommendations = []
            for zone_id, zone_data in zone_config["zones"].items():
                if not zone_data.get('master'):
                    recommendations.append(f"Zone {zone_id}: No master device configured")
                elif len(zone_data.get('slaves', [])) == 0:
                    recommendations.append(f"Zone {zone_id}: No slave devices - check if zone configuration is complete")

            return {
                "configuration_summary": zone_config,
                "house_zone_info": house_zones,
                "master_slave_relationships": relationships,
                "configuration_status": "OK" if not recommendations else "Issues Found",
                "recommendations": recommendations,
                "last_updated": self.coordinator.last_update_time.isoformat() if self.coordinator.last_update_time else None,
                "api_integration": {
                    "total_api_devices": len(self.coordinator.data) if self.coordinator.data else 0,
                    "connection_status": "Connected" if self.coordinator.data else "Disconnected",
                    "data_source": "Ambientika Cloud API"
                }
            }

        except Exception as e:
            LOGGER.error(f"Error generating zone management attributes: {e}")
            return {"error": str(e), "error_type": "attribute_generation_failed"}

    async def async_added_to_hass(self) -> None:
        """When entity is added to hass."""
        await super().async_added_to_hass()
        # Fetch initial zone data
        await self.async_update_zone_data()

    async def async_update(self) -> None:
        """Update the zone management data."""
        await self.async_update_zone_data()
        await super().async_update()
