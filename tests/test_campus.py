import random

from lostfound.campus import build_default_campus, get_campus, node_key


def test_graph_is_connected_and_symmetric():
    c = build_default_campus()
    keys = list(c.nodes)
    assert len(keys) >= 30
    for a in keys[:8]:
        for b in keys[:8]:
            assert c.minutes(a, b) == c.minutes(b, a)


def test_floors_are_adjacent_in_time():
    c = get_campus()
    one = c.minutes(node_key("library", 1), node_key("library", 2))
    three = c.minutes(node_key("library", 1), node_key("library", 4))
    assert one == 1.5
    assert three == 4.5


def test_walking_times_are_plausible():
    c = get_campus()
    lib_to_canteen = c.minutes(node_key("library", 1), node_key("canteen", 1))
    lib_to_hostel = c.minutes(node_key("library", 1), node_key("hostel_h1", 1))
    assert 1.0 <= lib_to_canteen <= 8.0
    assert 5.0 <= lib_to_hostel <= 20.0


def test_same_building_and_floor_gap():
    c = get_campus()
    assert c.same_building(node_key("library", 2), node_key("library", 4)) is True
    assert c.same_building("library-f2", "canteen-f1") is False
    assert c.floor_gap(node_key("library", 1), node_key("library", 4)) == 3
    assert c.floor_gap("main_gate", "library-f2") is None


def test_lpu_places_are_geocoded_and_separate_from_synthetic_graph():
    campus = build_default_campus()
    assert campus.map_center == (31.25327, 75.70324)
    assert len([building for building in campus.buildings.values() if building.latitude is not None]) == 3
    assert campus.minutes("lpu_technology-f1", "lpu_pharmacy-f1") is not None
    assert campus.minutes("lpu_technology-f1", "library-f1") is None


def test_resolve_location_specs():
    c = get_campus()
    rng = random.Random(0)
    assert c.resolve("canteen-f1", rng) == "canteen-f1"
    assert c.resolve("main_gate", rng) == "main_gate"
    for _ in range(10):
        key = c.resolve("library-*", rng)
        assert key.startswith("library-f")
    assert c.resolve("library", rng).startswith("library-f")
