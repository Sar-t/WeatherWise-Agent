"""Results of the activity scoring engine."""

from typing import Literal

from pydantic import AwareDatetime, BaseModel, Field

from backend.services.models import Location, TimeWindow

Verdict = Literal["suitable", "marginal", "not_suitable"]


class FactorScore(BaseModel):
    name: str  # e.g. "feels_like_c"
    value: float
    score: float = Field(ge=0, le=100)
    reason: str | None = None  # set when the factor lost >= 5 points, e.g. "feels like 38°C, too hot"


class HourAssessment(BaseModel):
    """One scored slot: an hour, or a part of the day for daily estimates."""

    time: AwareDatetime  # start
    end: AwareDatetime
    part_of_day: str | None = None  # "morning", ... for daily estimates
    score: int = Field(ge=0, le=100)
    verdict: Verdict
    factors: list[FactorScore] = Field(default_factory=list)
    hard_stops: list[str] = Field(default_factory=list)  # e.g. "thunderstorm"; any one zeroes the score
    capped_by: str | None = None  # why the score is capped, e.g. a day-level thunderstorm
    reasons: list[str] = Field(default_factory=list)  # hard stops, then factors that lost points (worst first)


class TimeBlock(BaseModel):
    start: AwareDatetime
    end: AwareDatetime
    average_score: int = Field(ge=0, le=100)
    verdict: Verdict
    reasons: list[str] = Field(default_factory=list)


class ActivityAssessment(BaseModel):
    activity: str  # as asked
    profile: str  # profile used, e.g. "general_outdoor" for an unknown activity
    location: Location
    windows: list[TimeWindow]
    verdict: Verdict
    score: int = Field(ge=0, le=100)
    best_blocks: list[TimeBlock] = Field(default_factory=list)  # up to 2, best first, non-overlapping
    avoid_blocks: list[TimeBlock] = Field(default_factory=list)  # runs scoring below the marginal threshold
    hourly: list[HourAssessment] = Field(default_factory=list)
    hints: list[str] = Field(default_factory=list)
    data_quality: list[str] = Field(default_factory=list)
    is_daily_estimate: bool = False
    confidence: Literal["normal", "low"] = "normal"
