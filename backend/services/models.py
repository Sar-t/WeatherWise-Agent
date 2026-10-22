"""Shared Pydantic models used across services."""

import datetime as dt
from typing import Literal
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
    """Weather at one hour (or the current moment), in metric units."""

    time: AwareDatetime
    temp_c: float
    feels_like_c: float
    humidity: float = Field(ge=0, le=100)  # percent
    rain_prob: float | None = Field(default=None, ge=0, le=1)  # None for observations
    wind_kmh: float = Field(ge=0)
    wind_gust_kmh: float | None = Field(default=None, ge=0)
    uvi: float | None = Field(default=None, ge=0)
    clouds: float | None = Field(default=None, ge=0, le=100)  # percent
    visibility_m: float | None = Field(default=None, ge=0)
    rain_mm: float | None = Field(default=None, ge=0)  # last hour; 0.0 when none fell
    snow_mm: float | None = Field(default=None, ge=0)  # last hour; 0.0 when none fell
    condition: str | None = None  # OpenWeather "main", e.g. "Rain"
    description: str | None = None  # e.g. "light rain"
    icon: str | None = None  # OpenWeather icon code, e.g. "10d"


class DailyWeather(BaseModel):
    """Weather for one local calendar day, forecast or historical."""

    date: dt.date
    temp_min_c: float
    temp_max_c: float
    temp_morning_c: float | None = None
    temp_day_c: float | None = None
    temp_evening_c: float | None = None
    temp_night_c: float | None = None
    feels_like_day_c: float | None = None
    humidity: float | None = Field(default=None, ge=0, le=100)
    rain_prob: float | None = Field(default=None, ge=0, le=1)
    rain_mm: float | None = Field(default=None, ge=0)  # day total; 0.0 when none fell
    snow_mm: float | None = Field(default=None, ge=0)  # day total; 0.0 when none fell
    wind_kmh: float | None = Field(default=None, ge=0)  # typical wind (forecasts only)
    wind_max_kmh: float | None = Field(default=None, ge=0)  # forecast gusts / historical peak
    uvi: float | None = Field(default=None, ge=0)
    condition: str | None = None
    description: str | None = None
    summary: str | None = None  # e.g. "Expect a day of partly cloudy with rain"


class WeatherAlert(BaseModel):
    """A national weather warning."""

    sender: str
    event: str
    start: AwareDatetime
    end: AwareDatetime
    description: str


class SunTimes(BaseModel):
    """Sunrise and sunset for one local date; None where the provider gives none (polar day/night)."""

    date: dt.date
    sunrise: AwareDatetime | None = None
    sunset: AwareDatetime | None = None


class WeatherResult(BaseModel):
    """Weather for one location and time window, at one granularity."""

    location: Location
    window: TimeWindow
    granularity: Literal["current", "hourly", "daily", "historical"]
    hourly: list[WeatherPoint] = Field(default_factory=list)
    daily: list[DailyWeather] = Field(default_factory=list)
    alerts: list[WeatherAlert] = Field(default_factory=list)
    sun: list[SunTimes] = Field(default_factory=list)  # for the local dates the window touches, when known
    fetched_at: AwareDatetime  # when the data came from the provider (not from cache)


class AirComponents(BaseModel):
    """Pollutant concentrations in ug/m3 (as OpenWeather reports them); None if missing."""

    co: float | None = None
    no: float | None = None
    no2: float | None = None
    o3: float | None = None
    so2: float | None = None
    pm2_5: float | None = None
    pm10: float | None = None
    nh3: float | None = None


class AirQualityPoint(BaseModel):
    """Air quality for one hour (or the current moment)."""

    time: AwareDatetime
    components: AirComponents  # this hour's readings
    owm_aqi: int | None = Field(default=None, ge=1, le=5)  # OpenWeather's own 1-5 scale, not NAQI
    naqi: int | None = Field(default=None, ge=0, le=500)  # India NAQI from trailing averages
    naqi_category: str | None = None
    dominant_pollutant: str | None = None  # e.g. "PM2.5"
    sub_indices: dict[str, int] = Field(default_factory=dict)  # per pollutant, e.g. {"PM2.5": 182}
    indicative: bool = False  # averages had fewer hours than CPCB asks for
    naqi_reason: str | None = None  # why naqi is None


class AirQualitySummary(BaseModel):
    """Headline numbers over a window."""

    naqi_max: int | None = None
    naqi_max_at: AwareDatetime | None = None
    naqi_category: str | None = None  # category of naqi_max
    dominant_pollutant: str | None = None  # at naqi_max
    owm_aqi_max: int | None = None
    pm2_5_avg: float | None = None  # mean of the hourly readings in the window
    pm10_avg: float | None = None
    indicative: bool = False  # any point in the window was indicative


class AirQualityResult(BaseModel):
    """Air quality for one location and time window."""

    location: Location
    window: TimeWindow
    granularity: Literal["current", "hourly", "historical"]
    points: list[AirQualityPoint] = Field(default_factory=list)
    summary: AirQualitySummary = Field(default_factory=AirQualitySummary)
    fetched_at: AwareDatetime  # when the data came from the provider (not from cache)
