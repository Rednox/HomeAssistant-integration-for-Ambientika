"""Diagnostic sensor platform for ambientika zone configuration.

This module provides sensors that display the current zone configuration
for Ambientika devices, showing master/slave relationships and zone assignments.
"""

from __future__ import annotations

from ambientika_py import Device
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import Entity, EntityCategory
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, LOGGER
from .hub import AmbientikaHub


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Create diagnostic sensors for zone configuration."""
    hub: AmbientikaHub = hass.data[DOMAIN][entry.entry_id]

    # Add zone configuration sensors for each device
    diagnostic_sensors = []

    for device in hub.devices:
        diagnostic_sensors.extend([
            DeviceRoleSensor(hub, device),
            DeviceZoneIndexSensor(hub, device),
            DeviceConfigurationSensor(hub, device),
            ZoneMasterDeviceNameSensor(hub, device)
        ])

    # Add zone summary sensors (one per house)
    houses_processed = set()
    for device in hub.devices:
        # Get house information from coordinator data
        house_id = getattr(device, 'house_id', None)
        if house_id and house_id not in houses_processed:
            diagnostic_sensors.append(ZoneConfigurationSummarySensor(hub, house_id))
            houses_processed.add(house_id)

    async_add_entities(diagnostic_sensors)


class DiagnosticSensorBase(CoordinatorEntity, Entity):
    """Base class for diagnostic sensors."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_should_poll = False

    def __init__(self, coordinator: AmbientikaHub, device: Device) -> None:
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

    def __init__(self, coordinator: AmbientikaHub, device: Device) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, device)
        self._attr_unique_id = f"{self._serial}_device_role"

    @property
    def state(self) -> str | None:
        """Return the device role."""
        try:
            role = getattr(self._device, 'role', None)
            if role:
                # Capitalize and format the role nicely
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

    def __init__(self, coordinator: AmbientikaHub, device: Device) -> None:
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

    def __init__(self, coordinator: AmbientikaHub, device: Device) -> None:
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

                if 'master' in role.lower():
                    zones[zone_index]['master'] = device_info
                else:
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


class ZoneMasterDeviceNameSensor(DiagnosticSensorBase):
    """Diagnostic sensor for displaying the name of the master device in the zone."""

    _attr_has_entity_name = True
    _attr_name = "Zone Master Device"
    _attr_icon = "mdi:account-supervisor"

    def __init__(self, coordinator: AmbientikaHub, device: Device) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, device)
        self._attr_unique_id = f"{self._serial}_zone_master_device_name"

    @property
    def state(self) -> str | None:
        """State of the sensor - returns the name of the master device in the same zone."""
        if not self.coordinator.data:
            return None

        # Get this device's zone
        this_device_zone = None
        for device in self.coordinator.data:
            if device.serial_number == self._serial:
                this_device_zone = getattr(device, 'zone_index', 0)
                break

        if this_device_zone is None:
            return None

        # Find the master device in the same zone
        for device in self.coordinator.data:
            device_zone = getattr(device, 'zone_index', 0)
            device_role = getattr(device, 'role', '').lower()

            if device_zone == this_device_zone and device_role == 'master':
                return device.name

        return "No master device found"

    @property
    def extra_state_attributes(self) -> dict:
        """Return additional state attributes."""
        if not self.coordinator.data:
            return {}

        # Get this device's zone
        this_device_zone = None
        this_device_zone_name = None
        zone_device_count = 0
        master_device_serial = None

        for device in self.coordinator.data:
            if device.serial_number == self._serial:
                this_device_zone = getattr(device, 'zone_index', 0)
                break

        if this_device_zone is not None:
            # Count devices in this zone and find master
            for device in self.coordinator.data:
                device_zone = getattr(device, 'zone_index', 0)
                if device_zone == this_device_zone:
                    zone_device_count += 1
                    device_role = getattr(device, 'role', '').lower()
                    if device_role == 'master':
                        master_device_serial = device.serial_number

            # Generate zone name
            if this_device_zone == 0:
                this_device_zone_name = "Default Zone"
            else:
                this_device_zone_name = f"Zone {this_device_zone}"

        return {
            "zone_index": this_device_zone,
            "zone_name": this_device_zone_name,
            "zone_device_count": zone_device_count,
            "master_device_serial": master_device_serial,
            "current_device_role": getattr(self._device, 'role', 'unknown').lower(),
        }
