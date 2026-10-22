"""Data shapes for the agent's understanding layer."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from backend.services.date_resolver import DATE_EXPRESSIONS
from backend.services.geocoding import timezone_at
from backend.services.models import Location, TimeWindow

Intent = Literal[
    "weather",
    "activity_recommendation",
    "travel_recommendation",
    "clothing_recommendation",
    "comparison",
    "out_of_scope",
    "unclear",
]
RecommendationType = Literal["suitability", "best_time", "avoid", "packing", "travel_timing"]
# Built from the resolver's own list so the LLM can only produce dates it can resolve.
DateExpr = Literal[tuple(DATE_EXPRESSIONS)]  # type: ignore[valid-type]

DEFAULT_TIMEZONE = "Asia/Kolkata"  # users are mainly in India


class QueryUnderstanding(BaseModel):
    """What one user message asks for, filled by the LLM via structured output."""

    intent: Intent = Field(description="What the user wants.")
    activity: str | None = Field(
        default=None, description="Activity asked about, e.g. running, cycling, picnic; null if none."
    )
    locations: list[str] = Field(
        default_factory=list,
        description="Place names exactly as the user wrote them. Empty means the user's current location.",
    )
    date_expr: DateExpr | None = Field(default=None, description="Supported date value, or null if not mentioned.")
    time_expr: str | None = Field(
        default=None,
        description="Supported time of day (morning, afternoon, evening, night, full_day) or an explicit "
        "range like '7-9am' or '18:00-20:00'; null if not mentioned.",
    )
    recommendation_type: RecommendationType | None = Field(
        default=None, description="Kind of recommendation wanted, if any."
    )
    is_follow_up: bool = Field(
        default=False,
        description="True if the message only changes or answers something about the previous query.",
    )
    needs_clarification: bool = Field(
        default=False, description="True if a detail can't be mapped to supported values, e.g. 'next month'."
    )
    clarification_question: str | None = Field(
        default=None, description="Short question to ask the user when needs_clarification is true."
    )


class UserContext(BaseModel):
    """Who is asking and from where. `now` is injectable for tests."""

    lat: float | None = Field(default=None, ge=-90, le=90)
    lon: float | None = Field(default=None, ge=-180, le=180)
    timezone: str = ""  # derived from lat/lon when empty
    preferred_country: str = "in"
    now: datetime | None = None

    @model_validator(mode="after")
    def _fill_timezone(self) -> "UserContext":
        if (self.lat is None) != (self.lon is None):
            raise ValueError("lat and lon must be given together")
        if not self.timezone:
            self.timezone = timezone_at(self.lat, self.lon) if self.has_location else DEFAULT_TIMEZONE
        return self

    @property
    def has_location(self) -> bool:
        return self.lat is not None and self.lon is not None


ClarificationReason = Literal[
    "no_location",
    "ambiguous_location",
    "location_not_found",
    "unsupported_date",
    "unsupported_time",
    "too_many_locations",
    "unclear",
    "llm_clarification",
]


class Clarification(BaseModel):
    """A question to put to the user instead of guessing."""

    question: str
    reason: ClarificationReason
    location_query: str | None = None  # the place name being clarified
    options: list[Location] = Field(default_factory=list)  # choices for an ambiguous place


class Target(BaseModel):
    """One resolved place and the time windows to look at there."""

    query: str  # as the user wrote it; "" for the current location
    location: Location
    windows: list[TimeWindow]


class ResolvedQuery(BaseModel):
    """Understanding plus concrete places and windows, or a clarification."""

    status: Literal["ready", "clarify", "out_of_scope"]
    understanding: QueryUnderstanding
    date_expr: str | None = None  # after defaults (today)
    time_expr: str | None = None  # after defaults (full_day)
    targets: list[Target] = Field(default_factory=list)
    clarification: Clarification | None = None
