"""Run the understanding evals against the REAL LLM and print accuracy per slot.

Manual only (not collected by pytest). From the repo root, venv active:
    python evals/run_understanding.py [--pause 5] [--only weather_basic,packing]
Pauses between calls to stay under free-tier per-minute limits.
"""

import argparse
import asyncio
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from backend.agent.extraction import extract
from backend.agent.llm import LLMRateLimitError
from backend.agent.memory import merge
from backend.agent.schemas import QueryUnderstanding, UserContext
from backend.core.config import get_settings
from backend.services import date_resolver
from backend.services.errors import UnrecognizedDateError

CASES = Path(__file__).parent / "understanding_cases.yaml"
_FIXED = datetime(2026, 1, 1, tzinfo=timezone.utc)


def same(slot: str, expected: Any, actual: Any) -> bool:
    if slot == "locations":
        return sorted(x.strip().lower() for x in expected) == sorted(x.strip().lower() for x in actual)
    if slot == "time_expr" and expected is not None and actual is not None:
        return _window(expected) == _window(actual)
    if slot == "include_air_quality":  # "not mentioned" may come back as null or false
        return bool(expected) == bool(actual)
    return expected == actual


def _window(time_expr: str) -> Any:
    """Compare time ranges by meaning: '7-9pm' == '19:00-21:00'."""
    try:
        return [(w.start, w.end) for w in date_resolver.resolve("today", time_expr, "UTC", now=_FIXED)]
    except UnrecognizedDateError:
        return time_expr.strip().lower()


async def run_case(case: dict[str, Any], ctx: UserContext) -> tuple[QueryUnderstanding | None, str | None]:
    previous = QueryUnderstanding(**case["previous"]) if case.get("previous") else None
    try:
        extraction = await extract(case["message"], ctx, previous=previous, pending_question=case.get("pending_question"))
    except LLMRateLimitError as exc:
        return None, str(exc)
    except Exception as exc:  # network blips etc.: count as a miss, keep going
        return None, f"{type(exc).__name__}: {exc}"
    u = extraction.understanding
    return (merge(previous, u) if previous is not None and u.is_follow_up else u), None


async def main(pause: float, only: set[str]) -> None:
    cases = [c for c in yaml.safe_load(CASES.read_text(encoding="utf-8")) if not only or c["id"] in only]
    ctx = UserContext()
    print(f"Model: {get_settings().llm_provider}/{get_settings().llm_model}  cases: {len(cases)}  pause: {pause}s\n")

    per_slot: dict[str, list[bool]] = defaultdict(list)
    passed = 0
    for i, case in enumerate(cases):
        if i:
            await asyncio.sleep(pause)
        u, error = await run_case(case, ctx)
        if u is None:
            for slot in case["expected"]:
                per_slot[slot].append(False)
            print(f"ERR  {case['id']}: {error}")
            continue
        misses = []
        for slot, expected in case["expected"].items():
            actual = getattr(u, slot)
            ok = same(slot, expected, actual)
            per_slot[slot].append(ok)
            if not ok:
                misses.append(f"{slot}: expected {expected!r}, got {actual!r}")
        passed += not misses
        print(f"{'ok ' if not misses else 'FAIL'} {case['id']}" + "".join(f"\n       {m}" for m in misses))

    print("\nAccuracy per slot:")
    for slot, results in sorted(per_slot.items()):
        print(f"  {slot:20} {sum(results):3}/{len(results):<3} {sum(results) / len(results):6.0%}")
    print(f"\nCases fully correct: {passed}/{len(cases)}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Understanding evals against the real LLM.")
    parser.add_argument("--pause", type=float, default=5.0, help="seconds between LLM calls (default 5)")
    parser.add_argument("--only", default="", help="comma-separated case ids to run")
    args = parser.parse_args()
    asyncio.run(main(args.pause, {x for x in args.only.split(",") if x}))
