"""Binary sensor platform for ambientika."""

from __future__ import annotations

from ambientika_py import Device, DeviceStatus
from returns.result import Failure, Success

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, LOGGER
from .hub import AmbientikaHub


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Create the `binary_sensor` entities for each device."""
    hub: AmbientikaHub = hass.data[DOMAIN][entry.entry_id]

    # TODO: this could be simplified with ENTITY_DESCTIPTIONS, but requires event subscription
    # https://github.com/DeebotUniverse/Deebot-4-Home-Assistant/blob/dev/custom_components/deebot/sensor.py#L79
    async_add_entities(HumidityAlarmBinarySensor(hub, device) for device in hub.devices)
    async_add_entities(NightAlarmBinarySensor(hub, device) for device in hub.devices)


class BinarySensorBase(CoordinatorEntity, BinarySensorEntity):
    """Base representation of an Ambientika Binary Sensor."""

    _attr_should_poll = False  # Coordinator handles updates

    def __init__(self, coordinator: AmbientikaHub, device: Device) -> None:
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
    def device_status(self):
        """Get the current device status from coordinator data."""
        if not self.coordinator.data:
            return None

        # Find our device in the coordinator's current data
        for device in self.coordinator.data:
            if device.serial_number == self._serial:
                # Get the current status without making an API call
                return device.current_status if hasattr(device, 'current_status') else None
        return None


class HumidityAlarmBinarySensor(BinarySensorBase):
    """Humidity Alarm Binary Sensor."""

    _attr_has_entity_name = True
    _attr_translation_key = "humidity_alarm"
    _attr_icon = "mdi:alarm-light"

    def __init__(self, coordinator: AmbientikaHub, device: Device) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, device)
        self._attr_unique_id = f"{self._serial}_humidity_alarm"
        LOGGER.debug(f"Creating HumidityAlarmBinarySensor: {self._device.name}")

    @property
    def is_on(self) -> bool | None:
        """Return true if the binary_sensor is on."""
        if status := self.device_status:
            humidity_alarm_value = status.get("humidity_alarm")
            if humidity_alarm_value is not None:
                return humidity_alarm_value is True
        return False  # Default to False (Inactive)


class NightAlarmBinarySensor(BinarySensorBase):
    """Night Alarm Binary Sensor."""

    _attr_has_entity_name = True
    _attr_translation_key = "night_alarm"
    _attr_icon = "mdi:alarm-light"

    def __init__(self, coordinator: AmbientikaHub, device: Device) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, device)
        self._attr_unique_id = f"{self._serial}_night_alarm"
        LOGGER.debug(f"Creating NightAlarmBinarySensor: {self._device.name}")

    @property
    def is_on(self) -> bool | None:
        """Return true if the binary_sensor is on."""
        if status := self.device_status:
            night_alarm_value = status.get("night_alarm")
            if night_alarm_value is not None:
                return night_alarm_value is True
        return False  # Default to False (Inactive)
