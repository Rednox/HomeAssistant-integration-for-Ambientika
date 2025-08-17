"""Select platform for ambientika.

References:
 - https://developers.home-assistant.io/docs/core/entity/select
 - https://app.ambientika.eu:4521/swagger/index.html
"""

from __future__ import annotations

import asyncio
from ambientika_py import Device, DeviceMode, LightSensorLevel, FanSpeed, OperatingMode, HumidityLevel
from returns.result import Failure, Success

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, LOGGER
from .hub import AmbientikaHub


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create the `select` entities for each device."""
    hub: AmbientikaHub = hass.data[DOMAIN][entry.entry_id]

    entities = []

    # Add select entities for each device
    for device in hub.devices:
        entities.extend([
            LightSensorLevelSelect(hub, device),
            FanSpeedSelect(hub, device),
            OperatingModeSelect(hub, device),
            HumidityLevelSelect(hub, device),
        ])

    async_add_entities(entities)


class LightSensorLevelSelect(CoordinatorEntity, SelectEntity):
    """Select entity for light sensor level."""

    _attr_has_entity_name = True
    _attr_translation_key = "light_sensor_level"
    _attr_icon = "mdi:lightbulb-on"

    def __init__(self, coordinator: AmbientikaHub, device: Device) -> None:
        """Initialize the select."""
        super().__init__(coordinator)
        self._device = device
        self._serial = device.serial_number
        self._attr_unique_id = f"{self._serial}_light_sensor_level"

        # Set up the available options - exclude NotAvailable as it's not selectable
        self._attr_options = [level.name for level in LightSensorLevel if level != LightSensorLevel.NotAvailable]

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

    @property
    def current_option(self) -> str | None:
        """Return the currently selected option."""
        if status := self.device_status:
            try:
                # Get light sensor level from device status
                light_sensor_value = status.get("light_sensor_level")
                LOGGER.debug(f"LightSensorLevelSelect: Device {self._serial} has light_sensor_level value: {light_sensor_value} (type: {type(light_sensor_value)})")
                if light_sensor_value is not None:
                    # Convert numeric value to LightSensorLevel enum
                    for level in LightSensorLevel:
                        if level.value == light_sensor_value:
                            # Return the name if it's a selectable option
                            if level.name in self._attr_options:
                                LOGGER.debug(f"LightSensorLevelSelect: Device {self._serial} matched to level.name: {level.name}")
                                return level.name
                            # If it's NotAvailable, return None to indicate unknown state
                            elif level == LightSensorLevel.NotAvailable:
                                LOGGER.debug(f"LightSensorLevelSelect: Device {self._serial} is NotAvailable")
                                return None

                    # If we can't match the value, log it for debugging
                    LOGGER.warning(
                        "Unknown light sensor level value for device %s: %s",
                        self._serial,
                        light_sensor_value,
                    )
            except (ValueError, TypeError) as e:
                LOGGER.error(
                    "Invalid light sensor level value for device %s: %s - %s",
                    self._serial,
                    status.get("light_sensor_level"),
                    str(e),
                )
        return None

    async def async_select_option(self, option: str) -> None:
        """Change the select option."""
        LOGGER.debug(f"LightSensorLevelSelect.async_select_option called with option: {option} for device {self._serial}")
        try:
            # Convert the option name to the corresponding enum value
            light_level = LightSensorLevel[option]
            LOGGER.debug(f"Setting light sensor level to {option} ({light_level.value}) for device {self._serial}")

            # Get the device object from coordinator data
            device_obj = None
            for device in self.coordinator.data:
                if device.serial_number == self._serial:
                    device_obj = device
                    break

            if device_obj is None:
                LOGGER.error(f"Device {self._serial} not found in coordinator data")
                return

            # Get current device status to preserve other settings
            current_status = self.device_status
            if not current_status:
                LOGGER.error(f"Could not get current status for device {self._serial}")
                return

            # Debug: Log current status structure
            LOGGER.debug(f"Current device status for {self._serial}: {current_status}")
            LOGGER.debug(f"Current status keys: {list(current_status.keys()) if current_status else 'None'}")

            # Check what type the current values are
            if current_status:
                LOGGER.debug(f"operating_mode type: {type(current_status.get('operating_mode'))}, value: {current_status.get('operating_mode')}")
                LOGGER.debug(f"fan_speed type: {type(current_status.get('fan_speed'))}, value: {current_status.get('fan_speed')}")

            # API requires ALL parameters: operatingMode, fanSpeed, humidityLevel, lightSensorLevel
            # Get current settings and convert integers to enums if needed
            current_operating_mode = current_status.get("operating_mode", OperatingMode.Auto)
            current_fan_speed = current_status.get("fan_speed", FanSpeed.Low)
            current_humidity_level = current_status.get("humidity_level", HumidityLevel.Normal)

            # Convert integers to enums if needed
            if isinstance(current_operating_mode, int):
                current_operating_mode = OperatingMode(current_operating_mode)
            if isinstance(current_fan_speed, int):
                current_fan_speed = FanSpeed(current_fan_speed)
            if isinstance(current_humidity_level, int):
                current_humidity_level = HumidityLevel(current_humidity_level)

            # Create dictionary with ALL required parameters
            mode_dict = {
                "light_sensor_level": light_level,  # Updated parameter
                "operating_mode": current_operating_mode,  # Required
                "fan_speed": current_fan_speed,  # Required
                "humidity_level": current_humidity_level,  # Required
            }

            LOGGER.debug(f"Sending mode_dict to API: {mode_dict}")

            result = await device_obj.change_mode(mode_dict)
            LOGGER.debug(f"LightSensorLevelSelect: API change_mode result: {result} (type: {type(result)})")

            if isinstance(result, Success):
                LOGGER.debug(f"Successfully set light sensor level to {option} for device {self._serial}")

                # CRITICAL FIX: Wait for device state to propagate before refreshing
                LOGGER.debug(f"LightSensorLevelSelect: Waiting 3 seconds for device state to propagate...")
                await asyncio.sleep(3)

                # Invalidate cache and force coordinator refresh to update the UI
                LOGGER.debug(f"LightSensorLevelSelect: Invalidating cache and refreshing for device {self._serial}")
                self.coordinator.invalidate_cache()
                await self.coordinator.async_request_refresh()
                # Force immediate entity state update
                self.async_write_ha_state()
                LOGGER.debug(f"LightSensorLevelSelect: Completed cache invalidation and state update for device {self._serial}")
            elif isinstance(result, Failure):
                error_msg = str(result.failure())
                LOGGER.error(f"Failed to set light sensor level to {option} for device {self._serial}: {error_msg}")
        except KeyError as e:
            LOGGER.error(f"Invalid light sensor level option: {option}")
        except Exception as e:
            LOGGER.error(f"Exception when setting light sensor level to {option} for device {self._serial}: {e}")


class FanSpeedSelect(CoordinatorEntity, SelectEntity):
    """Select entity for fan speed."""

    _attr_has_entity_name = True
    _attr_translation_key = "fan_speed"
    _attr_icon = "mdi:fan"

    def __init__(self, coordinator: AmbientikaHub, device: Device) -> None:
        """Initialize the select."""
        super().__init__(coordinator)
        self._device = device
        self._serial = device.serial_number
        self._attr_unique_id = f"{self._serial}_fan_speed"

        # Set up the available options
        self._attr_options = [speed.name for speed in FanSpeed]

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

    @property
    def current_option(self) -> str | None:
        """Return the currently selected option."""
        if status := self.device_status:
            try:
                # Get fan speed from device status
                fan_speed_value = status.get("fan_speed")
                if fan_speed_value is not None:
                    LOGGER.debug(
                        "FanSpeedSelect: Device %s has fan_speed value: %s (type: %s)",
                        self._serial,
                        fan_speed_value,
                        type(fan_speed_value),
                    )
                    # Convert value to FanSpeed enum
                    for speed in FanSpeed:
                        if speed.value == fan_speed_value or speed == fan_speed_value:
                            LOGGER.debug(
                                "FanSpeedSelect: Device %s matched to speed.name: %s",
                                self._serial,
                                speed.name,
                            )
                            return speed.name

                    # If we can't match the value, log it for debugging
                    LOGGER.warning(
                        "Unknown fan speed value for device %s: %s",
                        self._serial,
                        fan_speed_value,
                    )
                else:
                    LOGGER.debug(
                        "FanSpeedSelect: Device %s has no fan_speed in status",
                        self._serial,
                    )
            except (ValueError, TypeError) as e:
                LOGGER.error(
                    "Invalid fan speed value for device %s: %s - %s",
                    self._serial,
                    status.get("fan_speed"),
                    str(e),
                )
        else:
            LOGGER.debug(
                "FanSpeedSelect: Device %s has no device_status",
                self._serial,
            )
        return None

    async def async_select_option(self, option: str) -> None:
        """Change the selected option."""
        LOGGER.debug(f"FanSpeedSelect.async_select_option called with option: {option} for device {self._serial}")
        try:
            # Find the corresponding FanSpeed enum
            speed = None
            for fan_speed in FanSpeed:
                if fan_speed.name == option:
                    speed = fan_speed
                    break

            if speed is None:
                LOGGER.error(
                    "Invalid fan speed option selected: %s for device %s",
                    option,
                    self._serial,
                )
                return

            LOGGER.debug(
                "Setting fan speed to %s (%s) for device %s",
                option,
                speed.value,
                self._serial,
            )

            # Get the device object from coordinator data
            device_obj = None
            for device in self.coordinator.data:
                if device.serial_number == self._serial:
                    device_obj = device
                    break

            if device_obj is None:
                LOGGER.error(
                    "Device %s not found in coordinator data",
                    self._serial,
                )
                return

            # Get current device status to preserve other settings
            current_status = self.device_status
            if not current_status:
                LOGGER.error(
                    "Cannot get current device status for %s",
                    self._serial,
                )
                return

            LOGGER.debug(
                "FanSpeedSelect: Current status before change for device %s: %s",
                self._serial,
                current_status,
            )

            # Use the device's change_mode method with dictionary
            # API requires ALL parameters: operatingMode, fanSpeed, humidityLevel, lightSensorLevel
            # Get current settings and convert integers to enums if needed
            current_operating_mode = current_status.get("operating_mode", OperatingMode.Auto)
            current_light_sensor_level = current_status.get("light_sensor_level", LightSensorLevel.Off)
            current_humidity_level = current_status.get("humidity_level", HumidityLevel.Normal)

            # Convert integers to enums if needed
            if isinstance(current_operating_mode, int):
                current_operating_mode = OperatingMode(current_operating_mode)
            if isinstance(current_light_sensor_level, int):
                current_light_sensor_level = LightSensorLevel(current_light_sensor_level)
            if isinstance(current_humidity_level, int):
                current_humidity_level = HumidityLevel(current_humidity_level)

            # Create dictionary with ALL required parameters
            mode_data = {
                "fan_speed": speed,  # Updated parameter
                "operating_mode": current_operating_mode,  # Required
                "light_sensor_level": current_light_sensor_level,  # Required
                "humidity_level": current_humidity_level,  # Required
            }

            result = await device_obj.change_mode(mode_data)
            LOGGER.debug(f"FanSpeedSelect: API change_mode result: {result} (type: {type(result)})")

            if isinstance(result, Success):
                LOGGER.debug(
                    "Successfully set fan speed to %s for device %s",
                    option,
                    self._serial,
                )

                # CRITICAL FIX: Wait for device state to propagate before refreshing
                LOGGER.debug(f"FanSpeedSelect: Waiting 3 seconds for device state to propagate...")
                await asyncio.sleep(3)

                # Invalidate cache and request a coordinator update to refresh the state
                LOGGER.debug(f"FanSpeedSelect: Invalidating cache and refreshing for device {self._serial}")
                self.coordinator.invalidate_cache()
                await self.coordinator.async_request_refresh()
                # Force immediate entity state update
                self.async_write_ha_state()
                LOGGER.debug(f"FanSpeedSelect: Completed cache invalidation and state update for device {self._serial}")
            elif isinstance(result, Failure):
                error_msg = str(result.failure())
                LOGGER.error(
                    "Failed to set fan speed to %s for device %s: %s",
                    option,
                    self._serial,
                    error_msg,
                )
            else:
                LOGGER.error(
                    "Unexpected result type when setting fan speed to %s for device %s",
                    option,
                    self._serial,
                )

        except Exception as e:
            LOGGER.error(
                "Exception when setting fan speed to %s for device %s: %s",
                option,
                self._serial,
                str(e),
            )


class OperatingModeSelect(CoordinatorEntity, SelectEntity):
    """Select entity for operating mode (preset)."""

    _attr_has_entity_name = True
    _attr_translation_key = "operating_mode"
    _attr_icon = "mdi:cog"

    def __init__(self, coordinator: AmbientikaHub, device: Device) -> None:
        """Initialize the select."""
        super().__init__(coordinator)
        self._device = device
        self._serial = device.serial_number
        self._attr_unique_id = f"{self._serial}_operating_mode"

        # Set up the available options
        self._attr_options = [mode.name for mode in OperatingMode]

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

    @property
    def current_option(self) -> str | None:
        """Return the currently selected option."""
        if status := self.device_status:
            try:
                # Get operating mode from device status
                operating_mode_value = status.get("operating_mode")
                if operating_mode_value is not None:
                    # Convert value to OperatingMode enum
                    for mode in OperatingMode:
                        if mode.value == operating_mode_value or mode == operating_mode_value:
                            return mode.name

                    # If we can't match the value, log it for debugging
                    LOGGER.warning(
                        "Unknown operating mode value for device %s: %s",
                        self._serial,
                        operating_mode_value,
                    )
            except (ValueError, TypeError) as e:
                LOGGER.error(
                    "Invalid operating mode value for device %s: %s - %s",
                    self._serial,
                    status.get("operating_mode"),
                    str(e),
                )
        return None

    async def async_select_option(self, option: str) -> None:
        """Change the selected option."""
        try:
            # Find the corresponding OperatingMode enum
            mode = None
            for operating_mode in OperatingMode:
                if operating_mode.name == option:
                    mode = operating_mode
                    break

            if mode is None:
                LOGGER.error(
                    "Invalid operating mode option selected: %s for device %s",
                    option,
                    self._serial,
                )
                return

            LOGGER.debug(
                "Setting operating mode to %s (%s) for device %s",
                option,
                mode.value,
                self._serial,
            )

            # Get the device object from coordinator data
            device_obj = None
            for device in self.coordinator.data:
                if device.serial_number == self._serial:
                    device_obj = device
                    break

            if device_obj is None:
                LOGGER.error(
                    "Device %s not found in coordinator data",
                    self._serial,
                )
                return

            # Get current device status to preserve other settings
            current_status = self.device_status
            if not current_status:
                LOGGER.error(
                    "Cannot get current device status for %s",
                    self._serial,
                )
                return

            # Debug: Log current status structure
            LOGGER.debug(f"Current device status for {self._serial}: {current_status}")
            LOGGER.debug(f"Current status keys: {list(current_status.keys()) if current_status else 'None'}")

            # Check what type the current values are
            if current_status:
                LOGGER.debug(f"operating_mode type: {type(current_status.get('operating_mode'))}, value: {current_status.get('operating_mode')}")
                LOGGER.debug(f"fan_speed type: {type(current_status.get('fan_speed'))}, value: {current_status.get('fan_speed')}")
                LOGGER.debug(f"light_sensor_level type: {type(current_status.get('light_sensor_level'))}, value: {current_status.get('light_sensor_level')}")

            # API requires ALL parameters: operatingMode, fanSpeed, humidityLevel, lightSensorLevel
            # Get current settings and convert integers to enums if needed
            current_fan_speed = current_status.get("fan_speed", FanSpeed.Low)
            current_light_sensor_level = current_status.get("light_sensor_level", LightSensorLevel.Off)
            current_humidity_level = current_status.get("humidity_level", HumidityLevel.Normal)

            # Convert integers to enums if needed
            if isinstance(current_fan_speed, int):
                current_fan_speed = FanSpeed(current_fan_speed)
            if isinstance(current_light_sensor_level, int):
                current_light_sensor_level = LightSensorLevel(current_light_sensor_level)
            if isinstance(current_humidity_level, int):
                current_humidity_level = HumidityLevel(current_humidity_level)

            # Create dictionary with ALL required parameters
            mode_data = {
                "operating_mode": mode,  # Updated parameter
                "fan_speed": current_fan_speed,  # Required
                "light_sensor_level": current_light_sensor_level,  # Required
                "humidity_level": current_humidity_level,  # Required
            }

            LOGGER.debug(f"Sending mode_data to API for operating mode change: {mode_data}")
            LOGGER.debug(f"Types in mode_data: {[(k, type(v)) for k, v in mode_data.items()]}")

            result = await device_obj.change_mode(mode_data)

            if isinstance(result, Success):
                LOGGER.debug(
                    "Successfully set operating mode to %s for device %s",
                    option,
                    self._serial,
                )

                # CRITICAL FIX: Wait for device state to propagate before refreshing
                LOGGER.debug(f"OperatingModeSelect: Waiting 3 seconds for device state to propagate...")
                await asyncio.sleep(3)

                # Invalidate cache and request a coordinator update to refresh the state
                LOGGER.debug(f"OperatingModeSelect: Invalidating cache and refreshing for device {self._serial}")
                self.coordinator.invalidate_cache()
                await self.coordinator.async_request_refresh()
                # Force immediate entity state update
                self.async_write_ha_state()
                LOGGER.debug(f"OperatingModeSelect: Completed cache invalidation and state update for device {self._serial}")
            elif isinstance(result, Failure):
                error_msg = str(result.failure())
                LOGGER.error(
                    "Failed to set operating mode to %s for device %s: %s",
                    option,
                    self._serial,
                    error_msg,
                )
            else:
                LOGGER.error(
                    "Unexpected result type when setting operating mode to %s for device %s",
                    option,
                    self._serial,
                )

        except Exception as e:
            LOGGER.error(
                "Exception when setting operating mode to %s for device %s: %s",
                option,
                self._serial,
                str(e),
            )


class HumidityLevelSelect(CoordinatorEntity, SelectEntity):
    """Select entity for humidity level."""

    _attr_has_entity_name = True
    _attr_translation_key = "humidity_level"
    _attr_icon = "mdi:water-percent"

    def __init__(self, coordinator: AmbientikaHub, device: Device) -> None:
        """Initialize the select."""
        super().__init__(coordinator)
        self._device = device
        self._serial = device.serial_number
        self._attr_unique_id = f"{self._serial}_humidity_level"

        # Set up the available options
        self._attr_options = [level.name for level in HumidityLevel]

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

    @property
    def current_option(self) -> str | None:
        """Return the currently selected option."""
        if status := self.device_status:
            try:
                # Get humidity level from device status
                humidity_level_value = status.get("humidity_level")
                LOGGER.debug(f"HumidityLevelSelect: Device {self._serial} has humidity_level value: {humidity_level_value} (type: {type(humidity_level_value)})")
                if humidity_level_value is not None:
                    # Convert value to HumidityLevel enum
                    for level in HumidityLevel:
                        if level.value == humidity_level_value or level == humidity_level_value:
                            LOGGER.debug(f"HumidityLevelSelect: Device {self._serial} matched to level.name: {level.name}")
                            return level.name

                    # If we can't match the value, log it for debugging
                    LOGGER.warning(
                        "Unknown humidity level value for device %s: %s",
                        self._serial,
                        humidity_level_value,
                    )
            except (ValueError, TypeError) as e:
                LOGGER.error(
                    "Invalid humidity level value for device %s: %s - %s",
                    self._serial,
                    status.get("humidity_level"),
                    str(e),
                )
        return None

    async def async_select_option(self, option: str) -> None:
        """Change the selected option."""
        LOGGER.debug(f"HumidityLevelSelect.async_select_option called with option: {option} for device {self._serial}")
        try:
            # Find the corresponding HumidityLevel enum
            level = None
            for humidity_level in HumidityLevel:
                if humidity_level.name == option:
                    level = humidity_level
                    break

            if level is None:
                LOGGER.error(
                    "Invalid humidity level option selected: %s for device %s",
                    option,
                    self._serial,
                )
                return

            LOGGER.debug(
                "Setting humidity level to %s (%s) for device %s",
                option,
                level.value,
                self._serial,
            )

            # Get the device object from coordinator data
            device_obj = None
            for device in self.coordinator.data:
                if device.serial_number == self._serial:
                    device_obj = device
                    break

            if device_obj is None:
                LOGGER.error(
                    "Device %s not found in coordinator data",
                    self._serial,
                )
                return

            # Get current device status to preserve other settings
            current_status = self.device_status
            if not current_status:
                LOGGER.error(
                    "Cannot get current device status for %s",
                    self._serial,
                )
                return

            # API requires ALL parameters: operatingMode, fanSpeed, humidityLevel, lightSensorLevel
            # Get current settings and convert integers to enums if needed
            current_operating_mode = current_status.get("operating_mode", OperatingMode.Auto)
            current_fan_speed = current_status.get("fan_speed", FanSpeed.Low)
            current_light_sensor_level = current_status.get("light_sensor_level", LightSensorLevel.Off)

            # Convert integers to enums if needed
            if isinstance(current_operating_mode, int):
                current_operating_mode = OperatingMode(current_operating_mode)
            if isinstance(current_fan_speed, int):
                current_fan_speed = FanSpeed(current_fan_speed)
            if isinstance(current_light_sensor_level, int):
                current_light_sensor_level = LightSensorLevel(current_light_sensor_level)

            # Create dictionary with ALL required parameters
            mode_data = {
                "humidity_level": level,  # Updated parameter
                "operating_mode": current_operating_mode,  # Required
                "fan_speed": current_fan_speed,  # Required
                "light_sensor_level": current_light_sensor_level,  # Required
            }

            LOGGER.debug(f"Sending mode_data to API for humidity level change: {mode_data}")
            LOGGER.debug(f"Types in mode_data: {[(k, type(v)) for k, v in mode_data.items()]}")

            result = await device_obj.change_mode(mode_data)
            LOGGER.debug(f"HumidityLevelSelect: API change_mode result: {result} (type: {type(result)})")

            if isinstance(result, Success):
                LOGGER.debug(
                    "Successfully set humidity level to %s for device %s",
                    option,
                    self._serial,
                )

                # CRITICAL FIX: Wait for device state to propagate before refreshing
                LOGGER.debug(f"HumidityLevelSelect: Waiting 3 seconds for device state to propagate...")
                await asyncio.sleep(3)

                # Invalidate cache and request a coordinator update to refresh the state
                LOGGER.debug(f"HumidityLevelSelect: Invalidating cache and refreshing for device {self._serial}")
                self.coordinator.invalidate_cache()
                await self.coordinator.async_request_refresh()
                # Force immediate entity state update
                self.async_write_ha_state()
                LOGGER.debug(f"HumidityLevelSelect: Completed cache invalidation and state update for device {self._serial}")
            elif isinstance(result, Failure):
                error_msg = str(result.failure())
                LOGGER.error(
                    "Failed to set humidity level to %s for device %s: %s",
                    option,
                    self._serial,
                    error_msg,
                )
            else:
                LOGGER.error(
                    "Unexpected result type when setting humidity level to %s for device %s",
                    option,
                    self._serial,
                )

        except Exception as e:
            LOGGER.error(
                "Exception when setting humidity level to %s for device %s: %s",
                option,
                self._serial,
                str(e),
            )
