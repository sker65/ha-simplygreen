"""Manual smoke test against the REAL portal. Not part of pytest.

    cp .env.example .env   # fill in credentials (.env is git-ignored)
    python scripts/live_check.py

Prints only structure and aggregates; never credentials, cookies, tokens or the mliid.
"""
from __future__ import annotations

import asyncio
import importlib.util
import os
import sys
from datetime import date, timedelta
from pathlib import Path

import aiohttp

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "simplygreen_api", ROOT / "custom_components" / "simplygreen" / "api.py"
)
api = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(api)


def load_env(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


async def main() -> int:
    load_env(ROOT / ".env")
    user = os.environ.get("SIMPLYGREEN_USERNAME", "")
    pw = os.environ.get("SIMPLYGREEN_PASSWORD", "")
    if not user or not pw:
        print("SIMPLYGREEN_USERNAME / SIMPLYGREEN_PASSWORD missing (see .env.example)")
        return 2
    days = int(os.environ.get("SIMPLYGREEN_DAYS") or 3)

    async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar()) as session:
        client = api.SimplyGreenClient(session, user, pw)
        try:
            await client.login()
        except api.SimplyGreenAuthError:
            print("FAIL login: credentials rejected")
            return 1
        print("OK   login")

        mliid = os.environ.get("SIMPLYGREEN_MLIID") or client.discovered_mliid
        print(f"{'OK  ' if client.discovered_mliid else 'WARN'} mliid auto-discovery:",
              "found" if client.discovered_mliid else "not found")
        if not mliid:
            print("FAIL no mliid: set SIMPLYGREEN_MLIID in .env")
            return 1

        end = date.today()
        payload = await client.get_load_profile(mliid, end - timedelta(days=days), end)
        print("OK   getLoadProfile; series:", [s.get("LineDesc") for s in payload])

        energy = api.to_intervals(api.series(payload, "Menge"))
        price = api.to_intervals(api.series(payload, "EPEX"))
        print(f"     Menge: {len(energy)} intervals, EPEX: {len(price)} intervals")
        if energy:
            print(f"     range {energy[0][0]:%Y-%m-%d %H:%M} .. {energy[-1][0]:%Y-%m-%d %H:%M} UTC,"
                  f" total {sum(v for _, v in energy):.3f} kWh")
            by_day: dict = {}
            for start, v in energy:
                by_day.setdefault(start.astimezone(api.TZ).date(), []).append(v)
            for d, vals in sorted(by_day.items()):
                print(f"     {d}: {len(vals):3d} values, {sum(vals):8.3f} kWh")
            print("     hourly buckets:", len(api.hourly(energy, "sum")))
        else:
            print("WARN no energy data in range (values arrive delayed; try SIMPLYGREEN_DAYS=7)")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
