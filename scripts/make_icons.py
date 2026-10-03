"""Generate the Navigator tile icons and Composer device images.

Style matches the stock Control4 "Experience Button" icons: a gray disc with a
colored glow ring. One icon set per tile:

  refrigerator  refrigerator        status tile (primary proxy) - states ok, door, pending, error, unknown
  sabbath       Shabbat candles     (gold when on)
  power_cool    thermometer + arrow (cyan when on)
  power_freeze  snowflake           (blue when on)
  ice_maker     ice cubes           (aqua when on)

States (the uibutton proxy picks the icon by state id):
  on, off, pending (white ring + dots), error (red ring + "!"),
  unavailable (faded glyph with a slash - feature not supported by the model)
Status tile states:
  ok (green ring), door (amber ring, door drawn open), pending, error, unknown (gray ring)

Composer images: icons/device_sm.png / device_lg.png (the driver) and
icons/<tile>/device_sm.png / device_lg.png (each proxy).

Usage:  python scripts/make_icons.py
"""
import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parent.parent
ICON_DIR = ROOT / "src" / "www" / "icons"
SIZES = (70, 90, 300, 512, 1024)
STATES = ("on", "off", "pending", "error", "unavailable")
SS = 4          # supersampling factor
BASE = 300      # design grid

FEATURES = {
    "refrigerator": (60, 200, 110),
    "sabbath": (255, 190, 40),
    "power_cool": (40, 210, 255),
    "power_freeze": (70, 120, 255),
    "ice_maker": (120, 235, 230),
}
STATUS_STATES = ("ok", "door", "pending", "error", "unknown")
STATUS_RING = {"ok": (60, 200, 110), "door": (255, 165, 0), "pending": (235, 235, 235),
               "error": (230, 40, 40), "unknown": (95, 95, 95)}
RING = {"off": (95, 95, 95), "pending": (235, 235, 235), "error": (230, 40, 40), "unavailable": (80, 80, 80)}
GLYPH_OFF = (238, 238, 238, 255)


def S(v, k):
    return int(round(v * k))


def gradient_disc(W, box, top, bottom):
    grad = Image.new("RGBA", (W, W))
    gd = ImageDraw.Draw(grad)
    for y in range(W):
        t = min(max((y - box[1]) / max(1, box[3] - box[1]), 0), 1)
        gd.line([(0, y), (W, y)], fill=tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3)) + (255,))
    mask = Image.new("L", (W, W), 0)
    ImageDraw.Draw(mask).ellipse(box, fill=255)
    out = Image.new("RGBA", (W, W), (0, 0, 0, 0))
    out.paste(grad, (0, 0), mask)
    return out


# ---------------------------------------------------------------- glyphs
def glyph_sabbath(W, k, lit, color):
    layer = Image.new("RGBA", (W, W), (0, 0, 0, 0))
    if lit:
        glow = Image.new("RGBA", (W, W), (0, 0, 0, 0))
        gd = ImageDraw.Draw(glow)
        for cx in (118, 182):
            gd.ellipse([S(cx - 26, k), S(58, k), S(cx + 26, k), S(118, k)], fill=(255, 200, 60, 130))
        layer = Image.alpha_composite(layer, glow.filter(ImageFilter.GaussianBlur(S(8, k))))
    d = ImageDraw.Draw(layer)
    for cx in (118, 182):
        if lit:
            d.ellipse([S(cx - 11, k), S(84, k), S(cx + 11, k), S(110, k)], fill=(255, 170, 30, 255))
            d.polygon([(S(cx - 10, k), S(93, k)), (S(cx + 10, k), S(93, k)), (S(cx, k), S(66, k))], fill=(255, 170, 30, 255))
            d.ellipse([S(cx - 5, k), S(94, k), S(cx + 5, k), S(107, k)], fill=(255, 245, 190, 255))
            d.polygon([(S(cx - 5, k), S(99, k)), (S(cx + 5, k), S(99, k)), (S(cx, k), S(83, k))], fill=(255, 245, 190, 255))
        d.rounded_rectangle([S(cx - 26, k), S(212, k), S(cx + 26, k), S(226, k)], radius=S(6, k), fill=(205, 205, 210, 255))
        d.polygon([(S(cx - 12, k), S(212, k)), (S(cx + 12, k), S(212, k)), (S(cx + 7, k), S(196, k)), (S(cx - 7, k), S(196, k))],
                  fill=(205, 205, 210, 255))
        d.rounded_rectangle([S(cx - 13, k), S(118, k), S(cx + 13, k), S(198, k)], radius=S(4, k), fill=(250, 248, 240, 255))
        d.line([(S(cx, k), S(118, k)), (S(cx, k), S(106, k))], fill=(60, 60, 60, 255), width=max(1, S(3, k)))
    return layer


def glyph_power_cool(W, k, lit, color):
    layer = Image.new("RGBA", (W, W), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    fill = color + (255,) if lit else GLYPH_OFF
    # thermometer
    d.rounded_rectangle([S(104, k), S(78, k), S(132, k), S(196, k)], radius=S(14, k), fill=(250, 250, 250, 255))
    d.ellipse([S(92, k), S(178, k), S(144, k), S(230, k)], fill=(250, 250, 250, 255))
    d.rounded_rectangle([S(111, k), S(132, k), S(125, k), S(200, k)], radius=S(7, k), fill=fill)
    d.ellipse([S(100, k), S(186, k), S(136, k), S(222, k)], fill=fill)
    for y in (100, 118, 136):
        d.line([(S(136, k), S(y, k)), (S(146, k), S(y, k))], fill=(250, 250, 250, 255), width=S(4, k))
    # down arrow
    w = S(9, k)
    d.line([(S(196, k), S(84, k)), (S(196, k), S(196, k))], fill=fill, width=w)
    d.line([(S(170, k), S(170, k)), (S(196, k), S(198, k)), (S(222, k), S(170, k))], fill=fill, width=w, joint="curve")
    return layer


def glyph_power_freeze(W, k, lit, color):
    layer = Image.new("RGBA", (W, W), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    fill = color + (255,) if lit else GLYPH_OFF
    cx, cy, r = 150, 150, 78
    w = S(9, k)
    for i in range(6):
        a = math.radians(90 + i * 60)
        ex, ey = cx + r * math.cos(a), cy - r * math.sin(a)
        d.line([(S(cx, k), S(cy, k)), (S(ex, k), S(ey, k))], fill=fill, width=w)
        for frac, blen in ((0.55, 22), (0.8, 16)):
            bx, by = cx + r * frac * math.cos(a), cy - r * frac * math.sin(a)
            for side in (-1, 1):
                b = a + side * math.radians(45)
                d.line([(S(bx, k), S(by, k)), (S(bx + blen * math.cos(b), k), S(by - blen * math.sin(b), k))],
                       fill=fill, width=w)
    d.ellipse([S(cx - 10, k), S(cy - 10, k), S(cx + 10, k), S(cy + 10, k)], fill=fill)
    return layer


def glyph_ice_maker(W, k, lit, color):
    layer = Image.new("RGBA", (W, W), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    face = color + (255,) if lit else GLYPH_OFF
    edge = (255, 255, 255, 255)

    def cube(cx, cy, size, ang):
        pts = []
        for i in range(4):
            a = math.radians(ang + 45 + i * 90)
            pts.append((S(cx + size * math.cos(a), k), S(cy + size * math.sin(a), k)))
        d.polygon(pts, fill=face, outline=edge, width=S(5, k))
        # highlight
        d.line([pts[2], (S(cx, k), S(cy, k))], fill=(255, 255, 255, 140), width=S(4, k))

    cube(122, 178, 46, 10)
    cube(188, 170, 40, -14)
    cube(152, 112, 40, 22)
    return layer


def glyph_refrigerator(W, k, door_open, color):
    layer = Image.new("RGBA", (W, W), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    body, line, handle = (246, 246, 246, 255), (150, 150, 155, 255), (175, 175, 180, 255)
    lw = max(1, S(4, k))
    d.rounded_rectangle([S(98, k), S(58, k), S(202, k), S(240, k)], radius=S(12, k), fill=body)
    d.line([(S(98, k), S(172, k)), (S(202, k), S(172, k))], fill=line, width=lw)      # doors | drawers
    d.line([(S(98, k), S(206, k)), (S(202, k), S(206, k))], fill=line, width=lw)      # drawer split
    d.line([(S(150, k), S(58, k)), (S(150, k), S(172, k))], fill=line, width=lw)      # French doors
    for y in (189, 223):                                                              # drawer handles
        d.rounded_rectangle([S(128, k), S(y - 3, k), S(172, k), S(y + 3, k)], radius=S(3, k), fill=handle)
    d.rounded_rectangle([S(156, k), S(92, k), S(162, k), S(140, k)], radius=S(3, k), fill=handle)
    if door_open:
        # left door swung open: dark interior with a shelf, door panel outside
        d.rectangle([S(102, k), S(62, k), S(148, k), S(170, k)], fill=(70, 74, 80, 255))
        d.line([(S(104, k), S(116, k)), (S(146, k), S(116, k))], fill=(200, 200, 205, 255), width=lw)
        d.polygon([(S(98, k), S(58, k)), (S(62, k), S(72, k)), (S(62, k), S(160, k)), (S(98, k), S(172, k))],
                  fill=body, outline=line)
        d.rounded_rectangle([S(70, k), S(100, k), S(75, k), S(132, k)], radius=S(2, k), fill=handle)
    else:
        d.rounded_rectangle([S(138, k), S(92, k), S(144, k), S(140, k)], radius=S(3, k), fill=handle)
    return layer


GLYPHS = {"refrigerator": glyph_refrigerator, "sabbath": glyph_sabbath, "power_cool": glyph_power_cool,
          "power_freeze": glyph_power_freeze, "ice_maker": glyph_ice_maker}


# ---------------------------------------------------------------- icon
def make_icon(feature, state, size):
    W = size * SS
    k = W / BASE
    color = FEATURES[feature]
    img = Image.new("RGBA", (W, W), (0, 0, 0, 0))

    shadow = Image.new("RGBA", (W, W), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).ellipse([S(22, k), S(28, k), S(282, k), S(288, k)], fill=(0, 0, 0, 110))
    img = Image.alpha_composite(img, shadow.filter(ImageFilter.GaussianBlur(S(6, k))))

    status = feature == "refrigerator"
    ring = STATUS_RING[state] if status else (color if state == "on" else RING[state])
    ringimg = Image.new("RGBA", (W, W), (0, 0, 0, 0))
    ImageDraw.Draw(ringimg).ellipse([S(14, k), S(14, k), S(286, k), S(286, k)], fill=ring + (255,))
    if state in ("on", "ok", "door", "pending", "error"):
        img = Image.alpha_composite(img, ringimg.filter(ImageFilter.GaussianBlur(S(5, k))))
    img = Image.alpha_composite(img, ringimg)
    img = Image.alpha_composite(img, gradient_disc(W, [S(36, k), S(36, k), S(264, k), S(264, k)],
                                                   (165, 165, 165), (95, 95, 95)))

    glyph = GLYPHS[feature](W, k, (state == "door") if status else (state == "on"), color)
    if state == "unavailable":
        r, g, b, a = glyph.split()
        glyph = Image.merge("RGBA", (r, g, b, a.point(lambda v: v * 35 // 100)))
    img = Image.alpha_composite(img, glyph)

    d = ImageDraw.Draw(img)
    if state == "error":
        d.ellipse([S(196, k), S(196, k), S(262, k), S(262, k)], fill=(230, 40, 40, 255),
                  outline=(255, 255, 255, 255), width=S(5, k))
        d.rounded_rectangle([S(224, k), S(209, k), S(234, k), S(236, k)], radius=S(4, k), fill=(255, 255, 255, 255))
        d.ellipse([S(223, k), S(240, k), S(235, k), S(252, k)], fill=(255, 255, 255, 255))
    elif state == "pending":
        for i, x in enumerate((128, 150, 172)):
            d.ellipse([S(x - 7, k), S(240, k), S(x + 7, k), S(254, k)], fill=(255, 255, 255, 255 - i * 60))
    elif state == "unavailable":
        d.line([(S(80, k), S(220, k)), (S(220, k), S(80, k))], fill=(225, 225, 225, 230), width=S(12, k))

    out = img.resize((size, size), Image.LANCZOS)
    return out


def main():
    for feature in FEATURES:
        folder = ICON_DIR / feature
        folder.mkdir(parents=True, exist_ok=True)
        states = STATUS_STATES if feature == "refrigerator" else STATES
        for state in states:
            for size in SIZES:
                make_icon(feature, state, size).save(folder / f"{state}_{size}.png", optimize=True)
        shown = "ok" if feature == "refrigerator" else "on"
        make_icon(feature, shown, 32).save(folder / "device_lg.png", optimize=True)
        make_icon(feature, shown, 16).save(folder / "device_sm.png", optimize=True)
    make_icon("refrigerator", "ok", 32).save(ICON_DIR / "device_lg.png", optimize=True)
    make_icon("refrigerator", "ok", 16).save(ICON_DIR / "device_sm.png", optimize=True)
    print(f"Icons written to {ICON_DIR}")


if __name__ == "__main__":
    main()
