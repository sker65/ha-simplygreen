from datetime import datetime, timedelta


def test_15min_day_maps_to_local_day(sg_api, payload_15min):
    iv = sg_api.to_intervals(sg_api.series(payload_15min, "Menge"))
    assert len(iv) == 96
    assert iv[0][0].astimezone(sg_api.TZ).isoformat() == "2026-10-01T00:00:00+02:00"
    assert iv[-1][0].astimezone(sg_api.TZ).isoformat() == "2026-10-01T23:45:00+02:00"


def test_hourly_aggregation(sg_api, payload_15min):
    iv = sg_api.to_intervals(sg_api.series(payload_15min, "Menge"))
    h = sg_api.hourly(iv, "sum")
    assert len(h) == 24
    assert round(sum(h.values()), 3) == 0.308
    assert all(k.minute == 0 for k in h)


def test_epex_series_present(sg_api, payload_15min):
    prices = sg_api.series(payload_15min, "EPEX")
    assert len(prices) == 96


def _wall_ms(w):
    return int((w - datetime(1970, 1, 1)).total_seconds() * 1000)


def test_dst_fall_back_day_has_100_monotonic_intervals(sg_api):
    # ASSUMPTION: portal repeats wall-clock ends 02:15..03:00 on 25.10.2026 (unverified)
    walls, t, repeated = [], datetime(2026, 10, 25, 0, 15), False
    while t <= datetime(2026, 10, 26):
        walls.append(t)
        if t == datetime(2026, 10, 25, 3, 0) and not repeated:
            repeated, t = True, datetime(2026, 10, 25, 2, 15)
            continue
        t += timedelta(minutes=15)
    iv = sg_api.to_intervals([(_wall_ms(w), 1.0) for w in walls])
    starts = [s for s, _ in iv]
    assert len(starts) == 100
    assert all(b - a == timedelta(minutes=15) for a, b in zip(starts, starts[1:]))


def test_incomplete_day_uses_wall_clock(sg_api):
    pts = [(_wall_ms(datetime(2026, 10, 2, 0, 15)), 0.1), (_wall_ms(datetime(2026, 10, 2, 0, 30)), 0.2)]
    iv = sg_api.to_intervals(pts)
    assert iv[0][0].astimezone(sg_api.TZ).isoformat() == "2026-10-02T00:00:00+02:00"


def test_parse_csrf_both_attribute_orders(sg_api):
    assert sg_api.parse_csrf('<meta name="csrf-token" content="abc">') == "abc"
    assert sg_api.parse_csrf('<meta content="xyz" name="csrf-token">') == "xyz"
