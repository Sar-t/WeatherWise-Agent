"""Connect the scoring engine to the agent: when to assess, which profile, one assessment per place,
and the guideline topics an assessment raises (used by the guidelines step)."""

import logging
from datetime import datetime

from backend.agent.schemas import QueryUnderstanding
from backend.services.models import AirQualityResult, WeatherAlert, WeatherResult
from backend.services.scoring import assess_activity
from backend.services.scoring.config import FALLBACK_PROFILE, TwoSided, load_config
from backend.services.scoring.models import ActivityAssessment

log = logging.getLogger(__name__)

ASSESS_INTENTS = {"activity_recommendation", "travel_recommendation", "clothing_recommendation"}
TOPICS = ("heat", "cold", "rain", "thunderstorm", "air_quality", "uv", "wind")  # display order
FACTOR_TOPICS = {"rain_prob": "rain", "rain_mm": "rain", "naqi": "air_quality", "uvi": "uv",
                 "wind_kmh": "wind", "wind_gust_kmh": "wind"}  # feels_like_c: heat or cold; visibility: none yet
# Topic for each severe-alert keyword of the scoring engine (activities.yaml). None: no guideline topic.
ALERT_TOPICS = {
    "heat wave": "heat", "heatwave": "heat", "cold wave": "cold",
    "thunderstorm": "thunderstorm", "lightning": "thunderstorm", "hail": "thunderstorm",
    "heavy rain": "rain", "flood": "rain", "cyclone": "wind", "squall": "wind", "dust storm": "wind",
    "red": None, "orange": None, "severe": None, "extreme": None, "dense fog": None,
}


def should_assess(u: QueryUnderstanding) -> bool:
    """Activity, travel and clothing questions, and comparisons that name an activity."""
    return u.intent in ASSESS_INTENTS or (u.intent == "comparison" and bool(u.activity))


def profile_for(u: QueryUnderstanding) -> str:
    """travel for trips; else the activity asked about; else general outdoor (e.g. clothing)."""
    if u.intent == "travel_recommendation":
        return "travel"
    return u.activity or FALLBACK_PROFILE


def assess_place(
    u: QueryUnderstanding,
    weather: list[WeatherResult],
    air: list[AirQualityResult] | None,
    now: datetime | None,
) -> ActivityAssessment | None:
    """One assessment for a place's forecast windows (current conditions aside), or None if there's
    nothing to assess. `air` is None when air quality wasn't fetched, [] when it failed."""
    windows = [r for r in weather if r.granularity != "current"]
    if not windows:
        return None
    packing = u.intent == "clothing_recommendation" or u.recommendation_type == "packing"
    return assess_activity(profile_for(u), windows, air, now=now, duration_hours=u.duration_hours,
                           hints_from="window" if packing else "best_block")


def guideline_topics(assessments: list[ActivityAssessment], alerts: list[WeatherAlert]) -> list[str]:
    """Topics for the guidelines step: factors that lost points, hard stops and caps, and active
    severe alerts (matched with the scoring engine's keywords)."""
    config = load_config()
    topics: set[str] = set()
    for a in assessments:
        feels = config.profiles[a.profile].factors["feels_like_c"]
        assert isinstance(feels, TwoSided)
        for hour in a.hourly:
            for f in hour.factors:
                if not f.reason:  # lost < 5 points
                    continue
                if f.name == "feels_like_c":
                    topics.add("heat" if f.value > feels.ideal[1] else "cold")
                elif f.name in FACTOR_TOPICS:
                    topics.add(FACTOR_TOPICS[f.name])
            for text in [*hour.hard_stops, *([hour.capped_by] if hour.capped_by else [])]:
                topics |= _stop_topics(text)
    keywords = [k.lower() for k in config.severe_alert_keywords]
    for alert in alerts:
        event = alert.event.lower()
        topics |= {ALERT_TOPICS.get(k) for k in keywords if k in event} - {None}
    return [t for t in TOPICS if t in topics]


def _stop_topics(text: str) -> set[str]:
    """Topics for a hard-stop or cap reason such as "thunderstorm", "heavy rain (9.0 mm/h)", "NAQI 420, Severe"."""
    text = text.lower()
    if text.startswith("weather alert:"):
        return set()  # alerts are matched on their own, with the engine's keywords
    out = set()
    if "thunder" in text:
        out.add("thunderstorm")
    if "rain" in text and "thunder" not in text:
        out.add("rain")
    if "naqi" in text or "air quality" in text:
        out.add("air_quality")
    return out
