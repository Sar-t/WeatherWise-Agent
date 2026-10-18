"""Smoke test against the real APIs: geocode a place, resolve a date/time, print the weather.

Usage (from the repo root, with the venv active):
    python scripts/smoke_weather.py "Jaipur" tomorrow evening
    python scripts/smoke_weather.py "Bandra" this_weekend morning
    python scripts/smoke_weather.py "Paris" yesterday
"""

import argparse
import asyncio

from backend.services.date_resolver import resolve
from backend.services.geocoding import geocode, pick_best
from backend.services.models import WeatherResult
from backend.services.weather import get_weather_multi


def print_result(result: WeatherResult) -> None:
    w = result.window
    print(f"\n[{result.granularity}] {w.start:%a %d %b %H:%M} -> {w.end:%a %d %b %H:%M}"
          f"  (fetched {result.fetched_at:%H:%M:%S})")
    for p in result.hourly:
        rain = f"{p.rain_prob:.0%}" if p.rain_prob is not None else "  - "
        print(f"  {p.time:%a %H:%M}  {p.temp_c:5.1f}°C (feels {p.feels_like_c:5.1f})  rain {rain} {p.rain_mm:.1f}mm"
              f"  wind {p.wind_kmh:4.1f} km/h  {p.description or ''}")
    for d in result.daily:
        rain = f"{d.rain_prob:.0%}" if d.rain_prob is not None else "  -"
        wind = f"{d.wind_kmh:.1f}" if d.wind_kmh is not None else "-"
        peak = f"{d.wind_max_kmh:.1f}" if d.wind_max_kmh is not None else "-"
        rain_mm = f"{d.rain_mm:.1f}mm" if d.rain_mm is not None else "?"
        print(f"  {d.date:%a %d %b}  {d.temp_min_c:.1f}–{d.temp_max_c:.1f}°C  rain {rain} {rain_mm}"
              f"  wind {wind} (max {peak}) km/h  {d.summary or d.description or ''}")
    for a in result.alerts:
        print(f"  ALERT {a.event} ({a.sender}) {a.start:%a %H:%M} -> {a.end:%a %H:%M}")


async def main(place: str, date_expr: str, time_expr: str | None) -> None:
    location, ambiguous = pick_best(await geocode(place))
    print(f"{location.name} — {location.formatted} ({location.lat:.4f}, {location.lon:.4f}, {location.timezone})"
          + ("  [ambiguous: other places share this name]" if ambiguous else ""))
    windows = resolve(date_expr, time_expr, location.timezone)
    for result in await get_weather_multi(location, windows):
        print_result(result)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("place")
    parser.add_argument("date", help="e.g. today, tomorrow, next_monday, this_weekend, yesterday")
    parser.add_argument("time", nargs="?", help="e.g. morning, evening, 7-9am (default: full day)")
    args = parser.parse_args()
    asyncio.run(main(args.place, args.date, args.time))
