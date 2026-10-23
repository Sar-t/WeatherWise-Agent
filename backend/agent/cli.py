"""Chat with the WeatherWise agent in the terminal.

Usage (from the repo root, venv active):
    python -m backend.agent.cli --lat 26.91 --lon 75.79 [--debug]
--lat/--lon simulate the device location. Type 'quit' or press Ctrl+C to exit.
"""

import argparse
import asyncio

from backend.agent.graph import Agent
from backend.agent.schemas import AgentResponse, UserContext
from backend.services import guidelines


def print_response(response: AgentResponse, debug: bool) -> None:
    print(f"\nweatherwise> {response.text}")
    if not debug:
        return
    d = response.debug
    print(f"  [debug] kind={response.kind}  path: {' -> '.join(d.nodes)}")
    for call in d.tool_calls:
        print(f"  [debug] tool {call.name}({call.args}) {call.seconds:.2f}s")
    print(f"  [debug] llm calls: {d.llm_calls}" + ("  (answer fell back to the template)" if d.answer_fallback else ""))
    print("  [debug] timings: " + ", ".join(f"{name} {s:.2f}s" for name, s in d.timings.items()))
    if response.sources:
        print("  [debug] sources: " + "; ".join(f"{s.title} ({s.source_name})" for s in response.sources))
    if d.error:
        print(f"  [debug] error: {d.error}")


async def main(lat: float | None, lon: float | None, debug: bool) -> None:
    ctx = UserContext(lat=lat, lon=lon)
    agent = Agent(ctx)
    try:  # load the guidelines model and index once, before the first question
        seconds = guidelines.warm_up()
        if debug:
            print(f"  [debug] guidelines model and index loaded in {seconds:.1f}s")
    except Exception as exc:  # answers still work without guidance
        print(f"(guidelines unavailable: {type(exc).__name__}; answering without official guidance)")
    where = f"({lat}, {lon}), {ctx.timezone}" if ctx.has_location else f"no device location, {ctx.timezone}"
    print(f"WeatherWise. Location: {where}. Type 'quit' to exit.")
    while True:
        try:
            message = input("\nyou> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if message.lower() in {"quit", "exit", "q"}:
            break
        if message:
            print_response(await agent.ask(message), debug)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Chat with the WeatherWise agent.")
    parser.add_argument("--lat", type=float, help="device latitude")
    parser.add_argument("--lon", type=float, help="device longitude")
    parser.add_argument("--debug", action="store_true", help="also print node path, tool calls, LLM calls, timings")
    args = parser.parse_args()
    asyncio.run(main(args.lat, args.lon, args.debug))
