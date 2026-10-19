"""Shared card-count and slot planning defaults for AI Espresso."""

from __future__ import annotations

from typing import Any
import re


STORY_CARD_COUNT = 4
MAX_STORY_COUNT = 6

# The separate prompt tile never counts as a news story.
ALLOWED_STORY_COUNTS = (4, 5, 6)
STORY_COUNT_POLICY = "4-5-exceptional-6"


def largest_allowed_count(n: int, *, exceptional_six: bool = False) -> int:
    """Keep normal slates up to five; six needs an explicit valid exception."""
    return min(n, 6 if exceptional_six else 5)


def six_story_exception_error(exception: Any, original_headlines: list[str]) -> str | None:
    """Check a recorded exception's substance and connection to this slate.

    This is a mechanical backstop, not an automated judgment of news importance.
    The editor must explain why this specific story cannot be omitted or delayed.
    """
    if not isinstance(exception, dict) or type(exception.get("candidate_id")) is not int:
        return "six-story exception must identify a selected story"
    headline = exception.get("original_headline")
    if not isinstance(headline, str) or not headline.strip() or headline not in original_headlines:
        return "six-story exception must refer to a story in this edition"
    reason = exception.get("reason")
    if (not isinstance(reason, str) or not 60 <= len(reason.strip()) <= 1200
            or len(re.findall(r"\b\w+\b", reason)) < 10):
        return "six-story exception needs a substantive reason (10+ words, 60-1200 characters)"
    return None


def validate_story_count(data: dict) -> int:
    """Validate a slate before rendering or generating any illustrations."""
    stories = data.get("stories", [])
    count = len(stories)
    if count not in ALLOWED_STORY_COUNTS:
        raise ValueError(f"Edition has {count} stories; need 4 or 5 (six only by exception).")
    # Historical editions have no policy marker. All newly written editions do.
    if "story_count_policy" in data:
        if data["story_count_policy"] != STORY_COUNT_POLICY:
            raise ValueError("Unsupported story-count policy")
        if count == 6:
            error = six_story_exception_error(data.get("six_story_exception"),
                [s.get("original_headline") or s.get("headline", "") for s in stories])
            if error:
                raise ValueError(error)
    return count


def needed_slots_for_rules(today: Any, rules: dict) -> list[str]:
    """Return required slot names for the edition date."""
    is_rotation = today.weekday() in rules.get("tier4_rotation_days", [1, 4])
    if is_rotation:
        return ["business", "beginner", "cross", "engineer"]
    return ["business", "beginner", "engineer", "cross"]
