"""The settings object is the single reader of the environment.

These tests lock in that contract: env vars win over defaults, derived paths
follow DATA_DIR, and a malformed value fails loudly at load instead of silently
falling back.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from lostfound.settings import Settings


def _fresh(**env: str) -> Settings:
    """A Settings built from the given env vars only (no ``.env`` file)."""
    return Settings(_env_file=None, **env)


def test_defaults_are_the_demo_configuration():
    s = _fresh()
    assert s.env == "dev"
    assert s.is_production is False
    assert s.prob_alert == 0.55
    assert s.margin_min == 0.06
    assert s.plausible_floor == 0.30
    assert s.database.name == "lostfound.db"
    assert s.text_embedder == "hash-char-ngram-v1"
    assert s.image_embedder == "hist-grid-v1"
    assert s.supabase_url is None
    assert s.supabase_anon_key is None
    assert s.async_matching is False


def test_environment_overrides_defaults(monkeypatch):
    monkeypatch.setenv("ENV", "prod")
    monkeypatch.setenv("PROB_ALERT", "0.8")
    monkeypatch.setenv("DEFAULT_ITEM_COUNT", "42")
    monkeypatch.setenv("ASYNC_MATCHING", "true")
    s = _fresh()
    assert s.env == "prod"
    assert s.is_production is True
    assert s.prob_alert == 0.8
    assert s.default_item_count == 42
    assert s.async_matching is True


def test_alert_and_floor_overrides_drive_the_default_ui_bands(monkeypatch):
    monkeypatch.setenv("PROB_ALERT", "0.8")
    monkeypatch.setenv("PLAUSIBLE_FLOOR", "0.2")
    s = _fresh()
    assert s.probable_band == 0.8
    assert s.strong_band == 0.8
    assert s.weak_band == 0.2


@pytest.mark.parametrize("origin", ["*", "https://*.example", "https://example.com/path", "https://user@host"])
def test_invalid_cors_origins_are_rejected(origin):
    with pytest.raises(ValidationError):
        _fresh(cors_origins=origin)


def test_cors_origins_are_split_and_trimmed(monkeypatch):
    monkeypatch.setenv("CORS_ORIGINS", "https://a.example, https://b.example ,")
    assert _fresh().cors_origin_list == ["https://a.example", "https://b.example"]


def test_data_dir_moves_the_derived_paths(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    s = _fresh()
    assert s.media == tmp_path / "media"
    assert s.campus_file == tmp_path / "campus.json"
    assert s.database == tmp_path / "lostfound.db"


def test_explicit_paths_win_over_the_derived_ones(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DB_PATH", str(tmp_path / "elsewhere.db"))
    s = _fresh()
    assert s.database == tmp_path / "elsewhere.db"
    assert s.media == tmp_path / "media"  # still derived from DATA_DIR


def test_a_malformed_value_fails_loudly(monkeypatch):
    monkeypatch.setenv("PROB_ALERT", "not-a-number")
    with pytest.raises(ValidationError):
        _fresh()


def test_invalid_default_campus_timezone_is_rejected():
    with pytest.raises(ValidationError, match="valid IANA timezone"):
        _fresh(default_campus_timezone="Not/A_Real_Zone")


def test_config_reexports_the_resolved_values():
    """`lostfound.config` must never drift from the settings object."""
    from lostfound import config
    from lostfound.settings import settings as live

    expected = {
        "PROB_ALERT": live.prob_alert,
        "MARGIN_MIN": live.margin_min,
        "PLAUSIBLE_FLOOR": live.plausible_floor,
        "DB_PATH": live.database,
        "MEDIA_DIR": live.media,
        "TEXT_EMBEDDER": live.text_embedder,
        "IMAGE_EMBEDDER": live.image_embedder,
    }
    for name, value in expected.items():
        assert getattr(config, name) == value, f"config.{name} drifted from settings"
    assert config.DATA_DIR == config.ROOT / "data"
