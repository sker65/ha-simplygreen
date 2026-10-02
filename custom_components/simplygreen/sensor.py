"""Sensors with the most recent day's 15-min profile from SimplyGreen."""
from __future__ import annotations

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.const import UnitOfEnergy
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import SimplyGreenCoordinator


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    coordinator: SimplyGreenCoordinator = entry.runtime_data
    async_add_entities([LastDaySensor(coordinator), LastPointSensor(coordinator)])


class _Base(CoordinatorEntity[SimplyGreenCoordinator], SensorEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator: SimplyGreenCoordinator, key: str) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.mliid}_{key}"
        self._attr_translation_key = key
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.mliid)},
            name="SimplyGreen",
            manufacturer="SimplyGreen",
            model=f"Messlokation {coordinator.mliid}",
        )


class LastDaySensor(_Base):
    """Grid import of the last complete day; 15-min data in attributes (not recorded)."""

    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_suggested_display_precision = 2
    _unrecorded_attributes = frozenset({"intervals", "epex_ct"})

    def __init__(self, coordinator: SimplyGreenCoordinator) -> None:
        super().__init__(coordinator, "last_day")

    @property
    def native_value(self):
        return (self.coordinator.data or {}).get("total_kwh")

    @property
    def extra_state_attributes(self):
        d = self.coordinator.data or {}
        return {
            "day": d.get("day").isoformat() if d.get("day") else None,
            "avg_price_ct": d.get("avg_price_ct"),
            "intervals": d.get("intervals", []),
            "epex_ct": d.get("epex_ct", []),
        }


class LastPointSensor(_Base):
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, coordinator: SimplyGreenCoordinator) -> None:
        super().__init__(coordinator, "last_point")

    @property
    def native_value(self):
        return (self.coordinator.data or {}).get("last_point")
