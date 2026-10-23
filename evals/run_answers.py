"""Answer evals: REAL answer LLM, recorded weather and geocoding (reproducible).

Manual only (not collected by pytest). From the repo root, venv active:
    python evals/run_answers.py [--pause 5] [--only id,id] [--model gemini-2.5-flash] [--refresh]
Extraction is skipped: each case supplies its QueryUnderstanding, so a case costs at most one LLM
call. Replies are cached in evals/.cache/ by model + prompt hash; --refresh ignores the cache.
"""

import argparse
import asyncio
import copy
import hashlib
import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
import yaml
from langchain_core.messages import AIMessage

from backend.agent.answer import is_comparison
from backend.agent.graph import Agent, build_graph
from backend.agent.llm import get_llm, message_text
from backend.agent.schemas import QueryUnderstanding, UserContext
from backend.core.config import get_settings
from backend.services import air_quality, guidelines, weather
from backend.services.display import percent5, whole
from backend.services.cache import InMemoryCache
from backend.services.weather import DAY_SUMMARY_URL, ONECALL_URL, TIMEMACHINE_URL
from backend.tests.agent_fakes import (
    AIR_BY_LAT,
    ONECALL_BY_LAT,
    ONECALL_BY_LAT_AQ,
    QueuedExtractor,
    fixture_now,
    air,
    install_fake_geocoding,
    onecall,
)

ROOT = Path(__file__).parent
CASES = ROOT / "answer_cases.yaml"
CACHE = ROOT / ".cache"
FIXTURES = ROOT.parent / "backend" / "tests" / "fixtures"
NOWS = {"jaipur": fixture_now("jaipur"), "cities": fixture_now("mumbai"), "aq": fixture_now("delhi_aq")}
_onecall_by_lat: dict[str, str] = {}  # per-case override of which onecall recording a latitude gets

_NUM = r"-?\d+(?:\.\d+)?"
# "27°C", "27 °C", "27°", "35%", and ranges like "22-27°C", "22 to 27°C", "22 se 27°C" (both ends count).
_READING = re.compile(rf"(?<![\d:.])({_NUM})(?:\s*(?:-|–|to|se)\s*({_NUM}))?\s*(°\s*C|°|%)")  # not "06:30"
# Air quality: "NAQI 230", "AQI of 230", "air quality index is 230"; "2/5"; "63 µg/m³".
_NAQI = re.compile(r"\b(?:NAQI|AQI|air quality index)\D{0,12}?(?<![\d.])(\d{1,3})(?![\d./])", re.I)
_OWM_LEVEL = re.compile(r"(?<![\d.])([1-5])\s*/\s*5\b")
_PM = re.compile(rf"(?<![\d.])({_NUM})\s*(?:µ|μ|u)g\s*/\s*m(?:³|3)")
_TIME = re.compile(r"(?<![\d:])(\d{1,2}):(\d{2})(?:\s*([ap])\.?m\.?\b)?|(?<![\d:])(\d{1,2})\s*([ap])\.?m\.?\b", re.I)

# Verdict wording, matched as whole words/phrases. Negative phrases are found first and blanked out,
# so "not suitable" never counts as "suitable" (bare "nahi" is too broad: "baarish nahi hogi").
VERDICT_PHRASES = {
    "not_suitable": ["not suitable", "unsuitable", "not a good", "isn't a good", "is not a good", "not ideal",
                     "not recommended", "better to skip", "skip it", "theek nahi", "thik nahi", "accha nahi",
                     "achha nahi", "sahi nahi", "mat jao", "na jaye", "nahi jaana"],
    "marginal": ["possible", "with care", "doable", "manageable", "okay but", "ok but", "marginal", "keep an eye",
                 "be careful", "dhyan", "dhyaan"],
    "suitable": ["suitable", "good time", "good day", "good idea", "good for", "great", "ideal", "perfect",
                 "fine", "theek hai", "thik hai", "accha hai", "achha hai", "badhiya"],
}
# Wording that contradicts each engine verdict. A marginal verdict tolerates mild words either way,
# but not the strongest ones.
CONTRADICTS = {"suitable": ["not_suitable"], "not_suitable": ["suitable", "marginal"], "marginal": []}
MARGINAL_TOO_STRONG = ["perfect", "ideal", "great", "not suitable", "not recommended", "better to skip"]


def _phrase(p: str) -> re.Pattern:
    return re.compile(rf"(?<!\w){re.escape(p)}(?!\w)", re.I)


def verdict_wording(reply: str) -> tuple[set[str], str]:
    """(verdict classes the wording expresses, the reply with negative phrases blanked out)."""
    text, found = reply, set()
    for verdict in ("not_suitable", "marginal", "suitable"):  # negatives first
        for p in VERDICT_PHRASES[verdict]:
            if _phrase(p).search(text):
                found.add(verdict)
                if verdict == "not_suitable":
                    text = _phrase(p).sub(" ", text)  # "not suitable" can't then match "suitable"
    return found, text


def check_verdict(reply: str, verdict: str) -> tuple[list[str], list[str]]:
    """(failures, warnings): the reply must not word a different verdict; missing wording only warns."""
    found, blanked = verdict_wording(reply)
    failures = [f"verdict is {verdict!r} but the reply reads {c!r}" for c in CONTRADICTS[verdict] if c in found]
    if verdict == "marginal":
        failures += [f"verdict is 'marginal' but the reply says {p!r}" for p in MARGINAL_TOO_STRONG
                     if _phrase(p).search(reply if p.startswith(("not", "better")) else blanked)]
    warnings = [] if verdict in found or failures else [f"no wording found for verdict {verdict!r}"]
    return failures, warnings


def first_sentence(text: str) -> str:
    """The reply's direct answer (the prompt makes it the first sentence)."""
    return re.split(r"(?<=[.!?])\s+", text.strip(), maxsplit=1)[0]


# Conditional statements that need their line in the observations.
_UNSUPPORTED = [
    (re.compile(r"rough (daily )?outlook|daily estimate", re.I), "DAILY ESTIMATE",
     "calls it a rough outlook / daily estimate, but the observations aren't one"),
    (re.compile(r"air quality (couldn't|could not|can't|cannot|wasn't|was not) be checked"
                r"|air quality check nahi", re.I), "AIR QUALITY UNAVAILABLE",
     "says air quality couldn't be checked, but nothing says so"),
]


def check_unsupported(reply: str, observations: str) -> list[str]:
    return [why for pattern, needed, why in _UNSUPPORTED if pattern.search(reply) and needed not in observations]


def times(text: str) -> set[str]:
    """Clock times as 24h 'HH:MM': '06:30', '6:30 pm' -> '18:30', '6 am' -> '06:00'."""
    out = set()
    for m in _TIME.finditer(text):
        hour, minute, meridiem = (int(m.group(1)), int(m.group(2)), m.group(3)) if m.group(1) else (
            int(m.group(4)), 0, m.group(5))
        if meridiem:
            hour = hour % 12 + (12 if meridiem.lower() == "p" else 0)
        out.add(f"{hour:02d}:{minute:02d}")
    return out


_DERIVED_AFTER = re.compile(r"^\W*(?:\w+\W+){0,2}?(warmer|cooler|hotter|colder|higher|lower|more|less|difference|"
                            r"drop|rise|apart|up|down)\b", re.I)
_DERIVED_BEFORE = re.compile(r"\b(by|difference of|differ by)\s*$", re.I)


_GREETING = re.compile(r"^\W*(hi|hello|hey|namaste|greetings|good (morning|afternoon|evening|day))\b", re.I)
_SIGN_OFF = re.compile(r"(stay (safe|hydrated|cool|dry|warm)|have a (great|nice|good|lovely)|enjoy (your|the)|"
                       r"let me know|hope (this|that) helps|happy \w+ing)\b[^.!?]*[.!?]?\s*$", re.I)
_BULLET = re.compile(r"^\s*([-*•]|\d+[.)])\s+", re.M)


def check_style(reply: str, comparing: bool) -> list[str]:
    """No greeting, no sign-off, and bullets only when comparing 2+ places, days or windows."""
    problems = []
    if _GREETING.search(reply):
        problems.append(f"starts with a greeting: {reply.splitlines()[0][:40]!r}")
    if _SIGN_OFF.search(reply[-120:]):
        problems.append(f"ends with a sign-off: {reply.strip()[-60:]!r}")
    if not comparing and _BULLET.search(reply):
        problems.append("uses bullets but compares nothing")
    return problems


class CachedAnswerLLM:
    """The real answer model behind a disk cache, pausing between real calls."""

    def __init__(self, model: Any, model_name: str, pause: float, refresh: bool) -> None:
        self.model, self.model_name, self.pause, self.refresh = model, model_name, pause, refresh
        self.last_prompt: list | None = None
        self.real_calls = self.cache_hits = 0
        self._last_real_call: float | None = None

    async def ainvoke(self, messages: list) -> AIMessage:
        self.last_prompt = messages
        payload = json.dumps([self.model_name] + [[m.type, m.content] for m in messages], ensure_ascii=False)
        path = CACHE / f"{hashlib.sha256(payload.encode()).hexdigest()[:24]}.json"
        if path.exists() and not self.refresh:
            self.cache_hits += 1
            return AIMessage(content=json.loads(path.read_text(encoding="utf-8"))["reply"])
        if self._last_real_call is not None:
            await asyncio.sleep(max(0.0, self.pause - (time.monotonic() - self._last_real_call)))
        try:
            reply = message_text(await self.model.ainvoke(messages))
        finally:
            self._last_real_call = time.monotonic()
        self.real_calls += 1
        CACHE.mkdir(exist_ok=True)
        path.write_text(json.dumps({"model": self.model_name, "reply": reply}, ensure_ascii=False, indent=1),
                        encoding="utf-8")
        return AIMessage(content=reply)


@dataclass
class Grounding:
    failures: list[str] = field(default_factory=list)  # readings not in the data
    warnings: list[str] = field(default_factory=list)  # derived numbers (differences etc.)


def readings(text: str) -> list[tuple[float, str, int, int]]:
    """(value, unit, start, end) for each temperature, percentage and air quality figure in `text`."""
    out = []
    for m in _READING.finditer(text):
        unit = "%" if m.group(3) == "%" else "°C"
        for g in (1, 2):
            if m.group(g) is not None:
                out.append((float(m.group(g)), unit, m.start(), m.end()))
    for pattern, unit in ((_NAQI, "NAQI"), (_OWM_LEVEL, "/5"), (_PM, "µg/m³")):
        out += [(float(m.group(1)), unit, m.start(), m.end()) for m in pattern.finditer(text)]
    return out


def check_grounding(reply: str, observations: str) -> Grounding:
    """Every °C / % / NAQI / N-of-5 / µg/m³ figure in the reply must appear in the observations, which
    are already rounded for display (whole °C, rain chance to 5%, whole µg/m³), so the match is exact."""
    known = {(unit, _display(unit, value)) for value, unit, _, _ in readings(observations)}
    result = Grounding()
    for value, unit, start, end in readings(reply):
        if (unit, _display(unit, value)) in known:
            continue
        context = reply[max(0, start - 20): end + 25].replace("\n", " ")
        derived = _DERIVED_AFTER.search(reply[end:end + 30]) or _DERIVED_BEFORE.search(reply[max(0, start - 15):start])
        (result.warnings if derived else result.failures).append(f"{value:g}{unit} in '…{context}…'")
    return result


def _display(unit: str, value: float) -> float:
    """A figure as the shared display helper would show it, so equal values compare equal."""
    if unit == "%":
        return percent5(value / 100) if value <= 100 else value
    return value if unit == "/5" else whole(value)


def check_times(reply: str, observations: str) -> list[str]:
    """Every clock time in the reply must appear in the observations (best/avoid blocks, hours)."""
    return [f"time {t} not in the observations" for t in sorted(times(reply) - times(observations))]


# SYNTHETIC per-case overrides (a case's `synthetic:`), clearly separate from the recordings:
# air_scale multiplies recorded PM2.5/PM10 (no recording has Poor air); alerts are added to the
# recorded forecast (no recording has an alert).
_synthetic: dict = {}


def _with_synthetic_alerts(data: dict) -> dict:
    alerts = _synthetic.get("alerts")
    if not alerts:
        return data
    data = copy.deepcopy(data)
    start = int(data["current"]["dt"])
    data["alerts"] = [{"sender_name": "IMD (synthetic eval alert)", "event": a["event"], "description": "Synthetic.",
                       "start": start, "end": start + 3600 * a.get("hours", 36), "tags": []} for a in alerts]
    return data


def _with_synthetic_air(data: dict) -> dict:
    scale = _synthetic.get("air_scale")
    if not scale:
        return data
    data = copy.deepcopy(data)
    for entry in data.get("list") or []:
        components = entry.get("components") or {}
        for key in ("pm2_5", "pm10"):
            if components.get(key) is not None:
                components[key] = round(components[key] * scale, 2)
    return data


def install_recorded_weather(router: respx.MockRouter) -> None:
    """Recorded weather and air quality. Air quality exists for Delhi and Paris only (others: HTTP 503)."""
    from backend.services.air_quality import CURRENT_URL, FORECAST_URL, HISTORY_URL

    def onecall_for(request: httpx.Request) -> httpx.Response:
        name = (ONECALL_BY_LAT | _onecall_by_lat).get(request.url.params["lat"], "jaipur")
        return httpx.Response(200, json=_with_synthetic_alerts(onecall(name)))

    router.get(ONECALL_URL).mock(side_effect=onecall_for)
    for url, name in ((TIMEMACHINE_URL, "timemachine"), (DAY_SUMMARY_URL, "day_summary")):
        data = json.loads((FIXTURES / f"openweather_{name}_jaipur.json").read_text(encoding="utf-8"))
        router.get(url).mock(return_value=httpx.Response(200, json=data))

    def air_for(kind: str):
        def respond(request: httpx.Request) -> httpx.Response:
            name = AIR_BY_LAT.get(request.url.params["lat"])
            return httpx.Response(200, json=_with_synthetic_air(air(kind, name))) if name else httpx.Response(503)
        return respond

    for url, kind in ((CURRENT_URL, "current"), (FORECAST_URL, "forecast"), (HISTORY_URL, "history")):
        router.get(url).mock(side_effect=air_for(kind))


SOURCE_WORDS = ("CPCB", "Health Ministry", "MoHFW", "Ministry of Health", "NDMA", "WHO", "World Health")


def check_guidance(response, expected: bool) -> list[str]:
    """With guidance: sources are returned and the reply names one. Without: neither."""
    named = [w for w in SOURCE_WORDS if re.search(rf"(?<!\w){re.escape(w)}(?!\w)", response.text)]
    if expected:
        return ([] if response.sources else ["no guidance sources returned"]) + (
            [] if named else ["reply names no official source"])
    return ([f"unexpected guidance sources: {[s.title for s in response.sources]}"] if response.sources else []) + (
        [f"reply cites {named} without guidance"] if named else [])


async def run_case(case: dict, answer_llm: CachedAnswerLLM) -> tuple[list[str], list[str], list[str]]:
    """Returns (failures, warnings, reply lines) for one case."""
    weather.cache = InMemoryCache()
    air_quality.cache = InMemoryCache()
    _onecall_by_lat.clear()
    if case["now"] == "aq":  # weather recorded alongside the air quality fixtures
        _onecall_by_lat.update(ONECALL_BY_LAT_AQ)
    _synthetic.clear()
    _synthetic.update(case.get("synthetic") or {})
    turns = case["turns"]
    extractor = QueuedExtractor(*(QueryUnderstanding(**t["understanding"]) for t in turns))
    ctx = UserContext(now=NOWS[case["now"]], **case.get("device", {}))
    agent = Agent(ctx, graph=build_graph(extractor=extractor, answer_llm=answer_llm))

    failures, warnings, replies = [], [], []
    for i, turn in enumerate(turns, 1):
        answer_llm.last_prompt = None
        response = await agent.ask(turn["message"])
        expect = turn["expect"]
        replies.append(f"[{response.kind}] {response.text}")
        tag = f"turn {i}: " if len(turns) > 1 else ""
        if response.kind != expect["kind"]:
            failures.append(f"{tag}kind {response.kind!r}, expected {expect['kind']!r} ({response.debug.error})")
            continue
        if response.kind != "answer":
            continue
        if response.debug.answer_fallback:
            warnings.append(f"{tag}answer LLM failed, template used: {response.debug.error}")
        names = expect.get("locations", [])
        if names and not any(n.lower() in response.text.lower() for n in names):
            failures.append(f"{tag}none of {names} named in the reply")
        observations = answer_llm.last_prompt[-1].content if answer_llm.last_prompt else ""
        if not readings(response.text):
            failures.append(f"{tag}no °C, %, NAQI or µg/m³ figure in the reply (expected key facts with units)")
        failures += [f"{tag}doesn't mention {word!r}" for word in expect.get("mentions", [])
                     if word.lower() not in response.text.lower()]
        failures += [f"{tag}mentions {word!r}" for word in expect.get("not_mentions", [])
                     if word.lower() in response.text.lower()]
        failures += [f"{tag}style: {p}" for p in check_style(response.text,
                                                             is_comparison(response.cards, response.air_quality))]
        grounding = check_grounding(response.text, observations)
        failures += [f"{tag}ungrounded reading {f}" for f in grounding.failures]
        warnings += [f"{tag}derived number {w}" for w in grounding.warnings]
        failures += [f"{tag}{f}" for f in check_times(response.text, observations)]
        failures += [f"{tag}{f}" for f in check_unsupported(response.text, observations)]
        if "guidance" in expect:
            failures += [f"{tag}{f}" for f in check_guidance(response, expect["guidance"])]
        if len(response.assessments) == 1 and not response.debug.answer_fallback:  # comparisons word two verdicts
            # Only the direct answer: later sentences may rightly call an avoid window "not suitable".
            verdict_failures, verdict_warnings = check_verdict(first_sentence(response.text),
                                                               response.assessments[0].verdict)
            failures += [f"{tag}{f}" for f in verdict_failures]
            warnings += [f"{tag}{w}" for w in verdict_warnings]
    return failures, warnings, replies


async def main(pause: float, only: set[str], model_name: str | None, refresh: bool) -> None:
    settings = get_settings()
    if model_name:
        settings = settings.model_copy(update={"answer_model": model_name})
    name = settings.answer_model or settings.llm_model
    answer_llm = CachedAnswerLLM(get_llm("answer", settings=settings), name, pause, refresh)
    cases = [c for c in yaml.safe_load(CASES.read_text(encoding="utf-8")) if not only or c["id"] in only]
    try:  # the real local retriever (build the index first: python -m ingestion.build_index)
        warm = f"{guidelines.warm_up():.1f}s"
    except Exception as exc:
        warm = f"unavailable ({type(exc).__name__}); cases expecting guidance will fail"
    print(f"Answer model: {settings.llm_provider}/{name}  cases: {len(cases)}  pause: {pause}s  "
          f"cache: {'refresh' if refresh else 'on'}  guidelines: {warm}\n")

    patcher = pytest.MonkeyPatch()
    install_fake_geocoding(patcher)
    passed, total_warnings = 0, 0
    try:
        with respx.mock(assert_all_called=False) as router:
            router.route(host="generativelanguage.googleapis.com").pass_through()  # only the LLM is live
            install_recorded_weather(router)
            for case in cases:
                failures, warnings, replies = await run_case(case, answer_llm)
                passed += not failures
                total_warnings += len(warnings)
                print(f"{'ok  ' if not failures else 'FAIL'} {case['id']}")
                for line in replies:
                    print(f"       {line}")
                for f in failures:
                    print(f"     ✗ {f}")
                for w in warnings:
                    print(f"     ! {w}")
    finally:
        patcher.undo()

    print(f"\nCases passed: {passed}/{len(cases)}   warnings: {total_warnings}   "
          f"LLM calls: {answer_llm.real_calls} real, {answer_llm.cache_hits} cached")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Answer evals against the real LLM with recorded weather.")
    parser.add_argument("--pause", type=float, default=5.0, help="seconds between real LLM calls (default 5)")
    parser.add_argument("--only", default="", help="comma-separated case ids to run")
    parser.add_argument("--model", default=None, help="answer model to use (default: ANSWER_MODEL or LLM_MODEL)")
    parser.add_argument("--refresh", action="store_true", help="ignore cached replies and call the LLM again")
    args = parser.parse_args()
    asyncio.run(main(args.pause, {x for x in args.only.split(",") if x}, args.model, args.refresh))
