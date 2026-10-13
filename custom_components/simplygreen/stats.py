"""Pure logic between the portal data and Home Assistant: fetch window, statistics rows
and the last-day summary. No Home Assistant imports, so it is tested in `tests/` without HA.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, tzinfo

from .api import hourly

QUARTER = timedelta(minutes=15)
MIN_COMPLETE_INTERVALS = 92  # 92/96/100 intervals on DST days


def window_start(
    today: date,
    last_import: datetime | None,
    tz: tzinfo,
    backfill_days: int,
    refetch_days: int,
) -> date:
    """First day to request: initial backfill, or a few days before the last imported hour."""
    if last_import is None:
        return today - timedelta(days=backfill_days)
    return last_import.astimezone(tz).date() - timedelta(days=refetch_days)


def chunks(start: date, end: date, days: int = 7) -> list[tuple[date, date]]:
    """Split start..end (inclusive) into blocks of at most `days` days."""
    out = []
    day = start
    while day <= end:
        chunk_end = min(day + timedelta(days=days - 1), end)
        out.append((day, chunk_end))
        day = chunk_end + timedelta(days=1)
    return out


def merge(points: list[tuple[datetime, float]]) -> list[tuple[datetime, float]]:
    """De-duplicate overlapping chunks (later value wins) and sort by time."""
    return sorted(dict(points).items())


def first_hour(intervals: list[tuple[datetime, float]]) -> datetime:
    return min(start.replace(minute=0, second=0, microsecond=0) for start, _ in intervals)


def energy_stats(intervals: list[tuple[datetime, float]], base_sum: float) -> list[dict]:
    """Hourly kWh with a running sum that continues from `base_sum`."""
    running = base_sum
    rows = []
    for start, kwh in hourly(intervals, "sum").items():
        running += kwh
        rows.append({"start": start, "state": round(running, 4), "sum": round(running, 4)})
    return rows


def price_stats(intervals: list[tuple[datetime, float]]) -> list[dict]:
    """Hourly mean/min/max in ct/kWh from 15-min EPEX prices in EUR/MWh."""
    buckets: dict[datetime, list[float]] = {}
    for start, eur_mwh in intervals:
        buckets.setdefault(start.replace(minute=0, second=0, microsecond=0), []).append(eur_mwh / 10)
    return [
        {"start": h, "mean": round(sum(v) / len(v), 3), "min": round(min(v), 3), "max": round(max(v), 3)}
        for h, v in sorted(buckets.items())
    ]


def final_price_ct(eur_mwh: float, surcharge_ct: float, vat_percent: float) -> float:
    """Price per kWh on the bill: (EPEX + surcharge) incl. VAT, in ct/kWh."""
    return (eur_mwh / 10 + surcharge_ct) * (1 + vat_percent / 100)


def cost_stats(
    energy: list[tuple[datetime, float]],
    price: list[tuple[datetime, float]],
    surcharge_ct: float,
    vat_percent: float,
    base_sum: float,
) -> list[dict]:
    """Hourly cost in EUR (kWh x final price) with a running sum from `base_sum`.

    Intervals without a price are skipped; an hour without any price gets no row.
    """
    prices = dict(price)
    buckets: dict[datetime, float] = {}
    for start, kwh in energy:
        if start in prices:
            hour = start.replace(minute=0, second=0, microsecond=0)
            eur = kwh * final_price_ct(prices[start], surcharge_ct, vat_percent) / 100
            buckets[hour] = buckets.get(hour, 0.0) + eur
    running = base_sum
    rows = []
    for hour, eur in sorted(buckets.items()):
        running += eur
        rows.append({"start": hour, "state": round(running, 4), "sum": round(running, 4)})
    return rows


def final_price_stats(
    price: list[tuple[datetime, float]], surcharge_ct: float, vat_percent: float
) -> list[dict]:
    """Hourly mean/min/max of the final price in ct/kWh."""
    return price_stats(
        [(start, final_price_ct(eur_mwh, surcharge_ct, vat_percent) * 10) for start, eur_mwh in price]
    )


def summarize(energy: list[tuple[datetime, float]], price: list[tuple[datetime, float]], tz: tzinfo) -> dict:
    """15-min profile of the most recent complete local day."""
    last_point = energy[-1][0] + QUARTER if energy else None
    by_day: dict[date, list] = {}
    for start, kwh in energy:
        by_day.setdefault(start.astimezone(tz).date(), []).append((start, kwh))
    complete = [d for d, v in by_day.items() if len(v) >= MIN_COMPLETE_INTERVALS]
    if not complete:
        return {"day": None, "last_point": last_point}
    day = max(complete)
    prices = dict(price)
    rows = sorted(by_day[day])
    total = sum(k for _, k in rows)
    # weight only the intervals that have a price, so gaps don't pull the average down
    priced = [(s, k) for s, k in rows if s in prices]
    priced_kwh = sum(k for _, k in priced)
    cost_ct = sum(k * prices[s] / 10 for s, k in priced)
    return {
        "day": day,
        "total_kwh": round(total, 3),
        "avg_price_ct": round(cost_ct / priced_kwh, 2) if priced_kwh else None,
        "intervals": [[s.astimezone(tz).isoformat(), k] for s, k in rows],
        "epex_ct": [[s.astimezone(tz).isoformat(), round(prices[s] / 10, 3)] for s, _ in rows if s in prices],
        "last_point": last_point,
    }
