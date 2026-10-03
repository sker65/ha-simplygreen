"""Client flow against a local mock of the portal (behaviour as observed in the browser)."""
import asyncio
import json
from datetime import date
from pathlib import Path

import aiohttp
from aiohttp import web


MET_LINES = json.loads((Path(__file__).parent / "fixtures" / "met_lines.json").read_text())


def _mock_app(payload, expired="redirect"):
    # expired: how getLoadProfile answers without a valid session.
    #   "redirect" -> 302 to page=expired (seen in the browser)
    #   "empty"    -> 200 with [] (seen from the real portal, 03.10.2026)
    async def handler(req):
        q = req.query
        if req.method == "POST":
            form = await req.post()
            assert req.headers["x-csrf-token"] == "tok1"
            ok = form.get("form[password]") == "pw"
            resp = web.json_response({"ok": ok})  # real response body format unknown
            if ok:
                resp.set_cookie("auth_x", "SESSION")
            return resp
        if q.get("page") == "expired":
            return web.Response(text="<h2>Sitzung Abgelaufen</h2>", content_type="text/html")
        if q.get("page") == "login":
            return web.Response(text='<meta name="csrf-token" content="tok1">', content_type="text/html")
        if q.get("page") == "loadprofile":
            if req.cookies.get("auth_x") != "SESSION":
                raise web.HTTPFound("/index.php?page=login")
            return web.Response(
                text='<meta name="csrf-token" content="tok2"><script>var mliids=["1234567"]</script>',
                content_type="text/html",
            )
        if q.get("ajaxService") == "getMetLines":
            if req.cookies.get("auth_x") != "SESSION" or req.headers.get("x-csrf-token") != "tok2":
                raise web.HTTPFound("/index.php?page=expired")
            return web.json_response(MET_LINES)
        if q.get("ajaxService") == "getLoadProfile":
            if req.cookies.get("auth_x") != "SESSION" or req.headers.get("x-csrf-token") != "tok2":
                if expired == "empty":
                    return web.json_response([])
                raise web.HTTPFound("/index.php?page=expired")
            assert json.loads(q["mliids"]) == ["1234567"]
            assert q["startDate"] == "01.10.2026" and q["resOpt"] == "ResOpt1"
            return web.json_response(payload)
        return web.Response(status=404)

    app = web.Application()
    app.router.add_route("*", "/index.php", handler)
    return app


async def _run(sg_api, payload, expired="redirect"):
    runner = web.AppRunner(_mock_app(payload, expired))
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    sg_api.BASE_URL = f"http://127.0.0.1:{port}/index.php"
    results = {}
    try:
        async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True)) as s:
            try:
                await sg_api.SimplyGreenClient(s, "u", "wrong").login()
                results["bad_login"] = "accepted"
            except sg_api.SimplyGreenAuthError:
                results["bad_login"] = "rejected"
        async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True)) as s:
            c = sg_api.SimplyGreenClient(s, "u", "pw")
            try:
                await c.get_load_profile("1234567", date(2026, 10, 1), date(2026, 10, 2))
                results["without_login"] = "accepted"
            except sg_api.SimplyGreenError:
                results["without_login"] = "rejected"
            await c.login()
            results["mliid"] = c.discovered_mliid
            results["first"] = await c.get_load_profile("1234567", date(2026, 10, 1), date(2026, 10, 2))
            s.cookie_jar.clear()  # simulate session expiry
            try:
                results["expired"] = await c.get_load_profile("1234567", date(2026, 10, 1), date(2026, 10, 2))
            except sg_api.SimplyGreenError:
                results["expired"] = "error"
            await c.login()  # what the coordinator does on every run
            results["after_relogin"] = await c.get_load_profile("1234567", date(2026, 10, 1), date(2026, 10, 2))
    finally:
        await runner.cleanup()
    return results


def test_login_fetch_and_relogin(sg_api, payload_15min):
    r = asyncio.run(_run(sg_api, payload_15min))
    assert r["bad_login"] == "rejected"
    assert r["without_login"] == "rejected"
    assert r["mliid"] == "1234567"
    assert len(r["first"]) == 2
    assert r["expired"] == "error"  # redirect to page=expired is detected
    assert len(r["after_relogin"]) == 2


def test_expired_session_answering_empty_list_is_fixed_by_relogin(sg_api, payload_15min):
    # The real portal answers an expired session with [] - indistinguishable from "no data".
    r = asyncio.run(_run(sg_api, payload_15min, expired="empty"))
    assert r["expired"] == []
    assert len(r["after_relogin"]) == 2


def test_parse_mliids(sg_api):
    assert sg_api.parse_mliids(MET_LINES) == ["1234567"]
    assert sg_api.parse_mliids([{"Lines": [{"Type": "OTHER", "MliId": "1"}]}, {}]) == []

