"""Sensor platform for ambientika.

References:
 - https://github.com/ludeeus/integration_blueprint/blob/main/custom_components/integration_blueprint/sensor.py
 - https://github.com/home-assistant/example-custom-config/blob/master/custom_components/detailed_hello_world_push/sensor.py
  https://github.com/DeebotUniverse/Deebot-4-Home-Assistant/blob/dev/custom_components/deebot/sensor.py

"""

from __future__ import annotations

from ambientika_py import DeviceStatus, LightSensorLevel, FanSpeed, OperatingMode, HumidityLevel

from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import Entity
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.components.sensor.const import SensorDeviceClass

from .const import DOMAIN, LOGGER, AirQuality, FilterStatus
from .hub import AmbientikaHub

# Import management sensors
try:
    from .management import async_setup_entry as setup_management_sensors
    MANAGEMENT_SENSORS_AVAILABLE = True
except ImportError:
    MANAGEMENT_SENSORS_AVAILABLE = False
    LOGGER.warning("Management sensors not available")

# Import zone synchronization
try:
    from .zone_sync import AmbientikaZoneSync
    ZONE_SYNC_AVAILABLE = True
except ImportError:
    ZONE_SYNC_AVAILABLE = False
    LOGGER.warning("Zone synchronization not available")


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Create the `sensor` entities for each device."""
    hub: AmbientikaHub = hass.data[DOMAIN][entry.entry_id]

    # Add all sensor entities for each device
    async_add_entities(
        [
            TemperatureSensor(hub, device)
            for device in hub.devices
        ]
    )
    async_add_entities(
        [
            HumiditySensor(hub, device)
            for device in hub.devices
        ]
    )
    async_add_entities(
        [
            AirQualitySensor(hub, device)
            for device in hub.devices
        ]
    )
    async_add_entities(
        [
            FilterStatusSensor(hub, device)
            for device in hub.devices
        ]
    )
    # Add select state monitoring sensors
    async_add_entities(
        [
            LightSensorLevelStateSensor(hub, device)
            for device in hub.devices
        ]
    )
    async_add_entities(
        [
            FanSpeedStateSensor(hub, device)
            for device in hub.devices
        ]
    )
    async_add_entities(
        [
            OperatingModeStateSensor(hub, device)
            for device in hub.devices
        ]
    )
    # Add humidity level state sensor
    async_add_entities(
        [
            HumidityLevelStateSensor(hub, device)
            for device in hub.devices
        ]
    )
    # NOTE: Alarm sensors are implemented as binary_sensors in binary_sensor.py
    # They should not be duplicated here as regular sensors

    # Add management sensors (includes diagnostics)
    if MANAGEMENT_SENSORS_AVAILABLE:
        try:
            LOGGER.debug("Setting up management sensors")
            await setup_management_sensors(hass, entry, async_add_entities)
        except Exception as e:
            LOGGER.error(f"Failed to setup management sensors: {e}")
    else:
        LOGGER.info("Management sensors not available")

    # Add diagnostic sensors
    try:
        from .diagnostic_sensor import async_setup_entry as setup_diagnostic_sensors
        LOGGER.debug("Setting up diagnostic sensors")
        await setup_diagnostic_sensors(hass, entry, async_add_entities)
    except Exception as e:
        LOGGER.error(f"Failed to setup diagnostic sensors: {e}")

    # Add zone synchronization
    if ZONE_SYNC_AVAILABLE:
        try:
            LOGGER.debug("Setting up zone synchronization")
            # Create zone sync manager
            zone_sync = AmbientikaZoneSync(hass, hub, entry.data)
            await zone_sync.async_initialize()

            # Store zone sync manager for use by other components
            if not hasattr(hass.data[DOMAIN], 'zone_sync'):
                hass.data[DOMAIN]['zone_sync'] = {}
            hass.data[DOMAIN]['zone_sync'][entry.entry_id] = zone_sync
        except Exception as e:
            LOGGER.error(f"Failed to setup zone synchronization: {e}")
    else:
        LOGGER.info("Zone synchronization not available")


from homeassistant.helpers.update_coordinator import CoordinatorEntity

class SensorBase(CoordinatorEntity, Entity):
    """Base representation of an Ambientika Sensor."""

    def __init__(self, coordinator: AmbientikaHub, device) -> None:
        """Initialize the sensor."""
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
        # Find our device in the coordinator's data
        for device in self.coordinator.data:
            if device.serial_number == self._serial:
                return True
        return False

    @property
    def device_status(self) -> DeviceStatus | None:
        """Get the current device status from coordinator data."""
        if not self.coordinator.data:
            return None

        # Find our device in the coordinator's current data
        for device in self.coordinator.data:
            if device.serial_number == self._serial:
                # Get the current status without making an API call
                return device.current_status if hasattr(device, 'current_status') else None
        return None


class TemperatureSensor(SensorBase):
    """Sensor for the temperature status."""

    _attr_has_entity_name = True
    _attr_translation_key = "temperature"
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_unit_of_measurement = "°C"

    def __init__(self, coordinator, device):
        """Initialize the sensor."""
        super().__init__(coordinator, device)
        self._attr_unique_id = f"{self._serial}_temperature"

    @property
    def state(self):
        """State of the sensor."""
        if status := self.device_status:
            try:
                return float(status.get("temperature", 0))
            except (ValueError, TypeError):
                LOGGER.error(
                    "Invalid temperature value for device %s: %s",
                    self._serial,
                    status.get("temperature"),
                )
                return None


class HumiditySensor(SensorBase):
    """Sensor for the humidity status."""

    _attr_has_entity_name = True
    _attr_translation_key = "humidity"
    _attr_device_class = SensorDeviceClass.HUMIDITY
    _attr_unit_of_measurement = "%"

    def __init__(self, coordinator, device):
        """Initialize the sensor."""
        super().__init__(coordinator, device)
        self._attr_unique_id = f"{self._serial}_humidity"

    @property
    def state(self):
        """State of the sensor."""
        if status := self.device_status:
            try:
                return int(status.get("humidity", 0))
            except (ValueError, TypeError):
                LOGGER.error(
                    "Invalid humidity value for device %s: %s",
                    self._serial,
                    status.get("humidity"),
                )
                return None


class AirQualitySensor(SensorBase):
    """Sensor for the Air Quality status."""

    _attr_has_entity_name = True
    _attr_translation_key = "air_quality"
    _attr_icon = "mdi:air-purifier"

    def __init__(self, coordinator, device):
        """Initialize the sensor."""
        super().__init__(coordinator, device)
        self._attr_unique_id = f"{self._serial}_air_quality"

    @property
    def state(self):
        """State of the sensor."""
        if status := self.device_status:
            air_quality = status.get("air_quality")
            if air_quality and air_quality in AirQuality.__members__:
                return AirQuality[air_quality]


class FilterStatusSensor(SensorBase):
    """Sensor for the Filter Status."""

    _attr_has_entity_name = True
    _attr_translation_key = "filter_status"
    _attr_icon = "mdi:air-filter"
    _attr_device_class = SensorDeviceClass.ENUM

    def __init__(self, coordinator, device):
        """Initialize the sensor."""
        super().__init__(coordinator, device)
        self._attr_unique_id = f"{self._serial}_filter_status"

    @property
    def state(self):
        """State of the sensor."""
        if status := self.device_status:
            filter_status = status.get("filters_status")
            if filter_status and filter_status in FilterStatus.__members__:
                return FilterStatus[filter_status]

    @property
    def options(self):
        """Return the list of available options."""
        return [name for name, _ in FilterStatus.__members__.items()]


class LightSensorLevelStateSensor(SensorBase):
    """Sensor for monitoring the current light sensor level state.
    
    ZONE-AWARE MASTER DATA CONSUMPTION:
    This sensor implements the core zone functionality by showing master device settings
    for non-master devices within the same zone. This ensures that:
    
    1. Master devices display their own light sensor level state
    2. Slave devices in the same zone display their master's light sensor level state
    3. Each zone operates independently with its own master/slave hierarchy
    
    This fulfills the requirement: "Ensure consumed master data for non master device 
    entities respect the corresponding zone if there are multiple zones configured"
    """

    _attr_has_entity_name = True
    _attr_translation_key = "light_sensor_level_state"
    _attr_icon = "mdi:lightbulb-on-outline"
    _attr_device_class = SensorDeviceClass.ENUM

    def __init__(self, coordinator, device):
        """Initialize the sensor."""
        super().__init__(coordinator, device)
        self._attr_unique_id = f"{self._serial}_light_sensor_level_state"

    def _get_master_device_status(self):
        """Get the master device status from the same zone."""
        if not self.coordinator.data:
            return None

        # Find this device's zone
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
                return device.current_status if hasattr(device, 'current_status') else None

        return None

    def _is_master_device(self):
        """Check if this device is a master device."""
        if not self.coordinator.data:
            return False

        for device in self.coordinator.data:
            if device.serial_number == self._serial:
                device_role = getattr(device, 'role', '').lower()
                return device_role == 'master'
        return False

    @property
    def state(self):
        """State of the sensor."""
        # For master devices, show their own state
        if self._is_master_device():
            status = self.device_status
        else:
            # For non-master devices, show the master device's state
            status = self._get_master_device_status()

        if status:
            try:
                light_sensor_value = status.get("light_sensor_level")
                if light_sensor_value is not None:
                    # Convert numeric value to LightSensorLevel enum
                    for level in LightSensorLevel:
                        if level.value == light_sensor_value:
                            return level.name
                    # If we can't match the value, return the raw value
                    return str(light_sensor_value)
            except (ValueError, TypeError) as e:
                LOGGER.error(
                    "Invalid light sensor level value for device %s: %s - %s",
                    self._serial,
                    status.get("light_sensor_level"),
                    str(e),
                )
        return None

    @property
    def options(self):
        """Return the list of available options."""
        return [level.name for level in LightSensorLevel if level != LightSensorLevel.NotAvailable]

    @property
    def extra_state_attributes(self) -> dict:
        """Return additional state attributes."""
        if not self.coordinator.data:
            return {}

        attributes = {}

        # Add information about whether this shows master or own values
        if self._is_master_device():
            attributes["source"] = "own_device"
            attributes["device_role"] = "master"
        else:
            attributes["source"] = "master_device"
            attributes["device_role"] = getattr(self._device, 'role', 'unknown').lower()

            # Find master device info
            this_device_zone = None
            master_device_name = None
            master_device_serial = None

            for device in self.coordinator.data:
                if device.serial_number == self._serial:
                    this_device_zone = getattr(device, 'zone_index', 0)
                    break

            if this_device_zone is not None:
                for device in self.coordinator.data:
                    device_zone = getattr(device, 'zone_index', 0)
                    device_role = getattr(device, 'role', '').lower()

                    if device_zone == this_device_zone and device_role == 'master':
                        master_device_name = device.name
                        master_device_serial = device.serial_number
                        break

            attributes["master_device_name"] = master_device_name
            attributes["master_device_serial"] = master_device_serial
            attributes["zone_index"] = this_device_zone

        return attributes


class FanSpeedStateSensor(SensorBase):
    """Sensor for monitoring the current fan speed state.
    
    ZONE-AWARE MASTER DATA CONSUMPTION:
    Implements zone-aware master data consumption like LightSensorLevelStateSensor.
    Shows master device fan speed settings for slave devices in the same zone.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "fan_speed_state"
    _attr_icon = "mdi:fan-alert"
    _attr_device_class = SensorDeviceClass.ENUM

    def __init__(self, coordinator, device):
        """Initialize the sensor."""
        super().__init__(coordinator, device)
        self._attr_unique_id = f"{self._serial}_fan_speed_state"

    def _get_master_device_status(self):
        """Get the master device status from the same zone."""
        if not self.coordinator.data:
            return None

        # Find this device's zone
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
                return device.current_status if hasattr(device, 'current_status') else None

        return None

    def _is_master_device(self):
        """Check if this device is a master device."""
        if not self.coordinator.data:
            return False

        for device in self.coordinator.data:
            if device.serial_number == self._serial:
                device_role = getattr(device, 'role', '').lower()
                return device_role == 'master'
        return False

    @property
    def state(self):
        """State of the sensor."""
        # For master devices, show their own state
        if self._is_master_device():
            status = self.device_status
        else:
            # For non-master devices, show the master device's state
            status = self._get_master_device_status()

        if status:
            try:
                fan_speed_value = status.get("fan_speed")
                if fan_speed_value is not None:
                    # Convert value to FanSpeed enum
                    for speed in FanSpeed:
                        if speed.value == fan_speed_value or speed == fan_speed_value:
                            return speed.name
                    # If we can't match the value, return the raw value
                    return str(fan_speed_value)
            except (ValueError, TypeError) as e:
                LOGGER.error(
                    "Invalid fan speed value for device %s: %s - %s",
                    self._serial,
                    status.get("fan_speed"),
                    str(e),
                )
        return None

    @property
    def options(self):
        """Return the list of available options."""
        return [speed.name for speed in FanSpeed]

    @property
    def extra_state_attributes(self) -> dict:
        """Return additional state attributes."""
        if not self.coordinator.data:
            return {}

        attributes = {}

        # Add information about whether this shows master or own values
        if self._is_master_device():
            attributes["source"] = "own_device"
            attributes["device_role"] = "master"
        else:
            attributes["source"] = "master_device"
            attributes["device_role"] = getattr(self._device, 'role', 'unknown').lower()

            # Find master device info
            this_device_zone = None
            master_device_name = None
            master_device_serial = None

            for device in self.coordinator.data:
                if device.serial_number == self._serial:
                    this_device_zone = getattr(device, 'zone_index', 0)
                    break

            if this_device_zone is not None:
                for device in self.coordinator.data:
                    device_zone = getattr(device, 'zone_index', 0)
                    device_role = getattr(device, 'role', '').lower()

                    if device_zone == this_device_zone and device_role == 'master':
                        master_device_name = device.name
                        master_device_serial = device.serial_number
                        break

            attributes["master_device_name"] = master_device_name
            attributes["master_device_serial"] = master_device_serial
            attributes["zone_index"] = this_device_zone

        return attributes


class OperatingModeStateSensor(SensorBase):
    """Sensor for monitoring the current operating mode state.
    
    ZONE-AWARE MASTER DATA CONSUMPTION:
    Implements zone-aware master data consumption like other state sensors.
    Shows master device operating mode for slave devices in the same zone.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "operating_mode_state"
    _attr_icon = "mdi:cog-outline"
    _attr_device_class = SensorDeviceClass.ENUM

    def __init__(self, coordinator, device):
        """Initialize the sensor."""
        super().__init__(coordinator, device)
        self._attr_unique_id = f"{self._serial}_operating_mode_state"

    def _get_master_device_status(self):
        """Get the master device status from the same zone."""
        if not self.coordinator.data:
            return None

        # Find this device's zone
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
                return device.current_status if hasattr(device, 'current_status') else None

        return None

    def _is_master_device(self):
        """Check if this device is a master device."""
        if not self.coordinator.data:
            return False

        for device in self.coordinator.data:
            if device.serial_number == self._serial:
                device_role = getattr(device, 'role', '').lower()
                return device_role == 'master'
        return False

    @property
    def state(self):
        """State of the sensor."""
        # For master devices, show their own state
        if self._is_master_device():
            status = self.device_status
        else:
            # For non-master devices, show the master device's state
            status = self._get_master_device_status()

        if status:
            try:
                operating_mode_value = status.get("operating_mode")
                if operating_mode_value is not None:
                    # Convert value to OperatingMode enum
                    for mode in OperatingMode:
                        if mode.value == operating_mode_value or mode == operating_mode_value:
                            return mode.name
                    # If we can't match the value, return the raw value
                    return str(operating_mode_value)
            except (ValueError, TypeError) as e:
                LOGGER.error(
                    "Invalid operating mode value for device %s: %s - %s",
                    self._serial,
                    status.get("operating_mode"),
                    str(e),
                )
        return None

    @property
    def options(self):
        """Return the list of available options."""
        return [mode.name for mode in OperatingMode]

    @property
    def extra_state_attributes(self) -> dict:
        """Return additional state attributes."""
        if not self.coordinator.data:
            return {}

        attributes = {}

        # Add information about whether this shows master or own values
        if self._is_master_device():
            attributes["source"] = "own_device"
            attributes["device_role"] = "master"
        else:
            attributes["source"] = "master_device"
            attributes["device_role"] = getattr(self._device, 'role', 'unknown').lower()

            # Find master device info
            this_device_zone = None
            master_device_name = None
            master_device_serial = None

            for device in self.coordinator.data:
                if device.serial_number == self._serial:
                    this_device_zone = getattr(device, 'zone_index', 0)
                    break

            if this_device_zone is not None:
                for device in self.coordinator.data:
                    device_zone = getattr(device, 'zone_index', 0)
                    device_role = getattr(device, 'role', '').lower()

                    if device_zone == this_device_zone and device_role == 'master':
                        master_device_name = device.name
                        master_device_serial = device.serial_number
                        break

            attributes["master_device_name"] = master_device_name
            attributes["master_device_serial"] = master_device_serial
            attributes["zone_index"] = this_device_zone

        return attributes


class HumidityLevelStateSensor(SensorBase):
    """Sensor for monitoring the current humidity level state.
    
    ZONE-AWARE MASTER DATA CONSUMPTION:
    Implements zone-aware master data consumption like other state sensors.
    Shows master device humidity level settings for slave devices in the same zone.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "humidity_level_state"
    _attr_icon = "mdi:water-percent-alert"
    _attr_device_class = SensorDeviceClass.ENUM

    def __init__(self, coordinator, device):
        """Initialize the sensor."""
        super().__init__(coordinator, device)
        self._attr_unique_id = f"{self._serial}_humidity_level_state"

    def _get_master_device_status(self):
        """Get the master device status from the same zone."""
        if not self.coordinator.data:
            return None

        # Find this device's zone
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
                return device.current_status if hasattr(device, 'current_status') else None

        return None

    def _is_master_device(self):
        """Check if this device is a master device."""
        if not self.coordinator.data:
            return False

        for device in self.coordinator.data:
            if device.serial_number == self._serial:
                device_role = getattr(device, 'role', '').lower()
                return device_role == 'master'
        return False

    @property
    def state(self):
        """State of the sensor."""
        # For master devices, show their own state
        if self._is_master_device():
            status = self.device_status
        else:
            # For non-master devices, show the master device's state
            status = self._get_master_device_status()

        if status:
            try:
                humidity_level_value = status.get("humidity_level")
                if humidity_level_value is not None:
                    # Convert value to HumidityLevel enum
                    for level in HumidityLevel:
                        if level.value == humidity_level_value or level == humidity_level_value:
                            return level.name
                    # If we can't match the value, return the raw value
                    return str(humidity_level_value)
            except (ValueError, TypeError) as e:
                LOGGER.error(
                    "Invalid humidity level value for device %s: %s - %s",
                    self._serial,
                    status.get("humidity_level"),
                    str(e),
                )
        return None

    @property
    def options(self):
        """Return the list of available options."""
        return [level.name for level in HumidityLevel]

    @property
    def extra_state_attributes(self) -> dict:
        """Return additional state attributes."""
        if not self.coordinator.data:
            return {}

        attributes = {}

        # Add information about whether this shows master or own values
        if self._is_master_device():
            attributes["source"] = "own_device"
            attributes["device_role"] = "master"
        else:
            attributes["source"] = "master_device"
            attributes["device_role"] = getattr(self._device, 'role', 'unknown').lower()

            # Find master device info
            this_device_zone = None
            master_device_name = None
            master_device_serial = None

            for device in self.coordinator.data:
                if device.serial_number == self._serial:
                    this_device_zone = getattr(device, 'zone_index', 0)
                    break

            if this_device_zone is not None:
                for device in self.coordinator.data:
                    device_zone = getattr(device, 'zone_index', 0)
                    device_role = getattr(device, 'role', '').lower()

                    if device_zone == this_device_zone and device_role == 'master':
                        master_device_name = device.name
                        master_device_serial = device.serial_number
                        break

            attributes["master_device_name"] = master_device_name
            attributes["master_device_serial"] = master_device_serial
            attributes["zone_index"] = this_device_zone

        return attributes


