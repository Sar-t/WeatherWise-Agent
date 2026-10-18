"""Shared Pydantic models used across services."""

from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import AwareDatetime, BaseModel, Field, field_validator, model_validator


class Location(BaseModel):
    """A resolved place, e.g. from the OpenWeather Geocoding API."""

    name: str
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    country: str  # ISO 3166 country code, e.g. "IN"
    state: str | None = None
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
