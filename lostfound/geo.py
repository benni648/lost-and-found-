"""Real geographic distance evidence.

The campus walk graph answers "how far apart are these two *buildings*". Once a
reporter pins a location on a map — a found item's exact spot, or where the owner
last had it — the honest question becomes "how far apart are these two *points*",
and that is a haversine on WGS84 coordinates.

Both pieces of evidence stay separate: topology is what we have when only a
building was chosen, GPS is what we have when the phone supplied coordinates.
They are different measurements, so they get different weights rather than one
being silently overwritten by the other.
"""

from __future__ import annotations

import math

EARTH_RADIUS_M = 6_371_008.8
# A campus is walked, not driven: 250 m is roughly a five-minute walk, which is
# the scale at which "found near where you lost it" stops being surprising.
CAMPUS_GEO_SCALE_M = 250.0
# Below this, two GPS fixes are inside their own error circle.
MIN_MEANINGFUL_M = 15.0


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in metres between two WGS84 points."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = phi2 - phi1
    d_lambda = math.radians(lon2 - lon1)
    a = math.sin(d_phi / 2.0) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2.0) ** 2
    return 2.0 * EARTH_RADIUS_M * math.asin(min(1.0, math.sqrt(a)))


def geo_similarity(distance_m: float, scale_m: float = CAMPUS_GEO_SCALE_M) -> float:
    """Map a distance in metres to evidence in ``[0, 1]``.

    Exponential decay, not a hard radius: a match 100 m away should be strong
    evidence, 1 km away should be weak, and nothing should be exactly zero
    because a reporter's "last seen" pin is an estimate.
    """
    distance = max(0.0, float(distance_m))
    if distance <= MIN_MEANINGFUL_M:
        return 1.0
    return max(0.0, min(1.0, math.exp(-(distance - MIN_MEANINGFUL_M) / max(1.0, scale_m))))


def distance_label(distance_m: float) -> str:
    """Human-readable distance for a reason chip."""
    metres = max(0.0, float(distance_m))
    if metres < 1000:
        return f"{metres:.0f} m"
    return f"{metres / 1000.0:.1f} km"


__all__ = [
    "CAMPUS_GEO_SCALE_M",
    "EARTH_RADIUS_M",
    "MIN_MEANINGFUL_M",
    "distance_label",
    "geo_similarity",
    "haversine_m",
]
