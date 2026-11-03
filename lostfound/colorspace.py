"""Colour utilities: hex <-> Lab, perceptual distance, palette naming."""

from __future__ import annotations

import math

from .taxonomy import COLORS


def hex_to_rgb(value: str) -> tuple[int, int, int]:
    v = value.lstrip("#")
    return int(v[0:2], 16), int(v[2:4], 16), int(v[4:6], 16)


def _srgb_to_linear(c: float) -> float:
    c = c / 255.0
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def rgb_to_lab(rgb: tuple[int, int, int]) -> tuple[float, float, float]:
    r, g, b = (_srgb_to_linear(c) for c in rgb)
    x = r * 0.4124 + g * 0.3576 + b * 0.1805
    y = r * 0.2126 + g * 0.7152 + b * 0.0722
    z = r * 0.0193 + g * 0.1192 + b * 0.9505
    xyz = (x / 0.95047, y / 1.0, z / 1.08883)
    f = [v ** (1 / 3) if v > 0.008856 else 7.787 * v + 16 / 116 for v in xyz]
    return 116 * f[1] - 16, 500 * (f[0] - f[1]), 200 * (f[1] - f[2])


def delta_e(a: tuple[int, int, int] | None, b: tuple[int, int, int] | None) -> float | None:
    """CIE76 colour difference between two RGB triples (0 = identical)."""
    if a is None or b is None:
        return None
    la, lb = rgb_to_lab(a), rgb_to_lab(b)
    return math.dist(la, lb)


_PALETTE = {name: rgb_to_lab(hex_to_rgb(hexv)) for name, hexv in COLORS.items()}


def name_color(rgb: tuple[int, int, int]) -> tuple[str, float]:
    """Nearest palette colour plus its distance in Lab units."""
    lab = rgb_to_lab(rgb)
    best, best_d = "black", math.inf
    for name, plat in _PALETTE.items():
        d = math.dist(lab, plat)
        if d < best_d:
            best, best_d = name, d
    return best, round(best_d, 2)


def color_distance_by_name(a: str | None, b: str | None) -> float | None:
    if not a or not b or a not in COLORS or b not in COLORS:
        return None
    distance = delta_e(hex_to_rgb(COLORS[a]), hex_to_rgb(COLORS[b]))
    return None if distance is None else round(distance, 2)
