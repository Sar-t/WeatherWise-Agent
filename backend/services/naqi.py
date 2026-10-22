"""India's National Air Quality Index (CPCB NAQI). Pure functions, no I/O beyond reading the table.

Breakpoints live in data/naqi_breakpoints.toml (with their source). Concentrations are taken in
OpenWeather's units (ug/m3); CO is converted to mg/m3 as the table requires.
"""

import tomllib
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from functools import lru_cache
from pathlib import Path

BREAKPOINTS_PATH = Path(__file__).parent / "data" / "naqi_breakpoints.toml"
MIN_POLLUTANTS = 3
PM_POLLUTANTS = {"PM2.5", "PM10"}
MAX_INDEX = 500


@dataclass(frozen=True)
class Pollutant:
    name: str  # "PM2.5"
    component: str  # OpenWeather key, "pm2_5"
    averaging_hours: int  # 24 or 8
    scale: float  # OpenWeather unit -> table unit
    edges: tuple[float, ...]  # concentration band edges, starting at 0, incl. the Severe extension

    @property
    def min_hours(self) -> int:
        """Below this many hours an average is only indicative (CPCB: 16 of 24; 6 of 8 likewise)."""
        return 16 if self.averaging_hours == 24 else 6


@dataclass(frozen=True)
class Average:
    """A trailing mean and how many hours went into it."""

    value: float | None
    hours: int
    indicative: bool


@dataclass
class NaqiResult:
    value: int | None
    category: str | None
    dominant: str | None
    sub_indices: dict[str, int] = field(default_factory=dict)
    indicative: bool = False
    reason: str | None = None  # why value is None


@lru_cache(maxsize=1)
def _table() -> tuple[dict[str, Pollutant], list[str], list[int]]:
    data = tomllib.loads(BREAKPOINTS_PATH.read_text(encoding="utf-8"))
    pollutants = {}
    for name, p in data["pollutants"].items():
        upper = [float(u) for u in p["upper"]]
        severe_top = upper[-1] + (upper[-1] - upper[-2])  # convention, see the TOML header
        pollutants[name] = Pollutant(name, p["component"], p["averaging_hours"], p.get("scale", 1.0),
                                     (0.0, *upper, severe_top))
    return pollutants, data["categories"]["names"], data["categories"]["index_upper"]


def pollutants() -> dict[str, Pollutant]:
    """The NAQI pollutants OpenWeather can supply, keyed by display name ("PM2.5")."""
    return _table()[0]


def sub_index(pollutant: str, concentration: float) -> float:
    """Sub-index for one pollutant: linear between band edges; 500 above the Severe band."""
    p = pollutants()[pollutant]
    c = concentration * p.scale
    index_edges = [0, *_table()[2]]
    if c >= p.edges[-1]:
        return float(MAX_INDEX)
    for i in range(1, len(p.edges)):
        if c <= p.edges[i]:
            c_lo, c_hi = p.edges[i - 1], p.edges[i]
            i_lo, i_hi = index_edges[i - 1], index_edges[i]
            return i_lo + (c - c_lo) * (i_hi - i_lo) / (c_hi - c_lo)
    return float(MAX_INDEX)


def category(naqi: int) -> str:
    """'Good' for 0-50, 'Satisfactory' for 51-100, ... 'Severe' for 401-500."""
    _, names, uppers = _table()
    return next(name for name, upper in zip(names, uppers) if naqi <= upper)


def compute_naqi(averages: dict[str, Average]) -> NaqiResult:
    """Overall NAQI from per-pollutant averages (keyed by display name)."""
    available = {name: avg for name, avg in averages.items() if avg.value is not None and name in pollutants()}
    subs = {name: round(sub_index(name, avg.value)) for name, avg in available.items()}
    if len(subs) < MIN_POLLUTANTS:
        return NaqiResult(None, None, None, subs, reason=f"only {len(subs)} pollutant(s) available; NAQI needs 3")
    if not PM_POLLUTANTS & subs.keys():
        return NaqiResult(None, None, None, subs, reason="NAQI needs PM2.5 or PM10, and neither is available")
    dominant = max(subs, key=subs.get)
    value = subs[dominant]
    return NaqiResult(value, category(value), dominant, subs,
                      indicative=any(available[name].indicative for name in subs))


def rolling_averages(series: list[tuple[datetime, dict[str, float | None]]], at: datetime) -> dict[str, Average]:
    """Trailing means ending at `at` (inclusive): 24 h for most pollutants, 8 h for CO and O3.

    `series` is hourly (time, components) in any order; uses whatever hours exist.
    """
    out = {}
    for name, p in pollutants().items():
        since = at - timedelta(hours=p.averaging_hours)
        values = [c[p.component] for t, c in series if since < t <= at and c.get(p.component) is not None]
        mean = sum(values) / len(values) if values else None
        out[name] = Average(mean, len(values), indicative=len(values) < p.min_hours)
    return out
