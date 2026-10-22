"""Smoke test against the real APIs: geocode a place, resolve a date/time, print air quality.

Usage (from the repo root, venv active):
    python scripts/smoke_air_quality.py "Delhi" tomorrow morning
    python scripts/smoke_air_quality.py "Delhi" today
"""

import argparse
import asyncio

from backend.services.air_quality import get_air_quality_multi
from backend.services.date_resolver import resolve
from backend.services.geocoding import geocode, pick_best
from backend.services.models import AirQualityResult


def print_result(result: AirQualityResult) -> None:
    w, s = result.window, result.summary
    print(f"\n[{result.granularity}] {w.start:%a %d %b %H:%M} -> {w.end:%a %d %b %H:%M}")
    for p in result.points:
        naqi = f"NAQI {p.naqi:3d} {p.naqi_category:<19} {p.dominant_pollutant or '':5}" if p.naqi is not None \
            else f"NAQI  -  ({p.naqi_reason})"
        pm = f"PM2.5 {p.components.pm2_5 or 0:6.1f}  PM10 {p.components.pm10 or 0:6.1f} ug/m3"
        print(f"  {p.time:%a %H:%M}  {naqi}  {pm}  OWM {p.owm_aqi}{'  (indicative)' if p.indicative else ''}")
    if s.naqi_max is not None:
        print(f"  Summary: worst NAQI {s.naqi_max} ({s.naqi_category}, {s.dominant_pollutant}) at "
              f"{s.naqi_max_at:%a %H:%M}; avg PM2.5 {s.pm2_5_avg} / PM10 {s.pm10_avg} ug/m3"
              f"{'; indicative' if s.indicative else ''}")


async def main(place: str, date_expr: str, time_expr: str | None) -> None:
    location, ambiguous = pick_best(await geocode(place))
    print(f"{location.name} — {location.formatted} ({location.timezone})"
          + ("  [ambiguous: other places share this name]" if ambiguous else ""))
    windows = resolve(date_expr, time_expr, location.timezone)
    for result in await get_air_quality_multi(location, windows):
        print_result(result)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("place")
    parser.add_argument("date", help="e.g. today, tomorrow, yesterday, this_weekend")
    parser.add_argument("time", nargs="?", help="e.g. morning, evening, 7-9am (default: full day)")
    args = parser.parse_args()
    asyncio.run(main(args.place, args.date, args.time))
