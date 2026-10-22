"""Rule-based practical hints (clothing, packing). Pure functions."""

from backend.services.scoring.config import HintRule
from backend.services.scoring.models import HourAssessment

MAX_HINTS = 4


def hints(rules: list[HintRule], slots: list[HourAssessment]) -> list[str]:
    """Hints whose rule matches the slots, in rule order, deduplicated, at most MAX_HINTS.

    at_least checks the highest value of the factor across the slots, at_most the lowest.
    """
    out: list[str] = []
    for rule in rules:
        values = [f.value for s in slots for f in s.factors if f.name == rule.factor]
        if not values:
            continue
        hit = (rule.at_least is not None and max(values) >= rule.at_least) or (
            rule.at_most is not None and min(values) <= rule.at_most
        )
        if hit and rule.text not in out:
            out.append(rule.text)
            if len(out) == MAX_HINTS:
                break
    return out
