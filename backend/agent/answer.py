"""Write the reply from compact observations (LLM call #2), with a template fallback in code."""

import logging
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from backend.agent.llm import call_with_backoff, invoke, message_text
from backend.agent.observations import OWM_AQI_NAMES, hourly_stats, place_name, window_text
from backend.agent.schemas import QueryUnderstanding
from backend.services.display import celsius, chance, kmh, whole
from backend.services.models import AirQualityResult, WeatherResult
from backend.services.scoring.models import ActivityAssessment

log = logging.getLogger(__name__)

PROMPT_PATH = Path(__file__).parent / "prompts" / "answer.md"
_BULLET = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+")


@dataclass
class Answer:
    text: str
    fallback: bool  # True when the LLM failed and the template wrote the reply
    error: str | None = None


def build_messages(
    message: str, understanding: QueryUnderstanding, observations: list[str], now: datetime | None = None
) -> list:
    """The answer prompt: rules, then the time, question, slots and observations (text only).

    `now` (the user's local time) lets the model relate "today"/"tomorrow" to the dated observations.
    """
    slots = understanding.model_dump(
        exclude_defaults=True, include={"intent", "activity", "recommendation_type", "time_expr"}
    )
    body = "\n".join([
        *([f"Now (user's local time): {now:%a %d %b %Y, %H:%M %Z}"] if now else []),
        f"User message: {message}",
        f"Understood as: {slots or {'intent': understanding.intent}}",
        "",
        "Observations:",
        "\n\n".join(observations),
    ])
    return [SystemMessage(PROMPT_PATH.read_text(encoding="utf-8")), HumanMessage(body)]


async def generate_answer(
    message: str,
    understanding: QueryUnderstanding,
    observations: list[str],
    cards: list[WeatherResult],
    llm: Any = None,
    now: datetime | None = None,
    air_cards: list[AirQualityResult] = (),
    assessments: list[ActivityAssessment] = (),
    guidance_note: str | None = None,
) -> Answer:
    """LLM reply grounded in `observations`; if the LLM fails, a template reply from the cards."""
    messages = build_messages(message, understanding, observations, now)
    try:
        if llm is not None:
            reply = await call_with_backoff(lambda: llm.ainvoke(messages))
        else:
            reply = await invoke("answer", lambda model: model.ainvoke(messages))
        text = message_text(reply).strip()
        if not text:
            raise ValueError("empty reply from the answer model")
        if not is_comparison(cards, air_cards):
            text = flatten_bullets(text)  # the prompt asks for prose; models still slip into lists
        return Answer(text, fallback=False)
    except Exception as exc:  # any LLM failure: the user still gets the key numbers
        log.warning("answer llm failed, using template", extra={"error": f"{type(exc).__name__}: {exc}"})
        return Answer(template_answer(cards, air_cards, assessments, guidance_note), fallback=True,
                      error=f"{type(exc).__name__}: {exc}")


def is_comparison(cards: list[WeatherResult], air_cards: list[AirQualityResult] = ()) -> bool:
    """2+ distinct place/window cards (current conditions aside), or one card spanning 2+ days."""
    spans = {(c.location.lat, c.location.lon, c.window.start, c.window.end)
             for c in [*cards, *air_cards] if c.granularity != "current"}
    return len(spans) >= 2 or any(len(c.daily) >= 2 for c in cards)


def flatten_bullets(text: str) -> str:
    """Join bulleted or numbered lines into the paragraph before them, as plain sentences."""
    if not any(_BULLET.match(line) for line in text.splitlines()):
        return text
    paragraphs: list[list[str]] = [[]]
    for line in text.splitlines():
        line = line.strip()
        if not line:
            if paragraphs[-1]:
                paragraphs.append([])
            continue
        if bullet := _BULLET.match(line):
            item = line[bullet.end():].replace("**", "").strip()
            if not item:
                continue
            item = item[0].upper() + item[1:]
            if item[-1] not in ".!?":
                item += "."
            target = paragraphs[-1] if paragraphs[-1] or len(paragraphs) == 1 else paragraphs[-2]
            target.append(item)
        else:
            paragraphs[-1].append(line)
    return "\n\n".join(" ".join(p) for p in paragraphs if p)


def template_answer(cards: list[WeatherResult], air_cards: list[AirQualityResult] = (),
                    assessments: list[ActivityAssessment] = (), guidance_note: str | None = None) -> str:
    """A plain reply built in code from the same data the LLM would have seen: alerts, the engine's
    verdicts and best times, then the weather and air quality."""
    alerts = [a for card in cards for a in card.alerts]
    lines = [f"Weather alert: {a.event} ({a.sender}), {a.start:%a %H:%M} to {a.end:%a %H:%M}." for a in
             {(a.event, a.start): a for a in alerts}.values()]
    lines += [_assessment_line(a) for a in assessments]
    for card in cards:
        lines.append(_card_line(card))
    for card in air_cards:
        lines.append(_air_line(card))
    if guidance_note:
        lines.append(guidance_note)
    return "\n".join(lines)


def _assessment_line(a: ActivityAssessment) -> str:
    """'Running in New Delhi, Fri 23 Oct, 06:00-12:00 IST: suitable (86/100); best time Fri 06:30-07:30.'"""
    activity = a.profile.replace("_", " ")
    windows = "; ".join(window_text(w) for w in a.windows)
    line = f"{activity.capitalize()} in {place_name(a.location)}, {windows}: {a.verdict.replace('_', ' ')} ({a.score}/100)"
    if a.best_blocks:
        b = a.best_blocks[0]
        line += f"; best time {b.start:%a %H:%M}-{b.end:%H:%M}"
    line += "."
    if a.is_daily_estimate:
        line += " This is a rough outlook; check again closer to the date."
    return line


def _air_line(card: AirQualityResult) -> str:
    where = f"{place_name(card.location)} air quality, {window_text(card.window)}"
    s = card.summary
    if card.location.country_code == "in" and s.naqi_max is not None:
        main = f", mainly {s.dominant_pollutant}" if s.dominant_pollutant else ""
        return f"{where}: NAQI up to {s.naqi_max} ({s.naqi_category}{main})."
    if s.owm_aqi_max is not None:
        return f"{where}: up to {s.owm_aqi_max}/5 ({OWM_AQI_NAMES[s.owm_aqi_max]}) on OpenWeather's scale."
    return f"{where}: no readings available."


def _card_line(card: WeatherResult) -> str:
    where = f"{place_name(card.location)}, {window_text(card.window)}"
    if card.granularity == "current" and card.hourly:
        p = card.hourly[0]
        return (f"{place_name(card.location)} now: {celsius(p.temp_c)} (feels {celsius(p.feels_like_c)}), "
                f"wind {kmh(p.wind_kmh)}.")
    past = card.granularity == "historical"
    if card.hourly:
        s = hourly_stats(card.hourly)
        rain = f", rain chance up to {chance(s.rain_prob_max)}" if s.rain_prob_max is not None else ""
        verb = "was" if past else "expected"
        return (f"{where}: {verb} {whole(s.temp_min)}-{celsius(s.temp_max)}{rain}, {s.rain_total_mm:.1f} mm rain, "
                f"wind up to {kmh(s.wind_max)}.")
    if card.daily:
        days = []
        for d in card.daily:
            rain = f", rain chance {chance(d.rain_prob)}" if d.rain_prob is not None else ""
            mm = f", {d.rain_mm:.1f} mm rain" if d.rain_mm is not None else ""
            days.append(f"{d.date:%a %d %b} {whole(d.temp_min_c)}-{celsius(d.temp_max_c)}{rain}{mm}")
        return f"{where}: {'observed' if past else 'expected'} " + "; ".join(days) + "."
    return f"{where}: no data available."
