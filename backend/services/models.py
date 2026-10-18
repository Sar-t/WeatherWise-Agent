"""Shared Pydantic models used across services."""

from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import AwareDatetime, BaseModel, Field, field_validator, model_validator


class Location(BaseModel):
    """A resolved place, e.g. from the Geoapify geocoding API."""

    name: str  # the place itself: "Jaipur", or an area like "Bandra West"
    city: str | None = None  # parent city, e.g. "Mumbai" for Bandra West
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    country: str | None = None  # e.g. "India"; None for places outside any country
    country_code: str | None = None  # lowercase ISO 3166-1 alpha-2, e.g. "in"
    state: str | None = None
    formatted: str  # display address, e.g. "Jaipur, RJ, India"
    result_type: str = "unknown"  # Geoapify type: city, suburb, district, county, ...
    confidence: float | None = Field(default=None, ge=0, le=1)
    importance: float | None = Field(default=None, ge=0)  # Geoapify rank.importance
    timezone: str  # IANA name, e.g. "Asia/Kolkata"

    @field_validator("timezone")
    @classmethod
    def _valid_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"unknown IANA timezone: {value!r}") from exc
        return value


class TimeWindow(BaseModel):
    """A half-open interval [start, end) of timezone-aware datetimes."""

    start: AwareDatetime
    end: AwareDatetime

    @model_validator(mode="after")
    def _end_after_start(self) -> "TimeWindow":
        if self.end <= self.start:
            raise ValueError(f"end ({self.end}) must be after start ({self.start})")
        return self


class WeatherPoint(BaseModel):
    """Weather at a single point in time, in metric units."""

    time: AwareDatetime
    temp_c: float
    feels_like_c: float
    humidity: float = Field(ge=0, le=100)  # percent
    rain_prob: float = Field(ge=0, le=1)
    wind_kmh: float = Field(ge=0)
    uvi: float | None = Field(default=None, ge=0)
    description: str
