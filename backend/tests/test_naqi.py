from datetime import datetime, timedelta, timezone

import pytest

from backend.services import naqi
from backend.services.naqi import Average, category, compute_naqi, rolling_averages, sub_index

T0 = datetime(2026, 10, 22, 6, tzinfo=timezone.utc)


def full(value: float) -> Average:
    return Average(value, hours=24, indicative=False)


# --- breakpoint table ----------------------------------------------------------


def test_table_matches_cpcb_bands() -> None:
    # Upper bounds of Good / Satisfactory / Moderately Polluted / Poor / Very Poor (CPCB 2014),
    # then the Severe extension (same width as Very Poor; a convention, not CPCB's).
    expected = {
        "PM10": (50, 100, 250, 350, 430, 510),
        "PM2.5": (30, 60, 90, 120, 250, 380),
        "NO2": (40, 80, 180, 280, 400, 520),
        "SO2": (40, 80, 380, 800, 1600, 2400),
        "NH3": (200, 400, 800, 1200, 1800, 2400),
        "O3": (50, 100, 168, 208, 748, 1288),
        "CO": (1.0, 2.0, 10, 17, 34, 51),
    }
    assert {name: p.edges[1:] for name, p in naqi.pollutants().items()} == expected
    assert {name: p.averaging_hours for name, p in naqi.pollutants().items()} == {
        "PM10": 24, "PM2.5": 24, "NO2": 24, "SO2": 24, "NH3": 24, "O3": 8, "CO": 8,
    }


# --- sub-indices ---------------------------------------------------------------


@pytest.mark.parametrize(
    "pollutant, concentration, expected",
    [
        # exact band edges
        ("PM2.5", 0, 0), ("PM2.5", 30, 50), ("PM2.5", 60, 100), ("PM2.5", 90, 200),
        ("PM2.5", 120, 300), ("PM2.5", 250, 400),
        ("PM10", 100, 100), ("PM10", 250, 200), ("NO2", 180, 200), ("SO2", 800, 300), ("NH3", 1800, 400),
        ("O3", 168, 200), ("O3", 208, 300),
        # mid-band interpolation, by hand: I = I_lo + (C - C_lo) * (I_hi - I_lo) / (C_hi - C_lo)
        ("PM2.5", 45, 75),  # 50 + 15 * 50/30
        ("PM10", 175, 150),  # 100 + 75 * 100/150
        ("PM2.5", 185, 350),  # 300 + 65 * 100/130
        ("O3", 188, 250),  # 200 + 20 * 100/40
        ("NO2", 340, 350),  # 300 + 60 * 100/120
        # Severe band, using the Very-Poor-width extension
        ("PM10", 470, 450),  # 400 + 40 * 100/80
        ("PM2.5", 315, 450),  # 400 + 65 * 100/130
    ],
)
def test_sub_index(pollutant: str, concentration: float, expected: float) -> None:
    assert sub_index(pollutant, concentration) == pytest.approx(expected)


def test_co_is_converted_from_ug_to_mg() -> None:
    # OpenWeather gives ug/m3; CO bands are mg/m3. 1500 ug/m3 = 1.5 mg/m3 -> 50 + 0.5 * 50/1 = 75
    assert sub_index("CO", 1500) == pytest.approx(75)
    assert sub_index("CO", 6000) == pytest.approx(150)  # 6 mg/m3: 100 + 4 * 100/8
    assert sub_index("CO", 1.5) < 1  # not mistaken for 1.5 mg/m3


@pytest.mark.parametrize("pollutant, concentration", [("PM10", 510), ("PM10", 2000), ("PM2.5", 900), ("CO", 99_000)])
def test_above_severe_band_clamps_at_500(pollutant: str, concentration: float) -> None:
    assert sub_index(pollutant, concentration) == 500


@pytest.mark.parametrize(
    "value, expected",
    [(0, "Good"), (50, "Good"), (51, "Satisfactory"), (100, "Satisfactory"), (101, "Moderately Polluted"),
     (200, "Moderately Polluted"), (201, "Poor"), (300, "Poor"), (301, "Very Poor"), (400, "Very Poor"),
     (401, "Severe"), (500, "Severe")],
)
def test_category(value: int, expected: str) -> None:
    assert category(value) == expected


# --- overall NAQI ----------------------------------------------------------------


def test_overall_is_max_sub_index_and_names_dominant() -> None:
    result = compute_naqi({"PM2.5": full(185), "PM10": full(175), "NO2": full(40), "CO": full(1500)})
    assert result.sub_indices == {"PM2.5": 350, "PM10": 150, "NO2": 50, "CO": 75}
    assert (result.value, result.category, result.dominant) == (350, "Very Poor", "PM2.5")
    assert result.reason is None and not result.indicative


def test_dominant_can_be_a_gas() -> None:
    result = compute_naqi({"PM2.5": full(20), "PM10": full(40), "O3": full(188)})
    assert (result.value, result.dominant, result.category) == (250, "O3", "Poor")


def test_clamped_overall_stays_severe() -> None:
    result = compute_naqi({"PM2.5": full(900), "PM10": full(700), "NO2": full(50)})
    assert (result.value, result.category) == (500, "Severe")


def test_fewer_than_three_pollutants_gives_none_with_reason() -> None:
    result = compute_naqi({"PM2.5": full(185), "PM10": full(175), "NO2": Average(None, 0, True)})
    assert (result.value, result.category, result.dominant) == (None, None, None)
    assert result.reason == "only 2 pollutant(s) available; NAQI needs 3"
    assert result.sub_indices == {"PM2.5": 350, "PM10": 150}  # still reported


def test_missing_pm_gives_none_with_reason() -> None:
    result = compute_naqi({"NO2": full(90), "SO2": full(30), "O3": full(60), "CO": full(800)})
    assert result.value is None
    assert result.reason == "NAQI needs PM2.5 or PM10, and neither is available"


def test_indicative_if_any_used_average_is_partial() -> None:
    result = compute_naqi({"PM2.5": Average(45, 10, True), "PM10": full(175), "NO2": full(40)})
    assert result.indicative and result.value == 150


# --- rolling averages ------------------------------------------------------------


def hourly(hours: int, end: datetime = T0, **components: float | None) -> list:
    return [(end - timedelta(hours=h), dict(components)) for h in range(hours)]


def test_rolling_24h_and_8h_trailing_means() -> None:
    series = [(T0 - timedelta(hours=h), {"pm2_5": float(h), "co": float(h)}) for h in range(30)]
    averages = rolling_averages(series, T0)
    assert averages["PM2.5"].hours == 24 and averages["PM2.5"].value == pytest.approx(sum(range(24)) / 24)
    assert averages["CO"].hours == 8 and averages["CO"].value == pytest.approx(sum(range(8)) / 8)
    assert not averages["PM2.5"].indicative and not averages["CO"].indicative


def test_window_is_trailing_and_inclusive_of_the_hour() -> None:
    series = hourly(3, end=T0 + timedelta(hours=2), pm2_5=10.0) + hourly(1, end=T0 - timedelta(hours=24), pm2_5=999.0)
    averages = rolling_averages(series, T0)  # future hours and the hour 24 h back are excluded
    assert (averages["PM2.5"].value, averages["PM2.5"].hours) == (10.0, 1)


@pytest.mark.parametrize("hours, indicative", [(24, False), (16, False), (15, True), (1, True)])
def test_indicative_24h(hours: int, indicative: bool) -> None:
    assert rolling_averages(hourly(hours, pm2_5=50.0), T0)["PM2.5"].indicative is indicative


@pytest.mark.parametrize("hours, indicative", [(8, False), (6, False), (5, True)])
def test_indicative_8h(hours: int, indicative: bool) -> None:
    assert rolling_averages(hourly(hours, o3=50.0), T0)["O3"].indicative is indicative


def test_missing_component_values_are_skipped() -> None:
    series = hourly(24, pm2_5=40.0) + [(T0 - timedelta(minutes=30), {"pm2_5": None})]
    averages = rolling_averages(series, T0)
    assert averages["PM2.5"].hours == 24 and averages["PM2.5"].value == 40.0
    assert averages["NO2"] == Average(None, 0, True)
