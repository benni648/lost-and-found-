"""The recovery loop: claim a found item, prove it, collect it.

Identification is not recovery. A ranked match says "these two reports are
probably the same item"; this module is what turns that into a handover:

1. the owner asks for the challenge questions on a plausible pair
   (:func:`challenge_for`) -- drawn from their **own** lost report, so a stranger
   reading the public feed learns nothing they did not already know;
2. the answers are graded here and then discarded: only the questions asked and
   the attempt count are stored (:mod:`lostfound.store` never sees the answers);
3. a verified claim receives a one-time handover code, of which only a SHA-256
   hash is persisted;
4. staff close the counter with that code, which marks both reports recovered
   and writes an audit entry.

Answers are graded on normalised text (case, spaces and punctuation ignored), so
"SN-4471" and "sn 4471" are the same serial without making the question easy to
brute-force -- and attempts are capped for the same reason.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets

from .schema import Attributes
from .settings import settings
from .taxonomy import COLOR_ALIASES

MIN_QUESTIONS = 2
MAX_ATTEMPTS = 5


def max_attempts() -> int:
    """Wrong handover codes allowed before a claim is closed (configurable)."""
    return settings.claim_max_attempts
HANDOVER_DIGITS = 6
_WORD = re.compile(r"[a-z0-9]+")


def normalise(value: str) -> str:
    """Case-, space- and punctuation-insensitive form used for grading."""
    return "".join(_WORD.findall((value or "").lower()))


def challenge_for(report) -> list[dict]:
    """Questions the owner of ``report`` should be able to answer.

    Ordered strongest first: a printed serial and a described scratch are much
    harder for a stranger to guess than a colour that is visible in the photo.
    """
    attrs = Attributes.from_dict(report.attrs or {})
    questions: list[dict] = []
    if attrs.visible_text:
        questions.append(
            {
                "key": "visible_text",
                "prompt": "Type the serial, tag or printed number that appears on the item.",
            }
        )
    if attrs.marks or attrs.damages:
        questions.append(
            {
                "key": "details",
                "prompt": "Name one distinguishing mark, sticker or damage you described.",
            }
        )
    if attrs.brand or attrs.model:
        # The prompt must not name the brand: the question is a challenge, and a
        # prompt that contains its own answer is not one.
        questions.append({"key": "brand_or_model", "prompt": "Which brand or model is written on it?"})
    if attrs.primary_color:
        questions.append({"key": "primary_color", "prompt": "What is the item's main colour?"})
    return questions


def _colour_matches(answer: str, expected: str) -> bool:
    given = normalise(answer)
    wanted = normalise(expected)
    if not given or not wanted:
        return False
    if given == wanted:
        return True
    alias = COLOR_ALIASES.get((answer or "").strip().lower())
    return bool(alias and normalise(alias) == wanted)


def _details_match(answer: str, values: list[str]) -> bool:
    given = normalise(answer)
    if not given:
        return False
    return any(normalise(value) and (normalise(value) in given or given in normalise(value)) for value in values)


def grade(report, answers: dict[str, str]) -> tuple[bool, float]:
    """``(verified, share_correct)`` for one attempt. Answers are not retained."""
    attrs = Attributes.from_dict(report.attrs or {})
    questions = challenge_for(report)
    if not questions:
        return False, 0.0
    correct = 0
    for question in questions:
        answer = (answers or {}).get(question["key"], "")
        key = question["key"]
        if key == "visible_text":
            ok = any(normalise(answer) and normalise(answer) == normalise(token) for token in attrs.visible_text)
        elif key == "details":
            ok = _details_match(answer, list(attrs.marks) + list(attrs.damages))
        elif key == "brand_or_model":
            ok = any(
                normalise(answer) and normalise(answer) == normalise(value)
                for value in (attrs.brand, attrs.model)
                if value
            )
        elif key == "primary_color":
            ok = _colour_matches(answer, attrs.primary_color or "")
        else:  # a question the grader does not know can never be answered
            ok = False
        correct += int(ok)
    verified = correct == len(questions) and len(questions) >= MIN_QUESTIONS
    return verified, round(correct / len(questions), 3)


def new_claim_token() -> str:
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def token_matches(stored_hash: str | None, token: str) -> bool:
    if not stored_hash:
        return False
    return hmac.compare_digest(stored_hash, hash_token(token))


def new_handover_code() -> str:
    """Six digits, zero-padded: short enough to read out at a desk."""
    return f"{secrets.randbelow(10**HANDOVER_DIGITS):0{HANDOVER_DIGITS}d}"


def code_matches(stored_hash: str | None, code: str) -> bool:
    if not stored_hash:
        return False
    return hmac.compare_digest(stored_hash, hash_token((code or "").strip()))
