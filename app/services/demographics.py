"""Contributor demographics: gender + age range → standard age category.

Demographics are ADMIN-SET (contributors are created/edited by admins). These
fields are carried into the HuggingFace dataset metadata so the corpus is
useful for TTS/TTST voice mapping and balance reporting.
"""
from __future__ import annotations

from typing import Literal

Gender = Literal["male", "female", "other", "prefer_not_to_say"]
GENDERS: tuple[str, ...] = ("male", "female", "other", "prefer_not_to_say")

# Selected age ranges (minors 13-17 included per project decision).
AgeRange = Literal[
    "13-17",
    "18-24",
    "25-34",
    "35-44",
    "45-54",
    "55-64",
    "65+",
    "prefer_not_to_say",
]
AGE_RANGES: tuple[str, ...] = (
    "13-17",
    "18-24",
    "25-34",
    "35-44",
    "45-54",
    "55-64",
    "65+",
    "prefer_not_to_say",
)

# Standard life-stage categories derived from the selected range.
_RANGE_TO_CATEGORY: dict[str, str] = {
    "13-17": "adolescent",
    "18-24": "youth",
    "25-34": "adult_early",
    "35-44": "adult",
    "45-54": "middle_aged_adult",
    "55-64": "older_adult",
    "65+": "senior",
    "prefer_not_to_say": "unset",
}

# Range considered a minor (relevant for guardian_consent).
MINOR_RANGES: frozenset[str] = frozenset({"13-17"})


def normalize_gender(raw: object) -> str | None:
    if raw is None:
        return None
    s = str(raw).strip().lower()
    return s if s in GENDERS else None


def normalize_age_range(raw: object) -> str | None:
    if raw is None:
        return None
    s = str(raw).strip()
    return s if s in AGE_RANGES else None


def age_category_for_range(age_range: str | None) -> str | None:
    """Derive the standard age category from a selected range (None if unknown)."""
    if age_range is None:
        return None
    return _RANGE_TO_CATEGORY.get(age_range)


def is_minor_range(age_range: str | None) -> bool:
    return age_range in MINOR_RANGES
