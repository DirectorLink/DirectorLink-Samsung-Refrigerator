"""Package src/ into dist/DirectorLink-Samsung-Refrigerator.c4z.

A .c4z is a plain zip with driver.xml / driver.lua / www/ at the root. We use
Python's zipfile (forward-slash entry names); PowerShell 5.1's Compress-Archive
writes backslashes, which breaks icon paths on the controller.

VERSION (MAJOR.MINOR.PATCH) is the only file to edit for a release. The build
stamps it into the package, the same way DirectorLink does:
  * driver.xml <version>  -> MAJOR*10000 + MINOR*100 + PATCH (Control4's update number)
  * driver.lua DRIVER_VERSION = "dev" -> "MAJOR.MINOR.PATCH"
The sources keep their placeholders.

Checks:
  * driver.xml is well-formed; every controller://driver/<name>/... icon exists
    and <name> matches the .c4z file name
  * driver.lua compiles under Lua 5.1 and LuaJIT 2.1 (if lupa is installed)
  * the package has driver.xml / driver.lua at the root, no backslashes, and
    the stamped version

Usage:  python scripts/build.py
"""
import re
import sys
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
DIST = ROOT / "dist"
C4Z_NAME = "DirectorLink-Samsung-Refrigerator"
ZIP_DATE = (2026, 1, 1, 0, 0, 0)   # fixed timestamps: same sources -> same package


def fail(msg):
    print("BUILD FAILED: " + msg)
    sys.exit(1)


def read_version():
    v = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    m = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", v)
    if not m:
        fail(f"VERSION must be MAJOR.MINOR.PATCH, got {v!r}")
    major, minor, patch = (int(x) for x in m.groups())
    if minor > 99 or patch > 99:
        fail("MINOR and PATCH must be at most 99 (driver.xml version = MAJOR*10000 + MINOR*100 + PATCH)")
    return v, major * 10000 + minor * 100 + patch


def stamp_xml(text, number):
    new, n = re.subn(r"<version>\d+</version>", f"<version>{number}</version>", text, count=1)
    if n != 1:
        fail("driver.xml has no <version> element to stamp")
    return new


def stamp_lua(text, version):
    new, n = re.subn(r'^DRIVER_VERSION(\s*)= "dev"', f'DRIVER_VERSION\\1= "{version}"', text, count=1, flags=re.M)
    if n != 1:
        fail('driver.lua has no DRIVER_VERSION = "dev" line to stamp')
    return new


def check_xml(xml_text):
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        fail(f"driver.xml is not well-formed: {e}")
    for name, rel in re.findall(r"controller://driver/([^/<]+)/([^<\s]+)", xml_text):
        if name != C4Z_NAME:
            fail(f"icon URL uses driver name '{name}', expected '{C4Z_NAME}'")
        if not (SRC / "www" / rel).is_file():
            fail(f"missing icon file www/{rel}")
    for tag in ("small", "large"):
        rel = root.findtext(tag)
        if rel and not (SRC / "www" / rel).is_file():
            fail(f"missing <{tag}> image www/{rel}")
    doc = root.find("config/documentation")
    if doc is not None and not (SRC / doc.get("file")).is_file():
        fail(f"missing documentation file {doc.get('file')}")


def check_lua(code):
    try:
        from lupa import lua51, luajit21
    except ImportError:
        print("  (lupa not installed - skipping Lua compile check)")
        return
    for label, mod in (("Lua 5.1", lua51), ("LuaJIT 2.1", luajit21)):
        res = mod.LuaRuntime().eval("function(s) return loadstring(s, '=driver.lua') end")(code)
        fn, err = res if isinstance(res, tuple) else (res, None)
        if fn is None:
            fail(f"driver.lua does not compile under {label}: {err}")
        print(f"  driver.lua compiles under {label}")


def add(z, name, data):
    info = zipfile.ZipInfo(name, date_time=ZIP_DATE)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o644 << 16
    z.writestr(info, data)


def main():
    version, number = read_version()
    xml_text = stamp_xml((SRC / "driver.xml").read_text(encoding="utf-8"), number)
    lua_text = stamp_lua((SRC / "driver.lua").read_text(encoding="utf-8"), version)
    check_xml(xml_text)
    print(f"  driver.xml OK (version {version} -> {number})")
    check_lua(lua_text)

    DIST.mkdir(exist_ok=True)
    out = DIST / f"{C4Z_NAME}.c4z"
    www = sorted(p for p in (SRC / "www").rglob("*") if p.is_file())
    with zipfile.ZipFile(out, "w") as z:
        add(z, "driver.xml", xml_text.encode("utf-8"))
        add(z, "driver.lua", lua_text.encode("utf-8"))
        for f in www:
            add(z, f.relative_to(SRC).as_posix(), f.read_bytes())

    with zipfile.ZipFile(out) as z:
        names = z.namelist()
        if any("\\" in n for n in names):
            fail("backslash in a package path")
        if "driver.xml" not in names or "driver.lua" not in names:
            fail("driver.xml / driver.lua missing at the package root")
        if f"<version>{number}</version>" not in z.read("driver.xml").decode("utf-8"):
            fail("stamped version missing from packaged driver.xml")
        if f'DRIVER_VERSION        = "{version}"' not in z.read("driver.lua").decode("utf-8"):
            fail("stamped version missing from packaged driver.lua")
        for rel in re.findall(r"controller://driver/[^/<]+/([^<\s]+)", z.read("driver.xml").decode("utf-8")):
            if f"www/{rel}" not in names:
                fail(f"icon www/{rel} missing from the package")
    print(f"Built {out.relative_to(ROOT)}  (v{version}, {out.stat().st_size // 1024} KB, {len(names)} files)")


if __name__ == "__main__":
    main()
