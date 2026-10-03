"""Regenerate the tile sections of src/driver.xml from one table.

Rewrites <proxies>, <capabilities> (Navigator icons per state) and
<connections> (UI buttons and keypad links). Everything else in driver.xml is
edited by hand.

Never change proxies or connections once a version is released: Control4
creates them only when the driver is added, so installed projects depend on them.

Usage:  python scripts/gen_ui_xml.py
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
XML = ROOT / "src" / "driver.xml"
C4Z_NAME = "DirectorLink-Samsung-Refrigerator"
SIZES = (70, 90, 300, 512, 1024)
FEATURE_STATES = ("on", "off", "pending", "error", "unavailable")
STATUS_STATES = ("ok", "door", "pending", "error", "unknown")

# binding, proxy name, icon folder, states, default icon, keypad binding
TILES = [
    (5001, "Samsung Refrigerator", "refrigerator", STATUS_STATES, "unknown", None),
    (5002, "Power Cool", "power_cool", FEATURE_STATES, "off", 300),
    (5003, "Power Freeze", "power_freeze", FEATURE_STATES, "off", 301),
    (5004, "Sabbath Mode", "sabbath", FEATURE_STATES, "off", 302),
    (5005, "Ice Maker", "ice_maker", FEATURE_STATES, "off", 303),
]


def icons(folder, state, indent):
    return "\n".join(f'{indent}<Icon width="{s}" height="{s}">controller://driver/{C4Z_NAME}/icons/{folder}/{state}_{s}.png</Icon>'
                     for s in SIZES)


def proxies():
    out = ["\t<proxies>"]
    for i, (b, name, folder, *_rest) in enumerate(TILES):
        primary = ' primary="True"' if i == 0 else ""
        out.append(f'\t\t<proxy proxybindingid="{b}" name="{name}"{primary} image_source="c4z" '
                   f'large_image="icons/{folder}/device_lg.png" small_image="icons/{folder}/device_sm.png">uibutton</proxy>')
    out.append("\t</proxies>")
    return "\n".join(out)


def capabilities():
    out = ["\t<capabilities>"]
    for b, _name, folder, states, default, _btn in TILES:
        out += [f'\t\t<navigator_display_option proxybindingid="{b}">', "\t\t\t<display_icons>",
                icons(folder, default, "\t\t\t\t")]
        for st in states:
            out += [f'\t\t\t\t<state id="{st}">', icons(folder, st, "\t\t\t\t\t"), "\t\t\t\t</state>"]
        out += ["\t\t\t</display_icons>", "\t\t</navigator_display_option>"]
    out.append("\t</capabilities>")
    return "\n".join(out)


def connection(cid, name, ctype, classname, extra=""):
    return (f"\t\t<connection>\n\t\t\t<id>{cid}</id>\n\t\t\t<facing>6</facing>\n"
            f"\t\t\t<connectionname>{name}</connectionname>\n\t\t\t<type>{ctype}</type>\n"
            "\t\t\t<consumer>False</consumer>\n\t\t\t<audiosource>False</audiosource>\n"
            "\t\t\t<videosource>False</videosource>\n\t\t\t<linelevel>False</linelevel>\n"
            f"\t\t\t<classes>\n\t\t\t\t<class>\n\t\t\t\t\t<classname>{classname}</classname>\n{extra}"
            "\t\t\t\t</class>\n\t\t\t</classes>\n"
            + ("\t\t\t<hidden>False</hidden>\n" if ctype == 1 else "") + "\t\t</connection>")


def connections():
    out = ["\t<connections>"]
    for b, name, *_rest in TILES:
        out.append(connection(b, f"UIBUTTON {name}", 2, "UIBUTTON"))
    for _b, name, _f, _s, _d, btn in TILES:
        if btn:
            out.append(connection(btn, f"Keypad Button: {name}", 1, "BUTTON_LINK",
                                  "\t\t\t\t\t<autobind>False</autobind>\n"))
    out.append("\t</connections>")
    return "\n".join(out)


def main():
    s = XML.read_text(encoding="utf-8")
    for tag, block in (("proxies", proxies()), ("capabilities", capabilities()), ("connections", connections())):
        s, n = re.subn(rf"\t<{tag}>.*?</{tag}>", lambda _m, b=block: b, s, count=1, flags=re.S)
        if n != 1:
            raise SystemExit(f"<{tag}> block not found in driver.xml")
    XML.write_text(s, encoding="utf-8", newline="\n")
    print(f"Updated {XML.relative_to(ROOT)}: {len(TILES)} tiles")


if __name__ == "__main__":
    main()
