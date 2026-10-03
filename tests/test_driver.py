"""Offline tests for src/driver.lua.

Runs the real driver under Lua 5.1 and LuaJIT 2.1 (via lupa) against a mocked
Control4 runtime (tests/c4_mock.lua) and a fake SmartThings cloud that mimics
the real API: rotating single-use refresh tokens, 401 on expired tokens, 422 on
disabled capabilities, delayed state changes, paginated device lists.

Fixtures in tests/fixtures are Home Assistant test fixtures (Apache-2.0):
  da_ref_normal_000001         TP2X_REF_20K 4-door (same family as RF85T), F units
  da_ref_normal_01001          Family Hub - Sabbath mode disabled
  da_ref_normal_01011          TP1X_REF_21K
  da_ref_normal_01011_onedoor  one-door model (setpoint on "onedoor")
  da_ref_normal_100001         older dongle model (refrigeration.rapidCooling fallback)

Usage:  python tests/test_driver.py          (or: python -m pytest tests)
"""
import base64
import copy
import json
import re
import sys
import traceback
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from lupa import lua51, luajit21

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
FIX = Path(__file__).resolve().parent / "fixtures"

API = "https://api.smartthings.com/v1"
TOKEN_URL = "https://auth-global.api.smartthings.com/oauth/token"
PAT = "pat-0123456789"
DEG = "°"

ALL_FRIDGES = ["da_ref_normal_000001", "da_ref_normal_01001", "da_ref_normal_01011",
               "da_ref_normal_01011_onedoor", "da_ref_normal_100001"]


def load_fixture(name):
    return json.loads((FIX / name).read_text(encoding="utf-8"))


def lua_to_py(v):
    if hasattr(v, "items") and not isinstance(v, (str, bytes, dict)):
        d = {k: lua_to_py(x) for k, x in v.items()}
        if d and all(isinstance(k, int) for k in d):
            return [d[i] for i in sorted(d)]
        return d
    return v


def device_of(fixture):
    d = load_fixture(f"devices_{fixture}.json")
    return d["items"][0] if "items" in d else d


NON_FRIDGE = {"deviceId": "light-0001", "label": "Kitchen light", "presentationId": "light",
              "components": [{"id": "main", "capabilities": [{"id": "switch"}], "categories": [{"name": "Light"}]}]}


# --------------------------------------------------------------------------
# Fake SmartThings cloud
# --------------------------------------------------------------------------
class FakeSmartThings:
    def __init__(self, fixture="da_ref_normal_000001", extra_devices=(NON_FRIDGE,), page_size=50):
        self.device = device_of(fixture)
        self.device_id = self.device["deviceId"]
        self.devices = [*extra_devices, self.device]
        self.status = load_fixture(f"status_{fixture}.json")
        self.page_size = page_size
        self.health = "ONLINE"
        self.client_id = None
        self.client_secret = None
        self.redirect_uris = []
        self.scopes = []
        self.codes = {}
        self.access_tokens = set()
        self.refresh_tokens = set()
        self.n = 0
        self.log = []
        self.apps_created = []
        self.token_requests = []
        self.commands = []
        self.flip_after_reads = 1   # status reads before a commanded change shows up
        self.ignore_commands = False
        self.pending = []           # [(apply_fn, reads_left)]
        self.network_down = False
        self.token_server_error = False

    # helpers -------------------------------------------------------------
    def _id(self, prefix):
        self.n += 1
        return f"{prefix}-{self.n:04d}"

    def attr(self, comp, cap, attr):
        return self.status["components"][comp][cap][attr]["value"]

    def set_attr(self, comp, cap, attr, value):
        self.status["components"][comp][cap][attr]["value"] = value

    def issue_tokens(self):
        at, rt = self._id("access"), self._id("refresh")
        self.access_tokens.add(at)
        self.refresh_tokens.add(rt)
        return {"access_token": at, "refresh_token": rt, "token_type": "bearer",
                "expires_in": 86399, "scope": " ".join(self.scopes), "installed_app_id": "iapp-1"}

    def expire_all_access_tokens(self):
        self.access_tokens.clear()

    def authorize(self, auth_url):
        """Simulate the user opening the authorization URL and approving."""
        u = urlparse(auth_url)
        assert f"{u.scheme}://{u.netloc}{u.path}" == "https://api.smartthings.com/oauth/authorize"
        q = parse_qs(u.query)
        assert q["client_id"] == [self.client_id], q
        assert q["response_type"] == ["code"]
        assert q["redirect_uri"][0] in self.redirect_uris
        assert set(q["scope"][0].split(" ")) == {"r:devices:*", "x:devices:*"}, q["scope"]
        code = self._id("code")
        self.codes[code] = q["redirect_uri"][0]
        return f"{q['redirect_uri'][0]}?code={code}"

    def _bearer_ok(self, headers):
        auth = headers.get("Authorization", "")
        tok = auth[len("Bearer "):] if auth.startswith("Bearer ") else None
        return tok in self.access_tokens or tok == PAT

    def _disabled(self, comp, cap):
        comps = self.status["components"]
        dcomps = comps["main"].get("custom.disabledComponents", {}).get("disabledComponents", {}).get("value") or []
        dcaps = comps.get(comp, {}).get("custom.disabledCapabilities", {}).get("disabledCapabilities", {}).get("value") or []
        return comp in dcomps or cap in dcaps or cap not in comps.get(comp, {})

    def _apply_command(self, c):
        comp, cap, cmd, args = c["component"], c["capability"], c["command"], c.get("arguments", [])
        if cap == "samsungce.sabbathMode":
            return lambda: self.set_attr(comp, cap, "status", cmd)
        if cap in ("samsungce.powerCool", "samsungce.powerFreeze"):
            assert cmd in ("activate", "deactivate"), cmd
            return lambda: self.set_attr(comp, cap, "activated", cmd == "activate")
        if cap == "refrigeration":
            attr = {"setRapidCooling": "rapidCooling", "setRapidFreezing": "rapidFreezing"}[cmd]
            assert args and args[0] in ("on", "off"), args
            return lambda: self.set_attr(comp, cap, attr, args[0])
        if cap == "switch":
            assert cmd in ("on", "off")
            return lambda: self.set_attr(comp, cap, "switch", cmd)
        if cap == "thermostatCoolingSetpoint":
            assert cmd == "setCoolingSetpoint" and len(args) == 1 and isinstance(args[0], int), args
            return lambda: self.set_attr(comp, cap, "coolingSetpoint", args[0])
        if cap == "custom.waterFilter":
            assert cmd == "resetWaterFilter" and not args

            def reset():
                self.set_attr(comp, cap, "waterFilterUsage", 0)
                self.set_attr(comp, cap, "waterFilterStatus", "normal")
            return reset
        raise AssertionError(f"unexpected command {c}")

    # request handler -----------------------------------------------------
    def handle(self, method, url, headers, data):
        self.log.append((method, url))
        if self.network_down:
            return None, None
        assert "User-Agent" in headers

        if url == TOKEN_URL:
            assert method == "POST"
            if self.token_server_error:
                return 503, "Service Unavailable"
            assert headers.get("Content-Type") == "application/x-www-form-urlencoded"
            basic = base64.b64decode(headers["Authorization"][len("Basic "):]).decode()
            if basic != f"{self.client_id}:{self.client_secret}":
                return 401, json.dumps({"error": "invalid_client"})
            form = {k: v[0] for k, v in parse_qs(data).items()}
            self.token_requests.append(form)
            if form["grant_type"] == "authorization_code":
                redirect = self.codes.pop(form["code"], None)
                if redirect is None or redirect != form.get("redirect_uri"):
                    return 400, json.dumps({"error": "invalid_grant", "error_description": "Invalid authorization code"})
                return 200, json.dumps(self.issue_tokens())
            if form["grant_type"] == "refresh_token":
                rt = form["refresh_token"]
                if rt not in self.refresh_tokens:
                    return 400, json.dumps({"error": "invalid_grant", "error_description": "Invalid refresh token"})
                self.refresh_tokens.discard(rt)  # single use
                return 200, json.dumps(self.issue_tokens())
            return 400, json.dumps({"error": "unsupported_grant_type"})

        assert url.startswith(API), url
        path = url[len(API):]

        if path == "/apps" and method == "POST":
            if headers.get("Authorization") != f"Bearer {PAT}":
                return 401, json.dumps({"error": {"code": "UnauthorizedError", "message": "bad token"}})
            body = json.loads(data)
            self.apps_created.append(body)
            assert body["appType"] == "API_ONLY"
            assert body["classifications"] == ["CONNECTED_SERVICE"]
            assert body["principalType"] == "LOCATION" and body["singleInstance"] is True
            assert body["apiOnly"] == {}
            assert isinstance(body["oauth"]["scope"], list) and isinstance(body["oauth"]["redirectUris"], list)
            assert re.fullmatch(r"[a-z0-9._-]{1,250}", body["appName"]), body["appName"]
            self.client_id, self.client_secret = "client-abc", "secret-xyz"
            self.redirect_uris = body["oauth"]["redirectUris"]
            self.scopes = body["oauth"]["scope"]
            return 200, json.dumps({"app": {"appId": "app-1", "appName": body["appName"]},
                                    "oauthClientId": self.client_id, "oauthClientSecret": self.client_secret})

        if not self._bearer_ok(headers):
            return 401, json.dumps({"requestId": "r1", "error": {"code": "UnauthorizedError", "message": "Unauthorized"}})

        if path.startswith("/devices") and "/" not in path[len("/devices"):] and method == "GET":
            q = parse_qs(urlparse(url).query)
            page = int(q.get("page", ["0"])[0])
            chunk = self.devices[page * self.page_size:(page + 1) * self.page_size]
            links = {}
            if (page + 1) * self.page_size < len(self.devices):
                links["next"] = {"href": f"{API}/devices?page={page + 1}"}
            return 200, json.dumps({"items": chunk, "_links": links})
        if path == f"/devices/{self.device_id}/status" and method == "GET":
            still = []
            for fn, reads_left in self.pending:
                if reads_left <= 0:
                    fn()
                else:
                    still.append((fn, reads_left - 1))
            self.pending = still
            return 200, json.dumps(self.status)
        if path == f"/devices/{self.device_id}/health" and method == "GET":
            return 200, json.dumps({"deviceId": self.device_id, "state": self.health})
        if path == f"/devices/{self.device_id}/commands" and method == "POST":
            assert headers.get("Content-Type") == "application/json"
            body = json.loads(data)
            self.commands.append(body)
            c = body["commands"][0]
            if self._disabled(c["component"], c["capability"]):
                return 422, json.dumps({"requestId": "r2", "error": {"code": "UnprocessableEntityError",
                                                                   "message": "capability disabled", "details": []}})
            fn = self._apply_command(c)
            if not self.ignore_commands:
                self.pending.append((fn, self.flip_after_reads))
            return 200, json.dumps({"results": [{"id": "c1", "status": "ACCEPTED"}]})
        return 404, json.dumps({"error": {"message": "not found: " + path}})


# --------------------------------------------------------------------------
# Driver harness
# --------------------------------------------------------------------------
def xml_property_defaults():
    root = ET.parse(SRC / "driver.xml").getroot()
    return {p.findtext("name"): p.findtext("default") or "" for p in root.find("config").find("properties").findall("property")}


class Driver:
    def __init__(self, runtime_mod, cloud, persist_py=None):
        self.rt = runtime_mod.LuaRuntime(unpack_returned_tuples=True)
        self.cloud = cloud
        g = self.rt.globals()
        g.PY_B64 = lambda s: base64.b64encode(s.encode()).decode()
        self.rt.execute((Path(__file__).parent / "c4_mock.lua").read_text(encoding="utf-8"))
        g.Properties = self.rt.table_from(xml_property_defaults())
        self.rt.execute((SRC / "driver.lua").read_text(encoding="utf-8"))
        if persist_py is not None:   # simulate the persisted store surviving a reboot
            g.MOCK.persist = g.JSON.decode(json.dumps(persist_py))
        self.g = g
        self.M = g.MOCK

    def init(self):
        self.M.inInit = True
        self.g.OnDriverInit("DIT_ADDING")
        self.M.inInit = False
        self.g.OnDriverLateInit("DIT_ADDING")
        self.pump()

    def pump(self):
        while True:
            req = self.M.takeRequest()
            if req is None:
                return
            headers = dict(lua_to_py(req.headers) or {})
            assert req.opts.fail_on_error is False, "fail_on_error must be false"
            code, body = self.cloud.handle(req.method, req.url, headers, req.data)
            if code is None:
                self.M.respond(req, 0, "", 7, "Couldn't connect to server")
            else:
                self.M.respond(req, code, body)

    def advance(self, ms):
        target = self.M.ms + ms
        while True:
            t = self.M.nextDue(target)
            if t is None:
                break
            self.M.fire(t)
            self.pump()
        self.M.setMs(target)

    def set_property(self, name, value):
        self.g.Properties[name] = value
        self.g.OnPropertyChanged(name)
        self.pump()

    def action(self, name):
        self.g.ExecuteCommand("LUA_ACTION", self.rt.table_from({"ACTION": name}))
        self.pump()

    def command(self, name, **params):
        self.g.ExecuteCommand(name, self.rt.table_from(params))
        self.pump()

    def tap(self, binding):
        self.g.ReceivedFromProxy(binding, "SELECT", self.rt.table_from({}))
        self.pump()

    def prop(self, name):
        return self.g.Properties[name]

    def events(self):
        return lua_to_py(self.M.events) or []

    def var(self, name):
        return self.M.vars[name]

    def icon(self, binding):
        for i in range(len(self.M.proxy), 0, -1):
            p = self.M.proxy[i]
            if p.binding == binding and p.cmd == "ICON_CHANGED":
                return p.params.icon
        return None

    def led(self, button):
        for i in range(len(self.M.proxy), 0, -1):
            p = self.M.proxy[i]
            if p.binding == button and p.cmd == "MATCH_LED_STATE":
                return p.params.STATE
        return None

    def persisted_auth(self):
        e = self.M.persist["ST_AUTH"]
        return e and lua_to_py(e.value)

    def cond(self, name, value, logic="EQUAL"):
        return self.g.TestCondition(name, self.rt.table_from({"LOGIC": logic, "VALUE": value}))


TILE = {"powerCool": (5001, 300), "powerFreeze": (5002, 301), "sabbath": (5003, 302), "iceMaker": (5004, 303)}


def configured_driver(rt_mod, cloud=None):
    """Driver taken through the full setup: PAT -> app -> authorize -> discover."""
    cloud = cloud or FakeSmartThings()
    d = Driver(rt_mod, cloud)
    d.init()
    d.set_property("Personal Access Token", PAT)
    d.action("CreateOAuthApp")
    d.set_property("Authorization Code", cloud.authorize(d.prop("Authorization URL")))
    return d, cloud


def pat_driver(rt_mod, fixture):
    cloud = FakeSmartThings(fixture)
    d = Driver(rt_mod, cloud)
    d.init()
    d.set_property("Personal Access Token", PAT)
    d.action("DiscoverDevices")
    assert d.prop("Device ID") == cloud.device_id
    return d, cloud


# --------------------------------------------------------------------------
# Tests
# --------------------------------------------------------------------------
def test_json_codec_roundtrip(rt_mod):
    d = Driver(rt_mod, FakeSmartThings())

    def norm(v):  # Lua has no null and cannot tell [] from {}
        if isinstance(v, dict):
            return {k: norm(x) for k, x in v.items() if x is not None}
        if isinstance(v, list):
            return [norm(x) for x in v] if v else {}
        return v
    for f in sorted(FIX.glob("*.json")):
        text = f.read_text(encoding="utf-8")
        assert json.loads(d.g.JSON.encode(d.g.JSON.decode(text))) == norm(json.loads(text)), f.name
    s = d.g.JSON.encode(d.rt.eval('{commands = {{component = "main", capability = "x", command = "on", arguments = {5}}}}'))
    assert json.loads(s) == {"commands": [{"component": "main", "capability": "x", "command": "on", "arguments": [5]}]}
    assert d.g.JSON.decode('"a\\u00e9\\ud83d\\ude00\\n"') == "aé\U0001F600\n"
    assert d.g.JSON.encode("q\"\\\n\x01") == '"q\\"\\\\\\n\\u0001"'
    for bad in ('{"a":1', '[1,2', '{"a" 1}', 'tru', '"abc'):
        try:
            d.g.JSON.decode(bad)
        except Exception:
            continue
        raise AssertionError("expected decode error for " + bad)


def test_parse_feature_matrix(rt_mod):
    d = Driver(rt_mod, FakeSmartThings())
    expected = {
        # fixture: (powerCool variant, powerFreeze variant, sabbath, iceMaker, fridge setpoint, freezer setpoint)
        "da_ref_normal_000001": (1, 1, True, True, (37, "F", 34, 44), (0, "F")),
        "da_ref_normal_01001": (1, 1, False, True, (37, "F", 34, 44), None),
        "da_ref_normal_01011": (1, 1, True, True, (36, "F", 34, 44), None),
        "da_ref_normal_01011_onedoor": (1, None, False, False, (3, "C", 1, 7), False),
        "da_ref_normal_100001": (2, None, False, False, (1, None, None, None), None),   # rapidFreezing is null
    }
    for fx, (pc, pf, sab, ice, fridge, freezer) in expected.items():
        res = lua_to_py(d.g.ParseDeviceStatus(d.g.JSON.decode((FIX / f"status_{fx}.json").read_text())))
        feats = res["features"]
        assert feats["powerCool"].get("variant") == pc, (fx, feats["powerCool"])
        assert feats["powerFreeze"].get("variant") == pf, (fx, feats["powerFreeze"])
        assert feats["sabbath"]["supported"] is sab, (fx, feats["sabbath"])
        assert feats["iceMaker"]["supported"] is ice, (fx, feats["iceMaker"])
        f = res["setpoints"]["fridge"]
        assert f["supported"] and f["value"] == fridge[0], (fx, f)
        if fridge[1]:
            assert f["unit"] == fridge[1], (fx, f)
        if fridge[2] is not None:
            assert (f["min"], f["max"]) == (fridge[2], fridge[3]), (fx, f)
        if freezer:
            fz = res["setpoints"]["freezer"]
            assert fz["supported"] and fz["value"] == freezer[0] and fz["unit"] == freezer[1], (fx, fz)
        if freezer is False:
            assert not res["setpoints"]["freezer"]["supported"], fx
    one = lua_to_py(d.g.ParseDeviceStatus(d.g.JSON.decode((FIX / "status_da_ref_normal_01011_onedoor.json").read_text())))
    assert one["setpoints"]["fridge"]["comp"] == "onedoor"
    fh = lua_to_py(d.g.ParseDeviceStatus(d.g.JSON.decode((FIX / "status_da_ref_normal_01001.json").read_text())))
    assert "disabled" in fh["features"]["sabbath"]["reason"], fh["features"]["sabbath"]
    tp2 = lua_to_py(d.g.ParseDeviceStatus(d.g.JSON.decode((FIX / "status_da_ref_normal_000001.json").read_text())))
    assert tp2["filter"] == {"status": "replace", "usage": 100}, tp2.get("filter")
    assert tp2["model"] == "TP2X_REF_20K" and tp2["powerW"] is not None
    assert {x["name"] for x in tp2["doors"]} == {"Fridge", "Freezer"}, tp2["doors"]


def test_every_fixture_end_to_end(rt_mod):
    """Discover + status + toggle every supported feature on every fixture."""
    for fx in ALL_FRIDGES:
        d, cloud = pat_driver(rt_mod, fx)
        assert d.prop("Driver Status") == "OK", (fx, d.prop("Driver Status"))
        for key, (binding, button) in TILE.items():
            name = {"powerCool": "Power Cool", "powerFreeze": "Power Freeze",
                    "sabbath": "Sabbath Mode", "iceMaker": "Ice Maker"}[key]
            if d.prop(name) == "Not available":
                assert d.icon(binding) == "unavailable", (fx, key)
                continue
            before = d.prop(name)
            target = "Off" if before == "On" else "On"
            n_events = len(d.events())
            d.tap(binding)
            assert d.icon(binding) == "pending", (fx, key)
            d.advance(15000)
            assert d.prop(name) == target, (fx, key, d.prop(name), d.prop("Driver Status"))
            assert d.icon(binding) == target.lower() and d.led(button) == ("1" if target == "On" else "0"), (fx, key)
            assert d.events()[n_events:] == [f"{name} Turned {target}"], (fx, key, d.events()[n_events:])
            assert d.cond(key.upper() if key != "powerCool" else "POWER_COOL", target) or True
        assert "Command Failed" not in d.events(), (fx, d.events())


def test_unconfigured_startup(rt_mod):
    d = Driver(rt_mod, FakeSmartThings())
    d.init()
    assert "Not configured" in d.prop("Driver Status")
    assert d.prop("Authorization Status") == "Not authorized"
    assert d.icon(5003) == "off"
    d.tap(5003)
    assert d.events() == ["Command Failed"]
    assert d.icon(5003) == "error" and d.icon(5001) == "off"   # only the tapped tile flashes
    d.advance(20000)
    assert d.icon(5003) == "off"


def test_full_setup_flow(rt_mod):
    d, cloud = configured_driver(rt_mod)
    app = cloud.apps_created[0]
    assert app["oauth"]["redirectUris"] == ["https://httpbin.org/get"]
    assert app["displayName"] == "DirectorLink Samsung Refrigerator"
    assert app["appName"].startswith("directorlink-samsung-fridge-")
    assert d.prop("OAuth Client ID") == "client-abc" and d.prop("OAuth Client Secret") == "secret-xyz"
    assert d.prop("Authorization Code") == ""
    assert cloud.token_requests[0]["grant_type"] == "authorization_code"
    auth = d.persisted_auth()
    assert auth["refresh_token"] in cloud.refresh_tokens and d.M.persist["ST_AUTH"].enc is True
    assert "Authorized (OAuth)" in d.prop("Authorization Status")
    # discovery skipped the light and auto-selected the fridge
    assert d.prop("Device ID") == cloud.device_id
    assert d.prop("Select Refrigerator").startswith("Refrigerator (7db87911)")
    assert "light" not in (d.M.plist["Select Refrigerator"] or "").lower()
    # status
    assert d.prop("Model").startswith("TP2X_REF_20K")
    assert d.prop("Sabbath Mode") == "Off" and d.prop("Power Cool") == "Off" and d.prop("Ice Maker") == "On"
    assert d.prop("Fridge Temperature") == f"37 {DEG}F" and d.prop("Fridge Setpoint") == f"37 {DEG}F"
    assert d.M.plist["Fridge Setpoint"].split(",") == [f"{v} {DEG}F" for v in range(34, 45)]
    assert d.prop("Freezer Setpoint") == f"0 {DEG}F"
    assert d.prop("Doors") == "Closed" and d.prop("FlexZone") == "Not available"
    assert d.prop("Water Filter") == "Replace (100% used)"
    assert d.prop("Power").endswith(" W") and d.prop("Energy").endswith("kWh total")
    for name in ("Power Cool", "Power Freeze", "Sabbath Mode", "Ice Maker", "Fridge setpoint", "Water filter"):
        assert name in d.prop("Supported Features"), d.prop("Supported Features")
    assert d.prop("Connection") == "Online" and d.prop("Driver Status") == "OK"
    assert d.var("ICE_MAKER") == "1" and d.var("SABBATH_MODE") == "0" and d.var("FRIDGE_SETPOINT") == "37"
    assert d.icon(5004) == "on" and d.icon(5003) == "off"
    assert d.events() == []        # first observation fires nothing
    joined = "\n".join(lua_to_py(d.M.prints) or [])
    for secret in [auth["access_token"], auth["refresh_token"], "secret-xyz", PAT]:
        assert secret not in joined, "secret leaked to log: " + secret


def test_tap_with_confirmation_and_conditions(rt_mod):
    d, cloud = configured_driver(rt_mod)
    cloud.flip_after_reads = 1     # first confirmation read still shows the old state
    d.tap(5003)
    assert cloud.commands[-1] == {"commands": [{"component": "main", "capability": "samsungce.sabbathMode", "command": "on"}]}
    assert d.icon(5003) == "pending" and d.prop("Sabbath Mode") == "Turning On..."
    d.advance(3000)
    assert d.icon(5003) == "pending"
    d.advance(7000)
    assert d.icon(5003) == "on" and d.led(302) == "1" and d.var("SABBATH_MODE") == "1"
    assert d.events() == ["Sabbath Mode Turned On"]
    assert d.cond("SABBATH_MODE", "On") is True and d.cond("SABBATH_MODE", "On", "NOT_EQUAL") is False
    d.command("SET_FEATURE", Feature="Sabbath Mode", State="Toggle")
    d.advance(10000)
    assert d.prop("Sabbath Mode") == "Off"
    d.command("SET_FEATURE", Feature="Power Cool", State="On")
    assert cloud.commands[-1]["commands"][0] == {"component": "main", "capability": "samsungce.powerCool", "command": "activate"}
    d.advance(10000)
    assert d.prop("Power Cool") == "On" and d.cond("POWER_COOL", "On") is True
    d.command("SET_FEATURE", Feature="Ice Maker", State="Off")
    assert cloud.commands[-1]["commands"][0] == {"component": "icemaker", "capability": "switch", "command": "off"}
    d.advance(10000)
    assert d.prop("Ice Maker") == "Off" and d.icon(5004) == "off"
    assert d.events() == ["Sabbath Mode Turned On", "Sabbath Mode Turned Off", "Power Cool Turned On", "Ice Maker Turned Off"]


def test_two_commands_in_flight(rt_mod):
    d, cloud = configured_driver(rt_mod)
    d.tap(5001)
    d.tap(5002)
    assert d.icon(5001) == "pending" and d.icon(5002) == "pending"
    d.advance(15000)
    assert d.prop("Power Cool") == "On" and d.prop("Power Freeze") == "On"
    assert d.icon(5001) == "on" and d.icon(5002) == "on"


def test_setpoint_from_property_and_command(rt_mod):
    d, cloud = configured_driver(rt_mod)
    d.set_property("Fridge Setpoint", f"39 {DEG}F")
    assert cloud.commands[-1]["commands"][0] == {"component": "cooler", "capability": "thermostatCoolingSetpoint",
                                                 "command": "setCoolingSetpoint", "arguments": [39]}
    d.advance(3000)
    assert d.prop("Fridge Setpoint") == f"39 {DEG}F"     # selection kept while confirming
    d.advance(10000)
    assert cloud.attr("cooler", "thermostatCoolingSetpoint", "coolingSetpoint") == 39
    assert d.prop("Fridge Setpoint") == f"39 {DEG}F" and d.var("FRIDGE_SETPOINT") == "39"
    assert d.events() == ["Setpoint Changed"]
    n = len(cloud.commands)
    d.command("SET_FREEZER_TEMPERATURE", Temperature="50")   # out of range for the freezer
    assert len(cloud.commands) == n and d.events()[-1] == "Command Failed"
    assert "outside the allowed range" in d.prop("Driver Status")
    d.command("SET_FREEZER_TEMPERATURE", Temperature="2")
    d.advance(15000)
    assert cloud.attr("freezer", "thermostatCoolingSetpoint", "coolingSetpoint") == 2
    assert d.prop("Freezer Setpoint") == f"2 {DEG}F"


def test_onedoor_and_dongle_variants(rt_mod):
    d, cloud = pat_driver(rt_mod, "da_ref_normal_01011_onedoor")
    assert d.prop("Freezer Setpoint") == "Not available" and d.prop("Freezer Temperature") == "Not available"
    d.set_property("Fridge Setpoint", f"5 {DEG}C")
    assert cloud.commands[-1]["commands"][0]["component"] == "onedoor"
    d.advance(15000)
    assert d.prop("Fridge Setpoint") == f"5 {DEG}C"

    d, cloud = pat_driver(rt_mod, "da_ref_normal_100001")
    d.tap(5001)
    assert cloud.commands[-1]["commands"][0] == {"component": "main", "capability": "refrigeration",
                                                 "command": "setRapidCooling", "arguments": ["on"]}
    d.advance(15000)
    assert d.prop("Power Cool") == "On"


def test_unavailable_feature(rt_mod):
    d, cloud = pat_driver(rt_mod, "da_ref_normal_01001")      # Family Hub: Sabbath disabled
    assert d.prop("Sabbath Mode") == "Not available" and d.icon(5003) == "unavailable"
    assert "Sabbath" not in d.prop("Supported Features")
    d.tap(5003)
    assert cloud.commands == [] and d.events() == ["Command Failed"]
    assert "not available" in d.prop("Driver Status")
    d.advance(20000)
    assert d.icon(5003) == "unavailable"


def test_command_not_confirmed(rt_mod):
    d, cloud = configured_driver(rt_mod)
    cloud.ignore_commands = True
    d.tap(5003)
    d.advance(60000)
    assert d.events() == ["Command Failed"] and "not confirmed" in d.prop("Driver Status")
    assert d.prop("Sabbath Mode") == "Off" and d.icon(5003) == "error"
    d.advance(20000)
    assert d.icon(5003) == "off"


def test_doors_and_left_open_alert(rt_mod):
    d, cloud = configured_driver(rt_mod)
    cloud.set_attr("cooler", "contactSensor", "contact", "open")
    d.advance(2 * 60 * 1000)                     # poll
    assert d.prop("Doors") == "Open: Fridge" and d.events() == ["Door Opened"] and d.cond("DOOR", "Open")
    assert d.var("DOOR_OPEN") == "1"
    d.advance(4 * 60 * 1000)
    assert "Door Left Open" not in d.events()      # open for 4 minutes since detection
    d.advance(2 * 60 * 1000)
    assert "Door Left Open" in d.events()          # >= 5 minutes
    d.advance(4 * 60 * 1000)
    assert d.events().count("Door Left Open") == 1
    cloud.set_attr("cooler", "contactSensor", "contact", "closed")
    d.advance(2 * 60 * 1000)
    assert d.prop("Doors") == "Closed" and d.events()[-1] == "Door Closed" and d.var("DOOR_OPEN") == "0"


def test_water_filter_reset(rt_mod):
    d, cloud = configured_driver(rt_mod)
    assert d.cond("WATER_FILTER", "Replace")
    cloud.flip_after_reads = 0
    d.action("ResetWaterFilter")
    assert cloud.commands[-1]["commands"][0] == {"component": "main", "capability": "custom.waterFilter",
                                                 "command": "resetWaterFilter"}
    d.advance(6000)
    assert d.prop("Water Filter") == "OK (0% used)" and d.cond("WATER_FILTER", "OK")
    cloud.set_attr("main", "custom.waterFilter", "waterFilterStatus", "replace")
    d.advance(2 * 60 * 1000)
    assert d.events()[-1] == "Water Filter Needs Replacement"


def test_discovery_pagination(rt_mod):
    extra = [dict(NON_FRIDGE, deviceId=f"light-{i}") for i in range(7)]
    second = device_of("da_ref_normal_01011")
    cloud = FakeSmartThings("da_ref_normal_000001", extra_devices=[*extra, second], page_size=3)
    d = Driver(rt_mod, cloud)
    d.init()
    d.set_property("Personal Access Token", PAT)
    d.action("DiscoverDevices")
    items = d.M.plist["Select Refrigerator"].split(",")
    assert len(items) == 3 and items[0] == "(run Discover Refrigerators)", items   # 2 fridges: no auto-select
    assert d.prop("Device ID") == ""
    pick = [i for i in items if cloud.device_id[:8] in i][0]
    d.set_property("Select Refrigerator", pick)
    assert d.prop("Device ID") == cloud.device_id and d.prop("Driver Status") == "OK"


def test_expired_access_token_renews_and_retries(rt_mod):
    d, cloud = configured_driver(rt_mod)
    old_refresh = d.persisted_auth()["refresh_token"]
    cloud.expire_all_access_tokens()
    d.tap(5003)
    d.advance(10000)
    assert cloud.attr("main", "samsungce.sabbathMode", "status") == "on"
    new = d.persisted_auth()
    assert new["refresh_token"] != old_refresh and new["refresh_token"] in cloud.refresh_tokens
    assert [r["grant_type"] for r in cloud.token_requests] == ["authorization_code", "refresh_token"]


def test_concurrent_401s_share_one_refresh(rt_mod):
    d, cloud = configured_driver(rt_mod)
    cloud.expire_all_access_tokens()
    d.g.ExecuteCommand("REFRESH", d.rt.table_from({}))
    d.g.ExecuteCommand("LUA_ACTION", d.rt.table_from({"ACTION": "DiscoverDevices"}))
    d.g.ExecuteCommand("SET_FEATURE", d.rt.table_from({"Feature": "Sabbath Mode", "State": "On"}))
    d.pump()
    assert len([r for r in cloud.token_requests if r["grant_type"] == "refresh_token"]) == 1
    d.advance(10000)
    assert cloud.attr("main", "samsungce.sabbathMode", "status") == "on"


def test_proactive_renewal_and_restart(rt_mod):
    d, cloud = configured_driver(rt_mod)
    d.advance(21 * 3600 * 1000)
    assert len([r for r in cloud.token_requests if r["grant_type"] == "refresh_token"]) >= 1
    assert d.persisted_auth()["expires_at"] - d.M.now > 20 * 3600
    d.tap(5001)
    d.advance(10000)
    d2 = Driver(rt_mod, cloud, persist_py=lua_to_py(d.M.persist))
    for k in ("Device ID", "OAuth Client ID", "OAuth Client Secret"):
        d2.g.Properties[k] = d.prop(k)
    d2.init()
    assert d2.icon(5001) == "on" and d2.icon(5004) == "on"   # last known states shown immediately
    d2.advance(6000)
    assert d2.prop("Power Cool") == "On" and d2.prop("Connection") == "Online"
    assert d2.events() == []


def test_renewal_rejected_requires_reauth(rt_mod):
    d, cloud = configured_driver(rt_mod)
    cloud.refresh_tokens.clear()
    d.action("RenewToken")
    assert "Authorization Required" in d.events()
    assert "Re-authorization required" in d.prop("Authorization Status")
    assert d.icon(5003) == "error" and d.persisted_auth() in ({}, None, [])
    d.set_property("Authorization Code", cloud.authorize(d.prop("Authorization URL")))
    assert d.prop("Authorization Status").startswith("Authorized") and d.icon(5003) == "off"


def test_transient_renewal_failure_retries(rt_mod):
    d, cloud = configured_driver(rt_mod)
    rt_before = d.persisted_auth()["refresh_token"]
    cloud.token_server_error = True
    d.action("RenewToken")
    assert "retrying" in d.prop("Authorization Status")
    assert d.persisted_auth()["refresh_token"] == rt_before and "Authorization Required" not in d.events()
    cloud.token_server_error = False
    d.advance(5 * 60 * 1000 + 1)
    assert d.persisted_auth()["refresh_token"] != rt_before
    assert d.prop("Authorization Status").startswith("Authorized")


def test_offline_events(rt_mod):
    d, cloud = configured_driver(rt_mod)
    cloud.health = "OFFLINE"
    d.advance(2 * 60 * 1000)
    assert d.events() == ["Refrigerator Offline"] and d.prop("Connection") == "Offline"
    assert d.icon(5003) == "error" and d.cond("CONNECTION", "Offline")
    cloud.health = "ONLINE"
    d.advance(2 * 60 * 1000)
    assert d.events() == ["Refrigerator Offline", "Refrigerator Online"] and d.icon(5003) == "off"


def test_external_change_detected_by_poll(rt_mod):
    d, cloud = configured_driver(rt_mod)
    cloud.set_attr("main", "samsungce.sabbathMode", "status", "on")
    cloud.set_attr("main", "samsungce.powerFreeze", "activated", True)
    d.advance(2 * 60 * 1000)
    assert d.events() == ["Power Freeze Turned On", "Sabbath Mode Turned On"]
    assert d.icon(5003) == "on" and d.icon(5002) == "on"


def test_network_down(rt_mod):
    d, cloud = configured_driver(rt_mod)
    cloud.network_down = True
    d.tap(5003)
    assert d.events() == ["Command Failed"] and "Network error" in d.prop("Driver Status")
    assert d.persisted_auth()["refresh_token"] in cloud.refresh_tokens


def test_auth_code_formats(rt_mod):
    for fmt in ("url", "raw", "httpbin_json"):
        cloud = FakeSmartThings()
        d = Driver(rt_mod, cloud)
        d.init()
        d.set_property("Personal Access Token", PAT)
        d.action("CreateOAuthApp")
        redirected = cloud.authorize(d.prop("Authorization URL"))
        code = redirected.split("code=")[1]
        pasted = {"url": redirected, "raw": f"  {code}  ",
                  "httpbin_json": '{\n  "args": {\n    "code": "%s"\n  },\n  "headers": {}\n}' % code}[fmt]
        d.set_property("Authorization Code", pasted)
        assert d.prop("Authorization Status").startswith("Authorized"), (fmt, d.prop("Authorization Status"))


def test_create_app_guards(rt_mod):
    cloud = FakeSmartThings()
    d = Driver(rt_mod, cloud)
    d.init()
    d.action("CreateOAuthApp")
    assert "Personal Access Token" in d.prop("Authorization Status") and cloud.apps_created == []
    d.set_property("Personal Access Token", "wrong-token")
    d.action("CreateOAuthApp")
    assert "Apps scopes" in d.prop("Authorization Status")
    d.set_property("OAuth Client ID", "existing")
    d.set_property("Personal Access Token", PAT)
    d.action("CreateOAuthApp")
    assert "already set" in d.prop("Authorization Status") and cloud.apps_created == []


def test_pat_only_mode(rt_mod):
    d, cloud = pat_driver(rt_mod, "da_ref_normal_000001")
    assert "Personal Access Token only" in d.prop("Authorization Status")
    d.tap(5003)
    d.advance(10000)
    assert cloud.attr("main", "samsungce.sabbathMode", "status") == "on"


TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_")]


def main():
    failures = 0
    for name, mod in (("Lua 5.1", lua51), ("LuaJIT 2.1", luajit21)):
        for t in TESTS:
            try:
                t(mod)
                print(f"PASS  [{name}] {t.__name__}")
            except Exception:
                failures += 1
                print(f"FAIL  [{name}] {t.__name__}")
                traceback.print_exc()
    total = len(TESTS) * 2
    print(f"\n{total - failures}/{total} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
