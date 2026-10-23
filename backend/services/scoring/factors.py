"""Factor curves and hour scores. Pure functions."""

from backend.services.display import celsius, chance, kmh, whole
from backend.services.scoring.config import FactorSpec, LowerLimit, TwoSided, UpperLimit
from backend.services.scoring.models import Verdict

SUITABLE = 70  # score >= this: suitable
MARGINAL = 40  # score >= this: marginal; below: not suitable
CAP_MARGIN = 20  # an hour scores at most its worst factor + this
IDEAL, EDGE = 100.0, 50.0  # score inside the ideal range / at the acceptable edge
REASON_BELOW = 95  # a factor is listed as a reason only if it lost at least 5 points


def verdict(score: float) -> Verdict:
    if score >= SUITABLE:
        return "suitable"
    return "marginal" if score >= MARGINAL else "not_suitable"


def _decline(value: float, ideal: float, acceptable: float, hard: float) -> float:
    """100 up to `ideal`, linear to 50 at `acceptable`, linear to 0 at `hard`, for rising values."""
    if value <= ideal:
        return IDEAL
    if value <= acceptable:
        return IDEAL - (IDEAL - EDGE) * (value - ideal) / (acceptable - ideal)
    if value < hard:
        return EDGE - EDGE * (value - acceptable) / (hard - acceptable)
    return 0.0


def factor_score(spec: FactorSpec, value: float) -> float:
    """0-100 for one factor value."""
    if isinstance(spec, TwoSided):
        if value > spec.ideal[1]:
            return _decline(value, spec.ideal[1], spec.acceptable[1], spec.hard[1])
        return _decline(-value, -spec.ideal[0], -spec.acceptable[0], -spec.hard[0])
    if isinstance(spec, UpperLimit):
        return _decline(value, spec.ideal_max, spec.acceptable_max, spec.hard_max)
    assert isinstance(spec, LowerLimit)
    return _decline(-value, -spec.ideal_min, -spec.acceptable_min, -spec.hard_min)


def combine(scores: dict[str, float], weights: dict[str, float]) -> float:
    """Weighted mean of the available factor scores (weights renormalised over them), capped by
    the worst factor: never above worst + CAP_MARGIN, and 0 if any factor is at 0."""
    worst = min(scores.values())
    if worst == 0:
        return 0.0
    mean = sum(s * weights[name] for name, s in scores.items()) / sum(weights[name] for name in scores)
    return min(mean, worst + CAP_MARGIN)


def reason(name: str, value: float, spec: FactorSpec, label: str | None = None) -> str:
    """Why a factor lost points, with its value. `label` overrides the default wording:
    the temperature wording ("feels like" / "temperature"), or the whole air quality text."""
    if name == "feels_like_c":
        assert isinstance(spec, TwoSided)
        # Judged on the shown (rounded) value, so "33°C" at an acceptable limit of 33 says "warm".
        shown = whole(value)
        hot = value > spec.ideal[1]
        beyond = shown > spec.acceptable[1] if hot else shown < spec.acceptable[0]
        side = ("too hot" if beyond else "warm") if hot else ("too cold" if beyond else "cool")
        return f"{label or 'feels like'} {celsius(value)}, {side}"
    if name == "naqi":
        return label or f"NAQI {whole(value)}"
    if name == "rain_prob":
        return f"rain chance {chance(value)}"
    if name == "rain_mm":
        return f"rain {value:.1f} mm/h"
    if name == "wind_kmh":
        return f"wind {kmh(value)}"
    if name == "wind_gust_kmh":
        return f"gusts {kmh(value)}"
    if name == "uvi":
        return f"UV index {whole(value)}"
    if name == "visibility_m":
        return f"visibility {value / 1000:.1f} km" if value >= 1000 else f"visibility {whole(value)} m"
    raise ValueError(f"unknown factor {name!r}")
