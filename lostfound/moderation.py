"""Low-cost spam signals for public report submissions."""

from __future__ import annotations

import re
import time

LINKS_ONLY = re.compile(r"(?:https?://\S+\s*)+", re.IGNORECASE)
MINIMUM_FORM_SECONDS = 2.0


def needs_review(
    description: str,
    *,
    website: str = "",
    form_started_at: float | None = None,
    now: float | None = None,
) -> bool:
    if website.strip():
        return True
    if (
        form_started_at is not None
        and (now if now is not None else time.time()) - form_started_at < MINIMUM_FORM_SECONDS
    ):
        return True
    return bool(LINKS_ONLY.fullmatch(description.strip()))
