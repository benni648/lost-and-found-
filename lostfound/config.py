"""Resolved configuration constants.

This module is the historical import surface (``from .config import DB_PATH``).
It no longer reads the environment itself: every value below is a re-export of
:mod:`lostfound.settings`, which is the single reader of ``os.environ`` and the
``.env`` file. Keeping the names here means no call site had to change when the
configuration moved to 12-factor form.
"""

from __future__ import annotations

from .settings import PROJECT_ROOT, Settings, get_settings, settings

# -- paths -----------------------------------------------------------------
ROOT = PROJECT_ROOT
DATA_DIR = settings.data_dir
MEDIA_DIR = settings.media
CAMPUS_JSON = settings.campus_file
DB_PATH = settings.database
EVAL_DIR = settings.evaluations

# -- reproducible corpus simulation ---------------------------------------
# The demo corpus is generated around a fixed "now" so every run, screenshot
# and report figure is reproducible.
SIM_NOW = settings.sim_now
SIM_WINDOW_DAYS = settings.sim_window_days
DEFAULT_SEED = settings.default_seed
DEFAULT_ITEM_COUNT = settings.default_item_count

# -- embedding registry names ---------------------------------------------
# Both active encoders are classical (hashed n-grams / HSV grid). This project
# deliberately loads and trains no deep model; see `embedders.py`.
TEXT_EMBEDDER = settings.text_embedder
IMAGE_EMBEDDER = settings.image_embedder

# -- ranking thresholds (operating point of the system) --------------------
PLAUSIBLE_FLOOR = settings.plausible_floor
PROB_ALERT = settings.prob_alert  # below this we say "no confident match"
MARGIN_MIN = settings.margin_min  # if #1 and #2 are this close, ask a human

__all__ = [
    "CAMPUS_JSON",
    "DATA_DIR",
    "DB_PATH",
    "DEFAULT_ITEM_COUNT",
    "DEFAULT_SEED",
    "EVAL_DIR",
    "IMAGE_EMBEDDER",
    "MARGIN_MIN",
    "MEDIA_DIR",
    "PLAUSIBLE_FLOOR",
    "PROB_ALERT",
    "ROOT",
    "SIM_NOW",
    "SIM_WINDOW_DAYS",
    "TEXT_EMBEDDER",
    "Settings",
    "get_settings",
    "settings",
]
