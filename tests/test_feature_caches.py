import json

from lostfound.features import _cached_attrs, _cached_tokens


def test_feature_caches_stay_within_fixed_capacity_during_10k_input_soak():
    _cached_attrs.cache_clear()
    _cached_tokens.cache_clear()

    for index in range(10_000):
        _cached_attrs(json.dumps({"category": f"category-{index}"}))
        _cached_tokens(f"item description {index}")

    attrs_info = _cached_attrs.cache_info()
    tokens_info = _cached_tokens.cache_info()
    assert attrs_info.maxsize == 4096
    assert attrs_info.currsize == 4096
    assert tokens_info.maxsize == 4096
    assert tokens_info.currsize == 4096
    assert attrs_info.misses == 10_000
    assert tokens_info.misses == 10_000


def test_feature_caches_retain_recent_entries_after_eviction():
    _cached_attrs.cache_clear()
    _cached_tokens.cache_clear()
    recent_attrs = json.dumps({"category": "latest"})
    recent_description = "latest unique item description"

    for index in range(4096):
        _cached_attrs(json.dumps({"category": f"category-{index}"}))
        _cached_tokens(f"item description {index}")
    _cached_attrs(recent_attrs)
    _cached_tokens(recent_description)
    before_attrs = _cached_attrs.cache_info().hits
    before_tokens = _cached_tokens.cache_info().hits

    _cached_attrs(recent_attrs)
    _cached_tokens(recent_description)

    assert _cached_attrs.cache_info().hits == before_attrs + 1
    assert _cached_tokens.cache_info().hits == before_tokens + 1
