"""Write the reply from compact observations (LLM call #2), with a template fallback in code."""

import logging
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from backend.agent.llm import call_with_backoff, get_llm, message_text
from backend.agent.observations import hourly_stats, place_name, window_text
from backend.agent.schemas import QueryUnderstanding
from backend.services.models import WeatherResult

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
        "Weather observations:",
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
) -> Answer:
    """LLM reply grounded in `observations`; if the LLM fails, a template reply from `cards`."""
    messages = build_messages(message, understanding, observations, now)
    try:
        model = llm or get_llm("answer")
        reply = await call_with_backoff(lambda: model.ainvoke(messages))
        text = message_text(reply).strip()
        if not text:
            raise ValueError("empty reply from the answer model")
        if not is_comparison(cards):
            text = flatten_bullets(text)  # the prompt asks for prose; models still slip into lists
        return Answer(text, fallback=False)
    except Exception as exc:  # any LLM failure: the user still gets the key numbers
        log.warning("answer llm failed, using template", extra={"error": f"{type(exc).__name__}: {exc}"})
        return Answer(template_answer(cards), fallback=True, error=f"{type(exc).__name__}: {exc}")


def is_comparison(cards: list[WeatherResult]) -> bool:
    """2+ distinct place/window cards (current conditions aside), or one card spanning 2+ days."""
    spans = {(c.location.lat, c.location.lon, c.window.start, c.window.end) for c in cards if c.granularity != "current"}
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


def template_answer(cards: list[WeatherResult]) -> str:
    """A plain reply built in code from the same data the LLM would have seen."""
    alerts = [a for card in cards for a in card.alerts]
    lines = [f"Weather alert: {a.event} ({a.sender}), {a.start:%a %H:%M} to {a.end:%a %H:%M}." for a in
             {(a.event, a.start): a for a in alerts}.values()]
    for card in cards:
        lines.append(_card_line(card))
    return "\n".join(lines)


def _card_line(card: WeatherResult) -> str:
    where = f"{place_name(card.location)}, {window_text(card.window)}"
    if card.granularity == "current" and card.hourly:
        p = card.hourly[0]
        return f"{place_name(card.location)} now: {p.temp_c:.0f}°C (feels {p.feels_like_c:.0f}°C), wind {p.wind_kmh:.0f} km/h."
    past = card.granularity == "historical"
    if card.hourly:
        s = hourly_stats(card.hourly)
        rain = f", rain chance up to {s.rain_prob_max * 100:.0f}%" if s.rain_prob_max is not None else ""
        verb = "was" if past else "expected"
        return (f"{where}: {verb} {s.temp_min:.0f}-{s.temp_max:.0f}°C{rain}, {s.rain_total_mm:.1f} mm rain, "
                f"wind up to {s.wind_max:.0f} km/h.")
    if card.daily:
        days = []
        for d in card.daily:
            rain = f", rain chance {d.rain_prob * 100:.0f}%" if d.rain_prob is not None else ""
            mm = f", {d.rain_mm:.1f} mm rain" if d.rain_mm is not None else ""
            days.append(f"{d.date:%a %d %b} {d.temp_min_c:.0f}-{d.temp_max_c:.0f}°C{rain}{mm}")
        return f"{where}: {'observed' if past else 'expected'} " + "; ".join(days) + "."
    return f"{where}: no data available."
