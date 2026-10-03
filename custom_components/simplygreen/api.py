"""Minimal client for the SimplyGreen (ex ENTEGA dynamisch) customer portal.

No Home Assistant imports here, so the parsing can be tested standalone.
"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import aiohttp

BASE_URL = "https://mein.simplygreen.de/index.php"
TZ = ZoneInfo("Europe/Berlin")
QUARTER = timedelta(minutes=15)

_CSRF_RES = (
    re.compile(r'name=["\']csrf-token["\'][^>]*content=["\']([^"\']+)', re.I),
    re.compile(r'content=["\']([^"\']+)["\'][^>]*name=["\']csrf-token', re.I),
)


class SimplyGreenError(Exception):
    """Generic portal error."""


class SimplyGreenAuthError(SimplyGreenError):
    """Login failed / credentials invalid."""


def parse_csrf(html: str) -> str | None:
    for rx in _CSRF_RES:
        if m := rx.search(html):
            return m.group(1)
    return None


def parse_mliids(payload: list) -> list[str]:
    """Extract the load-profile ids (Type "MLI") from a getMetLines response."""
    return [
        str(line["MliId"])
        for mec in payload
        for line in mec.get("Lines", [])
        if line.get("Type") == "MLI" and line.get("MliId")
    ]


def _is_logged_out(url: str) -> bool:
    return "page=login" in url or "page=expired" in url


class SimplyGreenClient:
    def __init__(self, session: aiohttp.ClientSession, username: str, password: str) -> None:
        self._session = session
        self._username = username
        self._password = password
        self._csrf: str | None = None
        self.discovered_mliid: str | None = None

    async def _get(self, params: dict, headers: dict | None = None) -> tuple[str, str]:
        async with self._session.get(BASE_URL, params=params, headers=headers,
                                     timeout=aiohttp.ClientTimeout(total=60)) as resp:
            resp.raise_for_status()
            return await resp.text(), str(resp.url)

    async def login(self) -> None:
        html, _ = await self._get({"page": "login"})
        token = parse_csrf(html)
        if not token:
            raise SimplyGreenError("CSRF token not found on login page")

        with aiohttp.MultipartWriter("form-data") as mp:
            for name, value in (("form[username]", self._username), ("form[password]", self._password)):
                part = mp.append(value)
                part.set_content_disposition("form-data", name=name)

        async with self._session.post(
            BASE_URL,
            params={"page": "login", "action": "login", "ajax": "y"},
            data=mp,
            headers={
                "x-csrf-token": token,
                "x-requested-with": "XMLHttpRequest",
                "accept": "application/json, text/javascript, */*; q=0.01",
                "referer": f"{BASE_URL}?page=login",
            },
            timeout=aiohttp.ClientTimeout(total=60),
        ) as resp:
            resp.raise_for_status()
            await resp.text()

        # Verify by opening the load profile page; a failed login lands on page=login.
        html, url = await self._get({"page": "loadprofile"})
        if _is_logged_out(url):
            raise SimplyGreenAuthError("Login rejected")
        self._csrf = parse_csrf(html) or token
        try:
            mliids = parse_mliids(await self.get_met_lines())
        except (SimplyGreenError, aiohttp.ClientError):
            mliids = []  # discovery is best effort; the user can enter the id manually
        self.discovered_mliid = mliids[0] if mliids else None

    async def get_met_lines(self) -> list:
        """Return the raw list of metering points with their lines (ids, descriptions)."""
        text, url = await self._get(
            {"ajaxService": "getMetLines", "ajax": "y"},
            headers={
                "x-csrf-token": self._csrf or "",
                "x-requested-with": "XMLHttpRequest",
                "referer": f"{BASE_URL}?page=loadprofile",
            },
        )
        try:
            data = json.loads(text)
        except ValueError:
            data = None
        if _is_logged_out(url) or not isinstance(data, list):
            raise SimplyGreenError("Unexpected response from getMetLines")
        return data

    async def get_load_profile(self, mliid: str, start: date, end: date, res: str = "ResOpt1") -> list:
        """Return the raw series list. ResOpt1 = 15 min, ResOpt2 = 1 h. Requires login().

        The portal answers an expired session with 200 and [] - the same as a range
        without data - so callers log in before every update instead of reusing a session.
        """
        if self._csrf is None:
            raise SimplyGreenError("Not logged in")
        text, url = await self._get(
            {
                "ajaxService": "getLoadProfile",
                "ajax": "y",
                "startDate": start.strftime("%d.%m.%Y"),
                "resOpt": res,
                "endDate": end.strftime("%d.%m.%Y"),
                "mliids": json.dumps([str(mliid)]),
            },
            headers={
                "x-csrf-token": self._csrf,
                "x-requested-with": "XMLHttpRequest",
                "referer": f"{BASE_URL}?page=loadprofile",
            },
        )
        try:
            data = None if _is_logged_out(url) else json.loads(text)
        except ValueError:
            data = None
        if not isinstance(data, list):
            raise SimplyGreenError("Unexpected response from getLoadProfile")
        return data


def series(payload: list, line_desc: str) -> list[tuple[int, float]]:
    for s in payload:
        if s.get("LineDesc") == line_desc:
            return [(int(ts), float(v)) for ts, v in s.get("data", []) if v is not None]
    return []


def _local_midnight_utc(d: date) -> datetime:
    return datetime(d.year, d.month, d.day, tzinfo=TZ).astimezone(timezone.utc)


def to_intervals(points: list[tuple[int, float]], step: timedelta = QUARTER) -> list[tuple[datetime, float]]:
    """Portal timestamps are interval ENDS in local wall-clock time, encoded as UTC epoch.

    Returns (interval_start_utc, value). Complete days (92/96/100 values) are laid
    out sequentially from local midnight, which resolves DST days unambiguously;
    incomplete days fall back to plain wall-clock conversion.
    """
    by_day: dict[date, list[tuple[datetime, float]]] = defaultdict(list)
    for ts, val in sorted(points, key=lambda p: p[0]):
        wall_start = datetime(1970, 1, 1) + timedelta(milliseconds=ts) - step
        by_day[wall_start.date()].append((wall_start, val))

    out: list[tuple[datetime, float]] = []
    for day, rows in sorted(by_day.items()):
        midnight = _local_midnight_utc(day)
        expected = (_local_midnight_utc(day + timedelta(days=1)) - midnight) // step
        if len(rows) == expected:
            out += [(midnight + i * step, v) for i, (_, v) in enumerate(rows)]
        else:
            out += [(w.replace(tzinfo=TZ).astimezone(timezone.utc), v) for w, v in rows]
    return out


def hourly(intervals: list[tuple[datetime, float]], how: str) -> dict[datetime, float]:
    buckets: dict[datetime, list[float]] = defaultdict(list)
    for start, val in intervals:
        buckets[start.replace(minute=0, second=0, microsecond=0)].append(val)
    if how == "sum":
        return {k: round(sum(v), 4) for k, v in sorted(buckets.items())}
    return {k: sum(v) / len(v) for k, v in sorted(buckets.items())}
