"""Campus model: buildings, floors, outdoor places and walkable distances.

The campus is a small weighted graph. Every node is a *place a student can
stand in* -- either a floor of a building (``library-f3``) or an outdoor
location (``main_gate``). Edge weights are minutes.

Distance is graph distance, not straight-line distance, because that is what
makes the location signal meaningful: "found in the library basement" and
"lost in the library 3rd floor" are 3 minutes apart, while two buildings that
look close on a map can be 8 minutes apart through the campus gate.
"""

from __future__ import annotations

import heapq
import json
import math
from dataclasses import dataclass, field
from pathlib import Path

from .config import CAMPUS_JSON

WALK_SPEED_M_PER_MIN = 75.0
DETOUR_FACTOR = 1.35
FLOOR_MINUTES = 1.5


@dataclass
class Building:
    key: str
    name: str
    short: str
    kind: str
    x: float
    y: float
    floors: int = 1
    width: float = 40.0
    height: float = 30.0
    latitude: float | None = None
    longitude: float | None = None

    @property
    def is_outdoor(self) -> bool:
        return self.floors == 0


@dataclass
class Campus:
    buildings: dict[str, Building] = field(default_factory=dict)
    nodes: dict[str, dict] = field(default_factory=dict)
    edges: list[tuple[str, str, float]] = field(default_factory=list)
    map_center: tuple[float, float] | None = None
    map_bounds: tuple[tuple[float, float], tuple[float, float]] | None = None
    _adj: dict[str, list[tuple[str, float]]] = field(default_factory=dict)
    _dist: dict[str, dict[str, float]] = field(default_factory=dict)

    # ---------------------------------------------------------------- build
    def add_building(self, b: Building) -> None:
        self.buildings[b.key] = b
        if b.is_outdoor:
            self._add_node(
                b.key,
                b.key,
                None,
                b.name,
                b.x,
                b.y,
                outdoor=True,
                latitude=b.latitude,
                longitude=b.longitude,
            )
            return
        for floor in range(1, b.floors + 1):
            nk = node_key(b.key, floor)
            label = b.name if b.floors == 1 else f"{b.name}, Floor {floor}"
            self._add_node(nk, b.key, floor, label, b.x, b.y, latitude=b.latitude, longitude=b.longitude)
        for floor in range(1, b.floors):
            self._add_edge(
                node_key(b.key, floor),
                node_key(b.key, floor + 1),
                FLOOR_MINUTES,
                kind="vertical",
            )

    def _add_node(self, key, building, floor, label, x, y, outdoor=False, latitude=None, longitude=None) -> None:
        self.nodes[key] = {
            "key": key,
            "building": building,
            "floor": floor,
            "label": label,
            "x": x,
            "y": y,
            "outdoor": outdoor,
            "latitude": latitude,
            "longitude": longitude,
        }

    def _add_edge(self, a: str, b: str, minutes: float, kind: str = "walk") -> None:
        self.edges.append((a, b, round(minutes, 2)))
        self._adj.setdefault(a, []).append((b, minutes))
        self._adj.setdefault(b, []).append((a, minutes))

    def connect_entrances(self, min_minutes: float = 0.6) -> None:
        """Outdoor walkway between every pair of building entrances + places."""
        entrances = [k for k, n in self.nodes.items() if n["floor"] in (1, None)]
        for i, a in enumerate(entrances):
            for b in entrances[i + 1 :]:
                na, nb = self.nodes[a], self.nodes[b]
                if na["building"] == nb["building"]:
                    continue
                if (na["latitude"] is None) != (nb["latitude"] is None):
                    continue
                metres = math.dist((na["x"], na["y"]), (nb["x"], nb["y"]))
                minutes = max(min_minutes, metres * DETOUR_FACTOR / WALK_SPEED_M_PER_MIN)
                self._add_edge(a, b, minutes, kind="walk")

    # ------------------------------------------------------------- distances
    def _ensure_distances(self) -> None:
        if self._dist:
            return
        keys = list(self.nodes)
        for src in keys:
            dist = {src: 0.0}
            heap = [(0.0, src)]
            while heap:
                d, u = heapq.heappop(heap)
                if d > dist.get(u, math.inf):
                    continue
                for v, w in self._adj.get(u, ()):  # noqa: B007
                    nd = d + w
                    if nd < dist.get(v, math.inf) - 1e-9:
                        dist[v] = nd
                        heapq.heappush(heap, (nd, v))
            self._dist[src] = dist

    def minutes(self, a: str | None, b: str | None) -> float | None:
        if not a or not b:
            return None
        if a not in self.nodes or b not in self.nodes:
            return None
        self._ensure_distances()
        d = self._dist.get(a, {}).get(b)
        return None if d is None else round(d, 2)

    def same_building(self, a: str | None, b: str | None) -> bool | None:
        if not a or not b or a not in self.nodes or b not in self.nodes:
            return None
        return self.nodes[a]["building"] == self.nodes[b]["building"]

    def floor_gap(self, a: str | None, b: str | None) -> int | None:
        if not a or not b or a not in self.nodes or b not in self.nodes:
            return None
        fa, fb = self.nodes[a]["floor"], self.nodes[b]["floor"]
        if fa is None or fb is None:
            return None
        return abs(fa - fb)

    def resolve(self, spec: str, rng) -> str:
        """Resolve a taxonomy location spec to a concrete node key.

        ``library``      -> a random floor of the library (weighted to low floors)
        ``library-*``    -> any floor
        ``canteen-f1``   -> that exact node
        """
        if spec in self.nodes:
            return spec
        if spec.endswith("-*"):
            base = spec[:-2]
            floors = [k for k, n in self.nodes.items() if n["building"] == base]
            return rng.choice(sorted(floors)) if floors else spec
        if "-f" in spec:
            base, _, fl = spec.rpartition("-f")
            key = f"{base}-f{fl}"
            if key in self.nodes:
                return key
        floors = sorted(k for k, n in self.nodes.items() if n["building"] == spec)
        if floors:
            weights = [1.0 / (i + 1) for i in range(len(floors))]
            return rng.choices(floors, weights=weights, k=1)[0]
        return spec

    def label(self, key: str | None) -> str:
        if not key:
            return "unknown location"
        return self.nodes.get(key, {}).get("label", key)

    # -------------------------------------------------------------- (de)serial
    def to_dict(self) -> dict:
        return {
            "buildings": [
                {
                    "key": b.key,
                    "name": b.name,
                    "short": b.short,
                    "kind": b.kind,
                    "x": b.x,
                    "y": b.y,
                    "floors": b.floors,
                    "width": b.width,
                    "height": b.height,
                    "latitude": b.latitude,
                    "longitude": b.longitude,
                }
                for b in self.buildings.values()
            ],
            "nodes": list(self.nodes.values()),
            "edges": [{"a": a, "b": b, "minutes": m} for a, b, m in self.edges],
            "map_center": self.map_center,
            "map_bounds": self.map_bounds,
        }

    def save(self, path: Path | None = None) -> Path:
        path = path or CAMPUS_JSON
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")
        return path

    def stats(self) -> str:
        indoor = sum(1 for n in self.nodes.values() if not n["outdoor"])
        return f"{len(self.buildings)} places, {len(self.nodes)} nodes ({indoor} indoor), {len(self.edges)} edges"


def node_key(building: str, floor: int) -> str:
    return f"{building}-f{floor}"


def build_default_campus() -> Campus:
    """Build the legacy demo graph and geocoded LPU report locations."""
    c = Campus()
    buildings = [
        Building("main_gate", "Main Gate", "GATE", "gate", 0, 0, floors=0),
        Building("bus_stop", "Campus Bus Stop", "BUS", "transit", -45, -25, floors=0),
        Building("parking", "Two-Wheeler Parking", "PARK", "parking", 25, 68, floors=0),
        Building("amphitheatre", "Open Amphitheatre", "AMPH", "outdoor", 60, 150, floors=0),
        Building("sports_ground", "Sports Ground", "GRND", "outdoor", 272, -158, floors=0),
        Building("admin", "Administrative Block", "ADM", "admin", 55, 35, 3, 44, 28),
        Building("academic_a", "Academic Block A", "ABA", "academic", 135, 95, 4, 46, 30),
        Building("academic_b", "Academic Block B", "ABB", "academic", 235, 70, 4, 46, 30),
        Building("library", "Central Library", "LIB", "library", 150, -35, 4, 52, 34),
        Building("canteen", "Canteen & Food Court", "CANT", "canteen", 55, -65, 1, 40, 26),
        Building("hostel_h1", "Boys Hostel H1", "H1", "hostel", -95, 118, 5, 38, 34),
        Building("hostel_h2", "Girls Hostel H2", "H2", "hostel", -152, 32, 5, 38, 34),
        Building("sports", "Sports Complex", "SPRT", "sports", 255, -125, 1, 40, 30),
        Building("workshop", "Workshop & Labs", "WSHP", "lab", 300, 142, 2, 44, 30),
        Building("auditorium", "Auditorium", "AUD", "auditorium", 108, 178, 1, 50, 34),
        Building("health", "Health Centre", "HLTH", "health", -70, -105, 1, 30, 24),
    ]
    # The layout above is drawn on a compact grid; scale it out to a ~500 m
    # wide campus so that walk times match a real one.
    for b in buildings:
        b.x *= 2.0
        b.y *= 2.0
    for b in buildings:
        c.add_building(b)

    lpu_origin_lat, lpu_origin_lon = 31.25327, 75.70324
    c.map_center = (lpu_origin_lat, lpu_origin_lon)
    c.map_bounds = ((31.2457534, 75.6973474), (31.2607949, 75.7091258))
    lpu_places = [
        ("lpu_management", "Lovely Institute of Management", "LIM", "academic", 31.2586388, 75.7088212),
        ("lpu_technology", "Lovely Institute of Technology", "LIT", "academic", 31.2579180, 75.7069431),
        ("lpu_pharmacy", "Lovely Institute of Pharmacy", "PHARM", "health", 31.2575791, 75.7072785),
    ]
    for key, name, short, kind, latitude, longitude in lpu_places:
        x = (longitude - lpu_origin_lon) * 111_320 * math.cos(math.radians(lpu_origin_lat))
        y = (latitude - lpu_origin_lat) * 110_574
        c.add_building(
            Building(
                key,
                name,
                short,
                kind,
                x,
                y,
                floors=1,
                width=60,
                height=44,
                latitude=latitude,
                longitude=longitude,
            )
        )
    c.connect_entrances()
    return c


_CAMPUS: Campus | None = None


def get_campus() -> Campus:
    global _CAMPUS
    if _CAMPUS is None:
        _CAMPUS = build_default_campus()
    return _CAMPUS
