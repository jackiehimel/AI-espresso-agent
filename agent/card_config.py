"""Shared card-count and slot planning defaults for AI Espresso."""

from __future__ import annotations

from typing import Any


STORY_CARD_COUNT = 4
MAX_STORY_COUNT = 6

# Editions ship an even number of story cards so the grid always fills cleanly
# (no dangling card). The "Try this prompt" tile is a separate accent and does
# not count toward this.
ALLOWED_STORY_COUNTS = (4, 6)


def largest_allowed_even_count(n: int) -> int:
    """Largest supported even story count (4 or 6) achievable from ``n`` picks.

    Used as the deterministic ship backstop: an odd pick set is trimmed down to
    the next even count (5 -> 4, 6 -> 6). Below 4 we return ``n`` unchanged so
    validation can reject the unsupported count without mutating the picks.
    """
    if n >= 6:
        return 6
    if n >= 4:
        return 4
    return n


def needed_slots_for_rules(today: Any, rules: dict) -> list[str]:
    """Return required slot names for the edition date."""
    is_rotation = today.weekday() in rules.get("tier4_rotation_days", [1, 4])
    if is_rotation:
        return ["business", "beginner", "cross", "engineer"]
    return ["business", "beginner", "engineer", "cross"]
