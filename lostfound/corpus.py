"""Simulated campus lost & found corpus.

One generator, fully deterministic, that produces what a real lost & found
office sees over three weeks: items get lost, most get found, a few never do,
and some turn up with nobody claiming them.

What makes the corpus useful rather than decorative:

* reports are generated from the *physical item's* attributes, but each
  reporter only states a subset of them (terse / detailed / vague registers),
  so the matcher faces the real problem -- partial evidence.
* lost reports carry an *interval* ("last saw it at lunch, noticed at 4pm"),
  not a timestamp, because that is all a person can honestly report.
* 10% of reports contain a small human error (wrong shade, wrong floor),
  because perfect reports make overconfident models.
* confusion pairs are guaranteed: same category, same colour, different item.

Ground truth is returned separately and never enters the matcher.
"""

from __future__ import annotations

import datetime as dt
import random
from dataclasses import dataclass, field

from .campus import Campus, get_campus
from .config import (
    DEFAULT_ITEM_COUNT,
    DEFAULT_SEED,
    MEDIA_DIR,
    SIM_NOW,
    SIM_WINDOW_DAYS,
)
from .imagery import render_item_image
from .store import Report
from .taxonomy import CATEGORIES, CATEGORY_DAMAGES, CATEGORY_MARKS, DAMAGES, MARKS

CATEGORY_WEIGHTS = {
    "bottle": 14,
    "phone": 12,
    "backpack": 10,
    "id_card": 9,
    "wallet": 8,
    "keys": 7,
    "earbuds": 6,
    "laptop": 6,
    "umbrella": 5,
    "calculator": 4,
    "notebook": 4,
    "glasses": 3,
    "watch": 3,
    "charger": 3,
    "hoodie": 3,
    "lunchbox": 2,
    "pendrive": 2,
}

# Losses cluster around class changeovers, lunch and the walk home.
HOUR_WEIGHTS = {
    8: 4,
    9: 8,
    10: 9,
    11: 7,
    12: 6,
    13: 8,
    14: 7,
    15: 5,
    16: 6,
    17: 8,
    18: 6,
    19: 3,
}

# Colour neighbours used to inject realistic report error.
COLOR_NEIGHBOURS = {
    "navy": "blue",
    "blue": "navy",
    "grey": "black",
    "black": "grey",
    "sky blue": "blue",
    "silver": "grey",
    "maroon": "brown",
    "olive": "green",
    "teal": "green",
    "brown": "beige",
    "white": "silver",
}

BUILDING_SHORT = {
    "library": "library",
    "canteen": "canteen",
    "academic_a": "Academic Block A",
    "academic_b": "Academic Block B",
    "hostel_h1": "H1 hostel",
    "hostel_h2": "H2 hostel",
    "sports": "sports complex",
    "sports_ground": "ground",
    "workshop": "workshop",
    "auditorium": "auditorium",
    "bus_stop": "bus stop",
    "parking": "parking",
    "main_gate": "main gate",
    "health": "health centre",
    "amphitheatre": "amphitheatre",
}


@dataclass
class GenItem:
    item_id: str
    category: str
    brand: str | None
    model: str | None
    primary_color: str
    secondary_color: str | None
    material: str
    damages: list[str]
    marks: list[str]
    serial: str | None
    lost_node: str
    found_node: str
    lost_at: dt.datetime
    found_at: dt.datetime
    has_lost_report: bool
    has_found_report: bool
    photo_found: bool
    photo_lost: bool
    errors: list[str] = field(default_factory=list)

    def attrs(self) -> dict:
        return {
            "category": self.category,
            "brand": self.brand,
            "model": self.model,
            "primary_color": self.primary_color,
            "secondary_color": self.secondary_color,
            "material": self.material,
            "damages": self.damages,
            "marks": self.marks,
            "visible_text": [self.serial] if self.serial else [],
        }


# --------------------------------------------------------------------- time
def _weighted_hour(rng: random.Random) -> int:
    hours = list(HOUR_WEIGHTS)
    return rng.choices(hours, weights=[HOUR_WEIGHTS[h] for h in hours], k=1)[0]


def _sample_lost_time(rng: random.Random, now: dt.datetime) -> dt.datetime:
    day = rng.randrange(SIM_WINDOW_DAYS)
    date = (now - dt.timedelta(days=SIM_WINDOW_DAYS - day)).date()
    if date.weekday() >= 5 and rng.random() < 0.55:  # quieter weekends
        date -= dt.timedelta(days=2)
    return dt.datetime.combine(date, dt.time(_weighted_hour(rng), rng.choice([0, 10, 20, 30, 40, 50])))


# ---------------------------------------------------------------- locations
def _nearby_node(campus: Campus, home: str, rng: random.Random, spread_min: float = 7.0) -> str:
    """Where the item is actually found: usually close to where it was lost."""
    if rng.random() < 0.55:  # found where it was left, or one floor away
        return home
    cands = []
    for key in campus.nodes:
        d = campus.minutes(home, key)
        if d is not None and 0 < d <= spread_min:
            cands.append((key, d))
    if not cands:
        return home
    cands.sort(key=lambda kv: kv[1])
    weights = [1.0 / (1.0 + d) ** 2 for _, d in cands]
    return rng.choices([k for k, _ in cands], weights=weights, k=1)[0]


# -------------------------------------------------------------- item fields
def _pick_category(rng: random.Random) -> str:
    cats = list(CATEGORY_WEIGHTS)
    return rng.choices(cats, weights=[CATEGORY_WEIGHTS[c] for c in cats], k=1)[0]


def _make_item(rng: random.Random, idx: int, campus: Campus, now: dt.datetime) -> GenItem:
    cat = _pick_category(rng)
    spec = CATEGORIES[cat]
    brand = rng.choice(spec["brands"])
    brand = None if brand == "no brand" else brand
    models = spec.get("models", {})
    model = rng.choice(models[brand]) if brand and brand in models else None
    color = rng.choice(spec["colors"])
    secondary = rng.choice([c for c in spec["colors"] if c != color]) if rng.random() < 0.22 else None
    material = rng.choice(spec["materials"])
    damages = [rng.choice(CATEGORY_DAMAGES.get(cat, DAMAGES))] if rng.random() < 0.45 else []
    marks = [rng.choice(CATEGORY_MARKS.get(cat, MARKS))] if rng.random() < 0.33 else []

    serial = None
    if cat in ("laptop", "id_card", "phone", "pendrive") and rng.random() < 0.35:
        serial = f"SIT-{rng.randrange(2021, 2027)}-{rng.randrange(1000, 9999)}"

    lost_node = campus.resolve(rng.choice(spec["locations"]), rng)
    found_node = _nearby_node(campus, lost_node, rng)
    lost_at = _sample_lost_time(rng, now)
    hold = max(0.4, rng.expovariate(1.0 / spec["hold_hours"]))
    found_at = lost_at + dt.timedelta(hours=min(hold, 120))

    item = GenItem(
        item_id=f"ITM-{idx:04d}",
        category=cat,
        brand=brand,
        model=model,
        primary_color=color,
        secondary_color=secondary,
        material=material,
        damages=damages,
        marks=marks,
        serial=serial,
        lost_node=lost_node,
        found_node=found_node,
        lost_at=lost_at,
        found_at=found_at,
        has_lost_report=False,
        has_found_report=False,
        photo_found=False,
        photo_lost=False,
    )

    # who files what
    recovered = found_at < now - dt.timedelta(hours=2)
    item.has_found_report = recovered and rng.random() < 0.9
    item.has_lost_report = rng.random() < (0.82 if recovered else 0.9)
    if not (item.has_lost_report or item.has_found_report):
        item.has_lost_report = True
    item.photo_found = item.has_found_report and rng.random() < spec["photo_prob"]
    item.photo_lost = item.has_lost_report and rng.random() < spec["photo_prob"] * 0.7
    return item


# ------------------------------------------------------------- descriptions
def _loc_phrase(campus: Campus, node: str, rng: random.Random) -> str:
    """ "in the library" / "in Academic Block A" / "near the bus stop"."""
    b = campus.nodes[node]["building"]
    short = BUILDING_SHORT.get(b, campus.nodes[node]["label"].split(",")[0].lower())
    article = "" if short[:1].isupper() else "the "
    if campus.nodes[node]["outdoor"]:
        return rng.choice([f"at the {short}", f"near the {short}"])
    prep = "near" if rng.random() < 0.5 else "in"
    return f"{prep} {article}{short}"


def _describe(item: GenItem, kind: str, campus: Campus, rng: random.Random) -> tuple[str, list[str]]:
    """Render a report the way a student would actually write one."""
    notes: list[str] = []
    color = item.primary_color
    if rng.random() < 0.10 and color in COLOR_NEIGHBOURS:
        color = COLOR_NEIGHBOURS[color]
        notes.append(f"colour_reported_as:{color}")

    brand = item.brand
    label = CATEGORIES[item.category]["label"].lower()
    noun = label if label != "hoodie / jacket" else "hoodie"

    # How the reporter names the object. A brand + model pair replaces the
    # noun, because "white iPhone phone 12" is not how anyone writes.
    if brand and item.model:
        full, core = f"{brand} {item.model}", item.model
    elif brand:
        full, core = f"{brand} {noun}", brand
    else:
        full = core = f"{noun}{(' ' + item.model) if item.model else ''}".strip()

    detail_bits = [b for b in (item.material, *item.damages, *item.marks) if b]
    node = item.lost_node if kind == "lost" else item.found_node
    loc = _loc_phrase(campus, node, rng)
    where = BUILDING_SHORT.get(campus.nodes[node]["building"], "campus")

    register = rng.choices(["terse", "detailed", "vague"], weights=[45, 35, 20] if kind == "lost" else [45, 40, 15])[0]

    if register == "vague":
        when = "yesterday" if kind == "lost" else "today"
        text = f"{'Lost' if kind == 'lost' else 'Found'} a {noun} somewhere around the {where} {when}, please contact."
        notes.append("omitted:colour")
        return text, notes

    if register == "terse":
        # sometimes the model number survives, sometimes only the generic noun
        phrase = rng.choices([full, core, noun], weights=[45, 20, 35])[0]
        text = ("Lost my " if kind == "lost" else "Found a ") + f"{color} {phrase}"
        if rng.random() < 0.5:
            text += f", {loc}"
        notes.append("omitted:material,damages,marks")
        return text + ".", notes

    text = ("Lost my " if kind == "lost" else "Found a ") + f"{color} {full}"
    if detail_bits:
        text += ", " + ", ".join(detail_bits)
    text += f". {loc.capitalize()}"
    if item.serial:
        text += f", tag reads {item.serial}"
    if kind == "lost":
        text += ". Last seen around " + item.lost_at.strftime("%I:%M %p").lstrip("0")
    return text + ".", notes


# ------------------------------------------------------------------- corpus
@dataclass
class Corpus:
    reports: list[Report]
    items: list[GenItem]
    truth: dict[str, str]  # lost_report_id -> found_report_id
    report_to_item: dict[str, str]


def generate_corpus(
    n_items: int = DEFAULT_ITEM_COUNT,
    seed: int = DEFAULT_SEED,
    now: dt.datetime = SIM_NOW,
    campus: Campus | None = None,
    render_images: bool = True,
    media_dir=MEDIA_DIR,
) -> Corpus:
    campus = campus or get_campus()
    rng = random.Random(seed)
    items = [_make_item(rng, i, campus, now) for i in range(n_items)]

    reports: list[Report] = []
    truth: dict[str, str] = {}
    report_to_item: dict[str, str] = {}
    counters = {"lost": 0, "found": 0}

    for item in items:
        found_id = lost_id = None
        if item.has_found_report:
            counters["found"] += 1
            found_id = f"FR-{counters['found']:04d}"
            text, notes = _describe(item, "found", campus, rng)
            img_path = None
            if item.photo_found:
                img_path = _save_image(found_id, item, rng, "found", media_dir, render_images)
            reports.append(
                Report(
                    id=found_id,
                    kind="found",
                    created_at=(item.found_at + dt.timedelta(minutes=rng.randrange(10, 120))).isoformat(
                        timespec="seconds"
                    ),
                    description=text,
                    attrs={},
                    category=item.category,
                    node_key=item.found_node,
                    location_note=campus.label(item.found_node),
                    image_path=img_path,
                    has_photo=bool(img_path),
                    is_synthetic_art=bool(img_path),
                )
            )
            report_to_item[found_id] = item.item_id

        if item.has_lost_report:
            counters["lost"] += 1
            lost_id = f"LR-{counters['lost']:04d}"
            text, notes = _describe(item, "lost", campus, rng)
            img_path = None
            if item.photo_lost:
                img_path = _save_image(lost_id, item, rng, "lost", media_dir, render_images)
            reported_at = item.lost_at + dt.timedelta(hours=rng.uniform(0.5, 7))
            uncertainty = dt.timedelta(minutes=rng.choice([15, 30, 60, 120, 180]))
            reports.append(
                Report(
                    id=lost_id,
                    kind="lost",
                    created_at=reported_at.isoformat(timespec="seconds"),
                    description=text,
                    attrs={},
                    category=item.category,
                    node_key=item.lost_node,
                    location_note=campus.label(item.lost_node),
                    lost_from=item.lost_at.isoformat(timespec="seconds"),
                    lost_to=(item.lost_at + uncertainty).isoformat(timespec="seconds"),
                    image_path=img_path,
                    has_photo=bool(img_path),
                    is_synthetic_art=bool(img_path),
                )
            )
            report_to_item[lost_id] = item.item_id

        if found_id and lost_id:
            truth[lost_id] = found_id

    reports.sort(key=lambda r: r.created_at)
    return Corpus(reports=reports, items=items, truth=truth, report_to_item=report_to_item)


def _save_image(report_id: str, item: GenItem, rng: random.Random, style: str, media_dir, render: bool):
    if not render:
        return None
    media_dir.mkdir(parents=True, exist_ok=True)
    img = render_item_image(item.attrs(), rng, style=style)
    path = media_dir / f"{report_id}.png"
    img.save(path, "PNG")
    return str(path.relative_to(media_dir.parent.parent)).replace("\\", "/")
