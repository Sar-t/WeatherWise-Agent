from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest
from langchain_core.messages import AIMessage

from backend.agent.answer import build_messages, flatten_bullets, generate_answer, is_comparison, template_answer
from backend.agent.schemas import QueryUnderstanding
from backend.services.models import DailyWeather, TimeWindow, WeatherAlert, WeatherPoint, WeatherResult
from backend.tests.agent_fakes import RecordingChatModel, candidates

pytestmark = pytest.mark.anyio

IST = ZoneInfo("Asia/Kolkata")
JAIPUR = candidates("geoapify_search_jaipur.json")[0]
U = QueryUnderstanding(intent="activity_recommendation", activity="running", recommendation_type="suitability",
                       locations=["Jaipur"], date_expr="tomorrow", time_expr="morning")
OBS = ["Jaipur | FORECAST, hourly | Mon 19 Oct, 06:00-12:00 IST\nSummary of 6 hours: temp 22.0°C to 27.0°C"]


def at(h: int, d: int = 19) -> datetime:
    return datetime(2026, 10, d, h, tzinfo=IST)


def point(h: int, temp: float, rain: float | None = 0.1, d: int = 19) -> WeatherPoint:
    return WeatherPoint(time=at(h, d), temp_c=temp, feels_like_c=temp + 1, humidity=50, rain_prob=rain,
                        wind_kmh=10 + h, rain_mm=0.0, description="clear sky")


def card(granularity: str = "hourly", **fields) -> WeatherResult:
    return WeatherResult(location=JAIPUR, window=TimeWindow(start=at(6), end=at(12)), granularity=granularity,
                         fetched_at=at(5), **fields)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.agent import llm

    async def no_sleep(delay: float) -> None:
        return None

    monkeypatch.setattr(llm, "_sleep", no_sleep)


def test_prompt_has_rules_question_slots_and_observations() -> None:
    system, human = build_messages("good for a run tmrw morning?", U, OBS)
    assert "Use only numbers that appear in the observations" in system.content
    assert "mention the alerts first" in system.content and "past tense" in system.content
    assert "Hinglish" in system.content and "120 words" in system.content
    assert "No greetings" in system.content and "no sign-offs" in system.content
    assert "No bullet points" in system.content and "one bullet per place, day or window" in system.content
    assert human.content.startswith("User message: good for a run tmrw morning?")
    assert "'activity': 'running'" in human.content and "'recommendation_type': 'suitability'" in human.content
    assert human.content.endswith(OBS[0])


def test_prompt_states_the_users_current_time() -> None:
    _, human = build_messages("tomorrow evening?", U, OBS, now=datetime(2026, 10, 18, 20, 17, tzinfo=IST))
    assert human.content.startswith("Now (user's local time): Sun 18 Oct 2026, 20:17 IST" + chr(10) + "User message:")


async def test_llm_reply_is_returned() -> None:
    llm = RecordingChatModel(replies=["  Good morning for a run: 22-27°C.  "])
    answer = await generate_answer("run?", U, OBS, [card(hourly=[point(6, 22)])], llm=llm)
    assert (answer.text, answer.fallback) == ("Good morning for a run: 22-27°C.", False)
    assert len(llm.calls) == 1


BULLETED = """The best time to cycle in Jaipur tomorrow is the evening.

*   Early morning has a **100%** rain chance at 06:30
*   The afternoon peaks at 32°C.
- evening: 0% rain chance, 27°C

Carry water."""


async def test_bullets_are_joined_into_sentences_for_one_place_and_window() -> None:
    llm = RecordingChatModel(replies=[BULLETED])
    answer = await generate_answer("q", U, OBS, [card(hourly=[point(6, 22)])], llm=llm)
    assert answer.text == ("The best time to cycle in Jaipur tomorrow is the evening. Early morning has a 100% rain "
                           "chance at 06:30. The afternoon peaks at 32°C. Evening: 0% rain chance, 27°C.\n\nCarry water.")


async def test_bullets_are_kept_for_comparisons() -> None:
    llm = RecordingChatModel(replies=[BULLETED])
    other = card(hourly=[point(17, 27)]).model_copy(update={"window": TimeWindow(start=at(17), end=at(21))})
    cards = [card(hourly=[point(6, 22)]), other]
    assert is_comparison(cards)
    assert (await generate_answer("q", U, OBS, cards, llm=llm)).text == BULLETED


def test_air_quality_cards_count_towards_a_comparison() -> None:
    from backend.tests.test_observations import air_result

    jaipur_air = air_result()
    paris_air = air_result("fr").model_copy(update={"location": air_result("fr").location.model_copy(
        update={"lat": 48.85, "lon": 2.35})})
    assert not is_comparison([], [jaipur_air])
    assert is_comparison([], [jaipur_air, paris_air])  # an air-quality-only comparison keeps its bullets


def test_template_leads_with_the_engines_verdict_and_best_time() -> None:
    from backend.tests.test_observations import an_assessment

    a = an_assessment()
    daily = a.model_copy(update={"is_daily_estimate": True, "best_blocks": []})
    text = template_answer([], [], [a, daily])
    assert text.splitlines() == [
        f"Running in New Delhi, Fri 23 Oct, 06:00-12:00 IST: suitable ({a.score}/100); best time Fri 06:00-07:00.",
        f"Running in New Delhi, Fri 23 Oct, 06:00-12:00 IST: suitable ({a.score}/100). "
        "This is a rough outlook; check again closer to the date.",
    ]


def test_template_air_quality_lines() -> None:
    from backend.tests.test_observations import air_result

    text = template_answer([], [air_result(), air_result("fr")])
    assert text.splitlines() == [
        "Jaipur air quality, Fri 23 Oct, 06:00-09:00 IST: NAQI up to 230 (Poor, mainly PM2.5).",
        "Paris, France air quality, Fri 23 Oct, 06:00-09:00 IST: up to 3/5 (Moderate) on OpenWeather's scale.",
    ]


@pytest.mark.parametrize("text, expected", [
    ("No bullets here.\nSecond line.", "No bullets here.\nSecond line."),  # untouched
    ("1. first\n2) second!", "First. Second!"),  # numbered, no lead-in
    ("Lead-in:\n- a\n\n- b", "Lead-in: A. B."),  # blank line between bullets
])
def test_flatten_bullets(text: str, expected: str) -> None:
    assert flatten_bullets(text) == expected


async def test_content_blocks_are_flattened() -> None:
    class BlockLLM:
        async def ainvoke(self, messages):
            return AIMessage(content=[{"type": "text", "text": "Expected "}, {"type": "text", "text": "27°C."}])

    answer = await generate_answer("q", U, OBS, [], llm=BlockLLM())
    assert answer.text == "Expected 27°C."


@pytest.mark.parametrize("failure", [RuntimeError("503 UNAVAILABLE"), ValueError("bad"), ""])
async def test_failure_or_empty_reply_uses_template(failure) -> None:
    llm = RecordingChatModel(replies=[failure] * 4)
    cards = [card(hourly=[point(6, 22.4, 0.1), point(7, 26.6, 0.35)])]
    answer = await generate_answer("q", U, OBS, cards, llm=llm)
    assert answer.fallback is True and answer.error
    assert answer.text == ("Jaipur, Mon 19 Oct, 06:00-12:00 IST: expected 22-27°C, rain chance up to 35%, "
                           "0.0 mm rain, wind up to 17 km/h.")


def test_template_alerts_first_then_cards() -> None:
    alert = WeatherAlert(sender="IMD", event="Heavy rain", start=at(9), end=at(18), description="...")
    text = template_answer([card(hourly=[point(6, 22)], alerts=[alert]), card("current", hourly=[point(5, 21)])])
    lines = text.splitlines()
    assert lines[0] == "Weather alert: Heavy rain (IMD), Mon 09:00 to Mon 18:00."
    assert lines[2] == "Jaipur now: 21°C (feels 22°C), wind 15 km/h."


def test_template_past_and_daily() -> None:
    past = card("historical", hourly=[point(6, 22, rain=None)])
    assert ": was 22-22°C, 0.0 mm rain" in template_answer([past])

    day = DailyWeather(date=date(2026, 10, 22), temp_min_c=21.4, temp_max_c=30.6, rain_prob=0.26, rain_mm=0.35)
    text = template_answer([card("daily", daily=[day])])
    assert text.endswith(": expected Thu 22 Oct 21-31°C, rain chance 25%, 0.3 mm rain.")
