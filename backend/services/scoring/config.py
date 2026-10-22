"""Load and validate the activity profiles in activities.yaml. A malformed file fails loudly."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

CONFIG_PATH = Path(__file__).parent / "activities.yaml"
FALLBACK_PROFILE = "general_outdoor"

# Every factor a profile scores, in display order.
FACTORS = ("feels_like_c", "rain_prob", "rain_mm", "wind_kmh", "wind_gust_kmh", "uvi", "visibility_m", "naqi")
HardStop = Literal["thunderstorm", "heavy_rain", "severe_alert", "severe_naqi"]


class ConfigError(ValueError):
    """activities.yaml is missing, unreadable or invalid."""


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class TwoSided(_Strict):
    """Good in the middle, bad on both sides (feels-like temperature)."""

    ideal: tuple[float, float]
    acceptable: tuple[float, float]
    hard: tuple[float, float]

    @model_validator(mode="after")
    def _ordered(self) -> "TwoSided":
        lo = (self.hard[0], self.acceptable[0], self.ideal[0])
        hi = (self.ideal[1], self.acceptable[1], self.hard[1])
        if not (lo[0] < lo[1] < lo[2] <= hi[0] < hi[1] < hi[2]):
            raise ValueError("needs hard[0] < acceptable[0] < ideal[0] <= ideal[1] < acceptable[1] < hard[1]")
        return self


class UpperLimit(_Strict):
    """Higher is worse (rain, wind, UV, NAQI)."""

    ideal_max: float
    acceptable_max: float
    hard_max: float

    @model_validator(mode="after")
    def _ordered(self) -> "UpperLimit":
        if not self.ideal_max < self.acceptable_max < self.hard_max:
            raise ValueError("needs ideal_max < acceptable_max < hard_max")
        return self


class LowerLimit(_Strict):
    """Lower is worse (visibility)."""

    ideal_min: float
    acceptable_min: float
    hard_min: float

    @model_validator(mode="after")
    def _ordered(self) -> "LowerLimit":
        if not self.ideal_min > self.acceptable_min > self.hard_min:
            raise ValueError("needs ideal_min > acceptable_min > hard_min")
        return self


FactorSpec = TwoSided | UpperLimit | LowerLimit
_SPEC_TYPES: dict[str, type[FactorSpec]] = {
    name: TwoSided if name == "feels_like_c" else LowerLimit if name == "visibility_m" else UpperLimit
    for name in FACTORS
}


class Profile(_Strict):
    duration_hours: int = Field(ge=1)
    needs_daylight: bool
    factors: dict[str, FactorSpec]
    weights: dict[str, float]
    hard_stops: list[HardStop]

    @field_validator("factors", mode="before")
    @classmethod
    def _typed_factors(cls, raw: object) -> object:
        """Parse each factor with the curve its name implies, so a wrong shape names the factor."""
        if not isinstance(raw, dict):
            return raw
        if missing := [f for f in FACTORS if f not in raw]:
            raise ValueError(f"missing factor(s): {', '.join(missing)}")
        if unknown := [f for f in raw if f not in FACTORS]:
            raise ValueError(f"unknown factor(s): {', '.join(unknown)}")
        parsed = {}
        for name, spec in raw.items():
            try:
                parsed[name] = _SPEC_TYPES[name].model_validate(spec)
            except ValidationError as exc:
                raise ValueError(f"{name}: {exc}") from None
        return parsed

    @model_validator(mode="after")
    def _weights_match(self) -> "Profile":
        if set(self.weights) != set(self.factors):
            raise ValueError(f"weights must cover exactly the factors {list(FACTORS)}")
        if bad := [name for name, w in self.weights.items() if w <= 0]:
            raise ValueError(f"weights must be positive: {', '.join(bad)}")
        return self


class HintRule(_Strict):
    factor: str
    at_least: float | None = None
    at_most: float | None = None
    text: str

    @model_validator(mode="after")
    def _one_condition(self) -> "HintRule":
        if self.factor not in FACTORS:
            raise ValueError(f"unknown factor {self.factor!r}")
        if (self.at_least is None) == (self.at_most is None):
            raise ValueError("needs exactly one of at_least / at_most")
        return self


class ScoringConfig(_Strict):
    profiles: dict[str, Profile]
    hints: list[HintRule]
    severe_alert_keywords: list[str]

    @model_validator(mode="after")
    def _has_fallback(self) -> "ScoringConfig":
        if FALLBACK_PROFILE not in self.profiles:
            raise ValueError(f"a {FALLBACK_PROFILE!r} profile is required as the fallback")
        return self


def parse_config(text: str, source: str = "activities.yaml") -> ScoringConfig:
    try:
        return ScoringConfig.model_validate(yaml.safe_load(text))
    except (yaml.YAMLError, ValidationError) as exc:
        raise ConfigError(f"{source} is invalid:\n{exc}") from exc


@lru_cache(maxsize=1)
def load_config() -> ScoringConfig:
    return parse_config(CONFIG_PATH.read_text(encoding="utf-8"), str(CONFIG_PATH))
