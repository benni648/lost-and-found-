"""Deterministic synthetic item photos for the demo corpus.

Real campus photos cannot be shipped with the project, so the demo corpus is
rendered: a flat-design product illustration of the reported item, shot twice
under slightly different lighting/rotation to simulate two people
photographing the same physical object. Same instance -> near-identical
vectors; different instance -> different colour/shape/detail.

Images are flagged ``is_synthetic_art`` in the database, and the report and
model card say so. Swapping in real photos later is a data change, not a code
change: drop files in ``data/media`` and point ``reports.image_path`` at them.
"""

from __future__ import annotations

import math
import random

from PIL import Image, ImageDraw, ImageFilter

from .colorspace import hex_to_rgb
from .taxonomy import COLORS

SIZE = 448
_BACKGROUNDS = [(226, 224, 219), (214, 216, 219), (231, 226, 214), (208, 213, 215)]


def _shade(rgb: tuple[int, int, int], factor: float) -> tuple[int, int, int]:
    return tuple(max(0, min(255, int(c * factor))) for c in rgb)  # type: ignore[return-value]


def _rr(draw: ImageDraw.ImageDraw, box, radius, fill, outline=None, width=2):
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width)


def _draw_item(d: ImageDraw.ImageDraw, item: dict, rng: random.Random) -> None:
    cat = item["category"]
    base = hex_to_rgb(COLORS.get(item.get("primary_color") or "black", "#333333"))
    accent = hex_to_rgb(COLORS.get(item.get("secondary_color") or "white", "#eeeeee"))
    dark, light = _shade(base, 0.62), _shade(base, 1.22)
    cx, cy = SIZE // 2, SIZE // 2

    if cat == "bottle":
        _rr(d, (cx - 46, cy - 105, cx + 46, cy + 118), 26, base, dark, 3)
        _rr(d, (cx - 20, cy - 138, cx + 20, cy - 100), 7, dark, dark, 2)
        _rr(d, (cx - 34, cy - 60, cx - 14, cy + 96), 8, light, None)
    elif cat == "backpack":
        _rr(d, (cx - 78, cy - 90, cx + 78, cy + 110), 30, base, dark, 3)
        d.pieslice((cx - 78, cy - 130, cx + 78, cy - 30), 180, 360, fill=dark, outline=dark)
        _rr(d, (cx - 58, cy - 20, cx + 58, cy + 30), 14, dark, None)
        for sx in (-48, 48):
            _rr(d, (cx + sx - 10, cy - 20, cx + sx + 10, cy + 92), 8, _shade(base, 0.8), None)
    elif cat == "wallet":
        _rr(d, (cx - 92, cy - 58, cx + 92, cy + 62), 14, base, dark, 3)
        d.line((cx - 92, cy + 4, cx + 92, cy + 4), fill=dark, width=3)
        _rr(d, (cx + 30, cy + 18, cx + 62, cy + 40), 6, light, None)
    elif cat == "phone":
        _rr(d, (cx - 58, cy - 112, cx + 58, cy + 112), 20, dark, dark, 3)
        _rr(d, (cx - 50, cy - 100, cx + 50, cy + 100), 14, _shade(base, 0.9), None)
        _rr(d, (cx - 38, cy - 92, cx - 10, cy - 60), 5, _shade(base, 1.1), None)
        _rr(d, (cx - 30, cy + 74, cx + 30, cy + 88), 6, dark, None)
    elif cat == "laptop":
        d.rounded_rectangle((cx - 104, cy - 96, cx + 104, cy + 26), 8, fill=base, outline=dark, width=3)
        d.rounded_rectangle((cx - 92, cy - 84, cx + 92, cy + 14), 4, fill=_shade(base, 1.25), outline=None)
        d.polygon(
            [(cx - 128, cy + 60), (cx + 128, cy + 60), (cx + 104, cy + 28), (cx - 104, cy + 28)],
            fill=_shade(base, 0.85),
            outline=dark,
        )
        d.line((cx - 40, cy + 44, cx + 40, cy + 44), fill=dark, width=3)
    elif cat == "id_card":
        _rr(d, (cx - 88, cy - 62, cx + 88, cy + 62), 10, base, dark, 3)
        d.rectangle((cx - 88, cy - 62, cx + 88, cy - 40), fill=_shade(base, 0.7))
        _rr(d, (cx - 72, cy - 26, cx - 24, cy + 26), 4, accent, dark, 2)
        for i, w in enumerate((84, 64, 74)):
            d.line((cx - 8, cy - 16 + i * 16, cx - 8 + w, cy - 16 + i * 16), fill=_shade(base, 1.3), width=5)
    elif cat == "keys":
        d.ellipse((cx - 52, cy - 92, cx + 12, cy - 28), outline=light, width=10)
        for i, ang in enumerate((40, 20, 0)):
            rad = math.radians(ang)
            x0, y0 = cx + 10, cy - 20 + i * 26
            d.line((x0, y0, x0 + 70 * math.cos(rad), y0 + 70 * math.sin(rad)), fill=light, width=9)
            d.line((x0 + 54, y0 + 18, x0 + 66, y0 + 30), fill=light, width=7)
    elif cat == "earbuds":
        _rr(d, (cx - 70, cy - 50, cx + 70, cy + 70), 34, base, dark, 3)
        for sx in (-34, 34):
            d.ellipse((cx + sx - 18, cy - 96, cx + sx + 18, cy - 58), fill=dark, outline=None)
            d.line((cx + sx, cy - 66, cx + sx, cy - 18), fill=dark, width=11)
    elif cat == "umbrella":
        d.pieslice((cx - 120, cy - 110, cx + 120, cy + 110), 180, 360, fill=base, outline=dark)
        for i in range(4):
            d.line((cx, cy - 8, cx - 96 + i * 64, cy - 4), fill=light, width=4)
        d.line((cx, cy, cx, cy + 100), fill=dark, width=7)
        d.arc((cx - 34, cy + 72, cx, cy + 112), 0, 180, fill=dark, width=7)
    elif cat == "calculator":
        _rr(d, (cx - 62, cy - 108, cx + 62, cy + 108), 10, base, dark, 3)
        _rr(d, (cx - 46, cy - 92, cx + 46, cy - 48), 5, _shade(base, 1.3), dark, 2)
        for row in range(4):
            for col in range(4):
                _rr(d, (cx - 48 + col * 25, cy - 30 + row * 28, cx - 28 + col * 25, cy - 12 + row * 28), 4, light, None)
    elif cat == "notebook":
        _rr(d, (cx - 76, cy - 100, cx + 76, cy + 100), 6, base, dark, 3)
        d.rectangle((cx - 76, cy - 100, cx - 56, cy + 100), fill=_shade(base, 0.7))
        for i in range(7):
            d.ellipse((cx - 74, cy - 78 + i * 24, cx - 58, cy - 62 + i * 24), outline=light, width=4)
        for i in range(5):
            d.line((cx - 36, cy - 70 + i * 32, cx + 60, cy - 70 + i * 32), fill=_shade(base, 1.18), width=3)
    elif cat == "glasses":
        for sx in (-56, 56):
            d.rounded_rectangle((cx + sx - 48, cy - 26, cx + sx + 48, cy + 26), 12, outline=base, width=9)
        d.arc((cx - 60, cy - 24, cx + 60, cy + 6), 200, 340, fill=base, width=8)
        for sx in (-104, 104):
            d.line((cx + sx, cy - 14, cx + sx + (26 if sx > 0 else -26), cy - 44), fill=base, width=7)
        d.rectangle((cx - 112, cy - 32, cx - 56, cy + 30), fill=None)
    elif cat == "watch":
        _rr(d, (cx - 30, cy - 130, cx + 30, cy - 30), 10, _shade(base, 0.8), None)
        _rr(d, (cx - 30, cy + 30, cx + 30, cy + 130), 10, _shade(base, 0.8), None)
        d.ellipse((cx - 58, cy - 58, cx + 58, cy + 58), fill=base, outline=dark, width=6)
        d.ellipse((cx - 42, cy - 42, cx + 42, cy + 42), fill=_shade(base, 1.28), outline=None)
        for ang in (270, 0, 90):
            r = math.radians(ang)
            d.line((cx, cy, cx + 30 * math.cos(r), cy + 30 * math.sin(r)), fill=dark, width=5)
    elif cat == "charger":
        _rr(d, (cx - 56, cy - 56, cx + 44, cy + 56), 14, base, dark, 3)
        for sx in (-18, 14):
            d.rectangle((cx + sx, cy - 78, cx + sx + 10, cy - 56), fill=light, outline=None)
        d.arc((cx + 30, cy + 10, cx + 150, cy + 130), 120, 300, fill=dark, width=9)
    elif cat == "hoodie":
        d.polygon(
            [
                (cx - 70, cy - 80),
                (cx + 70, cy - 80),
                (cx + 96, cy + 30),
                (cx + 60, cy + 40),
                (cx + 56, cy + 120),
                (cx - 56, cy + 120),
                (cx - 60, cy + 40),
                (cx - 96, cy + 30),
            ],
            fill=base,
            outline=dark,
        )
        d.pieslice((cx - 60, cy - 130, cx + 60, cy - 30), 0, 360, fill=_shade(base, 0.78), outline=dark)
        d.pieslice((cx - 40, cy - 120, cx + 40, cy - 40), 0, 360, fill=_shade(base, 1.1), outline=None)
        d.line((cx, cy - 30, cx, cy + 4), fill=light, width=5)
    elif cat == "lunchbox":
        _rr(d, (cx - 86, cy - 60, cx + 86, cy + 66), 12, base, dark, 3)
        d.line((cx - 86, cy - 22, cx + 86, cy - 22), fill=dark, width=4)
        _rr(d, (cx - 22, cy - 78, cx + 22, cy - 56), 8, light, dark, 2)
        d.line((cx, cy - 8, cx, cy + 52), fill=_shade(base, 1.25), width=4)
    elif cat == "pendrive":
        _rr(d, (cx - 34, cy - 74, cx + 34, cy + 46), 8, base, dark, 3)
        d.rectangle((cx - 22, cy - 110, cx + 22, cy - 74), fill=light, outline=dark, width=3)
        _rr(d, (cx - 8, cy + 8, cx + 8, cy + 34), 4, dark, None)
    else:  # generic
        _rr(d, (cx - 70, cy - 70, cx + 70, cy + 70), 12, base, dark, 3)

    # distinguishing mark
    if item.get("marks"):
        mx, my = cx + 52, cy - 78
        if "sticker" in " ".join(item["marks"]) or "logo" in " ".join(item["marks"]):
            d.ellipse((mx - 16, my - 16, mx + 16, my + 16), fill=accent, outline=dark, width=2)
            d.arc((mx - 9, my - 6, mx + 9, my + 12), 200, 340, fill=dark, width=3)
        else:
            d.line((mx - 20, my, mx + 20, my - 14), fill=accent, width=6)

    # damage hints
    if item.get("damages"):
        dmg = " ".join(item["damages"])
        if "scratch" in dmg or "crack" in dmg:
            d.line((cx - 30, cy + 10, cx - 4, cy + 44), fill=_shade(base, 1.5), width=3)
            d.line((cx - 4, cy + 44, cx + 18, cy + 22), fill=_shade(base, 1.5), width=2)
        if "dent" in dmg or "bent" in dmg:
            d.arc((cx + 20, cy + 10, cx + 90, cy + 70), 20, 150, fill=_shade(base, 0.5), width=4)


def render_item_image(item: dict, rng: random.Random, style: str = "found") -> Image.Image:
    """Render one photo of ``item``. ``style`` = found | lost (owner's phone)."""
    bg = rng.choice(_BACKGROUNDS)
    img = Image.new("RGB", (SIZE, SIZE), bg)
    d = ImageDraw.Draw(img)
    # soft vertical gradient
    top = _shade(bg, 1.06)
    for y in range(SIZE):
        t = y / SIZE
        row = tuple(int(top[i] * (1 - t) + bg[i] * t) for i in range(3))
        d.line((0, y, SIZE, y), fill=row)
    # shadow
    d.ellipse((SIZE // 2 - 130, SIZE - 130, SIZE // 2 + 130, SIZE - 78), fill=_shade(bg, 0.86))

    item_layer = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    _draw_item(ImageDraw.Draw(item_layer), item, rng)
    angle = rng.uniform(-5, 5)
    item_layer = item_layer.rotate(angle, resample=Image.Resampling.BICUBIC, center=(SIZE // 2, SIZE // 2))
    img.paste(item_layer, (0, 0), item_layer)

    # photographic imperfection
    img = img.filter(ImageFilter.GaussianBlur(radius=0.4 if style == "found" else 0.9))
    brightness = rng.uniform(0.93, 1.07)
    arr = img.point(lambda v: max(0, min(255, int(v * brightness))))
    noise = Image.effect_noise((SIZE, SIZE), 14).convert("L")
    arr = Image.blend(arr, Image.merge("RGB", (noise, noise, noise)), 0.035)
    return arr
