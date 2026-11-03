"""Reading printed text off a photo: serials, asset tags, ID cards.

The matcher already knows what to do with printed text -- ``visible_text`` feeds
the ``serial`` evidence feature, and a matching serial is an identity assertion
that overrides a weak colour match. What it lacked was a way to get that text
*out of a photograph*. This module is that reader, kept deliberately small and
optional:

* the engine is pluggable (``TesseractEngine`` is the default when
  ``pytesseract`` is installed, and any object with ``read(bytes) -> str``
  works -- which is how the tests drive it without a native dependency);
* with no engine, every entry point returns "nothing read" instead of raising,
  so ingest never depends on OCR being available;
* everything read passes through :func:`redact` **before** it is stored, because
  a student ID card carries a name, a roll number and often a phone number, and
  none of that belongs in a matching vector or a public feed.

Only serial-like tokens survive: mixed letters and digits (``SN-4471``,
``PF2ABCDE``), never a bare 10+ digit number, which is far more likely to be a
phone number or an identity number than a serial.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Protocol

from .objects import get_object_store

MAX_VISIBLE_TEXT = 6
MIN_SERIAL_LENGTH = 4
MAX_SERIAL_LENGTH = 24

_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
_PHONE = re.compile(r"(?<![\w])(?:\+\d{1,3}[\s-]?)?(?:\d[\s-]?){9,13}\d(?![\w])")
# 12-16 digits, optionally grouped as 4-4-4 (Aadhaar) or 4-4-4-4 (card).
_ID_NUMBER = re.compile(r"(?<!\d)(?:\d{4}[\s-]?){2,3}\d{4}(?!\d)")
_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9/._+-]*")
_SERIAL_LIKE = re.compile(rf"^[A-Z0-9][A-Z0-9/._-]{{{MIN_SERIAL_LENGTH - 1},{MAX_SERIAL_LENGTH - 1}}}$")

REDACTED_EMAIL = "[email]"
REDACTED_PHONE = "[phone]"
REDACTED_ID = "[id number]"


class OcrEngine(Protocol):
    name: str

    def read(self, data: bytes) -> str:
        """Return the text visible in one image, or an empty string."""


@dataclass(frozen=True)
class TesseractEngine:
    """Tesseract through ``pytesseract``, used only when both are installed."""

    name: str = "tesseract"

    def read(self, data: bytes) -> str:
        from io import BytesIO

        import pytesseract
        from PIL import Image

        with Image.open(BytesIO(data)) as image:
            return pytesseract.image_to_string(image.convert("RGB"))


@lru_cache(maxsize=1)
def resolve_engine() -> OcrEngine | None:
    """The OCR engine this installation can actually run, if any.

    Cached because probing for the native ``tesseract`` binary on every ingest
    would be wasteful, and because the answer cannot change without a restart.
    """
    try:
        import pytesseract
    except ImportError:
        return None
    engine = TesseractEngine()
    try:
        # A successful version probe is the only way to know the binary exists;
        # the Python package alone is not enough.
        pytesseract.get_tesseract_version()
    except (OSError, RuntimeError, ValueError):
        return None
    return engine


def redact(text: str) -> str:
    """Remove personal data from OCR output before it is stored.

    Order matters: e-mail addresses first (they can contain digit runs), then
    long identity numbers, then phone-like digit groups -- otherwise the phone
    pattern would swallow a spaced 12-digit identity number and mislabel it.
    Serials survive because they mix letters with digits.
    """
    redacted = _EMAIL.sub(REDACTED_EMAIL, text)
    redacted = _ID_NUMBER.sub(REDACTED_ID, redacted)
    return _PHONE.sub(REDACTED_PHONE, redacted)


def looks_like_serial(token: str) -> bool:
    """True for a printed identifier such as ``SN-4471`` or ``PF2ABCDE``."""
    candidate = token.strip().upper()
    if not _SERIAL_LIKE.match(candidate):
        return False
    has_digit = any(char.isdigit() for char in candidate)
    has_letter = any(char.isalpha() for char in candidate)
    return has_digit and has_letter


def visible_tokens(text: str) -> list[str]:
    """Serial-like, PII-free tokens in reading order, deduplicated and capped."""
    out: list[str] = []
    seen: set[str] = set()
    for match in _TOKEN.finditer(redact(text)):
        token = match.group(0).strip("._-/").upper()
        if token in seen or not looks_like_serial(token):
            continue
        seen.add(token)
        out.append(token)
        if len(out) >= MAX_VISIBLE_TEXT:
            break
    return out


def read_photo(data: bytes, engine: OcrEngine | None = None) -> str:
    """OCR one image's bytes; returns ``""`` when no engine is available."""
    reader = engine or resolve_engine()
    if reader is None:
        return ""
    try:
        return reader.read(data) or ""
    except Exception:  # noqa: BLE001 - a broken engine must never fail ingest
        return ""


def extract_visible_text(image_path: str | None, engine: OcrEngine | None = None) -> list[str]:
    """Printed serials on a stored report photo, redacted and deduplicated."""
    if not image_path:
        return []
    if engine is None and resolve_engine() is None:
        return []
    try:
        data = get_object_store().read(image_path)
    except (OSError, ValueError):
        return []
    return visible_tokens(read_photo(data, engine))
