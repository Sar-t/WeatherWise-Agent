from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from backend.agent.assess import ALERT_TOPICS, TOPICS, guideline_topics, profile_for, should_assess
from backend.agent.schemas import QueryUnderstanding
from backend.services.models import TimeWindow, WeatherAlert
from backend.services.scoring import assess_activity
from backend.services.scoring.config import load_config
from backend.tests.test_scoring import NOW, air, at, daily_result, with_hours


def u(**slots) -> QueryUnderstanding:
    return QueryUnderstanding(**({"intent": "activity_recommendation"} | slots))


# --- which questions, which profile ---------------------------------------------------


@pytest.mark.parametrize("understanding, expected", [
    (u(activity="running"), True),
    (u(intent="travel_recommendation"), True),
    (u(intent="clothing_recommendation"), True),
    (u(intent="comparison", activity="running"), True),
    (u(intent="comparison"), False),
    (u(intent="weather", activity="running"), False),
    (u(intent="air_quality"), False),
])
def test_should_assess(understanding: QueryUnderstanding, expected: bool) -> None:
    assert should_assess(understanding) is expected


@pytest.mark.parametrize("understanding, expected", [
    (u(activity="hiking"), "hiking"),
    (u(intent="travel_recommendation", activity="cycling"), "travel"),  # a trip is a trip
    (u(intent="clothing_recommendation"), "general_outdoor"),
    (u(intent="clothing_recommendation", activity="picnic"), "picnic"),
    (u(), "general_outdoor"),
])
def test_profile_for(understanding: QueryUnderstanding, expected: str) -> None:
    assert profile_for(understanding) == expected


# --- guideline topics -------------------------------------------------------------------


def topics_for(changes: dict, activity: str = "running", aq=None, alerts=()) -> list[str]:
    return guideline_topics([assess_activity(activity, [with_hours(at(6), 3, changes)], aq, now=NOW)], list(alerts))


def test_pleasant_hours_raise_no_topics() -> None:
    assert topics_for({}) == []


@pytest.mark.parametrize("change, topic", [
    ({"feels_like_c": 33}, "heat"),
    ({"feels_like_c": 3}, "cold"),
    ({"rain_prob": 0.6}, "rain"),
    ({"rain_mm": 3.0}, "rain"),
    ({"uvi": 9}, "uv"),
    ({"wind_kmh": 35}, "wind"),
    ({"wind_gust_kmh": 55}, "wind"),
    ({"condition": "Thunderstorm", "description": "thunderstorm"}, "thunderstorm"),
    ({"condition": "Rain", "description": "heavy intensity rain"}, "rain"),
])
def test_factors_and_hard_stops_map_to_topics(change: dict, topic: str) -> None:
    assert topic in topics_for({1: change})


def test_small_losses_and_visibility_raise_no_topic() -> None:
    assert topics_for({1: {"feels_like_c": 26.5}}) == []  # loses < 5 points
    assert topics_for({1: {"visibility_m": 600}}) == []  # no fog guideline yet


def test_air_quality_topic_from_naqi() -> None:
    assert topics_for({}, aq=[air(at(6), 3, changes={1: 250})]) == ["air_quality"]
    assert topics_for({}, aq=[air(at(6), 3, changes={1: 420})]) == ["air_quality"]  # Severe: a hard stop


def test_topics_come_in_a_fixed_order() -> None:
    assert topics_for({0: {"uvi": 9}, 1: {"feels_like_c": 33, "rain_prob": 0.7}}) == ["heat", "rain", "uv"]


@pytest.mark.parametrize("event, topic", [
    ("Heat Wave", "heat"), ("Severe Heatwave", "heat"), ("Cold Wave", "cold"),
    ("Thunderstorm & Lightning", "thunderstorm"), ("Heavy Rain", "rain"), ("Flood Watch", "rain"),
    ("Dust Storm", "wind"), ("Squall", "wind"),
])
def test_severe_alerts_map_to_topics(event: str, topic: str) -> None:
    alert = WeatherAlert(sender="IMD", event=event, start=at(6), end=at(9), description="")
    assert topic in topics_for({}, alerts=[alert])


def test_minor_and_topicless_alerts_add_nothing() -> None:
    alerts = [WeatherAlert(sender="IMD", event=e, start=at(6), end=at(9), description="")
              for e in ("Moderate showers", "Orange alert", "Dense Fog")]
    assert topics_for({}, alerts=alerts) == []


def test_alert_topics_cover_every_engine_keyword() -> None:
    keywords = {k.lower() for k in load_config().severe_alert_keywords}
    assert keywords == set(ALERT_TOPICS)  # keep the two lists in step when activities.yaml changes
    assert {t for t in ALERT_TOPICS.values() if t} <= set(TOPICS)


def test_daily_thunderstorm_cap_raises_the_thunderstorm_topic() -> None:
    ist = ZoneInfo("Asia/Kolkata")
    window = TimeWindow(start=datetime(2026, 10, 26, 6, tzinfo=ist), end=datetime(2026, 10, 26, 12, tzinfo=ist))
    a = assess_activity("running", [daily_result(date(2026, 10, 26), window, condition="Thunderstorm")], now=NOW)
    assert guideline_topics([a], []) == ["thunderstorm"]


def test_topics_merge_across_places() -> None:
    hot = assess_activity("running", [with_hours(at(6), 2, {0: {"feels_like_c": 33}})], now=NOW)
    windy = assess_activity("running", [with_hours(at(6), 2, {1: {"wind_kmh": 35}})], now=NOW)
    assert guideline_topics([hot, windy], []) == ["heat", "wind"]
