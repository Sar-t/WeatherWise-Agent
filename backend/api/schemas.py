"""Stable API models for the frontend, separate from the internal service/agent models.

Values are display-ready (services/display.py rounding); times are local ISO 8601 strings with
offsets; coordinates are rounded to 2 decimals (no precise positions ever leave the server).
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from backend.agent.schemas import AgentResponse, GuidelineSource
from backend.services.display import percent5, whole
from backend.services.icons import compass, icon_key
from backend.services.models import (
    AirQualityPoint,
    AirQualityResult,
    DailyWeather,
    Location,
    WeatherAlert,
    WeatherPoint,
    WeatherResult,
)
from backend.services.scoring.models import ActivityAssessment, TimeBlock

OWM_NAMES = {1: "Good", 2: "Fair", 3: "Moderate", 4: "Poor", 5: "Very Poor"}


# --- errors ------------------------------------------------------------------------------------


class ErrorBody(BaseModel):
    code: str  # e.g. "bad_request", "rate_limited", "not_found", "ambiguous_place", "internal_error"
    message: str  # safe to show to a user
    request_id: str | None = None
    details: dict | None = None  # e.g. {"candidates": [...]} for an ambiguous place


class ErrorResponse(BaseModel):
    error: ErrorBody


# --- shared pieces -------------------------------------------------------------------------------


class LocationOut(BaseModel):
    name: str  # display name, e.g. "Bandra West, Mumbai"
    formatted: str
    country_code: str | None = None
    timezone: str
    lat: float  # 2 decimals
    lon: float


class HourOut(BaseModel):
    time: datetime
    temp_c: int
    feels_like_c: int
    rain_prob_pct: int | None = None  # nearest 5%
    wind_kmh: int
    condition: str | None = None
    icon: str


class DayOut(BaseModel):
    date: str  # YYYY-MM-DD
    min_c: int
    max_c: int
    condition: str | None = None
    icon: str
    rain_prob_pct: int | None = None
    rain_mm: float | None = None  # 1 decimal
    uv_index: int | None = None


class AlertOut(BaseModel):
    event: str
    sender: str
    start: datetime
    end: datetime


class WeatherCard(BaseModel):
    location: LocationOut
    kind: Literal["current", "hourly", "daily", "historical"]
    window_start: datetime
    window_end: datetime
    hourly: list[HourOut] = Field(default_factory=list)
    daily: list[DayOut] = Field(default_factory=list)
    alerts: list[AlertOut] = Field(default_factory=list)


class AirHourOut(BaseModel):
    time: datetime
    value: int | None = None  # NAQI (India) or the 1-5 level elsewhere
    category: str | None = None


class AirQualityCard(BaseModel):
    location: LocationOut
    scale: Literal["naqi", "owm"]  # India's NAQI (CPCB), or OpenWeather's 1-5 scale elsewhere
    value: int | None = None  # worst in the window
    category: str | None = None
    dominant_pollutant: str | None = None
    worst_at: datetime | None = None
    pm2_5: int | None = None  # µg/m³, mean of the hours
    pm10: int | None = None
    window_start: datetime
    window_end: datetime
    hourly: list[AirHourOut] = Field(default_factory=list)


class BlockOut(BaseModel):
    start: datetime
    end: datetime
    score: int
    verdict: str
    reasons: list[str] = Field(default_factory=list)


class AssessmentOut(BaseModel):
    activity: str
    profile: str
    verdict: Literal["suitable", "marginal", "not_suitable"]
    score: int
    location: LocationOut
    best_blocks: list[BlockOut] = Field(default_factory=list)
    avoid_blocks: list[BlockOut] = Field(default_factory=list)
    hints: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    daily_estimate: bool = False


class SourceOut(BaseModel):
    title: str
    source_name: str
    url: str


# --- chat and conversations ----------------------------------------------------------------------


class LatLon(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)


class ChatRequest(BaseModel):
    message: str
    conversation_id: str | None = None  # omit to start a new conversation
    location: LatLon | None = None  # the device's position (rounded to 2 decimals on arrival)
    timezone: str | None = None  # IANA name; derived from location when omitted


class ChatResponse(BaseModel):
    conversation_id: str
    kind: Literal["answer", "clarification", "decline", "error"]
    text: str  # canonical text (post-processed); a streaming client replaces its streamed text with this
    cards: list[WeatherCard] = Field(default_factory=list)
    air_quality: list[AirQualityCard] = Field(default_factory=list)
    assessments: list[AssessmentOut] = Field(default_factory=list)
    sources: list[SourceOut] = Field(default_factory=list)


class ConversationOut(BaseModel):
    id: str
    title: str
    created_at: datetime
    updated_at: datetime


class MessageOut(BaseModel):
    role: Literal["user", "assistant"]
    text: str
    kind: str | None = None
    response: ChatResponse | None = None  # assistant turns
    created_at: datetime


class ConversationDetail(BaseModel):
    conversation: ConversationOut
    messages: list[MessageOut]


class HealthOut(BaseModel):
    status: Literal["ok", "degraded"]
    version: str
    guidelines: dict  # {"loaded": bool, "documents": int, "detail": str | None}
    models: dict  # model names only, never keys
    database: bool


# --- dashboard ---------------------------------------------------------------------------------------


class CurrentOut(BaseModel):
    time: datetime
    temp_c: int
    feels_like_c: int
    condition: str | None = None
    description: str | None = None
    icon: str
    humidity_pct: int
    wind_kmh: int
    gust_kmh: int | None = None
    wind_deg: int | None = None
    wind_dir: str | None = None  # "NW"
    uv_index: int | None = None
    visibility_km: float | None = None  # 1 decimal
    pressure_hpa: int | None = None
    sunrise: datetime | None = None
    sunset: datetime | None = None


class DashboardAir(BaseModel):
    scale: Literal["naqi", "owm"]
    value: int | None = None  # now
    category: str | None = None
    dominant_pollutant: str | None = None
    pm2_5: int | None = None  # µg/m³, latest reading
    pm10: int | None = None
    hourly: list[AirHourOut] = Field(default_factory=list)  # the next 24 hours


class OutdoorOut(BaseModel):
    day: Literal["today", "tomorrow"]
    verdict: Literal["suitable", "marginal", "not_suitable"]
    score: int
    best_block: BlockOut | None = None
    reason: str | None = None  # one line from the scoring engine


class DashboardOut(BaseModel):
    location: LocationOut
    local_time: datetime
    current: CurrentOut
    hourly: list[HourOut]
    daily: list[DayOut]
    air_quality: DashboardAir | None = None
    alerts: list[AlertOut] = Field(default_factory=list)
    outdoor_today: OutdoorOut | None = None


class Candidate(BaseModel):
    name: str
    formatted: str
    state: str | None = None
    country: str | None = None
    lat: float
    lon: float


# --- converters (internal -> API) ----------------------------------------------------------------------


def coord(value: float) -> float:
    return round(value, 2)


def location_out(loc: Location) -> LocationOut:
    from backend.agent.observations import place_name

    return LocationOut(name=place_name(loc), formatted=loc.formatted, country_code=loc.country_code,
                       timezone=loc.timezone, lat=coord(loc.lat), lon=coord(loc.lon))


def hour_out(p: WeatherPoint) -> HourOut:
    return HourOut(time=p.time, temp_c=whole(p.temp_c), feels_like_c=whole(p.feels_like_c),
                   rain_prob_pct=percent5(p.rain_prob) if p.rain_prob is not None else None,
                   wind_kmh=whole(p.wind_kmh), condition=p.condition, icon=icon_key(p.condition_id, p.icon))


def day_out(d: DailyWeather) -> DayOut:
    return DayOut(date=d.date.isoformat(), min_c=whole(d.temp_min_c), max_c=whole(d.temp_max_c), condition=d.condition,
                  icon=icon_key(d.condition_id, "d"),
                  rain_prob_pct=percent5(d.rain_prob) if d.rain_prob is not None else None,
                  rain_mm=round(d.rain_mm, 1) if d.rain_mm is not None else None,
                  uv_index=whole(d.uvi) if d.uvi is not None else None)


def alert_out(a: WeatherAlert) -> AlertOut:
    return AlertOut(event=a.event, sender=a.sender, start=a.start, end=a.end)


def weather_card(r: WeatherResult) -> WeatherCard:
    return WeatherCard(location=location_out(r.location), kind=r.granularity, window_start=r.window.start,
                       window_end=r.window.end, hourly=[hour_out(p) for p in r.hourly],
                       daily=[day_out(d) for d in r.daily], alerts=[alert_out(a) for a in r.alerts])


def _scale(result: AirQualityResult) -> Literal["naqi", "owm"]:
    india = result.location.country_code == "in"
    return "naqi" if india and any(p.naqi is not None for p in result.points) else "owm"


def air_hour(p: AirQualityPoint, scale: str) -> AirHourOut:
    if scale == "naqi":
        return AirHourOut(time=p.time, value=p.naqi, category=p.naqi_category)
    return AirHourOut(time=p.time, value=p.owm_aqi, category=OWM_NAMES.get(p.owm_aqi or 0))


def air_card(r: AirQualityResult) -> AirQualityCard:
    scale, s = _scale(r), r.summary
    if scale == "naqi":
        value, category, dominant, worst_at = s.naqi_max, s.naqi_category, s.dominant_pollutant, s.naqi_max_at
    else:
        worst = max((p for p in r.points if p.owm_aqi is not None), key=lambda p: p.owm_aqi, default=None)
        value, category = (worst.owm_aqi, OWM_NAMES[worst.owm_aqi]) if worst else (None, None)
        dominant, worst_at = None, worst.time if worst else None
    return AirQualityCard(
        location=location_out(r.location), scale=scale, value=value, category=category, dominant_pollutant=dominant,
        worst_at=worst_at, pm2_5=whole(s.pm2_5_avg) if s.pm2_5_avg is not None else None,
        pm10=whole(s.pm10_avg) if s.pm10_avg is not None else None, window_start=r.window.start,
        window_end=r.window.end, hourly=[air_hour(p, scale) for p in r.points])


def block_out(b: TimeBlock) -> BlockOut:
    return BlockOut(start=b.start, end=b.end, score=b.average_score, verdict=b.verdict, reasons=list(b.reasons))


def assessment_out(a: ActivityAssessment) -> AssessmentOut:
    return AssessmentOut(activity=a.activity, profile=a.profile, verdict=a.verdict, score=a.score,
                         location=location_out(a.location), best_blocks=[block_out(b) for b in a.best_blocks],
                         avoid_blocks=[block_out(b) for b in a.avoid_blocks], hints=list(a.hints),
                         notes=list(a.data_quality), daily_estimate=a.is_daily_estimate)


def source_out(s: GuidelineSource) -> SourceOut:
    return SourceOut(title=s.title, source_name=s.source_name, url=s.source_url)


def chat_response(r: AgentResponse, conversation_id: str) -> ChatResponse:
    return ChatResponse(conversation_id=conversation_id, kind=r.kind, text=r.text,
                        cards=[weather_card(c) for c in r.cards], air_quality=[air_card(c) for c in r.air_quality],
                        assessments=[assessment_out(a) for a in r.assessments],
                        sources=[source_out(s) for s in r.sources])


def current_out(p: WeatherPoint, sunrise: datetime | None, sunset: datetime | None) -> CurrentOut:
    return CurrentOut(
        time=p.time, temp_c=whole(p.temp_c), feels_like_c=whole(p.feels_like_c), condition=p.condition,
        description=p.description, icon=icon_key(p.condition_id, p.icon), humidity_pct=whole(p.humidity),
        wind_kmh=whole(p.wind_kmh), gust_kmh=whole(p.wind_gust_kmh) if p.wind_gust_kmh is not None else None,
        wind_deg=whole(p.wind_deg) if p.wind_deg is not None else None, wind_dir=compass(p.wind_deg),
        uv_index=whole(p.uvi) if p.uvi is not None else None,
        visibility_km=round(p.visibility_m / 1000, 1) if p.visibility_m is not None else None,
        pressure_hpa=whole(p.pressure_hpa) if p.pressure_hpa is not None else None, sunrise=sunrise, sunset=sunset)


def candidate(loc: Location) -> Candidate:
    from backend.agent.observations import place_name

    return Candidate(name=place_name(loc), formatted=loc.formatted, state=loc.state, country=loc.country,
                     lat=coord(loc.lat), lon=coord(loc.lon))
