"""Interactive check of the understanding layer: slots, resolved places and windows, or a question.

Usage (from the repo root, venv active):
    python -m backend.agent.cli --lat 26.91 --lon 75.79 [--debug]
--lat/--lon simulate the device location. Type 'quit' or press Ctrl+C to exit.
"""

import argparse
import asyncio
import time

from backend.agent.llm import LLMConfigError, LLMRateLimitError
from backend.agent.memory import Conversation
from backend.agent.schemas import ResolvedQuery, UserContext
from backend.services.errors import UpstreamAPIError


def print_result(result: ResolvedQuery, conversation: Conversation, debug: bool, seconds: float) -> None:
    u = result.understanding
    print("  slots:", u.model_dump(exclude_defaults=True) or {"intent": u.intent})
    if result.status == "out_of_scope":
        print("  -> out of scope (will be declined politely)")
    elif result.status == "clarify":
        print(f"  -> ask: {result.clarification.question}  [{result.clarification.reason}]")
    else:
        print(f"  -> {result.date_expr} / {result.time_expr}")
        for target in result.targets:
            loc = target.location
            print(f"  {loc.name} — {loc.formatted} ({loc.timezone})")
            for w in target.windows:
                print(f"      {w.start:%a %d %b %H:%M} -> {w.end:%a %d %b %H:%M} {w.start:%Z}")
    if debug:
        extraction = conversation.last_extraction
        if extraction is None:
            print("  [debug] no LLM call (clarification answered directly)")
        else:
            print(f"  [debug] llm: {extraction.seconds:.2f}s, attempts={extraction.attempts}")
            print(f"  [debug] raw: {extraction.raw}")
        print(f"  [debug] total: {seconds:.2f}s")


async def main(lat: float | None, lon: float | None, debug: bool) -> None:
    ctx = UserContext(lat=lat, lon=lon)
    conversation = Conversation(ctx)
    where = f"({lat}, {lon}), {ctx.timezone}" if ctx.has_location else f"no device location, {ctx.timezone}"
    print(f"WeatherWise understanding CLI. Location: {where}. Type 'quit' to exit.")
    while True:
        try:
            message = input("\nyou> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if message.lower() in {"quit", "exit", "q"}:
            break
        if not message:
            continue
        started = time.perf_counter()
        try:
            result = await conversation.handle(message)
        except (LLMConfigError, LLMRateLimitError, UpstreamAPIError) as exc:
            print(f"  error: {exc}")
            continue
        except Exception as exc:  # e.g. a provider 503 "high demand": report it, keep the session
            print(f"  error ({type(exc).__name__}): {str(exc)[:200]}")
            continue
        print_result(result, conversation, debug, time.perf_counter() - started)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Try the WeatherWise understanding layer.")
    parser.add_argument("--lat", type=float, help="device latitude")
    parser.add_argument("--lon", type=float, help="device longitude")
    parser.add_argument("--debug", action="store_true", help="also print raw LLM output and timings")
    args = parser.parse_args()
    asyncio.run(main(args.lat, args.lon, args.debug))
