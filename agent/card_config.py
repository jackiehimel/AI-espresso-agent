"""Shared card-count and slot planning defaults for AI Espresso."""

from __future__ import annotations

from typing import Any


STORY_CARD_COUNT = 4


def needed_slots_for_rules(today: Any, rules: dict) -> list[str]:
    """Return required slot names for the edition date."""
    is_rotation = today.weekday() in rules.get("tier4_rotation_days", [1, 4])
    if is_rotation:
        return ["business", "beginner", "cross", "engineer"]
    return ["business", "beginner", "engineer", "cross"]
