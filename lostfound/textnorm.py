"""Reading free-text lost/found reports into the canonical schema.

This is intentionally a rule-based extractor, not an LLM: it is deterministic,
it runs on CPU in microseconds for 300 reports, and every extraction is
inspectable. The LLM/VLM extractor plugs into exactly the same function
signature (see ``ingest_image`` in corpus.py for the vision side).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .schema import Attributes
from .taxonomy import CATEGORIES, COLOR_ALIASES, COLORS, DAMAGES, MARKS, MATERIALS

_WORD = re.compile(r"[a-z0-9]+")
_MODEL_TOKEN = re.compile(r"\b(?:note\s*)?(\d{1,2})\s*(pro|plus|max|lite)?\b", re.I)
_SERIAL = re.compile(r"\b[A-Z0-9]{2,}[-0-9]{3,}\b")

# Alias index: longest alias first so "water bottle" beats "bottle".
_ALIAS_TO_CAT: list[tuple[str, str]] = []
for _cat, _spec in CATEGORIES.items():
    for _alias in _spec["aliases"]:
        _ALIAS_TO_CAT.append((_alias, _cat))
_ALIAS_TO_CAT.sort(key=lambda kv: -len(kv[0]))

_BRAND_INDEX: list[tuple[str, str, str]] = []
for _cat, _spec in CATEGORIES.items():
    for _brand in _spec.get("brands", []):
        if _brand != "no brand":
            _BRAND_INDEX.append((_brand.lower(), _brand, _cat))
_BRAND_INDEX.sort(key=lambda kv: -len(kv[0]))

_COLOR_INDEX: list[tuple[str, str]] = sorted(
    [(c, c) for c in COLORS] + list(COLOR_ALIASES.items()), key=lambda kv: -len(kv[0])
)


@dataclass
class Extraction:
    attrs: Attributes
    tokens: list[str]


def tokenize(text: str) -> list[str]:
    return _WORD.findall(text.lower())


def extract(text: str) -> Extraction:
    low = " " + re.sub(r"\s+", " ", text.lower()) + " "
    attrs = Attributes()
    tokens = tokenize(text)

    # -- category -----------------------------------------------------------
    for alias, cat in _ALIAS_TO_CAT:
        if alias in low:
            attrs.set("category", cat, 0.9 if len(alias) > 4 else 0.75)
            break

    # -- brand --------------------------------------------------------------
    for key, brand, cat in _BRAND_INDEX:
        if key in low and attrs.category in (None, cat):
            attrs.set("brand", brand, 0.85)
            break

    # -- colours (first mention is primary) ---------------------------------
    colours: list[str] = []
    for word, canonical in _COLOR_INDEX:
        if re.search(rf"\b{re.escape(word)}\b", low) and canonical not in colours:
            colours.append(canonical)
    if colours:
        attrs.set("primary_color", colours[0], 0.85)
        if len(colours) > 1:
            attrs.set("secondary_color", colours[1], 0.6)

    # -- material -----------------------------------------------------------
    for mat in MATERIALS:
        if re.search(rf"\b{re.escape(mat)}\b", low):
            attrs.set("material", mat, 0.7)
            break

    # -- model number -------------------------------------------------------
    cat_name, brand_name = attrs.category, attrs.brand
    known = CATEGORIES.get(cat_name or "", {}).get("models", {}).get(brand_name or "", [])
    if known:
        for cand in known:
            if cand.lower() in low:
                attrs.set("model", cand, 0.9)
                break
        else:
            m = _MODEL_TOKEN.search(low)
            if m:
                attrs.set("model", m.group(1), 0.6)

    # -- damage / distinguishing marks --------------------------------------
    dmg = [d for d in DAMAGES if d in low]
    if dmg:
        attrs.damages = dmg[:3]
        attrs.confidence["damages"] = 0.8
    marks = [m for m in MARKS if m in low]
    if marks:
        attrs.marks = marks[:3]
        attrs.confidence["marks"] = 0.8

    # -- serials printed on the item ---------------------------------------
    serials = list(_SERIAL.findall(text))
    if serials:
        attrs.visible_text = serials[:3]
        attrs.confidence["visible_text"] = 0.7

    return Extraction(attrs=attrs, tokens=tokens)


def content_tokens(text: str) -> set[str]:
    """Tokens worth comparing: drops stopwords, keeps model numbers."""
    from .stopwords import STOPWORDS

    return {t for t in tokenize(text) if t not in STOPWORDS and len(t) > 1}
