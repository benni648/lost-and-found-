"""Canonical report attribute schema.

One schema, two extractors: a vision extractor fills this from a photo, a
language extractor fills the same fields from free text. Every field is
optional and carries its own confidence, because a one-line report should not
be punished for the fields it never mentioned.

``None`` means "not stated", which is *evidence-free*, never a mismatch.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

LIST_FIELDS = ("damages", "marks", "visible_text")


@dataclass
class Attributes:
    category: str | None = None
    brand: str | None = None
    model: str | None = None
    primary_color: str | None = None
    secondary_color: str | None = None
    material: str | None = None
    size: str | None = None
    damages: list[str] = field(default_factory=list)
    marks: list[str] = field(default_factory=list)
    visible_text: list[str] = field(default_factory=list)
    confidence: dict[str, float] = field(default_factory=dict)

    # ------------------------------------------------------------------ utils
    def set(self, name: str, value, conf: float = 0.8) -> None:
        if value in (None, "", []):
            return
        setattr(self, name, value)
        self.confidence[name] = conf

    def conf(self, name: str) -> float:
        return float(self.confidence.get(name, 0.0))

    def stated(self) -> list[str]:
        out = []
        for name, value in asdict(self).items():
            if name == "confidence":
                continue
            if value not in (None, "", []):
                out.append(name)
        return out

    def completeness(self) -> float:
        """0..1, how much of the schema this report actually pins down."""
        key_fields = ("category", "brand", "primary_color", "material")
        have = sum(1 for f in key_fields if getattr(self, f))
        extras = min(len(self.damages) + len(self.marks), 2)
        return round(min(1.0, (have + extras) / 6.0), 3)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> Attributes:
        return cls(**{k: v for k, v in data.items() if k in cls.__annotations__})
