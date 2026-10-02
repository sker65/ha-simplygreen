"""Fetch SimplyGreen data and import it as long-term statistics."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.statistics import (
    async_add_external_statistics,
    get_last_statistics,
    statistics_during_period,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import (
    SimplyGreenAuthError,
    SimplyGreenClient,
    SimplyGreenError,
    hourly,
    series,
    to_intervals,
)
from .stats import chunks, energy_stats, first_hour, merge, price_stats, summarize, window_start
from .const import (
    CONF_BACKFILL_DAYS,
    CONF_MLIID,
    DEFAULT_BACKFILL_DAYS,
    DOMAIN,
    REFETCH_DAYS,
    UPDATE_HOURS,
)

_LOGGER = logging.getLogger(__name__)


# Statistic names are stored in the recorder metadata and cannot use translation files.
_STAT_NAMES = {
    "de": {"energy": "SimplyGreen Netzbezug", "price": "SimplyGreen EPEX-Preis"},
    "en": {"energy": "SimplyGreen grid import", "price": "SimplyGreen EPEX price"},
}


def _metadata(statistic_id: str, name: str, unit: str, *, has_sum: bool, mean: bool) -> dict:
    meta = {
        "source": DOMAIN,
        "statistic_id": statistic_id,
        "name": name,
        "unit_of_measurement": unit,
        "has_sum": has_sum,
        "unit_class": "energy" if unit == "kWh" else None,
    }
    try:
        from homeassistant.components.recorder.models import StatisticMeanType

        meta["mean_type"] = StatisticMeanType.ARITHMETIC if mean else StatisticMeanType.NONE
    except ImportError:  # older HA
        meta["has_mean"] = mean
    return meta


class SimplyGreenCoordinator(DataUpdateCoordinator[dict]):
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, client: SimplyGreenClient) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(hours=UPDATE_HOURS),
        )
        self.client = client
        self.mliid: str = entry.data[CONF_MLIID]
        self.energy_id = f"{DOMAIN}:netzbezug_{self.mliid}"
        self.price_id = f"{DOMAIN}:epex_{self.mliid}"
        self._backfill_days = entry.options.get(
            CONF_BACKFILL_DAYS, entry.data.get(CONF_BACKFILL_DAYS, DEFAULT_BACKFILL_DAYS)
        )

    async def _recorder(self, func, *args):
        return await get_instance(self.hass).async_add_executor_job(func, self.hass, *args)

    async def _last_import(self) -> datetime | None:
        last = await self._recorder(get_last_statistics, 1, self.energy_id, True, {"sum"})
        rows = last.get(self.energy_id)
        if not rows:
            return None
        start = rows[0]["start"]
        return datetime.fromtimestamp(start, timezone.utc) if isinstance(start, (int, float)) else start

    async def _sum_before(self, hour: datetime) -> float:
        rows = await self._recorder(
            statistics_during_period,
            hour - timedelta(days=90),
            hour,
            {self.energy_id},
            "hour",
            None,
            {"sum"},
        )
        data = rows.get(self.energy_id) or []
        return float(data[-1]["sum"] or 0.0) if data else 0.0

    async def _async_update_data(self) -> dict:
        today = dt_util.now().date()
        last = await self._last_import()
        start_day = window_start(
            today, last, dt_util.get_default_time_zone(), int(self._backfill_days), REFETCH_DAYS
        )

        _LOGGER.debug(
            "Update start: window %s..%s, last imported hour: %s",
            start_day, today, dt_util.as_local(last).isoformat() if last else "none (initial backfill)",
        )
        energy: list = []
        price: list = []
        try:
            for chunk_start, chunk_end in chunks(start_day, today):
                payload = await self.client.get_load_profile(self.mliid, chunk_start, chunk_end)
                energy += to_intervals(series(payload, "Menge"))
                price += to_intervals(series(payload, "EPEX"))
        except SimplyGreenAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except SimplyGreenError as err:
            raise UpdateFailed(str(err)) from err

        energy = merge(energy)
        price = merge(price)

        if energy:
            await self._import(energy, price)
            newest_hour = max(hourly(energy, "sum"))
            _LOGGER.debug(
                "Update done: %d energy / %d price intervals, newest data ends %s; %s",
                len(energy), len(price),
                dt_util.as_local(energy[-1][0] + timedelta(minutes=15)).isoformat(),
                "no new hours since last run" if last and newest_hour <= last
                else f"{int((newest_hour - last) / timedelta(hours=1)) if last else 'all'} new hour(s)",
            )
        else:
            _LOGGER.debug("Update done: portal returned no energy data for the window")

        return summarize(energy, price, dt_util.get_default_time_zone())

    async def _import(self, energy: list, price: list) -> None:
        names = _STAT_NAMES.get(self.hass.config.language.split("-")[0], _STAT_NAMES["en"])
        e_stats = energy_stats(energy, await self._sum_before(first_hour(energy)))
        async_add_external_statistics(
            self.hass,
            _metadata(self.energy_id, names["energy"], "kWh", has_sum=True, mean=False),
            e_stats,
        )

        if price:
            p_stats = price_stats(price)
            async_add_external_statistics(
                self.hass,
                _metadata(self.price_id, names["price"], "ct/kWh", has_sum=False, mean=True),
                p_stats,
            )
