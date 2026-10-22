"""Forward and reverse geocoding via Geoapify, plus choosing the best candidate."""

import math
from functools import lru_cache
from typing import Any

from timezonefinder import TimezoneFinder

from backend.core.config import get_settings
from backend.services.cache import Cache, InMemoryCache
from backend.services.errors import InvalidInputError, LocationNotFoundError
from backend.services.http import get_json
from backend.services.models import Location

PROVIDER = "Geoapify"
SEARCH_URL = "https://api.geoapify.com/v1/geocode/search"
REVERSE_URL = "https://api.geoapify.com/v1/geocode/reverse"

NO_COUNTRY_BIAS = "countrycode:none"
# Geoapify's countrycode:none returns nothing for some major cities (Paris, London),
# so an empty result is retried once with a whole-world box. Neither is IP-based.
WORLD_BIAS = "rect:-180,-90,180,90"

TIE_MARGIN = 0.1  # confidence/importance this close counts as a tie when ranking
AMBIGUITY_IMPORTANCE_GAP = 0.25  # rivals within this importance of the best are plausible
NEAR_DUPLICATE_KM = 5.0
LOCAL_KM = 50.0  # a candidate this close to the user is "the local one"

cache: Cache = InMemoryCache()


async def geocode(
    query: str,
    limit: int = 5,
    near: tuple[float, float] | None = None,
    country: str | None = None,
) -> list[Location]:
    """Search for places matching free-form `query`, in the order Geoapify ranks them.

    `near=(lat, lon)` biases results toward a point; otherwise `country` (ISO alpha-2)
    biases toward a country; otherwise no country is favoured.
    Raises LocationNotFoundError when nothing matches.
    """
    text = " ".join(query.split())
    if not text:
        raise InvalidInputError("query must not be empty")
    if limit < 1:
        raise InvalidInputError("limit must be at least 1")
    bias = _bias(near, country)

    key = f"geocode|{text.lower()}|{limit}|{bias}"
    if (cached := cache.get(key)) is not None:
        return [Location.model_validate(item) for item in cached]

    results = await _search(text, limit, bias)
    if not results and bias == NO_COUNTRY_BIAS:
        results = await _search(text, limit, WORLD_BIAS)
    if not results:
        raise LocationNotFoundError(f"No place found matching {text!r}.")

    locations = [_to_location(result) for result in results]
    cache.set(key, [loc.model_dump() for loc in locations])
    return locations


async def reverse_geocode(lat: float, lon: float) -> Location:
    """Describe the place at (lat, lon), keeping the given coordinates.

    If Geoapify finds no place inside a country (e.g. at sea), returns a Location named
    "Current location" with the timezone from timezonefinder instead of failing.
    """
    _check_coordinates(lat, lon)
    key = f"reverse|{lat:.3f}|{lon:.3f}"
    if (cached := cache.get(key)) is not None:
        return Location.model_validate(cached)

    data = await get_json(
        PROVIDER, REVERSE_URL, {"lat": lat, "lon": lon, "format": "json", "lang": "en", "apiKey": _api_key()}
    )
    results = data.get("results") or []
    # Over open water Geoapify returns a distant "ocean" feature with no country; skip it.
    place = next((r for r in results if r.get("country_code")), None)
    if place is None:
        location = Location(
            name="Current location",
            lat=lat,
            lon=lon,
            formatted=f"{lat:.4f}, {lon:.4f}",
            timezone=timezone_at(lat, lon),
        )
    else:
        # Name the city, not the nearest building/street the point happens to fall on.
        location = _to_location(place, area_name=False).model_copy(update={"lat": lat, "lon": lon})

    cache.set(key, location.model_dump())
    return location


def pick_best(
    candidates: list[Location], preferred_country: str = "in", near: tuple[float, float] | None = None
) -> tuple[Location, bool]:
    """Choose the most likely candidate and say whether the choice is ambiguous.

    If exactly one candidate is within LOCAL_KM of `near` (the user's (lat, lon)), it wins
    outright; next, the same for `preferred_country`. Otherwise rank
    by confidence, then importance (ties within TIE_MARGIN), then city-level results,
    then API order. Ambiguous means another plausible candidate is in a different
    state or country and not just a near-duplicate of the best one.
    """
    if not candidates:
        raise LocationNotFoundError("No candidate locations to choose from.")

    if near is not None:
        here = Location(name="", lat=near[0], lon=near[1], formatted="", timezone="UTC")
        local = [c for c in candidates if _distance_km(here, c) <= LOCAL_KM]
        if len(local) == 1:
            return local[0], False

    preferred = preferred_country.strip().lower()
    in_preferred = [c for c in candidates if c.country_code == preferred]
    if len(in_preferred) == 1:
        return in_preferred[0], False

    best = _rank_best(candidates)
    return best, bool(alternatives(best, candidates))


def alternatives(best: Location, candidates: list[Location]) -> list[Location]:
    """Other candidates plausible enough to make `best` ambiguous, in API order."""
    return [c for c in candidates if c is not best and _is_plausible_rival(best, c)]


def distinct_places(best: Location, candidates: list[Location], limit: int = 3) -> list[Location]:
    """`best` then other candidates in API order, skipping near-duplicates and repeated states.

    For listing choices to the user once pick_best has said the name is ambiguous.
    """
    chosen = [best]
    for c in candidates:
        if len(chosen) >= limit:
            break
        if all(
            (c.state, c.country_code) != (o.state, o.country_code) and _distance_km(c, o) > NEAR_DUPLICATE_KM
            for o in chosen
        ):
            chosen.append(c)
    return chosen


def _api_key() -> str:
    return get_settings().geoapify_api_key.get_secret_value()


async def _search(text: str, limit: int, bias: str) -> list[dict[str, Any]]:
    params = {"text": text, "limit": limit, "bias": bias, "format": "json", "lang": "en", "apiKey": _api_key()}
    data = await get_json(PROVIDER, SEARCH_URL, params)
    return data.get("results") or []


def _bias(near: tuple[float, float] | None, country: str | None) -> str:
    """Build an explicit bias so Geoapify never falls back to our server's IP country."""
    if near is not None:
        lat, lon = near
        _check_coordinates(lat, lon)
        return f"proximity:{lon},{lat}"  # Geoapify wants lon first
    if country is not None:
        code = country.strip().lower()
        if len(code) != 2 or not code.isalpha():
            raise InvalidInputError(f"country must be an ISO 3166-1 alpha-2 code like 'in', got {country!r}")
        return f"countrycode:{code}"
    return NO_COUNTRY_BIAS


def _check_coordinates(lat: float, lon: float) -> None:
    if not -90 <= lat <= 90:
        raise InvalidInputError(f"lat must be between -90 and 90, got {lat}")
    if not -180 <= lon <= 180:
        raise InvalidInputError(f"lon must be between -180 and 180, got {lon}")


def _to_location(result: dict[str, Any], area_name: bool = True) -> Location:
    """Map one Geoapify result (format=json) onto a Location.

    City-level results are named after the city. Area-level results (suburb, district,
    amenity, ...) keep their own name when `area_name` is set, with the city in `city`.
    """
    lat, lon = result["lat"], result["lon"]
    rank = result.get("rank") or {}
    country_code = result.get("country_code")
    city = result.get("city")
    if area_name and result.get("result_type") != "city":
        name = result.get("name") or city or result["formatted"]
    else:
        name = city or result.get("name") or result["formatted"]
    return Location(
        name=name,
        city=city,
        lat=lat,
        lon=lon,
        country=result.get("country"),
        country_code=country_code.lower() if country_code else None,
        state=result.get("state"),
        formatted=result.get("formatted") or f"{lat:.4f}, {lon:.4f}",
        result_type=result.get("result_type") or "unknown",
        confidence=rank.get("confidence"),
        importance=rank.get("importance"),
        timezone=(result.get("timezone") or {}).get("name") or timezone_at(lat, lon),
    )


@lru_cache(maxsize=1)
def _timezone_finder() -> TimezoneFinder:
    return TimezoneFinder()


def timezone_at(lat: float, lon: float) -> str:
    """IANA timezone at a point (timezonefinder); "UTC" if it has none."""
    return _timezone_finder().timezone_at(lat=lat, lng=lon) or "UTC"


def _rank_best(candidates: list[Location]) -> Location:
    """Keep near-ties on confidence, then on importance, then prefer cities; API order breaks ties."""
    pool = candidates
    for score in (_confidence, _importance):
        top = max(score(c) for c in pool)
        pool = [c for c in pool if score(c) >= top - TIE_MARGIN]
    cities = [c for c in pool if c.result_type == "city"]
    return (cities or pool)[0]


def _is_plausible_rival(best: Location, other: Location) -> bool:
    return (
        (other.state, other.country_code) != (best.state, best.country_code)
        and _distance_km(best, other) > NEAR_DUPLICATE_KM
        and _confidence(other) >= _confidence(best) - TIE_MARGIN
        and _importance(other) >= _importance(best) - AMBIGUITY_IMPORTANCE_GAP
    )


def _confidence(loc: Location) -> float:
    return loc.confidence or 0.0


def _importance(loc: Location) -> float:
    return loc.importance or 0.0


def _distance_km(a: Location, b: Location) -> float:
    """Great-circle (haversine) distance."""
    lat1, lon1, lat2, lon2 = map(math.radians, (a.lat, a.lon, b.lat, b.lon))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 6371.0 * 2 * math.asin(math.sqrt(h))
