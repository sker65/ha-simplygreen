"""Fetch SimplyGreen data and import it as long-term statistics."""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone

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
        if last is None:
            start_day = today - timedelta(days=int(self._backfill_days))
        else:
            start_day = dt_util.as_local(last).date() - timedelta(days=REFETCH_DAYS)

        _LOGGER.debug(
            "Update start: window %s..%s, last imported hour: %s",
            start_day, today, dt_util.as_local(last).isoformat() if last else "none (initial backfill)",
        )
        energy: list = []
        price: list = []
        try:
            day = start_day
            while day <= today:
                chunk_end = min(day + timedelta(days=6), today)
                payload = await self.client.get_load_profile(self.mliid, day, chunk_end)
                energy += to_intervals(series(payload, "Menge"))
                price += to_intervals(series(payload, "EPEX"))
                day = chunk_end + timedelta(days=1)
        except SimplyGreenAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except SimplyGreenError as err:
            raise UpdateFailed(str(err)) from err

        # de-duplicate overlapping chunks
        energy = sorted(dict(energy).items())
        price = sorted(dict(price).items())

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

        return self._summary(energy, price)

    async def _import(self, energy: list, price: list) -> None:
        names = _STAT_NAMES.get(self.hass.config.language.split("-")[0], _STAT_NAMES["en"])
        e_hours = hourly(energy, "sum")
        running = await self._sum_before(next(iter(e_hours)))
        e_stats = []
        for start, kwh in e_hours.items():
            running += kwh
            e_stats.append({"start": start, "state": round(running, 4), "sum": round(running, 4)})
        async_add_external_statistics(
            self.hass,
            _metadata(self.energy_id, names["energy"], "kWh", has_sum=True, mean=False),
            e_stats,
        )

        if price:
            buckets: dict[datetime, list[float]] = {}
            for start, eur_mwh in price:
                buckets.setdefault(start.replace(minute=0, second=0, microsecond=0), []).append(eur_mwh / 10)
            p_stats = [
                {"start": h, "mean": round(sum(v) / len(v), 3), "min": round(min(v), 3), "max": round(max(v), 3)}
                for h, v in sorted(buckets.items())
            ]
            async_add_external_statistics(
                self.hass,
                _metadata(self.price_id, names["price"], "ct/kWh", has_sum=False, mean=True),
                p_stats,
            )

    @staticmethod
    def _summary(energy: list, price: list) -> dict:
        """15-min profile of the most recent complete local day."""
        by_day: dict[date, list] = {}
        for start, kwh in energy:
            by_day.setdefault(dt_util.as_local(start).date(), []).append((start, kwh))
        complete = [d for d, v in by_day.items() if len(v) >= 92]  # 92/96/100 on DST days
        if not complete:
            return {"day": None, "last_point": energy[-1][0] + timedelta(minutes=15) if energy else None}
        day = max(complete)
        prices = dict(price)
        rows = sorted(by_day[day])
        total = sum(k for _, k in rows)
        cost_ct = sum(k * prices[s] / 10 for s, k in rows if s in prices)
        return {
            "day": day,
            "total_kwh": round(total, 3),
            "avg_price_ct": round(cost_ct / total, 2) if total else None,
            "intervals": [[dt_util.as_local(s).isoformat(), k] for s, k in rows],
            "epex_ct": [[dt_util.as_local(s).isoformat(), round(prices[s] / 10, 3)] for s, _ in rows if s in prices],
            "last_point": energy[-1][0] + timedelta(minutes=15),
        }
