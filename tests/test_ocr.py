"""OCR feeds printed serials into ``visible_text``, with personal data removed.

The engine is injected, so these tests need neither ``pytesseract`` nor the
native ``tesseract`` binary: that is the whole point of the adapter.
"""

from __future__ import annotations

from dataclasses import dataclass

from lostfound import ocr, pipeline
from lostfound.store import Report


@dataclass
class FakeEngine:
    text: str
    name: str = "fake"

    def read(self, data: bytes) -> str:
        return self.text


def _report() -> Report:
    return Report(
        id="LR-0001",
        kind="lost",
        created_at="2026-10-28T00:00:00Z",
        description="black laptop bag",
        attrs={},
        image_path="reports/LR-0001.jpg",
    )


def test_redact_removes_email_phone_and_id_numbers():
    dirty = "Ravi Kumar ravi.kumar@lpu.co.in +91 98765 43210 Aadhaar 1234 5678 9012"
    clean = ocr.redact(dirty)
    assert "ravi.kumar" not in clean
    assert "98765" not in clean
    assert "1234 5678" not in clean
    assert ocr.REDACTED_EMAIL in clean
    assert ocr.REDACTED_PHONE in clean
    assert ocr.REDACTED_ID in clean


def test_serial_detection_keeps_identifiers_and_rejects_numbers():
    assert ocr.looks_like_serial("SN-4471")
    assert ocr.looks_like_serial("PF2ABCDE")
    assert ocr.looks_like_serial("tag/88b2")
    # a bare digit run is far more likely to be a phone/ID than a serial
    assert not ocr.looks_like_serial("9876543210")
    assert not ocr.looks_like_serial("bag")
    assert not ocr.looks_like_serial("ab")


def test_visible_tokens_are_deduplicated_uppercase_and_redacted():
    text = "SN-4471\nsn-4471\nPF2ABCDE\nRavi Kumar\n+91 98765 43210\n"
    assert ocr.visible_tokens(text) == ["SN-4471", "PF2ABCDE"]


def test_photo_reading_returns_nothing_without_an_engine(monkeypatch):
    monkeypatch.setattr(ocr, "resolve_engine", lambda: None)
    assert ocr.extract_visible_text("reports/LR-0001.jpg") == []
    assert ocr.read_photo(b"\xff\xd8\xff") == ""


def test_engine_failure_never_breaks_ingest():
    class Broken:
        name = "broken"

        def read(self, data: bytes) -> str:
            raise RuntimeError("tesseract crashed")

    assert ocr.read_photo(b"\xff\xd8\xff", engine=Broken()) == ""


def test_enrichment_merges_printed_serial_with_what_was_typed(monkeypatch):
    monkeypatch.setattr(pipeline, "dominant_colour", lambda path: None)
    monkeypatch.setattr(ocr, "extract_visible_text", lambda path, engine=None: ["SN-4471", "PF2ABCDE"])
    attrs = pipeline.enrich_report(_report())
    assert attrs.visible_text == ["SN-4471", "PF2ABCDE"]
    assert attrs.confidence["visible_text_source"] == 1.0


def test_enrichment_does_not_duplicate_a_serial_the_reporter_typed(monkeypatch):
    monkeypatch.setattr(pipeline, "dominant_colour", lambda path: None)
    monkeypatch.setattr(ocr, "extract_visible_text", lambda path, engine=None: ["sn-4471", "PF2ABCDE"])
    report = _report()
    report.description = "black laptop bag, serial SN-4471"
    attrs = pipeline.enrich_report(report)
    assert [value.upper() for value in attrs.visible_text] == ["SN-4471", "PF2ABCDE"]
