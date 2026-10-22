"""Turn a QueryUnderstanding into concrete places and time windows. Deterministic: no LLM.

User-caused problems (no city, unknown or ambiguous city, unsupported date) come back as a
Clarification on the ResolvedQuery; only infrastructure failures (e.g. UpstreamAPIError) raise.
"""

from backend.agent.schemas import Clarification, QueryUnderstanding, ResolvedQuery, Target, UserContext
from backend.services import date_resolver, geocoding
from backend.services.errors import LocationNotFoundError, UnrecognizedDateError
from backend.services.models import Location

MAX_LOCATIONS = 3
MAX_OPTIONS = 3
DEFAULT_DATE = "today"
DEFAULT_TIME = "full_day"

SUPPORTED_DATES_HINT = (
    "I can look up today, tomorrow, the day after tomorrow, yesterday, the day before yesterday, "
    "a weekday like 'next Monday', or this weekend. Which day did you mean?"
)
SUPPORTED_TIMES_HINT = (
    "I can look at the morning, afternoon, evening, night, the whole day, or a range like '7-9am'. "
    "Which time did you mean?"
)


async def resolve_query(
    understanding: QueryUnderstanding,
    ctx: UserContext,
    pinned: dict[str, Location] | None = None,
) -> ResolvedQuery:
    """Resolve places and windows. `pinned` maps a typed place name to an already chosen Location."""
    u = understanding
    if u.intent == "out_of_scope":
        return ResolvedQuery(status="out_of_scope", understanding=u)
    if u.intent == "unclear":
        return _clarify(u, "unclear", u.clarification_question or (
            "Sorry, I didn't quite get that. Could you rephrase, e.g. 'Will it rain in Jaipur tomorrow evening?'"
        ))
    if u.needs_clarification:
        return _clarify(u, "llm_clarification", u.clarification_question or SUPPORTED_DATES_HINT)
    if len(u.locations) > MAX_LOCATIONS:
        return _clarify(u, "too_many_locations", (
            f"I can compare up to {MAX_LOCATIONS} places at a time. Which {MAX_LOCATIONS} should I look at?"
        ))

    date_expr = u.date_expr or DEFAULT_DATE
    time_expr = u.time_expr or DEFAULT_TIME
    if not date_resolver.is_supported_time(time_expr):
        return _clarify(u, "unsupported_time", SUPPORTED_TIMES_HINT)

    places: list[tuple[str, Location]] = []
    if not u.locations:
        if not ctx.has_location:
            return _clarify(u, "no_location", "Which city?")
        places.append(("", await geocoding.reverse_geocode(ctx.lat, ctx.lon)))
    for name in u.locations:
        location = (pinned or {}).get(place_key(name)) or await _geocode_one(name, ctx)
        if isinstance(location, Clarification):
            return ResolvedQuery(status="clarify", understanding=u, clarification=location)
        places.append((name, location))

    targets = []
    for name, location in places:
        try:
            windows = date_resolver.resolve(date_expr, time_expr, location.timezone, now=ctx.now)
        except UnrecognizedDateError as exc:
            question = (
                f"There's no {time_expr.replace('_', ' ')} left this weekend. Would another day work, "
                "e.g. next Saturday?"
                if date_expr == date_resolver.THIS_WEEKEND and "weekend" in str(exc)
                else SUPPORTED_DATES_HINT
            )
            return _clarify(u, "unsupported_date", question)
        targets.append(Target(query=name, location=location, windows=windows))

    return ResolvedQuery(status="ready", understanding=u, date_expr=date_expr, time_expr=time_expr, targets=targets)


def option_label(option: Location, typed: str, preferred_country: str = "in") -> str:
    """e.g. 'Chhatrapati Sambhajinagar (Aurangabad), Maharashtra' or 'Paris, Texas, United States'."""
    label = option.name
    if typed and typed.lower() not in option.name.lower():
        label += f" ({typed})"
    region = option.state or option.country
    if region and region != option.name:
        label += f", {region}"
    if option.country and option.country_code != preferred_country.lower() and option.country != region:
        label += f", {option.country}"
    return label


async def _geocode_one(name: str, ctx: UserContext) -> Location | Clarification:
    """Geocode one typed name and pick a result, or explain why we need to ask.

    No proximity bias on the search: Geoapify then returns only the nearest same-name place,
    which hides ambiguity. The user's location is used by pick_best to prefer a local match.
    """
    near = (ctx.lat, ctx.lon) if ctx.has_location else None
    try:
        candidates = await geocoding.geocode(name)
    except LocationNotFoundError:
        return Clarification(
            reason="location_not_found",
            location_query=name,
            question=f"I couldn't find '{name}'. Could you check the spelling or add the state or country, "
            f"e.g. '{name}, Rajasthan'?",
        )
    best, ambiguous = geocoding.pick_best(candidates, preferred_country=ctx.preferred_country, near=near)
    if not ambiguous:
        return best
    options = geocoding.distinct_places(best, candidates, limit=MAX_OPTIONS)
    labels = [option_label(o, name, ctx.preferred_country) for o in options]
    return Clarification(
        reason="ambiguous_location",
        location_query=name,
        options=options,
        question=f"Did you mean {', '.join(labels[:-1])} or {labels[-1]}?",
    )


def _clarify(u: QueryUnderstanding, reason: str, question: str) -> ResolvedQuery:
    return ResolvedQuery(
        status="clarify", understanding=u, clarification=Clarification(reason=reason, question=question)
    )


def place_key(name: str) -> str:
    """Normalized place name used as the key for pinned choices."""
    return " ".join(name.lower().split())
