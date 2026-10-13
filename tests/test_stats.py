"""Fetch window, statistics rows and day summary (stats.py), without Home Assistant."""
from datetime import date, datetime, timedelta, timezone

UTC = timezone.utc


def _day(sg_api, d, values):
    """Sequential 15-min intervals starting at local midnight of `d` (DST-aware)."""
    midnight = datetime(d.year, d.month, d.day, tzinfo=sg_api.TZ).astimezone(UTC)
    return [(midnight + i * timedelta(minutes=15), v) for i, v in enumerate(values)]


# --- window_start ---------------------------------------------------------------

def test_window_initial_backfill(sg_stats, sg_api):
    assert sg_stats.window_start(date(2026, 10, 2), None, sg_api.TZ, 30, 3) == date(2026, 9, 2)


def test_window_refetches_days_before_last_import(sg_stats, sg_api):
    last = datetime(2026, 10, 1, 21, 0, tzinfo=UTC)  # 23:00 local on 01.10.
    assert sg_stats.window_start(date(2026, 10, 2), last, sg_api.TZ, 30, 3) == date(2026, 9, 28)


def test_window_uses_local_date_not_utc_date(sg_stats, sg_api):
    last = datetime(2026, 9, 30, 23, 0, tzinfo=UTC)  # already 01.10. 01:00 local
    assert sg_stats.window_start(date(2026, 10, 2), last, sg_api.TZ, 30, 3) == date(2026, 9, 28)


# --- chunks ---------------------------------------------------------------------

def test_chunks_split_into_blocks_of_seven_days(sg_stats):
    c = sg_stats.chunks(date(2026, 9, 1), date(2026, 9, 30))
    assert [(a.day, b.day) for a, b in c] == [(1, 7), (8, 14), (15, 21), (22, 28), (29, 30)]


def test_chunks_cover_range_without_gaps_or_overlap(sg_stats):
    c = sg_stats.chunks(date(2026, 1, 1), date(2026, 3, 15))
    assert c[0][0] == date(2026, 1, 1) and c[-1][1] == date(2026, 3, 15)
    assert all(nxt[0] == prev[1] + timedelta(days=1) for prev, nxt in zip(c, c[1:]))
    assert all((b - a).days <= 6 for a, b in c)


def test_chunks_single_day_and_empty_range(sg_stats):
    assert sg_stats.chunks(date(2026, 10, 2), date(2026, 10, 2)) == [(date(2026, 10, 2), date(2026, 10, 2))]
    assert sg_stats.chunks(date(2026, 10, 3), date(2026, 10, 2)) == []


# --- merge ----------------------------------------------------------------------

def test_merge_dedupes_overlap_later_value_wins_and_sorts(sg_stats):
    t = datetime(2026, 10, 1, tzinfo=UTC)
    pts = [(t + timedelta(minutes=15), 2.0), (t, 1.0), (t + timedelta(minutes=15), 9.0)]
    assert sg_stats.merge(pts) == [(t, 1.0), (t + timedelta(minutes=15), 9.0)]


# --- energy_stats ---------------------------------------------------------------

def test_energy_stats_sums_quarters_to_hours_with_running_sum(sg_stats, sg_api):
    iv = _day(sg_api, date(2026, 10, 1), [0.1] * 8)  # two hours of 0.4 kWh
    rows = sg_stats.energy_stats(iv, 0.0)
    assert [r["sum"] for r in rows] == [0.4, 0.8]
    assert all(r["state"] == r["sum"] for r in rows)
    assert all(r["start"].minute == 0 and r["start"].tzinfo is not None for r in rows)


def test_energy_stats_continues_from_base_sum(sg_stats, sg_api):
    rows = sg_stats.energy_stats(_day(sg_api, date(2026, 10, 1), [0.25] * 4), 100.0)
    assert rows[0]["sum"] == 101.0


def test_energy_stats_empty(sg_stats):
    assert sg_stats.energy_stats([], 5.0) == []


def test_energy_stats_fall_back_day_has_25_unique_hours(sg_stats, sg_api):
    iv = _day(sg_api, date(2026, 10, 25), [0.01] * 100)
    rows = sg_stats.energy_stats(iv, 0.0)
    starts = [r["start"] for r in rows]
    assert len(rows) == 25
    assert all(b - a == timedelta(hours=1) for a, b in zip(starts, starts[1:]))
    assert rows[-1]["sum"] == 1.0


def test_energy_stats_spring_forward_day_has_23_hours(sg_stats, sg_api):
    rows = sg_stats.energy_stats(_day(sg_api, date(2026, 3, 29), [0.01] * 92), 0.0)
    assert len(rows) == 23
    assert rows[-1]["sum"] == 0.92


def test_first_hour(sg_stats):
    t = datetime(2026, 10, 1, 7, 45, tzinfo=UTC)
    assert sg_stats.first_hour([(t, 1.0), (t - timedelta(minutes=45), 1.0)]) == datetime(2026, 10, 1, 7, 0, tzinfo=UTC)


# --- price_stats ----------------------------------------------------------------

def test_price_stats_converts_eur_mwh_to_ct_kwh_and_aggregates(sg_stats):
    t = datetime(2026, 10, 1, 10, 0, tzinfo=UTC)
    iv = [(t + timedelta(minutes=15 * i), p) for i, p in enumerate([100.0, 150.0, 200.0, 250.0])]
    (row,) = sg_stats.price_stats(iv)
    assert row == {"start": t, "mean": 17.5, "min": 10.0, "max": 25.0}


def test_price_stats_one_row_per_hour(sg_stats, sg_api, payload_15min):
    prices = sg_api.to_intervals(sg_api.series(payload_15min, "EPEX"))
    rows = sg_stats.price_stats(prices)
    assert len(rows) == 24
    assert all(r["min"] <= r["mean"] <= r["max"] for r in rows)


# --- summarize ------------------------------------------------------------------

def test_summarize_real_day(sg_stats, sg_api, payload_15min):
    energy = sg_api.to_intervals(sg_api.series(payload_15min, "Menge"))
    price = sg_api.to_intervals(sg_api.series(payload_15min, "EPEX"))
    s = sg_stats.summarize(energy, price, sg_api.TZ)
    assert s["day"] == date(2026, 10, 1)
    assert s["total_kwh"] == 0.308
    assert len(s["intervals"]) == 96 and len(s["epex_ct"]) == 96
    assert s["intervals"][0][0] == "2026-10-01T00:00:00+02:00"
    assert s["last_point"] == energy[-1][0] + timedelta(minutes=15)
    prices = dict(price)
    expected = sum(k * prices[t] / 10 for t, k in energy) / sum(k for _, k in energy)
    assert s["avg_price_ct"] == round(expected, 2)


def test_summarize_average_price_only_weights_priced_intervals(sg_stats, sg_api):
    energy = _day(sg_api, date(2026, 10, 1), [1.0] * 96)
    price = [(energy[0][0], 100.0), (energy[1][0], 200.0)]  # only two intervals have a price
    s = sg_stats.summarize(energy, price, sg_api.TZ)
    assert s["avg_price_ct"] == 15.0
    assert len(s["epex_ct"]) == 2


def test_summarize_ignores_incomplete_day(sg_stats, sg_api):
    s = sg_stats.summarize(_day(sg_api, date(2026, 10, 2), [0.1] * 50), [], sg_api.TZ)
    assert s["day"] is None
    assert s["last_point"] == _day(sg_api, date(2026, 10, 2), [0.1] * 50)[-1][0] + timedelta(minutes=15)


def test_summarize_picks_most_recent_complete_day(sg_stats, sg_api):
    energy = _day(sg_api, date(2026, 9, 30), [0.1] * 96) + _day(sg_api, date(2026, 10, 1), [0.2] * 96)
    energy += _day(sg_api, date(2026, 10, 2), [0.3] * 10)  # today, still incomplete
    s = sg_stats.summarize(energy, [], sg_api.TZ)
    assert s["day"] == date(2026, 10, 1)
    assert s["total_kwh"] == 19.2
    assert s["avg_price_ct"] is None and s["epex_ct"] == []


def test_summarize_dst_days_count_as_complete(sg_stats, sg_api):
    assert sg_stats.summarize(_day(sg_api, date(2026, 10, 25), [0.01] * 100), [], sg_api.TZ)["day"] == date(2026, 10, 25)
    assert sg_stats.summarize(_day(sg_api, date(2026, 3, 29), [0.01] * 92), [], sg_api.TZ)["day"] == date(2026, 3, 29)


def test_summarize_no_data(sg_stats, sg_api):
    assert sg_stats.summarize([], [], sg_api.TZ) == {"day": None, "last_point": None}


# --- final price / cost ---------------------------------------------------------

def test_final_price_adds_surcharge_and_vat(sg_stats):
    # 200 EUR/MWh = 20 ct/kWh; + 10 ct surcharge = 30 ct net; + 19 % VAT = 35.7 ct
    assert round(sg_stats.final_price_ct(200.0, 10.0, 19.0), 6) == 35.7
    assert sg_stats.final_price_ct(200.0, 0.0, 0.0) == 20.0


def test_final_price_with_negative_epex(sg_stats):
    assert round(sg_stats.final_price_ct(-50.0, 20.0, 0.0), 6) == 15.0


def test_cost_stats_sums_hourly_cost_with_running_sum(sg_stats, sg_api):
    energy = _day(sg_api, date(2026, 10, 1), [0.5] * 8)  # 2 kWh per hour
    price = [(t, 200.0) for t, _ in energy]
    rows = sg_stats.cost_stats(energy, price, 10.0, 19.0, 1.0)
    # 2 kWh x 35.7 ct = 0.714 EUR per hour
    assert [r["sum"] for r in rows] == [1.714, 2.428]
    assert all(r["state"] == r["sum"] and r["start"].minute == 0 for r in rows)


def test_cost_stats_without_surcharge_matches_portal_cost_series(sg_stats, sg_api, payload_15min):
    # The portal's own "Kosten" series is exactly kWh x EPEX (net, EUR).
    energy = sg_api.to_intervals(sg_api.series(payload_15min, "Menge"))
    price = sg_api.to_intervals(sg_api.series(payload_15min, "EPEX"))
    rows = sg_stats.cost_stats(energy, price, 0.0, 0.0, 0.0)
    expected = sum(k * dict(price)[t] / 1000 for t, k in energy)
    assert len(rows) == 24
    assert abs(rows[-1]["sum"] - expected) < 1e-3


def test_cost_stats_skips_intervals_without_price(sg_stats, sg_api):
    energy = _day(sg_api, date(2026, 10, 1), [1.0] * 8)
    price = [(energy[0][0], 100.0)]  # only the very first interval has a price
    rows = sg_stats.cost_stats(energy, price, 0.0, 0.0, 0.0)
    assert len(rows) == 1 and rows[0]["sum"] == 0.1


def test_final_price_stats(sg_stats):
    t = datetime(2026, 10, 1, 10, 0, tzinfo=UTC)
    iv = [(t + timedelta(minutes=15 * i), p) for i, p in enumerate([100.0, 300.0])]
    (row,) = sg_stats.final_price_stats(iv, 10.0, 0.0)
    assert row == {"start": t, "mean": 30.0, "min": 20.0, "max": 40.0}
