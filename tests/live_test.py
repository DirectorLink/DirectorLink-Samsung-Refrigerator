"""LIVE end-to-end tests: run the real driver.lua against the real SmartThings API.

These WILL operate your refrigerator (every change is reverted at the end).

  set ST_TOKEN=<personal access token>          (see README: required scopes)

  python tests/live_test.py features             toggle every supported feature on and off,
                                                 nudge fridge/freezer setpoints by 1 degree and back
  python tests/live_test.py create-app           create the OAuth app via the driver; prints Client ID,
                                                 Secret and the authorization URL (saved to --state)
  python tests/live_test.py oauth <redirect-url> exchange the code, renew the token once (rotation),
                                                 and read status with the OAuth token

The driver runs inside the mocked Control4 runtime (tests/c4_mock.lua); HTTP goes
to the real api.smartthings.com. Timers advance in real time.
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lupa import luajit21  # noqa: E402

from test_driver import DEG, TILE, Driver, lua_to_py  # noqa: E402

API = "https://api.smartthings.com/v1"
NAMES = {"powerCool": "Power Cool", "powerFreeze": "Power Freeze", "sabbath": "Sabbath Mode", "iceMaker": "Ice Maker"}


class LiveCloud:
    """Same interface as FakeSmartThings.handle, but real HTTPS."""

    def handle(self, method, url, headers, data):
        t0 = time.time()
        req = urllib.request.Request(url, data=data.encode() if data else None, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                code, body = r.status, r.read().decode()
        except urllib.error.HTTPError as e:
            code, body = e.code, e.read().decode()
        except Exception as e:  # noqa: BLE001
            print(f"   !! {method} {url} -> {e}")
            return None, None
        short = url.replace(API, "")
        if "oauth/token" in url:
            short = "/oauth/token"
        print(f"   {method:4} {short[:72]:72} -> {code} ({int((time.time() - t0) * 1000)} ms)")
        return code, body


def direct(token, method, path, body=None):
    req = urllib.request.Request(API + path, method=method, data=json.dumps(body).encode() if body else None,
                                 headers={"Authorization": f"Bearer {token}", "Accept": "application/json",
                                          "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode() or "null")


def wait_for(d, predicate, timeout=70):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if predicate():
            return time.time() - t0
        time.sleep(1)
        d.advance(1000)
    return None


class Results:
    def __init__(self):
        self.items = []

    def check(self, name, ok, detail=""):
        self.items.append((name, bool(ok)))
        print(("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail else ""))

    def summary(self):
        passed = sum(ok for _, ok in self.items)
        print(f"\n{passed}/{len(self.items)} live checks passed")
        return 0 if passed == len(self.items) else 1


def snapshot(token, dev):
    s = direct(token, "GET", f"/devices/{dev}/status")["components"]

    def v(c, cap, a):
        return ((s.get(c, {}).get(cap) or {}).get(a) or {}).get("value")
    return {
        "sabbath": v("main", "samsungce.sabbathMode", "status"),
        "powerCool": v("main", "samsungce.powerCool", "activated"),
        "powerFreeze": v("main", "samsungce.powerFreeze", "activated"),
        "fridgeSp": v("cooler", "thermostatCoolingSetpoint", "coolingSetpoint"),
        "freezerSp": v("freezer", "thermostatCoolingSetpoint", "coolingSetpoint"),
    }


def restore(token, dev, orig):
    now = snapshot(token, dev)
    cmds = []
    if now["sabbath"] != orig["sabbath"] and orig["sabbath"] in ("on", "off"):
        cmds.append(("main", "samsungce.sabbathMode", orig["sabbath"], None))
    for k, cap in (("powerCool", "samsungce.powerCool"), ("powerFreeze", "samsungce.powerFreeze")):
        if orig[k] is not None and now[k] != orig[k]:
            cmds.append(("main", cap, "activate" if orig[k] else "deactivate", None))
    for k, comp in (("fridgeSp", "cooler"), ("freezerSp", "freezer")):
        if orig[k] is not None and now[k] != orig[k]:
            cmds.append((comp, "thermostatCoolingSetpoint", "setCoolingSetpoint", [orig[k]]))
    for comp, cap, cmd, args in cmds:
        c = {"component": comp, "capability": cap, "command": cmd}
        if args:
            c["arguments"] = args
        print(f"!! restoring {comp} {cap}.{cmd} {args or ''}")
        direct(token, "POST", f"/devices/{dev}/commands", {"commands": [c]})
    return not cmds


def run_features(token, device):
    R = Results()
    d = Driver(luajit21, LiveCloud())
    d.init()
    print("\n== Discovery and status (Personal Access Token mode)")
    d.set_property("Personal Access Token", token)
    d.action("DiscoverDevices")
    if device:
        d.set_property("Device ID", device)
    dev = d.prop("Device ID")
    R.check("refrigerator discovered and selected", dev, dev)
    if not dev:
        return R.summary()
    orig = snapshot(token, dev)
    print("   original values:", orig)
    for p in ("Driver Status", "Model", "Supported Features", "Connection", "Power Cool", "Power Freeze", "Sabbath Mode",
              "Ice Maker", "Fridge Temperature", "Fridge Setpoint", "Freezer Temperature", "Freezer Setpoint",
              "FlexZone", "Doors", "Water Filter", "Power", "Energy"):
        print(f"   {p:20}: {d.prop(p)}")
    R.check("status OK", d.prop("Driver Status") == "OK", d.prop("Driver Status"))
    try:
        for key, (binding, button) in TILE.items():
            name = NAMES[key]
            if d.prop(name) == "Not available":
                R.check(f"{name}: tile shows unavailable", d.icon(binding) == "unavailable")
                continue
            start = d.prop(name)
            for target in (("On", "Off") if start == "Off" else ("Off", "On")):
                print(f"\n== {name} -> {target}")
                n_ev = len(d.events())
                d.tap(binding)
                secs = wait_for(d, lambda: d.prop(name) == target and d.icon(binding) != "pending")
                R.check(f"{name} {target} confirmed", secs is not None,
                        f"{secs:.0f}s" if secs is not None else d.prop("Driver Status"))
                R.check(f"{name} {target} event", f"{name} Turned {target}" in d.events()[n_ev:], d.events()[n_ev:])
                R.check(f"{name} {target} icon/LED", d.icon(binding) == target.lower()
                        and d.led(button) == ("1" if target == "On" else "0"))

        for key, prop in (("fridge", "Fridge Setpoint"), ("freezer", "Freezer Setpoint")):
            cur = d.prop(prop)
            if cur in ("Not available", "Unknown"):
                continue
            base = int(cur.split(" ")[0])
            items = (d.M.plist[prop] or "").split(",")
            unit = cur.split(" ", 1)[1]
            alt = base + 1 if f"{base + 1} {unit}" in items else base - 1
            for target in (alt, base):
                print(f"\n== {prop} -> {target}")
                d.set_property(prop, f"{target} {unit}")
                secs = wait_for(d, lambda: d.prop(prop) == f"{target} {unit}" and not d.g.gOps[f"setpoint:{key}"])
                R.check(f"{prop} {target} confirmed", secs is not None,
                        f"{secs:.0f}s" if secs is not None else d.prop("Driver Status"))
        R.check("no Command Failed events", "Command Failed" not in d.events(), d.events())
    finally:
        print("\n== Restore check")
        R.check("refrigerator back to original values", restore(token, dev, orig), snapshot(token, dev))
    errors = lua_to_py(d.M.errors) or []
    if errors:
        print("Driver error log:", *errors, sep="\n  ")
    return R.summary()


def run_create_app(token, state_file):
    R = Results()
    d = Driver(luajit21, LiveCloud())
    d.init()
    d.set_property("Personal Access Token", token)
    print("\n== Create OAuth app via the driver")
    d.action("CreateOAuthApp")
    cid, secret = d.prop("OAuth Client ID"), d.prop("OAuth Client Secret")
    R.check("OAuth app created", cid and secret, d.prop("Authorization Status"))
    if cid:
        state = {"client_id": cid, "client_secret": secret, "redirect_uri": d.prop("OAuth Redirect URI"),
                 "authorization_url": d.prop("Authorization URL")}
        Path(state_file).write_text(json.dumps(state, indent=2), encoding="utf-8")
        print(f"\nSaved to {state_file}")
        print(f"Client ID     : {cid}")
        print(f"Client Secret : {secret}")
        print(f"Authorize URL : {state['authorization_url']}")
    return R.summary()


def run_oauth(token, state_file, redirected):
    R = Results()
    state = json.loads(Path(state_file).read_text(encoding="utf-8"))
    d = Driver(luajit21, LiveCloud())
    d.init()
    d.g.Properties["OAuth Client ID"] = state["client_id"]
    d.g.Properties["OAuth Client Secret"] = state["client_secret"]
    print("\n== Exchange authorization code")
    d.set_property("Authorization Code", redirected)
    auth = d.persisted_auth() or {}
    R.check("code exchanged for tokens", auth.get("access_token") and auth.get("refresh_token"),
            d.prop("Authorization Status"))
    R.check("discovery with OAuth token", d.prop("Device ID") != "", d.prop("Driver Status"))
    if not auth.get("refresh_token"):
        return R.summary()
    print("\n== Renew (refresh token rotation)")
    old_rt = auth["refresh_token"]
    d.action("RenewToken")
    new = d.persisted_auth() or {}
    R.check("token renewed", new.get("access_token") and new.get("access_token") != auth["access_token"],
            d.prop("Authorization Status"))
    R.check("refresh token rotated", new.get("refresh_token") and new["refresh_token"] != old_rt)
    R.check("expires_in ~24h", 20 * 3600 < new.get("expires_at", 0) - d.M.now <= 24 * 3600,
            new.get("expires_at", 0) - d.M.now)
    print("\n== Status with renewed OAuth token")
    d.action("RefreshStatus")
    R.check("status read with OAuth token", d.prop("Driver Status") == "OK", d.prop("Driver Status"))
    return R.summary()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["features", "create-app", "oauth"])
    ap.add_argument("redirected", nargs="?", default="")
    ap.add_argument("--device", default="")
    ap.add_argument("--state", default=str(Path(os.environ.get("TEMP", ".")) / "c4_samsung_oauth_state.json"))
    args = ap.parse_args()
    token = os.environ.get("ST_TOKEN", "").strip()
    if not token and args.mode != "oauth":
        sys.exit("Set ST_TOKEN to a SmartThings personal access token (see README for the required scopes)")
    if args.mode == "features":
        return run_features(token, args.device)
    if args.mode == "create-app":
        return run_create_app(token, args.state)
    return run_oauth(token, args.state, args.redirected)


if __name__ == "__main__":
    sys.exit(main())
