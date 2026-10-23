import json
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pytest

from backend.services import air_quality, weather
from backend.services.air_quality import FORECAST_URL, HISTORY_URL
from backend.services.cache import InMemoryCache
from backend.services.errors import InvalidInputError
from backend.services.models import (
    AirComponents,
    AirQualityPoint,
    AirQualityResult,
    DailyWeather,
    Location,
    SunTimes,
    TimeWindow,
    WeatherAlert,
    WeatherPoint,
    WeatherResult,
)
from backend.services.scoring import assess_activity, blocks, hints
from backend.services.scoring.config import (
    ConfigError,
    HintRule,
    LowerLimit,
    TwoSided,
    UpperLimit,
    load_config,
    parse_config,
)
from backend.services.scoring.factors import combine, factor_score, verdict
from backend.services.scoring.models import FactorScore, HourAssessment

IST = ZoneInfo("Asia/Kolkata")
FIXTURES = Path(__file__).parent / "fixtures"
DELHI = Location(name="New Delhi", lat=28.6139, lon=77.209, country="India", country_code="in",
                 formatted="New Delhi, India", result_type="city", timezone="Asia/Kolkata")
NOW = datetime(2026, 10, 22, 12, 0, tzinfo=IST)


def at(hour: int, day: int = 23, minute: int = 0) -> datetime:
    return datetime(2026, 10, day, hour, minute, tzinfo=IST)


def point(t: datetime, **kw) -> WeatherPoint:
    """A pleasant hour for running unless overridden."""
    values = dict(temp_c=20, feels_like_c=20, humidity=50, rain_prob=0.0, wind_kmh=5, wind_gust_kmh=8,
                  uvi=1, visibility_m=10000, rain_mm=0.0, condition="Clear", description="clear sky")
    return WeatherPoint(time=t, **(values | kw))


def hourly(start: datetime, hours: int, alerts=(), **overrides) -> WeatherResult:
    """`overrides` maps an hour offset to that hour's fields, e.g. {2: {"feels_like_c": 40}}."""
    pts = [point(start + timedelta(hours=i), **overrides.get(str(i), {})) for i in range(hours)]
    return WeatherResult(location=DELHI, window=TimeWindow(start=start, end=start + timedelta(hours=hours)),
                         granularity="hourly", hourly=pts, alerts=list(alerts), fetched_at=NOW)


def with_hours(start: datetime, hours: int, changes: dict[int, dict], alerts=()) -> WeatherResult:
    return hourly(start, hours, alerts, **{str(k): v for k, v in changes.items()})


def air(start: datetime, hours: int, naqi: int | None = 80, owm: int | None = 2,
        changes: dict[int, int] | None = None, indicative: bool = False) -> AirQualityResult:
    from backend.services.naqi import category

    pts = []
    for i in range(hours):
        value = (changes or {}).get(i, naqi)
        pts.append(AirQualityPoint(time=start + timedelta(hours=i), components=AirComponents(pm2_5=40), owm_aqi=owm,
                                   naqi=value, naqi_category=category(value) if value is not None else None,
                                   indicative=indicative))
    return AirQualityResult(location=DELHI, window=TimeWindow(start=start, end=start + timedelta(hours=hours)),
                            granularity="hourly", points=pts, fetched_at=NOW)


# --- factor curves ------------------------------------------------------------------


UPPER = UpperLimit(ideal_max=10, acceptable_max=20, hard_max=40)
TWO = TwoSided(ideal=(10, 26), acceptable=(5, 31), hard=(0, 36))
LOWER = LowerLimit(ideal_min=2000, acceptable_min=1000, hard_min=200)


@pytest.mark.parametrize("value, expected", [(0, 100), (10, 100), (15, 75), (20, 50), (30, 25), (40, 0), (99, 0)])
def test_upper_limit_curve(value: float, expected: float) -> None:
    assert factor_score(UPPER, value) == pytest.approx(expected)


@pytest.mark.parametrize("value, expected", [
    (10, 100), (26, 100), (18, 100),  # ideal, both edges
    (28.5, 75), (31, 50), (33.5, 25), (36, 0), (45, 0),  # hot side
    (7.5, 75), (5, 50), (2.5, 25), (0, 0), (-8, 0),  # cold side
])
def test_two_sided_curve(value: float, expected: float) -> None:
    assert factor_score(TWO, value) == pytest.approx(expected)


@pytest.mark.parametrize("value, expected", [(10000, 100), (2000, 100), (1500, 75), (1000, 50), (600, 25), (200, 0), (50, 0)])
def test_lower_limit_curve(value: float, expected: float) -> None:
    assert factor_score(LOWER, value) == pytest.approx(expected)


# --- combining -------------------------------------------------------------------------


def test_weighted_mean_renormalises_over_available_factors() -> None:
    weights = {"a": 3, "b": 1, "c": 2}
    assert combine({"a": 100, "b": 80}, weights) == pytest.approx(95)  # (300 + 80) / 4; c missing
    assert combine({"a": 100, "c": 70}, weights) == pytest.approx(88)  # (300 + 140) / 5


def test_worst_factor_caps_the_mean() -> None:
    weights = {"a": 10, "b": 1}
    assert combine({"a": 100, "b": 20}, weights) == 40  # mean 92.7, capped at worst 20 + 20
    assert combine({"a": 100, "b": 0.1}, weights) == pytest.approx(20.1)
    assert combine({"a": 100, "b": 50}, weights) == 70  # a factor at its acceptable limit: suitable at most just
    assert combine({"a": 100, "b": 0}, weights) == 0  # any factor at 0 zeroes the hour


@pytest.mark.parametrize("score, expected", [(100, "suitable"), (70, "suitable"), (69, "marginal"),
                                             (40, "marginal"), (39, "not_suitable"), (0, "not_suitable")])
def test_verdict_thresholds(score: int, expected: str) -> None:
    assert verdict(score) == expected


# --- hour assessment ----------------------------------------------------------------


def test_reasons_name_value_and_direction() -> None:
    result = assess_activity("running", [with_hours(at(6), 2, {0: {"feels_like_c": 33, "rain_prob": 0.65}})],
                             [air(at(6), 2, changes={0: 230})], now=NOW)
    hour = result.hourly[0]
    # worst first: rain 65% scores 25, feels-like 33 °C 30, NAQI 230 35
    assert hour.reasons == ["rain chance 65%", "feels like 33°C, too hot", "NAQI 230, Poor"]
    assert result.hourly[1].reasons == [] and result.hourly[1].score == 100
    cold = assess_activity("running", [with_hours(at(6), 1, {0: {"feels_like_c": 3}})], now=NOW)
    assert cold.hourly[0].reasons == ["feels like 3°C, too cold"]
    # inside the acceptable range the wording is milder; a loss under 5 points isn't a reason
    mild = assess_activity("running", [with_hours(at(6), 2, {0: {"feels_like_c": 29}, 1: {"feels_like_c": 26.5}})],
                           now=NOW)
    assert [h.reasons for h in mild.hourly] == [["feels like 29°C, warm"], []]
    # At the acceptable limit (running: 5-31 °C), judged on the shown value: "31°C" is warm, "32°C" too hot.
    edge = assess_activity("running", [with_hours(at(6), 4, {0: {"feels_like_c": 31.4}, 1: {"feels_like_c": 31.5},
                                                              2: {"feels_like_c": 4.6}, 3: {"feels_like_c": 4.4}})],
                           now=NOW)
    assert [h.reasons[0] for h in edge.hourly] == ["feels like 31°C, warm", "feels like 32°C, too hot",
                                                   "feels like 5°C, cool", "feels like 4°C, too cold"]
    assert assess_activity("running", [with_hours(at(6), 1, {0: {"feels_like_c": 8}})], now=NOW
                           ).hourly[0].reasons == ["feels like 8°C, cool"]


@pytest.mark.parametrize("change, stop", [
    ({"condition": "Thunderstorm", "description": "thunderstorm with light rain"}, "thunderstorm"),
    ({"condition": "Rain", "description": "heavy intensity rain"}, "heavy intensity rain"),
    ({"rain_mm": 9.0}, "heavy rain (9.0 mm/h)"),
])
def test_weather_hard_stops_zero_the_hour(change: dict, stop: str) -> None:
    result = assess_activity("walking", [with_hours(at(6), 2, {0: change})], now=NOW)
    first, second = result.hourly
    assert (first.score, first.verdict, first.hard_stops[0], first.reasons[0]) == (0, "not_suitable", stop, stop)
    assert second.score == 100


def test_severe_alert_is_a_hard_stop_and_minor_alert_is_only_a_reason() -> None:
    severe = WeatherAlert(sender="IMD", event="Heavy Rain", start=at(7), end=at(8), description="")
    minor = WeatherAlert(sender="IMD", event="Moderate showers", start=at(6), end=at(9), description="")
    result = assess_activity("running", [hourly(at(6), 3, alerts=[severe, minor])], now=NOW)
    six, seven, eight = result.hourly
    assert six.score == 100 and six.reasons == ["weather alert: Moderate showers"]
    assert seven.score == 0 and seven.hard_stops == ["weather alert: Heavy Rain"]
    assert eight.score == 100


def test_severe_naqi_is_a_hard_stop_except_for_travel() -> None:
    weather_, aq = [hourly(at(6), 1)], [air(at(6), 1, naqi=420)]
    walking = assess_activity("walking", weather_, aq, now=NOW).hourly[0]
    assert walking.score == 0 and walking.hard_stops == ["NAQI 420, Severe"]
    assert walking.reasons.count("NAQI 420, Severe") == 1
    travel = assess_activity("travel", weather_, aq, now=NOW).hourly[0]
    assert travel.hard_stops == [] and travel.verdict == "marginal"  # scored, not a stop, for travel
    assert travel.reasons == ["NAQI 420, Severe"]


def test_profile_without_a_hard_stop_kind_ignores_it() -> None:
    # travel has no severe_naqi stop; it still scores thunderstorms as stops
    result = assess_activity("travel", [with_hours(at(6), 1, {0: {"condition": "Thunderstorm"}})], now=NOW)
    assert result.hourly[0].hard_stops == ["thunderstorm"]


# --- missing data ------------------------------------------------------------------------


def test_missing_factors_are_skipped_and_noted() -> None:
    result = assess_activity("running", [with_hours(at(6), 3, {0: {"uvi": None}, 1: {"uvi": None}})], now=NOW)
    first = result.hourly[0]
    assert "uvi" not in {f.name for f in first.factors} and first.score == 100
    assert "UV index missing for 2 of 3 hours; those hours are scored without it." in result.data_quality
    assert "Air quality not provided; scored on weather alone." in result.data_quality


def test_missing_factor_neither_helps_nor_hurts() -> None:
    hot = {"feels_like_c": 33, "uvi": 9}
    with_uv = assess_activity("running", [with_hours(at(6), 1, {0: hot})], now=NOW).hourly[0]
    without = assess_activity("running", [with_hours(at(6), 1, {0: hot | {"uvi": None}})], now=NOW).hourly[0]
    profile = load_config().profiles["running"]
    scores = {f.name: f.score for f in with_uv.factors}
    expected = combine({k: v for k, v in scores.items() if k != "uvi"}, profile.weights)
    assert without.score == round(expected)
    assert without.score != with_uv.score


def test_missing_air_quality_is_noted_for_daily_estimates_and_partial_hours() -> None:
    day, window = date(2026, 10, 26), TimeWindow(start=datetime(2026, 10, 26, 6, tzinfo=IST),
                                                 end=datetime(2026, 10, 26, 17, tzinfo=IST))
    daily = assess_activity("running", [daily_result(day, window)], now=NOW)
    assert "Air quality not provided; scored on weather alone." in daily.data_quality
    partial = assess_activity("running", [hourly(at(6), 3)], [air(at(6), 2)], now=NOW)
    assert "Air quality missing for 1 of 3 hours; those are scored without it." in partial.data_quality


def test_air_quality_results_with_no_naqi_or_owm_are_noted() -> None:
    result = assess_activity("running", [hourly(at(6), 2)], [air(at(6), 2, naqi=None, owm=None)], now=NOW)
    assert "Air quality unavailable; not scored." in result.data_quality


def test_owm_scale_is_used_when_naqi_is_missing() -> None:
    result = assess_activity("running", [hourly(at(6), 1)], [air(at(6), 1, naqi=None, owm=4)], now=NOW)
    naqi = next(f for f in result.hourly[0].factors if f.name == "naqi")
    assert (naqi.value, naqi.reason) == (300, "air quality Poor (OpenWeather 4/5)")
    assert any("OpenWeather's 1-5" in n for n in result.data_quality)


def test_indicative_naqi_is_noted() -> None:
    result = assess_activity("running", [hourly(at(6), 1)], [air(at(6), 1, indicative=True)], now=NOW)
    assert any("indicative" in n for n in result.data_quality)


def test_weather_and_air_align_by_hour_even_with_offset_minutes() -> None:
    # weather at :30 IST and air quality at the same UTC hour, given in UTC
    start = at(6, minute=30)
    aq = air(start.astimezone(ZoneInfo("UTC")), 2, changes={1: 250})
    result = assess_activity("running", [hourly(start, 2)], [aq], now=NOW)
    assert [next(f.value for f in h.factors if f.name == "naqi") for h in result.hourly] == [80, 250]


# --- best time and avoid blocks --------------------------------------------------------


def test_best_time_uses_profile_duration_and_skips_past_hours() -> None:
    # 07:00-12:00 with 07:00 (past) and 11:00 hot: the clean 2 h block starts at 09:00
    now = at(9)
    result = assess_activity("football", [with_hours(at(7), 5, {0: {"feels_like_c": 30}, 4: {"feels_like_c": 30}})],
                             now=now)
    [first, *_] = result.best_blocks
    assert first.start >= now
    assert first.end - first.start == timedelta(hours=2)  # football: 2 h
    assert (first.start, first.average_score) == (at(9), 100)


def test_top_two_blocks_do_not_overlap() -> None:
    result = assess_activity("hiking", [with_hours(at(6), 12, {4: {"feels_like_c": 31}})], now=NOW)
    a, b = result.best_blocks
    assert a.end - a.start == timedelta(hours=4)
    assert a.end <= b.start or b.end <= a.start
    assert a.average_score >= b.average_score


def test_best_blocks_prefer_better_hours() -> None:
    hot = {"feels_like_c": 34}
    result = assess_activity("running", [with_hours(at(6), 4, {0: hot, 1: hot, 3: hot})], now=NOW)
    assert [b.start for b in result.best_blocks] == [at(8), at(6)]  # 100 first, then the earlier tie
    assert result.best_blocks[1].reasons == ["feels like 34°C, too hot"]


def test_daylight_activities_only_get_daylight_blocks() -> None:
    result = assess_activity("cycling", [hourly(at(4), 18)], now=NOW)  # 04:00-22:00
    for block in result.best_blocks:
        assert at(6) <= block.start and block.end <= at(18) + timedelta(minutes=30)
    assert any("06:00-18:00" in n for n in result.data_quality)
    night = assess_activity("hiking", [hourly(at(21), 3)], now=NOW)
    assert night.best_blocks == [] and (night.verdict, night.score) == ("not_suitable", 0)
    assert "No daylight hours in the requested time; hiking needs daylight." in night.data_quality


def test_daylight_uses_sunrise_and_sunset_when_known() -> None:
    result = hourly(at(4), 18)  # 04:00-22:00
    result.sun = [SunTimes(date=date(2026, 10, 23), sunrise=at(6, minute=40), sunset=at(17, minute=40))]
    cycling = assess_activity("cycling", [result], now=NOW)
    starts = {b.start for b in cycling.best_blocks}
    assert min(starts) >= at(7) and max(b.end for b in cycling.best_blocks) <= at(18)
    # 06:00-07:00 has its midpoint before sunrise (06:40); 16:00-18:00 ends with the 17:30 midpoint before sunset
    assert at(6) not in starts
    assert not any("06:00-18:00" in n for n in cycling.data_quality)  # incl. the 21:00-22:00 slot near midnight
    late = hourly(at(20), 4)  # 20:00-24:00: the last slot's midpoint is past midnight, a date with no sun data
    late.sun = [SunTimes(date=date(2026, 10, 23), sunrise=at(6, minute=40), sunset=at(17, minute=40))]
    assert not any("06:00-18:00" in n for n in assess_activity("cycling", [late], now=NOW).data_quality)


def test_daylight_falls_back_per_date_with_a_note() -> None:
    today, tomorrow = hourly(at(6, day=23), 4), hourly(at(6, day=24), 4)
    today.sun = [SunTimes(date=date(2026, 10, 23), sunrise=at(6, minute=40), sunset=at(17, minute=40))]
    result = assess_activity("picnic", [today, tomorrow], now=NOW)
    assert any("06:00-18:00" in n for n in result.data_quality)
    assert at(6, day=24) in {b.start for b in result.best_blocks}  # fallback: 06:00 counts as daylight there


def test_window_shorter_than_duration_gives_a_shorter_block_with_a_note() -> None:
    result = assess_activity("hiking", [hourly(at(7), 2)], now=NOW)
    [block] = result.best_blocks
    assert block.end - block.start == timedelta(hours=2)
    assert any("No stretch of 4 h" in n for n in result.data_quality)


def test_duration_override() -> None:
    result = assess_activity("travel", [hourly(at(6), 6)], now=NOW, duration_hours=3)
    assert result.best_blocks[0].end - result.best_blocks[0].start == timedelta(hours=3)


def test_blocks_never_span_two_windows() -> None:
    morning, evening = hourly(at(6), 2), hourly(at(17), 2)
    result = assess_activity("football", [morning, evening], now=NOW)
    assert [(b.start, b.end) for b in result.best_blocks] == [(at(6), at(8)), (at(17), at(19))]


def test_avoid_blocks_are_contiguous_runs_below_40() -> None:
    storm = {"condition": "Thunderstorm"}
    hot = {"feels_like_c": 38}
    result = assess_activity("running", [with_hours(at(6), 6, {1: storm, 2: hot, 4: hot})], now=NOW)
    assert [(b.start, b.end) for b in result.avoid_blocks] == [(at(7), at(9)), (at(10), at(11))]
    assert result.avoid_blocks[0].reasons == ["thunderstorm", "feels like 38°C, too hot"]
    assert all(b.verdict == "not_suitable" for b in result.avoid_blocks)


def test_overall_follows_the_best_block() -> None:
    hot = {"feels_like_c": 33}
    result = assess_activity("running", [with_hours(at(6), 3, {0: hot, 2: hot})], now=NOW)
    assert (result.score, result.verdict) == (100, "suitable")
    all_hot = assess_activity("running", [with_hours(at(6), 2, {0: hot, 1: hot})], now=NOW)
    assert all_hot.verdict == "marginal"


def test_past_window_has_no_best_time() -> None:
    result = assess_activity("running", [hourly(at(6, day=21), 3)], now=NOW)
    assert result.best_blocks == [] and result.avoid_blocks == []
    assert result.score == 100 and "already passed" in result.data_quality[-1]


def test_block_reasons_put_hard_stops_first() -> None:
    def h(t: datetime, score: int, stops: list[str], factor: FactorScore) -> HourAssessment:
        return HourAssessment(time=t, end=t + timedelta(hours=1), score=score, verdict=verdict(score),
                              factors=[factor], hard_stops=stops)

    slots = [h(at(6), 60, [], FactorScore(name="uvi", value=8, score=60, reason="UV index 8")),
             h(at(7), 0, ["thunderstorm"], FactorScore(name="uvi", value=9, score=40, reason="UV index 9"))]
    assert blocks.block_reasons(slots, 3) == ["thunderstorm", "UV index 9"]


# --- daily estimate -----------------------------------------------------------------------


def daily_result(day: date, window: TimeWindow, **kw) -> WeatherResult:
    values = dict(date=day, temp_min_c=12, temp_max_c=24, temp_morning_c=15, temp_day_c=22, temp_evening_c=20,
                  temp_night_c=14, feels_like_day_c=22, rain_prob=0.1, rain_mm=0.0, wind_kmh=8, wind_max_kmh=15,
                  uvi=5, condition="Clear", description="clear sky")
    return WeatherResult(location=DELHI, window=window, granularity="daily",
                         daily=[DailyWeather(**(values | kw))], fetched_at=NOW)


def test_daily_estimate_scores_parts_of_day() -> None:
    day = date(2026, 10, 26)
    full_day = TimeWindow(start=datetime(2026, 10, 26, tzinfo=IST), end=datetime(2026, 10, 27, tzinfo=IST))
    result = assess_activity("running", [daily_result(day, full_day, feels_like_day_c=33)], now=NOW)

    assert result.is_daily_estimate and result.confidence == "low"
    assert [h.part_of_day for h in result.hourly] == ["morning", "afternoon", "evening", "night"]
    afternoon = result.hourly[1]
    assert afternoon.reasons[0] == "feels like 33°C, too hot"
    assert {f.name for f in afternoon.factors} == {"feels_like_c", "rain_prob", "wind_kmh", "wind_gust_kmh", "uvi"}
    assert "uvi" not in {f.name for f in result.hourly[0].factors}  # UV only for the afternoon
    # best times are whole parts of day, not hour blocks
    assert [(b.start, b.end) for b in result.best_blocks][0] == (
        datetime(2026, 10, 26, 6, tzinfo=IST), datetime(2026, 10, 26, 12, tzinfo=IST))
    assert any(n.startswith("Daily estimate") for n in result.data_quality)
    assert result.hourly[0].reasons == []  # morning 15 °C is ideal; "temperature" wording when it isn't:
    cold = assess_activity("running", [daily_result(day, full_day, temp_morning_c=3)], now=NOW)
    assert cold.hourly[0].reasons == ["temperature 3°C, too cold"]


def test_daily_estimate_only_parts_in_the_window_and_daylight() -> None:
    day = date(2026, 10, 26)
    evening = TimeWindow(start=datetime(2026, 10, 26, 17, tzinfo=IST), end=datetime(2026, 10, 26, 21, tzinfo=IST))
    result = assess_activity("walking", [daily_result(day, evening)], now=NOW)
    assert [h.part_of_day for h in result.hourly] == ["evening"]

    full_day = TimeWindow(start=datetime(2026, 10, 26, tzinfo=IST), end=datetime(2026, 10, 27, tzinfo=IST))
    picnic = assess_activity("picnic", [daily_result(day, full_day)], now=NOW)
    assert {b.start.hour for b in picnic.best_blocks} <= {6, 12}  # morning/afternoon: daylight parts


def test_daily_thunderstorm_caps_at_marginal_instead_of_stopping() -> None:
    day = date(2026, 10, 26)
    full_day = TimeWindow(start=datetime(2026, 10, 26, tzinfo=IST), end=datetime(2026, 10, 27, tzinfo=IST))
    storm = assess_activity("running", [daily_result(day, full_day, condition="Thunderstorm",
                                                     description="thunderstorm with rain")], now=NOW)
    assert [h.score for h in storm.hourly] == [45, 45, 45, 45]  # each part would otherwise score 100
    assert all(h.hard_stops == [] and h.verdict == "marginal" for h in storm.hourly)
    assert storm.hourly[0].reasons[0] == "thunderstorms possible during the day"
    assert storm.verdict == "marginal" and storm.best_blocks[0].reasons == ["thunderstorms possible during the day"]
    assert ("Thunderstorms possible during the day; check the hourly forecast closer to the date."
            in storm.data_quality)

    hot = assess_activity("running", [daily_result(day, full_day, condition="Thunderstorm",
                                                   feels_like_day_c=36)], now=NOW)
    assert hot.hourly[1].score == 0  # the cap never raises a score; a factor at its hard limit still zeroes


def test_hourly_thunderstorm_is_still_a_hard_stop() -> None:
    result = assess_activity("running", [with_hours(at(6), 1, {0: {"condition": "Thunderstorm"}})], now=NOW)
    assert (result.hourly[0].score, result.hourly[0].hard_stops) == (0, ["thunderstorm"])
    assert not any("Thunderstorms possible" in n for n in result.data_quality)


def test_daily_heavy_rain_is_still_a_stop() -> None:
    day = date(2026, 10, 26)
    window = TimeWindow(start=datetime(2026, 10, 26, 6, tzinfo=IST), end=datetime(2026, 10, 26, 12, tzinfo=IST))
    wet = assess_activity("running", [daily_result(day, window, rain_mm=80.0)], now=NOW)
    assert wet.hourly[0].hard_stops == ["heavy rain (80 mm in the day)"] and wet.hourly[0].score == 0


def test_daily_part_uses_worst_air_quality_hour() -> None:
    day = date(2026, 10, 26)
    window = TimeWindow(start=datetime(2026, 10, 26, 6, tzinfo=IST), end=datetime(2026, 10, 26, 12, tzinfo=IST))
    aq = air(datetime(2026, 10, 26, 6, tzinfo=IST), 6, changes={3: 260})
    result = assess_activity("running", [daily_result(day, window)], [aq], now=NOW)
    assert next(f.value for f in result.hourly[0].factors if f.name == "naqi") == 260


# --- unknown activity, hints, inputs --------------------------------------------------------


def test_unknown_activity_falls_back_to_general_outdoor() -> None:
    result = assess_activity("Kite Flying", [hourly(at(6), 2)], now=NOW)
    assert result.profile == "general_outdoor" and result.activity == "Kite Flying"
    assert "No profile for 'Kite Flying'; used the general outdoor profile." in result.data_quality
    known = assess_activity(" Running ", [hourly(at(6), 2)], now=NOW)
    assert known.profile == "running" and not any("No profile" in n for n in known.data_quality)


def test_hint_rules() -> None:
    rules = load_config().hints

    def slot(**values: float) -> HourAssessment:
        return HourAssessment(time=at(6), end=at(7), score=50, verdict="marginal",
                              factors=[FactorScore(name=k, value=v, score=50) for k, v in values.items()])

    assert hints.hints(rules, [slot(rain_prob=0.4)]) == ["Carry an umbrella or a rain jacket."]
    assert hints.hints(rules, [slot(rain_prob=0.39, feels_like_c=13)]) == []
    assert hints.hints(rules, [slot(feels_like_c=11), slot(feels_like_c=20)]) == ["Dress in layers."]
    assert hints.hints(rules, [slot(naqi=201, uvi=6)]) == [
        "Consider an N95 mask and a shorter or lighter session.", "Use sunscreen and wear a cap."]


def test_hints_are_capped_at_four_in_priority_order() -> None:
    rules = load_config().hints
    many = HourAssessment(time=at(6), end=at(7), score=0, verdict="not_suitable", factors=[
        FactorScore(name=k, value=v, score=0) for k, v in
        {"feels_like_c": 40, "naqi": 300, "rain_prob": 0.9, "uvi": 10, "wind_kmh": 40}.items()])
    out = hints.hints(rules, [many])
    assert len(out) == 4 and out[0].startswith("Wear light") and "Take a windproof layer." not in out


def test_hints_deduplicate() -> None:
    rules = [HintRule(factor="rain_prob", at_least=0.4, text="Umbrella."),
             HintRule(factor="rain_mm", at_least=1, text="Umbrella.")]
    slot = HourAssessment(time=at(6), end=at(7), score=50, verdict="marginal", factors=[
        FactorScore(name="rain_prob", value=0.8, score=0), FactorScore(name="rain_mm", value=3, score=0)])
    assert hints.hints(rules, [slot]) == ["Umbrella."]


def test_hints_can_cover_the_whole_window() -> None:
    hot = {"feels_like_c": 36}
    window = [with_hours(at(10), 4, {2: hot, 3: hot})]
    assert assess_activity("walking", window, now=NOW).hints == []  # best block 10:00 is mild
    assert assess_activity("walking", window, now=NOW, hints_from="window").hints[0].startswith("Wear light")


def test_assessment_hints_come_from_the_best_block() -> None:
    hot = {"feels_like_c": 36}
    result = assess_activity("walking", [with_hours(at(10), 4, {2: hot, 3: hot})], now=NOW)
    assert result.best_blocks[0].start == at(10) and result.hints == []
    all_hot = assess_activity("walking", [with_hours(at(12), 1, {0: hot})], now=NOW)
    assert all_hot.hints[0].startswith("Wear light")


def test_needs_weather_and_aware_now() -> None:
    with pytest.raises(InvalidInputError):
        assess_activity("running", [])
    with pytest.raises(InvalidInputError):
        assess_activity("running", [hourly(at(6), 1)], now=datetime(2026, 10, 22, 12))


# --- config validation ------------------------------------------------------------------------


def test_shipped_config_is_valid() -> None:
    config = load_config()
    assert set(config.profiles) == {"running", "cycling", "walking", "hiking", "football", "picnic", "travel",
                                    "general_outdoor"}
    assert {p: config.profiles[p].needs_daylight for p in ("hiking", "cycling", "picnic")} == dict.fromkeys(
        ("hiking", "cycling", "picnic"), True)
    assert {p: c.duration_hours for p, c in config.profiles.items()} == {
        "running": 1, "cycling": 2, "walking": 1, "hiking": 4, "football": 2, "picnic": 3, "travel": 1,
        "general_outdoor": 2}


def _yaml_with(profile_text: str) -> str:
    shipped = (Path(__file__).parents[1] / "services" / "scoring" / "activities.yaml").read_text(encoding="utf-8")
    return shipped.replace("profiles:\n", f"profiles:\n{profile_text}", 1)


GOOD_FACTORS = """\
      feels_like_c:  {ideal: [10, 26], acceptable: [5, 31], hard: [0, 36]}
      rain_prob:     {ideal_max: 0.2, acceptable_max: 0.5, hard_max: 0.8}
      rain_mm:       {ideal_max: 0.5, acceptable_max: 2.5, hard_max: 7.6}
      wind_kmh:      {ideal_max: 15, acceptable_max: 30, hard_max: 45}
      wind_gust_kmh: {ideal_max: 25, acceptable_max: 45, hard_max: 65}
      uvi:           {ideal_max: 3, acceptable_max: 7, hard_max: 11}
      visibility_m:  {ideal_min: 2000, acceptable_min: 1000, hard_min: 200}
      naqi:          {ideal_max: 100, acceptable_max: 200, hard_max: 300}
"""
GOOD_WEIGHTS = ("    weights: {feels_like_c: 3, rain_prob: 2, rain_mm: 2, wind_kmh: 1, wind_gust_kmh: 1, "
                "uvi: 1, visibility_m: 1, naqi: 3}\n")


def profile(factors: str = GOOD_FACTORS, weights: str = GOOD_WEIGHTS, extra: str = "") -> str:
    return (f"  test:\n    duration_hours: 1\n    needs_daylight: false\n{extra}    factors:\n{factors}"
            f"{weights}    hard_stops: [thunderstorm]\n")


def test_valid_extra_profile_parses() -> None:
    assert "test" in parse_config(_yaml_with(profile())).profiles


@pytest.mark.parametrize("text, message", [
    (profile(factors=GOOD_FACTORS.replace("hard_max: 300", "hard_max: 150")), "ideal_max < acceptable_max < hard_max"),
    (profile(factors=GOOD_FACTORS.replace("hard: [0, 36]", "hard: [6, 36]")), "hard[0] < acceptable[0]"),
    (profile(factors=GOOD_FACTORS.replace("hard_min: 200", "hard_min: 1500")), "ideal_min > acceptable_min"),
    (profile(factors=GOOD_FACTORS.replace("      uvi:           {ideal_max: 3, acceptable_max: 7, hard_max: 11}\n",
                                          "")), "missing factor(s): uvi"),
    (profile(factors=GOOD_FACTORS + "      humidity: {ideal_max: 60, acceptable_max: 80, hard_max: 95}\n"),
     "unknown factor(s): humidity"),
    (profile(factors=GOOD_FACTORS.replace("{ideal_min: 2000", "{ideal_max: 2000")), "visibility_m"),
    (profile(weights=GOOD_WEIGHTS.replace("naqi: 3", "naqi: 0")), "weights must be positive: naqi"),
    (profile(weights=GOOD_WEIGHTS.replace(", naqi: 3", "")), "weights must cover exactly"),
    (profile(extra="    colour: blue\n"), "colour"),
    (profile().replace("[thunderstorm]", "[tornado]"), "hard_stops"),
    (profile().replace("duration_hours: 1", "duration_hours: 0"), "duration_hours"),
])
def test_malformed_profile_fails_loudly(text: str, message: str) -> None:
    with pytest.raises(ConfigError, match="test") as exc:
        parse_config(_yaml_with(text))
    assert message in str(exc.value)


def test_missing_fallback_profile_and_bad_yaml_fail() -> None:
    shipped = _yaml_with("")
    with pytest.raises(ConfigError, match="general_outdoor"):
        parse_config(shipped.replace("  general_outdoor:", "  general_outdoorx:"))
    with pytest.raises(ConfigError):
        parse_config("profiles: [unclosed")
    with pytest.raises(ConfigError, match="exactly one of at_least"):
        parse_config(shipped.replace("{factor: uvi, at_least: 6,", "{factor: uvi,"))


# --- realistic: Delhi fixtures ---------------------------------------------------------------


def _fixture(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_delhi_tomorrow_morning_from_fixtures(respx_mock, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(weather, "cache", InMemoryCache())
    monkeypatch.setattr(air_quality, "cache", InMemoryCache())
    respx_mock.get(weather.ONECALL_URL).mock(
        return_value=httpx.Response(200, json=_fixture("openweather_onecall_delhi_aq")))
    respx_mock.get(FORECAST_URL).mock(return_value=httpx.Response(200, json=_fixture("openweather_air_forecast_delhi")))
    respx_mock.get(HISTORY_URL).mock(return_value=httpx.Response(200, json=_fixture("openweather_air_history_delhi")))

    window = TimeWindow(start=at(6), end=at(12))  # Fri 23 Oct morning
    now = at(11, day=22, minute=51)
    w = await weather.get_weather(DELHI, window, now=now)
    aq = await air_quality.get_air_quality(DELHI, window, now=now)
    result = assess_activity("running", [w], [aq], now=now)

    assert len(result.hourly) == len(w.hourly) == 6
    for hour, wp, ap in zip(result.hourly, w.hourly, aq.points):
        values = {f.name: f.value for f in hour.factors}
        assert values["feels_like_c"] == round(wp.feels_like_c, 2)
        assert values["naqi"] == ap.naqi  # aligned hour by hour
        assert 0 <= hour.score <= 100 and hour.verdict == verdict(hour.score)
    best = result.best_blocks[0]
    assert best.average_score == max(h.score for h in result.hourly)
    assert result.score == best.average_score and result.verdict == verdict(result.score)
    assert result.windows == [w.window] and not result.is_daily_estimate
    assert w.sun and result.data_quality == []  # real sun times, air quality and every factor present
    # every factor that lost >= 5 points is explained in that hour's reasons
    for hour in result.hourly:
        assert [f.reason for f in hour.factors if f.score < 95] == [f.reason for f in hour.factors if f.reason]
        assert all(f.reason in hour.reasons for f in hour.factors if f.reason)
