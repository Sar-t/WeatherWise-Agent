"""Answer evals: REAL answer LLM, recorded weather and geocoding (reproducible).

Manual only (not collected by pytest). From the repo root, venv active:
    python evals/run_answers.py [--pause 5] [--only id,id] [--model gemini-2.5-flash] [--refresh]
Extraction is skipped: each case supplies its QueryUnderstanding, so a case costs at most one LLM
call. Replies are cached in evals/.cache/ by model + prompt hash; --refresh ignores the cache.
"""

import argparse
import asyncio
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
from backend.services import weather
from backend.services.cache import InMemoryCache
from backend.services.weather import DAY_SUMMARY_URL, ONECALL_URL, TIMEMACHINE_URL
from backend.tests.agent_fakes import QueuedExtractor, fixture_now, install_fake_geocoding, mock_onecall

ROOT = Path(__file__).parent
CASES = ROOT / "answer_cases.yaml"
CACHE = ROOT / ".cache"
FIXTURES = ROOT.parent / "backend" / "tests" / "fixtures"
NOWS = {"jaipur": fixture_now("jaipur"), "cities": fixture_now("mumbai")}

_NUM = r"-?\d+(?:\.\d+)?"
# "27°C", "27 °C", "27°", "35%", and ranges like "22-27°C", "22 to 27°C", "22 se 27°C" (both ends count).
_READING = re.compile(rf"(?<![\d:.])({_NUM})(?:\s*(?:-|–|to|se)\s*({_NUM}))?\s*(°\s*C|°|%)")  # not "06:30"
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
    """(value, unit, start, end) for each temperature/percentage in `text`."""
    out = []
    for m in _READING.finditer(text):
        unit = "%" if m.group(3) == "%" else "°C"
        for g in (1, 2):
            if m.group(g) is not None:
                out.append((float(m.group(g)), unit, m.start(), m.end()))
    return out


def check_grounding(reply: str, observations: str) -> Grounding:
    """Every °C / % reading in the reply must appear in the observations, which are already rounded
    for display (whole °C, rain chance to 5%), so the match is exact."""
    known = {(unit, value) for value, unit, _, _ in readings(observations)}
    result = Grounding()
    for value, unit, start, end in readings(reply):
        if (unit, value) in known:
            continue
        context = reply[max(0, start - 20): end + 25].replace("\n", " ")
        derived = _DERIVED_AFTER.search(reply[end:end + 30]) or _DERIVED_BEFORE.search(reply[max(0, start - 15):start])
        (result.warnings if derived else result.failures).append(f"{value:g}{unit} in '…{context}…'")
    return result


def install_recorded_weather(router: respx.MockRouter) -> None:
    mock_onecall(router)
    for url, name in ((TIMEMACHINE_URL, "timemachine"), (DAY_SUMMARY_URL, "day_summary")):
        data = json.loads((FIXTURES / f"openweather_{name}_jaipur.json").read_text(encoding="utf-8"))
        router.get(url).mock(return_value=httpx.Response(200, json=data))


async def run_case(case: dict, answer_llm: CachedAnswerLLM) -> tuple[list[str], list[str], list[str]]:
    """Returns (failures, warnings, reply lines) for one case."""
    weather.cache = InMemoryCache()
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
            failures.append(f"{tag}no °C or % figure in the reply (expected key facts with units)")
        failures += [f"{tag}style: {p}" for p in check_style(response.text, is_comparison(response.cards))]
        grounding = check_grounding(response.text, observations)
        failures += [f"{tag}ungrounded reading {f}" for f in grounding.failures]
        warnings += [f"{tag}derived number {w}" for w in grounding.warnings]
    return failures, warnings, replies


async def main(pause: float, only: set[str], model_name: str | None, refresh: bool) -> None:
    settings = get_settings()
    if model_name:
        settings = settings.model_copy(update={"answer_model": model_name})
    name = settings.answer_model or settings.llm_model
    answer_llm = CachedAnswerLLM(get_llm("answer", settings=settings), name, pause, refresh)
    cases = [c for c in yaml.safe_load(CASES.read_text(encoding="utf-8")) if not only or c["id"] in only]
    print(f"Answer model: {settings.llm_provider}/{name}  cases: {len(cases)}  pause: {pause}s  "
          f"cache: {'refresh' if refresh else 'on'}\n")

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
