"""Display rounding shared by everything that shows numbers to a person or a model: scoring
reasons, agent observations, the template fallback and the answer evals' grounding check.

Whole °C, km/h, UV and µg/m³ (rounded half up), rain chance to the nearest 5%.
"""

import math


def whole(value: float) -> int:
    """Round half up: 24.5 -> 25 (Python's round() gives 24)."""
    return math.floor(value + 0.5)


def percent5(fraction: float) -> int:
    """A 0-1 fraction as a percentage rounded to the nearest 5: 0.26 -> 25, 0.04 -> 5."""
    return 5 * whole(fraction * 20)


def celsius(value: float) -> str:
    return f"{whole(value)}°C"


def chance(fraction: float) -> str:
    return f"{percent5(fraction)}%"


def kmh(value: float) -> str:
    return f"{whole(value)} km/h"
