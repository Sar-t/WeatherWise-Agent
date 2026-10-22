"""Assess an activity against the real APIs: geocode, resolve the time, fetch weather + air quality.

Usage (from the repo root, venv active):
    python scripts/assess_activity.py "Delhi" running tomorrow morning
    python scripts/assess_activity.py "Lonavala" hiking this_weekend
    python scripts/assess_activity.py "Mumbai" travel today 6-9pm --hours 3
"""

import argparse
import asyncio

from backend.services.air_quality import get_air_quality_multi
from backend.services.date_resolver import resolve
from backend.services.errors import WeatherWiseError
from backend.services.geocoding import geocode, pick_best
from backend.services.scoring import assess_activity
from backend.services.scoring.models import ActivityAssessment, TimeBlock
from backend.services.weather import get_weather_multi


def fmt_block(b: TimeBlock) -> str:
    return f"{b.start:%a %H:%M}-{b.end:%H:%M}  score {b.average_score:3d} ({b.verdict})  " + "; ".join(b.reasons)


def print_assessment(a: ActivityAssessment) -> None:
    print(f"\n{a.activity} (profile: {a.profile}) -> {a.verdict.upper()} ({a.score}/100)"
          + ("  [daily estimate, low confidence]" if a.is_daily_estimate else ""))
    print("\nHours:")
    for h in a.hourly:
        label = h.part_of_day or f"{h.time:%H:%M}"
        print(f"  {h.time:%a} {label:<9} {h.score:3d} {h.verdict:<12} " + "; ".join(h.reasons))
    print("\nBest times:")
    for b in a.best_blocks or []:
        print("  " + fmt_block(b))
    if not a.best_blocks:
        print("  (none)")
    print("\nAvoid:")
    for b in a.avoid_blocks:
        print("  " + fmt_block(b))
    if not a.avoid_blocks:
        print("  (none)")
    if a.hints:
        print("\nHints:\n" + "\n".join(f"  - {h}" for h in a.hints))
    if a.data_quality:
        print("\nNotes:\n" + "\n".join(f"  - {n}" for n in a.data_quality))


async def main(place: str, activity: str, date_expr: str, time_expr: str | None, hours: int | None) -> None:
    location, ambiguous = pick_best(await geocode(place))
    print(f"{location.name} — {location.formatted} ({location.timezone})"
          + ("  [ambiguous: other places share this name]" if ambiguous else ""))
    windows = resolve(date_expr, time_expr, location.timezone)
    weather_results = await get_weather_multi(location, windows)
    try:
        air = await get_air_quality_multi(location, windows)
    except WeatherWiseError as exc:
        print(f"(air quality unavailable: {exc})")
        air = None
    print_assessment(assess_activity(activity, weather_results, air, duration_hours=hours))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("place")
    parser.add_argument("activity", help="running, cycling, walking, hiking, football, picnic, travel, or anything")
    parser.add_argument("date", help="e.g. today, tomorrow, this_weekend")
    parser.add_argument("time", nargs="?", help="e.g. morning, evening, 7-9am (default: full day)")
    parser.add_argument("--hours", type=int, help="block length override, e.g. a trip's duration")
    args = parser.parse_args()
    asyncio.run(main(args.place, args.activity, args.date, args.time, args.hours))
